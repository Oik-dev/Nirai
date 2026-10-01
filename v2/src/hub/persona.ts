import { createHash } from "node:crypto";
import { open, realpath } from "node:fs/promises";
import { isAbsolute } from "node:path";
import { HubError } from "../shared/errors.js";

export const PERSONA_MAX_BYTES = 64 * 1024;

export function validatePersonaPath(path: string): void {
  if (!isAbsolute(path) || /^[\\/]{2}/.test(path) || path.includes("\0")) {
    throw new HubError("invalid", "Personaはローカルファイルを選択してください。");
  }
}

/** Persona files remain authoritative; their contents are never persisted in Hub settings. */
export async function readPersona(path: string): Promise<{ path: string; text: string; fingerprint: string }> {
  validatePersonaPath(path);
  let file;
  try {
    const resolved = await realpath(path);
    validatePersonaPath(resolved);
    file = await open(resolved, "r");
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > PERSONA_MAX_BYTES) {
      throw new HubError("invalid", "Personaは64 KiB以内のテキストファイルにしてください。");
    }
    const buffer = Buffer.alloc(PERSONA_MAX_BYTES + 1);
    let length = 0;
    while (length < buffer.length) {
      const read = await file.read(buffer, length, buffer.length - length, null);
      if (!read.bytesRead) break;
      length += read.bytesRead;
    }
    if (length > PERSONA_MAX_BYTES) throw new HubError("invalid", "Personaのサイズが上限を超えました。");
    const bytes = buffer.subarray(0, length);
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (!text.trim() || text.includes("\0")) throw new HubError("invalid", "PersonaにはUTF-8の文章を指定してください。");
    return { path: resolved, text, fingerprint: createHash("sha256").update(bytes).digest("hex") };
  } catch (error) {
    if (error instanceof HubError) throw error;
    throw new HubError("unavailable", "Personaファイルを読み込めません。UTF-8のファイルを選び直してください。");
  } finally {
    await file?.close();
  }
}
