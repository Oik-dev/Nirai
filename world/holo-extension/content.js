// Holoの部屋のタブで、郵便局からの一言を入力欄に入れて送る。拡張が画面に触るのはここだけ。
// Masterの下書きがあるときは送らない。返事中かどうかの正本は郵便局の通信監視。

(() => {
const composerSelector = '#prompt-textarea,[contenteditable="true"][data-virtualkeyboard="true"]';
const sendSelector = 'button[data-testid="send-button"],button[data-testid="composer-submit-button"],button#composer-submit-button';
const stopSelector = 'button[data-testid="stop-button"]';
const trustedEvents = ["keydown", "pointerdown", "paste", "drop", "compositionstart", "popstate"];

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const normalized = text => (text ?? "").replace(/[\s​﻿]+/g, " ").trim();
const roomUrl = globalThis.NiraiRoomUrl;

function visible(element) {
  if (!(element instanceof HTMLElement) || !element.isConnected || element.matches(":disabled")) return false;
  for (let node = element; node; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (node.hidden || node.inert || node.getAttribute("aria-hidden") === "true" || style.display === "none" || style.visibility === "hidden") return false;
  }
  return element.getClientRects().length > 0;
}
const first = selector => [...document.querySelectorAll(selector)].find(visible);
const textOf = element => (element instanceof HTMLTextAreaElement ? element.value : element.innerText);

function expectedPage(raw) {
  const projectId = roomUrl.projectIdFromEntry(raw);
  if (projectId) return roomUrl.isProjectEntry(location.href, projectId);
  return roomUrl.sameConversation(location.href, raw);
}

function owner(composer, expectedUrl) {
  let lost = false;
  const lose = event => { if (event.isTrusted) lost = true; };
  for (const type of trustedEvents) window.addEventListener(type, lose, true);
  return {
    owns(checkPage = true) {
      if (lost) return false;
      if (composer && !composer.isConnected) return false;
      if (composer && !visible(composer)) return false;
      if (checkPage && expectedUrl && !expectedPage(expectedUrl)) return false;
      return true;
    },
    stop() {
      for (const type of trustedEvents) window.removeEventListener(type, lose, true);
    },
  };
}

async function guardedUntil(find, guard, ms = 4000, checkPage = true) {
  const deadline = Date.now() + ms;
  while (true) {
    if (!guard.owns(checkPage)) return { lost: true };
    const found = find();
    if (found) return { found };
    const remaining = deadline - Date.now();
    if (remaining <= 0) return {};
    await sleep(Math.min(100, remaining));
  }
}

async function until(find, ms = 4000) {
  const deadline = Date.now() + ms;
  while (true) {
    const found = find();
    if (found) return found;
    const remaining = deadline - Date.now();
    if (remaining <= 0) return;
    await sleep(Math.min(100, remaining));
  }
}

function typeAtEnd(composer, text) {
  composer.focus();
  const range = document.createRange();
  range.selectNodeContents(composer);
  range.collapse(false);
  const selection = getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand("insertText", false, text);
}

/** 送れなかったときは、自分が入れたものを消す（入力欄は空だったときだけ入れているので、Masterの下書きは消さない）。 */
function clear(composer) {
  composer.focus();
  const range = document.createRange();
  range.selectNodeContents(composer);
  const selection = getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand("delete", false);
}

function canMove() {
  const composer = first(composerSelector);
  if (!composer) return { ok: false, reason: "入力欄が見つからない" };
  if (first('[role="dialog"]')) return { ok: false, reason: "ダイアログが開いている" };
  if (normalized(textOf(composer))) return { ok: false, reason: "Masterの下書きがある" };
  return { ok: true };
}

function hasSaid(text) {
  const wanted = normalized(text);
  if (!wanted) return false;
  return [...document.querySelectorAll('[data-message-author-role="user"]')]
    .some(message => normalized(message.textContent).includes(wanted));
}

function composerFocused(composer) {
  return document.activeElement === composer || composer.contains(document.activeElement);
}

// @Nirai の候補には、接続のNirai（名前と説明がどちらも「Nirai」）と、同じ名前のProject（名前だけ）が出る。押すのは接続だけ。
// 候補が並ぶ欄の名前は画面の版や打った字で変わる（plugins・exact-matches）ので、欄でなく候補の字で見分ける。
// 押せたかは、入力欄に接続の印（app-mention-name）が付いたかで確かめる。
const mentionItemSelector = '[data-mention-section-id] [data-list-navigation-item]';
const appChipSelector = '[app-mention-name="nirai"]';
const itemTexts = item => [...item.querySelectorAll("span")]
  .filter(span => span.children.length === 0).map(span => normalized(span.textContent)).filter(Boolean);
const isNiraiApp = item => itemTexts(item).join("|") === "Nirai|Nirai";

function shownMentions() {
  return [...document.querySelectorAll("[data-mention-section-id]")]
    .map(section => `${section.dataset.mentionSectionId}:${[...section.querySelectorAll("[data-list-navigation-item]")].map(item => itemTexts(item).join("/")).join("|")}`)
    .join(", ") || "候補なし";
}

async function connectNirai(composer, guard, activity) {
  if (!guard.owns()) return { ok: false, reason: "Masterが画面を操作した", touched: activity.touched };
  activity.touched = true;
  typeAtEnd(composer, "@Nirai");
  const item = await guardedUntil(() => {
    const items = [...document.querySelectorAll(mentionItemSelector)].filter(found => visible(found) && isNiraiApp(found));
    return items.length === 1 ? items[0] : undefined;
  }, guard, 4000);
  if (item.lost) return { ok: false, reason: "@Nirai の候補待ち中にMasterが操作した", touched: true };
  if (!item.found) {
    const shown = shownMentions();
    if (guard.owns()) clear(composer);
    return { ok: false, reason: `接続の Nirai が候補に1つだけ出ない（${shown}）`, touched: true };
  }
  item.found.click();
  const chip = await guardedUntil(() => composer.querySelector(appChipSelector), guard, 2000);
  if (chip.lost) return { ok: false, reason: "@Nirai の選択中にMasterが操作した", touched: true };
  if (!chip.found) {
    if (guard.owns()) clear(composer);
    return { ok: false, reason: "Nirai を選んだが入力欄に接続の印が付かない", touched: true };
  }
  return { ok: true };
}

async function recover(marker, projectId) {
  if (!marker || !hasSaid(marker)) return undefined;
  const guard = owner(undefined, undefined);
  try {
    const room = await guardedUntil(() => {
      const parsed = roomUrl.parse(location.href);
      return parsed?.projectId === projectId && hasSaid(marker) ? parsed.url : undefined;
    }, guard, 15_000, false);
    if (room.lost) return { ok: false, reason: "部屋の確認中にMasterが操作した", touched: true };
    if (!room.found) return { ok: false, reason: "送信済みだが新しい部屋のURLをまだ確定できない", touched: true };
    const parsed = roomUrl.parse(location.href);
    return guard.owns(false) && parsed?.projectId === projectId && hasSaid(marker)
      ? { ok: true, url: parsed.url }
      : { ok: false, reason: "新しい部屋を確定できない", touched: true };
  } finally {
    guard.stop();
  }
}

// 既存の部屋は接続が続くのでそのまま送る。新しい部屋だけ、最初の一言の前に @Nirai を選ぶ。
async function say({ text, connect = false, expectedUrl, marker, projectId }) {
  const recovered = await recover(marker, projectId);
  if (recovered) return recovered;
  if (expectedUrl && !expectedPage(expectedUrl)) return { ok: false, reason: "このタブは届け先の部屋ではない", touched: false };
  const state = canMove();
  if (!state.ok) return state;
  const composer = first(composerSelector);
  const guard = owner(composer, expectedUrl);
  const activity = { touched: false };
  try {
    if (connect) {
      const connected = await connectNirai(composer, guard, activity);
      if (!connected.ok) return connected;
    } else {
      // 通常起床は隠れたタブでも動く。見つけた会話入力欄そのものだけを、拡張が明示的にfocusする。
      composer.focus();
    }
    if (!guard.owns() || !composerFocused(composer) || first('[role="dialog"]')) return { ok: false, reason: "本文入力前の入力欄を確かめられない", touched: activity.touched };
    activity.touched = true;
    typeAtEnd(composer, connect ? ` ${text}` : text);
    const button = await guardedUntil(() => sendButton(composer), guard, connect ? 20000 : 1500);
    if (button.lost) return { ok: false, reason: "送信待ち中にMasterが操作した", touched: true };
    if (!guard.owns() || !composerFocused(composer)) return { ok: false, reason: "送信直前の入力欄を確かめられない", touched: true };
    if (button.found) button.found.click();
    else composer.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    // 送信操作より後は、画面遷移で入力欄が作り直されても失敗扱いにしない。
    // ここから先はMasterの操作を見張らず、「入力が消えた」という送信の事実だけを見る。
    const emptied = await until(() => {
      if (composer.isConnected && !normalized(textOf(composer))) return true;
      const current = first(composerSelector);
      return current && !normalized(textOf(current)) ? true : undefined;
    }, 3000);
    if (!emptied) {
      return { ok: false, reason: `送れなかった（ボタン: ${describeButtons(composer)}）`, touched: true };
    }
    return { ok: true, touched: activity.touched };
  } finally {
    guard.stop();
  }
}

function sendButton(composer) {
  const scope = composer.closest("form") ?? document;
  return [...scope.querySelectorAll("button")].find(b => visible(b) && !b.matches(stopSelector)
    && (b.matches(sendSelector) || (b.type === "submit" && b.form?.contains(composer)) || /send|送信/i.test(b.getAttribute("aria-label") ?? "")));
}

/** 送れなかったときに、入力欄のまわりのボタンを短く書き出す（次に直すための手がかり） */
function describeButtons(composer) {
  const scope = composer.closest("form") ?? composer.parentElement?.parentElement?.parentElement ?? document;
  return [...scope.querySelectorAll("button")].slice(0, 12)
    .map(b => [b.dataset.testid, b.getAttribute("aria-label"), b.type, b.disabled ? "disabled" : ""].filter(Boolean).join("/"))
    .join(", ") || "なし";
}

function moveTo({ expectedUrl, url }) {
  const state = canMove();
  if (!state.ok) return state;
  const composer = first(composerSelector);
  const guard = owner(composer, expectedUrl);
  try {
    if (!guard.owns()) return { ok: false, reason: "部屋を切り替える前にMasterが操作した" };
    location.assign(url);
    return { ok: true };
  } finally {
    guard.stop();
  }
}

if (!globalThis.__niraiHoloOps) globalThis.__niraiHoloOps = new Map();
function once(key, run) {
  if (!key) return run();
  if (globalThis.__niraiHoloOps.has(key)) return globalThis.__niraiHoloOps.get(key);
  const pending = Promise.resolve().then(run).finally(() => globalThis.__niraiHoloOps.delete(key));
  globalThis.__niraiHoloOps.set(key, pending);
  return pending;
}

// 差し込みが重なっても、受け取り手は1つだけにする（2回送らないため）
function listen(message, _sender, sendResponse) {
  if (message?.type === "nirai-move") {
    sendResponse(moveTo(message));
    return;
  }
  if (message?.type === "nirai-recover") {
    recover(message.marker, message.projectId).then(result => sendResponse(result ?? { ok: false }), error => sendResponse({ ok: false, reason: String(error) }));
    return true;
  }
  if (message?.type !== "nirai-say") return;
  once(message.marker, () => say(message)).then(sendResponse, error => sendResponse({ ok: false, reason: String(error) }));
  return true;
}
if (globalThis.__niraiHoloListen) chrome.runtime.onMessage.removeListener(globalThis.__niraiHoloListen);
globalThis.__niraiHoloListen = listen;
chrome.runtime.onMessage.addListener(listen);
})();
