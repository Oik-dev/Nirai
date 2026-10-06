import assert from 'node:assert/strict';
import test from 'node:test';
import { validateAvatar } from './body.ts';

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
