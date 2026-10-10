// Holoの部屋の拡張（裏方）。
// 部屋のURLの正本はHoloの生ログ。拡張が覚えるのは作業場→タブ番号だけ。

import "./room-url.js";
import { badgeText } from "./badge.js";
import { shouldReloadExtension } from "./version.js";

const POST = "http://127.0.0.1:47800/holo";
const filter = { urls: ["https://chatgpt.com/backend-api/*"] };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function roomTabs() {
  const { roomTabs } = await chrome.storage.local.get("roomTabs");
  return roomTabs && typeof roomTabs === "object" ? roomTabs : {};
}

async function roomTabId(work = "") {
  return (await roomTabs())[work.toLowerCase()];
}

async function workForTab(tabId) {
  const found = Object.entries(await roomTabs()).find(([, id]) => id === tabId);
  return found?.[0];
}

async function rememberRoomTab(tabId, work = "") {
  const tabs = await roomTabs();
  tabs[work.toLowerCase()] = tabId;
  await chrome.storage.local.set({ roomTabs: tabs });
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
    const work = await workForTab(details.tabId);
    if (work === undefined) return;
    const path = new URL(details.url).pathname;
    await tell("net", { phase, id: details.requestId, method: details.method, path, status: details.statusCode, error: details.error, work });
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
    await chrome.scripting.executeScript({ target: { tabId }, files: ["room-url.js", "content.js"] });
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

async function waitProjectConversation(tabId, projectId) {
  for (let i = 0; i < 150; i++) {
    const tab = await chrome.tabs.get(tabId).catch(() => undefined);
    if (!tab) return undefined;
    const parsed = roomUrl.parse(tab.url);
    if (parsed?.projectId === projectId) return parsed.url;
    await sleep(100);
  }
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

const roomUrl = globalThis.NiraiRoomUrl;

async function existingTab(tabId) {
  return Number.isInteger(tabId) ? chrome.tabs.get(tabId).catch(() => undefined) : undefined;
}

async function findRoom(url) {
  const target = roomUrl.parse(url);
  if (!target) return undefined;
  return (await chrome.tabs.query({ url: "https://chatgpt.com/*" })).find(tab => roomUrl.sameConversation(tab.url, target.url));
}

/** 既存の部屋を開く。タブが消えていれば、郵便局から受け取ったURLで新しいタブ（または最小化窓）を作る。 */
async function openExisting(next) {
  const work = next.work ?? "";
  const remembered = await existingTab(await roomTabId(work));
  if (remembered && roomUrl.sameConversation(remembered.url, next.url)) return remembered.id;
  const found = await findRoom(next.url);
  const tabId = found?.id ?? await createHidden(next.url);
  if (!Number.isInteger(tabId)) throw new Error("部屋のタブを作れない");
  await rememberRoomTab(tabId, work);
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
  await rememberRoomTab(tab.id, next.work ?? "");
  await waitLoaded(tab.id);
  await sleep(2000);
  return { tabId: tab.id };
}

async function openNew(next) {
  const projectId = roomUrl.projectIdFromEntry(next.url);
  if (!projectId) return { reason: "新しい部屋のProjectを確かめられない" };
  const remembered = await existingTab(await roomTabId(next.work ?? ""));
  if (remembered) {
    if (roomUrl.sameConversation(remembered.url, next.currentRoomUrl)) return switchOldRoom(remembered, next);
    if (roomUrl.isProjectEntry(remembered.url, projectId)) return { tabId: remembered.id };
    if (roomUrl.isProjectConversation(remembered.url, projectId)) {
      const recovered = await deliver(remembered.id, { type: "nirai-recover", marker: next.roomMarker, projectId })
        .catch(() => undefined);
      if (recovered?.ok && recovered.url) return { tabId: remembered.id, alreadySentUrl: recovered.url };
      // markerが見えているなら送信そのものは済んでいる。URLを確定できなくても作り直さない。
      if (recovered?.touched) return { tabId: remembered.id, alreadySent: true, reason: recovered.reason ?? "送信済みの新しい部屋を確定できない" };
    }
  }

  const tabId = await createHidden(next.url);
  if (!Number.isInteger(tabId)) return { reason: "新しい部屋のタブを作れない" };
  await rememberRoomTab(tabId, next.work ?? "");
  await waitLoaded(tabId);
  await sleep(2000);
  return { tabId };
}

let polling = false;
async function closeFinished(works) {
  if (!Array.isArray(works)) return;
  const tabs = await roomTabs();
  let changed = false;
  for (const work of works) {
    if (typeof work !== "string" || !work || !Object.hasOwn(tabs, work)) continue;
    const tabId = tabs[work];
    if (Number.isInteger(tabId)) await chrome.tabs.remove(tabId).catch(() => {});
    delete tabs[work];
    changed = true;
  }
  if (changed) await chrome.storage.local.set({ roomTabs: tabs });
}

async function refreshBadge() {
  const status = await fetch(`${POST}/status`).then(res => res.json()).catch(() => undefined);
  await closeFinished(status?.closedWorks);
  await chrome.action.setBadgeText({ text: badgeText(status) }).catch(() => {});
}

async function reloadIfExtensionChanged() {
  const status = await fetch(`${POST}/status`).then(res => res.ok ? res.json() : undefined).catch(() => undefined);
  const current = status?.revision?.extension;
  if (typeof current !== "string" || !current) return false;
  const saved = (await chrome.storage.session.get("extensionVersion")).extensionVersion;
  if (shouldReloadExtension(saved, current)) {
    chrome.runtime.reload();
    return true;
  }
  if (saved === undefined) await chrome.storage.session.set({ extensionVersion: current });
  return false;
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    // 返事の途中で読み直すとnetを取りこぼす。必ず次の一言を取る前に判定する。
    if (await reloadIfExtensionChanged()) return;
    const res = await fetch(`${POST}/next`, { method: "POST" }).catch(() => undefined);
    if (res?.status !== 200) return;
    const next = await res.json();
    const work = next.work ?? "";

    if (next.createRoom) {
      const prepared = await openNew(next);
      if (prepared.alreadySentUrl) {
        await tell("sent", { ok: true, letters: next.letters, url: prepared.alreadySentUrl });
        return;
      }
      if (prepared.alreadySent) {
        await tell("sent", { ok: true, letters: next.letters, reason: prepared.reason });
        return;
      }
      if (prepared.reason) {
        if (prepared.touched) await tell("sent", { ok: false, letters: next.letters, reason: prepared.reason, touched: true });
        return;
      }
      const text = next.roomMarker ? `${next.text}\n${next.roomMarker}` : next.text;
      const projectId = roomUrl.projectIdFromEntry(next.url);
      const result = await deliver(prepared.tabId, {
        type: "nirai-say",
        text,
        connect: true,
        expectedUrl: next.url,
        marker: next.roomMarker,
        projectId,
      })
        .catch(error => ({ ok: false, reason: String(error) }));
      if (!result?.ok) {
        await tell("sent", { ok: false, letters: next.letters, reason: result?.reason, touched: Boolean(result?.touched) });
        return;
      }
      // 送れた事実を先に残す。ここから先でURL確認に失敗しても、同じ引っ越しで2部屋目は作らない。
      await tell("sent", { ok: true, letters: next.letters });
      const url = await waitProjectConversation(prepared.tabId, projectId);
      if (url) await tell("room", { url, work });
      return;
    }

    const tabId = await openExisting(next);
    const result = await deliver(tabId, { type: "nirai-say", text: next.text, expectedUrl: next.url })
      .catch(error => ({ ok: false, reason: String(error) }));
    await tell("sent", { ok: Boolean(result?.ok), letters: next.letters, reason: result?.reason });
  } finally {
    polling = false;
    void refreshBadge();
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
  if (message?.type === "nirai-poll-now") {
    void poll().then(() => sendResponse(true));
    return true;
  }
  return true;
});
