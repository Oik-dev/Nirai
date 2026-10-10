// D0「関門」：本人のVRMに適用済みの世界座標と、VRM 1.0正規化ボーンの局所回転を見る。
// 動きの名前を知らない純関数。呼び出し元が描画・書き込みを始める前に pass を確認する。
export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];
export type BoneFrames = { position: Vec3[]; rotation: Quat[] };
// 体表面の固定サンプル。restと各フレームの点は同じ順番（同じ頂点）で並べる。各骨最大64点。
// 両足（leftFoot・rightFoot）は必須で、足裏の「かかと」「拇趾球」「つま先」を含めること（toesがあればtoesにも）。
// 接地と滑りは足裏の点だけで測る。骨の位置から足裏を推定しない。
export type SkinSamples = { rest: Vec3[]; position: Vec3[][] };
export type GateInput = {
  fps: number; bones: Record<string, BoneFrames>; rest: Record<string, Vec3>;
  skin: Record<string, SkinSamples>;
};
export type GateIssue = {
  kind: 'missing_bone' | 'invalid_data' | 'duration' | 'stature' | 'bone_length'
    | 'penetration' | 'foot_slide' | 'joint_limit' | 'jitter';
  bone: string | null;
  fromFrame: number;
  toFrame: number;
  metric: string;
  value: number | null;
  limit: number;
  unit: string;
};
export type GateResult = { pass: boolean; issues: GateIssue[] };

// D0の並べた絵で再調整する値はここだけ。論文のしきい値の転載ではなく、単位つきの初期仮置き。
// VRM必須骨: https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/humanoid.md
// 接地時の足の水平速度: Zou et al., WACV 2020 (ground contact + footskate);
// Kimodoの実装・根拠: ResidentMotion-PoC/vendor/kimodo/kimodo/metrics/foot_skate.py、
// 同 docs/source/benchmark/metrics.md（手元にあるKimodoの写し）。
// 高さのみの接地推定は限界がある: Mourot et al., UnderPressure, CGF 2022, doi:10.1111/cgf.14635。
export const GATE_LIMITS = Object.freeze({
  minFps: 10, maxFps: 120,
  minDurationS: 0.25, maxDurationS: 120,
  minStatureM: 0.45, maxStatureM: 2.5,
  minSegmentM: 0.015, maxSegmentStretchRatio: 0.25,
  penetrationM: 0.008, // 骨と体表点の海底下侵入は8mm以上で検出する
  soleBandM: 0.025, // 足の体表点のうち、restでいちばん低い点からこの高さまでを足裏とみなす
  contactHeightM: 0.012, // 足裏の点がこの高さ以下で上下に止まっていれば接地
  contactStableFrames: 3, // 接地・離地の境界を判定しない
  maxContactVerticalSpeedMps: 0.12,
  slidingSpeedMps: 0.35, // 接地区間に限って見る（高さだけで即判定しない）
  slidingDriftM: 0.04,
  seatedHipsRatio: 1 / 3, // 腰が立位の1/3未満なら足は体重を支えない
  // ガタつき：隣り合うフレームの加速度（2階差分）が逆向きになった大きさ＝速度が1フレームだけ跳ねて戻った量。
  // 等速・等加速・急停止（ぶつかって止まる）は0、1フレームの跳ねはその高さ、±eの毎フレーム往復は4e。
  // 骨が動いていても止まっていても同じに測る。D0の最大（Kimodo 6本：4.8mm・1.7°、SwimXYZ σ1：11.3mm・4.3°）の上に置く。
  // 振幅10cmで3Hzを超える速い往復は、フレームとの位相によってここに掛かる。
  positionFlipM: 0.015,
  rotationFlipDeg: 20,
  maxNeckTwistDeg: 85,
  maxCombinedNeckTwistDeg: 125,
  maxNeckSwingDeg: 75,
  maxKneeFlexDeg: 165,
  maxElbowFlexDeg: 155,
  minHingeFlexDeg: -12, // 逆曲がりの許容（正規化VRMのT姿勢から）
  // 膝と肘は、曲げ・骨まわりのひねり・軸外に分けて見る（hinge）。膝はX曲げ・Yひねり、肘はY曲げ・Xひねり。
  maxKneeOffAxisDeg: 20, // 膝の内反・外反。D0の最大はSwimXYZの平泳ぎの15.2°
  maxShinTwistDeg: 45, // 膝を曲げると下腿は約45°まで回る。D0の最大はKimodoの平泳ぎ（あおり足）の43°
  maxElbowOffAxisDeg: 55,
  maxForearmTwistDeg: 115, // 前腕の回内・回外
  maxSurfaceSamples: 64,
  quaternionNormTolerance: 0.03,
});

