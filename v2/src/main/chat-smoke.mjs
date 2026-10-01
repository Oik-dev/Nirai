import assert from 'node:assert/strict';

// Only the separate UI-smoke Data Root is used. No external AI is called here.
export async function checkChatMode(window, { request, js, waitFor, capture }) {
  const frame = () => js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const snapshot = () => request('snapshot');
  const chatBox = () => js("(() => {const r=document.getElementById('usualChat').getBoundingClientRect(); return {left:r.left,bottom:r.bottom,width:r.width,height:r.height};})()");
  const dragGrip = async (id, dx, dy, afterDown) => {
    const before = await chatBox();
    const grip = await js(`(() => {const h=document.getElementById('${id}'),r=h.getBoundingClientRect(); h.addEventListener('pointerdown',event=>h.dataset.smokePointerId=String(event.pointerId),{once:true}); return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()`);
    window.webContents.sendInputEvent({ type: 'mouseDown', x: grip.x, y: grip.y, button: 'left', clickCount: 1 });
    await frame();
    if (afterDown) await afterDown();
    for (let step = 1; step <= 8; step++) {
      window.webContents.sendInputEvent({ type: 'mouseMove', x: Math.round(grip.x + dx * step / 8), y: Math.round(grip.y + dy * step / 8), button: 'left', modifiers: ['leftbuttondown'] });
      await new Promise(resolve => setTimeout(resolve, 8));
    }
    window.webContents.sendInputEvent({ type: 'mouseUp', x: grip.x + dx, y: grip.y + dy, button: 'left', clickCount: 1 });
    await frame();
    await js(`delete document.getElementById('${id}').dataset.smokePointerId`);
    return { before, after: await chatBox() };
  };
  const before = await snapshot();
  window.setSize(1500, 930);
  await js("setDashboardOpen(true); document.getElementById('settingsButton').click(); document.getElementById('addResidentButton').click();");
  // Provider metadata is a display fixture; this identifier has no executable AI.
  await js(`(() => {
    snapshot = {...snapshot, conversation_providers:[...snapshot.conversation_providers, {id:'ui-models-fixture', display_name:'UI検証用AI', models:[{id:'ui-model-a', display_name:'UI Model A'}], availability:{state:'ready'}}]};
    renderResidentSettings();
    const provider=document.getElementById('newResidentProvider'); provider.value='ui-models-fixture'; provider.dispatchEvent(new Event('change',{bubbles:true}));
    document.getElementById('newResidentModel').value='ui-model-a';
    const n=document.getElementById('newResidentName'); n.value='Chat検証'; n.dispatchEvent(new Event('input',{bubbles:true}));
  })()`);
  assert.equal(await js("document.getElementById('newResidentModel').selectedOptions[0].textContent"), 'UI Model A', 'new Resident displays provider Model metadata');
  for (const [width, height] of [[360, 600], [620, 980]]) {
    window.setSize(width, height); await frame();
    assert.equal(await js("(() => {const form=document.getElementById('newResidentForm'),modal=document.querySelector('.resident-settings-modal'),r=modal.getBoundingClientRect();return form.scrollWidth<=form.clientWidth&&r.top>=0&&r.bottom<=innerHeight;})()"), true, 'Resident connection controls fit at ' + width);
    await capture('resident-create-' + width + '.png');
  }
  window.setSize(1500, 930);
  await js("document.getElementById('newResidentForm').requestSubmit();");
  await waitFor(async () => (await snapshot()).residents.some(item => item.display_name === 'Chat検証'), 'Resident creation from settings');
  const resident = (await snapshot()).residents.find(item => item.display_name === 'Chat検証');
  assert.equal(resident.capability_id, 'ui-models-fixture', 'new Resident connection is saved through Hub');
  assert.equal(resident.model, 'ui-model-a', 'new Resident Model is saved through Hub');
  await waitFor(() => js("document.getElementById('dashboard').getAttribute('aria-busy') !== 'true'"), 'Resident command idle');
  assert.equal(await js(`document.querySelector('[data-resident-setting="${resident.id}"][data-field="model"]').value`), 'ui-model-a', 'saved Model survives unavailable metadata');
  const refreshRejected = await js("window.niraiDashboard.refreshConversationProvider('ui-models-fixture').then(()=>false,()=>true)");
  assert.equal(refreshRejected, true, 'limited refresh IPC rejects an unregistered provider');
  await js("document.getElementById('residentSettingsClose').click(); document.getElementById('collapseButton').click();");
  await waitFor(() => js("!document.getElementById('usualChat').hidden"), 'ChatMode shown');
  assert.equal(await js("Boolean(document.getElementById('worldFocus'))"), false, 'no Focus-release button');
  assert.equal(await js("Boolean(document.querySelector('[data-channel=\"whisper:holo\"]'))"), true, 'Holo is a normal channel');
  await js("window.dispatchEvent(new CustomEvent('nirai:world-focus',{detail:{resident_id:'holo'}}));");
  assert.equal(await js("document.querySelector('#usualChatChannels [aria-selected=true]').dataset.channel"), 'whisper:holo', 'Holo Focus opens Whisper');
  await js("document.querySelector('[data-channel=say]').click(); document.getElementById('chatSettingsButton').click(); const opener=document.querySelector('[data-holo-open]'); opener.focus(); opener.click();");
  await waitFor(() => js("!document.getElementById('holoChatPanel').hidden"), 'Task-free ChatGPT settings surface');
  assert.equal((await snapshot()).tasks.length, before.tasks.length, 'opening ChatGPT must not create a Task');
  for (const [width, height] of [[360, 600], [620, 980], [1500, 930]]) {
    window.setSize(width, height);
    await frame();
    assert.equal(await js("(() => {const r=document.getElementById('holoChatSurface').getBoundingClientRect(),c=document.getElementById('holoChatClose').getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&c.right<=innerWidth&&c.top>=0;})()"), true, 'Task-free ChatGPT fits at ' + width);
    await capture('holo-chat-settings-' + width + '.png');
  }
  await js("document.getElementById('holoChatClose').click();");
  assert.equal(await js("document.activeElement.hasAttribute('data-holo-open')"), true, 'ChatGPT closes back to its settings trigger');
  await js("document.getElementById('residentSettingsClose').click();");
  const say = '  公開の文章\n改行をそのまま保存';
  await js(`(() => {const i=document.getElementById('usualChatInput'); i.value=${JSON.stringify(say)}; i.dispatchEvent(new Event('input',{bubbles:true})); document.getElementById('usualChatForm').requestSubmit();})()`);
  await waitFor(async () => (await snapshot()).messages.some(item => item.content === say), 'Say saved from ChatMode');
  await waitFor(() => js("[...document.querySelectorAll('.usual-response-status')].some(item => item.textContent.includes('Chat検証') && item.textContent.includes('未接続'))"), 'honest offline response');
  const afterSay = await snapshot();
  assert.equal(afterSay.tasks.length, before.tasks.length, 'chat must not create a Task');
  assert.equal(afterSay.runs.length, before.runs.length, 'chat must not grant Action authority');
  assert.equal(afterSay.messages.filter(item => item.content === say).length, 1, 'one exact saved input');
  const record = afterSay.messages.find(item => item.content === say);
  assert.ok(record.audience.includes('holo'), 'Holo directly hears Say');
  const expectedTime = new Date(record.created_at).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
  assert.equal(await js(`document.querySelector('[data-message-id="${record.id}"] time').textContent`), expectedTime);
  await js(`document.querySelector('[data-channel="whisper:${resident.id}"]').click();`);
  const secret = 'ここだけの会話';
  await js(`(() => {const i=document.getElementById('usualChatInput'); i.value=${JSON.stringify(secret)}; i.dispatchEvent(new Event('input',{bubbles:true})); document.getElementById('usualChatForm').requestSubmit();})()`);
  await waitFor(async () => (await snapshot()).messages.some(item => item.content === secret), 'Whisper saved');
  await waitFor(() => js("!document.getElementById('usualChatInput').disabled"), 'Whisper send idle');
  await js("document.querySelector('[data-channel=say]').click();");
  assert.equal(await js(`document.getElementById('usualChatMessages').textContent.includes(${JSON.stringify(secret)})`), false, 'Whisper never shown in Say');

  await js(`window.dispatchEvent(new CustomEvent('nirai:world-focus',{detail:{resident_id:${JSON.stringify(resident.id)}}}));`);
  assert.equal(await js("document.querySelector('#usualChatChannels [aria-selected=true]').dataset.channel"), `whisper:${resident.id}`, 'empty input Focus opens Whisper');
  await js("(() => {document.querySelector('[data-channel=say]').click(); const i=document.getElementById('usualChatInput'); i.value='Sayの下書き'; i.dispatchEvent(new Event('input',{bubbles:true}));})()");
  await js(`window.dispatchEvent(new CustomEvent('nirai:world-focus',{detail:{resident_id:${JSON.stringify(resident.id)}}}));`);
  assert.equal(await js("document.querySelector('#usualChatChannels [aria-selected=true]').dataset.channel"), 'say', 'Focus never reroutes an existing draft');
  await js(`(() => {document.querySelector('[data-channel="whisper:${resident.id}"]').click(); const i=document.getElementById('usualChatInput'); i.value='Whisperの下書き'; i.dispatchEvent(new Event('input',{bubbles:true})); setDashboardOpen(true); setDashboardOpen(false);})()`);
  assert.equal(await js("document.getElementById('usualChatInput').value"), 'Whisperの下書き');
  await js("window.dispatchEvent(new CustomEvent('nirai:world-focus',{detail:{resident_id:null}}));");
  assert.equal(await js("document.querySelector('#usualChatChannels [aria-selected=true]').dataset.channel"), `whisper:${resident.id}`, 'background preserves Whisper');
  await js("document.querySelector('[data-channel=say]').click();");
  assert.equal(await js("document.getElementById('usualChatInput').value"), 'Sayの下書き', 'draft isolation');
  assert.ok(Math.abs((await chatBox()).width - 528) <= 1, 'ChatMode starts at 1.2 times the previous width');
  await capture('chatmode-initial-1500.png');

  // Presentation-only history tests never invent a reply in Hub storage.
  await js(`(async () => {
    const original = snapshot;
    const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve));
    const list = document.getElementById('usualChatMessages');
    const conversation = original.conversations.find(item => item.kind === 'say');
    const firstDay = new Date(); firstDay.setDate(firstDay.getDate() - 1);
    try {
      const history = Array.from({length:30}, (_, index) => ({
        id:'chat-history-' + index, conversation_id:conversation.id, seq:index + 1,
        sender:index % 2 === 0 ? 'master' : ${JSON.stringify(resident.id)}, content:'過去の文章を読んでいる間の表示確認。'.repeat(12),
        created_at:(index < 15 ? firstDay : new Date()).toISOString(), audience:[], reply_to_message_id:null,
      }));
      snapshot = {...original, messages:history, chat_responses:[]};
      usualConversation.render(); await nextFrame();
      if (list.querySelectorAll('.usual-chat-date').length !== 2) throw new Error('saved dates must split history');
      const master = list.querySelector('.usual-message.master'), agent = list.querySelector('.usual-message.agent');
      if (master.getBoundingClientRect().left <= agent.getBoundingClientRect().left + 10) throw new Error('Master must appear to the right of Resident');
      if (master.getBoundingClientRect().right <= agent.getBoundingClientRect().right + 10) throw new Error('Resident must appear to the left of Master');
      if ([master, agent].some(item => getComputedStyle(item.querySelector('.usual-message-text')).textAlign !== 'left')) throw new Error('Japanese message bodies must stay left aligned');
      list.scrollTop = 90;
      list.dispatchEvent(new Event('scroll'));
      usualConversation.render(); await nextFrame();
      if (list.scrollTop !== 90) throw new Error('unchanged history moved reading position');
      snapshot.messages = [...history, {...history.at(-1), id:'chat-history-new', seq:31}];
      usualConversation.render(); await nextFrame();
      if (list.scrollTop !== 90) throw new Error('new history interrupted reading');
      document.querySelector('[data-channel="whisper:${resident.id}"]').click(); await nextFrame();
      document.querySelector('[data-channel=say]').click(); await nextFrame();
      if (list.scrollTop !== 90) throw new Error('channel change lost reading position');
      setDashboardOpen(true); setDashboardOpen(false); await nextFrame();
      if (list.scrollTop !== 90) throw new Error('TaskMode change lost reading position');
      const viewport = list.getBoundingClientRect();
      const anchor = [...list.querySelectorAll('[data-message-id]')].find(item => item.getBoundingClientRect().bottom > viewport.top);
      const offset = anchor.getBoundingClientRect().top - viewport.top;
      const widthGrip = document.getElementById('usualChatResizeWidth');
      const priorWidth = document.getElementById('usualChat').getBoundingClientRect().width;
      widthGrip.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})); await nextFrame();
      if (Math.abs(document.getElementById('usualChat').getBoundingClientRect().width - priorWidth - 24) > 1) throw new Error('width grip must support arrow keys');
      if (Math.abs(anchor.getBoundingClientRect().top - list.getBoundingClientRect().top - offset) > 1) throw new Error('width reflow moved the visible message');
      list.scrollTop = list.scrollHeight;
      document.getElementById('usualChatResizeCorner').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true})); await nextFrame();
      if (list.scrollHeight - list.clientHeight - list.scrollTop > 1) throw new Error('latest history must remain at the bottom when resized');
      widthGrip.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));
      document.getElementById('usualChatResizeCorner').dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true})); await nextFrame();
    } finally { snapshot = original; usualConversation.render(); await nextFrame(); }
  })()`);

  const heightDrag = await dragGrip('usualChatResize', 0, -80);
  assert.ok(heightDrag.after.height > heightDrag.before.height + 40, 'top handle drag resizes height');
  assert.equal(heightDrag.after.width, heightDrag.before.width, 'top handle only changes height');
  const widthDrag = await dragGrip('usualChatResizeWidth', 80, 0);
  assert.ok(widthDrag.after.width > widthDrag.before.width + 40, 'right edge drag resizes width');
  assert.equal(widthDrag.after.height, widthDrag.before.height, 'right edge only changes width');
  const cornerDrag = await dragGrip('usualChatResizeCorner', 60, -50);
  assert.ok(cornerDrag.after.width > cornerDrag.before.width + 30 && cornerDrag.after.height > cornerDrag.before.height + 30, 'upper-right corner drag changes both dimensions');
  for (const { before, after } of [heightDrag, widthDrag, cornerDrag]) {
    assert.equal(after.left, before.left, 'resize keeps the lower-left horizontal anchor');
    assert.equal(after.bottom, before.bottom, 'resize keeps the lower-left vertical anchor');
  }
  const blurDrag = await dragGrip('usualChatResizeWidth', 80, 0, () => js("window.dispatchEvent(new Event('blur'))"));
  assert.equal(blurDrag.after.width, blurDrag.before.width, 'window blur releases an unfinished drag');
  const modeDrag = await dragGrip('usualChatResizeCorner', 50, -50, () => js("setDashboardOpen(true); setDashboardOpen(false)"));
  assert.equal(modeDrag.after.width, modeDrag.before.width, 'TaskMode switch releases width drag');
  assert.equal(modeDrag.after.height, modeDrag.before.height, 'TaskMode switch releases height drag');
  const cancelDrag = await dragGrip('usualChatResizeWidth', 80, 0, () => js("(() => {const h=document.getElementById('usualChatResizeWidth'),id=Number(h.dataset.smokePointerId); if(!h.hasPointerCapture(id)) throw new Error('native width drag must hold pointer capture'); h.dispatchEvent(new PointerEvent('pointercancel',{pointerId:id}));})()"));
  assert.equal(cancelDrag.after.width, cancelDrag.before.width, 'pointer cancellation releases an unfinished drag');
  assert.equal(await js("(() => {const h=document.getElementById('usualChatResizeWidth'),m=document.getElementById('usualChatMessages'); return m.getBoundingClientRect().right <= h.getBoundingClientRect().left + 1 && h.getAttribute('aria-orientation') === 'vertical' && Number(h.getAttribute('aria-valuenow')) > 0;})()"), true, 'width grip avoids the history scrollbar and exposes its dimension');
  assert.equal(await js("document.getElementById('usualChatInput').value"), 'Sayの下書き', 'resize keeps draft');
  const chosenSize = await chatBox();
  await js("document.getElementById('usualChat').style.width='1px'; window.dispatchEvent(new Event('resize'));");
  assert.equal((await chatBox()).width, chosenSize.width, 'invalid external width restores the finite selected size');

  for (const [width, height] of [[360, 600], [620, 980], [1500, 930]]) {
    window.setSize(width, height);
    await frame();
    const inside = await js("(() => {const p=document.getElementById('usualChat'),r=p.getBoundingClientRect(),i=document.getElementById('usualChatInput').getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight&&i.right<=r.right&&p.scrollWidth<=p.clientWidth;})()");
    assert.equal(inside, true, 'ChatMode is usable at ' + width);
    await capture('chatmode-' + width + '.png');
  }
  assert.equal((await chatBox()).width, chosenSize.width, 'widening the screen restores the chosen width');
  assert.equal((await chatBox()).height, chosenSize.height, 'raising the screen restores the chosen height');
  await js("document.getElementById('chatSettingsButton').click(); document.getElementById('residentSettingsClose').click();");
  assert.equal(await js("document.activeElement.id"), 'chatSettingsButton', 'settings returns to the ChatMode trigger');
  await js("setDashboardOpen(true)");
  console.log('ChatMode: real UI/Hub save, private channels, saved timestamps, offline state, Focus/draft separation, native height/width/corner drags and cancellation, three layouts, size recovery and settings focus passed; presentation fixture=left/right bubbles, left-aligned text, date breaks and anchored reading position');
}
