import assert from 'node:assert/strict';
import test from 'node:test';
import { WorldFrameLoop } from './sea/frame-loop.js';

test('隠れた窓は描画要求を持ち越さない', () => {
  let visible = true;
  let requested = null;
  let cancelled = 0;
  let now = 100;
  let draws = 0;
  const loop = new WorldFrameLoop({
    available: () => visible,
    continuous: () => false,
    draw: () => { draws++; },
    now: () => now,
    request: callback => { requested = callback; return 7; },
    cancel: () => { cancelled++; },
  });

  loop.invalidate();
  assert.equal(loop.frame, 7);
  visible = false;
  loop.sync();
  assert.equal(loop.frame, 0);
  assert.equal(cancelled, 1);
  assert.equal(draws, 0);

  visible = true;
  now = 200;
  loop.sync();
  assert.equal(loop.frame, 7);
  requested(234);
  assert.equal(draws, 1);
});
