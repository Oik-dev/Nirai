import * as THREE from 'three';
import { MotionPose, modelRotation } from './pose.js';
import { IdleMotion } from './idle.js';
import { Blinker } from './blink.js';
import { NaturalGaze } from './gaze.js';
import { GESTURE_NAMES, gestureAnimation } from './gestures.js';
import { motionClip, parseMotion } from './motions.js';
import { OWNED_EXPRESSIONS } from './catalog.js';
import { HOME_ACTIVITY } from './activities.js';
import { PLACES, placeAt } from './place.js';
import { WATER_OPTICS } from '../sea/optics.js';
export { OWNED_EXPRESSIONS } from './catalog.js';

const FADE_SECONDS = .3;
const EXPRESSION_SECONDS = .12; // 表情が 1/e まで移る秒数
const LISTEN_SECONDS = 8; // Masterが話しかけてから、こちらを向いている長さ
const SECONDS_PER_CHARACTER = .07; // 本人が話している長さの目安（声はないので、文字の長さから）
const LONGEST_SPEECH = 8;
const HIP = .85; // 体の中心（腰）の、足もとからの高さ（体は1.55 m）
const HOME = Object.freeze({ name: HOME_ACTIVITY, since: null, from: null });
// 寝姿：居場所の砂の上で、こちらを向いて右を下に横になり、少し丸くなる。眠る・起きるは数秒かけて移る。
const SLEEP = Object.freeze({ seconds: 5, height: .16 });
const BED = Object.freeze({
  center: new THREE.Vector3(PLACES['居場所'].x, WATER_OPTICS.floorY + SLEEP.height, PLACES['居場所'].z),
  turn: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, Math.PI / 2)),
});
// 丸くなる・座る姿勢。動きと揺らぎを組んだ骨の回転に、重み（0〜1）のぶんだけ掛ける。角度は度（x, y, z）。
const POSTURES = Object.freeze({
  // 眠る：背中と首を少し丸め、膝を寄せ、腕を体に沿わせて前へ。
  sleep: Object.freeze({
    spine: [10, 0, 0], chest: [8, 0, 0], neck: [12, 0, 0],
    leftUpperLeg: [-38, 0, 0], rightUpperLeg: [-30, 0, 0], leftLowerLeg: [55, 0, 0], rightLowerLeg: [48, 0, 0],
    leftUpperArm: [0, -25, -20], rightUpperArm: [0, 25, 20], leftLowerArm: [0, -60, 0], rightLowerArm: [0, 60, 0],
  }),
  // 砂に腰を下ろす：膝を立てて座る（腰の高さは place.js が下げる）。
  sit: Object.freeze({
    spine: [6, 0, 0],
    leftUpperLeg: [-110, 0, 0], rightUpperLeg: [-110, 0, 0], leftLowerLeg: [70, 0, 0], rightLowerLeg: [70, 0, 0],
  }),
});
const smoothstep = value => value * value * (3 - 2 * value);
function applyBlink(manager, value) {
  const presets = manager.presetExpressionMap ?? {};
  if (presets.blink?.binds.length) manager.setValue('blink', value);
  else if (presets.blinkLeft?.binds.length && presets.blinkRight?.binds.length) {
    manager.setValue('blinkLeft', value);
    manager.setValue('blinkRight', value);
  }
}

