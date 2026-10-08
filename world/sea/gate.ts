// D0「関門」：本人のVRMに適用済みの世界座標と、VRM 1.0正規化ボーンの局所回転を見る。
// 動きの名前を知らない純関数。呼び出し元が描画・書き込みを始める前に pass を確認する。
export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];
export type BoneFrames = { position: Vec3[]; rotation: Quat[] };
// 体表面の固定サンプル。restと各フレームの点は同じ順番（同じ頂点）で並べる。
// foot/toesには足裏の「かかと」「拇趾球」「つま先」を含めること。各骨最大64点。
// samplesがない場合は骨の立位restから概算するが、足裏の接触判定は近似になる。
export type SkinSamples = { rest: Vec3[]; position: Vec3[][] };
export type GateInput = {
  fps: number; bones: Record<string, BoneFrames>; rest: Record<string, Vec3>;
  skin?: Record<string, SkinSamples>;
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
  penetrationM: 0.008, // 体表点の海底下侵入は8mm以上で検出する
  inferredSolePenetrationM: 0.012, // 足裏の点が無い場合、直立・足平坦時のrest位置から仮推定
  skinContactHeightM: 0.012, // 実測の足裏点から支持面を読む
  inferredContactHeightM: 0.014, // 骨の付け根からの推定接地帯（着地直前と混同しない）
  contactStableFrames: 3, // 接地・離地の境界を判定しない
  maxContactVerticalSpeedMps: 0.12,
  slidingSpeedMps: 0.35, // 接地区間に限って見る（高さだけで即判定しない）
  slidingDriftM: 0.04,
  positionSnapM: 0.011, // 単フレームで跳ね戻る局所的な位置ノイズ（加速度とは区別）
  rotationSnapDeg: 35, // 1フレームだけ回って戻る不自然な回転
  maxNeckTwistDeg: 85,
  maxCombinedNeckTwistDeg: 125,
  maxNeckSwingDeg: 75,
  maxKneeFlexDeg: 165,
  maxElbowFlexDeg: 155,
  minHingeFlexDeg: -12, // 逆曲がりの許容（正規化VRMのT姿勢から）
  maxKneeOffAxisDeg: 35, // 膝はX軸の蝶番（足首の回転は含めない）
  maxElbowOffAxisDeg: 55, // 肘はY軸の蝶番。X軸の前腕回外・回内は別に扱う
  maxForearmTwistDeg: 115,
  maxSurfaceSamples: 64,
  quaternionNormTolerance: 0.03,
});

const REQUIRED = [
  'hips', 'spine', 'head',
  'leftUpperLeg', 'leftLowerLeg', 'leftFoot', 'rightUpperLeg', 'rightLowerLeg', 'rightFoot',
  'leftUpperArm', 'leftLowerArm', 'leftHand', 'rightUpperArm', 'rightLowerArm', 'rightHand',
] as const;
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
const degrees = (r: number): number => r * 180 / Math.PI;

// q と -q は同じ回転。正規化して w>=0 に寄せ、0〜180度の回転を検査する。
function aligned(q: Quat): Quat {
  return q[3] < 0 ? [-q[0], -q[1], -q[2], -q[3]] : q;
}
function hingeAngles(q: Quat, axis: 0 | 1): { bend: number; offAxis: number } {
  const a = aligned(q);
  const bend = degrees(2 * Math.atan2(a[axis], a[3]));
  const offAxis = degrees(2 * Math.atan2(
    Math.hypot(...a.slice(0, 3).filter((_, i) => i !== axis)), Math.hypot(a[axis], a[3]),
  ));
  return { bend, offAxis };
}
function rotationDistance(a: Quat, b: Quat): number {
  const dot = Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]);
  return degrees(2 * Math.acos(Math.min(1, Math.max(0, dot))));
}
function snapBack(a: Vec3, b: Vec3, c: Vec3): number {
  const v1 = b.map((x, i) => x - a[i]), v2 = c.map((x, i) => x - b[i]);
  const len1 = Math.hypot(...v1), len2 = Math.hypot(...v2);
  if (!len1 || !len2 || distance(a, c) > .45 * Math.min(len1, len2)) return 0;
  const cosine = v1.reduce((s, x, i) => s + x * v2[i], 0) / (len1 * len2);
  return cosine < -.85 ? Math.min(len1, len2) : 0;
}
function isolatedSnap(frames: Vec3[], i: number): number {
  // 2cmの単発の跳び、または±8mmの毎フレーム往復だけを扱う。
  // 3〜6フレームかけて円滑に折り返す速い腕の動きは、加速度が大きくても不合格にしない。
  if (i < 2 || i + 2 >= frames.length) return 0;
  const size = snapBack(frames[i - 1], frames[i], frames[i + 1]);
  if (size <= GATE_LIMITS.positionSnapM) return 0;
  const tolerance = size * .3;
  const isolated = distance(frames[i - 2], frames[i - 1]) < tolerance
    && distance(frames[i + 1], frames[i + 2]) < tolerance;
  const alternating = distance(frames[i - 2], frames[i]) < tolerance
    && distance(frames[i], frames[i + 2]) < tolerance;
  return isolated || alternating ? size : 0;
}
const finish = (issues: GateIssue[]): GateResult => ({ pass: issues.length === 0, issues });