const REQUIRED = [
  'hips', 'spine', 'head',
  'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
  'leftUpperArm', 'leftLowerArm', 'leftHand', 'rightUpperArm', 'rightLowerArm', 'rightHand',
] as const;
const FEET = ['leftFoot', 'rightFoot'] as const;
const SOLE_BONES = ['leftFoot', 'leftToes', 'rightFoot', 'rightToes'];
// optional な胸・首・肩は親子間の距離検査から除外しても、必須骨を欠いたことにはしない。
const PARENTS: Record<string, string> = {
  spine: 'hips', chest: 'spine', upperChest: 'chest', neck: 'upperChest', head: 'neck',
  leftUpperLeg: 'hips', leftLowerLeg: 'leftUpperLeg', leftFoot: 'leftLowerLeg', leftToes: 'leftFoot',
  rightUpperLeg: 'hips', rightLowerLeg: 'rightUpperLeg', rightFoot: 'rightLowerLeg', rightToes: 'rightFoot',
  leftShoulder: 'upperChest', leftUpperArm: 'leftShoulder', leftLowerArm: 'leftUpperArm', leftHand: 'leftLowerArm',
  rightShoulder: 'upperChest', rightUpperArm: 'rightShoulder', rightLowerArm: 'rightUpperArm', rightHand: 'rightLowerArm',
};
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const vector = (v: unknown, length: number): v is number[] =>
  Array.isArray(v) && v.length === length && v.every(finite);
const distance = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const horizontal = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[2] - b[2]);
const minus = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const degrees = (r: number): number => r * 180 / Math.PI;

// q と -q は同じ回転。正規化して w>=0 に寄せ、0〜180度の回転を検査する。
function aligned(q: Quat): Quat {
  return q[3] < 0 ? [-q[0], -q[1], -q[2], -q[3]] : q;
}
// 軸まわりのひねり（swing-twist分解のtwist）と、それ以外の振れ（swing）の角度。
function twistSwing(q: Quat, axis: 0 | 1): { twist: number; swing: number } {
  const a = aligned(q);
  return {
    twist: degrees(2 * Math.atan2(a[axis], a[3])),
    swing: degrees(2 * Math.atan2(
      Math.hypot(...a.slice(0, 3).filter((_, i) => i !== axis)), Math.hypot(a[axis], a[3]),
    )),
  };
}
// 膝・肘を、骨の長さ方向まわりのひねり（swing-twistのtwist）と、骨の向きの変化に分ける。
// 向きの変化のうち、曲げの面の中の角度が曲げ、面から外れた角度が軸外。ひねりは骨の向きを変えないので混ざらない。
function hinge(q: Quat, knee: boolean, left: boolean): { bend: number; twist: number; offAxis: number } {
  const [x, y, z, w] = aligned(q);
  // すねは -Y を向き、X回りに後ろ（-Z）へ曲がる。
  if (knee) return {
    bend: degrees(Math.atan2(2 * (y * z + x * w), 1 - 2 * (x * x + z * z))),
    twist: degrees(2 * Math.atan2(y, w)),
    offAxis: degrees(Math.asin(Math.min(1, Math.abs(2 * (x * y - z * w))))),
  };
  // 前腕は左なら +X、右なら -X を向き、Y回りに前（+Z）へ曲がる。
  return {
    bend: degrees(Math.atan2((left ? 1 : -1) * 2 * (x * z - y * w), 1 - 2 * (y * y + z * z))),
    twist: degrees(2 * Math.atan2(x, w)),
    offAxis: degrees(Math.asin(Math.min(1, Math.abs(2 * (x * y + z * w))))),
  };
}
// a から b への回転（b·a⁻¹）を、軸×角度（度）のベクトルで表す。
function turn(a: Quat, b: Quat): Vec3 {
  const [x0, y0, z0, w0] = [-a[0], -a[1], -a[2], a[3]];
  const x = b[3] * x0 + b[0] * w0 + b[1] * z0 - b[2] * y0;
  const y = b[3] * y0 - b[0] * z0 + b[1] * w0 + b[2] * x0;
  const z = b[3] * z0 + b[0] * y0 - b[1] * x0 + b[2] * w0;
  const w = b[3] * w0 - b[0] * x0 - b[1] * y0 - b[2] * z0;
  const sin = Math.hypot(x, y, z);
  if (!sin) return [0, 0, 0];
  const scale = Math.sign(w || 1) * degrees(2 * Math.atan2(sin, Math.abs(w))) / sin;
  return [x * scale, y * scale, z * scale];
}
// steps[i] は i→i+1 フレームの変化。前後の加速度 steps[i]-steps[i-1] と steps[i+1]-steps[i] が
// 逆向きなら、steps[i] だけが前後の流れから跳ねている。逆向きの成分を大きい方の長さで割って返す。
function flip(steps: Vec3[], i: number): number {
  const a = minus(steps[i], steps[i - 1]), b = minus(steps[i + 1], steps[i]);
  const opposite = -(a[0] * b[0] + a[1] * b[1] + a[2] * b[2]);
  return opposite > 0 ? opposite / Math.max(Math.hypot(...a), Math.hypot(...b)) : 0;
}

