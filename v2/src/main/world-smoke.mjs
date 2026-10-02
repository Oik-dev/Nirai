import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { controlCommand } from '../../out/src/bridge/client.js';
import { runAppearanceSmoke } from './appearance-smoke.mjs';

export async function runWorldSmoke(window, { request, interruptNextReply, finish }) {
  window.webContents.setBackgroundThrottling(false);
  window.show();
  const errors = [];
  window.webContents.on('console-message', event => {
    if (event.level === 'error') errors.push(event.message);
  });
  const js = source => window.webContents.executeJavaScript(source);
  const wait = async (source, label, timeout = 20_000) => {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
      try { if (await js(source)) return; } catch { /* wait for reload */ }
      await new Promise(resolve => setTimeout(resolve, 80));
    }
    throw new Error(`World smoke timeout: ${label}; ${await js("document.getElementById('worldStatus')?.textContent")}; ${errors.join('\n')}`);
  };
  const world = "(await import('../../out/world.js')).world";
  const run = source => js(`(async () => { const w = ${world}; ${source} })()`);
  const waitSnapshot = async (predicate, label) => {
    const deadline = Date.now() + 20_000;
    while (Date.now() < deadline) {
      try {
        const snapshot = await request('snapshot');
        const result = predicate(snapshot);
        if (result) return result;
      } catch (error) {
        if (!/transport:|unavailable:/.test(String(error))) throw error;
      }
      await new Promise(resolve => setTimeout(resolve, 80));
    }
    throw new Error(`World smoke timeout: ${label}`);
  };
  const master = (type, payload, expectedRevision) => request('command', { envelope: {
    protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
    type, target: null, payload, ...(expectedRevision ? { expected_revision: expectedRevision } : {}),
  } });
  const capture = async name => {
    const directory = process.env.NIRAI_V2_UI_CAPTURE_DIR;
    await new Promise(resolve => setTimeout(resolve, 150));
    const image = await window.webContents.capturePage(undefined, { stayAwake: true });
    if (directory) {
      mkdirSync(directory, { recursive: true });
      writeFileSync(join(directory, name + '.png'), image.toPNG());
    }
    return image;
  };
  const saveAvatar = async path => {
    const snapshot = await request('snapshot');
    await request('command', { envelope: {
      protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(),
      type: 'UpdateSettings', target: null, expected_revision: snapshot.settings.revision,
      payload: { settings: { resident_avatars: path ? { holo: path } : {} } },
    } });
  };
  await wait(`(async () => (${world}).ready)()`, '3D ready');
  const frames = await run('return w.frames;');
  await wait(`(async () => (${world}).frames > ${frames + 3})()`, 'animation');
  await capture('world-empty');
  assert.equal(await run('return w.renderer.info.render.calls > 3;'), true, 'real scene draw calls');
  const avatarPath = process.env.NIRAI_V2_WORLD_SMOKE_AVATAR;
  let savedAppearance = null;
  let appearanceCoverage = null;
  const assertRelaxedArms = async label => {
    const pose = await run(`const a = w.avatars[0];
      if (!a) return null;
      w.scene.updateMatrixWorld(true);
      return { version: a.vrm.meta?.metaVersion, arms: ['left', 'right'].map(side => {
        const shoulder = a.vrm.humanoid.getRawBoneNode(side + 'UpperArm');
        const elbow = a.vrm.humanoid.getRawBoneNode(side + 'LowerArm');
        const hand = a.vrm.humanoid.getRawBoneNode(side + 'Hand');
        if (!shoulder || !elbow || !hand) return { side, missing: true };
        const shoulderY = shoulder.getWorldPosition(w.camera.position.clone()).y;
        const elbowY = elbow.getWorldPosition(w.camera.position.clone()).y;
        const handY = hand.getWorldPosition(w.camera.position.clone()).y;
        return { side, shoulderY, elbowY, handY, elbowDrop: shoulderY - elbowY, handDrop: shoulderY - handY };
      }) };`);
    if (label === 'initial load') console.log(`World smoke arm pose: ${JSON.stringify(pose)}`);
    assert.ok(pose, `${label}: displayed Avatar is present`);
    for (const arm of pose.arms) {
      assert.ok(!arm.missing && [arm.shoulderY, arm.elbowY, arm.handY].every(Number.isFinite),
        `${label}: ${arm.side} arm has displayed bone positions`);
      assert.ok(arm.elbowDrop > .01 && arm.handDrop > .01,
        `${label}: ${arm.side} elbow and hand stay below the shoulder: ${JSON.stringify(arm)}`);
    }
  };
  if (avatarPath) {
    await saveAvatar(avatarPath);
    await wait(`(async () => (${world}).avatars.length === 1)()`, 'real VRM');
    await capture('world-avatar');
    await assertRelaxedArms('initial load');
  }

  // Environment keyframes and the continuous 24-hour timeline must use the actual Debug UI
  // without moving the camera, replacing the Resident, or mutating Sea Settings/draw counts.
  const timeBaseline = await run(`w.input?.clear(); w.rig.home(); w.updateMode(); w.render();
    return {
      seaSettings: w.seaSettings,
      cameraPosition: w.camera.position.toArray(),
      cameraRotation: w.camera.quaternion.toArray(),
      particles: w.environment.particles.points.geometry.drawRange.count,
      bubbles: w.environment.bubbles.geometry.drawRange.count,
      avatarCount: w.avatars.length,
    };`);
  const assertEnvironmentState = async (expectedHour, expectedLive = false) => {
    const state = await run(`return {
      displayHour: w.environmentHour,
      environmentHour: w.environment.environmentHour,
      live: w.environmentTimeLive,
      seaSettings: w.seaSettings,
      cameraPosition: w.camera.position.toArray(),
      cameraRotation: w.camera.quaternion.toArray(),
      particles: w.environment.particles.points.geometry.drawRange.count,
      bubbles: w.environment.bubbles.geometry.drawRange.count,
      avatarCount: w.avatars.length,
    };`);
    assert.equal(state.displayHour, expectedHour);
    assert.equal(state.environmentHour, expectedHour);
    assert.equal(state.live, expectedLive);
    assert.deepEqual(state.seaSettings, timeBaseline.seaSettings);
    assert.deepEqual(state.cameraPosition, timeBaseline.cameraPosition);
    assert.deepEqual(state.cameraRotation, timeBaseline.cameraRotation);
    assert.equal(state.particles, timeBaseline.particles);
    assert.equal(state.bubbles, timeBaseline.bubbles);
    assert.equal(state.avatarCount, timeBaseline.avatarCount);
  };
  for (const timeOfDay of ['morning', 'day', 'night']) {
    const expectedHour = await run(`const button=document.querySelector('[data-time-of-day="${timeOfDay}"]');
      button.click(); w.render(); return Number(button.dataset.environmentHour);`);
    await assertEnvironmentState(expectedHour);
    await capture(`world-time-${timeOfDay}`);
  }
  await run(`const slider=document.getElementById('environment-hour'); slider.value='19.5';
    slider.dispatchEvent(new Event('input', { bubbles:true })); w.render();`);
  await assertEnvironmentState(19.5);
  await capture('world-time-day-night-transition');

  const liveState = await run(`document.getElementById('environment-time-live').click(); w.render();
    const now=new Date(); return {
      hour:w.environmentHour,
      expected:now.getHours()+now.getMinutes()/60+now.getSeconds()/3600+now.getMilliseconds()/3600000,
      live:w.environmentTimeLive,
    };`);
  const liveDistance = Math.abs(liveState.hour - liveState.expected);
  assert.equal(liveState.live, true);
  assert.ok(Math.min(liveDistance, 24 - liveDistance) < .01, 'live environment follows the local system clock');

  await run(`document.querySelector('[data-time-of-day="day"]').click(); w.render();`);
  await assertEnvironmentState(13);

  // Keep the sea review available without a local avatar, then restore the starting layout.
  const initialSize = window.getSize();
  for (const [width, height] of [[360, 600], [620, 980], [1500, 930]]) {
    window.setSize(width, height);
    await new Promise(resolve => setTimeout(resolve, 180));
    assert.ok(await run('return w.ready && !w.canvas.hidden && w.renderer.domElement.width > 0;'));
    await capture(`world-${width}`);
  }
  await run("document.getElementById('collapseButton').click();");
  for (const [name, position] of [
    ['full', ''],
    ['look-up', 'w.rig.rotate(0, -230);'],
    ['reverse', 'w.rig.rotate(-Math.PI / .004, 0);'],
    ['near-floor', 'w.rig.move(0, -1, 0, 3); w.rig.rotate(0, -35);'],
    ['near-surface', 'w.rig.move(0, 1, 0, 3); w.rig.rotate(0, -130);'],
  ]) {
    await run(`w.input.clear(); w.rig.home(); ${position} w.updateMode(); w.render();`);
    await capture(`world-${name}`);
  }
  // Native keyboard and pointer input must share the same presentation cadence,
  // whether or not this smoke has a local VRM available.
  const cameraSample = 'return { position:w.camera.position.toArray(), rotation:w.camera.quaternion.toArray(), frames:w.frames, time:performance.now() };';
  const drag = await run(`w.input.clear(); w.rig.home(); w.updateMode(); w.requestRender(); w.canvas.focus();
    return { x:Math.round(innerWidth*.45), y:Math.round(innerHeight*.45) };`);
  const beforeMixed = await run(cameraSample);
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'W' });
  window.webContents.sendInputEvent({ type: 'mouseDown', ...drag, button: 'right', clickCount: 1 });
  try {
    for (let step = 1; step <= 32; step++) {
      await new Promise(resolve => setTimeout(resolve, 12));
      window.webContents.sendInputEvent({ type: 'mouseMove', x: drag.x + step * 3, y: drag.y + step,
        button: 'right', modifiers: ['rightbuttondown'] });
    }
  } finally {
    window.webContents.sendInputEvent({ type: 'mouseUp', x: drag.x + 96, y: drag.y + 32, button: 'right', clickCount: 1 });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'W' });
  }
  await new Promise(resolve => setTimeout(resolve, 80));
  const afterMixed = await run(cameraSample);
  const mixedElapsed = afterMixed.time - beforeMixed.time;
  const mixedFrames = afterMixed.frames - beforeMixed.frames;
  const mixedDistance = Math.hypot(...afterMixed.position.map((value, index) => value - beforeMixed.position[index]));
  const rotationDot = afterMixed.rotation.reduce((sum, value, index) => sum + value * beforeMixed.rotation[index], 0);
  const mixedTurn = 2 * Math.acos(Math.min(1, Math.abs(rotationDot)));
  assert.ok(mixedDistance > .05, 'native WASD moves the camera while right-dragging');
  assert.ok(mixedTurn > .05, 'native right-drag turns the camera while WASD is held');
  assert.ok(mixedFrames > 0 && mixedFrames <= Math.ceil(mixedElapsed * 30 / 1000) + 3,
    `mixed input must not render per pointer event (${mixedFrames} frames in ${mixedElapsed.toFixed(0)} ms)`);
  window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'W' });
  try {
    await new Promise(resolve => setTimeout(resolve, 80));
    const beforeBlur = await run('document.getElementById("edgeDock").focus(); return w.camera.position.toArray();');
    await new Promise(resolve => setTimeout(resolve, 120));
    assert.deepEqual(await run('return w.camera.position.toArray();'), beforeBlur, 'UI focus stops held movement after mixed input');
  } finally {
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'W' });
  }
  const cameraCoverage = { frames: mixedFrames, elapsed_ms: Math.round(mixedElapsed),
    distance: Number(mixedDistance.toFixed(3)), turn_radians: Number(mixedTurn.toFixed(3)) };
  await run('w.rig.home(); w.updateMode(); document.getElementById("edgeDock").click();');
  window.setSize(...initialSize);
  await new Promise(resolve => setTimeout(resolve, 180));
  await run('w.resize();');

  if (avatarPath) {
    // Hit-test a real character at its projected chest, using native pointer events.
    const characterPoint = () => run(`const a=w.avatars[0]; w.scene.updateMatrixWorld(true);
      const p=a.vrm.humanoid.getRawBoneNode('chest').getWorldPosition(w.camera.position.clone()).project(w.camera);
      return { x: Math.round((p.x+1)*innerWidth/2), y: Math.round((1-p.y)*innerHeight/2) };`);
    const clickAt = point => {
      window.webContents.sendInputEvent({ type: 'mouseDown', ...point, button: 'left', clickCount: 1 });
      window.webContents.sendInputEvent({ type: 'mouseUp', ...point, button: 'left', clickCount: 1 });
    };
    const uiPoint = selector => run(`const node=document.querySelector(${JSON.stringify(selector)});
      const rect=node.getBoundingClientRect(); const x=Math.round(rect.left+rect.width/2), y=Math.round(rect.top+rect.height/2);
      if (!rect.width || !rect.height || !node.contains(document.elementFromPoint(x,y))) throw new Error('World smoke UI target is covered');
      return {x,y};`);
    clickAt(await characterPoint());
    await wait(`(async () => Boolean((${world}).rig.focus))()`, 'click Focus');

    // Only World background clicks release Focus. Native events target this
    // isolated WebContents, so they never move the user's OS pointer or focus.
    const focusedResident = await run('return w.rig.focus.id;');
    clickAt(await uiPoint('#settingsButton'));
    await wait("!document.getElementById('residentSettingsPanel').hidden", 'Focus settings open');
    assert.equal(await run('return w.rig.focus?.id;'), focusedResident, 'opening UI keeps camera Focus');
    clickAt(await uiPoint('#residentSettingsClose'));
    await wait("document.getElementById('residentSettingsPanel').hidden", 'Focus settings close');
    assert.equal(await run('return w.rig.focus?.id;'), focusedResident, 'closing UI keeps camera Focus');

    const backgroundPoint = () => run(`
      const {Raycaster,Vector2}=await import('../../node_modules/three/build/three.module.js');
      w.scene.updateMatrixWorld(true); w.camera.updateMatrixWorld();
      const rect=w.canvas.getBoundingClientRect(), ray=new Raycaster();
      for (const fx of [.86,.74,.94,.62,.5,.3,.1]) for (const fy of [.3,.5,.7,.9,.1]) {
        const x=Math.round(rect.left+rect.width*fx), y=Math.round(rect.top+rect.height*fy);
        if (document.elementFromPoint(x,y)!==w.canvas) continue;
        ray.setFromCamera(new Vector2((x-rect.left)/rect.width*2-1,1-(y-rect.top)/rect.height*2),w.camera);
        if (!ray.intersectObjects(w.avatars.map(avatar=>avatar.root),true).length) return {x,y};
      }
      throw new Error('World smoke cannot find uncovered background');`);
    const orbitPoint = await backgroundPoint();
    const beforeOrbit = await run('return w.camera.quaternion.toArray();');
    window.webContents.sendInputEvent({ type: 'mouseDown', ...orbitPoint, button: 'right', clickCount: 1 });
    try {
      window.webContents.sendInputEvent({ type: 'mouseMove', x: orbitPoint.x + 20, y: orbitPoint.y + 12,
        button: 'right', modifiers: ['rightbuttondown'] });
    } finally {
      window.webContents.sendInputEvent({ type: 'mouseUp', x: orbitPoint.x + 20, y: orbitPoint.y + 12, button: 'right', clickCount: 1 });
    }
    await wait(`(async () => { const w=${world}; return w.camera.quaternion.toArray().some((value,index)=>Math.abs(value-${JSON.stringify(beforeOrbit)}[index])>1e-7); })()`, 'focused right drag applied');
    assert.equal(await run('return w.rig.focus?.id;'), focusedResident, 'right drag keeps camera Focus');
    const wheelPoint = await backgroundPoint();
    const beforeZoom = await run('return w.rig.distance;');
    window.webContents.sendInputEvent({ type: 'mouseMove', ...wheelPoint });
    window.webContents.sendInputEvent({ type: 'mouseWheel', ...wheelPoint, deltaX: 0, deltaY: -90 });
    await wait(`(async () => Math.abs((${world}).rig.distance-${beforeZoom})>1e-7)()`, 'focused wheel applied');
    assert.equal(await run('return w.rig.focus?.id;'), focusedResident, 'wheel keeps camera Focus');

    const releasePoint = await backgroundPoint();
    const beforeRelease = await run('w.input.clear(); return {position:w.camera.position.toArray(),rotation:w.camera.quaternion.toArray()};');
    clickAt(releasePoint);
    await wait(`(async () => (${world}).rig.focus === null)()`, 'background click releases Focus');
    assert.deepEqual(await run('return {position:w.camera.position.toArray(),rotation:w.camera.quaternion.toArray()};'), beforeRelease,
      'background release preserves camera position and orientation');
    assert.equal(await js("document.getElementById('worldCanvas').dataset.focusResident"), '');

    // Restore the original viewing pose before the existing bounded-gaze checks.
    await run('w.input.clear(); w.rig.home(); w.updateMode(); w.render();');
    clickAt(await characterPoint());
    await wait(`(async () => (${world}).rig.focus?.id === ${JSON.stringify(focusedResident)})()`, 'click refocus before gaze');
    const body = await run('const a=w.avatars[0]; return [a.root.quaternion.toArray(),a.vrm.scene.quaternion.toArray(),a.vrm.humanoid.getNormalizedBoneNode("hips").quaternion.toArray()];');
    await run('w.rig.rotate(-120, -40); for(let i=0;i<90;i++) w.render(.016);');
    const gaze = await run('const a=w.avatars[0]; return {yaw:a.gaze.yaw,pitch:a.gaze.pitch,body:[a.root.quaternion.toArray(),a.vrm.scene.quaternion.toArray(),a.vrm.humanoid.getNormalizedBoneNode("hips").quaternion.toArray()]};');
    assert.ok(Math.abs(gaze.yaw) > .05 && Math.abs(gaze.yaw) < .62, 'head follows within bounds');
    assert.deepEqual(gaze.body, body, 'gaze must not spin the body');
    await capture('world-focus');
    await run('w.rig.yaw=Math.PI; w.rig.pitch=0; w.rig.updateFocus(); for(let i=0;i<200;i++) w.render(.02);');
    assert.ok(await run('return Math.abs(w.avatars[0].gaze.yaw) < .01;'), 'camera behind the body releases gaze');
    await capture('world-behind');
    await run('w.rig.home(); w.updateMode(); w.render();');

    let observed = await waitSnapshot(s => s.avatar_states?.find(a => a.resident_id === 'holo' && a.status === 'ready'), 'avatar capabilities observed');
    const created = await master('CreateTask', { resident_id: 'holo' });
    await master('SendConversationMessage', { task_id: created.task_id, sender: 'master', content: 'hold: Avatar self-expression verification' }, created.task.revision);
    const turn = await waitSnapshot(s => s.holo_turns.find(t => t.task_id === created.task_id && !t.ended_at), 'bound Resident turn');
    const invoke = async (operation, input) => {
      const accepted = await controlCommand(join(process.env.NIRAI_V2_SMOKE_DATA_ROOT, 'control', 'connection.json'), turn.id, {
        protocol_version: 1, command_id: randomUUID(), issued_at: new Date().toISOString(), target: null,
        type: 'InvokeCapability', payload: { capability_id: 'avatar', operation, input },
      });
      return waitSnapshot(s => s.runs.find(r => r.id === accepted.run_id && ['Completed', 'Failed'].includes(r.state)), operation);
    };
    const inspected = await invoke('inspect', {});
    assert.equal(inspected.state, 'Completed');
    if (process.env.NIRAI_V2_WORLD_SMOKE_APPEARANCE === '1') {
      observed = await runAppearanceSmoke({ run, invoke, waitSnapshot, capture, saveAvatar, observed });
    }
    const expression = observed.capabilities.expressions.find(e => e.id === 'happy') ?? observed.capabilities.expressions[0];
    const appearance = structuredClone(observed.desired.appearance);
    if (expression) appearance.expression = { id: expression.id, weight: expression.is_binary ? 1 : .7 };
    const control = observed.capabilities.controls?.find(control => control.category === 'outfit');
    if (control) appearance.choices[control.id] = control.options.find(option => option.id !== control.default_option).id;
    const item = observed.capabilities.wardrobe.find(w => w.removable && ['accessory', 'hair_accessory'].includes(w.category));
    if (item) appearance.wardrobe[item.id] = !appearance.wardrobe[item.id];
    if (process.env.NIRAI_V2_WORLD_SMOKE_WARDROBE === '1') assert.ok(item, 'fixture must expose a real wardrobe item');
    const hiddenNodes = () => run('let count=0;w.avatars[0].root.traverse(o=>{if(!o.visible)count++;});return count;');
    const hiddenBefore = await hiddenNodes();
    const selected = await invoke('set', { model_id: observed.model_id, expected_revision: observed.desired.revision, appearance });
    assert.equal(selected.state, 'Completed', selected.error_json ?? 'selection saved');
    const selection = JSON.parse(selected.result_json).value;
    assert.equal(selection.desired_saved, true);
    assert.equal(selection.display_applied, false, 'saving must not pretend the screen has applied it');
    await waitSnapshot(s => s.avatar_states?.some(a => a.resident_id === 'holo' && a.applied_revision === selected.id && a.status === 'ready'), 'actual expression/wardrobe applied');
    savedAppearance = { revision: selected.id, appearance };
    assert.deepEqual(await run('return w.avatars[0].appearance;'), appearance);
    if (expression) assert.ok(await run(`return Math.abs(w.avatars[0].vrm.expressionManager.getValue(${JSON.stringify(expression.id)}) - ${appearance.expression.weight}) < .001;`));
    if (item) assert.equal(await hiddenNodes(), hiddenBefore + (appearance.wardrobe[item.id] ? -1 : 1), 'real wardrobe node visibility changed');
    appearanceCoverage = { expressions: observed.capabilities.expressions.length, wardrobe: observed.capabilities.wardrobe.length,
      controls: observed.capabilities.controls?.length ?? 0, changed_item: item?.id ?? null, changed_control: control?.id ?? null };
    await capture('world-appearance');
    await run('document.getElementById("worldMotion").click();');
    const pausedChoice = { ...appearance, expression: null };
    const pausedSelection = await invoke('set', { model_id: observed.model_id, expected_revision: selected.id, appearance: pausedChoice });
    assert.equal(pausedSelection.state, 'Completed');
    await waitSnapshot(s => s.avatar_states?.some(a => a.applied_revision === pausedSelection.id && a.display_applied), 'selection while motion paused');
    assert.equal(await run('return w.frame;'), 0, 'changing expression does not restart paused animation');
    savedAppearance = { revision: pausedSelection.id, appearance: pausedChoice };
    await run('document.getElementById("worldMotion").click();');
    await master('PauseTask', { task_id: created.task_id });
    await assert.rejects(() => invoke('set', { model_id: observed.model_id, expected_revision: selected.id, appearance }), /Turn|turn|running|expired/i);

    await run("document.getElementById('collapseButton').click();");
    // Sustained real key input is confined to the canvas, and stops at blur.
    await run('w.canvas.focus();');
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'E' });
    await new Promise(resolve => setTimeout(resolve, 250));
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'E' });
    assert.ok(await run('return w.camera.position.y > 1.40;'), 'free movement input');
    const beforeUi = await run('w.input.clear(); document.getElementById("edgeDock").focus(); return w.camera.position.toArray();');
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'W' });
    await new Promise(resolve => setTimeout(resolve, 150));
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'W' });
    assert.deepEqual(await run('return w.camera.position.toArray();'), beforeUi, 'UI keys must not move camera');

    // Missing file after a successful load removes stale avatar and keeps the sea/UI usable.
    await saveAvatar(avatarPath + '.missing.vrm');
    await wait(`document.getElementById('worldStatus').textContent.includes('選び直して')`, 'missing model fallback');
    assert.equal(await run('return w.avatars.length;'), 0);
    await saveAvatar(avatarPath);
    await wait(`(async () => (${world}).avatars.length === 1)()`, 'replace model after failure');
    await assertRelaxedArms('model replacement');
    window.webContents.reload();
    await wait(`(async () => (${world}).avatars.length === 1)()`, 'reload restores saved model');
    await waitSnapshot(s => s.avatar_states?.some(a => a.resident_id === 'holo' && a.applied_revision === savedAppearance.revision && a.status === 'ready'), 'reload restores Resident choice');
    assert.deepEqual(await run('return w.avatars[0].appearance;'), savedAppearance.appearance);
    await assertRelaxedArms('reload');

    // The normal Main recovery path restarts the Hub while World keeps its canvas.
    // Both model reads and appearance observations must bind to the new connection.
    const previousToken = await run('return w.avatars[0].token;');
    interruptNextReply();
    await js(`window.niraiDashboard.command({protocol_version:1,command_id:${JSON.stringify(randomUUID())},issued_at:new Date().toISOString(),
      type:'PauseTask',target:null,payload:{task_id:${JSON.stringify(created.task_id)}}}).catch(()=>{});`);
    await waitSnapshot(s => s.avatar_states?.some(a => a.resident_id === 'holo' && a.token && a.token !== previousToken
      && a.applied_revision === savedAppearance.revision && a.display_applied), 'Hub restart restores Avatar observation');
    assert.deepEqual(await run('return w.avatars[0].appearance;'), savedAppearance.appearance);
    await assertRelaxedArms('Hub restart');
    const stale = await run(`return window.niraiDashboard.reportAvatar('holo',${JSON.stringify(previousToken)},{status:'ready',capabilities:w.avatars[0].catalog,applied_revision:null});`);
    assert.equal(stale.accepted, false, 'old renderer load cannot report over the replacement');
  }
  await run('window.worldSmokeContext = w.renderer.getContext().getExtension("WEBGL_lose_context"); window.worldSmokeContext.loseContext();');
  await wait(`(async () => (${world}).lost && (${world}).canvas.hidden)()`, 'context loss fallback');
  assert.equal(await js("Boolean(window.niraiDashboard) && !document.getElementById('settingsButton').disabled"), true);
  await capture('world-context-lost');
  await run('window.worldSmokeContext.restoreContext();');
  await wait(`(async () => !(${world}).lost && !(${world}).canvas.hidden)()`, 'context restored');
  const restoredFrame = await run('return w.frames;');
  await wait(`(async () => (${world}).frames > ${restoredFrame + 3})()`, 'restored rendering');
  await capture('world-restored');
  if (avatarPath) await assertRelaxedArms('context restore');
  await run('document.getElementById("worldMotion").click();');
  await wait(`(async () => (${world}).frame === 0)()`, 'pause frame settled');
  const stopped = await run('return w.frames;');
  await new Promise(resolve => setTimeout(resolve, 200));
  assert.equal(await run('return w.frames;'), stopped, 'paused background consumes no animation frames');
  // Paused recovery has no animation loop to repair a skipped first frame. Check the presented image, not only flags.
  const beforeRecovery = await capture('world-paused');
  await run('window.worldSmokeContext.loseContext();');
  await wait(`(async () => (${world}).lost && (${world}).canvas.hidden)()`, 'paused context loss');
  await run('window.worldSmokeContext.restoreContext();');
  await wait(`(async () => !(${world}).lost && !(${world}).canvas.hidden && (${world}).frame === 0)()`, 'paused context restored and drawn');
  const afterRecovery = await capture('world-paused-restored');
  if (avatarPath) await assertRelaxedArms('paused context restore');
  assert.deepEqual(afterRecovery.getSize(), beforeRecovery.getSize(), 'restored image size');
  const { width, height } = beforeRecovery.getSize();
  const beforePixels = beforeRecovery.toBitmap(), afterPixels = afterRecovery.toBitmap();
  let difference = 0, samples = 0;
  for (let y = Math.floor(height * .25); y < height * .9; y += 5) {
    for (let x = Math.floor(width * .76); x < width * .96; x += 5) {
      const pixel = (y * width + x) * 4;
      for (let channel = 0; channel < 3; channel++) {
        difference += Math.abs(beforePixels[pixel + channel] - afterPixels[pixel + channel]); samples++;
      }
    }
  }
  assert.ok(difference / samples < 3, `paused World must be presented after recovery (pixel difference ${difference / samples})`);
  assert.equal(await run('return w.frame;'), 0, 'recovery preserves paused motion');
  const size = await run('return {calls:w.renderer.info.render.calls,triangles:w.renderer.info.render.triangles,textures:w.renderer.info.memory.textures,geometries:w.renderer.info.memory.geometries};');
  await run('w.dispose();');
  assert.ok(await run('return w.frame === 0 && w.avatars.length === 0;'), 'cleanup');
  assert.deepEqual(errors, [], 'no shader or renderer errors');
  const avatarChecks = avatarPath
    ? `passed (VRM load, relaxed arms across replacement/reload/Hub restart/context recovery, Focus, UI/drag/wheel Focus retention, background release without pose change, bounded gaze, free movement, UI input separation, Resident choice, missing model fallback, reload, Hub restart); appearance=${JSON.stringify(appearanceCoverage)}`
    : 'skipped (NIRAI_V2_WORLD_SMOKE_AVATAR is not set)';
  console.log(`World smoke passed: ${JSON.stringify(size)}; common=rendering, resize, sea viewpoints, native mixed camera input, blur, context recovery, paused context recovery, pause, disposal; camera=${JSON.stringify(cameraCoverage)}; avatar=${avatarChecks}`);
  await finish();
}
