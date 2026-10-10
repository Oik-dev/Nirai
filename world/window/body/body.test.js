import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import * as THREE from 'three';
import { Body } from './body.js';
import { PLACES } from './place.js';
import { BLINK, BLINK_SECONDS, Blinker, blinkShape } from './blink.js';
import { GAZE_LIMITS, gazeAngles } from './gaze.js';
import { smoothNoise } from './noise.js';
import { createSeededRandom } from '../sea/sea-random.js';
import { GESTURES, GESTURE_NAMES, gestureAnimation } from './gestures.js';
import { GESTURE_NAMES as CATALOG_GESTURES, OWNED_EXPRESSIONS, WORLD_GESTURES } from './catalog.js';

const PARENTS = {
  hips: null, spine: 'hips', chest: 'spine', neck: 'chest', head: 'neck',
  leftUpperArm: 'chest', leftLowerArm: 'leftUpperArm', leftHand: 'leftLowerArm',
  rightUpperArm: 'chest', rightLowerArm: 'rightUpperArm', rightHand: 'rightLowerArm',
  leftUpperLeg: 'hips', leftLowerLeg: 'leftUpperLeg', rightUpperLeg: 'hips', rightLowerLeg: 'rightUpperLeg',
};

// 正規化された骨だけを持つ、描かない体。VRMを読まずに、姿勢の組み方を確かめる。
function fakeVrm(metaVersion, narrowedHappy = true) {
  const scene = new THREE.Group();
  const nodes = {};
  for (const [name, parent] of Object.entries(PARENTS)) {
    const node = new THREE.Object3D();
    node.name = `Normalized_${name}`;
    node.position.y = .1;
    (parent ? nodes[parent] : scene).add(node);
    nodes[name] = node;
  }
  const values = new Map();
  return {
    scene,
    meta: { metaVersion },
    humanoid: {
      normalizedHumanBones: Object.fromEntries(Object.entries(nodes).map(([name, node]) => [name, { node }])),
      normalizedRestPose: { hips: { position: [0, .85, 0] } },
      getNormalizedBoneNode: name => nodes[name] ?? null,
      getRawBoneNode: name => nodes[name] ?? null,
      update() {},
    },
    expressionManager: {
      expressions: ['happy', 'blink'].map(expressionName => ({
        expressionName,
        binds: expressionName === 'happy' && narrowedHappy
          ? [{ primitives: [{ morphTargetDictionary: { eye_joy: 1 } }], index: 1 }]
          : [],
      })),
      presetExpressionMap: { blink: { binds: [{}] } },
      setValue: (name, value) => values.set(name, value),
      values,
    },
    lookAt: null,
    update() {},
  };
}

function fakeBody(metaVersion = '1', narrowedHappy = true) {
  const vrm = fakeVrm(metaVersion, narrowedHappy);
  const root = new THREE.Group();
  root.add(vrm.scene);
  return new Body(vrm, root);
}

const camera = new THREE.PerspectiveCamera();
camera.position.set(0, 1.4, 3);
const run = (bodies, seconds, step = 1 / 30) => {
  for (let frame = Math.round(seconds / step); frame > 0; frame--) for (const body of bodies) body.update(step, camera, false);
};
const pose = body => Object.fromEntries(Object.keys(PARENTS)
  .map(name => [name, body.vrm.humanoid.getNormalizedBoneNode(name).quaternion.clone()]));
const degreesBetween = (a, b) => {
  const turn = a.clone().invert().multiply(b);
  return THREE.MathUtils.radToDeg(2 * Math.atan2(Math.hypot(turn.x, turn.y, turn.z), Math.abs(turn.w)));
};

test('体は毎フレーム姿勢を組み直し、動きを止めればその場で止まって目を開ける', () => {
  const body = fakeBody();
  run([body], 5);
  const before = pose(body);
  const position = body.vrm.scene.position.clone();
  body.blink.start = body.blink.clock - BLINK.close; // 瞬きの途中で止める
  // 前のフレームの回転に積み増していれば、揺らぎが重なって姿勢が動く。
  for (let i = 0; i < 10; i++) body.update(0, camera, false);
  for (const [name, quaternion] of Object.entries(pose(body))) assert.ok(degreesBetween(quaternion, before[name]) < 1e-4, name);
  assert.ok(body.vrm.scene.position.equals(position));
  assert.equal(body.vrm.expressionManager.values.get('blink'), 0);
});

