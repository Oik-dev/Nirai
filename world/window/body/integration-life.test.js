import assert from 'node:assert/strict';
import test from 'node:test';
import { lifeOf } from '../../sea/life.ts';
import { activityAt } from './activity-route.js';
import { applyDefaultAppearance } from './appearance.js';
import { BodyChoices } from './choices.js';

const at = minute => new Date(Date.UTC(2026, 9, 9, 3, minute)).toISOString();
const record = (minute, kind, value, by = 'reply', ref = 'r1') => ({
  ts: at(minute), kind, value, by, ref,
});
const catalog = {
  expressions: [],
  gestures: [],
  appearance: [{ name: '衣装', options: ['普段着', '上着'] }],
};

async function wardrobe() {
  const scene = {};
  const jacket = { isMesh: true, visible: true, parent: scene, traverse(visitor) { visitor(this); } };
  const gltf = {
    scene,
    parser: {
      json: {
        nodes: [{ name: 'jacket', mesh: 0 }],
        meshes: [{ primitives: [{}] }],
        extras: { nirai: { capabilities: { appearance: {
          schemaVersion: 1,
          controls: [{
            id: 'outfit', label: '衣装', defaultOption: 'normal',
            options: [
              { id: 'normal', label: '普段着', visibility: [{ node: 0, nodeName: 'jacket', value: false }], morphs: [] },
              { id: 'jacket', label: '上着', visibility: [{ node: 0, nodeName: 'jacket', value: true }], morphs: [] },
            ],
          }],
        } } } },
      },
      async getDependency(kind, index) {
        assert.equal(kind, 'node');
        assert.equal(index, 0);
        return jacket;
      },
    },
  };
  return { jacket, appearance: await applyDefaultAppearance(gltf) };
}

test('海のPulse訪問中も目覚めの着替えが実際のVRM表示に届き、本人の活動へ戻る', async () => {
  const { jacket, appearance } = await wardrobe();
  const swim = record(5, 'activity', '海の中を泳ぐ');
  const approach = record(7, 'approach', 'start', 'pulse');
  const outfit = { ...record(8, 'appearance', '上着', 'waking'), control: '衣装' };
  const rest = record(9, 'activity', '砂地で休む');

  let records = [approach, swim];
  let reloaded = 0;
  const body = {
    expressions: [],
    setLife(life) { this.life = life; },
    setAppearance(chosen) { appearance.apply(chosen); },
    setExpression() {},
    async play() {},
  };
  const choices = new BodyChoices({
    body: () => body,
    reloadAvatar: async () => { reloaded++; },
    snapshot: async () => ({
      catalog, revision: 'avatar-1',
      life: await lifeOf(records, false, catalog),
    }),
    onError(error) { throw error; },
  });

  assert.equal(await choices.refresh(), true);
  assert.equal(jacket.visible, false, '最初はVRM既定の普段着');
  assert.equal(activityAt(body.life.activity, Date.parse(at(8))).name, '窓辺にいる');

  records = [outfit, approach, swim];
  assert.equal(await choices.refresh(), true);
  assert.equal(jacket.visible, true, '記録の外見labelを同じVRMに適用');
  assert.equal(activityAt(body.life.activity, Date.parse(at(8))).name, '窓辺にいる',
    '服だけ変えても訪問は続く');

  records = [rest, outfit, approach, swim];
  assert.equal(await choices.refresh(), true);
  assert.equal(activityAt(body.life.activity, Date.parse(at(9))).name, '砂地で休む');
  assert.equal(jacket.visible, true, '活動を変えても服は維持');
  assert.equal(reloaded, 1, '活動・服の更新ではVRMを読み直さない');
  choices.dispose();
});
