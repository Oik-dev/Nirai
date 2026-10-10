/** 郵便局の状態から、拡張アイコンに出す印を決める。 */
export function badgeText(status) {
  const residents = status?.residents;
  if (!Array.isArray(residents) || !residents.every(resident =>
    Number.isInteger(resident?.stuck) && Number.isInteger(resident?.unreachable))) return "×";
  if (status?.room?.failure) return "!";
  if (status?.room?.state === "unregistered") return "?";
  return residents.some(resident => resident.stuck > 0 || resident.unreachable > 0) ? "!" : "";
}