test('身振りは値が止まっている間も骨に届き、終われば基準姿勢と揺らぎだけに戻る', () => {
  const waving = fakeBody();
  const still = fakeBody();
  run([waving, still], 1);
  waving.play('小さく手を振る');
  // 肘は 0.5〜2 秒のあいだ同じ角度のまま。AnimationMixer は同じ値を書き直さないので、本物の骨へ直接書かせると基準姿勢のままになる。
  run([waving, still], 1);
  assert.ok(degreesBetween(waving.vrm.humanoid.getNormalizedBoneNode('rightLowerArm').quaternion,
    still.vrm.humanoid.getNormalizedBoneNode('rightLowerArm').quaternion) > 60);
  run([waving, still], 2.5);
  assert.equal(waving.action, null);
  const after = pose(still);
  for (const [name, quaternion] of Object.entries(pose(waving))) assert.ok(degreesBetween(quaternion, after[name]) < 1e-3, name);
});

test('VRM 0.x の体には、同じ姿勢と動きを前後の向きを直して掛ける', () => {
  const vrm1 = fakeBody('1');
  const vrm0 = fakeBody('0');
  run([vrm1, vrm0], .5);
  for (const body of [vrm1, vrm0]) body.play('おじぎ');
  run([vrm1, vrm0], 1);
  const pose0 = pose(vrm0);
  for (const [name, q] of Object.entries(pose(vrm1))) {
    assert.ok(degreesBetween(pose0[name], new THREE.Quaternion(-q.x, q.y, -q.z, q.w)) < 1e-3, name);
  }
});

test('視線は首と頭の届く範囲を越えず、肩より後ろへは振り向かない', () => {
  const right = gazeAngles(new THREE.Vector3(10, 0, 1), true);
  assert.equal(right.yaw, GAZE_LIMITS.yaw);
  const above = gazeAngles(new THREE.Vector3(0, 10, 1), true);
  assert.equal(above.pitch, GAZE_LIMITS.pitch);
  const behind = gazeAngles(new THREE.Vector3(.1, 0, -1), true);
  assert.equal(behind.yaw, 0);
  assert.equal(behind.pitch, 0);
  assert.deepEqual(gazeAngles(new THREE.Vector3(1, 0, 1), false), { yaw: 0, pitch: 0 });
});

test('瞬きは決まった周期でなく、間は人の瞬きの範囲に収まる', () => {
  assert.equal(blinkShape(0), 0);
  assert.equal(blinkShape(BLINK.close), 1);
  assert.equal(blinkShape(BLINK_SECONDS), 0);
  const blinker = new Blinker(createSeededRandom(7));
  const starts = [blinker.start];
  for (let frame = 0; frame < 600 * 60; frame++) {
    const closed = blinker.update(1 / 60);
    assert.ok(closed >= 0 && closed <= 1);
    if (blinker.start !== starts.at(-1)) starts.push(blinker.start);
  }
  const gaps = starts.slice(1).map((start, i) => start - starts[i] - BLINK_SECONDS);
  assert.ok(gaps.every(gap => gap >= BLINK.gap - 1e-9 && gap <= BLINK.max + 1e-9));
  assert.ok(gaps.some(gap => Math.abs(gap - BLINK.gap) < 1e-9), '2回続く瞬きがある');
  const mean = gaps.reduce((sum, gap) => sum + gap, 0) / gaps.length;
  const spread = Math.sqrt(gaps.reduce((sum, gap) => sum + (gap - mean) ** 2, 0) / gaps.length);
  assert.ok(spread > .5, `間の揺れ ${spread}`);
});

test('揺らぎの雑音はなめらかで、範囲を越えず、同じ種なら同じ揺れになる', () => {
  const noise = smoothNoise(3);
  const again = smoothNoise(3);
  const other = smoothNoise(4);
  let previous = noise(0);
  let differs = false;
  for (let t = .01; t < 300; t += .01) {
    const value = noise(t);
    assert.ok(value >= -1 && value <= 1);
    assert.ok(Math.abs(value - previous) < .05);
    assert.equal(again(t), value);
    differs ||= other(t) !== value;
    previous = value;
  }
  assert.ok(differs);
});

