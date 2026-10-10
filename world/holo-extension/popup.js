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

function seatState(seat) {
  if (seat.closing) return "閉じ中";
  if (seat.awake) return "返事中";
  if (seat.masterWaiting) return "Master待ち";
  if (seat.waiting.length) return "返事待ち";
  return "";
}

function showSeats(status) {
  const box = $("seats");
  box.replaceChildren();
  const taken = status.seats.filter(seat => seat.state !== "empty");
  $("enter").disabled = taken.length === status.seats.length;
  if (!taken.length) {
    box.className = "muted";
    box.textContent = "席は全部空いている";
    return;
  }
  box.className = "";
  for (const seat of taken) {
    const row = el("div", undefined, "seat");
    const head = el("div", undefined, "seat-head");
    head.append(
      el("span", `席${seat.seat}・${seat.work ?? "入ったばかり"}`),
      el("span", [seatState(seat), `${chars(seat.chars)}／${chars(seat.limit)}字`].filter(Boolean).join("・"), "right muted"),
    );
    row.append(head);
    if (seat.problem) row.append(el("div", seat.problem, "problem"));
    const buttons = el("div", undefined, "seat-buttons");
    const go = el("button", "行く");
    go.addEventListener("click", async () => {
      const result = await ask({ type: "nirai-go", seat: seat.seat });
      if (!result?.ok) $("message").textContent = result?.reason ?? "行けなかった";
    });
    const leave = el("button", "退室");
    leave.disabled = seat.closing;
    leave.addEventListener("click", async () => {
      const res = await fetch(`${POST}/leave`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ seat: seat.seat }),
      }).catch(() => undefined);
      const result = res?.ok ? await res.json() : undefined;
      $("message").textContent = !result ? "退室できなかった"
        : result.closed ? `席${seat.seat}を閉じた`
        : `席${seat.seat}のHoloに、引き継ぎを書いて閉じるよう頼んだ`;
      void ask({ type: "nirai-poll-now" });
      await show();
    });
    buttons.append(go, leave);
    row.append(buttons);
    box.append(row);
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
    $("seats").textContent = "";
    $("team").textContent = "";
    $("enter").disabled = true;
    return;
  }
  $("post").textContent = status.reload ? "郵便局: 新しい版へ入れ替わるのを待っている" : "郵便局: つながっている";
  showSeats(status);
  showTeam(status);
}

$("enter").addEventListener("click", async () => {
  $("enter").disabled = true;
  const result = await ask({ type: "nirai-enter" });
  $("message").textContent = result?.ok ? `席${result.seat}に入った` : result?.reason ?? "入れなかった";
  await show();
});
$("pollNow").addEventListener("click", async () => {
  await ask({ type: "nirai-poll-now" });
  await show();
});
$("usage").addEventListener("click", () => chrome.tabs.create({ url: "http://127.0.0.1:47800/usage" }));

void show();
