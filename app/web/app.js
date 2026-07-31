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

// core/state/serina_day.py の SERINA_DAY_HOUR と同じ値。
const SERINA_DAY_BOUNDARY_HOUR = 7;

// 日記(episodic)の表示日は metadata.target_date（またはlegacyのdate）を優先する
// （2026-07-26恒久解）。タグが無い既存行だけ、created_atがSerina日界の瞬間ちょうど
// （hour===7かつ分秒0）なら1日前へ戻す暫定ヒューリスティックを使う。
function fmtMemoryDate(m) {
  if (m && m.type === "episodic") {
    const meta = m.metadata || {};
    const tagged = meta.target_date || meta.date;
    if (typeof tagged === "string" && /^\d{4}-\d{2}-\d{2}/.test(tagged)) {
      const [y, mo, d] = tagged.slice(0, 10).split("-").map(Number);
      return `${y}/${mo}/${d}`;
    }
    if (m.created_at) {
      const d = new Date(m.created_at);
      if (
        d.getHours() === SERINA_DAY_BOUNDARY_HOUR &&
        d.getMinutes() === 0 &&
        d.getSeconds() === 0 &&
        d.getMilliseconds() === 0
      ) {
        const shifted = new Date(d.getTime() - 24 * 60 * 60 * 1000);
        return fmtDate(shifted.toISOString());
      }
    }
  }
  return fmtDate(m && m.created_at);
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
  const msg = "この発言を削除します。関連する未消化宿題や蒸留記憶に影響する場合があります。よろしいですか？";
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
    closeEvalPanel();
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
  // 棚上げ・人格直し待ち（原則1: 無言破棄禁止のGUI側表示）
  const notice = $("shelved-notice");
  const bits = [];
  if (st.shelved > 0) bits.push(`処理できなかった宿題${st.shelved}件`);
  if (st.pending_persona_revise > 0) bits.push(`人格直し待ち${st.pending_persona_revise}件`);
  if (bits.length) {
    notice.textContent = bits.join("・");
    notice.classList.remove("hidden");
  } else {
    notice.classList.add("hidden");
  }
}

let activeViewId = null; // 今メインに表示している会話（現在の会話 or 過去セッションid）

async function loadCurrent() {
  viewingPast = false;
  closeEvalPanel();
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

/* ---------- 確認モーダル・トースト ---------- */

function openConfirm({ title, body, okLabel, onOk }) {
  $("confirm-title").textContent = title;
  $("confirm-body").textContent = body;
  $("confirm-ok").textContent = okLabel || "削除する";
  const overlay = $("confirm-overlay");
  overlay.classList.remove("hidden");
  const okBtn = $("confirm-ok");
  const cancelBtn = $("confirm-cancel");
  const cleanup = () => {
    overlay.classList.add("hidden");
    okBtn.onclick = null;
    cancelBtn.onclick = null;
  };
  okBtn.onclick = () => { cleanup(); onOk(); };
  cancelBtn.onclick = cleanup;
}

let toastTimer = null;
function showToast(msg) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2400);
}

/* ---------- 記憶メンテ（検索・種別・ソート・編集・削除） ---------- */

const MEMORY_PAGE_SIZE = 100;
const GRADE_LABEL = { S: "S", A: "A", B: "B" };
let memoryPage = 1;
let memoryQuery = "";
let memoryType = "";
let memorySort = "date";
let memoryDir = "desc";
let memoryPages = 0;
let memoryItems = [];
let editingId = null;
let materialsOpenId = null;
let materialsCache = {}; // memory_id -> array（開いている日記の参照記憶）

function truncateContent(text, n) {
  const s = text || "";
  return s.length <= n ? s : s.slice(0, n) + "…";
}

async function openMemoryMaint(resetPage) {
  if (resetPage) memoryPage = 1;
  const overlay = $("memory-overlay");
  overlay.classList.remove("hidden");
  $("memory-q").value = memoryQuery;
  $("memory-type").value = memoryType;
  await loadMemoryMaint();
}

function updateSortArrows() {
  for (const key of ["type", "grade", "content", "date"]) {
    const el = $("arrow-" + key);
    if (!el) continue;
    if (memorySort === key) {
      el.textContent = memoryDir === "asc" ? "▲" : "▼";
      el.classList.add("active");
    } else {
      el.textContent = "▲";
      el.classList.remove("active");
    }
  }
}

