// 筋ごとのCLIログ・Claude指示書に使う、Windowsで安全な短いファイル鍵。
// 短い名前はUTF-8十六進のファイル名、長い名前だけ固定長のハッシュにする。
import { createHash } from "node:crypto";
import { trackKey } from "./letters.ts";

export function trackFileKey(work: string): string {
  const normalized = trackKey(work);
  const hex = Buffer.from(normalized, "utf8").toString("hex");
  return hex.length <= 160 ? hex : `sha256-${createHash("sha256").update(normalized, "utf8").digest("hex")}`;
}

/** 短い鍵は復元可能。ハッシュ鍵はpostの実際のwakeで確認できる筋だけへ帰属させる。
 *  作業場のなかったころのログ（reception）は、どの作業場にも帰属させない。 */
export function workFromTrackFileKey(key: string, knownWorks: Iterable<string>): string | undefined {
  if (key === "reception") return undefined;
  if (/^[0-9a-f]+$/.test(key) && key.length % 2 === 0) return Buffer.from(key, "hex").toString("utf8");
  if (/^sha256-[0-9a-f]{64}$/.test(key)) {
    for (const work of knownWorks) if (trackFileKey(work) === key) return trackKey(work);
  }
  return undefined;
}
