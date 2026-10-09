import assert from 'node:assert/strict';
import { appendFile, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { appendBodyApproach, appendBodyChoice, appendWorkshopResult, bodyRecordsNewestFirst, installWorkshopMotion, pendingBodyWishes, readBodyCatalog, readIdeaAvatar, readIdeaMotion, validateAvatar, validateMotion } from './body.ts';
import { GESTURE_NAMES } from '../window/body/catalog.js';
import { ACTIVITIES } from '../window/body/activities.js';
import { lifeOf } from './life.ts';

const expressionNow = async (idea: string) => (await lifeOf(bodyRecordsNewestFirst(idea), false)).expression;

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

async function fixture(t: test.TestContext) {
  const idea = await mkdtemp(join(tmpdir(), 'nirai-body-choice-'));
  t.after(() => rm(idea, { recursive: true, force: true }));
  await mkdir(join(idea, 'body', 'motions'), { recursive: true });
  return idea;
}

const morph = { morphTargetBinds: [{ node: 0, index: 0, weight: 1 }] };
const motion = glb({ asset: { version: '2.0' }, extensions: { VRMC_vrm_animation: { specVersion: '1.0' } } });

test('工房が合格した動きだけをpartial経由で配置し、残った書きかけを上書きできる', async t => {
  const idea = await fixture(t);
  const directory = join(idea, 'body', 'motions');
  await writeFile(join(directory, '新しい動き.vrma.partial'), '中断した書きかけ');
  await installWorkshopMotion(idea, '新しい動き', motion);
  assert.deepEqual((await readIdeaMotion(idea, '新しい動き')).bytes, motion);
  assert.deepEqual((await readdir(directory)).sort(), ['新しい動き.vrma']);
  await assert.rejects(installWorkshopMotion(idea, '新しい動き', motion), /すでに保存/);
  assert.deepEqual((await readIdeaMotion(idea, '新しい動き')).bytes, motion);
});

test('工房は不正な名前・VRMA・リンクに動きを保存しない', async t => {
  const idea = await fixture(t);
  const directory = join(idea, 'body', 'motions');
  for (const name of ['../外', 'なし', 'そのまま', 'ほかの動き', 'a'.repeat(41)]) {
    await assert.rejects(installWorkshopMotion(idea, name, motion), /名前/, name);
  }
  await assert.rejects(installWorkshopMotion(idea, '不正な動き', Buffer.from('broken')), /GLB/);
  const outside = await mkdtemp(join(tmpdir(), 'nirai-workshop-outside-'));
  t.after(() => rm(outside, { recursive: true, force: true }));
  await symlink(outside, join(directory, 'リンク.vrma.partial'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(installWorkshopMotion(idea, 'リンク', motion), /書きかけ/);
  assert.deepEqual(await readdir(outside), []);
});

test('今のVRM 1.0の効く表情と有効な動きだけを日本語カタログにする', async t => {
  const idea = await fixture(t);
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb({ extensions: { VRMC_vrm: { expressions: {
    preset: { happy: morph, sad: morph, neutral: {}, angry: { morphTargetBinds: [] }, blink: morph, lookUp: morph, aa: morph, unknown: morph },
    custom: { 照れる: morph, joy: morph, happy: morph, なし: morph, そのまま: morph, 色が変わる: { materialColorBinds: [{ material: 0 }] } },
  } } } }));
  await writeFile(join(idea, 'body', 'motions', 'のびをする.vrma'), motion);
  await writeFile(join(idea, 'body', 'motions', 'うなずく.vrma'), motion);
  await writeFile(join(idea, 'body', 'motions', '壊れた動き.vrma'), '途中で切れたファイル');
  await writeFile(join(idea, 'body', 'motions', '外を読む.vrma'), glb({ extensions: { VRMC_vrm_animation: {} }, buffers: [{ uri: '../other.bin' }] }));
  await writeFile(join(idea, 'body', 'motions', '.hidden.vrma'), motion);
  await writeFile(join(idea, 'body', 'motions', 'そのまま.vrma'), motion);
  const catalog = await readBodyCatalog(idea);
  assert.deepEqual(catalog.expressions, ['喜び', '悲しみ', '照れる', 'joy', '色が変わる']);
  assert.deepEqual(catalog.gestures, [...GESTURE_NAMES, 'のびをする']);
});

test('外見MetadataをJSONだけで検査し、壊れた服の選択肢は精神へ渡さない', async t => {
  const idea = await fixture(t);
  const avatar = (invalid: boolean) => glb({
    extensions: { VRMC_vrm: { expressions: { preset: { happy: morph } } } },
    nodes: [{ name: 'Coat', mesh: 0 }],
    meshes: [{ primitives: [{}] }],
    extras: { nirai: { capabilities: { appearance: { schemaVersion: 1, controls: [{
      id: 'outfit', label: '服装', defaultOption: 'normal', options: [
        { id: 'normal', label: '普段着', visibility: [{ node: 0, nodeName: 'Coat', value: false }], morphs: [] },
        { id: 'coat', label: 'コート', visibility: [{ node: 0, nodeName: invalid ? 'Unknown' : 'Coat', value: true }], morphs: [] },
      ],
    }] } } } },
  });
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar(false));
  let catalog = await readBodyCatalog(idea);
  assert.deepEqual(catalog.appearance, [{ name: '服装', options: ['普段着', 'コート'] }]);
  assert.deepEqual(catalog.expressions, ['喜び']);
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar(true));
  catalog = await readBodyCatalog(idea);
  assert.deepEqual(catalog.appearance, []);
  assert.deepEqual(catalog.expressions, ['喜び'], '服が無効でも表情は残す');
});

