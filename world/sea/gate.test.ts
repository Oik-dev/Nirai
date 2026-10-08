import assert from 'node:assert/strict';
import test from 'node:test';
import { checkMotion, GATE_LIMITS, type BoneFrames, type GateInput, type Quat, type Vec3 } from './gate.ts';

const REST: Record<string, Vec3> = {
  hips: [0, 1, 0], spine: [0, 1.2, 0], head: [0, 1.63, 0],
  leftUpperLeg: [.13, .95, 0], leftLowerLeg: [.13, .55, 0], leftFoot: [.13, .1, 0],
  rightUpperLeg: [-.13, .95, 0], rightLowerLeg: [-.13, .55, 0], rightFoot: [-.13, .1, 0],
  leftUpperArm: [.18, 1.4, 0], leftLowerArm: [.46, 1.4, 0], leftHand: [.74, 1.4, 0],
  rightUpperArm: [-.18, 1.4, 0], rightLowerArm: [-.46, 1.4, 0], rightHand: [-.74, 1.4, 0],
};
const identity: Quat = [0, 0, 0, 1];
const around = (axis: 'x' | 'y', deg: number): Quat => {
  const a = deg * Math.PI / 360;
  return axis === 'x' ? [Math.sin(a), 0, 0, Math.cos(a)] : [0, Math.sin(a), 0, Math.cos(a)];
};
function motion(frames = 61, fps = 30): GateInput {
  const bones: Record<string, BoneFrames> = {};
  for (const [bone, rest] of Object.entries(REST)) {
    bones[bone] = {
      position: Array.from({ length: frames }, () => [...rest] as Vec3),
      rotation: Array.from({ length: frames }, () => [...identity] as Quat),
    };
  }
  return { fps, bones, rest: Object.fromEntries(Object.entries(REST).map(([bone, v]) => [bone, [...v]])) };
}
function expectPass(title: string, clip: GateInput): void {
  const result = checkMotion(clip);
  assert.equal(result.pass, true, `${title}: ${JSON.stringify(result.issues.slice(0, 5))}`);
  assert.deepEqual(result.issues, []);
}
function expectIssue(clip: GateInput, kind: string, bone?: string, metric?: string) {
  const result = checkMotion(clip);
  assert.equal(result.pass, false);
  const issue = result.issues.find(x => x.kind === kind
    && (bone === undefined || x.bone === bone) && (metric === undefined || x.metric === metric));
  assert.ok(issue, `missing ${kind}/${bone ?? '*'}/${metric ?? '*'}: ${JSON.stringify(result.issues.slice(0, 8))}`);
  assert.ok(issue.fromFrame >= 0 && issue.toFrame >= issue.fromFrame);
  assert.ok(typeof issue.limit === 'number' && Number.isFinite(issue.limit));
  return issue;
}

test('T姿勢で静止・立ったまま小さく揺れる動きは通る', () => {
  expectPass('T姿勢', motion());
  const clip = motion();
  for (let i = 0; i < 61; i++) {
    const sway = .008 * Math.sin(i * Math.PI / 30);
    for (const bone of ['hips', 'spine', 'head', 'leftUpperArm', 'rightUpperArm', 'leftLowerArm',
      'rightLowerArm', 'leftHand', 'rightHand']) clip.bones[bone].position[i][0] += sway;
  }
  expectPass('揺れ', clip);
});

test('その場で足踏みは、接地から離れた区間の移動を滑りに数えない', () => {
  const clip = motion();
  for (let i = 0; i < 61; i++) {
    const lift = Math.sin(Math.PI * i / 60) ** 2;
    clip.bones.leftFoot.position[i][1] += .12 * lift;
    clip.bones.leftLowerLeg.position[i][1] += .05 * lift;
  }
  expectPass('足踏み', clip);
});

test('足が地面から離れた泳ぎと浮遊は滑り扱いにしない', () => {
  const clip = motion();
  for (const [name, bone] of Object.entries(clip.bones)) {
    for (let i = 0; i < bone.position.length; i++) {
      const t = i / clip.fps;
      bone.position[i][1] += 1 + .03 * Math.sin(t * 2);
      bone.position[i][0] += .14 * Math.sin(t * 1.7);
      if (name === 'hips') bone.rotation[i] = around('x', 75);
    }
  }
  expectPass('泳ぎ・浮遊', clip);
});

test('砂に横たわって静かに呼吸する（体の傾き・床への接触があっても通る）', () => {
  const clip = motion();
  for (const [name, bone] of Object.entries(clip.bones)) {
    for (let i = 0; i < bone.position.length; i++) {
      const [, y, z] = REST[name];
      bone.position[i] = [REST[name][0], .12 - z + .003 * Math.sin(i / 8), y - 1];
      if (name === 'hips') bone.rotation[i] = around('x', 90);
    }
  }
  expectPass('砂に横たわる', clip);
});

test('接地中の速い滑りを検出し、骨・フレーム・測定速度を返す', () => {
  const clip = motion();
  for (let i = 0; i < 61; i++) clip.bones.leftFoot.position[i][0] += .02 * i;
  const issue = expectIssue(clip, 'foot_slide', 'leftFoot', 'contact_speed');
  assert.ok(issue.value! > GATE_LIMITS.slidingSpeedMps);
  assert.ok(issue.fromFrame > 0);
  assert.ok(issue.toFrame > issue.fromFrame, '連続した滑りのフレーム範囲をまとめる');
  assert.equal(issue.unit, 'm/s');
});

