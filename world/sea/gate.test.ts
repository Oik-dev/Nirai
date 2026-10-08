import assert from 'node:assert/strict';
import test from 'node:test';
import { checkMotion, GATE_LIMITS, type BoneFrames, type GateInput, type Quat, type SkinSamples, type Vec3 } from './gate.ts';

const REST: Record<string, Vec3> = {
  hips: [0, 1, 0], spine: [0, 1.2, 0], head: [0, 1.63, 0],
  leftUpperLeg: [.13, .95, 0], leftLowerLeg: [.13, .55, 0], leftFoot: [.13, .1, 0],
  rightUpperLeg: [-.13, .95, 0], rightLowerLeg: [-.13, .55, 0], rightFoot: [-.13, .1, 0],
  leftUpperArm: [.18, 1.4, 0], leftLowerArm: [.46, 1.4, 0], leftHand: [.74, 1.4, 0],
  rightUpperArm: [-.18, 1.4, 0], rightLowerArm: [-.46, 1.4, 0], rightHand: [-.74, 1.4, 0],
};
const identity: Quat = [0, 0, 0, 1];
const around = (axis: 'x' | 'y' | 'z', deg: number): Quat => {
  const a = deg * Math.PI / 360, s = Math.sin(a);
  return [axis === 'x' ? s : 0, axis === 'y' ? s : 0, axis === 'z' ? s : 0, Math.cos(a)];
};
// a·b（b を先に、a を後に回す）。
const times = (a: Quat, b: Quat): Quat => [
  a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
  a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
  a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
  a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2],
];
const plus = (a: Vec3, b: Vec3): Vec3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const minus = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
function motion(frames = 61, fps = 30): GateInput {
  const bones: Record<string, BoneFrames> = {};
  for (const [bone, rest] of Object.entries(REST)) {
    bones[bone] = {
      position: Array.from({ length: frames }, () => [...rest] as Vec3),
      rotation: Array.from({ length: frames }, () => [...identity] as Quat),
    };
  }
  return { fps, bones, rest: Object.fromEntries(Object.entries(REST).map(([bone, v]) => [bone, [...v]])), skin: {} };
}
// 足とつま先の骨の真下（地面の高さ）に、かかと・拇趾球・つま先の足裏の点を置き、骨の平行移動に付いて動かす。
const SOLE: Record<string, Vec3[]> = {
  Foot: [[0, 0, -.06], [-.02, 0, .04], [.02, 0, .04], [0, 0, .1]],
  Toes: [[0, 0, 0], [0, 0, .03]],
};
function soles(clip: GateInput): Record<string, SkinSamples> {
  const skin: Record<string, SkinSamples> = {};
  for (const side of ['left', 'right']) for (const part of ['Foot', 'Toes']) {
    const name = side + part, rest = clip.rest[name], bone = clip.bones[name];
    if (!rest || !bone) continue;
    const points = SOLE[part].map(p => [rest[0] + p[0], p[1], rest[2] + p[2]] as Vec3);
    skin[name] = { rest: points, position: bone.position.map(at => points.map(p => plus(p, minus(at, rest)))) };
  }
  return skin;
}
// 足裏の点を付けて判定する。テストが自分で置いた体表点が優先。
const check = (clip: GateInput) => checkMotion({ ...clip, skin: { ...soles(clip), ...clip.skin } });
function expectPass(title: string, clip: GateInput): void {
  const result = check(clip);
  assert.equal(result.pass, true, `${title}: ${JSON.stringify(result.issues.slice(0, 5))}`);
  assert.deepEqual(result.issues, []);
}
function expectIssue(clip: GateInput, kind: string, bone?: string, metric?: string) {
  const result = check(clip);
  assert.equal(result.pass, false);
  const issue = result.issues.find(x => x.kind === kind
    && (bone === undefined || x.bone === bone) && (metric === undefined || x.metric === metric));
  assert.ok(issue, `missing ${kind}/${bone ?? '*'}/${metric ?? '*'}: ${JSON.stringify(result.issues.slice(0, 8))}`);
  assert.ok(issue.fromFrame >= 0 && issue.toFrame >= issue.fromFrame);
  assert.ok(typeof issue.limit === 'number' && Number.isFinite(issue.limit));
  return issue;
}
const covers = (issue: { fromFrame: number; toFrame: number }, frame: number) =>
  issue.fromFrame <= frame && frame <= issue.toFrame;