test('瞬きと視線の表情は、本人の選ぶ表情に入らない', () => {
  const body = fakeBody();
  assert.deepEqual(body.expressions, ['happy']);
  body.setExpression('blink');
  assert.equal(body.expression, null);
  body.setExpression('happy');
  run([body], 1);
  assert.ok(body.vrm.expressionManager.values.get('happy') > .99);
});

test('細目の笑顔では自動瞬きを抑え、表情を戻せば瞬きも戻り、睡眠の閉眼は守る', () => {
  const body = fakeBody();
  const closeEyes = () => {
    body.blink.start = body.blink.clock - BLINK.close;
    body.update(1 / 60, camera, false);
    return body.vrm.expressionManager.values.get('blink');
  };
  assert.ok(closeEyes() > .99, '通常の瞬き');
  body.setExpression('happy');
  run([body], 1.2);
  assert.ok(body.vrm.expressionManager.values.get('happy') > .99);
  assert.ok(closeEyes() < .01, '笑顔の細目に全閉眼を重ねない');
  body.setExpression(null);
  run([body], 1.2);
  assert.ok(closeEyes() > .99, '笑顔をやめたら瞬きが戻る');
  const openEyeSmile = fakeBody('1', false);
  openEyeSmile.setExpression('happy');
  run([openEyeSmile], 1.2);
  openEyeSmile.blink.start = openEyeSmile.blink.clock - BLINK.close;
  openEyeSmile.update(1 / 60, camera, false);
  assert.ok(openEyeSmile.vrm.expressionManager.values.get('blink') > .99,
    '目を細めない笑顔では通常の瞬きを保つ');
  body.setExpression('happy');
  body.setLife({ activity: { name: '居場所でくつろぐ' }, asleep: true });
  run([body], .5);
  assert.equal(body.vrm.expressionManager.values.get('blink'), 1, '眠っている間は必ず閉眼');
});

test('本人が選んだ表情は10秒後に自然な顔に戻り、古い記録で窓を開いても張り付かない', () => {
  const body = fakeBody();
  const start = Date.parse('2026-10-09T03:00:00.000Z');
  let now = start;
  body.now = () => now;
  body.setExpression('happy', new Date(start).toISOString());
  run([body], 1);
  assert.ok(body.vrm.expressionManager.values.get('happy') > .99);
  now += 9999;
  body.update(1 / 60, camera, false);
  assert.equal(body.expression, 'happy', '10秒より前は維持');
  now += 2;
  run([body], .8);
  assert.equal(body.expression, null);
  assert.ok(body.vrm.expressionManager.values.get('happy') < .01, '通常の顔にフェードする');
  body.setExpression('happy', new Date(start).toISOString());
  run([body], .5);
  assert.equal(body.expression, null, '古い表情の再取得で復活しない');
  body.setExpression('happy', new Date(now).toISOString());
  run([body], .5);
  assert.ok(body.vrm.expressionManager.values.get('happy') > .9, '新しい選択は再び反映');
});

test('笑顔以外の細目表情でも、まぶたのMorphに基づいて瞬きを抑える', () => {
  const vrm = fakeVrm('1', false);
  vrm.expressionManager.expressions.push({
    expressionName: 'relaxed',
    binds: [{ primitives: [{ morphTargetDictionary: { eye_nagomi_2: 7 } }], index: 7 }],
  });
  const root = new THREE.Group();
  root.add(vrm.scene);
  const body = new Body(vrm, root);
  body.setExpression('relaxed');
  run([body], 1);
  body.blink.start = body.blink.clock - BLINK.close;
  body.update(1 / 60, camera, false);
  assert.ok(vrm.expressionManager.values.get('blink') < .01);
  body.setExpression(null);
  run([body], 1);
  body.blink.start = body.blink.clock - BLINK.close;
  body.update(1 / 60, camera, false);
  assert.ok(vrm.expressionManager.values.get('blink') > .99);
});

test('組み込みの身振りの名前と動きは、海も使うカタログの正本と一致する', () => {
  assert.equal(GESTURE_NAMES, CATALOG_GESTURES);
  assert.deepEqual(CATALOG_GESTURES, [...Object.keys(GESTURES), ...Object.keys(WORLD_GESTURES)]);
  for (const name of Object.keys(GESTURES)) assert.ok(gestureAnimation(name), name);
  for (const name of Object.keys(WORLD_GESTURES)) assert.equal(gestureAnimation(name), null);
  assert.equal(gestureAnimation('知らない動き'), null);
});

