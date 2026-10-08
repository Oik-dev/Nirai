// 体の置き場（イデアの body/）を読む入口。体（VRM）と覚えた動き（.vrma）は、どちらも自己完結したGLBだけを窓へ渡す。
import { lstat, mkdir, open, readFile, readdir, realpath } from 'node:fs/promises';
import { isAbsolute, resolve, sep } from 'node:path';
import { expressionLabel, expressionPresetName, GESTURE_NAMES, OWNED_EXPRESSIONS } from '../window/body/catalog.js';

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

async function localIdeaRoot(ideaRoot: string) {
  if (!ideaRoot || !isAbsolute(ideaRoot)) throw new Error('ローカルのイデアを指定してください。');
  const root = await realpath(ideaRoot);
  if (root.startsWith('\\\\')) throw new Error('ローカルのイデアを指定してください。');
  return root;
}

function withinIdea(root: string, path: string) {
  if (!path.startsWith(root + sep)) throw new Error('体のファイルがイデアの外を参照しています。');
  return path;
}

async function readIdeaBodyFile<T>(ideaRoot: string, parts: string[], limit: number, validate: (bytes: Buffer) => T) {
  const root = await localIdeaRoot(ideaRoot);
  const path = await realpath(resolve(root, 'body', ...parts));
  withinIdea(root, path);
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
    const document = validate(bytes);
    return { path, bytes, document };
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

export type BodyCatalog = { expressions: string[]; gestures: string[] };
export type BodyRecord = {
  ts: string;
  kind: 'expression' | 'gesture';
  value: string;
  by: 'reply' | 'pulse';
  ref: string;
};

function hasCode(error: unknown, code: string) {
  return error !== null && typeof error === 'object' && 'code' in error && error.code === code;
}

function selectableName(raw: string) {
  return raw.trim() !== '' && raw !== 'なし' && raw !== 'そのまま' && !OWNED_EXPRESSIONS.includes(raw);
}

function hasBinds(expression: unknown, keys: string[]) {
  if (expression === null || typeof expression !== 'object') return false;
  const fields = expression as Record<string, unknown>;
  return keys.some(key => Array.isArray(fields[key]) && fields[key].length > 0);
}

// 保存した一覧を使い回さず、今の体と覚えた動きから、そのつど作る。
export async function readBodyCatalog(ideaRoot: string): Promise<BodyCatalog> {
  const expressions = new Set<string>();
  try {
    const { document } = await readIdeaAvatar(ideaRoot);
    const modern = document.extensions?.VRMC_vrm;
    if (modern) {
      for (const [preset, group] of [[true, modern.expressions?.preset], [false, modern.expressions?.custom]] as const) {
        for (const [raw, expression] of Object.entries(group ?? {})) {
          const knownPreset = expressionPresetName(raw, '1') !== null;
          if (preset ? !knownPreset : knownPreset) continue;
          if (selectableName(raw) && hasBinds(expression, ['morphTargetBinds', 'materialColorBinds', 'textureTransformBinds'])) {
            expressions.add(preset ? expressionLabel(raw) : raw);
          }
        }
      }
    } else {
      for (const expression of document.extensions?.VRM?.blendShapeMaster?.blendShapeGroups ?? []) {
        // three-vrm が 0.x を読む時と同じく、プリセットがあれば name より優先する。
        const preset = expressionPresetName(expression.presetName, '0');
        const raw = preset ?? expression.name;
        if (typeof raw === 'string' && selectableName(raw) && hasBinds(expression, ['binds', 'materialValues'])) {
          expressions.add(preset ? expressionLabel(raw) : raw);
        }
      }
    }
  } catch (error) {
    if (!hasCode(error, 'ENOENT')) throw error;
  }

  const root = await localIdeaRoot(ideaRoot);
  const gestures = new Set<string>(GESTURE_NAMES);
  let motions;
  try {
    const directory = withinIdea(root, await realpath(resolve(root, 'body', 'motions')));
    motions = await readdir(directory);
  } catch (error) {
    if (!hasCode(error, 'ENOENT')) throw error;
    motions = [];
  }
  for (const filename of motions.sort()) {
    if (!filename.endsWith('.vrma')) continue;
    const name = filename.slice(0, -5);
    if (!MOTION_NAME.test(name) || name === 'なし' || name === 'そのまま') continue;
    try {
      await readIdeaMotion(ideaRoot, name);
      gestures.add(name);
    } catch (error) {
      // 読めないだけの動きを「壊れた」と確定せず、海が次の見回りで読み直せるようにする。
      if (error !== null && typeof error === 'object' && 'code' in error && error.code !== 'ENOENT') throw error;
      // 壊れた動きや外を指す動きは、できることとして本人へ渡さない。
    }
  }
  return { expressions: [...expressions], gestures: [...gestures] };
}

// lifelog を外へ向けたリンクにすり替えても、外でフォルダーや記録を作らない。
async function bodyLogDirectory(root: string, create: boolean) {
  let directory = root;
  for (const part of ['lifelog', 'body']) {
    const candidate = resolve(directory, part);
    if (create) {
      try { await mkdir(candidate); }
      catch (error) { if (!hasCode(error, 'EEXIST')) throw error; }
    }
    directory = withinIdea(root, await realpath(candidate));
  }
  return directory;
}

async function bodyLogFile(directory: string, name: string) {
  const path = resolve(directory, name);
  try {
    if ((await lstat(path)).isSymbolicLink()) throw new Error('体の記録にはリンクを使えません。');
  } catch (error) {
    if (!hasCode(error, 'ENOENT')) throw error;
  }
  return path;
}

const JST_DAY = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' });

// 確かな選択だけを追記し、書き終えたものだけを呼び出し元へ返す。
export async function appendBodyChoice(ideaRoot: string, event: unknown, catalog: BodyCatalog): Promise<BodyRecord[]> {
  if (event === null || typeof event !== 'object' || Array.isArray(event)) return [];
  const choice = event as { by?: unknown; ref?: unknown; expression?: unknown; gesture?: unknown };
  if ((choice.by !== 'reply' && choice.by !== 'pulse') || typeof choice.ref !== 'string' || !choice.ref.trim()) return [];
  const now = new Date();
  const records: BodyRecord[] = [];
  for (const kind of ['expression', 'gesture'] as const) {
    const value = choice[kind];
    const available = kind === 'expression' ? catalog.expressions : catalog.gestures;
    if (typeof value !== 'string' || value === 'そのまま'
      || (value === 'なし' ? kind !== 'expression' : !available.includes(value))) continue;
    records.push({ ts: now.toISOString(), kind, value, by: choice.by, ref: choice.ref });
  }
  if (!records.length) return records;

  const root = await localIdeaRoot(ideaRoot);
  const directory = await bodyLogDirectory(root, true);
  const path = await bodyLogFile(directory, `${JST_DAY.format(now)}.jsonl`);
  const file = await open(path, 'a+');
  try {
    const info = await file.stat();
    if (!info.isFile()) throw new Error('体の記録の置き場がファイルではありません。');
    let prefix = '';
    if (info.size) {
      const last = Buffer.alloc(1);
      await file.read(last, 0, 1, info.size - 1);
      // 前回が途中で切れていても、その行を直さず、改行して次の記録を足す。
      if (last[0] !== 0x0a) prefix = '\n';
    }
    await file.writeFile(prefix + records.map(record => JSON.stringify(record) + '\n').join(''), 'utf8');
  } finally {
    await file.close();
  }
  return records;
}

export async function latestBodyExpression(ideaRoot: string): Promise<string | null> {
  const root = await localIdeaRoot(ideaRoot);
  let directory;
  let days: string[];
  try {
    directory = await bodyLogDirectory(root, false);
    days = (await readdir(directory)).filter(name => /^\d{4}-\d{2}-\d{2}\.jsonl$/.test(name)).sort().reverse();
  } catch (error) {
    if (hasCode(error, 'ENOENT')) return null;
    throw error;
  }
  for (const day of days) {
    const lines = (await readFile(await bodyLogFile(directory, day), 'utf8')).split('\n');
    for (let index = lines.length - 1; index >= 0; index--) {
      let record;
      try { record = JSON.parse(lines[index]); }
      catch { continue; }
      if (!record || record.kind !== 'expression' || typeof record.value !== 'string' || !record.value.trim()
        || (record.by !== 'reply' && record.by !== 'pulse') || typeof record.ref !== 'string' || !record.ref.trim()
        || typeof record.ts !== 'string' || !Number.isFinite(Date.parse(record.ts))) continue;
      return record.value === 'なし' ? null : record.value;
    }
  }
  return null;
}