test('VRM 0.xのプリセットを日本語へ直し、生理用と空の表情を除く', async t => {
  const idea = await fixture(t);
  const bound = { binds: [{ mesh: 0, index: 0, weight: 100 }] };
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb({ extensions: { VRM: { blendShapeMaster: { blendShapeGroups: [
    { presetName: 'joy', name: 'Joy', ...bound },
    { presetName: 'sorrow', name: 'Sorrow', ...bound },
    { presetName: 'fun', ...bound },
    { presetName: 'blink_l', ...bound },
    { presetName: 'lookup', ...bound },
    { presetName: 'a', ...bound },
    { presetName: 'neutral', binds: [] },
    { presetName: 'unknown', name: '照れる', ...bound },
    { presetName: 'unknown', name: 'joy', ...bound },
    { presetName: 'unrecognized', name: '困る', ...bound },
    { name: 'にやり', materialValues: [{ materialName: 'face', propertyName: '_Color', targetValue: [1, 1, 1, 1] }] },
  ] } } } }));
  assert.deepEqual((await readBodyCatalog(idea)).expressions, ['喜び', '悲しみ', '楽しさ', '照れる', 'joy', '困る', 'にやり']);
});

test('カタログは体と覚えた動きを替えるたびに作り直す', async t => {
  const idea = await fixture(t);
  const avatar = (raw: string) => glb({ extensions: { VRMC_vrm: { expressions: { preset: { [raw]: morph } } } } });
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar('happy'));
  assert.deepEqual((await readBodyCatalog(idea)).expressions, ['喜び']);
  await writeFile(join(idea, 'body', 'avatar.vrm'), avatar('sad'));
  await writeFile(join(idea, 'body', 'motions', 'のびをする.vrma'), motion);
  assert.deepEqual(await readBodyCatalog(idea), { expressions: ['悲しみ'], gestures: [...GESTURE_NAMES, 'のびをする'], activities: Object.keys(ACTIVITIES), appearance: [] });
});

test('カタログは体が無ければ表情を持たず、不正な体は成功扱いにしない', async t => {
  const idea = await fixture(t);
  assert.deepEqual(await readBodyCatalog(idea), { expressions: [], gestures: [...GESTURE_NAMES], activities: Object.keys(ACTIVITIES), appearance: [] });
  await writeFile(join(idea, 'body', 'avatar.vrm'), glb({ extensions: { VRMC_vrm: {} }, images: [{ uri: 'https://example.com/image.png' }] }));
  await assert.rejects(readBodyCatalog(idea), /外部ファイル/);
});

