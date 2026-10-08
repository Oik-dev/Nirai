import { expressionKey } from './catalog.js';

async function readSnapshot(signal) {
  const response = await fetch('/sea/body', { cache: 'no-store', signal });
  if (!response.ok) throw new Error('体の記録を読み込めませんでした。');
  return response.json();
}

// 表情は記録から復元する。身振りは、今この窓に届いた知らせだけを同じ Body.play へ渡す。
export class BodyChoices {
  constructor({ body, reloadAvatar, snapshot = readSnapshot, invalidate = () => {}, onError = console.error }) {
    this.body = body;
    this.reloadAvatar = reloadAvatar;
    this.snapshot = snapshot;
    this.invalidate = invalidate;
    this.onError = onError;
    this.catalog = null;
    this.revision = null;
    this.expression = null;
    this.expressionVersion = 0;
    this.pendingGestures = [];
    this.refreshAbort = null;
    this.loading = false;
    this.disposed = false;
  }

  async refresh() {
    if (this.disposed) return false;
    this.refreshAbort?.abort();
    const abort = new AbortController();
    this.refreshAbort = abort;
    this.loading = true;
    const expressionVersion = this.expressionVersion;
    try {
      const state = await this.snapshot(abort.signal);
      if (abort.signal.aborted) return false;
      if (typeof state.revision !== 'string'
        || !Array.isArray(state.catalog?.expressions) || !state.catalog.expressions.every(name => typeof name === 'string')
        || !Array.isArray(state.catalog?.gestures) || !state.catalog.gestures.every(name => typeof name === 'string')
        || !(state.expression === null || typeof state.expression === 'string')) {
        throw new Error('体の記録の形を確認できませんでした。');
      }
      if (state.revision !== this.revision) await this.reloadAvatar(abort.signal);
      if (abort.signal.aborted) return false;
      this.catalog = state.catalog;
      this.revision = state.revision;
      // 読み直しの間に新しい選びが届いていたら、古い記録で上書きしない。
      if (this.expressionVersion === expressionVersion || !this.availableExpression(this.expression)) {
        this.expression = this.availableExpression(state.expression) ? state.expression : null;
      }
      this.loading = false;
      this.applyExpression();
      for (const name of this.pendingGestures.splice(0)) this.play(name);
      return true;
    } catch (error) {
      if (!abort.signal.aborted) {
        this.loading = false;
        this.pendingGestures.length = 0;
        this.onError(error);
      }
      return false;
    }
  }

  availableExpression(name) {
    return name === null || this.catalog?.expressions.includes(name);
  }

  receive(records) {
    if (this.disposed) return;
    for (const record of records) {
      if (record?.kind === 'expression' && typeof record.value === 'string') {
        const expression = record.value === 'なし' ? null : record.value;
        if (!this.loading && this.catalog && !this.availableExpression(expression)) continue;
        this.expression = expression;
        this.expressionVersion++;
        if (!this.loading) this.applyExpression();
      }
      if (record?.kind === 'gesture' && typeof record.value === 'string') {
        if (this.loading || !this.catalog) this.pendingGestures.push(record.value);
        else this.play(record.value);
      }
    }
  }

  applyExpression() {
    const body = this.body();
    if (!body) return;
    body.setExpression(this.availableExpression(this.expression)
      ? expressionKey(this.expression, body.expressions) : null);
    this.invalidate();
  }

  play(name) {
    const body = this.body();
    if (!body || !this.catalog?.gestures.includes(name)) return;
    Promise.resolve(body.play(name)).catch(this.onError).finally(this.invalidate);
  }

  dispose() {
    this.disposed = true;
    this.refreshAbort?.abort();
    this.pendingGestures.length = 0;
  }
}
