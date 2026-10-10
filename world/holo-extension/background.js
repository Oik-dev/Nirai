// Holoの席の拡張（裏方）。
// 席と会話のURLの正本はHoloの生ログ（郵便局）。拡張が覚えるのは「席の居場所（since）→タブ番号」だけ。
// 次の一言を取りに行くのは、alarmと、郵便局が「席の返事が終わった」と答えたときだけ。

import "./chat-url.js";
import { badgeText } from "./badge.js";
import { shouldReloadExtension } from "./version.js";

const POST = "http://127.0.0.1:47800/holo";
const filter = { urls: ["https://chatgpt.com/backend-api/*"] };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const chatUrl = globalThis.NiraiChatUrl;

// --- 席のタブ（seat → { tabId, since, created, url }） ---

async function seatTabs() {
  const { seatTabs } = await chrome.storage.local.get("seatTabs");
  return seatTabs && typeof seatTabs === "object" ? seatTabs : {};
}

async function saveSeatTabs(tabs) {
  await chrome.storage.local.set({ seatTabs: tabs });
}

async function seatOfTab(tabId) {
  const found = Object.entries(await seatTabs()).find(([, entry]) => entry.tabId === tabId);
  return found ? { seat: Number(found[0]), ...found[1] } : undefined;
}

/** 1つのタブは1つの席だけ。 */
async function rememberSeatTab(seat, entry) {
  const tabs = await seatTabs();
  for (const [key, other] of Object.entries(tabs)) if (other.tabId === entry.tabId) delete tabs[key];
  tabs[seat] = entry;
  await saveSeatTabs(tabs);
}

async function forgetTab(tabId) {
  const tabs = await seatTabs();
  let changed = false;
  for (const [key, entry] of Object.entries(tabs)) if (entry.tabId === tabId) { delete tabs[key]; changed = true; }
  if (changed) await saveSeatTabs(tabs);
}

// --- 郵便局 ---