test('体の選択は現在のカタログで確かめ、日本日付の記録に追記する', async t => {
  const idea = await fixture(t);
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-10-08T15:10:00.000Z').getTime() });
  const catalog = { expressions: ['喜び'], gestures: [...GESTURE_NAMES] };
  const first = await appendBodyChoice(idea, { by: 'reply', ref: 'a/ref with no prescribed format', expression: '喜び', gesture: 'うなずく' }, catalog);
  assert.deepEqual(first, [
    { ts: '2026-10-08T15:10:00.000Z', kind: 'expression', value: '喜び', by: 'reply', ref: 'a/ref with no prescribed format' },
    { ts: '2026-10-08T15:10:00.000Z', kind: 'gesture', value: 'うなずく', by: 'reply', ref: 'a/ref with no prescribed format' },
  ]);
  const path = join(idea, 'lifelog', 'body', '2026-10-09.jsonl');
  const before = await readFile(path, 'utf8');
  const reset = await appendBodyChoice(idea, { by: 'pulse', ref: 'pulse-ref', expression: 'なし', gesture: 'なし' }, catalog);
  assert.equal(reset.length, 1);
  assert.equal(reset[0].value, 'なし');
  assert.ok((await readFile(path, 'utf8')).startsWith(before));
  assert.deepEqual(await readdir(join(idea, 'lifelog', 'body')), ['2026-10-09.jsonl']);
  assert.equal(await expressionNow(idea), null);
});

test('ほかの動きだけが願いになり、既知の身振りと不正な名前は願いにならない', async t => {
  const idea = await fixture(t);
  const catalog = { expressions: [], gestures: [...GESTURE_NAMES] };
  const outside = await appendBodyChoice(idea, { by: 'reply', ref: 'r0', gesture: 'うなずく', wish: '踊る' }, catalog);
  assert.deepEqual(outside.map(record => record.kind), ['gesture']);
  const newWish = await appendBodyChoice(idea, { by: 'reply', ref: 'r1', wish: '手を振る' }, catalog);
  assert.deepEqual(newWish.map(record => [record.kind, record.value]), [['wish', '手を振る']]);
  const existing = await appendBodyChoice(idea, { by: 'pulse', ref: 'r2', gesture: 'ほかの動き', wish: 'うなずく' }, catalog);
  assert.deepEqual(existing.map(record => [record.kind, record.value]), [['gesture', 'うなずく']]);
  for (const invalid of ['なし', 'そのまま', 'ほかの動き', '../outside', '長'.repeat(41), 'あ\nい']) {
    assert.deepEqual(await appendBodyChoice(idea, { by: 'reply', ref: 'bad', gesture: 'ほかの動き', wish: invalid }, catalog), []);
  }
  assert.deepEqual(await pendingBodyWishes(idea), [{ name: '手を振る', ref: 'r1' }]);
});

test('工房の候補は古い願い優先、学習済・当日失敗・3回失敗・既存ファイルを除く', async t => {
  const idea = await fixture(t);
  const directory = join(idea, 'lifelog', 'body');
  await mkdir(directory, { recursive: true });
  const row = (kind: string, value: string, ts: string, ref: string, by = kind === 'wish' ? 'reply' : 'workshop') =>
    JSON.stringify({ ts, kind, value, by, ref });
  await writeFile(join(directory, '2026-10-08.jsonl'), [
    row('wish', '先の願い', '2026-10-08T01:00:00Z', 'original'),
    row('wish', '後の願い', '2026-10-08T02:00:00Z', 'second'),
    row('wish', '先の願い', '2026-10-08T03:00:00Z', 'repeat'),
    row('wish', '済んだ願い', '2026-10-08T04:00:00Z', 'done'),
    row('learned', '済んだ願い', '2026-10-08T05:00:00Z', 'done'),
    row('wish', '三回失敗', '2026-10-08T06:00:00Z', 'failed'),
    ...[7, 8, 9].map(hour => row('failed', '三回失敗', `2026-10-08T${String(hour).padStart(2, '0')}:00:00Z`, 'failed')),
    row('wish', 'ファイルあり', '2026-10-08T10:00:00Z', 'file'),
  ].join('\n') + '\n');
  await mkdir(join(idea, 'body', 'motions'), { recursive: true });
  await writeFile(join(idea, 'body', 'motions', 'ファイルあり.vrma'), '未検証でも既存のため再生成しない');
  const day = new Date('2026-10-10T12:00:00Z');
  assert.deepEqual(await pendingBodyWishes(idea, day), [
    { name: '先の願い', ref: 'original' }, { name: '後の願い', ref: 'second' },
  ]);
  const now = new Date();
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-10-10T12:00:00Z').getTime() });
  await appendWorkshopResult(idea, { kind: 'failed', value: '先の願い', ref: 'original' });
  assert.deepEqual(await pendingBodyWishes(idea, day), [{ name: '後の願い', ref: 'second' }]);
  assert.ok(now instanceof Date);
  await assert.rejects(appendWorkshopResult(idea, { kind: 'learned', value: '../unsafe', ref: 'ref' }), /不正/);
});