export function checkMotion(input: GateInput): GateResult {
  // 警告は（種類・骨・指標）ごとにフレームの最悪値を集め、最後に連続区間へまとめる。
  // 体表点のように同じ骨・同じフレームを何度見ても、骨ごとに1つの区間になる。
  type Found = Omit<GateIssue, 'fromFrame' | 'toFrame' | 'value'> & { frames: Map<number, number | null> };
  const found = new Map<string, Found>();
  const worse = (value: number | null, than: number | null | undefined) =>
    than === undefined || (value !== null && (than === null || Math.abs(value) > Math.abs(than)));
  const add = (kind: GateIssue['kind'], bone: string | null, frame: number, metric: string,
    value: number | null, limit: number, unit: string) => {
    const key = JSON.stringify([kind, bone, metric, limit, unit]);
    let entry = found.get(key);
    if (!entry) found.set(key, entry = { kind, bone, metric, limit, unit, frames: new Map() });
    if (worse(value, entry.frames.get(frame))) entry.frames.set(frame, value);
  };
  const finish = (): GateResult => {
    const issues: GateIssue[] = [];
    for (const { frames, ...issue } of found.values()) {
      let last: GateIssue | undefined;
      for (const [frame, value] of [...frames].sort((a, b) => a[0] - b[0])) {
        if (last && last.toFrame + 1 === frame) {
          last.toFrame = frame;
          if (worse(value, last.value)) last.value = value;
        } else issues.push(last = { ...issue, fromFrame: frame, toFrame: frame, value });
      }
    }
    return { pass: issues.length === 0, issues };
  };
  if (!input || !finite(input.fps) || input.fps < GATE_LIMITS.minFps || input.fps > GATE_LIMITS.maxFps
      || !input.bones || typeof input.bones !== 'object' || Array.isArray(input.bones)
      || !input.rest || typeof input.rest !== 'object' || Array.isArray(input.rest)) {
    add('invalid_data', null, 0, 'fps_or_input', input && finite(input.fps) ? input.fps : null, GATE_LIMITS.maxFps, 'fps');
    return finish();
  }
  const bones = input.bones;
  const names = Object.keys(bones);
  const count = bones.hips?.position?.length;
  for (const bone of REQUIRED) {
    if (!Object.hasOwn(bones, bone)) add('missing_bone', bone, 0, 'required', 0, 1, 'bones');
  }
  if (!Number.isSafeInteger(count) || count! < 1) {
    add('invalid_data', 'hips', 0, 'frames', count ?? null, 1, 'frames');
    return finish();
  }
  const duration = (count! - 1) / input.fps;
  if (duration < GATE_LIMITS.minDurationS) add('duration', null, 0, 'seconds_min', duration, GATE_LIMITS.minDurationS, 's');
  if (duration > GATE_LIMITS.maxDurationS) {
    add('duration', null, count! - 1, 'seconds_max', duration, GATE_LIMITS.maxDurationS, 's');
    // 外れた巨大入力を全フレーム走査せず入口で拒否する。
    return finish();
  }
  for (const name of names) {
    const entry = bones[name];
    if (!entry || !Array.isArray(entry.position) || !Array.isArray(entry.rotation)
      || entry.position.length !== count || entry.rotation.length !== count) {
      add('invalid_data', name, 0, 'frame_count', entry?.position?.length ?? null, count!, 'frames');
      continue;
    }
    if (!vector(input.rest[name], 3)) add('invalid_data', name, 0, 'rest_position', null, 3, 'components');
    for (let i = 0; i < count!; i++) {
      if (!vector(entry.position[i], 3)) add('invalid_data', name, i, 'position', null, 3, 'components');
      const rotation = entry.rotation[i];
      if (!vector(rotation, 4)) add('invalid_data', name, i, 'rotation', null, 4, 'components');
      else {
        const norm = Math.hypot(...rotation);
        if (Math.abs(norm - 1) > GATE_LIMITS.quaternionNormTolerance)
          add('invalid_data', name, i, 'quaternion_norm', Math.abs(norm - 1), GATE_LIMITS.quaternionNormTolerance, 'difference_from_1');
      }
    }
  }
  // 体表点は順序固定の skinned vertices。両足は必須、指・目・顎などは付けなくてもよい。
  const skin = input.skin;
  if (!skin || typeof skin !== 'object' || Array.isArray(skin)) {
    add('invalid_data', null, 0, 'skin', null, 1, 'object');
    return finish();
  }
  for (const foot of FEET) if (!Object.hasOwn(skin, foot)) add('invalid_data', foot, 0, 'skin_required', 0, 1, 'samples');
  for (const [name, samples] of Object.entries(skin)) {
    if (!bones[name] || !samples || !Array.isArray(samples.rest) || !Array.isArray(samples.position)
      || samples.rest.length < 1 || samples.rest.length > GATE_LIMITS.maxSurfaceSamples
      || samples.position.length !== count) {
      add('invalid_data', name, 0, 'skin_shape', samples?.rest?.length ?? null, GATE_LIMITS.maxSurfaceSamples, 'samples');
      continue;
    }
    if (!samples.rest.every(rest => vector(rest, 3))) add('invalid_data', name, 0, 'skin_rest', null, 3, 'components');
    for (let i = 0; i < count!; i++) {
      if (!Array.isArray(samples.position[i]) || samples.position[i].length !== samples.rest.length) {
        add('invalid_data', name, i, 'skin_points', samples.position[i]?.length ?? null, samples.rest.length, 'samples');
      } else if (!samples.position[i].every(point => vector(point, 3))) {
        add('invalid_data', name, i, 'skin_point', null, 3, 'components');
      }
    }
  }
  if (found.size) return finish();
  const height = input.rest.head[1] - Math.min(input.rest.leftFoot[1], input.rest.rightFoot[1]);
  if (height < GATE_LIMITS.minStatureM) add('stature', null, 0, 'height_min', height, GATE_LIMITS.minStatureM, 'm');
  if (height > GATE_LIMITS.maxStatureM) add('stature', null, 0, 'height_max', height, GATE_LIMITS.maxStatureM, 'm');
  for (const name of names) {
    const b = bones[name];
    const parentName = PARENTS[name];
    // 親が任意骨で不在なら、その直近の在る先祖に遡る（VRM 1.0）。
    let parent = parentName;
    while (parent && !bones[parent]) parent = PARENTS[parent];
    if (parent) {
      const original = distance(input.rest[parent] as Vec3, input.rest[name]);
      if (original < GATE_LIMITS.minSegmentM && REQUIRED.includes(name as typeof REQUIRED[number])) {
        add('bone_length', name, 0, 'rest_min', original, GATE_LIMITS.minSegmentM, 'm');
      } else if (original >= GATE_LIMITS.minSegmentM) {
        for (let i = 0; i < count!; i++) {
          const error = Math.abs(distance(bones[parent].position[i], b.position[i]) / original - 1);
          if (error > GATE_LIMITS.maxSegmentStretchRatio)
            add('bone_length', name, i, 'stretch_ratio', error, GATE_LIMITS.maxSegmentStretchRatio, 'ratio');
        }
      }
    }
    for (let i = 0; i < count!; i++) {
      const point = b.position[i];
      if (point[1] < -GATE_LIMITS.penetrationM)
        add('penetration', name, i, 'bone_below_ground', -point[1], GATE_LIMITS.penetrationM, 'm');
      const samples = skin[name]?.position[i];
      if (samples) {
        const lowest = Math.min(...samples.map(p => p[1]));
        if (lowest < -GATE_LIMITS.penetrationM)
          add('penetration', name, i, 'skin_below_ground', -lowest, GATE_LIMITS.penetrationM, 'm');
      }
      const q = b.rotation[i];
      if (name === 'neck' || (name === 'head' && !bones.neck)) {
        const { twist, swing } = twistSwing(q, 1);
        if (Math.abs(twist) > GATE_LIMITS.maxNeckTwistDeg)
          add('joint_limit', name, i, 'neck_twist', Math.abs(twist), GATE_LIMITS.maxNeckTwistDeg, 'deg');
        if (swing > GATE_LIMITS.maxNeckSwingDeg)
          add('joint_limit', name, i, 'neck_swing', swing, GATE_LIMITS.maxNeckSwingDeg, 'deg');
      }
      if (name === 'head' && bones.neck) {
        const total = Math.abs(twistSwing(bones.neck.rotation[i], 1).twist + twistSwing(q, 1).twist);
        if (total > GATE_LIMITS.maxCombinedNeckTwistDeg)
          add('joint_limit', name, i, 'combined_neck_twist', total, GATE_LIMITS.maxCombinedNeckTwistDeg, 'deg');
      }
      if (name.endsWith('LowerLeg') || name.endsWith('LowerArm')) {
        const knee = name.endsWith('LowerLeg');
        const { bend, twist, offAxis } = hinge(q, knee, name.startsWith('left'));
        const [maxBend, maxTwist, maxOffAxis] = knee
          ? [GATE_LIMITS.maxKneeFlexDeg, GATE_LIMITS.maxShinTwistDeg, GATE_LIMITS.maxKneeOffAxisDeg]
          : [GATE_LIMITS.maxElbowFlexDeg, GATE_LIMITS.maxForearmTwistDeg, GATE_LIMITS.maxElbowOffAxisDeg];
        if (bend < GATE_LIMITS.minHingeFlexDeg)
          add('joint_limit', name, i, 'reverse_bend', bend, GATE_LIMITS.minHingeFlexDeg, 'deg');
        if (bend > maxBend) add('joint_limit', name, i, 'excess_bend', bend, maxBend, 'deg');
        if (Math.abs(twist) > maxTwist) add('joint_limit', name, i, 'hinge_twist', Math.abs(twist), maxTwist, 'deg');
        if (offAxis > maxOffAxis) add('joint_limit', name, i, 'hinge_off_axis', offAxis, maxOffAxis, 'deg');
      }
    }
    // ガタつきは i→i+1 の跳ねとして、その両端のフレームに付ける。
    const moves = b.position.slice(1).map((p, i) => minus(p, b.position[i]));
    const turns = b.rotation.slice(1).map((q, i) => turn(b.rotation[i], q));
    for (let i = 1; i + 1 < moves.length; i++) {
      const position = flip(moves, i), rotation = flip(turns, i);
      for (const frame of [i, i + 1]) {
        if (position > GATE_LIMITS.positionFlipM)
          add('jitter', name, frame, 'position_flip', position, GATE_LIMITS.positionFlipM, 'm');
        if (rotation > GATE_LIMITS.rotationFlipDeg)
          add('jitter', name, frame, 'rotation_flip', rotation, GATE_LIMITS.rotationFlipDeg, 'deg');
      }
    }
  }
  // 足で体を支える立位では接地滑りを測る。座位・寝姿では足を砂の上で動かせる。
  // 踵が上がる/踏み込む瞬間は支持点が移るので、点ごとに接地を判定し、移った点の速度で落とさない。
  const seated = (i: number) => bones.hips.position[i][1] < input.rest.hips[1] * GATE_LIMITS.seatedHipsRatio;
  for (const name of SOLE_BONES) {
    const points = skin[name];
    if (!points) continue;
    const soleMin = Math.min(...points.rest.map(p => p[1]));
    for (const [j, restPoint] of points.rest.entries()) {
      // 上面の頂点は接地センサーにはしない。足裏の低い点だけを見る。
      if (restPoint[1] - soleMin > GATE_LIMITS.soleBandM) continue;
      const frames = points.position.map(row => row[j]);
      const contact = (i: number) => {
        const y = frames[i][1];
        const vertical = i ? Math.abs(y - frames[i - 1][1]) * input.fps : 0;
        return y >= -GATE_LIMITS.penetrationM && y <= GATE_LIMITS.contactHeightM
          && vertical < GATE_LIMITS.maxContactVerticalSpeedMps;
      };
      let start = -1;
      for (let i = 0; i < count!; i++) {
        if (!contact(i) || seated(i)) { start = -1; continue; }
        if (start < 0) start = i;
        const origin = start + GATE_LIMITS.contactStableFrames - 1;
        if (i <= origin) continue;
        const speed = horizontal(frames[i - 1], frames[i]) * input.fps;
        const drift = horizontal(frames[origin], frames[i]);
        if (speed > GATE_LIMITS.slidingSpeedMps)
          add('foot_slide', name, i, 'contact_speed', speed, GATE_LIMITS.slidingSpeedMps, 'm/s');
        if (drift > GATE_LIMITS.slidingDriftM)
          add('foot_slide', name, i, 'contact_drift', drift, GATE_LIMITS.slidingDriftM, 'm');
      }
    }
  }
  return finish();
}
