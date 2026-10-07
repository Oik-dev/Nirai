// 体の置き場（イデアの body/）を読む入口。体（VRM）と覚えた動き（.vrma）は、どちらも自己完結したGLBだけを窓へ渡す。
import { open, realpath } from 'node:fs/promises';
import { isAbsolute, resolve, sep } from 'node:path';

export const MAX_AVATAR_BYTES = 96 * 1024 * 1024;
export const MAX_MOTION_BYTES = 16 * 1024 * 1024;
// 覚えた動きの名前は、そのままファイル名になる。区切りや予約文字、前後の空白、ドットで始まる名前は受け取らない。
const MOTION_NAME = /^(?![.\s])(?!.*\s$)[^\\/:*?"<>|\u0000-\u001f]{1,64}$/u;

function selfContainedGlb(bytes: Buffer, limit: number) {
  if (bytes.length < 20 || bytes.length > limit
    || bytes.readUInt32LE(0) !== 0x46546c67 || bytes.readUInt32LE(4) !== 2
    || bytes.readUInt32LE(8) !== bytes.length || bytes.readUInt32LE(16) !== 0x4e4f534a) {
    throw new Error('対応するGLBファイルではありません。');
  }
  const jsonLength = bytes.readUInt32LE(12);
  if (jsonLength > 8 * 1024 * 1024 || jsonLength + 20 > bytes.length) {
    throw new Error('GLBの構造が不正です。');
  }
  let document;
  try {
    document = JSON.parse(bytes.toString('utf8', 20, 20 + jsonLength));
  } catch {
    throw new Error('GLBの構造が不正です。');
  }
  for (const item of [...(document.buffers ?? []), ...(document.images ?? [])]) {
    if (item.uri !== undefined && (typeof item.uri !== 'string' || !item.uri.startsWith('data:'))) {
      throw new Error('外部ファイルを参照するGLBには対応していません。');
    }
  }
  return document;
}

export function validateAvatar(bytes: Buffer) {
  const document = selfContainedGlb(bytes, MAX_AVATAR_BYTES);
  if (!document.extensions?.VRMC_vrm && !document.extensions?.VRM) {
    throw new Error('VRM情報が含まれていません。');
  }
  return document;
}

export function validateMotion(bytes: Buffer) {
  const document = selfContainedGlb(bytes, MAX_MOTION_BYTES);
  if (!document.extensions?.VRMC_vrm_animation) {
    throw new Error('VRMアニメーション情報が含まれていません。');
  }
  return document;
}

async function readIdeaBodyFile(ideaRoot: string, parts: string[], limit: number, validate: (bytes: Buffer) => unknown) {
  if (!ideaRoot || !isAbsolute(ideaRoot)) throw new Error('ローカルのイデアを指定してください。');
  const root = await realpath(ideaRoot);
  if (root.startsWith('\\\\')) throw new Error('ローカルのイデアを指定してください。');
  const path = await realpath(resolve(root, 'body', ...parts));
  if (!path.startsWith(root + sep)) {
    throw new Error('体のファイルがイデアの外を参照しています。');
  }
  const file = await open(path, 'r');
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > limit) throw new Error('体のファイルが大きすぎます。');
    const bytes = Buffer.alloc(stat.size);
    let offset = 0;
    while (offset < bytes.length) {
      const { bytesRead } = await file.read(bytes, offset, bytes.length - offset, offset);
      if (!bytesRead) throw new Error('体のファイルの読み込み中にファイルが変更されました。');
      offset += bytesRead;
    }
    validate(bytes);
    return { path, bytes };
  } finally {
    await file.close();
  }
}

export function readIdeaAvatar(ideaRoot: string) {
  return readIdeaBodyFile(ideaRoot, ['avatar.vrm'], MAX_AVATAR_BYTES, validateAvatar);
}

export async function readIdeaMotion(ideaRoot: string, name: string) {
  if (!MOTION_NAME.test(name)) throw new Error('動きの名前が不正です。');
  return readIdeaBodyFile(ideaRoot, ['motions', `${name}.vrma`], MAX_MOTION_BYTES, validateMotion);
}
