const HISTORY_LIMIT = 80;
const DRAFT_KEY = 'nirai.chat.draft';

function nearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 56;
}

function dayLabel(ts) {
  const d = new Date(ts);
  return new Intl.DateTimeFormat('ja-JP', { month: 'numeric', day: 'numeric', weekday: 'short' }).format(d);
}

function clockLabel(ts) {
  const d = new Date(ts);
  return new Intl.DateTimeFormat('ja-JP', { hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}

function sameLocalDay(a, b) {
  const x = new Date(a);
  const y = new Date(b);
  return x.getFullYear() === y.getFullYear()
    && x.getMonth() === y.getMonth()
    && x.getDate() === y.getDate();
}

export class ChatWindow {
  constructor() {
    this.panel = document.getElementById('chatPanel');
    this.messages = document.getElementById('chatMessages');
    this.form = document.getElementById('chatForm');
    this.input = document.getElementById('chatInput');
    this.send = document.getElementById('chatSend');
    this.mute = document.getElementById('chatMute');
    this.status = document.getElementById('chatStatus');
    this.rows = [];
    this.refs = new Set();
    this.hasMore = true;
    this.loadingOlder = false;
    this.sending = false;
    this.muted = false;
    this.desiredWidth = 528;
    this.desiredHeight = 460;
    this.abort = new AbortController();
    this.events = null;
    this.eventRetryTimer = null;
    this.eventRetryMs = 500;
    this.refreshPending = false;
  }

  async start() {
    this.input.value = localStorage.getItem(DRAFT_KEY) ?? '';
    this.installEvents();
    this.applySize();
    try {
      await this.loadLatest(true);
    } catch {
      this.status.textContent = '会話を読み込めませんでした。再接続します。';
    }
    this.connectEvents();
  }

  installEvents() {
    const options = { signal: this.abort.signal };
    this.input.addEventListener('input', () => {
      localStorage.setItem(DRAFT_KEY, this.input.value);
      this.growInput();
    }, options);
    this.input.addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        this.form.requestSubmit();
      }
    }, options);
    this.form.addEventListener('submit', event => {
      event.preventDefault();
      void this.submit();
    }, options);
    this.messages.addEventListener('scroll', () => {
      if (this.messages.scrollTop < 36) void this.loadOlder();
    }, options);
    this.mute.addEventListener('click', () => void this.toggleMute(), options);
    window.addEventListener('resize', () => this.applySize(), options);
    this.installResize(document.getElementById('chatResizeTop'), false, true);
    this.installResize(document.getElementById('chatResizeRight'), true, false);
    this.installResize(document.getElementById('chatResizeCorner'), true, true);
    this.growInput();
  }

  installResize(handle, horizontal, vertical) {
    handle.addEventListener('pointerdown', event => {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      const startX = event.clientX;
      const startY = event.clientY;
      const startWidth = this.desiredWidth;
      const startHeight = this.desiredHeight;
      const move = moveEvent => {
        if (horizontal) this.desiredWidth = Math.max(320, startWidth + moveEvent.clientX - startX);
        if (vertical) this.desiredHeight = Math.max(260, startHeight + startY - moveEvent.clientY);
        this.applySize();
      };
      const cleanup = () => {
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', finish);
        handle.removeEventListener('pointercancel', cancel);
        handle.removeEventListener('lostpointercapture', finish);
        window.removeEventListener('keydown', keydown);
      };
      const finish = () => cleanup();
      const cancel = () => {
        this.desiredWidth = startWidth;
        this.desiredHeight = startHeight;
        this.applySize();
        cleanup();
      };
      const keydown = keyEvent => {
        if (keyEvent.key === 'Escape') cancel();
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', finish);
      handle.addEventListener('pointercancel', cancel);
      handle.addEventListener('lostpointercapture', finish);
      window.addEventListener('keydown', keydown);
    }, { signal: this.abort.signal });
  }

  applySize() {
    const stick = this.messages ? nearBottom(this.messages) : true;
    const oldTop = this.messages?.scrollTop ?? 0;
    const anchor = this.messages
      ? [...this.messages.children].find(node => node.offsetTop + node.offsetHeight >= oldTop)
      : null;
    const anchorOffset = anchor ? anchor.offsetTop - oldTop : 0;
    const width = Math.min(this.desiredWidth, Math.max(300, innerWidth - 28));
    const height = Math.min(this.desiredHeight, Math.max(240, innerHeight - 28));
    this.panel.style.width = `${width}px`;
    this.panel.style.height = `${height}px`;
    if (this.messages) {
      if (stick) this.messages.scrollTop = this.messages.scrollHeight;
      else if (anchor) this.messages.scrollTop = anchor.offsetTop - anchorOffset;
    }
  }

  growInput() {
    this.input.style.height = 'auto';
    this.input.style.height = `${Math.min(112, Math.max(38, this.input.scrollHeight))}px`;
  }

  async fetchPage(params = '') {
    const response = await fetch(`/api/conversation?limit=${HISTORY_LIMIT}${params}`, { cache: 'no-store' });
    if (!response.ok) {
      const error = new Error('会話を読み込めませんでした。');
      error.status = response.status;
      throw error;
    }
    return response.json();
  }

  async loadLatest(initial = false) {
    const stick = initial || nearBottom(this.messages);
    const data = await this.fetchPage();
    this.hasMore = data.has_more;
    this.merge(data.messages);
    this.render(stick);
  }

  async reconcileLatest() {
    const stick = nearBottom(this.messages);
    const known = new Set(this.refs);
    let data = await this.fetchPage();
    if (known.size === 0) this.hasMore = data.has_more;
    let overlap = data.messages.some(row => known.has(row.ref));
    this.merge(data.messages);
    while (known.size > 0 && !overlap && data.has_more && data.messages.length > 0) {
      const before = data.messages[0].ref;
      data = await this.fetchPage(`&before=${encodeURIComponent(before)}`);
      overlap = data.messages.some(row => known.has(row.ref));
      this.merge(data.messages);
    }
    if (known.size > 0 && !overlap && !data.has_more) this.hasMore = false;
    this.render(stick);
  }

  async loadOlder() {
    if (!this.hasMore || this.loadingOlder) return;
    const first = this.rows.find(row => row.ref && !row.pending);
    if (!first) return;
    this.loadingOlder = true;
    const oldHeight = this.messages.scrollHeight;
    const oldTop = this.messages.scrollTop;
    try {
      const data = await this.fetchPage(`&before=${encodeURIComponent(first.ref)}`);
      this.hasMore = data.has_more;
      this.merge(data.messages);
      this.render(false);
      this.messages.scrollTop = oldTop + (this.messages.scrollHeight - oldHeight);
    } catch (error) {
      if (error.status !== 404) {
        this.status.textContent = '古い会話を読み込めませんでした。';
        return;
      }
      this.rows = this.rows.filter(row => row !== first);
      this.refs.delete(first.ref);
      const next = this.rows.find(row => row.ref && !row.pending);
      if (!next) {
        await this.loadLatest(false);
        return;
      }
      const data = await this.fetchPage(`&before=${encodeURIComponent(next.ref)}`);
      this.hasMore = data.has_more;
      this.merge(data.messages);
      this.render(false);
    } finally {
      this.loadingOlder = false;
    }
  }

  merge(incoming) {
    for (const row of incoming) {
      if (this.refs.has(row.ref)) continue;
      this.refs.add(row.ref);
      this.rows.push(row);
    }
    this.rows.sort((a, b) => new Date(a.ts) - new Date(b.ts));
  }

  render(stick = false) {
    const oldHeight = this.messages.scrollHeight;
    const oldTop = this.messages.scrollTop;
    this.messages.replaceChildren();
    let previous = null;
    for (const row of this.rows) {
      if (!previous || !sameLocalDay(previous.ts, row.ts)) {
        const day = document.createElement('div');
        day.className = 'chat-day';
        day.textContent = dayLabel(row.ts);
        this.messages.append(day);
      }
      const item = document.createElement('article');
      item.className = `chat-message ${row.speaker === 'Master' ? 'master' : 'resident'}${row.pending ? ' pending' : ''}`;
      const bubble = document.createElement('div');
      bubble.className = 'chat-bubble';
      const text = document.createElement('div');
      text.className = 'chat-text';
      text.textContent = row.text;
      const meta = document.createElement('div');
      meta.className = 'chat-meta';
      const time = document.createElement('time');
      time.dateTime = row.ts;
      time.textContent = clockLabel(row.ts);
      meta.append(time);
      if (row.ref && !row.pending) {
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'chat-delete';
        del.textContent = '削除';
        del.title = 'この発言を削除';
        del.addEventListener('click', () => void this.remove(row));
        meta.append(del);
      }
      bubble.append(text, meta);
      item.append(bubble);
      this.messages.append(item);
      previous = row;
    }
    if (stick) this.messages.scrollTop = this.messages.scrollHeight;
    else if (oldHeight) this.messages.scrollTop = oldTop;
  }

  async remove(row) {
    if (!confirm('この発言を記録から削除しますか？')) return;
    const response = await fetch(`/api/conversation/${encodeURIComponent(row.ref)}?confirm=true`, { method: 'DELETE' });
    if (!response.ok) {
      this.status.textContent = '削除できませんでした。';
      return;
    }
    this.rows = this.rows.filter(item => item.ref !== row.ref);
    this.refs.delete(row.ref);
    this.render(false);
  }

  connectEvents() {
    if (this.abort.signal.aborted) return;
    if (this.eventRetryTimer !== null) {
      clearTimeout(this.eventRetryTimer);
      this.eventRetryTimer = null;
    }
    this.events?.close();
    const events = new EventSource('/api/events');
    this.events = events;
    events.onopen = () => {
      this.eventRetryMs = 500;
      void this.reconcileLatest()
        .then(() => {
          if (!this.sending) this.status.textContent = '';
        })
        .catch(() => {
          if (!this.sending) this.status.textContent = '会話を読み込めませんでした。再接続します。';
        });
    };
    events.onmessage = event => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === 'said') {
          if (this.sending) this.refreshPending = true;
          else void this.loadLatest(false);
        }
      } catch {}
    };
    events.onerror = () => {
      if (events.readyState !== EventSource.CLOSED || this.events !== events || this.abort.signal.aborted) return;
      events.close();
      this.events = null;
      if (this.eventRetryTimer !== null) return;
      const wait = this.eventRetryMs;
      this.eventRetryMs = Math.min(8000, this.eventRetryMs * 2);
      this.eventRetryTimer = setTimeout(() => {
        this.eventRetryTimer = null;
        this.connectEvents();
      }, wait);
    };
  }

  async toggleMute() {
    const next = !this.muted;
    const response = await fetch(`/api/pulse/mute?mute=${next}`, { method: 'POST' });
    if (!response.ok) return;
    this.muted = next;
    this.mute.setAttribute('aria-pressed', String(next));
    this.mute.textContent = next ? '静かにしています' : '静かに';
  }

  async submit() {
    const draft = this.input.value;
    const text = draft.trim();
    if (!text || this.sending) return;
    this.sending = true;
    this.send.disabled = true;
    this.status.textContent = '考えています…';
    const now = new Date().toISOString();
    const user = { ref: `pending-user-${Date.now()}`, ts: now, speaker: 'Master', text, pending: true };
    const reply = { ref: `pending-reply-${Date.now()}`, ts: now, speaker: 'Serina', text: '', pending: true };
    this.rows.push(user, reply);
    this.render(true);
    this.input.value = '';
    if (localStorage.getItem(DRAFT_KEY) === draft) localStorage.removeItem(DRAFT_KEY);
    this.growInput();
    let failed = false;
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ text }),
      });
      if (!response.ok || !response.body) throw new Error('send');
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
        const lines = buffer.split('\n');
        buffer = done ? '' : lines.pop() ?? '';
        for (const raw of lines) {
          if (!raw.trim()) continue;
          const stick = nearBottom(this.messages);
          const event = JSON.parse(raw);
          if (event.type === 'token') reply.text += event.text ?? '';
          if (event.type === 'done' && event.reply) reply.text = event.reply;
          if (event.type === 'error') {
            failed = true;
            this.status.textContent = event.text ?? '返事を受け取れませんでした。';
          }
          if (event.type === 'notice') {
            failed = true;
            this.status.textContent = event.text ?? '';
          }
          this.render(stick);
        }
        if (done) break;
      }
      if (failed) throw new Error('turn');
      const stick = nearBottom(this.messages);
      this.rows = this.rows.filter(row => !row.pending);
      this.refreshPending = false;
      try {
        await this.loadLatest(false);
        this.status.textContent = '';
      } catch {
        this.render(stick);
        this.status.textContent = '返事は届きました。履歴を再接続します。';
        this.refreshPending = true;
      }
    } catch {
      const stick = nearBottom(this.messages);
      this.rows = this.rows.filter(row => !row.pending);
      this.render(stick);
      if (!this.input.value && localStorage.getItem(DRAFT_KEY) === null) {
        this.input.value = draft;
        localStorage.setItem(DRAFT_KEY, draft);
        this.growInput();
      }
      this.status.textContent = '返事を受け取れませんでした。';
    } finally {
      this.sending = false;
      this.send.disabled = false;
      if (this.refreshPending) {
        this.refreshPending = false;
        void this.reconcileLatest().catch(() => {
          this.status.textContent = '会話を読み込めませんでした。再接続します。';
        });
      }
      this.input.focus();
    }
  }

  dispose() {
    this.abort.abort();
    this.events?.close();
    if (this.eventRetryTimer !== null) clearTimeout(this.eventRetryTimer);
  }
}

export async function startChat() {
  const chat = new ChatWindow();
  await chat.start();
  return chat;
}
