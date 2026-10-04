// 郵便局の設定の正本。

import { fileURLToPath } from "node:url";

export const settings = {
  /** 試しの郵便局は、本番とぶつからないように別のポート（NIRAI_PORT）で動かす */
  port: Number(process.env.NIRAI_PORT ?? 47800),
  residentsRoot: process.env.NIRAI_RESIDENTS ?? "D:\\Products\\Residents",
  workRoot: process.env.NIRAI_WORK ?? "D:\\Products\\Work",
  /** 郵便受けを持つ住人（イデアのフォルダー名）。Serinaへの手紙は段階5で。 */
  team: ["Holo", "Codex", "Claude"],
  /** 止まった直後・起こした直後に待つ時間 */
  restMs: 60_000,
  /** 全体を見直す間隔（新しい手紙や、止まった知らせのときは、待たずにすぐ見直す） */
  sweepMs: 60_000,
  /** 同じ手紙でこの回数起こしても済まなければ、Holoに頼んでMasterに知らせる */
  tellMasterAfter: 3,
  holo: {
    /** 返事の通信の知らせが途切れても、これを過ぎたら止まったとみなす（ChatGPTは25分で切れる） */
    busyLimitMs: 30 * 60_000,
    /** 返事の通信の道（2026-10-04のB0で確かめた） */
    replyPath: /^\/backend-api\/(f\/)?conversation$/,
  },
  /** 郵便局が手を貸す住人（hands.ts）。脳が手元のファイルにもコマンドにも届かない住人 */
  hands: {
    for: ["Holo"],
    /** ChatGPTは道具の呼び出しを約60秒で打ち切るので、それより前に返す。終わらないコマンドの結果は手紙で届ける */
    waitMs: 45_000,
    /** これを過ぎても終わらないコマンドは止める（Codexと同じ） */
    limitMs: 50 * 60_000,
  },
  codex: {
    /** レビュー担当。Masterの決めごと（2026-10-05）：6.1 Sol / Ultra */
    model: "gpt-6.1-sol",
    effort: "ultra",
    /** これを過ぎても終わらなければ止める。続きは次に起きてから（HoloがChatGPTで25分で切れるのと同じ扱い） */
    limitMs: 50 * 60_000,
  },
  claude: {
    /** Claudeの家。この郵便局が入っているNiraiのリポジトリ */
    home: fileURLToPath(new URL("../../", import.meta.url)),
    /** 設計担当。Claude Codeには ultra がないため、Opus 5.5 の最高値 max を使う（2026-10-05、Master） */
    model: "claude-opus-5-5",
    effort: "max",
    /** Codexと同じ */
    limitMs: 50 * 60_000,
  },
};

/** 宛先の名前を、イデアのフォルダー名にそろえる。チームにいなければ undefined。 */
export function resolveResident(name: string): string | undefined {
  const key = name.trim().toLowerCase();
  return settings.team.find(r => r.toLowerCase() === key);
}
