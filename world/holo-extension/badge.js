/** 郵便局の状態から、拡張アイコンに出す印を決める。×：郵便局を見られない、!：要確認、?：Masterの返事を待つ席がある。 */
export function badgeText(status) {
  const residents = status?.residents;
  const seats = status?.seats;
  if (!Array.isArray(residents) || !residents.every(resident =>
    Number.isInteger(resident?.stuck) && Number.isInteger(resident?.unreachable))) return "×";
  if (!Array.isArray(seats) || !seats.every(seat => Number.isInteger(seat?.seat))) return "×";
  if (residents.some(resident => resident.stuck > 0 || resident.unreachable > 0) || seats.some(seat => seat.problem)) return "!";
  return seats.some(seat => seat.masterWaiting) ? "?" : "";
}
