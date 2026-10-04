// 郵便局の設定の正本。

export const settings = {
  port: 47800,
  residentsRoot: process.env.NIRAI_RESIDENTS ?? "D:\\Products\\Residents",
  workRoot: process.env.NIRAI_WORK ?? "D:\\Products\\Work",
  /** 郵便受けを持つ住人（イデアのフォルダー名）。Serinaへの手紙は段階5で。 */
  team: ["Holo", "Codex", "Claude"],
};

/** 宛先の名前を、イデアのフォルダー名にそろえる。チームにいなければ undefined。 */
export function resolveResident(name: string): string | undefined {
  const key = name.trim().toLowerCase();
  return settings.team.find(r => r.toLowerCase() === key);
}
