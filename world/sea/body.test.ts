import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { readIdeaMotion, validateAvatar, validateMotion } from './body.ts';

function glb(document: object) {
  const source = Buffer.from(JSON.stringify(document), 'utf8');
  const jsonLength = Math.ceil(source.length / 4) * 4;
  const bytes = Buffer.alloc(20 + jsonLength, 0x20);
  bytes.writeUInt32LE(0x46546c67, 0);
  bytes.writeUInt32LE(2, 4);
  bytes.writeUInt32LE(bytes.length, 8);
  bytes.writeUInt32LE(jsonLength, 12);
  bytes.writeUInt32LE(0x4e4f534a, 16);
  source.copy(bytes, 20);
  return bytes;
}

test('VRM入口は自己完結したGLBだけを受け入れる', () => {
  const bytes = glb({
    asset: { version: '2.0' },
    extensions: { VRMC_vrm: { specVersion: '1.0' } },
    buffers: [{ byteLength: 0, uri: 'data:application/octet-stream;base64,' }],
  });
  assert.equal(validateAvatar(bytes).asset.version, '2.0');
});

test('VRM入口は外部参照を拒否する', () => {
  const bytes = glb({
    asset: { version: '2.0' },
    extensions: { VRMC_vrm: { specVersion: '1.0' } },
    images: [{ uri: 'https://example.com/face.png' }],
  });
  assert.throws(() => validateAvatar(bytes), /外部ファイル/);
});

test('VRM入口はVRMではないGLBを拒否する', () => {
  const bytes = glb({ asset: { version: '2.0' } });
  assert.throws(() => validateAvatar(bytes), /VRM情報/);
});

test('覚えた動きの入口はVRMアニメーションのGLBだけを受け入れる', () => {
  assert.ok(validateMotion(glb({ asset: { version: '2.0' }, extensions: { VRMC_vrm_animation: { specVersion: '1.0' } } })));
  assert.throws(() => validateMotion(glb({ asset: { version: '2.0' }, extensions: { VRMC_vrm: {} } })), /VRMアニメーション情報/);
});

test('覚えた動きは名前の通りにイデアの body/motions からだけ読む', async t => {
  const idea = await mkdtemp(join(tmpdir(), 'nirai-motion-'));
  t.after(() => rm(idea, { recursive: true, force: true }));
  await mkdir(join(idea, 'body', 'motions'), { recursive: true });
  const motion = glb({ asset: { version: '2.0' }, extensions: { VRMC_vrm_animation: { specVersion: '1.0' } } });
  await writeFile(join(idea, 'body', 'motions', 'のびをする.vrma'), motion);
  await writeFile(join(idea, 'body', 'avatar.vrm'), motion);

  assert.deepEqual((await readIdeaMotion(idea, 'のびをする')).bytes, motion);
  await assert.rejects(readIdeaMotion(idea, 'ない動き'), /ENOENT/);
  for (const name of ['../avatar', 'motions\\..\\..\\avatar', 'a/b', '.hidden', ' 前の空白', '後ろの空白 ', 'c:x', '', 'あ'.repeat(65)]) {
    await assert.rejects(readIdeaMotion(idea, name), /動きの名前/, name);
  }
});
