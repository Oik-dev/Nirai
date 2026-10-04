const $ = id => document.getElementById(id);

async function show() {
  const res = await fetch("http://127.0.0.1:47800/holo/next").catch(() => undefined);
  $("post").textContent = !res ? "郵便局: 止まっている" : res.status === 200 ? "郵便局: Holoに届ける手紙がある" : "郵便局: つながっている（届ける手紙はない）";
  const { room } = await chrome.storage.local.get("room");
  $("room").textContent = room ? `部屋: ${room.url}` : "部屋: まだ決まっていない（専用の会話を開いて、下のボタンを押す）";
  $("useConnector").checked = Boolean(room?.connector);
  if (room?.connector) $("connector").value = room.connector;
}

async function saveConnector() {
  const { room } = await chrome.storage.local.get("room");
  if (!room) return;
  room.connector = $("useConnector").checked ? $("connector").value.trim() : "";
  await chrome.storage.local.set({ room });
}

$("make").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const convId = /\/c\/([0-9a-f-]+)/.exec(new URL(tab.url).pathname)?.[1];
  if (!convId) {
    $("room").textContent = "ChatGPTの会話（URLに /c/ がある）を開いてから押してください。";
    return;
  }
  await chrome.storage.local.set({ room: { convId, url: tab.url, tabId: tab.id, connector: "" } });
  await saveConnector();
  await show();
});
$("useConnector").addEventListener("change", saveConnector);
$("connector").addEventListener("change", saveConnector);
$("pollNow").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
  await show();
});

void show();
