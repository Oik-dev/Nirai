import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import electronExe from 'electron'

const root = mkdtempSync(resolve(tmpdir(), 'nirai-v2-holo-smoke-'))
const env = { ...process.env, NIRAI_HOLO_SMOKE_ROOT: root }
delete env.ELECTRON_RUN_AS_NODE
const child = spawn(electronExe, [resolve(import.meta.dirname, '../holo-smoke/main.mjs')], { env, stdio: 'inherit', windowsHide: true })
let timedOut = false
const timer = setTimeout(() => { timedOut = true; child.kill() }, 45_000)
child.once('error', error => { clearTimeout(timer); console.error(error); process.exitCode = 1 })
child.once('exit', code => {
  clearTimeout(timer)
  rmSync(root, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  process.exitCode = timedOut ? 2 : code ?? 1
})
