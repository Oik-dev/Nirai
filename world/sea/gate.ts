// D0「関門」：本人のVRMに適用済みの世界座標と、正規化ボーンの局所回転だけを見る。
// 動きの名前を知らない純関数。呼び出し元が描画・書き込みを始める前に pass を確認する。
export type Vec3 = [number, number, number];
export type Quat = [number, number, number, number];
export type BoneFrames = { position: Vec3[]; rotation: Quat[] };
export type GateInput = { fps: number; bones: Record<string, BoneFrames>; rest: Record<string, Vec3> };
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
// Kimodo の foot_skate_from_height: https://github.com/mtderosier/AI_kimodo_AnimGen/blob/main/docs/source/benchmark/metrics.md
// 高さのみの接地推定は限界がある: Mourot et al., UnderPressure, CGF 2022, doi:10.1111/cgf.14635。
export const GATE_LIMITS = Object.freeze({
  minFps: 10, maxFps: 120,
  minDurationS: 0.25, maxDurationS: 120,
  minStatureM: 0.45, maxStatureM: 2.5,
  minSegmentM: 0.015, maxSegmentStretchRatio: 0.25,
  penetrationM: 0.025, // 骨の付け根の海底下への侵入。メッシュ接触とは別
  contactHeightM: 0.035, // 足首/つま先それぞれの立位基準高からの距離。Kimodoは高さ0.05mを用いる
  slidingSpeedMps: 0.35, // 接地した足のXZ速度。WACV/Kimodoの測り方に基づく要調整値
  slidingDriftM: 0.04, // 接地区間内の累積ずれ。遅い滑りも見逃さない
  maxCoreAccelerationMps2: 45,
  maxExtremityAccelerationMps2: 90,
  maxNeckTwistDeg: 85,
  maxNeckSwingDeg: 75,
  maxKneeFlexDeg: 155,
  maxElbowFlexDeg: 155,
  minHingeFlexDeg: -12, // 逆曲がりの許容（正規化VRMのT姿勢から）
  maxKneeOffAxisDeg: 35, // 膝はX軸の蝶番（足首の回転は含めない）
  maxElbowOffAxisDeg: 55, // 肘はY軸の蝶番。腕の回旋は上腕側で行う
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
const DISTAL = /(?:Foot|Toes|Hand|LowerArm|LowerLeg|Eye|Jaw|Finger|Thumb|Index|Middle|Ring|Little)/;
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
  for (const bone of REQUIRED) if (!vector(input.rest[bone], 3)) {
    add('invalid_data', bone, 0, 'rest_position', null, 3, 'components');
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
        add('penetration', name, i, 'below_ground', -point[1], GATE_LIMITS.penetrationM, 'm');
      const q = b.rotation[i];
      if (name === 'neck' || (name === 'head' && !bones.neck)) {
        const yaw = Math.abs(hingeAngles(q, 1).bend);
        const pitchRoll = hingeAngles(q, 1).offAxis;
        if (yaw > GATE_LIMITS.maxNeckTwistDeg)
          add('joint_limit', name, i, 'neck_twist', yaw, GATE_LIMITS.maxNeckTwistDeg, 'deg');
        if (pitchRoll > GATE_LIMITS.maxNeckSwingDeg)
          add('joint_limit', name, i, 'neck_swing', pitchRoll, GATE_LIMITS.maxNeckSwingDeg, 'deg');
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
        const offAxisMax = knee ? GATE_LIMITS.maxKneeOffAxisDeg : GATE_LIMITS.maxElbowOffAxisDeg;
        if (angles.offAxis > offAxisMax)
          add('joint_limit', name, i, 'hinge_off_axis', angles.offAxis, offAxisMax, 'deg');
      }
      if (i > 0 && i < count! - 1) {
        const prev = b.position[i - 1], next = b.position[i + 1];
        const accel = Math.hypot(...point.map((x, k) => (next[k] - 2 * x + prev[k]) * input.fps ** 2));
        const limit = DISTAL.test(name) ? GATE_LIMITS.maxExtremityAccelerationMps2
          : GATE_LIMITS.maxCoreAccelerationMps2;
        if (accel > limit) add('jitter', name, i, 'acceleration', accel, limit, 'm/s²');
      }
    }
  }
  for (const name of ['leftFoot', 'rightFoot', 'leftToes', 'rightToes']) {
    if (!bones[name]) continue;
    const frames = bones[name].position;
    const height = input.rest[name][1] + GATE_LIMITS.contactHeightM;
    let start = -1;
    for (let i = 0; i < count!; i++) {
      const grounded = frames[i][1] <= height && frames[i][1] >= -GATE_LIMITS.penetrationM;
      if (!grounded) { start = -1; continue; }
      if (start < 0) { start = i; continue; }
      const speed = horizontal(frames[i - 1], frames[i]) * input.fps;
      const drift = horizontal(frames[start], frames[i]);
      if (speed > GATE_LIMITS.slidingSpeedMps)
        add('foot_slide', name, i, 'contact_speed', speed, GATE_LIMITS.slidingSpeedMps, 'm/s');
      if (drift > GATE_LIMITS.slidingDriftM)
        add('foot_slide', name, i, 'contact_drift', drift, GATE_LIMITS.slidingDriftM, 'm');
    }
  }
  return finish(issues);
}
