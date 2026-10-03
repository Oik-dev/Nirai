"use strict";

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("btn-send");

let currentSessionId = null; // サーバ側の active セッション
let viewingPast = false;     // 過去セッション閲覧中（読み取り専用）
let sending = false;
let contextMessageId = null;

const contextMenuEl = $("context-menu");
const ctxDeleteBtn = $("ctx-delete-msg");

/* ---------- 表示ヘルパ ---------- */

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addUserMsg(text, messageId = null) {
  const div = document.createElement("div");
  div.className = "msg user";
  if (messageId != null) div.dataset.messageId = String(messageId);
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  div.appendChild(b);
  bindMessageContextMenu(div);
  messagesEl.appendChild(div);
  scrollBottom();
}

function addSerinaMsg(text, messageId = null) {
  const div = document.createElement("div");
  div.className = "msg serina";
  if (messageId != null) div.dataset.messageId = String(messageId);
  const b = document.createElement("div");
  b.className = "bubble";
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = "セリナ";
  const body = document.createElement("div");
  body.className = "body";
  body.textContent = text || "";
  b.appendChild(who);
  b.appendChild(body);
  div.appendChild(b);
  bindMessageContextMenu(div);
  messagesEl.appendChild(div);
  scrollBottom();
  return body;
}

function addNotice(text) {
  const div = document.createElement("div");
  div.className = "msg notice";
  div.textContent = text;
  messagesEl.appendChild(div);
  scrollBottom();
}

// 2026-07-31 Phase E: Tavily出典（citations）の画面表示。reply本体の下に小さく添えるだけで、
// 会話履歴（addSerinaMsg/セッション）には一切書き込まない（画面の注記。記憶には残らない）。
function renderCitations(bodyEl, citations) {
  const existing = bodyEl.nextElementSibling;
  if (existing && existing.classList && existing.classList.contains("citations")) {
    existing.remove(); // ストリーム経路の重複done（1通目→終幕）で二重表示しない
  }
  const cite = document.createElement("div");
  cite.className = "citations";
  citations.forEach((c, i) => {
    if (!c || typeof c.url !== "string" || !c.url) return;
    // Core側（core/runtime.py:_is_safe_citation_url）でも同じallowlistを掛けているが、
    // 表示層でも二重に確認する（外部由来の未検証URLが初めてクリック可能なhrefになる経路）。
    const scheme = c.url.trim().toLowerCase();
    if (!scheme.startsWith("http://") && !scheme.startsWith("https://")) return;
    const a = document.createElement("a");
    a.href = c.url;
    a.textContent = `出典[${i + 1}]`;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    cite.appendChild(a);
  });
  if (cite.childNodes.length) bodyEl.after(cite);
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function renderHistory(msgs) {
  messagesEl.innerHTML = "";
  for (const m of msgs) {
    if (m.role === "user") addUserMsg(m.content, m.id);
    else addSerinaMsg(m.content, m.id);
  }
  scrollBottom();
}

function hideContextMenu() {
  contextMenuEl.classList.add("hidden");
  contextMessageId = null;
}

function bindMessageContextMenu(msgEl) {
  if (!msgEl.dataset.messageId) return;
  msgEl.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    contextMessageId = msgEl.dataset.messageId;
    contextMenuEl.classList.remove("hidden");
    contextMenuEl.style.left = `${e.clientX}px`;
    contextMenuEl.style.top = `${e.clientY}px`;
  });
}

async function deleteMessage(messageId) {
  const msg = "この発言を削除します。会話の記録からも消え、この発言を含む記憶はいったん外れます（残りの会話は、次の眠りでセリナが思い出し直します）。よろしいですか？";
  if (!window.confirm(msg)) return;
  try {
    const res = await fetch(`/api/messages/${encodeURIComponent(messageId)}?confirm=true`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status}`);
    }
    hideContextMenu();
    if (viewingPast) {
      const active = document.querySelector(".session-item.active");
      const sid = active && active.dataset.id;
      if (sid) {
        renderHistory(await getJSON(`/api/sessions/${encodeURIComponent(sid)}/history`));
      }
    } else {
      await loadCurrent();
    }
    await loadSessions();
  } catch (e) {
    window.alert(`削除できませんでした: ${e.message || e}`);
  }
}

async function startNewSession() {
  const msg = "新しい会話を始めます。いまの会話は履歴に残り、短期・中期の文脈はリセットされます。よろしいですか？";
  if (!window.confirm(msg)) return;
  try {
    const res = await fetch("/api/sessions/new?confirm=true", { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status}`);
    }
    const body = await res.json();
    currentSessionId = body.session_id;
    viewingPast = false;
    $("readonly-bar").classList.add("hidden");
    setComposerEnabled(true);
    await loadCurrent();
    await loadSessions();
    await loadState();
  } catch (e) {
    window.alert(`新しい会話を開始できませんでした: ${e.message || e}`);
  }
}

