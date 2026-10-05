const $ = id => document.getElementById(id);

const labels = {
  idle: "待機",
  working: "作業中",
  waiting: "起こし待ち",
  stuck: "Master判断待ち",
  limited: "上限",
};

function limitLabel(resident) {
  if (resident.state !== "limited") return labels[resident.state] ?? resident.state;
  if (!resident.limitUntil) return "上限";
  const until = new Date(resident.limitUntil);
  const now = new Date();
  const sameDay = until.getFullYear() === now.getFullYear() && until.getMonth() === now.getMonth() && until.getDate() === now.getDate();
  const when = new Intl.DateTimeFormat("ja-JP", sameDay
    ? { hour: "2-digit", minute: "2-digit" }
    : { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(until);
  return resident.limitKnown === false ? `上限・${when}再試行` : `上限で ${when} まで`;
}

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
    state.textContent = `${limitLabel(resident)}${count}`;
    row.append(name, state);
    team.append(row);
  }
}

function chars(value) {
  if (!Number.isFinite(value)) return "?";
  return value >= 10_000 ? `${(value / 10_000).toFixed(1)}万` : String(value);
}

function showRoom(status) {
  const room = status?.room;
  if (!room) {
    $("room").textContent = "部屋の状態を見られません";
    return;
  }
  const length = `${chars(room.chars)}／${chars(room.limit)}字`;
  const moving = room.moving ? "・引っ越し中" : "";
  $("room").textContent = room.url ? `部屋: ${room.url}\n長さ: ${length}${moving}` : `部屋: 次の起床で作成\n長さ: ${length}`;
  $("move").disabled = !room.url || room.moving;
}

async function show() {
  const statusRes = await fetch("http://127.0.0.1:47800/holo/status").catch(() => undefined);
  const status = statusRes?.ok ? await statusRes.json() : undefined;
  const holo = status?.residents?.find(resident => resident.name === "Holo");
  $("post").textContent = !statusRes
    ? "郵便局: 止まっている"
    : holo?.unfinished ? "郵便局: Holoに届ける手紙がある"
    : "郵便局: つながっている（届ける手紙はない）";
  showTeam(status);
  showRoom(status);
}

$("move").addEventListener("click", async () => {
  await fetch("http://127.0.0.1:47800/holo/move", { method: "POST" }).catch(() => undefined);
  await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
  await show();
});
$("pollNow").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
  await show();
});

void show();