// 1体の体。毎フレーム決まった順で姿勢を組む：動きの姿勢（基準姿勢と、身振り・覚えた動き。AnimationMixer）→ 待機の揺らぎ → 視線
// → 表情と瞬き → vrm.update。動きは基準姿勢と混ぜて重ね、揺らぎはその上から掛ける。
// delta が 0（動きを止めている・動きを抑える設定）なら、その場で止まり、目は開ける。
// 居場所（root の位置と向き）は、海が記録から計算した暮らしと今の時刻から、毎フレーム決め直す（place.js）。
export class Body {
  constructor(vrm, root, now = Date.now) {
    this.vrm = vrm;
    this.root = root;
    this.now = now;
    this.life = null;
    this.sleep = null;
    this.center = new THREE.Vector3(); // 体の中心（腰）。カメラと影が追う
    this.look = new THREE.Vector3();
    this.turn = new THREE.Quaternion();
    this.postures = Object.fromEntries(Object.entries(POSTURES).map(([posture, bones]) => [posture,
      Object.entries(bones).map(([name, angles]) => ({
        node: vrm.humanoid.getNormalizedBoneNode(name), rotation: modelRotation(angles, vrm.meta?.metaVersion),
      })).filter(bone => bone.node)]));
    this.bend = new THREE.Quaternion();
    this.pose = new MotionPose(vrm);
    this.mixer = new THREE.AnimationMixer(this.pose.root);
    // 終わった動きは、mixer の更新が済んでから止める（更新の途中で止めると、その回の重ね合わせが崩れる）。
    this.finished = [];
    this.mixer.addEventListener('finished', event => this.finished.push(event.action));
    this.idle = new IdleMotion(vrm);
    this.gaze = new NaturalGaze(vrm, root);
    this.blink = new Blinker();
    this.clips = new Map();
    this.playSequence = 0;
    this.disposed = false;
    this.action = null;
    this.ending = false;
    this.expression = null;
    this.weights = new Map();
    this.clock = 0;
    this.speakingUntil = 0;
    this.attentionUntil = 0;
  }

  get gestures() { return GESTURE_NAMES; }

  get expressions() {
    return (this.vrm.expressionManager?.expressions ?? [])
      .map(expression => expression.expressionName)
      .filter(name => !OWNED_EXPRESSIONS.includes(name));
  }

  // 組み込みの身振りか、覚えた動き（イデアの body/motions/<名前>.vrma）を、始まった時刻から再生する。終わっていれば始めない。
  async play(name, since = this.now()) {
    if (this.disposed) return;
    const sequence = ++this.playSequence;
    let clip = this.clips.get(name);
    if (!clip) {
      let animation = gestureAnimation(name);
      if (!animation) {
        const response = await fetch(`/motions/${encodeURIComponent(name)}.vrma`, { cache: 'no-store' });
        if (this.disposed || sequence !== this.playSequence) return;
        if (!response.ok) throw new Error(`動き「${name}」がありません。`);
        animation = await parseMotion(new Uint8Array(await response.arrayBuffer()));
      }
      if (this.disposed || sequence !== this.playSequence) return;
      clip = motionClip(name, animation, this.vrm);
      this.clips.set(name, clip);
    }
    if (this.disposed || sequence !== this.playSequence) return;
    const elapsed = Math.max(0, (this.now() - since) / 1000);
    if (elapsed < clip.duration - FADE_SECONDS) this.start(clip, elapsed);
  }

  // 海が記録から計算した暮らし（null なら、まだ何もしていない＝居場所）。眠りの出入りは、変わった時刻から数秒かけて移る。
  setLife(life) {
    this.life = life;
    const asleep = life?.asleep ? 1 : 0;
    if (!this.sleep) this.sleep = { from: asleep, to: asleep, at: 0 };
    else if (asleep !== this.sleep.to) {
      const now = this.now();
      this.sleep = { from: this.sleepAmount(now), to: asleep, at: now };
    }
  }

  sleepAmount(now) {
    if (!this.sleep) return 0;
    const { from, to, at } = this.sleep;
    return from + (to - from) * smoothstep(THREE.MathUtils.clamp((now - at) / (SLEEP.seconds * 1000), 0, 1));
  }

