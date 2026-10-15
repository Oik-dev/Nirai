import { expressionKey } from './catalog.js';

async function readSnapshot(signal) {
  const response = await fetch('/sea/body', { cache: 'no-store', signal });
  if (!response.ok) throw new Error('体の記録を読み込めませんでした。');
  return response.json();
}

const isTime = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
const isActivity = value => value !== null && typeof value === 'object'
  && typeof value.name === 'string' && (value.since === null || isTime(value.since));
const isRoute = value => value === undefined || (value !== null && typeof value === 'object'
  && isActivity(value.origin) && Array.isArray(value.changes)
  && value.changes.every(change => isActivity(change) && isTime(change.since))
  && value.changes.every((change, i) => i === 0 || Date.parse(value.changes[i - 1].since) <= Date.parse(change.since)));

// 海が記録から計算した暮らし（計画書§2.5）の形。窓はこの形だけを体へ渡す。
export function validLife(life) {
  return life === null || (typeof life === 'object'
    && isActivity(life.activity) && (life.activity.from === null || isActivity(life.activity.from))
    && isRoute(life.activity.route)
    && (life.expression === null || typeof life.expression === 'string')
    && (life.expressionAt === undefined || life.expressionAt === null || isTime(life.expressionAt))
    && (life.appearance === undefined || (life.appearance !== null
      && typeof life.appearance === 'object' && !Array.isArray(life.appearance)
      && Object.values(life.appearance).every(value => typeof value === 'string')))
    && (life.gesture === null || (typeof life.gesture === 'object' && typeof life.gesture?.name === 'string' && isTime(life.gesture.at)))
    && typeof life.asleep === 'boolean');
}

// 体の今は、海が記録から計算した暮らしを、変わったと知らされるたびにまるごと読み直す（取りこぼしも順番の入れ違いも残らない）。
// 身振りは始まりから長さの間だけ見えるので、同じ身振りを二度は始めず、窓を開いたときに終わっていれば体が始めない。
export class BodyChoices {
  constructor({ body, reloadAvatar, removeAvatar = () => {}, snapshot = readSnapshot, invalidate = () => {}, onError = console.error }) {
    this.body = body;
    this.reloadAvatar = reloadAvatar;
    this.removeAvatar = removeAvatar;
    this.snapshot = snapshot;
    this.invalidate = invalidate;
    this.onError = onError;
    this.catalogs = {};
    this.revisions = {};
    this.lives = {};
    this.gestures = {};
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
      if (!state.lives || typeof state.lives !== 'object' || Array.isArray(state.lives)
        || !state.catalogs || typeof state.catalogs !== 'object'
        || !state.revisions || typeof state.revisions !== 'object') {
        throw new Error('体の記録の形を確認できませんでした。');
      }
      const names = Object.keys(state.lives).sort((a, b) => a.localeCompare(b));
      for (const name of Object.keys(this.revisions)) {
        if (!names.includes(name)) {
          this.removeAvatar(name);
          delete this.revisions[name]; delete this.catalogs[name]; delete this.lives[name]; delete this.gestures[name];
        }
      }
      for (const name of names) {
        if (abort.signal.aborted) return false;
        const catalog = state.catalogs[name];
        const revision = state.revisions[name];
        if (typeof revision !== 'string' || !Array.isArray(catalog?.expressions)
          || !catalog.expressions.every(value => typeof value === 'string')
          || !Array.isArray(catalog.gestures)
          || !catalog.gestures.every(value => typeof value === 'string')
          || !validLife(state.lives[name]) || state.lives[name] === null) {
          this.onError(new Error(`${name}の暮らしを確認できませんでした。`));
          continue;
        }
        try {
          if (revision !== this.revisions[name]) await this.reloadAvatar(name, abort.signal);
          if (abort.signal.aborted) return false;
          this.revisions[name] = revision;
          this.catalogs[name] = catalog;
          this.lives[name] = state.lives[name];
          this.apply(name, names);
        } catch (error) { if (!abort.signal.aborted) this.onError(error); }
      }
      this.invalidate();
      return true;
    } catch (error) {
      if (!abort.signal.aborted) this.onError(error);
      return false;
    }
  }

  apply(name, names) {
    const body = this.body(name);
    if (!body) return;
    const life = this.lives[name];
    const catalog = this.catalogs[name];
    body.setCompany?.(name, names);
    body.setLife(life);
    body.setAppearance?.(life?.appearance ?? {});
    const expression = life?.expression ?? null;
    body.setExpression(expression !== null && catalog.expressions.includes(expression)
      ? expressionKey(expression, body.expressions) : null, life?.expressionAt ?? null);
    const gesture = life?.gesture;
    const key = gesture ? `${gesture.at} ${gesture.name}` : null;
    if (gesture && key !== this.gestures[name] && catalog.gestures.includes(gesture.name)) {
      this.gestures[name] = key;
      Promise.resolve(body.play(gesture.name, Date.parse(gesture.at))).catch(this.onError).finally(this.invalidate);
    }
    this.invalidate();
  }

  dispose() {
    this.disposed = true;
    this.refreshAbort?.abort();
  }
}
