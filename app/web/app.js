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
  closeEvalPanel();
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

    const head = document.createElement("div");
    head.className = "s-head";
    const date = document.createElement("div");
    date.className = "s-date";
    date.textContent = fmtDate(s.last_activity);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-delete";
    del.title = "この会話を削除";
    del.textContent = "×";
    del.onclick = (e) => {
      e.stopPropagation();
      deleteSession(s.id, fmtDate(s.last_activity));
    };
    head.appendChild(date);
    head.appendChild(del);

    const prev = document.createElement("div");
    prev.className = "s-preview";
    prev.textContent = s.preview || "（無題の会話）";
    item.appendChild(head);
    item.appendChild(prev);
    item.onclick = () => openPastSession(s.id);
    box.appendChild(item);
  }
}

async function deleteSession(id, label) {
  const msg = `この会話（${label || id}）を完全に削除します。\n変更ログ以外は残りません。よろしいですか？`;
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
  closeEvalPanel();
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
    } else if (ev.type === "followup") {
      addSerinaMsg(ev.text); // advisor結果の2通目（1通目は置換しない）
      scrollBottom();
    } else if (ev.type === "error") {
      body.textContent = ev.text;
    } else if (ev.type === "done") {
      // 1通目確定。以降の待ち時間（裏の感情・advisor抽出）でカーソルを点滅させない
      cursor.remove();
      // 非ストリーム時の一括表示と、ストリーム途中失敗→復帰文言の置き換えを兼ねる
      if (ev.reply && ev.reply !== streamed.trim()) body.textContent = ev.reply;
      if (ev.session_id && ev.session_id !== currentSessionId) {
        currentSessionId = ev.session_id; // 別れの挨拶でセッションが切り替わった
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
      const head = document.createElement("div");
      head.className = "d-head";
      const date = document.createElement("div");
      date.className = "d-date";
      date.textContent = fmtDate(d.created_at);
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-delete";
      del.title = "この日記を削除";
      del.textContent = "×";
      del.onclick = () => deleteDiary(d.id, fmtDate(d.created_at));
      head.appendChild(date);
      head.appendChild(del);
      const content = document.createElement("div");
      content.className = "d-body";
      content.textContent = d.content;
      card.appendChild(head);
      card.appendChild(content);
      bodyEl.appendChild(card);
    }
  } catch (e) {
    bodyEl.innerHTML = '<div class="album-empty">アルバムを読み込めませんでした。</div>';
  }
}

async function deleteDiary(id, label) {
  if (id == null) {
    window.alert("この日記は削除できません（id不明）");
    return;
  }
  const msg = `この日記（${label || id}）と、その材料になった本番蒸留の記憶も削除します。\n原典の記憶は消しません。変更ログ以外は残りません。よろしいですか？`;
  if (!window.confirm(msg)) return;
  try {
    const res = await fetch(`/api/album/${encodeURIComponent(id)}?confirm=true`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status}`);
    }
    await openAlbum();
  } catch (e) {
    window.alert(`削除できませんでした: ${e.message || e}`);
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

/* ---------- 評価レポート ---------- */

let evalPanelOpen = false;

function setEvalBadge(needsAttention) {
  const badge = $("eval-badge");
  if (!badge) return;
  if (needsAttention) badge.classList.remove("hidden");
  else badge.classList.add("hidden");
}

function renderEvalReport(payload) {
  const body = $("eval-panel-body");
  const copyEl = $("eval-copy-text");
  if (!body) return;
  body.innerHTML = "";
  const report = payload && payload.report;
  if (!report) {
    body.innerHTML = "<div>まだ週次評価レポートがありません。日曜起動後、または <code>python tools/run_weekly_eval.py --force --no-wait</code> で生成されます。</div>";
    if (copyEl) copyEl.value = "";
    return;
  }
  const head = document.createElement("div");
  head.textContent = `実行: ${report.ran_at || ""} ／ fail=${report.fail_count || 0} skipped=${report.skipped_count || 0}`;
  body.appendChild(head);
  for (const m of report.metrics || []) {
    const row = document.createElement("div");
    row.className = "eval-row";
    const st = document.createElement("div");
    st.className = `eval-status ${m.status || ""}`;
    st.textContent = m.status || "";
    const detail = document.createElement("div");
    detail.textContent = `${m.name}: ${m.detail || ""}`;
    row.appendChild(st);
    row.appendChild(detail);
    body.appendChild(row);
  }
  if (copyEl) copyEl.value = payload.claude_copy || "";
}

async function refreshEvalBadge() {
  try {
    const data = await getJSON("/api/eval/report");
    setEvalBadge(!!data.needs_attention);
    return data;
  } catch (e) {
    setEvalBadge(false);
    return null;
  }
}

async function openEvalPanel() {
  evalPanelOpen = true;
  $("eval-panel").classList.remove("hidden");
  $("messages").classList.add("hidden");
  $("composer").classList.add("hidden");
  $("readonly-bar").classList.add("hidden");
  $("btn-eval").classList.add("active");
  $("btn-current").classList.remove("active");
  const data = await refreshEvalBadge();
  renderEvalReport(data || { report: null });
}

function closeEvalPanel() {
  evalPanelOpen = false;
  $("eval-panel").classList.add("hidden");
  $("messages").classList.remove("hidden");
  $("composer").classList.remove("hidden");
  $("btn-eval").classList.remove("active");
}

async function ackEvalReport() {
  try {
    await fetch("/api/eval/ack", { method: "POST" });
    await refreshEvalBadge();
    addNotice("評価レポートを確認済みにしました");
  } catch (e) {
    addNotice("確認済みの保存に失敗しました");
  }
}

async function copyEvalText() {
  const text = ($("eval-copy-text") && $("eval-copy-text").value) || "";
  if (!text) {
    addNotice("コピーする内容がありません");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    addNotice("Claude用テキストをコピーしました");
  } catch (e) {
    $("eval-copy-text").select();
    addNotice("手動で選択してコピーしてください");
  }
}

$("btn-eval").onclick = openEvalPanel;
$("btn-eval-ack").onclick = ackEvalReport;
$("btn-eval-copy").onclick = copyEvalText;

/* ---------- Pulse（§2.8・チャット欄へ） ---------- */

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

async function mutePulse() {
  try {
    await fetch("/api/pulse/mute?mute=true", { method: "POST" });
    pulseMuted = true;
    const btn = $("btn-pulse-mute");
    if (btn) {
      btn.textContent = "Pulse停止中";
      btn.classList.add("active");
    }
    addNotice("Pulse をしばらく止めました（再起動で解除）");
  } catch (e) {
    addNotice("Pulse の mute に失敗しました");
  }
}

$("btn-pulse-mute").onclick = mutePulse;

/* ---------- 起動 ---------- */

(async function init() {
  try {
    await loadState();
    await loadCurrent();
    await loadSessions();
    await refreshEvalBadge();
    await pollPulse();
    setInterval(() => {
      pollPulse().catch(() => {});
    }, PULSE_POLL_MS);
  } catch (e) {
    addNotice("サーバに接続できませんでした。Serina.bat から起動してください。");
  }
  inputEl.focus();
})();
