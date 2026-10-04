// Holoの部屋のタブで、郵便局からの一言を入力欄に入れて送る。拡張が画面に触るのはここだけ。
// Master の下書きがあるとき・返事の最中は送らない（郵便局が次の見直しでまた試す）。

(() => {
const composerSelector = '#prompt-textarea,[contenteditable="true"][data-virtualkeyboard="true"]';
const sendSelector = 'button[data-testid="send-button"],button[data-testid="composer-submit-button"],button#composer-submit-button';
const stopSelector = 'button[data-testid="stop-button"]';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const normalized = text => (text ?? "").replace(/[\s​﻿]+/g, " ").trim();

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

async function until(find, ms = 4000) {
  for (let waited = 0; waited < ms; waited += 100) {
    const found = find();
    if (found) return found;
    await sleep(100);
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

/** 「@接続名」と打って、名前が完全に一致する候補を1つだけ選ぶ。 */
async function chooseConnector(composer, name) {
  typeAtEnd(composer, `@${name}`);
  const candidates = await until(() => {
    const found = [...document.querySelectorAll("[data-mention-list-scroll-area] button[data-list-navigation-item]")]
      .filter(visible)
      .filter(button => normalized(button.querySelector("[data-menu-row-content] span")?.textContent) === name);
    return found.length ? found : undefined;
  });
  if (!candidates) return `接続「${name}」が候補に出ない`;
  if (candidates.length > 1) return `接続「${name}」の候補が複数ある`;
  candidates[0].click();
  await sleep(300);
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

async function say({ text, connector }) {
  const composer = first(composerSelector);
  if (!composer) return { ok: false, reason: "入力欄が見つからない" };
  if (first(stopSelector)) return { ok: false, reason: "返事の最中" };
  if (normalized(textOf(composer))) return { ok: false, reason: "Masterの下書きがある" };
  if (connector) {
    const problem = await chooseConnector(composer, connector);
    if (problem) {
      clear(composer);
      return { ok: false, reason: problem };
    }
    typeAtEnd(composer, `\n${text}`);
  } else {
    typeAtEnd(composer, text);
  }
  // 送信ボタンが見つかれば押し、なければ人と同じく Enter で送る。送れたかは、入力欄が空になったかで見る
  const button = await until(() => sendButton(composer), 1500);
  if (button) button.click();
  else composer.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
  if (await until(() => !normalized(textOf(composer)), 3000)) return { ok: true };
  clear(composer);
  return { ok: false, reason: `送れなかった（ボタン: ${describeButtons(composer)}）` };
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

// 差し込みが重なっても、受け取り手は1つだけにする（2回送らないため）
function listen(message, _sender, sendResponse) {
  if (message?.type !== "nirai-say") return;
  say(message).then(sendResponse, error => sendResponse({ ok: false, reason: String(error) }));
  return true;
}
if (globalThis.__niraiHoloListen) chrome.runtime.onMessage.removeListener(globalThis.__niraiHoloListen);
globalThis.__niraiHoloListen = listen;
chrome.runtime.onMessage.addListener(listen);
})();