// 全身を海底から1m浮かせ、前（+Z）へ進める。
function floating(clip: GateInput, forward: (i: number) => number): GateInput {
  for (const bone of Object.values(clip.bones)) bone.position.forEach((p, i) => { p[1] += 1; p[2] += forward(i); });
  return clip;
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

test('両足の足裏の体表点がなければ、ほかを見ずに落とす', () => {
  const none = checkMotion({ ...motion(), skin: {} });
  assert.deepEqual(none.issues.map(x => [x.kind, x.bone, x.metric]),
    [['invalid_data', 'leftFoot', 'skin_required'], ['invalid_data', 'rightFoot', 'skin_required']]);
  const missing = checkMotion({ ...motion(), skin: undefined } as unknown as GateInput);
  assert.deepEqual(missing.issues.map(x => [x.kind, x.metric]), [['invalid_data', 'skin']]);
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

test('ゆっくりでも長い距離の足滑りを見逃さず、足裏の点がいくつ滑っても骨ごとに1つにまとめる', () => {
  const clip = motion();
  for (let i = 0; i < 61; i++) clip.bones.rightFoot.position[i][0] += .002 * i;
  assert.ok(.002 * clip.fps < GATE_LIMITS.slidingSpeedMps);
  const issue = expectIssue(clip, 'foot_slide', 'rightFoot', 'contact_drift');
  assert.ok(issue.value! >= GATE_LIMITS.slidingDriftM);
  assert.equal(check(clip).issues.filter(x => x.kind === 'foot_slide').length, 1);
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
  const issue = expectIssue(clip, 'penetration', 'leftHand', 'bone_below_ground');
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

test('膝は曲げ・下腿のひねり・軸外を分けて見る', () => {
  const twisted = motion();
  for (const name of ['leftLowerLeg', 'rightLowerLeg']) {
    twisted.bones[name].rotation = Array.from({ length: 61 }, () => around('y', 42));
  }
  expectPass('下腿のひねり42度', twisted);
  // 120度曲げた膝で下腿を40度ひねっても、すねの向きは曲げの面から出ない。
  twisted.bones.leftLowerLeg.rotation = Array.from({ length: 61 }, () => times(around('x', 120), around('y', 40)));
  expectPass('曲げた膝でひねる', twisted);
  const swung = motion();
  // 曲げてから大腿の軸まわりに振ると、すねが横を向く（膝ではできない）。
  swung.bones.leftLowerLeg.rotation[9] = times(around('y', 40), around('x', 90));
  assert.ok(expectIssue(swung, 'joint_limit', 'leftLowerLeg', 'hinge_off_axis').value! > 35);
  const side = motion();
  side.bones.rightLowerLeg.rotation[9] = around('z', 25);
  expectIssue(side, 'joint_limit', 'rightLowerLeg', 'hinge_off_axis');
  const over = motion();
  over.bones.rightLowerLeg.rotation[9] = around('y', 50);
  expectIssue(over, 'joint_limit', 'rightLowerLeg', 'hinge_twist');
});

test('肘の逆曲がり・首のねじれすぎを検出', () => {
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
});

test('単発の位置飛びは加速度の跳ねとして検出する', () => {
  const clip = motion();
  clip.bones.spine.position[25][0] += .14;
  const issue = expectIssue(clip, 'jitter', 'spine', 'position_flip');
  assert.ok(covers(issue, 25));
  assert.equal(issue.unit, 'm');
});

test('進みながらのガタつき（手の±8mm・体の2cmの跳ね・手首の2フレーム反転）も落とす', () => {
  const hand = floating(motion(), i => .5 * i / 30);
  hand.bones.leftHand.position.forEach((p, i) => { p[0] += i % 2 ? .008 : -.008; });
  expectIssue(hand, 'jitter', 'leftHand', 'position_flip');
  const pop = floating(motion(), i => 1.2 * i / 30);
  for (const name of ['spine', 'head']) pop.bones[name].position[30][1] += .02;
  for (const name of ['spine', 'head']) assert.ok(covers(expectIssue(pop, 'jitter', name, 'position_flip'), 30));
  const wrist = floating(motion(), i => 1.2 * i / 30);
  wrist.bones.leftHand.rotation[30] = around('x', 180);
  wrist.bones.leftHand.rotation[31] = around('x', 180);
  const issue = expectIssue(wrist, 'jitter', 'leftHand', 'rotation_flip');
  assert.ok(covers(issue, 30) && covers(issue, 31));
});

test('急に止まる・跳ね返る・3Hzで滑らかに往復する動きはガタつきにしない', () => {
  expectPass('急停止', floating(motion(), i => 2 * Math.min(i, 30) / 30));
  expectPass('跳ね返り', floating(motion(), i => 2 * (i <= 30 ? i : 60 - i) / 30));
  // 振幅11cm・3Hz（30fpsで最大加速度は約39m/s²）。サンプルとの位相をずらしても通る。
  for (let phase = 0; phase < 6; phase++) {
    const clip = motion();
    for (const name of ['leftHand', 'rightHand']) clip.bones[name].position.forEach((p, i) => {
      p[1] += .11 * Math.sin(2 * Math.PI * 3 * (i + phase / 6) / 30);
    });
    expectPass(`往復 位相${phase}/6`, clip);
  }
});

// Claudeが本物のYumeka（VRM1.0）から測った寸法を、回帰の境界条件にする。
const YUMEKA: Record<string, Vec3> = {
  hips: [0, .756, .027], spine: [0, .810, .025], chest: [0, .929, .022],
  neck: [0, 1.097, -.032], head: [0, 1.143, -.027],
  leftUpperLeg: [.059, .724, -.002], leftLowerLeg: [.065, .450, .007],
  leftFoot: [.074, .097, -.024], leftToes: [.071, .046, .065],
  leftUpperArm: [.093, 1.063, -.028], leftLowerArm: [.248, 1.057, -.036],
  leftHand: [.416, 1.058, -.033],
};
for (const [name, point] of Object.entries({ ...YUMEKA })) {
  if (name.startsWith('left')) YUMEKA[`right${name.slice(4)}`] = [-point[0], point[1], point[2]];
}
function yumekaClip() {
  const clip = motion();
  clip.rest = structuredClone(YUMEKA);
  clip.bones = Object.fromEntries(Object.entries(YUMEKA).map(([name, position]) => [name, {
    position: Array.from({ length: 61 }, () => [...position] as Vec3),
    rotation: Array.from({ length: 61 }, () => [...identity] as Quat),
  }]));
  return clip;
}
// ユメカの左足の体表点。かかとは丸く、土踏まずは浮き、拇趾球はつま先の付け根の下。後ろの4点は甲。
const FOOT_POINTS: Vec3[] = [
  [.074, .010, -.075], [.074, 0, -.055], [.054, .003, -.05], [.094, .003, -.05], [.074, .012, -.005],
  [.054, 0, .055], [.074, 0, .055], [.094, 0, .055], [.074, .07, 0], [.074, .05, .04], [.044, .09, -.03], [.104, .09, -.03],
];
const TOE_POINTS: Vec3[] = [[.071, 0, .085], [.051, 0, .08], [.071, .004, .115], [.071, .03, .09]];
const pitch = (v: Vec3, a: number): Vec3 =>
  [v[0], v[1] * Math.cos(a) - v[2] * Math.sin(a), v[1] * Math.sin(a) + v[2] * Math.cos(a)];
type FootPose = { ankle: Vec3; toes: Vec3; foot: Vec3[]; toePoints: Vec3[] };
// 左足を pivotRest まわりに phi 傾けて pivotNow へ置き、つま先はその付け根まわりに phi+psi 傾ける。
function footPose(phi: number, psi: number, pivotRest: Vec3, pivotNow: Vec3): FootPose {
  const place = (p: Vec3) => plus(pivotNow, pitch(minus(p, pivotRest), phi));
  const toes = place(YUMEKA.leftToes);
  return {
    ankle: place(YUMEKA.leftFoot), toes, foot: FOOT_POINTS.map(place),
    toePoints: TOE_POINTS.map(p => plus(toes, pitch(minus(p, YUMEKA.leftToes), phi + psi))),
  };
}
function moveLeftFoot(clip: GateInput, frames: FootPose[]): GateInput {
  frames.forEach((f, i) => { clip.bones.leftFoot.position[i] = f.ankle; clip.bones.leftToes.position[i] = f.toes; });
  clip.skin.leftFoot = { rest: FOOT_POINTS, position: frames.map(f => f.foot) };
  clip.skin.leftToes = { rest: TOE_POINTS, position: frames.map(f => f.toePoints) };
  return clip;
}
// 自然な1歩：立つ→つま先の付け根まわりにかかとを上げる（つま先は床のまま）→振り出す→かかとから着く→足裏を下ろす。
function stepFrames(step: number, heelOffFrames: number): FootPose[] {
  const deg = Math.PI / 180, T0 = YUMEKA.leftToes, heel = FOOT_POINTS[1];
  const frames: FootPose[] = [];
  for (let i = 0; i < 10; i++) frames.push(footPose(0, 0, T0, T0));
  for (let i = 1; i <= heelOffFrames; i++) {
    const phi = 30 * deg * i / heelOffFrames;
    frames.push(footPose(phi, -phi, T0, T0));
  }
  const start = frames.at(-1)!, heelEnd = plus(heel, [0, 0, step]);
  const end = footPose(-15 * deg, 0, heel, heelEnd);
  for (let i = 1; i <= 12; i++) {
    const s = i / 12, w = (1 - Math.cos(Math.PI * s)) / 2;
    const ankle = plus(start.ankle.map((x, k) => x + (end.ankle[k] - x) * w) as Vec3, [0, .04 * Math.sin(Math.PI * s), 0]);
    frames.push(footPose((30 - 45 * w) * deg, -30 * deg * Math.max(0, 1 - 3 * s), YUMEKA.leftFoot, ankle));
  }
  for (let i = 1; i <= 4; i++) frames.push(footPose(-15 * deg * (1 - i / 4), 0, heel, heelEnd));
  while (frames.length < 61) frames.push(frames.at(-1)!);
  return frames;
}

test('Yumeka実寸：全身が3cm沈むとつま先の有無によらず落とす', () => {
  for (const toes of [true, false]) {
    const clip = yumekaClip();
    if (!toes) for (const name of ['leftToes', 'rightToes']) {
      delete clip.bones[name];
      delete clip.rest[name];
    }
    for (const b of Object.values(clip.bones)) for (const p of b.position) p[1] -= .03;
    const issue = expectIssue(clip, 'penetration', 'leftFoot', 'skin_below_ground');
    assert.ok(issue.value! > .025);
  }
});

test('Yumeka実寸：±8mmの毎フレーム振動と2cmの単発の跳ねは落とす', () => {
  const clip = yumekaClip();
  for (const name of ['head', 'leftHand', 'spine']) for (let i = 0; i < 61; i++)
    clip.bones[name].position[i][0] += i % 2 ? .008 : -.008;
  for (const name of ['head', 'leftHand', 'spine'])
    expectIssue(clip, 'jitter', name, 'position_flip');
  const pop = yumekaClip();
  pop.bones.spine.position[30][0] += .02;
  assert.ok(covers(expectIssue(pop, 'jitter', 'spine', 'position_flip'), 30));
});

test('Yumeka実寸：手首が単フレーム180度反転すれば、指がなくても落とす', () => {
  const clip = yumekaClip();
  clip.bones.leftHand.rotation[30] = around('x', 180);
  assert.ok(covers(expectIssue(clip, 'jitter', 'leftHand', 'rotation_flip'), 30));
});

test('Yumeka実寸：自然な1歩でかかと/つま先を転がしても、足滑りともめり込みとも判定しない', () => {
  for (const [step, heelOff] of [[.3, 6], [.6, 6], [.3, 3]]) {
    // 体幹・大腿を固定した簡易アニメなので、脚の長さは見ず、接地だけを見る。
    const result = check(moveLeftFoot(yumekaClip(), stepFrames(step, heelOff)));
    const contact = result.issues.filter(x => x.kind === 'foot_slide' || x.kind === 'penetration');
    assert.deepEqual(contact, [], `${step}m・かかと上げ${heelOff}フレーム`);
  }
});

test('Yumeka実寸：接地した足のずれ・ゆっくりの滑り・2フレームの滑りを落とす', () => {
  const T0 = YUMEKA.leftToes;
  for (const [metric, shift] of [
    ['contact_drift', (i: number) => Math.min(1, Math.max(0, (i - 20) / 10)) * .05],
    ['contact_drift', (i: number) => i / 60 * .06],
    ['contact_speed', (i: number) => i >= 30 ? .03 : i === 29 ? .015 : 0],
  ] as const) {
    const clip = moveLeftFoot(yumekaClip(), Array.from({ length: 61 }, (_, i) => footPose(0, 0, T0, plus(T0, [0, 0, shift(i)]))));
    const slides = check(clip).issues.filter(x => x.kind === 'foot_slide' && x.bone === 'leftFoot' && x.metric === metric);
    assert.equal(slides.length, 1, JSON.stringify(check(clip).issues.slice(0, 8)));
  }
});

test('足裏の体表サンプル：支持中の滑りを検出し、持ち上がった足は許す', () => {
  const clip = yumekaClip();
  clip.skin = {
    leftFoot: {
      rest: [[.07, 0, -.09], [.08, 0, .03]],
      position: Array.from({ length: 61 }, (_, i) => [
        [.07 + .003 * i, 0, -.09], [.08 + .003 * i, 0, .03],
      ] as Vec3[]),
    },
  };
  const issue = expectIssue(clip, 'foot_slide', 'leftFoot', 'contact_drift');
  assert.ok(issue.value! > .04);
  for (const row of clip.skin.leftFoot.position) for (const sample of row) sample[1] += .12;
  assert.equal(check(clip).issues.some(x => x.kind === 'foot_slide'), false);
});

test('足裏の体表サンプル：3cmのめり込みを捉え、砂に寝るときの床接触は通す', () => {
  const clip = yumekaClip();
  clip.skin = { leftFoot: {
    rest: [[.074, 0, 0]],
    position: Array.from({ length: 61 }, () => [[.074, -.03, 0]] as Vec3[]),
  } };
  expectIssue(clip, 'penetration', 'leftFoot', 'skin_below_ground');
  for (const row of clip.skin.leftFoot.position) row[0][1] = .002;
  expectPass('地面に置いた足裏', clip);
});

test('体表サンプルのNaNやフレーム・点数の不一致を拒否する', () => {
  const clip = yumekaClip();
  clip.skin = { leftFoot: { rest: [[0, 0, 0]], position: Array.from({ length: 61 }, () => [[0, 0, 0]] as Vec3[]) } };
  clip.skin.leftFoot.position[5][0][1] = Infinity;
  expectIssue(clip, 'invalid_data', 'leftFoot', 'skin_point');
  clip.skin.leftFoot.position[5] = [];
  expectIssue(clip, 'invalid_data', 'leftFoot', 'skin_points');
  clip.skin.leftFoot.position.pop();
  expectIssue(clip, 'invalid_data', 'leftFoot', 'skin_shape');
});

test('Yumeka実寸：正座158度・前腕回外80度は許し、首と頭の合算160度は拒否', () => {
  const clip = yumekaClip();
  for (const name of ['leftLowerLeg', 'rightLowerLeg'])
    for (let i = 0; i < 61; i++) clip.bones[name].rotation[i] = around('x', 158);
  clip.bones.leftLowerArm.rotation = Array.from({ length: 61 }, () => around('x', -80));
  clip.bones.rightLowerArm.rotation = Array.from({ length: 61 }, () => around('x', 80));
  expectPass('正座・前腕回外', clip);
  for (const name of ['neck', 'head']) clip.bones[name].rotation =
    Array.from({ length: 61 }, () => around('y', 80));
  expectIssue(clip, 'joint_limit', 'head', 'combined_neck_twist');
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
  const clip = moveLeftFoot(yumekaClip(), stepFrames(.3, 6));
  const input = { ...clip, skin: { ...soles(clip), ...clip.skin } };
  const before = structuredClone(input);
  assert.deepEqual(checkMotion(input), checkMotion(input));
  assert.deepEqual(input, before);
});