async function post(action, body) {
  try {
    return await fetch(`${POST}/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
  } catch {
    // 郵便局が止まっているときは何もしない。生ログから次の見直しでやり直せる。
  }
}

async function getStatus() {
  return fetch(`${POST}/status`).then(res => res.ok ? res.json() : undefined).catch(() => undefined);
}

// --- 返事の通信 ---

function watch(phase) {
  return async details => {
    if (details.method !== "POST" || details.tabId < 0) return;
    const entry = await seatOfTab(details.tabId);
    if (!entry) return;
    // 席の会話から離れたタブ（Masterがほかの会話を開いた）の通信は、席の返事ではない
    if (entry.url) {
      const tab = await chrome.tabs.get(details.tabId).catch(() => undefined);
      if (!tab || !chatUrl.sameConversation(tab.url, entry.url)) return;
    }
    const path = new URL(details.url).pathname;
    const res = await post("net", {
      phase, id: details.requestId, method: details.method, path,
      status: details.statusCode, error: details.error, seat: entry.seat, since: entry.since,
    });
    if (res?.status === 200) void poll();
  };
}
chrome.webRequest.onBeforeRequest.addListener(watch("start"), filter);
chrome.webRequest.onCompleted.addListener(watch("end"), filter);
chrome.webRequest.onErrorOccurred.addListener(watch("error"), filter);

// 新しい会話は、最初の一言を送った後にURLが /g/<project>/c/<id> へ変わる。そのURLを席に結ぶ
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (!change.url) return;
  void (async () => {
    const entry = await seatOfTab(tabId);
    if (!entry || entry.url || !chatUrl.parse(change.url)?.projectId) return;
    await post("url", { seat: entry.seat, since: entry.since, url: change.url });
  })();
});
chrome.tabs.onRemoved.addListener(tabId => { void forgetTab(tabId); });

// --- タブ ---

async function existingTab(tabId) {
  return Number.isInteger(tabId) ? chrome.tabs.get(tabId).catch(() => undefined) : undefined;
}

async function findConversationTab(url) {
  return (await chrome.tabs.query({ url: "https://chatgpt.com/*" })).find(tab => chatUrl.sameConversation(tab.url, url));
}

async function waitLoaded(tabId) {
  for (let i = 0; i < 60; i++) {
    const tab = await existingTab(tabId);
    if (!tab) return false;
    if (tab.status === "complete") return true;
    await sleep(500);
  }
  return false;
}

/** Masterの邪魔をしない場所にタブを作る（窓がなければ最小化窓）。 */
async function createHidden(url) {
  const windows = await chrome.windows.getAll({ populate: false });
  if (windows.length === 0) {
    const win = await chrome.windows.create({ url, focused: false, state: "minimized" });
    return win.tabs?.[0]?.id ?? (await chrome.tabs.query({ windowId: win.id }))[0]?.id;
  }
  return (await chrome.tabs.create({ url, active: false })).id;
}

/** Masterが見ているタブか。見ているタブは拡張が閉じない。 */
async function seenByMaster(tab) {
  if (!tab.active) return false;
  const win = await chrome.windows.get(tab.windowId).catch(() => undefined);
  return Boolean(win?.focused && win.state !== "minimized");
}

/** 郵便局の席に合わせて、覚えているタブを直す。閉じた席のタブは、拡張が作ってMasterが見ていないものだけ閉じる。 */
async function syncSeats(seats) {
  if (!Array.isArray(seats)) return;
  const tabs = await seatTabs();
  let changed = false;
  for (const [key, entry] of Object.entries(tabs)) {
    const seat = seats.find(s => s.seat === Number(key));
    const tab = await existingTab(entry.tabId);
    if (!tab) { delete tabs[key]; changed = true; continue; }
    if (seat?.since !== entry.since) {
      if (entry.created && !(await seenByMaster(tab))) await chrome.tabs.remove(tab.id).catch(() => {});
      delete tabs[key];
      changed = true;
      continue;
    }
    if (seat.url && entry.url !== seat.url) { entry.url = seat.url; changed = true; }
  }
  // 覚えていない席でも、その会話を開いているタブがあれば、その席のタブにする
  for (const seat of seats) {
    if (!seat.since || !seat.url || tabs[seat.seat]) continue;
    const tab = await findConversationTab(seat.url);
    if (!tab || Object.values(tabs).some(entry => entry.tabId === tab.id)) continue;
    tabs[seat.seat] = { tabId: tab.id, since: seat.since, url: seat.url };
    changed = true;
  }
  if (changed) await saveSeatTabs(tabs);
}

// --- 届ける ---

async function deliver(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["chat-url.js", "content.js"] });
    return chrome.tabs.sendMessage(tabId, message);
  }
}

/** 会話のある席：その会話のタブ（なければ作る）。 */
async function conversationTab(offer) {
  const known = (await seatTabs())[offer.seat];
  const remembered = known?.since === offer.since ? await existingTab(known.tabId) : undefined;
  if (remembered && chatUrl.sameConversation(remembered.url, offer.url)) return { tabId: remembered.id };
  const found = await findConversationTab(offer.url);
  const tabId = found?.id ?? await createHidden(offer.url);
  if (!Number.isInteger(tabId)) return { reason: "席のタブを作れない" };
  await rememberSeatTab(offer.seat, { tabId, since: offer.since, url: offer.url, created: !found });
  await waitLoaded(tabId);
  if (!found) await sleep(2000);
  return { tabId };
}

/** 会話のまだない席：Projectの入口のタブ。同じ居場所のタブが会話へ進んでいれば、送信は済んでいるので送らない。 */
async function entryTab(offer) {
  const projectId = chatUrl.projectIdFromEntry(offer.entry);
  if (!projectId) return { reason: "ProjectのURLが分からない" };
  const known = (await seatTabs())[offer.seat];
  const remembered = known?.since === offer.since ? await existingTab(known.tabId) : undefined;
  if (remembered) {
    if (chatUrl.isProjectEntry(remembered.url, projectId)) return { tabId: remembered.id };
    if (chatUrl.isProjectConversation(remembered.url, projectId)) {
      await post("url", { seat: offer.seat, since: offer.since, url: remembered.url });
      return { sent: true };
    }
  }
  const tabId = await createHidden(offer.entry);
  if (!Number.isInteger(tabId)) return { reason: "席のタブを作れない" };
  await rememberSeatTab(offer.seat, { tabId, since: offer.since, created: true });
  await waitLoaded(tabId);
  await sleep(2000);
  return { tabId };
}

async function deliverOffer(offer) {
  const prepared = offer.url ? await conversationTab(offer) : await entryTab(offer);
  if (prepared.sent) return;
  const report = { seat: offer.seat, since: offer.since, letters: offer.letters };
  if (prepared.reason) {
    await post("sent", { ...report, ok: false, reason: prepared.reason });
    return;
  }
  const result = await deliver(prepared.tabId, {
    type: "nirai-say", text: offer.text, connect: !offer.url, expectedUrl: offer.url ?? offer.entry,
  }).catch(error => ({ ok: false, reason: String(error) }));
  // 送れた事実はすぐ残す。新しい会話のURLは、タブのURLが変わったとき（onUpdated）に結ぶ
  await post("sent", { ...report, ok: Boolean(result?.ok), reason: result?.reason });
}

// --- 見回り ---

async function reloadIfExtensionChanged(status) {
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

let polling = false;
let again = false;
/** 同時に1つだけ。動いている間に頼まれたら、終わってから1回だけやり直す。 */
async function poll() {
  if (polling) { again = true; return; }
  polling = true;
  try {
    do {
      again = false;
      const status = await getStatus();
      await chrome.action.setBadgeText({ text: badgeText(status) }).catch(() => {});
      // 返事の途中で読み直すとnetを取りこぼす。必ず次の一言を取る前に判定する。
      if (await reloadIfExtensionChanged(status)) return;
      await syncSeats(status?.seats);
      const res = await post("next");
      if (res?.status === 200) await deliverOffer(await res.json());
    } while (again);
  } finally {
    polling = false;
  }
}

// --- Masterの操作（ポップアップは、タブに移ると閉じて止まるので、ここが代わりに動く） ---

/** 空いた席に入る：Projectの入口を前に開き、@Nirai と席の番号を入れておく（送るのはMaster）。 */
async function enter() {
  const res = await post("enter");
  if (res?.status !== 200) return { ok: false, reason: res ? "空いた席がない" : "郵便局が止まっている" };
  const { seat, since } = await res.json();
  const entry = (await getStatus())?.entry;
  if (!entry) return { ok: false, reason: "ProjectのURLが分からない" };
  const tab = await chrome.tabs.create({ url: entry, active: true });
  await rememberSeatTab(seat, { tabId: tab.id, since });
  await waitLoaded(tab.id);
  await sleep(1500);
  await deliver(tab.id, { type: "nirai-prefill", text: `[席${seat}] ` }).catch(() => {});
  return { ok: true, seat };
}

/** 席へ行く：席のタブを前に出す。なければ席の会話を開く。 */
async function go(seatNumber) {
  const status = await getStatus();
  const seat = status?.seats?.find(s => s.seat === seatNumber);
  if (!seat?.since) return { ok: false, reason: "その席は空いている" };
  const known = (await seatTabs())[seatNumber];
  const tab = known?.since === seat.since ? await existingTab(known.tabId) : undefined;
  if (tab) {
    await chrome.tabs.update(tab.id, { active: true });
    const win = await chrome.windows.get(tab.windowId);
    await chrome.windows.update(tab.windowId, win.state === "minimized" ? { state: "normal", focused: true } : { focused: true });
    return { ok: true };
  }
  if (!seat.url) return { ok: false, reason: "席の会話がまだない" };
  const created = await chrome.tabs.create({ url: seat.url, active: true });
  await rememberSeatTab(seatNumber, { tabId: created.id, since: seat.since, url: seat.url });
  return { ok: true };
}

// Chromeのウィンドウがなくても、ブラウザの背景処理が生きていればalarmから最小化窓を作って起こせる。
chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "poll") void poll(); });
chrome.runtime.onStartup.addListener(() => { void poll(); });
chrome.runtime.onInstalled.addListener(() => { void poll(); });
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const run = {
    "nirai-poll-now": () => poll().then(() => ({ ok: true })),
    "nirai-enter": enter,
    "nirai-go": () => go(message.seat),
  }[message?.type];
  if (!run) return;
  run().then(sendResponse, error => sendResponse({ ok: false, reason: String(error) }));
  return true;
});
