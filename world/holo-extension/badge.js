/** 郵便局の状態から、拡張アイコンに出す印を決める。×：郵便局を見られない、!：要確認、?：Masterへの未回答の問いがある。 */
export function badgeText(status) {
  const residents = status?.residents;
  const seats = status?.seats;
  if (!Array.isArray(residents) || !residents.every(resident =>
    Number.isInteger(resident?.stuck) && Number.isInteger(resident?.unreachable))) return "×";
  if (!Array.isArray(seats) || !seats.every(seat => Number.isInteger(seat?.seat))) return "×";
  if (!Number.isInteger(status.masterAsks) || status.masterAsks < 0) return "×";
  if (residents.some(resident => resident.stuck > 0 || resident.unreachable > 0) || seats.some(seat => seat.problem)) return "!";
  return status.masterAsks > 0 ? "?" : "";
}
