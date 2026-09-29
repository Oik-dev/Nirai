import { DEFAULT_SETTINGS } from "./settings.js";

export function encodeFrame(value: unknown): Buffer {
  const payload = Buffer.from(JSON.stringify(value));
  if (payload.length > DEFAULT_SETTINGS.control_message_bytes) throw new Error("control message exceeds 1 MiB");
  const frame = Buffer.allocUnsafe(payload.length + 4);
  frame.writeUInt32BE(payload.length); payload.copy(frame, 4);
  return frame;
}

export class FrameReader {
  private readonly header = Buffer.allocUnsafe(4);
  private headerBytes = 0;
  private payload: Buffer | null = null;
  private payloadBytes = 0;

  push(chunk: Buffer): unknown[] {
    const values: unknown[] = [];
    let offset = 0;
    while (offset < chunk.length) {
      if (this.payload === null) {
        const bytes = Math.min(4 - this.headerBytes, chunk.length - offset);
        chunk.copy(this.header, this.headerBytes, offset, offset + bytes);
        this.headerBytes += bytes;
        offset += bytes;
        if (this.headerBytes < 4) break;
        const size = this.header.readUInt32BE();
        if (size === 0 || size > DEFAULT_SETTINGS.control_message_bytes) throw new Error("invalid control frame length");
        // Allocate once per bounded frame; fragmented transport input must not
        // repeatedly copy the bytes already received.
        this.payload = Buffer.allocUnsafe(size);
        this.payloadBytes = 0;
      }
      const bytes = Math.min(this.payload.length - this.payloadBytes, chunk.length - offset);
      chunk.copy(this.payload, this.payloadBytes, offset, offset + bytes);
      this.payloadBytes += bytes;
      offset += bytes;
      if (this.payloadBytes === this.payload.length) {
        values.push(JSON.parse(this.payload.toString("utf8")));
        this.payload = null;
        this.headerBytes = 0;
      }
    }
    return values;
  }
}
