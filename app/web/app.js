"use strict";

const $ = (id) => document.getElementById(id);
const messagesEl = $("messages");
const inputEl = $("input");
const sendBtn = $("btn-send");

let currentSessionId = null; // サーバ側の active セッション
let viewingPast = false;     // 過去セッション閲覧中（読み取り専用）
let sending = false;

/* ---------- 表示ヘルパ ---------- */

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addUserMsg(text) {
  const div = document.createElement("div");
  div.className = "msg user";
  const b = document.createElement("div");
  b.className = "bubble";
  b.textContent = text;
  div.appendChild(b);
  messagesEl.appendChild(div);
  scrollBottom();
}

function addSerinaMsg(text) {
  const div = document.createElement("div");
  div.className = "msg serina";
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

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function renderHistory(msgs) {
  messagesEl.innerHTML = "";
  for (const m of msgs) {
    if (m.role === "user") addUserMsg(m.content);
    else addSerinaMsg(m.content);
  }
  scrollBottom();
}

/* ---------- API ---------- */

async function getJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json();
}

async function loadState() {
  const st = await getJSON("/api/state");
  currentSessionId = st.session_id;
  // 2026-07-12追加: 棚上げ棚（毒饅頭ジョブ）の件数表示。原則1「無言破棄禁止」のGUI側表示。
  const notice = $("shelved-notice");
  if (st.shelved > 0) {
    notice.textContent = `処理できなかった宿題${st.shelved}件`;
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }
}

async function loadCurrent() {
  viewingPast = false;
  $("readonly-bar").classList.add("hidden");
  $("btn-current").classList.add("active");
  setComposerEnabled(true);
  renderHistory(await getJSON("/api/history"));
  highlightSession(null);
}

async function loadSessions() {
  const list = await getJSON("/api/sessions");
  const box = $("session-list");
  box.innerHTML = "";
  for (const s of list) {
    if (s.id === currentSessionId || s.empty) continue;
    const item = document.createElement("div");
    item.className = "session-item";
    item.dataset.id = s.id;
    const date = document.createElement("div");
    date.className = "s-date";
    date.textContent = fmtDate(s.last_activity);
    const prev = document.createElement("div");
    prev.className = "s-preview";
    prev.textContent = s.preview || "（無題の会話）";
    item.appendChild(date);
    item.appendChild(prev);
    item.onclick = () => openPastSession(s.id);
    box.appendChild(item);
  }
}

async function openPastSession(id) {
  viewingPast = true;
  $("readonly-bar").classList.remove("hidden");
  $("btn-current").classList.remove("active");
  setComposerEnabled(false);
  renderHistory(await getJSON(`/api/sessions/${encodeURIComponent(id)}/history`));
  highlightSession(id);
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
      if (!streamed && ev.reply) body.textContent = ev.reply; // 非ストリーム系Skill（蒸留等）
      if (ev.session_id && ev.session_id !== currentSessionId) {
        currentSessionId = ev.session_id; // 蒸留でセッションが切り替わった
        addNotice("（新しいセッションになりました）");
      }
      scrollBottom();
    }
  }
}

/* ---------- アルバム ---------- */

async function openAlbum() {
  const overlay = $("album-overlay");
  const bodyEl = $("album-body");
  bodyEl.innerHTML = "";
  overlay.classList.remove("hidden");
  try {
    const diaries = await getJSON("/api/album");
    if (!diaries.length) {
      bodyEl.innerHTML = '<div class="album-empty">まだ日記がありません。会話を重ねると、セリナが日記を書きます。</div>';
      return;
    }
    for (const d of diaries) {
      const card = document.createElement("div");
      card.className = "diary-card";
      const date = document.createElement("div");
      date.className = "d-date";
      date.textContent = fmtDate(d.created_at);
      const content = document.createElement("div");
      content.className = "d-body";
      content.textContent = d.content;
      card.appendChild(date);
      card.appendChild(content);
      bodyEl.appendChild(card);
    }
  } catch (e) {
    bodyEl.innerHTML = '<div class="album-empty">アルバムを読み込めませんでした。</div>';
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
$("btn-current").onclick = loadCurrent;
$("btn-back").onclick = loadCurrent;
$("btn-album").onclick = openAlbum;
$("btn-album-close").onclick = () => $("album-overlay").classList.add("hidden");
$("album-overlay").addEventListener("click", (e) => {
  if (e.target.id === "album-overlay") $("album-overlay").classList.add("hidden");
});

/* ---------- 起動 ---------- */

(async function init() {
  try {
    await loadState();
    await loadCurrent();
    await loadSessions();
  } catch (e) {
    addNotice("サーバに接続できませんでした。Serina.bat から起動してください。");
  }
  inputEl.focus();
})();