test('世界の身振りと覚えた動きは定めた置き場だけから読み、404でフォールバックしない', async t => {
  const paths = [];
  t.mock.method(globalThis, 'fetch', async url => {
    paths.push(url);
    return { ok: false };
  });
  const body = fakeBody();
  await assert.rejects(body.play('伸び'), /動き「伸び」がありません/);
  await assert.rejects(body.play('覚えた動き'), /動き「覚えた動き」がありません/);
  await body.play('うなずく');
  assert.deepEqual(paths, [
    '/assets/motions/%E4%BC%B8%E3%81%B3.vrma',
    '/motions/%E8%A6%9A%E3%81%88%E3%81%9F%E5%8B%95%E3%81%8D.vrma',
  ]);
});

test('発声・瞬き・視線の名前を表情の選択肢へ入れない', () => {
  const body = fakeBody();
  body.vrm.expressionManager.expressions = ['happy', ...OWNED_EXPRESSIONS].map(expressionName => ({ expressionName }));
  assert.deepEqual(body.expressions, ['happy']);
  for (const name of OWNED_EXPRESSIONS) {
    body.setExpression(name);
    assert.equal(body.expression, null, name);
  }
});

test('遅い動きの読み込みは、次に選んだ身振りや破棄済みの体を上書きしない', async t => {
  let finish;
  const wait = new Promise(resolve => { finish = resolve; });
  t.mock.method(globalThis, 'fetch', () => wait);
  const playing = fakeBody();
  const disposed = fakeBody();
  const first = playing.play('覚えた動き');
  const old = disposed.play('覚えた動き');
  await playing.play('うなずく');
  disposed.dispose();
  finish({ ok: true, arrayBuffer: async () => {
    // .vrma の最小の、骨の動きなしのファイル。
    const json = Buffer.from(JSON.stringify({ asset: { version: '2.0' },
      extensionsUsed: ['VRMC_vrm_animation'],
      nodes: [{ translation: [0, 1, 0] }], scenes: [{ nodes: [0] }], scene: 0,
      animations: [{ channels: [], samplers: [] }],
      extensions: { VRMC_vrm_animation: { specVersion: '1.0', humanoid: { humanBones: { hips: { node: 0 } } } } } }));
    const size = Math.ceil(json.length / 4) * 4;
    const bytes = Buffer.alloc(20 + size, 0x20);
    bytes.writeUInt32LE(0x46546c67, 0);
    bytes.writeUInt32LE(2, 4);
    bytes.writeUInt32LE(bytes.length, 8);
    bytes.writeUInt32LE(size, 12);
    bytes.writeUInt32LE(0x4e4f534a, 16);
    json.copy(bytes, 20);
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  } });
  await Promise.all([first, old]);
  assert.equal(playing.action.getClip().name, 'うなずく');
  assert.equal(disposed.action, null);
  assert.equal(disposed.clips.size, 0);
});

function bodyAt(clock) {
  const vrm = fakeVrm('1');
  const root = new THREE.Group();
  root.add(vrm.scene);
  const body = new Body(vrm, root, () => clock.now);
  for (const key of ['swim', 'float', 'sitEntry', 'sit', 'recline', 'sleep']) {
    const clip = new THREE.AnimationClip(key, 2, []);
    const action = body.mixer.clipAction(clip);
    action.setLoop(key === 'sitEntry' || key === 'recline' ? THREE.LoopOnce : THREE.LoopRepeat)
      .setEffectiveWeight(0).play();
    if (key === 'sitEntry' || key === 'recline') action.setEffectiveTimeScale(0);
    body.baseActions.set(key, action);
    body.baseWeights.set(key, 0);
  }
  return body;
}

