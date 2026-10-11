const $ = id => document.getElementById(id);
const POST = "http://127.0.0.1:47800/holo";

const labels = {
  idle: "待機",
  working: "作業中",
  queued: "順番待ち",
  waiting: "返事待ち",
  stuck: "処理滞留・要確認",
  unreachable: "届かない・要確認",
  limited: "上限",
};

async function getStatus() {
  return fetch(`${POST}/status`).then(res => res.ok ? res.json() : undefined).catch(() => undefined);
}

/** ポップアップはタブに移ると閉じるので、タブを動かす操作は裏方に頼む。 */
const ask = message => chrome.runtime.sendMessage(message).catch(error => ({ ok: false, reason: String(error) }));

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function chars(value) {
  if (!Number.isFinite(value)) return "?";
  return value >= 10_000 ? `${(value / 10_000).toFixed(1)}万` : String(value);
}

function limitLabel(resident) {
  if (resident.state !== "limited") return labels[resident.state] ?? resident.state;
  if (!resident.limitUntil) return "上限";
  const until = new Date(resident.limitUntil);
  const now = new Date();
  const sameDay = until.toDateString() === now.toDateString();
  const when = new Intl.DateTimeFormat("ja-JP", sameDay
    ? { hour: "2-digit", minute: "2-digit" }
    : { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(until);
  return resident.limitKnown === false ? `上限・${when}再試行` : `上限で ${when} まで`;
}

function roomState(seat) {
  if (seat.state === "empty") return "空き";
  const doing = seat.awake ? "返事中" : seat.masterWaiting ? "Master待ち" : seat.waiting.length ? "返事待ち" : "";
  return [seat.work ?? "入ったばかり", doing, `${chars(seat.chars)}／${chars(seat.limit)}字`].filter(Boolean).join("・");
}

/** 部屋は設定の数だけいつも並べる。Room k はその部屋へ行く（空いていれば開く）、Exit はその場で空ける。 */
function showRooms(status) {
  const box = $("rooms");
  box.replaceChildren();
  box.className = "";
  for (const seat of status.seats) {
    const row = el("div", undefined, "room");
    const go = el("button", `Room ${seat.seat}`, "go");
    go.addEventListener("click", async () => {
      const result = await ask({ type: "nirai-room", seat: seat.seat });
      if (!result?.ok) $("message").textContent = result?.reason ?? "部屋へ行けなかった";
    });
    const exit = el("button", "Exit", "exit");
    exit.disabled = seat.state === "empty";
    exit.addEventListener("click", async () => {
      const res = await fetch(`${POST}/exit`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ seat: seat.seat }),
      }).catch(() => undefined);
      $("message").textContent = res?.ok ? `Room ${seat.seat}を空けた` : "空けられなかった";
      await show();
    });
    row.append(go, el("span", roomState(seat), "state muted"), exit);
    box.append(row);
    if (seat.problem) box.append(el("div", seat.problem, "problem"));
  }
}

function showTeam(status) {
  const team = $("team");
  team.replaceChildren();
  team.className = "";
  for (const resident of status.residents) {
    const row = el("div", undefined, "row");
    const count = resident.unfinished ? `・未済${resident.unfinished}` : "";
    row.append(el("span", resident.name), el("span", `${limitLabel(resident)}${count}`, "right"));
    team.append(row);
  }
}

async function show() {
  const status = await getStatus();
  if (!status?.residents || !status?.seats) {
    $("post").textContent = "郵便局: 止まっている";
    $("rooms").textContent = "";
    $("team").textContent = "";
    return;
  }
  $("post").textContent = status.reload ? "郵便局: 新しい版へ入れ替わるのを待っている" : "郵便局: つながっている";
  showRooms(status);
  showTeam(status);
}

$("usage").addEventListener("click", () => chrome.tabs.create({ url: "http://127.0.0.1:47800/usage" }));

void show();
