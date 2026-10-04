// 郵便局の設定の正本。

export const settings = {
  port: 47800,
  residentsRoot: process.env.NIRAI_RESIDENTS ?? "D:\\Products\\Residents",
  workRoot: process.env.NIRAI_WORK ?? "D:\\Products\\Work",
  /** 郵便受けを持つ住人（イデアのフォルダー名）。Serinaへの手紙は段階5で。 */
  team: ["Holo", "Codex", "Claude"],
  /** 止まった直後・起こした直後に待つ時間 */
  restMs: 60_000,
  holo: {
    /** 返事の通信の知らせが途切れても、これを過ぎたら止まったとみなす（ChatGPTは25分で切れる） */
    busyLimitMs: 30 * 60_000,
    /** 返事の通信の道（仮。B0で実際の通信を見て決める） */
    replyPath: /^\/backend-api\/(f\/)?conversation$/,
  },
};

/** 宛先の名前を、イデアのフォルダー名にそろえる。チームにいなければ undefined。 */
export function resolveResident(name: string): string | undefined {
  const key = name.trim().toLowerCase();
  return settings.team.find(r => r.toLowerCase() === key);
}