test('眠ると砂地へ泳いで座り、寝転び、目覚めたら選んだ活動へ戻る', () => {
  const clock = { now: Date.parse('2026-10-08T03:00:00.000Z') };
  const body = bodyAt(clock);
  const life = { activity: { name: '水面の近くで漂う', since: null, from: null }, expression: null, gesture: null, asleep: false };
  const surface = new THREE.Vector3(PLACES['水面の近く'].x, PLACES['水面の近く'].y, PLACES['水面の近く'].z);
  const up = () => new THREE.Vector3(0, 1, 0).applyQuaternion(body.root.quaternion);
  body.setLife(life);
  run([body], .1);
  assert.ok(body.root.position.distanceTo(new THREE.Vector3(surface.x, surface.y - .85, surface.z)) < 1e-6);
  assert.ok(up().y > .999);
  assert.equal(body.baseWeights.get('float'), 1);
  body.setLife({ ...life, asleep: true });
  clock.now += 2500;
  run([body], .1);
  assert.ok(body.root.position.distanceTo(new THREE.Vector3(surface.x, surface.y - .85, surface.z)) > .01,
    '水面から砂地へ移動を始める');
  assert.equal(body.baseWeights.get('sleep'), 0, '砂地へ着くまでは寝姿にならない');
  clock.now += 60_000;
  run([body], .1);
  const sand = PLACES['砂地'];
  assert.ok(body.root.position.distanceTo(new THREE.Vector3(sand.x, sand.y - .85, sand.z)) < 1e-6);
  assert.ok(up().y > .999, '横向きは寝姿の動きが持つ');
  assert.ok(body.baseWeights.get('sleep') > 0);
  assert.equal(body.vrm.expressionManager.values.get('blink'), 1);
  body.setLife(life);
  clock.now += 1000;
  run([body], .1);
  assert.ok(body.baseWeights.get('recline') > 0, '寝姿へ短いフェードを始める');
  run([body], .3);
  assert.equal(body.baseWeights.get('recline'), 1, 'フェード後は逆再生だけが残る');
  assert.equal(body.baseActions.get('recline').time, 1);
  clock.now += 1000;
  run([body], .1);
  assert.ok(body.baseWeights.get('recline') > 0, '切替直後は飛ばさず混ぜる');
  run([body], .3);
  assert.ok(body.baseWeights.get('recline') < .3, 'フェードで寝姿が薄れる');
  run([body], .9);
  assert.ok(body.baseWeights.get('recline') < .02, '起床後の寝姿はほぼ消える');
  clock.now += 100;
  run([body], .1);
  assert.ok(body.baseWeights.get('swim') > .2, '起き上がってから移動を始める');
  clock.now += 60_000;
  run([body], .1);
  assert.ok(body.root.position.distanceTo(new THREE.Vector3(surface.x, surface.y - .85, surface.z)) < 1e-6);
  // 窓を開いたときに既に眠っているなら、砂地で眠る状態から始める。
  const opened = bodyAt(clock);
  opened.setLife({ ...life, asleep: true });
  run([opened], .1);
  assert.ok(opened.baseWeights.get('sleep') > .99);
  assert.ok(opened.root.position.distanceTo(new THREE.Vector3(sand.x, sand.y - .85, sand.z)) < 1e-6);
  const expectedYaw = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0),
    Math.atan2(-sand.x, 3.65 - sand.z));
  assert.ok(degreesBetween(opened.root.quaternion, expectedYaw) < 1e-3,
    '眠りの向きは砂地の座る向きとつなげる');
});

test('採用された寝転びの再生位置は時刻で決まり、姿勢の切替は短く混ぜる', () => {
  const clock = { now: 1_000_000 };
  const body = bodyAt(clock);
  const life = { activity: { name: '砂地で休む' }, asleep: false };
  body.setLife(life);
  body.update(.1, camera, false);
  body.setLife({ ...life, asleep: true });
  clock.now += 1000;
  body.update(.1, camera, false);
  assert.ok(body.baseWeights.get('recline') > 0 && body.baseWeights.get('recline') < 1);
  body.update(.3, camera, false);
  assert.equal(body.baseWeights.get('recline'), 1);
  assert.equal(body.baseWeights.get('sleep'), 0);
  assert.equal(body.baseActions.get('recline').time, 1);
  clock.now += 1000;
  body.update(.1, camera, false);
  assert.ok(body.baseWeights.get('sleep') > 0 && body.baseWeights.get('sleep') < 1);
  body.update(.3, camera, false);
  assert.equal(body.baseWeights.get('sleep'), 1);
  body.setLife(life);
  clock.now += 500;
  body.update(.1, camera, false);
  assert.ok(body.baseWeights.get('recline') > 0 && body.baseWeights.get('recline') < 1);
  body.update(.3, camera, false);
  assert.equal(body.baseWeights.get('recline'), 1);
  assert.ok(Math.abs(body.baseActions.get('recline').time - 1.5) < 1e-8);
  assert.equal(body.baseWeights.get('sleep'), 0);
});

