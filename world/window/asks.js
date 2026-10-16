// 確認チケットは生ログから毎回読み直す。ここには下書きと最後の表示だけを持つ。
const clock = ts => new Intl.DateTimeFormat('ja-JP', {
  hour: '2-digit', minute: '2-digit', hour12: false,
}).format(new Date(ts));
const when = ts => {
  const date = new Date(ts);
  return date.toDateString() === new Date().toDateString() ? clock(ts)
    : `${new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric' }).format(date)} ${clock(ts)}`;
};
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};

export class MasterAsks {
  constructor(onChange = () => {}) {
    this.onChange = onChange;
    this.button = document.getElementById('asksButton');
    this.panel = document.getElementById('asksPanel');
    this.list = document.getElementById('asksList');
    this.warning = document.getElementById('asksWarning');
    this.items = null; // nullはまだ一度も読めていない
    this.lastSeen = null;
    this.drafts = new Map();
    this.sending = new Set();
    this.renderVersion = 0;
    this.feedback = new Map();
    this.open = false;
    this.abort = new AbortController();
    this.button.addEventListener('click', () => this.toggle());
    document.getElementById('asksClose').addEventListener('click', () => this.toggle(false));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && this.open) this.toggle(false);
    }, { signal: this.abort.signal });
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) void this.refresh();
    }, { signal: this.abort.signal });
    this.timer = setInterval(() => {
      if (!document.hidden) void this.refresh();
    }, 60_000);
    void this.refresh();
  }

  toggle(value = !this.open) {
    this.open = value;
    this.panel.hidden = !value;
    this.button.setAttribute('aria-expanded', String(value));
    if (value) void this.refresh();
  }

  openForResident(resident) {
    if (!this.items?.some(item => item.resident === resident)) return;
    this.toggle(true);
    const index = this.items.findIndex(item => item.resident === resident);
    this.list.children[index]?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
  }

  async refresh() {
    try {
      const response = await fetch('/master/asks', { cache: 'no-store', signal: this.abort.signal });
      if (!response.ok) throw new Error('post office');
      const data = await response.json();
      if (!Array.isArray(data.asks)) throw new Error('bad response');
      const changed = JSON.stringify(this.items) !== JSON.stringify(data.asks);
      this.items = data.asks;
      this.lastSeen = new Date();
      this.warning.textContent = '';
      if (changed) {
        this.render();
        this.onChange();
      }
    } catch {
      // 通信不能を「0枚」と誤認しない。最後に読めた内容は維持する。
      if (this.open && this.lastSeen) this.warning.textContent = `郵便局につながらない（最後に見た時刻 ${clock(this.lastSeen)}）`;
    }
  }

  render() {
    if (this.items === null) return;
    this.renderVersion++;
    const count = this.items.length;
    this.button.hidden = count === 0;
    this.button.textContent = `確認 ${count}`;
    if (!count) this.toggle(false);
    this.list.replaceChildren();
    for (const item of this.items) {
      const card = el('article', undefined, 'ask-card');
      const header = el('div', `${item.resident} · ${item.work} · ${when(item.asked)}`, 'ask-meta');
      const question = el('p', item.question, 'ask-question');
      const original = el('details', undefined, 'ask-original');
      original.append(el('summary', '元の手紙'), el('p', `${item.from} · ${item.letter}\n${item.letterBody}`));
      const input = el('textarea');
      input.rows = 3;
      input.maxLength = 12000;
      input.setAttribute('aria-label', `${item.resident}への返事`);
      input.value = this.drafts.get(item.letter) ?? '';
      input.addEventListener('input', () => this.drafts.set(item.letter, input.value));
      const submit = el('button', '送る');
      submit.type = 'button';
      submit.disabled = this.sending.has(item.letter);
      const message = el('div', this.feedback.get(item.letter) ?? '', 'ask-message');
      submit.addEventListener('click', () => void this.answer(item, input, submit, message));
      card.append(header, question, original, input, submit, message);
      this.list.append(card);
    }
  }

  async answer(item, input, button, message) {
    const body = input.value;
    if (!body || body.length > 12000 || this.sending.has(item.letter)) return;
    this.drafts.set(item.letter, body);
    const before = this.renderVersion;
    this.sending.add(item.letter);
    button.disabled = true;
    try {
      const response = await fetch('/master/answer', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ letter: item.letter, asked: item.asked, body }),
        signal: this.abort.signal,
      });
      if (response.status === 409) {
        message.textContent = 'この問いは、もう変わった';
        this.feedback.set(item.letter, message.textContent);
        await this.refresh();
        // 再取得した問いにも下書きを残す
        return;
      }
      if (!response.ok) throw new Error('post office');
      this.drafts.delete(item.letter);
      this.feedback.delete(item.letter);
      this.items = this.items.filter(ticket => ticket.letter !== item.letter || ticket.asked !== item.asked);
      this.render();
      await this.refresh();
    } catch {
      message.textContent = '届かなかった。もう一度送れる';
      this.feedback.set(item.letter, message.textContent);
    } finally {
      this.sending.delete(item.letter);
      // 送信中に問いが更新されて新しいDOMができた場合も、送信可能に戻す。
      if (this.renderVersion !== before) this.render();
      else button.disabled = false;
    }
  }

  dispose() {
    clearInterval(this.timer);
    this.abort.abort();
  }
}
