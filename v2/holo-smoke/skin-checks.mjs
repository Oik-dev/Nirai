import assert from 'node:assert/strict'

// Synthetic boundary cases derived from the live DOM audit.
// The contract is deliberately narrow: preserve unknown provider surfaces and
// only override elements that the Holo decorator/live audit identified.
export async function checkSkinSurfaces(js, wc) {
  wc.debugger.attach('1.3')
  try {
    await wc.debugger.sendCommand('Emulation.setFocusEmulationEnabled', { enabled:true })
    await runChecks(js, wc)
  } finally {
    wc.debugger.detach()
  }
}

async function runChecks(js, wc) {
  await js(`(() => {
    const root = document.createElement('div')
    root.id = 'skin-audit-fixture'
    root.innerHTML = \`
      <header>
        <div id="audit-mode" role="radiogroup">
          <div data-tpp-toggle-highlight></div>
          <button data-tpp-toggle-value="chatgpt">Chat</button>
          <button data-tpp-toggle-value="work">Work</button>
        </div>
        <button id="audit-keep">Keep header action</button>
      </header>

      <div id="audit-unknown" style="width:220px;height:40px;background:rgb(23,34,45);box-shadow:0 0 11px rgb(0,0,0);backdrop-filter:blur(7px)">Unknown provider surface</div>
      <div id="audit-translucent" class="translucent-surface" style="width:220px;height:40px;background:rgba(0,0,0,.6);box-shadow:0 0 18px rgb(0,0,0);backdrop-filter:blur(24px)">Audited floating surface</div>

      <div id="stage-popover-sidebar" role="dialog" style="position:fixed;right:0;top:0;width:240px;height:300px;background:black;box-shadow:0 0 64px black">
        <nav id="stage-sidebar-tiny-bar" inert><button>New chat</button><div data-testid="accounts-profile-button">Hidden account</div></nav>
        <div>
          <nav>
            <div id="audit-sidebar-header" style="position:sticky;top:0;background:black"><div id="sidebar-header">Header</div></div>
            <a href="#" data-sidebar-item id="audit-row">Selected row</a>
            <div id="audit-scroll" style="height:30px;overflow-y:auto"><div style="height:200px">History</div></div>
          </nav>
          <div id="audit-account" style="background:black"><div data-testid="accounts-profile-button" role="button" tabindex="0">Account</div></div>
        </div>
      </div>

      <svg id="audit-icon" width="12" height="12"><path fill="currentColor" d="M0 0h12v12H0z"/></svg>
      <img id="audit-image" alt="sample" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1' height='1'%3E%3C/svg%3E" style="filter:contrast(1.1)">
      <button id="audit-click" style="position:fixed;left:20px;bottom:20px;z-index:2147483647;width:120px;height:36px">Native click</button>
    \`
    document.body.append(root)
    document.getElementById('audit-click').addEventListener('click', () => { root.dataset.clicked = 'yes' })
    return true
  })()`)

  const result = await js(`(() => {
    const style = id => getComputedStyle(document.getElementById(id))
    const shell = document.querySelector('[data-nirai-holo-composer-shell]')
    const scroll = document.getElementById('audit-scroll')
    scroll.scrollTop = 20
    return {
      canvas: getComputedStyle(document.documentElement).backgroundColor,
      body: getComputedStyle(document.body).backgroundColor,
      mode: style('audit-mode').display,
      keep: style('audit-keep').display,
      shellId: shell?.id ?? null,
      shellBorder: shell ? getComputedStyle(shell).borderTopWidth : null,
      shellViewTransition: shell ? getComputedStyle(shell).viewTransitionName : null,

      unknown: {
        bg: style('audit-unknown').backgroundColor,
        shadow: style('audit-unknown').boxShadow,
        blur: style('audit-unknown').backdropFilter,
      },
      translucent: {
        bg: style('audit-translucent').backgroundColor,
        shadow: style('audit-translucent').boxShadow,
        blur: style('audit-translucent').backdropFilter,
      },

      sidebarMarked: document.getElementById('stage-popover-sidebar').hasAttribute('data-nirai-holo-sidebar'),
      railMarked: document.getElementById('stage-sidebar-tiny-bar').hasAttribute('data-nirai-holo-sidebar'),
      headerMarked: document.getElementById('audit-sidebar-header').hasAttribute('data-nirai-holo-sidebar-header'),
      footerMarked: document.getElementById('audit-account').hasAttribute('data-nirai-holo-sidebar-footer'),
      sidebar: {
        bg: style('stage-popover-sidebar').backgroundColor,
        shadow: style('stage-popover-sidebar').boxShadow,
        blur: style('stage-popover-sidebar').backdropFilter,
        viewTransition: style('stage-popover-sidebar').viewTransitionName,
      },
      headerBg: style('audit-sidebar-header').backgroundColor,
      footerBg: style('audit-account').backgroundColor,
      rowBg: style('audit-row').backgroundColor,

      scroll: scroll.scrollTop,
      scrollbar: style('audit-scroll').scrollbarColor,
      mediaFilter: style('audit-image').filter,
      iconDisplay: style('audit-icon').display,
      badToolbar: [...document.querySelectorAll('[data-nirai-holo-message-actions]')].some(n => n.contains(document.getElementById('prompt-textarea'))),
    }
  })()`)

  assert.equal(result.mode, 'none', 'hide the complete Chat/Work group')
  assert.equal(result.canvas, 'rgba(0, 0, 0, 0)', 'Holo must share the transparent World glass')
  assert.equal(result.body, 'rgba(0, 0, 0, 0)', 'Holo must not acquire an opaque conversation canvas')
  assert.notEqual(result.keep, 'none', 'keep unrelated header actions')

  assert.equal(result.shellId, 'composer-inner', 'theme the audited inner rounded composer surface')
  assert.equal(result.shellBorder, '1px')
  assert.equal(result.shellViewTransition, 'none', 'disable snapshots only on the owned composer surface')

  assert.equal(result.unknown.bg, 'rgb(23, 34, 45)', 'unknown provider surface background must remain provider-owned')
  assert.notEqual(result.unknown.shadow, 'none', 'unknown provider surface shadow must not be globally reset')
  assert.notEqual(result.unknown.blur, 'none', 'unknown provider surface blur must not be globally reset')

  assert.notEqual(result.translucent.bg, 'rgba(0, 0, 0, 0)', 'audited translucent surface gets a readable Nirai fill')
  assert.equal(result.translucent.shadow, 'none', 'audited translucent surface shadow is removed')
  assert.equal(result.translucent.blur, 'none', 'audited translucent surface blur is removed')

  assert.equal(result.sidebarMarked, true)
  assert.equal(result.railMarked, false, 'inert tiny rail must not steal sidebar ownership')
  assert.equal(result.headerMarked, true)
  assert.equal(result.footerMarked, true)
  assert.notEqual(result.sidebar.bg, 'rgb(0, 0, 0)', 'owned sidebar root uses the Nirai panel tone')
  assert.equal(result.sidebar.shadow, 'none')
  assert.equal(result.sidebar.blur, 'none')
  assert.equal(result.sidebar.viewTransition, 'none', 'disable snapshots only on the owned sidebar surface')
  assert.notEqual(result.headerBg, 'rgb(0, 0, 0)', 'real sidebar header loses Provider black fill')
  assert.notEqual(result.footerBg, 'rgb(0, 0, 0)', 'real sidebar footer loses Provider black fill')
  assert.equal(result.rowBg, 'rgba(0, 0, 0, 0)', 'ordinary provider rows are not repainted by Nirai')

  assert.equal(result.scroll, 20, 'scroll behavior is preserved')
  assert.notEqual(result.scrollbar, 'auto', 'scrollbar uses Nirai theme tokens')
  assert.equal(result.mediaFilter, 'contrast(1.1)', 'media pixels remain provider-owned')
  assert.notEqual(result.iconDisplay, 'none')
  assert.equal(result.badToolbar, false)

  // Let the compositor publish the hit-test region for the inserted control.
  await js('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
  const rect = await js(`document.getElementById('audit-click').getBoundingClientRect().toJSON()`)
  const point = { x:Math.round(rect.x+5), y:Math.round(rect.y+5) }
  await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type:'mouseMoved', ...point })
  await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type:'mousePressed', button:'left', clickCount:1, ...point })
  await wc.debugger.sendCommand('Input.dispatchMouseEvent', { type:'mouseReleased', button:'left', clickCount:1, ...point })
  assert.equal(await js(`document.getElementById('skin-audit-fixture').dataset.clicked`), 'yes', 'native listeners survive the skin')

  await js(`document.getElementById('skin-audit-fixture').remove()`)
}
