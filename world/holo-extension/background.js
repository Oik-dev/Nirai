// Holoの部屋の拡張（裏方）。
// 部屋のURLの正本はHoloの生ログ。拡張が覚えるのはタブ番号だけで、開くURLは毎回郵便局の /holo/next から受け取る。

import { badgeText } from "./badge.js";

const POST = "http://127.0.0.1:47800/holo";
const filter = { urls: ["https://chatgpt.com/backend-api/*"] };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

// 旧版のURL・convId入りstorageは、拡張が起きた時点で捨てる。
void chrome.storage.local.remove("room");

async function roomTabId() {
  return (await chrome.storage.local.get("roomTabId")).roomTabId;
}

async function rememberRoomTab(tabId) {
  await chrome.storage.local.set({ roomTabId: tabId });
  // 旧版のURL・convId入りstorageは残さない。
  await chrome.storage.local.remove("room");
}

async function tell(action, body) {
  try {
    return await fetch(`${POST}/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    // 郵便局が止まっているときは何もしない。生ログから次の見直しでやり直せる。
  }
}

function watch(phase) {
  return async details => {
    if (details.method !== "POST") return;
    const tabId = await roomTabId();
    if (details.tabId !== tabId) return;
    const path = new URL(details.url).pathname;
    await tell("net", { phase, id: details.requestId, method: details.method, path, status: details.statusCode, error: details.error });
    if (phase !== "start") void poll();
  };
}
chrome.webRequest.onBeforeRequest.addListener(watch("start"), filter);
chrome.webRequest.onCompleted.addListener(watch("end"), filter);
chrome.webRequest.onErrorOccurred.addListener(watch("error"), filter);

async function deliver(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return chrome.tabs.sendMessage(tabId, message);
  }
}

async function waitLoaded(tabId) {
  for (let i = 0; i < 60; i++) {
    const tab = await chrome.tabs.get(tabId).catch(() => undefined);
    if (!tab) return false;
    if (tab.status === "complete") return true;
    await sleep(500);
  }
  return false;
}

async function createHidden(url) {
  const windows = await chrome.windows.getAll({ populate: false });
  if (windows.length === 0) {
    const win = await chrome.windows.create({ url, focused: false, state: "minimized" });
    return win.tabs?.[0]?.id ?? (await chrome.tabs.query({ windowId: win.id }))[0]?.id;
  }
  const tab = await chrome.tabs.create({ url, active: false });
  return tab.id;
}

function conversationUrl(url) {
  try {
    const parsed = new URL(url);
    return parsed.origin === "https://chatgpt.com" && /^\/c\/[0-9a-f-]+\/?$/i.test(parsed.pathname)
      ? `${parsed.origin}${parsed.pathname.replace(/\/$/, "")}`
      : undefined;
  } catch {
    return undefined;
  }
}

async function existingTab(tabId) {
  return Number.isInteger(tabId) ? chrome.tabs.get(tabId).catch(() => undefined) : undefined;
}

async function findRoom(url) {
  const target = conversationUrl(url);
  if (!target) return undefined;
  return (await chrome.tabs.query({ url: "https://chatgpt.com/*" })).find(tab => conversationUrl(tab.url) === target);
}

/** 既存の部屋を開く。タブが消えていれば、郵便局から受け取ったURLで新しいタブ（または最小化窓）を作る。 */
async function openExisting(next) {
  const remembered = await existingTab(await roomTabId());
  if (remembered && conversationUrl(remembered.url) === conversationUrl(next.url)) return remembered.id;
  const found = await findRoom(next.url);
  const tabId = found?.id ?? await createHidden(next.url);
  if (!Number.isInteger(tabId)) throw new Error("部屋のタブを作れない");
  await rememberRoomTab(tabId);
  await waitLoaded(tabId);
  if (!found) await sleep(2000);
  return tabId;
}

/**
 * 新しい部屋を作るタブを用意する。
 * 覚えている旧部屋があるときは、Masterの下書きと返事中でないことを画面側で確かめてから同じタブを切り替える。
 * すでに /c/<id> へ変わっていれば、前回の送信は済んでURLの報告だけ失敗したものとして再送信しない。
 */
async function switchOldRoom(tab, next) {
  const safe = await deliver(tab.id, { type: "nirai-move", expectedUrl: next.currentRoomUrl, url: next.url })
    .catch(error => ({ ok: false, reason: String(error) }));
  if (!safe?.ok) return { reason: safe?.reason ?? "部屋を切り替えられない" };
  await rememberRoomTab(tab.id);
  await waitLoaded(tab.id);
  await sleep(2000);
  return { tabId: tab.id };
}

async function openNew(next) {
  const remembered = await existingTab(await roomTabId());
  if (remembered) {
    const currentConversation = conversationUrl(remembered.url);
    const oldConversation = conversationUrl(next.currentRoomUrl);
    if (currentConversation === oldConversation && oldConversation) return switchOldRoom(remembered, next);
    if (remembered.url?.startsWith("https://chatgpt.com/")) return { tabId: remembered.id };
  }

  const tabId = await createHidden(next.url);
  if (!Number.isInteger(tabId)) return { reason: "新しい部屋のタブを作れない" };
  await rememberRoomTab(tabId);
  await waitLoaded(tabId);
  await sleep(2000);
  return { tabId };
}

let polling = false;
async function refreshBadge() {
  const status = await fetch(`${POST}/status`).then(res => res.json()).catch(() => undefined);
  await chrome.action.setBadgeText({ text: badgeText(status) }).catch(() => {});
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const res = await fetch(`${POST}/next`).catch(() => undefined);
    if (res?.status !== 200) return;
    const next = await res.json();

    if (next.createRoom) {
      const prepared = await openNew(next);
      if (prepared.reason) return;
      const text = next.roomMarker ? `${next.text}\n${next.roomMarker}` : next.text;
      const result = await deliver(prepared.tabId, {
        type: "nirai-say",
        text,
        connect: true,
        expectedUrl: next.url,
        marker: next.roomMarker,
        waitForConversation: true,
      })
        .catch(error => ({ ok: false, reason: String(error) }));
      if (!result?.ok || !result.url) {
        await tell("sent", { ok: false, letters: next.letters, reason: result?.reason });
        return;
      }
      await tell("sent", { ok: true, letters: next.letters, url: result.url });
      return;
    }

    const tabId = await openExisting(next);
    const result = await deliver(tabId, { type: "nirai-say", text: next.text, expectedUrl: next.url })
      .catch(error => ({ ok: false, reason: String(error) }));
    await tell("sent", { ok: Boolean(result?.ok), letters: next.letters, reason: result?.reason });
  } finally {
    polling = false;
  }
}

// Chromeのウィンドウがなくても、ブラウザの背景処理が生きていればalarmから最小化窓を作って起こせる。
chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name !== "poll") return;
  void refreshBadge();
  void poll();
});
chrome.runtime.onStartup.addListener(() => { void refreshBadge(); void poll(); });
chrome.runtime.onInstalled.addListener(() => { void refreshBadge(); void poll(); });
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "nirai-poll-now") void poll().then(() => sendResponse(true));
  return true;
});