/* ---------- API ---------- */

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function loadState() {
  const st = await getJSON("/api/state");
  const prevSessionId = currentSessionId;
  currentSessionId = st.session_id;
  // サーバ側で日界rotate等があったら、閲覧中でなければ今日の会話を取り直す
  if (prevSessionId && prevSessionId !== currentSessionId && !viewingPast) {
    await loadCurrent();
    await loadSessions();
  }
  // 眠りで書けなかった記憶のページ（原則1: 無言で捨てない。次の眠りでもう一度書く）
  const notice = $("shelved-notice");
  if (st.unwritten_pages > 0) {
    notice.textContent = `眠りで書けなかった記憶${st.unwritten_pages}件（次の眠りでもう一度）`;
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }
}

let activeViewId = null; // 今メインに表示している会話（現在の会話 or 過去セッションid）

async function loadCurrent() {
  viewingPast = false;
  $("readonly-bar").classList.add("hidden");
  setComposerEnabled(true);
  renderHistory(await getJSON("/api/history"));
  activeViewId = currentSessionId;
  highlightSession(activeViewId);
}

async function loadSessions() {
  const list = await getJSON("/api/sessions");
  const box = $("session-list");
  box.innerHTML = "";
  // 現在の会話を常に先頭へ（メッセージが0件でも表示する）。以降は取得順（新しい順）のまま。
  const ordered = list.filter((s) => !s.empty || s.id === currentSessionId);
  ordered.sort((a, b) => (a.id === currentSessionId ? -1 : b.id === currentSessionId ? 1 : 0));
  for (const s of ordered) {
    const isCurrent = s.id === currentSessionId;
    const item = document.createElement("div");
    item.className = "session-item" + (isCurrent ? " current" : "");
    item.dataset.id = s.id;

    const head = document.createElement("div");
    head.className = "s-head";
    const date = document.createElement("div");
    date.className = "s-date";
    date.textContent = isCurrent ? "現在の会話" : fmtDate(s.last_activity);
    head.appendChild(date);
    if (!isCurrent) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-delete";
      del.title = "この会話を削除";
      del.textContent = "×";
      del.onclick = (e) => {
        e.stopPropagation();
        deleteSession(s.id, fmtDate(s.last_activity));
      };
      head.appendChild(del);
    }

    const prev = document.createElement("div");
    prev.className = "s-preview";
    prev.textContent = s.preview || (isCurrent ? "まだ会話がありません" : "（無題の会話）");
    item.appendChild(head);
    item.appendChild(prev);
    item.onclick = () => { if (isCurrent) loadCurrent(); else openPastSession(s.id); };
    box.appendChild(item);
  }
  highlightSession(activeViewId);
}

async function deleteSession(id, label) {
  const msg = `この会話（${label || id}）を完全に削除します。\n会話の記録からも消え、この会話を含む記憶も外れます。変更ログ以外は残りません。よろしいですか？`;
  if (!window.confirm(msg)) return;
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(id)}?confirm=true`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status}`);
    }
    if (viewingPast) {
      const active = document.querySelector(`.session-item.active`);
      if (active && active.dataset.id === id) {
        await loadCurrent();
      }
    }
    await loadSessions();
  } catch (e) {
    window.alert(`削除できませんでした: ${e.message || e}`);
  }
}

async function openPastSession(id) {
  viewingPast = true;
  $("readonly-bar").classList.remove("hidden");
  setComposerEnabled(false);
  renderHistory(await getJSON(`/api/sessions/${encodeURIComponent(id)}/history`));
  activeViewId = id;
  highlightSession(activeViewId);
}

function highlightSession(id) {
  for (const el of document.querySelectorAll(".session-item")) {
    el.classList.toggle("active", el.dataset.id === id);
  }
}

function setComposerEnabled(on) {
  inputEl.disabled = !on;
  sendBtn.disabled = !on || sending;
}

