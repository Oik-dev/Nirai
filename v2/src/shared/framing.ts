import { DEFAULT_SETTINGS } from "./settings.js";

export function encodeFrame(value: unknown): Buffer {
  const payload = Buffer.from(JSON.stringify(value));
  if (payload.length > DEFAULT_SETTINGS.control_message_bytes) throw new Error("control message exceeds 1 MiB");
  const frame = Buffer.allocUnsafe(payload.length + 4);
  frame.writeUInt32BE(payload.length); payload.copy(frame, 4);
  return frame;
}

export class FrameReader {
  private pending: Buffer = Buffer.alloc(0);
  push(chunk: Buffer): unknown[] {
    const values: unknown[] = [];
    this.pending = Buffer.concat([this.pending, chunk]);
    while (this.pending.length >= 4) {
      const size = this.pending.readUInt32BE();
      if (size === 0 || size > DEFAULT_SETTINGS.control_message_bytes) throw new Error("invalid control frame length");
      if (this.pending.length < size + 4) break;
      values.push(JSON.parse(this.pending.subarray(4, size + 4).toString("utf8")));
      this.pending = this.pending.subarray(size + 4);
    }
    return values;
  }
}