  // 起きている姿（活動の場所・移動の途中）と寝姿を混ぜて、体の中心と向きから root を置き、座っている重みを返す。
  place(camera, sleep) {
    camera.getWorldDirection(this.look);
    const view = { x: camera.position.x, y: camera.position.y, z: camera.position.z, yaw: Math.atan2(-this.look.x, -this.look.z) };
    const awake = placeAt(this.life?.activity ?? HOME, this.now(), view);
    this.center.set(awake.x, awake.y, awake.z).lerp(BED.center, sleep);
    this.turn.setFromEuler(new THREE.Euler(awake.pitch, awake.yaw, 0, 'YXZ'));
    this.root.quaternion.slerpQuaternions(this.turn, BED.turn, sleep);
    this.root.position.set(0, -HIP, 0).applyQuaternion(this.root.quaternion).add(this.center);
    return awake.sit * (1 - sleep); // 座っている重み（眠れば寝姿へ移る）
  }

  // 姿勢を、重みのぶんだけ今の骨の回転に掛ける。
  bendInto(posture, weight) {
    if (weight <= 0) return;
    for (const { node, rotation } of this.postures[posture]) node.quaternion.multiply(this.bend.identity().slerp(rotation, weight));
  }

  // 表情を変える（null で戻す）。瞬きと視線の表情は選べない。
  setExpression(name) {
    this.expression = name && this.expressions.includes(name) ? name : null;
  }

  // 声が聞こえた。Masterの声にはしばらく顔を向け、本人の声（届いた文字）の間は話す型で揺れる。
  hear(speaker, text = '') {
    if (speaker === 'Master') {
      this.attentionUntil = Math.max(this.attentionUntil, this.clock + LISTEN_SECONDS);
      return;
    }
    const from = Math.max(this.clock, this.speakingUntil);
    this.speakingUntil = Math.min(this.clock + LONGEST_SPEECH, from + [...text].length * SECONDS_PER_CHARACTER);
    this.attentionUntil = Math.max(this.attentionUntil, this.speakingUntil + 3);
  }

  update(delta, camera, focused) {
    this.clock += delta;
    const speaking = this.clock < this.speakingUntil;
    const sleep = this.sleepAmount(this.now());
    const sit = this.place(camera, sleep);
    if (this.action && !this.ending && this.action.time >= this.action.getClip().duration - FADE_SECONDS) {
      this.action.fadeOut(FADE_SECONDS);
      this.ending = true;
    }
    this.mixer.update(delta);
    for (const action of this.finished.splice(0)) {
      action.stop();
      if (action === this.action) this.action = null;
    }
    this.pose.apply();
    this.bendInto('sleep', sleep);
    this.bendInto('sit', sit);
    this.idle.update(delta, speaking);
    this.gaze.update(delta, camera, sleep < .5 && (focused || this.clock < this.attentionUntil));
    // 眠っている間は目を閉じ、選んだ表情もゆるむ（表情が瞬きを止める体でも、目を閉じられるように）。
    this.updateFace(delta, Math.max(sleep, delta > 0 ? this.blink.update(delta, speaking ? .75 : 1) : 0), 1 - sleep);
    this.vrm.update(delta);
  }

  start(clip, offset = 0) {
    const action = this.mixer.clipAction(clip);
    if (this.action && this.action !== action) this.action.fadeOut(FADE_SECONDS);
    action.reset().setLoop(THREE.LoopOnce, 1).fadeIn(FADE_SECONDS).play();
    action.time = offset;
    this.action = action;
    this.ending = false;
  }

  updateFace(delta, blink, awake = 1) {
    const manager = this.vrm.expressionManager;
    if (!manager) return;
    if (this.expression && !this.weights.has(this.expression)) this.weights.set(this.expression, 0);
    const step = 1 - Math.exp(-delta / EXPRESSION_SECONDS);
    for (const [name, weight] of this.weights) {
      const goal = name === this.expression ? 1 : 0;
      const next = Math.abs(goal - weight) < .001 ? goal : weight + (goal - weight) * step;
      manager.setValue(name, next * awake);
      if (next === 0) this.weights.delete(name);
      else this.weights.set(name, next);
    }
    // 表情が瞬きを止める規則（overrideBlink）は、VRMの expressionManager がそのまま守る。
    applyBlink(manager, blink);
  }

  dispose() {
    this.disposed = true;
    this.clips.clear();
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.pose.root);
  }
}