test('配布する合格済みの動きだけで活動を初期化する', async t => {
  const requested = [];
  t.mock.method(globalThis, 'fetch', async url => {
    const name = decodeURIComponent(new URL(url, 'http://127.0.0.1').pathname
      .replace('/assets/motions/', ''));
    requested.push(name);
    try {
      const bytes = await readFile(new URL('../assets/motions/' + name, import.meta.url));
      return new Response(bytes);
    } catch {
      return new Response(null, { status: 404 });
    }
  });
  const body = fakeBody();
  await body.loadActivities();
  assert.deepEqual(requested.sort(), ['泳ぐ.vrma', '浮く.vrma', '腰を下ろす.vrma',
    '座る.vrma', '寝転ぶ.vrma', '眠る.vrma'].sort());
  assert.equal(body.baseActions.size, 6);
  assert.ok(body.baseActions.has('recline'));
  body.dispose();
});

test('泳ぐ向きが正反対を横切っても寝入りと目覚めで身体が急回転しない', () => {
  const clock = { now: 100_700 };
  const body = bodyAt(clock);
  const life = { activity: { name: '海の中を泳ぐ', since: '1970-01-01T00:00:00.000Z', from: null }, asleep: false };
  body.setLife(life);
  body.update(1 / 30, camera, false);

  const sample = () => {
    let previous = body.root.quaternion.clone();
    let largest = 0;
    for (let frame = 0; frame < 180; frame++) {
      clock.now += 1000 / 30;
      body.update(1 / 30, camera, false);
      largest = Math.max(largest, degreesBetween(previous, body.root.quaternion));
      previous.copy(body.root.quaternion);
    }
    return largest;
  };

  body.setLife({ ...life, asleep: true });
  const fallingAsleep = sample();
  assert.ok(fallingAsleep < 5, `泳ぎから寝入る途中の回転: ${fallingAsleep.toFixed(2)}°`);
  body.setLife(life);
  const wakingUp = sample();
  assert.ok(wakingUp < 5, `泳ぎへ戻る途中の回転: ${wakingUp.toFixed(2)}°`);
});

test('砂地では座る土台を選び、眠れば座る重みをほどく', () => {
  const clock = { now: Date.parse('2026-10-08T03:00:00.000Z') };
  const body = bodyAt(clock);
  const sand = PLACES['砂地'];
  const rest = name => ({ activity: { name, since: null, from: null }, expression: null, gesture: null, asleep: false });
  body.setLife(rest('居場所でくつろぐ'));
  run([body], .1);
  body.setLife(rest('砂地で休む'));
  run([body], 1);
  assert.ok(body.root.position.distanceTo(new THREE.Vector3(sand.x, sand.y - .85, sand.z)) < 1e-6);
  assert.ok(body.baseWeights.get('sit') > .9);
  body.setLife({ ...rest('砂地で休む'), asleep: true });
  clock.now += 6000;
  run([body], 1);
  assert.ok(body.baseWeights.get('sit') < .1);
  assert.ok(body.baseWeights.get('sleep') > .9);
});

test('大きな身振りは立位で全重み、泳ぎと座位では活動と同時に重なる', () => {
  const clock = { now: Date.parse('2026-10-08T03:00:00.000Z') };
  for (const [activity, base] of [
    ['居場所でくつろぐ', null],
    ['海の中を泳ぐ', 'swim'],
    ['砂地で休む', 'sit'],
  ]) {
    const body = bodyAt(clock);
    body.setLife({ activity: { name: activity, since: null, from: null }, asleep: false });
    body.start(new THREE.AnimationClip('伸び', 3, []));
    run([body], .6);
    assert.ok(Math.abs(body.action.getEffectiveWeight() - 1) < 1e-6, activity);
    for (const [key, weight] of body.baseWeights) {
      assert.ok(Math.abs(weight - (key === base ? 1 : 0)) < 1e-6,
        `${activity}: ${key} weight=${weight}`);
    }
  }
});

test('身振りは始まった時刻から再生し、窓を開いたときに終わっていれば始めない', async () => {
  const clock = { now: 1_000_000 };
  const body = bodyAt(clock);
  await body.play('うなずく', clock.now - 60_000);
  assert.equal(body.action, null);
  await body.play('小さく手を振る', clock.now - 1000);
  assert.ok(Math.abs(body.action.time - 1) < 1e-6);
});