async function loadMemoryMaint() {
  updateSortArrows();
  const label = $("memory-page-label");
  editingId = null;
  materialsOpenId = null;
  try {
    const params = new URLSearchParams({
      limit: String(MEMORY_PAGE_SIZE),
      page: String(memoryPage),
      sort: memorySort,
      dir: memoryDir,
    });
    if (memoryQuery) params.set("q", memoryQuery);
    if (memoryType) params.set("type", memoryType);
    const data = await getJSON(`/api/memories?${params.toString()}`);
    memoryPages = data.pages || 0;
    memoryItems = data.items || [];
    const total = data.total || 0;
    label.textContent = total
      ? `${data.page} / ${memoryPages}（全${total}件）`
      : "0件";
    $("btn-memory-prev").disabled = memoryPage <= 1;
    $("btn-memory-next").disabled = memoryPages === 0 || memoryPage >= memoryPages;
    renderMemoryTable();
  } catch (e) {
    $("memory-tbody").innerHTML = '<tr><td colspan="5" class="memory-empty">記憶一覧を読み込めませんでした。</td></tr>';
    label.textContent = "";
    $("btn-memory-prev").disabled = true;
    $("btn-memory-next").disabled = true;
  }
}

function renderMemoryTable() {
  const tbody = $("memory-tbody");
  tbody.innerHTML = "";
  if (!memoryItems.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="memory-empty">該当する記憶がありません。</td></tr>';
    return;
  }
  for (const m of memoryItems) {
    if (editingId === m.id) {
      tbody.appendChild(buildEditRow(m));
      continue;
    }
    tbody.appendChild(buildViewRow(m));
    if (materialsOpenId === m.id) {
      tbody.appendChild(buildMaterialsRow(m));
    }
  }
}

function buildViewRow(m) {
  const tr = document.createElement("tr");
  tr.className = "memory-row";

  const tdType = document.createElement("td");
  const typeBadge = document.createElement("span");
  typeBadge.className = "type-badge type-badge-" + m.type;
  typeBadge.textContent = m.type;
  tdType.appendChild(typeBadge);

  const tdGrade = document.createElement("td");
  const badge = document.createElement("span");
  badge.className = "grade-badge grade-" + (m.protection_grade || "B").toLowerCase();
  badge.textContent = GRADE_LABEL[m.protection_grade] || m.protection_grade || "?";
  tdGrade.appendChild(badge);

  const tdContent = document.createElement("td");
  tdContent.className = "memory-content";
  const preview = document.createElement("div");
  preview.className = "memory-preview";
  preview.textContent = truncateContent(m.content, 34);
  const full = document.createElement("div");
  full.className = "memory-full hidden";
  full.textContent = m.content || "";
  tdContent.appendChild(preview);
  tdContent.appendChild(full);
  tdContent.onclick = () => {
    const open = !full.classList.contains("hidden");
    full.classList.toggle("hidden", open);
    preview.classList.toggle("hidden", !open);
  };

  const tdDate = document.createElement("td");
  tdDate.className = "memory-date";
  tdDate.textContent = fmtMemoryDate(m);

  const tdOps = document.createElement("td");
  if (m.pinned) {
    const lock = document.createElement("span");
    lock.className = "pin-lock";
    lock.title = "正典固定：GUIからの編集・削除は不可（同一性原則）";
    lock.textContent = "🔒 固定";
    tdOps.appendChild(lock);
  } else {
    const wrap = document.createElement("div");
    wrap.className = "row-ops";

    if (m.type === "episodic") {
      const matBtn = document.createElement("button");
      matBtn.type = "button";
      matBtn.className = "op-btn" + (materialsOpenId === m.id ? " op-active" : "");
      matBtn.textContent = "参照記憶";
      matBtn.onclick = () => toggleMaterials(m.id);
      wrap.appendChild(matBtn);
    }

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "op-btn";
    editBtn.textContent = "編集";
    editBtn.onclick = () => { editingId = m.id; renderMemoryTable(); };

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "op-btn op-delete";
    delBtn.textContent = "削除";
    delBtn.onclick = () => confirmDeleteMemory(m);

    wrap.appendChild(editBtn);
    wrap.appendChild(delBtn);
    tdOps.appendChild(wrap);
  }

  tr.appendChild(tdType);
  tr.appendChild(tdGrade);
  tr.appendChild(tdContent);
  tr.appendChild(tdDate);
  tr.appendChild(tdOps);
  return tr;
}

