import { open, realpath } from 'node:fs/promises';
import { extname, isAbsolute } from 'node:path';

export const MAX_AVATAR_BYTES = 96 * 1024 * 1024;

// Models are self-contained GLBs. Remote URLs and sidecar file reads are never
// delegated to the renderer. The same boundary applies to saved and new files.
export function validateAvatar(bytes) {
  if (bytes.length < 20 || bytes.length > MAX_AVATAR_BYTES
    || bytes.readUInt32LE(0) !== 0x46546c67 || bytes.readUInt32LE(4) !== 2
    || bytes.readUInt32LE(8) !== bytes.length || bytes.readUInt32LE(16) !== 0x4e4f534a) {
    throw new Error('対応するVRMファイルではありません。');
  }
  const jsonLength = bytes.readUInt32LE(12);
  if (jsonLength > 8 * 1024 * 1024 || jsonLength + 20 > bytes.length) throw new Error('VRMの構造が不正です。');
  const document = JSON.parse(bytes.toString('utf8', 20, 20 + jsonLength));
  if (!document.extensions?.VRMC_vrm && !document.extensions?.VRM) throw new Error('VRM情報が含まれていません。');
  for (const item of [...(document.buffers ?? []), ...(document.images ?? [])]) {
    if (item.uri !== undefined && (typeof item.uri !== 'string' || !item.uri.startsWith('data:'))) {
      throw new Error('外部ファイルを参照するVRMには対応していません。');
    }
  }
  return document;
}

export async function readAvatar(path) {
  if (typeof path !== 'string' || !isAbsolute(path) || path.startsWith('\\\\')
    || extname(path).toLowerCase() !== '.vrm') throw new Error('ローカルのVRMファイルを選択してください。');
  const resolved = await realpath(path);
  if (resolved.startsWith('\\\\')) throw new Error('ネットワーク上のVRMには対応していません。');
  const file = await open(resolved, 'r');
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > MAX_AVATAR_BYTES) throw new Error('VRMは96 MB以下のファイルを選択してください。');
    // A bounded read also protects against a file growing after stat().
    const bytes = Buffer.alloc(stat.size);
    let offset = 0;
    while (offset < bytes.length) {
      const { bytesRead } = await file.read(bytes, offset, bytes.length - offset, offset);
      if (!bytesRead) throw new Error('VRMの読み込み中にファイルが変更されました。');
      offset += bytesRead;
    }
    validateAvatar(bytes);
    return { path: resolved, bytes };
  } finally {
    await file.close();
  }
}