test('ゆっくりでも長い距離の足滑りを見逃さない', () => {
  const clip = motion();
  for (let i = 0; i < 61; i++) clip.bones.rightFoot.position[i][0] += .002 * i;
  assert.ok(.002 * clip.fps < GATE_LIMITS.slidingSpeedMps);
  const issue = expectIssue(clip, 'foot_slide', 'rightFoot', 'contact_drift');
  assert.ok(issue.value! >= GATE_LIMITS.slidingDriftM);
});

test('足のつま先があるときも接地を測る。つま先がないVRMは通す', () => {
  const clip = motion();
  clip.bones.leftToes = {
    position: Array.from({ length: 61 }, (_, i) => [.13, .02, .16 + .015 * i] as Vec3),
    rotation: Array.from({ length: 61 }, () => [...identity]),
  };
  clip.rest.leftToes = [.13, .02, .16];
  expectIssue(clip, 'foot_slide', 'leftToes');
  delete clip.bones.leftToes;
  delete clip.rest.leftToes;
  expectPass('つま先なし', clip);
});

test('海底への侵入は足が接地していなくても、すべての骨で検出する', () => {
  const clip = motion();
  for (const bone of Object.values(clip.bones)) for (const p of bone.position) p[1] += 1;
  clip.bones.leftHand.position[20][1] = -.08;
  const issue = expectIssue(clip, 'penetration', 'leftHand', 'below_ground');
  assert.equal(issue.fromFrame, 20);
  assert.ok(issue.value! > GATE_LIMITS.penetrationM);
});

test('膝の逆曲がりと過剰屈曲を、正規化ボーンの局所回転で検出する', () => {
  const reverse = motion();
  for (const r of reverse.bones.leftLowerLeg.rotation) r.splice(0, 4, ...around('x', -27));
  const issue = expectIssue(reverse, 'joint_limit', 'leftLowerLeg', 'reverse_bend');
  assert.ok(issue.value! < 0);
  const over = motion();
  for (const r of over.bones.rightLowerLeg.rotation) r.splice(0, 4, ...around('x', 170));
  expectIssue(over, 'joint_limit', 'rightLowerLeg', 'excess_bend');
});

test('肘の逆曲がり・首のねじれすぎ・膝の軸外回転を検出', () => {
  const elbow = motion();
  elbow.bones.leftLowerArm.rotation[12] = around('y', 35);
  expectIssue(elbow, 'joint_limit', 'leftLowerArm', 'reverse_bend');
  const neck = motion();
  neck.bones.neck = {
    position: Array.from({ length: 61 }, () => [0, 1.49, 0] as Vec3),
    rotation: Array.from({ length: 61 }, () => around('y', 100)),
  };
  neck.rest.neck = [0, 1.49, 0];
  expectIssue(neck, 'joint_limit', 'neck', 'neck_twist');
  const axis = motion();
  axis.bones.rightLowerLeg.rotation[9] = around('y', 50);
  expectIssue(axis, 'joint_limit', 'rightLowerLeg', 'hinge_off_axis');
});

test('単発の位置飛びは加速度の跳ねとして検出する', () => {
  const clip = motion();
  clip.bones.spine.position[25][0] += .14;
  const issue = expectIssue(clip, 'jitter', 'spine');
  assert.ok(issue.fromFrame <= 25 && issue.toFrame >= 24);
  assert.equal(issue.unit, 'm/s²');
});

test('人型必須骨は厳密に見るが胸・首・指・目・顎は必須にしない', () => {
  const clip = motion();
  expectPass('胸首なし', clip);
  delete clip.bones.leftHand;
  expectIssue(clip, 'missing_bone', 'leftHand');
});

test('欠損・NaN・回転の非単位長・フレーム長不一致を除外する', () => {
  const nan = motion();
  nan.bones.head.position[2][1] = NaN;
  expectIssue(nan, 'invalid_data', 'head', 'position');
  const mismatch = motion();
  mismatch.bones.spine.rotation.pop();
  expectIssue(mismatch, 'invalid_data', 'spine', 'frame_count');
  const quat = motion();
  quat.bones.leftHand.rotation[4] = [0, 0, 0, 5];
  expectIssue(quat, 'invalid_data', 'leftHand', 'quaternion_norm');
  const rest = motion();
  delete rest.rest.rightFoot;
  expectIssue(rest, 'invalid_data', 'rightFoot', 'rest_position');
});

test('時間・fps・体寸法・ボーン間の不自然な伸縮を検出する', () => {
  expectIssue(motion(4), 'duration');
  expectIssue(motion(61, 3), 'invalid_data');
  expectIssue(motion(6001, 30), 'duration');
  const short = motion();
  short.rest.head[1] = 0.3;
  expectIssue(short, 'stature');
  const stretch = motion();
  stretch.bones.rightHand.position[30][0] += 1;
  expectIssue(stretch, 'bone_length', 'rightHand');
});

test('判定は入力を変更せず、同じ入力から同じ結果を返す', () => {
  const clip = motion();
  const before = structuredClone(clip);
  assert.deepEqual(checkMotion(clip), checkMotion(clip));
  assert.deepEqual(clip, before);
});