function buildEditRow(m) {
  const tr = document.createElement("tr");
  tr.className = "edit-row";
  const td = document.createElement("td");
  td.colSpan = 5;

  const form = document.createElement("div");
  form.className = "edit-form";

  const textarea = document.createElement("textarea");
  textarea.value = m.content;

  const row2 = document.createElement("div");
  row2.className = "edit-form-row";
  const label = document.createElement("label");
  label.textContent = "保護等級";
  const select = document.createElement("select");
  for (const g of ["S", "A", "B"]) {
    const opt = document.createElement("option");
    opt.value = g; opt.textContent = g;
    if (g === m.protection_grade) opt.selected = true;
    select.appendChild(opt);
  }
  const warn = document.createElement("span");
  warn.className = "edit-warn";
  warn.textContent = "S等級への変更・S等級の編集は保存時に確認が入ります";
  warn.style.display = (m.protection_grade === "S" || select.value === "S") ? "inline" : "none";
  select.onchange = () => { warn.style.display = select.value === "S" ? "inline" : "none"; };

  const actions = document.createElement("div");
  actions.className = "edit-form-actions";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button"; cancelBtn.className = "edit-cancel"; cancelBtn.textContent = "キャンセル";
  cancelBtn.onclick = () => { editingId = null; renderMemoryTable(); };
  const saveBtn = document.createElement("button");
  saveBtn.type = "button"; saveBtn.className = "edit-save"; saveBtn.textContent = "保存";
  saveBtn.onclick = () => trySaveMemory(m, textarea.value, select.value);

  actions.appendChild(cancelBtn);
  actions.appendChild(saveBtn);
  row2.appendChild(label);
  row2.appendChild(select);
  row2.appendChild(warn);
  row2.appendChild(actions);

  form.appendChild(textarea);
  form.appendChild(row2);
  td.appendChild(form);
  tr.appendChild(td);
  return tr;
}

