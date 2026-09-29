import assert from "node:assert/strict";
import test from "node:test";
import { encodeFrame, FrameReader } from "../src/shared/framing.js";
import { DEFAULT_SETTINGS } from "../src/shared/settings.js";

test("control frames survive every split point, including the header and multibyte text", () => {
  const value = { id: "request", text: "衣装を変更 🪽", values: [null, true, 42] };
  const frame = encodeFrame(value);
  for (let split = 0; split <= frame.length; split++) {
    const reader = new FrameReader();
    const values = [...reader.push(frame.subarray(0, split)), ...reader.push(frame.subarray(split))];
    assert.deepEqual(values, [value], `split at byte ${split}`);
  }
  const reader = new FrameReader();
  const values = Array.from(frame, byte => reader.push(Buffer.from([byte]))).flat();
  assert.deepEqual(values, [value]);
  assert.deepEqual(reader.push(Buffer.alloc(0)), []);
});

test("control frames preserve order across joined frames and an incomplete following frame", () => {
  const values = [{ id: "authentication" }, { id: "command", text: "日本語" }, null, [1, 2]];
  const frames = values.map(encodeFrame);
  const joined = Buffer.concat(frames);
  assert.deepEqual(new FrameReader().push(joined), values);
  for (const extra of [1, 3, 4, 7]) {
    const reader = new FrameReader();
    const split = frames[0]!.length + extra;
    assert.deepEqual(reader.push(joined.subarray(0, split)), [values[0]]);
    assert.deepEqual(reader.push(joined.subarray(split)), values.slice(1));
  }
});

test("control frame byte limit accepts the boundary and rejects invalid declared lengths before receiving a body", () => {
  const limit = DEFAULT_SETTINGS.control_message_bytes;
  const value = "x".repeat(limit - 2); // JSON string quotes occupy the final two bytes.
  const frame = encodeFrame(value);
  assert.equal(frame.length, limit + 4);
  const reader = new FrameReader();
  const values: unknown[] = [];
  for (let offset = 0; offset < frame.length; offset += 31) {
    values.push(...reader.push(frame.subarray(offset, offset + 31)));
  }
  assert.deepEqual(values, [value]);
  assert.throws(() => encodeFrame(`${value}x`), /control message exceeds/);
  for (const size of [0, limit + 1, 0xffffffff]) {
    const header = Buffer.alloc(4);
    header.writeUInt32BE(size);
    const invalid = new FrameReader();
    assert.deepEqual(invalid.push(header.subarray(0, 3)), []);
    assert.throws(() => invalid.push(header.subarray(3)), /invalid control frame length/);
  }
});

test("control frames with malformed JSON fail only when the declared body is complete", () => {
  const body = Buffer.from('{"id":');
  const header = Buffer.alloc(4);
  header.writeUInt32BE(body.length);
  const reader = new FrameReader();
  assert.deepEqual(reader.push(Buffer.concat([header, body.subarray(0, -1)])), []);
  assert.throws(() => reader.push(body.subarray(-1)), SyntaxError);
  assert.throws(() => new FrameReader().push(Buffer.concat([encodeFrame({ id: "valid" }), header, body])), SyntaxError);
});
