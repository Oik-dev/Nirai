// 郵便局の設定の正本。

export const settings = {
  port: 47800,
  residentsRoot: process.env.NIRAI_RESIDENTS ?? "D:\\Products\\Residents",
  workRoot: process.env.NIRAI_WORK ?? "D:\\Products\\Work",
  /** 郵便受けを持つ住人（イデアのフォルダー名）。Serinaへの手紙は段階5で。 */
  team: ["Holo", "Codex", "Claude"],
  /** 止まった直後・起こした直後に待つ時間 */
  restMs: 60_000,
  /** 全体を見直す間隔（新しい手紙や、止まった知らせのときは、待たずにすぐ見直す） */
  sweepMs: 60_000,
  /** 同じ手紙でこの回数起こしても済まなければ、Holoに頼んでMasterに知らせる */
  tellMasterAfter: 5,
  holo: {
    /** 返事の通信の知らせが途切れても、これを過ぎたら止まったとみなす（ChatGPTは25分で切れる） */
    busyLimitMs: 30 * 60_000,
    /** 返事の通信の道（2026-10-04のB0で確かめた） */
    replyPath: /^\/backend-api\/(f\/)?conversation$/,
  },
  codex: {
    /** 動作確認は Luna の Low で済んだので（2026-10-04、B1の出口）、Astra の Ultra（Master の決めごと） */
    model: "gpt-6-astra",
    effort: "ultra",
    /** これを過ぎても終わらなければ止める。続きは次に起きてから（HoloがChatGPTで25分で切れるのと同じ扱い） */
    limitMs: 50 * 60_000,
  },
};

/** 宛先の名前を、イデアのフォルダー名にそろえる。チームにいなければ undefined。 */
export function resolveResident(name: string): string | undefined {
  const key = name.trim().toLowerCase();
  return settings.team.find(r => r.toLowerCase() === key);
}
