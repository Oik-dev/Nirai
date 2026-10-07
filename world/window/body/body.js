import * as THREE from 'three';
import { MotionPose } from './pose.js';
import { IdleMotion } from './idle.js';
import { Blinker } from './blink.js';
import { NaturalGaze } from './gaze.js';
import { GESTURE_NAMES, gestureAnimation } from './gestures.js';
import { motionClip, parseMotion } from './motions.js';

const FADE_SECONDS = .3;
const EXPRESSION_SECONDS = .12; // 表情が 1/e まで移る秒数
const LISTEN_SECONDS = 8; // Masterが話しかけてから、こちらを向いている長さ
const SECONDS_PER_CHARACTER = .07; // 本人が話している長さの目安（声はないので、文字の長さから）
const LONGEST_SPEECH = 8;
// 瞬きと視線が持つ表情。本人の選ぶ表情には入れない。
export const OWNED_EXPRESSIONS = Object.freeze(['blink', 'blinkLeft', 'blinkRight', 'lookUp', 'lookDown', 'lookLeft', 'lookRight']);

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
export class Body {
  constructor(vrm, root) {
    this.vrm = vrm;
    this.root = root;
    this.pose = new MotionPose(vrm);
    this.mixer = new THREE.AnimationMixer(this.pose.root);
    // 終わった動きは、mixer の更新が済んでから止める（更新の途中で止めると、その回の重ね合わせが崩れる）。
    this.finished = [];
    this.mixer.addEventListener('finished', event => this.finished.push(event.action));
    this.idle = new IdleMotion(vrm);
    this.gaze = new NaturalGaze(vrm, root);
    this.blink = new Blinker();
    this.clips = new Map();
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

  // 組み込みの身振りか、覚えた動き（イデアの body/motions/<名前>.vrma）を始める。
  async play(name) {
    let clip = this.clips.get(name);
    if (!clip) {
      let animation = gestureAnimation(name);
      if (!animation) {
        const response = await fetch(`/motions/${encodeURIComponent(name)}.vrma`, { cache: 'no-store' });
        if (!response.ok) throw new Error(`動き「${name}」がありません。`);
        animation = await parseMotion(new Uint8Array(await response.arrayBuffer()));
      }
      clip = motionClip(name, animation, this.vrm);
      this.clips.set(name, clip);
    }
    this.start(clip);
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
    this.idle.update(delta, speaking);
    this.gaze.update(delta, camera, focused || this.clock < this.attentionUntil);
    this.updateFace(delta, delta > 0 ? this.blink.update(delta, speaking ? .75 : 1) : 0);
    this.vrm.update(delta);
  }

  start(clip) {
    const action = this.mixer.clipAction(clip);
    if (this.action && this.action !== action) this.action.fadeOut(FADE_SECONDS);
    action.reset().setLoop(THREE.LoopOnce, 1).fadeIn(FADE_SECONDS).play();
    this.action = action;
    this.ending = false;
  }

  updateFace(delta, blink) {
    const manager = this.vrm.expressionManager;
    if (!manager) return;
    if (this.expression && !this.weights.has(this.expression)) this.weights.set(this.expression, 0);
    const step = 1 - Math.exp(-delta / EXPRESSION_SECONDS);
    for (const [name, weight] of this.weights) {
      const goal = name === this.expression ? 1 : 0;
      const next = Math.abs(goal - weight) < .001 ? goal : weight + (goal - weight) * step;
      manager.setValue(name, next);
      if (next === 0) this.weights.delete(name);
      else this.weights.set(name, next);
    }
    // 表情が瞬きを止める規則（overrideBlink）は、VRMの expressionManager がそのまま守る。
    applyBlink(manager, blink);
  }

  dispose() {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.pose.root);
  }
}