export function checkMotion(input: GateInput): GateResult {
  const issues: GateIssue[] = [];
  const lastByKey = new Map<string, GateIssue>();
  const add = (kind: GateIssue['kind'], bone: string | null, frame: number, metric: string,
    value: number | null, limit: number, unit: string) => {
    // 同じ警告の連続区間をひとつにして、D0の絵に範囲を重ねられるようにする。
    const key = JSON.stringify([kind, bone, metric, limit, unit]);
    const prev = lastByKey.get(key);
    if (prev && prev.toFrame + 1 === frame) {
      prev.toFrame = frame;
      if (value !== null && (prev.value === null || Math.abs(value) > Math.abs(prev.value))) prev.value = value;
    } else {
      const issue = { kind, bone, fromFrame: frame, toFrame: frame, metric, value, limit, unit };
      issues.push(issue);
      lastByKey.set(key, issue);
    }
  };
  if (!input || !finite(input.fps) || input.fps < GATE_LIMITS.minFps || input.fps > GATE_LIMITS.maxFps
      || !input.bones || typeof input.bones !== 'object' || Array.isArray(input.bones)
      || !input.rest || typeof input.rest !== 'object' || Array.isArray(input.rest)) {
    add('invalid_data', null, 0, 'fps_or_input', input && finite(input.fps) ? input.fps : null, GATE_LIMITS.maxFps, 'fps');
    return finish(issues);
  }
  const bones = input.bones;
  const names = Object.keys(bones);
  const count = bones.hips?.position?.length;
  for (const bone of REQUIRED) {
    if (!Object.hasOwn(bones, bone)) add('missing_bone', bone, 0, 'required', 0, 1, 'bones');
  }
  if (!Number.isSafeInteger(count) || count! < 1) {
    add('invalid_data', 'hips', 0, 'frames', count ?? null, 1, 'frames');
    return finish(issues);
  }
  const duration = (count! - 1) / input.fps;
  if (duration < GATE_LIMITS.minDurationS) add('duration', null, 0, 'seconds_min', duration, GATE_LIMITS.minDurationS, 's');
  if (duration > GATE_LIMITS.maxDurationS) {
    add('duration', null, count! - 1, 'seconds_max', duration, GATE_LIMITS.maxDurationS, 's');
    // 外れた巨大入力を全フレーム走査せず入口で拒否する。
    return finish(issues);
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
  // 追加の体表点は順序固定の skinned vertices。指・目・顎を付けなくてもよい。
  if (input.skin !== undefined) {
    if (!input.skin || typeof input.skin !== 'object' || Array.isArray(input.skin)) {
      add('invalid_data', null, 0, 'skin', null, 1, 'object');
    } else for (const [name, samples] of Object.entries(input.skin)) {
      if (!bones[name] || !samples || !Array.isArray(samples.rest) || !Array.isArray(samples.position)
        || samples.rest.length < 1 || samples.rest.length > GATE_LIMITS.maxSurfaceSamples
        || samples.position.length !== count) {
        add('invalid_data', name, 0, 'skin_shape', samples?.rest?.length ?? null, GATE_LIMITS.maxSurfaceSamples, 'samples');
        continue;
      }
      for (const [j, rest] of samples.rest.entries()) if (!vector(rest, 3))
        add('invalid_data', name, 0, `skin_rest_${j}`, null, 3, 'components');
      for (let i = 0; i < count!; i++) {
        if (!Array.isArray(samples.position[i]) || samples.position[i].length !== samples.rest.length) {
          add('invalid_data', name, i, 'skin_points', samples.position[i]?.length ?? null, samples.rest.length, 'samples');
        } else for (const [j, point] of samples.position[i].entries()) {
          if (!vector(point, 3)) add('invalid_data', name, i, `skin_point_${j}`, null, 3, 'components');
        }
      }
    }
  }
  if (issues.some(x => x.kind === 'invalid_data' || x.kind === 'missing_bone')) return finish(issues);
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
      const samples = input.skin?.[name]?.position[i];
      if (samples) for (const [j, p] of samples.entries()) {
        if (p[1] < -GATE_LIMITS.penetrationM)
          add('penetration', name, i, `skin_point_${j}_below_ground`, -p[1], GATE_LIMITS.penetrationM, 'm');
      }
      const q = b.rotation[i];
      if (name === 'neck' || (name === 'head' && !bones.neck)) {
        const yaw = Math.abs(hingeAngles(q, 1).bend);
        const pitchRoll = hingeAngles(q, 1).offAxis;
        if (yaw > GATE_LIMITS.maxNeckTwistDeg)
          add('joint_limit', name, i, 'neck_twist', yaw, GATE_LIMITS.maxNeckTwistDeg, 'deg');
        if (pitchRoll > GATE_LIMITS.maxNeckSwingDeg)
          add('joint_limit', name, i, 'neck_swing', pitchRoll, GATE_LIMITS.maxNeckSwingDeg, 'deg');
      }
      if (name === 'head' && bones.neck) {
        const neckYaw = hingeAngles(bones.neck.rotation[i], 1).bend;
        const headYaw = hingeAngles(q, 1).bend;
        const total = Math.abs(neckYaw + headYaw);
        if (total > GATE_LIMITS.maxCombinedNeckTwistDeg)
          add('joint_limit', name, i, 'combined_neck_twist', total, GATE_LIMITS.maxCombinedNeckTwistDeg, 'deg');
      }
      if (name.endsWith('LowerLeg') || name.endsWith('LowerArm')) {
        const knee = name.endsWith('LowerLeg');
        const axis = knee ? 0 : 1;
        const angles = hingeAngles(q, axis);
        const bend = knee ? angles.bend : angles.bend * (name.startsWith('left') ? -1 : 1);
        const maximum = knee ? GATE_LIMITS.maxKneeFlexDeg : GATE_LIMITS.maxElbowFlexDeg;
        if (bend < GATE_LIMITS.minHingeFlexDeg)
          add('joint_limit', name, i, 'reverse_bend', bend, GATE_LIMITS.minHingeFlexDeg, 'deg');
        if (bend > maximum)
          add('joint_limit', name, i, 'excess_bend', bend, maximum, 'deg');
        // 前腕のX回転は手のひらを返す回内/回外で、ひじの逆曲がりではない。
        const offAxis = knee ? angles.offAxis : degrees(2 * Math.atan2(Math.abs(aligned(q)[2]), Math.hypot(aligned(q)[1], aligned(q)[3])));
        const offAxisMax = knee ? GATE_LIMITS.maxKneeOffAxisDeg : GATE_LIMITS.maxElbowOffAxisDeg;
        if (offAxis > offAxisMax)
          add('joint_limit', name, i, 'hinge_off_axis', offAxis, offAxisMax, 'deg');
        if (!knee) {
          const twist = Math.abs(hingeAngles(q, 0).bend);
          if (twist > GATE_LIMITS.maxForearmTwistDeg)
            add('joint_limit', name, i, 'forearm_twist', twist, GATE_LIMITS.maxForearmTwistDeg, 'deg');
        }
      }
      if (i > 0 && i < count! - 1) {
        // 素早い滑らかな動きの加速度は許す。往復の鋭い跳ねは振幅で見る。
        const snap = isolatedSnap(b.position, i);
        if (snap > GATE_LIMITS.positionSnapM)
          add('jitter', name, i, 'position_snap', snap, GATE_LIMITS.positionSnapM, 'm');
        const prevQ = b.rotation[i - 1], nextQ = b.rotation[i + 1];
        const angleA = rotationDistance(prevQ, q), angleB = rotationDistance(q, nextQ);
        const around = rotationDistance(prevQ, nextQ);
        if (Math.min(angleA, angleB) > GATE_LIMITS.rotationSnapDeg
          && around < Math.min(angleA, angleB) * .5)
          add('jitter', name, i, 'rotation_snap', Math.min(angleA, angleB), GATE_LIMITS.rotationSnapDeg, 'deg');
      }
    }
  }
  // 骨の先端だけでは足裏と地面の接触を特定できない。体表の実測点があればそれを優先。
  // 無いときは、足首とつま先が共にT姿勢の接地高にある「平坦な支持」だけを判定する。
  // 踵が上がる/踏み込む瞬間は支持点が移るので、そこで速度だけを見て落とさない。
  const support = (points: Vec3[], contact: (i: number) => boolean, name: string, suffix: string) => {
    let start = -1;
    for (let i = 0; i < count!; i++) {
      if (!contact(i)) { start = -1; continue; }
      if (start < 0) start = i;
      if (i - start + 1 < GATE_LIMITS.contactStableFrames) continue;
      const origin = start + GATE_LIMITS.contactStableFrames - 1;
      if (i <= origin) continue;
      const speed = horizontal(points[i - 1], points[i]) * input.fps;
      const drift = horizontal(points[origin], points[i]);
      if (speed > GATE_LIMITS.slidingSpeedMps)
        add('foot_slide', name, i, `${suffix}contact_speed`, speed, GATE_LIMITS.slidingSpeedMps, 'm/s');
      if (drift > GATE_LIMITS.slidingDriftM)
        add('foot_slide', name, i, `${suffix}contact_drift`, drift, GATE_LIMITS.slidingDriftM, 'm');
    }
  };
  for (const side of ['left', 'right']) {
    const foot = `${side}Foot`, toes = `${side}Toes`;
    const hasSkin = !!(input.skin?.[foot] || input.skin?.[toes]);
    const upright = (i: number) => {
      const hip = bones.hips.position[i], head = bones.head.position[i];
      const delta = distance(hip, head);
      return delta > 0 && (head[1] - hip[1]) / delta > .70;
    };
    for (const name of [foot, toes]) {
      if (!bones[name]) continue;
      const frames = bones[name].position;
      if (!input.skin?.[name] && !hasSkin) {
        // 直立して足を立位と同じ向きに保っている場合、地面下の足裏位置をrestから推定する。
        // 横になる・足を持ち上げる姿勢は体表点がない限り、骨だけで断定できない。
        for (let i = 0; i < count!; i++) {
          if (upright(i) && rotationDistance(bones[foot].rotation[i], [0, 0, 0, 1]) < 12
            && frames[i][1] < input.rest[name][1] - GATE_LIMITS.inferredSolePenetrationM) {
            add('penetration', name, i, 'inferred_sole_below_ground',
              input.rest[name][1] - frames[i][1], GATE_LIMITS.inferredSolePenetrationM, 'm');
          }
        }
      }
      if (hasSkin) continue;
      const contactBone = (bone: string, i: number) =>
        !!bones[bone] && upright(i) && Math.abs(bones[bone].position[i][1] - input.rest[bone][1])
          <= GATE_LIMITS.inferredContactHeightM;
      support(frames, i => contactBone(foot, i) && (!bones[toes] || contactBone(toes, i)),
        name, '');
    }
    if (!hasSkin) continue;
    for (const name of [foot, toes]) {
      const points = input.skin?.[name];
      if (!points) continue;
      const soleMin = Math.min(...points.rest.map(p => p[1]));
      for (const [j, restPoint] of points.rest.entries()) {
        // 上面の頂点は接地センサーにはしない。足裏の低い点だけを見る。
        if (restPoint[1] - soleMin > .025) continue;
        const frames = points.position.map(row => row[j]);
        support(frames, i => {
          const y = frames[i][1];
          const before = i ? Math.abs(y - frames[i - 1][1]) * input.fps : 0;
          return y >= -GATE_LIMITS.penetrationM && y <= GATE_LIMITS.skinContactHeightM
            && before < GATE_LIMITS.maxContactVerticalSpeedMps;
        }, name, `skin_point_${j}_`);
      }
    }
  }
  return finish(issues);
}
