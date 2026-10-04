const $ = id => document.getElementById(id);

const labels = {
  idle: "待機",
  working: "作業中",
  waiting: "起こし待ち",
  stuck: "Master判断待ち",
};

function showTeam(status) {
  const team = $("team");
  team.replaceChildren();
  if (!status?.residents?.length) {
    team.textContent = "郵便受けの状態を見られません";
    return;
  }
  team.classList.remove("muted");
  for (const resident of status.residents) {
    const row = document.createElement("div");
    row.className = "resident";
    const name = document.createElement("span");
    name.textContent = resident.name;
    const state = document.createElement("span");
    state.className = "resident-state";
    const count = resident.unfinished ? `・未済${resident.unfinished}` : "";
    state.textContent = `${labels[resident.state] ?? resident.state}${count}`;
    row.append(name, state);
    team.append(row);
  }
}

async function show() {
  const [res, statusRes] = await Promise.all([
    fetch("http://127.0.0.1:47800/holo/next").catch(() => undefined),
    fetch("http://127.0.0.1:47800/holo/status").catch(() => undefined),
  ]);
  $("post").textContent = !res ? "郵便局: 止まっている" : res.status === 200 ? "郵便局: Holoに届ける手紙がある" : "郵便局: つながっている（届ける手紙はない）";
  showTeam(statusRes?.ok ? await statusRes.json() : undefined);
  const { room } = await chrome.storage.local.get("room");
  $("room").textContent = room ? `部屋: ${room.url}` : "部屋: まだ決まっていない（専用の会話を開いて、下のボタンを押す）";
}

$("make").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const convId = /\/c\/([0-9a-f-]+)/.exec(new URL(tab.url).pathname)?.[1];
  if (!convId) {
    $("room").textContent = "ChatGPTの会話（URLに /c/ がある）を開いてから押してください。";
    return;
  }
  await chrome.storage.local.set({ room: { convId, url: tab.url, tabId: tab.id } });
  await show();
});
$("pollNow").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
  await show();
});

void show();
