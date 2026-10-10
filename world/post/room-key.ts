// MCPの手に刻まれる部屋名と、手紙で使う筋の鍵を同じ形にそろえる。
import { workKey } from "./letters.ts";

export function roomKey(room: string): string {
  return room === "受付" ? "" : workKey(room);
}