/* ---------- 送信（NDJSON ストリーム） ---------- */

async function send() {
  const text = inputEl.value.trim();
  if (!text || sending || viewingPast) return;
  sending = true;
  sendBtn.disabled = true;
  inputEl.value = "";
  autoGrow();
  addUserMsg(text);

  const body = addSerinaMsg("");
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  body.after(cursor);
  let streamed = "";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok || !res.body) throw new Error(`chat: ${res.status}`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) handleEvent(JSON.parse(line));
      }
    }
  } catch (e) {
    console.error("chat送信に失敗:", e);
    body.textContent = "ごめん、送信に失敗したみたい。サーバが動いているか確認してもらえる？";
  } finally {
    cursor.remove();
    sending = false;
    sendBtn.disabled = false;
    inputEl.focus();
    loadSessions().catch(() => {});
  }

  function handleEvent(ev) {
    if (ev.type === "token") {
      streamed += ev.text;
      body.textContent = streamed;
      scrollBottom();
    } else if (ev.type === "notice") {
      addNotice(ev.text);
    } else if (ev.type === "error") {
      body.textContent = ev.text;
    } else if (ev.type === "done") {
      // 1通目確定。以降の待ち時間（裏の感情・advisor抽出）でカーソルを点滅させない
      cursor.remove();
      // 非ストリーム時の一括表示と、ストリーム途中失敗→復帰文言の置き換えを兼ねる
      if (ev.reply && ev.reply !== streamed.trim()) body.textContent = ev.reply;
      // 2026-07-31 Phase E: Tavily出典（citations）はreply本体・会話履歴とは別経路で
      // 画面の注記として届く。返答の下に小さく表示するのみで、記憶には残らない。
      if (Array.isArray(ev.citations) && ev.citations.length) {
        renderCitations(body, ev.citations);
      }
      if (ev.session_id && ev.session_id !== currentSessionId) {
        currentSessionId = ev.session_id; // 別れの挨拶でセッションが切り替わった
        addNotice("（新しいセッションになりました）");
      }
      scrollBottom();
    }
  }
}

/* ---------- 入力欄 ---------- */

function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 180) + "px";
}

inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});
sendBtn.onclick = send;
$("btn-back").onclick = loadCurrent;

/* ---------- Pulse（§2.8・チャット欄へ）／長期離席スイッチ ---------- */

const PULSE_POLL_MS = 15000;
let pulseMuted = false;

async function pollPulse() {
  if (pulseMuted || viewingPast) return;
  try {
    const data = await getJSON("/api/pulse/pending");
    const pending = (data && data.messages) || [];
    for (const item of pending) {
      if (item && item.text) addSerinaMsg(item.text);
    }
  } catch (e) {
    // 見回り中の一時不通は無視（次回ポーリングで再試行）
  }
}

async function setAway(on) {
  const sw = $("away-switch");
  const label = $("away-label");
  try {
    await fetch(`/api/pulse/mute?mute=${on ? "true" : "false"}`, { method: "POST" });
    pulseMuted = on;
    sw.classList.toggle("is-on", on);
    sw.setAttribute("aria-checked", String(on));
    label.textContent = on ? "離席設定中" : "長期離席";
    label.classList.toggle("is-on", on);
    addNotice(on ? "長期離席に設定しました（セリナからの声かけを止めます）" : "長期離席を解除しました（声かけを再開します）");
  } catch (e) {
    addNotice("設定の変更に失敗しました");
  }
}

$("away-switch").onclick = () => setAway(!pulseMuted);

document.addEventListener("click", () => hideContextMenu());
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") hideContextMenu();
});
if (ctxDeleteBtn) {
  ctxDeleteBtn.onclick = (e) => {
    e.stopPropagation();
    if (contextMessageId) deleteMessage(contextMessageId);
  };
}
$("btn-new-session").onclick = startNewSession;

/* ---------- 起動 ---------- */

(async function init() {
  try {
    await loadState();
    await loadCurrent();
    await loadSessions();
    await pollPulse();
    setInterval(() => {
      pollPulse().catch(() => {});
    }, PULSE_POLL_MS);
    // 07:00自動rotate等でサーバ側session_idが変わったとき追従する
    setInterval(() => {
      loadState().catch(() => {});
    }, 15000);
  } catch (e) {
    addNotice("サーバに接続できませんでした。Serina.bat から起動してください。");
  }
  inputEl.focus();
})();