test('知らない選択は欄ごとに落とし、不正な出所は全部落とす', async t => {
  const idea = await fixture(t);
  const catalog = { expressions: ['喜び'], gestures: [...GESTURE_NAMES] };
  for (const event of [null, [], '文字列', { by: 'waking', ref: 'ref', expression: '喜び' }, { by: 'reply', expression: '喜び' },
    { by: 'reply', ref: 1, expression: '喜び' }, { by: 'reply', ref: '  ', expression: '喜び' },
    { by: 'reply', ref: 'ref', expression: 'そのまま', gesture: 'なし' }, { by: 'reply', ref: 'ref', expression: '悲しみ', gesture: '踊る' }]) {
    assert.deepEqual(await appendBodyChoice(idea, event, catalog), []);
  }
  assert.equal(await expressionNow(idea), null);
  const records = await appendBodyChoice(idea, { by: 'reply', ref: 'ref', expression: '悲しみ', gesture: 'うなずく' }, catalog);
  assert.deepEqual(records.map(record => record.kind), ['gesture']);
});

test('本人が選んだ活動を記録し、同じ活動は訪問中だけ書き直す', async t => {
  const idea = await fixture(t);
  t.mock.timers.enable({ apis: ['Date'], now: Date.parse('2026-10-09T03:00:00.000Z') });
  const catalog = { expressions: [], gestures: [], activities: ['砂地で休む', '海の中を泳ぐ'] };
  const choose = async (activity: string) => appendBodyChoice(idea, { by: 'reply', ref: 'r', activity }, catalog);
  assert.deepEqual((await choose('砂地で休む')).map(r => r.kind), ['activity']);
  assert.deepEqual(await choose('砂地で休む'), [], '同じ活動は移動時刻をリセットしない');
  assert.deepEqual(await choose('窓辺にいる'), [], 'カタログ外は記録しない');
  const log = join(idea, 'lifelog', 'body', '2026-10-09.jsonl');
  await appendFile(log, JSON.stringify({ ts: '2026-10-09T03:00:01.000Z',
    kind: 'approach', value: 'start', by: 'pulse', ref: 'pulse-1' }) + '\n');
  t.mock.timers.setTime(Date.parse('2026-10-09T03:00:02.000Z'));
  assert.deepEqual((await choose('砂地で休む')).map(r => r.kind), ['activity'],
    '訪問中は同じ活動を選んでも窓辺への訪問を終える');
  assert.deepEqual(await choose('砂地で休む'), [], '終了後の重複は記録しない');
});

test('Pulseの訪問開始と失敗を同じrefで記録し、本文や壊れた知らせは書かない', async t => {
  const idea = await fixture(t);
  t.mock.timers.enable({ apis: ['Date'], now: Date.parse('2026-10-09T03:00:00.000Z') });
  const start = { type: 'approach', kind: 'connection', ref: 'pulse-1' };
  assert.deepEqual(await appendBodyApproach(idea, { ...start, ref: '' }), []);
  assert.deepEqual(await appendBodyApproach(idea, { ...start, failed: 'true' }), []);
  assert.deepEqual((await appendBodyApproach(idea, start)).map(r => r.value), ['start']);
  t.mock.timers.setTime(Date.parse('2026-10-09T03:00:01.000Z'));
  assert.deepEqual((await appendBodyApproach(idea, { ...start, failed: true, text: '記録しない言葉' })).map(r => r.value), ['failed']);
  const records = [];
  for await (const record of bodyRecordsNewestFirst(idea)) records.push(record);
  assert.deepEqual(records.map(({ kind, value, by, ref }) => ({ kind, value, by, ref })), [
    { kind: 'approach', value: 'failed', by: 'pulse', ref: 'pulse-1' },
    { kind: 'approach', value: 'start', by: 'pulse', ref: 'pulse-1' },
  ]);
  assert.ok(!(await readFile(join(idea, 'lifelog', 'body', '2026-10-09.jsonl'), 'utf8')).includes('記録しない言葉'));
});

