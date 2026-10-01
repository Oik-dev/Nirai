import assert from 'node:assert/strict'

// Presentation-only probes run in the isolated UI smoke application. They do
// not create Tasks, send messages or alter the Hub's state.
export async function checkPresentation(window, js, capture) {
  const frame = () => js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
  await js("if (!getSelectedTask()) document.querySelector('[data-task-toggle]').click(); taskListOpen = false; renderAll()")
  assert.equal(await js("getComputedStyle(document.getElementById('edgeDock')).display"), 'none', 'expanded Dashboard must not show a second open/close control at the screen edge')
  await js('setDashboardOpen(false)')
  assert.equal(await js("document.getElementById('edgeDock').getBoundingClientRect().left < 30"), true, 'collapsed Dashboard keeps its restore control on the Dashboard side')
  await js('setDashboardOpen(true)')
  for (const [width, height] of [[360, 600], [620, 980], [900, 600], [1500, 930], [1920, 1080]]) {
    window.setSize(width, height)
    await frame()
    const layout = await js(`(() => {
      const dashboard = document.getElementById('dashboard')
      const rect = node => node.getBoundingClientRect()
      const box = rect(dashboard)
      const chat = rect(document.getElementById('chatPane'))
      const surface = rect(document.getElementById('holoSurface'))
      const header = rect(document.querySelector('.chat-pane-header'))
      const painted = [...dashboard.querySelectorAll('*')].filter(node => node.getClientRects().length && getComputedStyle(node).backdropFilter !== 'none')
      return {
        inside: box.left >= 0 && box.top >= 0 && box.right <= innerWidth && box.bottom <= innerHeight,
        overflow: dashboard.scrollWidth > dashboard.clientWidth,
        chatWidth: chat.width, height: surface.height,
        controlsOutside: surface.top >= header.bottom,
        childBlurs: painted.length,
        background: getComputedStyle(dashboard).backgroundColor,
      }
    })()`)
    if (width === 360 || width === 900) await capture(`layout-${width}.png`)
    assert.equal(layout.inside, true, `${width}x${height}: Dashboard stays in viewport`)
    assert.equal(layout.overflow, false, `${width}x${height}: no clipped horizontal content`)
    assert.equal(layout.controlsOutside, true, 'native view never covers Nirai controls')
    assert.equal(layout.childBlurs, 0, 'only the Dashboard blurs the World underneath')
    assert.match(layout.background, /^rgba\(/, 'the shared conversation glass remains transparent')
    assert.ok(layout.chatWidth >= 290 && layout.height >= 170, `${width}x${height}: usable conversation area ${JSON.stringify(layout)}`)
  }
  window.setSize(1500, 930)
  await frame()

  const editing = await js(`(async () => {
    const assert = (condition, message) => { if (!condition) throw new Error(message) }
    const nextFrame = () => new Promise(resolve => requestAnimationFrame(resolve))
    const fixture = document.createElement('div')
    document.body.append(fixture)
    try {
      const markup = title => '<article data-task-id="probe"><details><summary>Results</summary>' + title + '</details><button data-request-id="request" data-request-action="approve">Approve</button><button data-request-id="request" data-request-action="reject">Reject</button></article>'
      renderMarkup(fixture, markup('before'))
      fixture.querySelector('details').open = true
      fixture.querySelector('[data-request-action="reject"]').focus()
      renderMarkup(fixture, markup('after'))
      assert(document.activeElement.dataset.requestAction === 'reject', 'Snapshot refresh moved focus from Reject to Approve')
      assert(fixture.querySelector('details').open, 'Snapshot refresh collapsed the result')

      const task = id => ({id, resident_id:'fixture', title:'通常会話の表示確認', status:'Running', agents:[], updated:'現在', messages:[], draft:false})
      const first = task('ui-probe-a'), second = task('ui-probe-b')
      first.messages = Array.from({length:30}, (_, i) => ({role:i % 2 ? 'assistant' : 'master', who:i % 2 ? 'Resident' : 'Master', time:'12:00', text:'海中世界を背景に、文章を落ち着いて読み進めるための表示確認。 '.repeat(5)}))
      renderChat(first)
      await nextFrame()
      const input = document.getElementById('chatInput'), messages = document.getElementById('chatMessages')
      input.value = '送信前の文章を保持'
      messages.scrollTop = 90
      renderChat(first)
      await nextFrame()
      assert(messages.scrollTop === 90, 'Unchanged snapshot forced scroll to bottom')
      first.messages.push({role:'assistant', who:'Resident', time:'12:01', text:'追記された回答'})
      renderChat(first)
      await nextFrame()
      assert(messages.scrollTop === 90, 'New message interrupted reading older text')
      renderChat(second)
      input.value = '別Taskの下書き'
      renderChat(first)
      assert(input.value === '送信前の文章を保持', 'Task switch lost the draft')
      renderChat(second)
      assert(input.value === '別Taskの下書き', 'Drafts leaked across Tasks')
      renderChat(first)
      await nextFrame()
      return true
    } finally { fixture.remove() }
  })()`)
  assert.equal(editing, true)
  await capture('ordinary-conversation.png')
  await js("renderAll(); chatDrafts.delete('ui-probe-a'); chatDrafts.delete('ui-probe-b')")

  await js("setResidentSettingsOpen(true); document.getElementById('addResidentButton').focus(); window.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}))")
  assert.equal(await js("document.activeElement.id"), 'residentSettingsClose', 'Tab skips hidden notices and wraps inside settings')
  await js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true,cancelable:true}))")
  assert.equal(await js("document.activeElement.id"), 'addResidentButton', 'Shift+Tab wraps to last visible control')
  await js("window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));")
  assert.equal(await js("document.getElementById('residentSettingsPanel').hidden && document.activeElement.id === 'settingsButton'"), true, 'Escape restores focus to the settings button')
  console.log('UI presentation: five window sizes, transparent single glass, unobscured controls, draft isolation, reading position and keyboard focus passed')
}
