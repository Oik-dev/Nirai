import assert from 'node:assert/strict';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

// Optional real-model acceptance. Drives the existing Turn -> Capability -> Run
// -> saved choice -> renderer report path; no direct appearance writes in tests.
export async function runAppearanceSmoke({ run, invoke, waitSnapshot, capture, saveAvatar, observed }) {
  const controls = observed.capabilities.controls;
  assert.ok(controls?.length, 'appearance fixture must expose semantic controls');
  assert.deepEqual(await run('return w.avatars[0].warnings;'), []);
  const initial = structuredClone(observed.desired.appearance);
  let current = observed;
  const records = [];
  const paused = await run('return w.paused;');
  await run(`if(!w.paused) document.getElementById('worldMotion').click();
    if(!document.getElementById('collapseButton').hidden && document.getElementById('collapseButton').getBoundingClientRect().width) document.getElementById('collapseButton').click();
    w.rig.home(); w.resize();`);
  const view = async (name, angle = 'front') => {
    await run(`w.rig.unlock(); w.input.clear();
      const a=w.avatars[0]; const p=a.root.position;
      w.camera.clearViewOffset();
      w.camera.position.set(p.x, p.y+${angle === 'face' ? '1.39' : '.88'}, p.z+${angle === 'back' ? '-2.5' : angle === 'face' ? '.64' : '2.5'});
      w.camera.lookAt(p.x,p.y+${angle === 'face' ? '1.39' : '.80'},p.z);
      w.rig.readAngles(); w.render(0);`);
    await capture(name);
  };
  const select = async (appearance, name) => {
    const result = await invoke('set', { model_id: current.model_id, expected_revision: current.desired.revision, appearance });
    assert.equal(result.state, 'Completed', result.error_json ?? name);
    assert.equal(JSON.parse(result.result_json).value.display_applied, false);
    current = await waitSnapshot(s => s.avatar_states?.find(a => a.resident_id === observed.resident_id
      && a.applied_revision === result.id && a.display_applied), name);
    assert.equal(await run('return w.avatars[0].settled;'), true, 'actual mesh/morph values match the selection');
    assert.deepEqual(await run('return w.avatars[0].appearance;'), appearance);
    records.push({ name, revision: result.id, display_applied: current.display_applied, appearance });
    return result;
  };
  const reference = process.env.NIRAI_V2_WORLD_SMOKE_REFERENCE;
  if (reference) {
    await saveAvatar(reference);
    await waitSnapshot(s => s.avatar_states?.find(a => a.model_path === reference && a.status === 'ready' && a.display_applied), 'reference model');
    for (const angle of ['front','back','face']) await view(`appearance-reference-${angle}`,angle);
    await saveAvatar(observed.model_path);
    current = await waitSnapshot(s => s.avatar_states?.find(a => a.model_id === observed.model_id && a.status === 'ready' && a.display_applied), 'restore candidate');
  }
  for (const control of controls.filter(control => control.category === 'outfit')) {
    // Forward and reverse order both matter: an outfit is a complete state.
    for (const option of [...control.options, ...control.options.toReversed()]) {
      const appearance = structuredClone(initial);
      appearance.choices[control.id] = option.id;
      const name = `appearance-${control.id}-${option.id}`.replace(/[^a-zA-Z0-9_-]/g,'_');
      await select(appearance,name);
      for (const angle of ['front','back','face']) await view(`${name}-${angle}`,angle);
    }
  }
  // Exercise every published option, including independent composition with each outfit.
  for (const outfit of controls.filter(control => control.category === 'outfit').flatMap(control => control.options.map(option => [control.id,option.id]))) {
    for (const control of controls.filter(control => control.category !== 'outfit')) {
      for (const option of control.options) {
        const appearance=structuredClone(initial);
        appearance.choices[outfit[0]]=outfit[1];
        appearance.choices[control.id]=option.id;
        await select(appearance,`${outfit[1]}-${control.id}-${option.id}`);
        if (control.category === 'body') await view(`appearance-${outfit[1]}-${control.id}-${option.id}`);
      }
    }
  }
  const accessoriesOff=structuredClone(initial);
  for (const control of controls.filter(control => ['accessory','hair_accessory'].includes(control.category))) {
    accessoriesOff.choices[control.id]=control.options.find(option=>option.id!==control.default_option).id;
  }
  await select(accessoriesOff,'all-accessories-alternate');
  await view('appearance-accessories-alternate');
  await select(initial,'restore-default');
  await view('appearance-restored-front');
  await run(`w.rig.home(); w.resize(); w.render(0);
    if(${!paused} && w.paused) document.getElementById('worldMotion').click();
    if(document.getElementById('edgeDock').getBoundingClientRect().width) document.getElementById('edgeDock').click();`);
  const directory=process.env.NIRAI_V2_UI_CAPTURE_DIR;
  if(directory) {
    mkdirSync(directory,{recursive:true});
    writeFileSync(join(directory,'appearance-evidence.json'),JSON.stringify({model_id:observed.model_id,capabilities:observed.capabilities,records},null,2));
  }
  return current;
}
