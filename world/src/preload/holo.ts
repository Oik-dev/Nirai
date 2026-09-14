import { ipcRenderer } from 'electron'
import { holoDomGuards } from '../shared/holoDom'

// No API is exposed to the remote page. Only an actual user click on its
// actionable Stop control can request cancellation in the owning Conversation.
const dom = holoDomGuards()
document.addEventListener('click', (event) => {
  if (!event.isTrusted || location.protocol !== 'https:' || location.hostname !== 'chatgpt.com') return
  const button = event.target instanceof Element ? event.target.closest('button') : null
  if (dom.isStopButton(button)) {
    document.documentElement.setAttribute('data-nirai-holo-master-stopped', location.href)
    ipcRenderer.send('holo:master-stop', location.href)
  } else if (button && button === dom.sendButton()) {
    document.documentElement.removeAttribute('data-nirai-holo-master-stopped')
  }
}, true)
document.addEventListener('keydown', (event) => {
  if (event.isTrusted && event.key === 'Enter' && !event.shiftKey && !event.isComposing
    && event.target instanceof Element && dom.composer()?.contains(event.target)) {
    document.documentElement.removeAttribute('data-nirai-holo-master-stopped')
  }
}, true)
