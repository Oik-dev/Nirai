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
  const action = $("roomAction");
  if (!room) {
    $("room").textContent = "部屋の状態を見られません";
    action.disabled = true;
    return;
  }
  const length = `${chars(room.chars)}／${chars(room.limit)}字`;
  if (room.state === "unregistered") {
    $("room").textContent = `部屋: 未登録\n長さ: ${length}`;
    action.textContent = "このタブを部屋にする";
    action.disabled = false;
    return;
  }
  if (room.state === "moving") {
    $("room").textContent = `部屋: ${room.url}\n長さ: ${length}・引っ越し中`;
    action.textContent = "引っ越し中";
    action.disabled = true;
    return;
  }
  if (room.state === "new-room") {
    $("room").textContent = `旧部屋: ${room.url}\n新しい部屋を待っています`;
    action.textContent = "このタブを新しい部屋にする";
    action.disabled = false;
    return;
  }
  $("room").textContent = `部屋: ${room.url}\n長さ: ${length}`;
  action.textContent = "今すぐ引っ越す";
  action.disabled = false;
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
  $("roomError").textContent = status?.room?.failure ? `自動引っ越し停止: ${status.room.failure}` : "";
}

$("roomAction").addEventListener("click", async () => {
  const statusRes = await fetch("http://127.0.0.1:47800/holo/status").catch(() => undefined);
  const status = statusRes?.ok ? await statusRes.json() : undefined;
  const state = status?.room?.state;
  if (!state || state === "moving") return;
  if (state === "unregistered" || state === "new-room") {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const registerRes = await fetch("http://127.0.0.1:47800/holo/room", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: tab?.url }),
    }).catch(() => undefined);
    const registered = registerRes?.ok ? await registerRes.json() : undefined;
    if (!registered?.registered) {
      $("roomError").textContent = "登録できません。Niraiプロジェクト内の会話タブで押してください";
      return;
    }
    await fetch("http://127.0.0.1:47800/holo/retry", { method: "POST" }).catch(() => undefined);
    await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
    await show();
    return;
  }
  await fetch("http://127.0.0.1:47800/holo/move", { method: "POST" }).catch(() => undefined);
  await fetch("http://127.0.0.1:47800/holo/retry", { method: "POST" }).catch(() => undefined);
  await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
  await show();
});
$("pollNow").addEventListener("click", async () => {
  await fetch("http://127.0.0.1:47800/holo/retry", { method: "POST" }).catch(() => undefined);
  await chrome.runtime.sendMessage({ type: "nirai-poll-now" });
  await show();
});

$("usage").addEventListener("click", () => chrome.tabs.create({ url: "http://127.0.0.1:47800/usage" }));

void show();
