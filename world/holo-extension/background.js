// Holoの部屋の拡張（裏方）。
// - 部屋のタブの通信の始まりと終わりを、郵便局に知らせる（道と状態だけ。中身は読まない）。返事をしているかは郵便局が決める
// - 郵便局へ起こす一言を取りに行き、あれば部屋のタブへ渡す。届いたかどうかを郵便局へ返す
// 画面に触るのは、一言を入れて送るとき（content.js）だけ。

const POST = "http://127.0.0.1:47800/holo";
const filter = { urls: ["https://chatgpt.com/backend-api/*"] };

/** 部屋 = { convId, url, tabId } */
async function room() {
  return (await chrome.storage.local.get("room")).room;
}

async function tell(action, body) {
  try {
    await fetch(`${POST}/${action}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    // 郵便局が止まっているときは何もしない。郵便局は毎回生ログから見直すので、取りこぼしは残らない
  }
}

function watch(phase) {
  return async details => {
    if (details.method !== "POST") return;
    const r = await room();
    if (!r || details.tabId !== r.tabId) return;
    const path = new URL(details.url).pathname;
    await tell("net", { phase, id: details.requestId, method: details.method, path, status: details.statusCode, error: details.error });
    if (phase !== "start") void poll();
  };
}
chrome.webRequest.onBeforeRequest.addListener(watch("start"), filter);
chrome.webRequest.onCompleted.addListener(watch("end"), filter);
chrome.webRequest.onErrorOccurred.addListener(watch("error"), filter);

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** 部屋のタブを見つける。閉じていたら、ピン留めの裏のタブで開き直す。 */
async function openRoom(r) {
  const alive = await chrome.tabs.get(r.tabId).catch(() => undefined);
  if (alive?.url?.includes(`/c/${r.convId}`)) return r.tabId;
  const found = (await chrome.tabs.query({ url: "https://chatgpt.com/*" })).find(tab => tab.url?.includes(`/c/${r.convId}`));
  const tab = found ?? (await chrome.tabs.create({ url: r.url, pinned: true, active: false }));
  for (let i = 0; i < 60 && (await chrome.tabs.get(tab.id)).status !== "complete"; i++) await sleep(500);
  if (!found) await sleep(3000); // 開いたばかりの画面が落ち着くまで
  await chrome.storage.local.set({ room: { ...r, tabId: tab.id } });
  return tab.id;
}

/** 部屋のタブへ一言を渡す。拡張を入れる前から開いていたタブや、拡張を入れ直したあとのタブには
 *  画面側（content.js）がいないので、そのときは差し込んでから渡す。 */
async function deliver(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return chrome.tabs.sendMessage(tabId, message);
  }
}

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const r = await room();
    if (!r) return;
    const res = await fetch(`${POST}/next`).catch(() => undefined);
    if (res?.status !== 200) return;
    const { text, letters } = await res.json();
    const tabId = await openRoom(r);
    const result = await deliver(tabId, { type: "nirai-say", text })
      .catch(error => ({ ok: false, reason: String(error) }));
    await tell("sent", { ok: Boolean(result?.ok), letters, reason: result?.reason });
  } finally {
    polling = false;
  }
}

// 30秒ごとにも見に行く（返事の終わりの知らせのほかに）
chrome.alarms.create("poll", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === "poll") void poll();
});
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "nirai-poll-now") void poll().then(() => sendResponse(true));
  return true;
});
