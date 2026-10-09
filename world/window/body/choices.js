import { expressionKey } from './catalog.js';

async function readSnapshot(signal) {
  const response = await fetch('/sea/body', { cache: 'no-store', signal });
  if (!response.ok) throw new Error('体の記録を読み込めませんでした。');
  return response.json();
}

const isTime = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
const isActivity = value => value !== null && typeof value === 'object'
  && typeof value.name === 'string' && (value.since === null || isTime(value.since));

// 海が記録から計算した暮らし（計画書§2.5）の形。窓はこの形だけを体へ渡す。
export function validLife(life) {
  return life === null || (typeof life === 'object'
    && isActivity(life.activity) && (life.activity.from === null || isActivity(life.activity.from))
    && (life.expression === null || typeof life.expression === 'string')
    && (life.expressionAt === undefined || life.expressionAt === null || isTime(life.expressionAt))
    && (life.gesture === null || (typeof life.gesture === 'object' && typeof life.gesture?.name === 'string' && isTime(life.gesture.at)))
    && typeof life.asleep === 'boolean');
}

// 体の今は、海が記録から計算した暮らしを、変わったと知らされるたびにまるごと読み直す（取りこぼしも順番の入れ違いも残らない）。
// 身振りは始まりから長さの間だけ見えるので、同じ身振りを二度は始めず、窓を開いたときに終わっていれば体が始めない。
export class BodyChoices {
  constructor({ body, reloadAvatar, snapshot = readSnapshot, invalidate = () => {}, onError = console.error }) {
    this.body = body;
    this.reloadAvatar = reloadAvatar;
    this.snapshot = snapshot;
    this.invalidate = invalidate;
    this.onError = onError;
    this.catalog = null;
    this.revision = null;
    this.life = null;
    this.gesture = null;
    this.refreshAbort = null;
    this.disposed = false;
  }

  async refresh() {
    if (this.disposed) return false;
    this.refreshAbort?.abort();
    const abort = new AbortController();
    this.refreshAbort = abort;
    try {
      const state = await this.snapshot(abort.signal);
      if (abort.signal.aborted) return false;
      if (typeof state.revision !== 'string'
        || !Array.isArray(state.catalog?.expressions) || !state.catalog.expressions.every(name => typeof name === 'string')
        || !Array.isArray(state.catalog?.gestures) || !state.catalog.gestures.every(name => typeof name === 'string')
        || !validLife(state.life)) {
        throw new Error('体の記録の形を確認できませんでした。');
      }
      if (state.revision !== this.revision) await this.reloadAvatar(abort.signal);
      if (abort.signal.aborted) return false;
      this.catalog = state.catalog;
      this.revision = state.revision;
      this.life = state.life;
      this.apply();
      return true;
    } catch (error) {
      if (!abort.signal.aborted) this.onError(error);
      return false;
    }
  }

  apply() {
    const body = this.body();
    if (!body) return;
    body.setLife(this.life);
    const expression = this.life?.expression ?? null;
    body.setExpression(expression !== null && this.catalog.expressions.includes(expression)
      ? expressionKey(expression, body.expressions) : null, this.life?.expressionAt ?? null);
    const gesture = this.life?.gesture;
    const key = gesture ? `${gesture.at} ${gesture.name}` : null;
    if (gesture && key !== this.gesture && this.catalog.gestures.includes(gesture.name)) {
      this.gesture = key;
      Promise.resolve(body.play(gesture.name, Date.parse(gesture.at))).catch(this.onError).finally(this.invalidate);
    }
    this.invalidate();
  }

  dispose() {
    this.disposed = true;
    this.refreshAbort?.abort();
  }
}
