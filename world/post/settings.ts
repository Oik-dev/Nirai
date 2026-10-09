// 郵便局の設定の正本。

import { fileURLToPath } from "node:url";

const sourceRepoRoot = process.env.NIRAI_SOURCE_REPO ?? fileURLToPath(new URL("../../", import.meta.url));

const holo = {
  /** 返事の通信の知らせが途切れても、これを過ぎたら止まったとみなす（ChatGPTは25分で切れる） */
  busyLimitMs: 30 * 60_000,
  /** Masterが話した後、同じ部屋に郵便を届けずにおく時間。版の入れ替えには影響しない。 */
  masterTurnMs: 10 * 60_000,
  /** 返事の通信の道（2026-10-04のB0で確かめた） */
  replyPath: /^\/backend-api\/(f\/)?conversation(?:\/resume)?$/,
  /** 最後のroom行より後に、手がHoloへ返したoutputがこの字数を超えたら引っ越す。Masterの体感で、10万字では早すぎた（2026-10-09）。 */
  roomChars: 300_000,
  /** Holoが暮らすChatGPT Project。部屋そのもののURLは生ログのroom行が正本。 */
  projectId: "g-p-6ac239a30bc0819186c12150b8208fe0-nirai",
};
const codex = {
  /** レビュー担当。Masterの決めごと（2026-10-05）：6.1 Sol / Ultra */
  model: "gpt-6.1-sol",
  effort: "ultra",
  /** これを過ぎても終わらなければ止める。続きは次に起きてから（HoloがChatGPTで25分で切れるのと同じ扱い） */
  limitMs: 50 * 60_000,
};
const claude = {
  /** Claudeの家は、候補ではなく本物のNiraiリポジトリ。 */
  home: sourceRepoRoot,
  /** 設計担当。考える分が使える量の約3割を占めるので、max から xhigh へ下げて試す。質が目に見えて落ちたら max に戻す（2026-10-09、Master） */
  model: "claude-opus-5-5",
  effort: "xhigh",
  /** 会話がこの長さを超えたら要約する。読み直しの重さは長さに比例するので、既定の約97万まで育てない（2026-10-07の試算で約1割減） */
  autoCompact: "200k",
  /** Codexと同じ */
  limitMs: 50 * 60_000,
};

export const settings = {
  /** 本物のNiraiリポジトリ。候補の置き場所からは決めない。 */
  repoRoot: sourceRepoRoot,
  /** 試しの郵便局は、本番とぶつからないように別のポート（NIRAI_PORT）で動かす */
  port: Number(process.env.NIRAI_PORT ?? 47800),
  residentsRoot: process.env.NIRAI_RESIDENTS ?? "D:\\Products\\Residents",
  workRoot: process.env.NIRAI_WORK ?? "D:\\Products\\Work",
  /** 郵便受けを持つ住人（イデアのフォルダー名）。Serinaへの手紙は段階5で。 */
  team: ["Holo", "Codex", "Claude"],
  /** 作業場ごとの同時起床上限。段1のHoloは従来どおり1部屋。 */
  maxConcurrent: { Holo: 1, Codex: 2, Claude: 2 },
  /** 止まった直後・起こした直後に待つ時間 */
  restMs: 60_000,
  /** 全体を見直す間隔（新しい手紙や、止まった知らせのときは、待たずにすぐ見直す） */
  sweepMs: Number(process.env.NIRAI_SWEEP_MS ?? 60_000),
  /** 同じ手紙でこの回数起こしても済まなければ、Holoに頼んでMasterに知らせる */
  tellMasterAfter: 3,
  /** 脳の上限から起きる時刻を読めないときの再試行。代わりを頼むか待つかの境目も同じ1時間。 */
  limitWaitMs: 60 * 60_000,
  /** 作業場は、最後のdoneから最長の1回の目覚めぶん残す。新しい時間は増やさない。 */
  workKeepMs: Math.max(codex.limitMs, claude.limitMs, holo.busyLimitMs),
  holo,
  /** 郵便局が手を貸す住人（hands.ts）。脳が手元のファイルにもコマンドにも届かない住人 */
  hands: {
    for: ["Holo"],
    /** ChatGPTは道具の呼び出しを約60秒で打ち切るので、それより前に返す。終わらないコマンドの結果は手紙で届ける */
    waitMs: 45_000,
    /** これを過ぎても終わらないコマンドは止める（Codexと同じ） */
    limitMs: 50 * 60_000,
  },
  codex,
  claude,
};

/** 宛先の名前を、イデアのフォルダー名にそろえる。チームにいなければ undefined。 */
export function resolveResident(name: string): string | undefined {
  const key = name.trim().toLowerCase();
  return settings.team.find(r => r.toLowerCase() === key);
}