test('記録がないときの初期活動は居場所なので、同じ選択では経路をリセットしない', async t => {
  const idea = await fixture(t);
  const catalog = { expressions: [], gestures: [], activities: ['居場所でくつろぐ'] };
  const actual = await appendBodyChoice(idea, { by: 'reply', ref: 'ref', activity: '居場所でくつろぐ' }, catalog);
  assert.deepEqual(actual, []);
});

test('壊れた末尾はそのまま残し、新しく追記した表情を復元できる', async t => {
  const idea = await fixture(t);
  t.mock.timers.enable({ apis: ['Date'], now: new Date('2026-10-09T01:00:00.000Z').getTime() });
  const directory = join(idea, 'lifelog', 'body');
  await mkdir(directory, { recursive: true });
  const record = { ts: '2026-10-08T01:00:00.000Z', kind: 'expression', value: '悲しみ', by: 'reply', ref: 'previous' };
  await writeFile(join(directory, '2026-10-08.jsonl'), JSON.stringify(record) + '\n');
  const path = join(directory, '2026-10-09.jsonl');
  const broken = '{"kind":"expression","value":"途中';
  await writeFile(path, broken);
  assert.equal(await expressionNow(idea), '悲しみ');
  await appendBodyChoice(idea, { by: 'reply', ref: 'next', expression: '喜び' }, { expressions: ['喜び'], gestures: [] });
  assert.ok((await readFile(path, 'utf8')).startsWith(broken + '\n'));
  assert.equal(await expressionNow(idea), '喜び');
  await appendBodyChoice(idea, { by: 'pulse', ref: 'reset', expression: 'なし' }, { expressions: [], gestures: [] });
  assert.equal(await expressionNow(idea), null);
});

test('追記できなければ記録の成功を返さない', async t => {
  const idea = await fixture(t);
  await mkdir(join(idea, 'lifelog'));
  await writeFile(join(idea, 'lifelog', 'body'), 'ここはフォルダーではない');
  await assert.rejects(appendBodyChoice(idea, { by: 'reply', ref: 'ref', expression: '喜び' }, { expressions: ['喜び'], gestures: [] }));
  assert.equal(await readFile(join(idea, 'lifelog', 'body'), 'utf8'), 'ここはフォルダーではない');
});

test('イデアの外へ向けた体・動き・記録のリンクを通らない', async t => {
  const parent = await mkdtemp(join(tmpdir(), 'nirai-body-boundary-'));
  t.after(() => rm(parent, { recursive: true, force: true }));
  const idea = join(parent, 'idea');
  const outside = join(parent, 'outside');
  await mkdir(idea);
  await mkdir(join(outside, 'motions'), { recursive: true });
  await writeFile(join(outside, 'avatar.vrm'), glb({ extensions: { VRMC_vrm: {} } }));
  await writeFile(join(outside, 'motions', 'のびをする.vrma'), motion);
  await symlink(outside, join(idea, 'body'), process.platform === 'win32' ? 'junction' : 'dir');
  await symlink(outside, join(idea, 'lifelog'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(readIdeaAvatar(idea), /イデアの外/);
  await assert.rejects(readIdeaMotion(idea, 'のびをする'), /イデアの外/);
  await assert.rejects(readBodyCatalog(idea), /イデアの外/);
  await assert.rejects(expressionNow(idea), /イデアの外/);
  await assert.rejects(appendBodyChoice(idea, { by: 'reply', ref: 'ref', expression: '喜び' }, { expressions: ['喜び'], gestures: [] }), /イデアの外/);
  assert.deepEqual((await readdir(outside)).sort(), ['avatar.vrm', 'motions']);
});