async function saveMemoryEdit(m, newContent, newGrade) {
  try {
    const res = await fetch(`/api/memories/${encodeURIComponent(m.id)}?confirm=true`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: newContent, protection_grade: newGrade }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status}`);
    }
    editingId = null;
    await loadMemoryMaint();
    showToast("保存しました（変更前の内容は控えとして残っています）");
  } catch (e) {
    window.alert(`保存できませんでした: ${e.message || e}`);
  }
}

function trySaveMemory(m, newContent, newGrade) {
  const goingS = newGrade === "S";
  const wasS = m.protection_grade === "S";
  if (goingS || wasS) {
    openConfirm({
      title: "保護等級Sの記憶を編集します",
      body: "保護等級Sは正典級の扱いです。\n内容・等級の変更後もこのアプリ内には控え（変更前の内容）が残るので、後から見返せます。\nこのまま保存しますか？",
      okLabel: "保存する",
      onOk: () => saveMemoryEdit(m, newContent, newGrade),
    });
  } else {
    saveMemoryEdit(m, newContent, newGrade);
  }
}

function confirmDeleteMemory(m, onDone) {
  const gradeNote = m.protection_grade === "S" ? "\n保護等級Sのため、確認のうえ削除します。" : "";
  const chunkNote = m.type === "episodic"
    ? "\nこの日記と、同一内容の検索用チャンク（あれば）だけを消します。他の記憶は巻き込みません。"
    : "\nこの1件と、これに紐づく検索用データ（埋め込み・索引）だけを消します。他の記憶は巻き込みません。";
  openConfirm({
    title: "この記憶を削除します",
    body: `「${truncateContent(m.content, 28)}」を完全に削除します。${gradeNote}${chunkNote}\n変更ログ以外は残りません。よろしいですか？`,
    okLabel: "削除する",
    onOk: () => deleteMemory(m, onDone),
  });
}

async function deleteMemory(m, onDone) {
  try {
    const res = await fetch(`/api/memories/${encodeURIComponent(m.id)}?confirm=true`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `${res.status}`);
    }
    if (onDone) {
      onDone();
    } else {
      if (materialsOpenId === m.id) materialsOpenId = null;
      await loadMemoryMaint();
    }
    showToast("削除しました（関連データも一緒に消去済み）");
  } catch (e) {
    window.alert(`削除できませんでした: ${e.message || e}`);
  }
}

async function toggleMaterials(diaryId) {
  if (materialsOpenId === diaryId) {
    materialsOpenId = null;
    renderMemoryTable();
    return;
  }
  materialsOpenId = diaryId;
  renderMemoryTable(); // 先にパネルの枠だけ出す→読み込み中は空表示
  try {
    materialsCache[diaryId] = await getJSON(`/api/memories/${encodeURIComponent(diaryId)}/diary_material`);
  } catch (e) {
    materialsCache[diaryId] = [];
  }
  if (materialsOpenId === diaryId) renderMemoryTable();
}

function buildMaterialsRow(diary) {
  const tr = document.createElement("tr");
  tr.className = "materials-row";
  const td = document.createElement("td");
  td.colSpan = 5;

  const box = document.createElement("div");
  box.className = "materials-box";

  const head = document.createElement("div");
  head.className = "materials-head";
  head.innerHTML = `この日記「${truncateContent(diary.content, 24)}」が参照している記憶です。<b>自動では消しません。</b>ここで見て、必要なものだけ個別に削除してください。`;
  box.appendChild(head);

  const list = materialsCache[diary.id];
  if (list === undefined) {
    const loading = document.createElement("div");
    loading.className = "materials-empty";
    loading.textContent = "読み込み中…";
    box.appendChild(loading);
  } else if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "materials-empty";
    empty.textContent = "参照している記憶は見つかりませんでした。";
    box.appendChild(empty);
  } else {
    const table = document.createElement("table");
    table.className = "materials-table";
    for (const mat of list) {
      const row = document.createElement("tr");

      const tdB = document.createElement("td");
      const tbadge = document.createElement("span");
      tbadge.className = "type-badge type-badge-" + mat.type;
      tbadge.textContent = mat.type;
      tdB.appendChild(tbadge);

      const tdG = document.createElement("td");
      const gbadge = document.createElement("span");
      gbadge.className = "grade-badge grade-" + (mat.protection_grade || "B").toLowerCase();
      gbadge.textContent = mat.protection_grade;
      tdG.appendChild(gbadge);

      const tdC = document.createElement("td");
      tdC.textContent = truncateContent(mat.content, 60);

      const tdD = document.createElement("td");
      tdD.className = "memory-date";
      tdD.textContent = fmtDate(mat.created_at);

      const tdOp = document.createElement("td");
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.className = "op-btn op-delete";
      delBtn.textContent = "削除";
      delBtn.onclick = () => confirmDeleteMemory(mat, () => {
        materialsCache[diary.id] = (materialsCache[diary.id] || []).filter((x) => x.id !== mat.id);
        renderMemoryTable();
      });
      tdOp.appendChild(delBtn);

      row.appendChild(tdB);
      row.appendChild(tdG);
      row.appendChild(tdC);
      row.appendChild(tdD);
      row.appendChild(tdOp);
      table.appendChild(row);
    }
    box.appendChild(table);
  }

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "materials-close";
  closeBtn.textContent = "閉じる";
  closeBtn.onclick = () => { materialsOpenId = null; renderMemoryTable(); };
  box.appendChild(closeBtn);

  td.appendChild(box);
  tr.appendChild(td);
  return tr;
}

$("btn-memory-maint").onclick = () => openMemoryMaint(true);
$("btn-memory-close").onclick = () => $("memory-overlay").classList.add("hidden");
$("memory-overlay").addEventListener("click", (e) => {
  if (e.target.id === "memory-overlay") $("memory-overlay").classList.add("hidden");
});
$("btn-memory-search").onclick = () => {
  memoryQuery = ($("memory-q").value || "").trim();
  memoryPage = 1;
  loadMemoryMaint();
};
$("memory-q").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    $("btn-memory-search").click();
  }
});
$("memory-type").addEventListener("change", (e) => {
  memoryType = e.target.value;
  memoryPage = 1;
  loadMemoryMaint();
});
document.querySelectorAll("#memory-panel th.sortable").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (memorySort === key) {
      memoryDir = memoryDir === "asc" ? "desc" : "asc";
    } else {
      memorySort = key;
      memoryDir = "asc";
    }
    memoryPage = 1;
    loadMemoryMaint();
  });
});
$("btn-memory-prev").onclick = () => {
  if (memoryPage <= 1) return;
  memoryPage -= 1;
  loadMemoryMaint();
};
$("btn-memory-next").onclick = () => {
  if (memoryPages && memoryPage >= memoryPages) return;
  memoryPage += 1;
  loadMemoryMaint();
};

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
  highlightSession(null);
  const data = await refreshEvalBadge();
  renderEvalReport(data || { report: null });
}

function closeEvalPanel() {
  evalPanelOpen = false;
  $("eval-panel").classList.add("hidden");
  $("messages").classList.remove("hidden");
  $("composer").classList.remove("hidden");
  $("btn-eval").classList.remove("active");
  highlightSession(activeViewId);
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
    await refreshEvalBadge();
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
