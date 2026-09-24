import { randomUUID } from 'node:crypto'
import { mkdir, readFile, readdir, rename, rm, rmdir, stat, unlink, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function processIsAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    return error?.code === 'EPERM'
  }
}

async function readOwner(lockPath) {
  // Windows can briefly deny either the directory listing or the owner read
  // during hand-off. Retry the whole observation at one boundary.
  for (let attempt = 0; ; attempt += 1) {
    let name
    try {
      const entries = await readdir(lockPath)
      name = entries.find((entry) => /^owner\.[0-9a-f-]+\.json$/.test(entry))
        ?? (entries.includes('owner.json') ? 'owner.json' : null)
      if (!name) return null
      const parsed = JSON.parse(await readFile(join(lockPath, name), 'utf8'))
      return { name, data: parsed && typeof parsed === 'object' ? parsed : null }
    } catch (error) {
      // Older clients wrote owner.json in place. An interrupted write must not
      // permanently poison the lock; treat it as an orphan with the full grace.
      if (error instanceof SyntaxError) return { name, data: null }
      if (error?.code === 'ENOENT') return null
      if (attempt < 4 && ['EPERM', 'EACCES', 'EBUSY'].includes(error?.code)) {
        await delay(2)
        continue
      }
      throw error
    }
  }
}

async function removeObservedOwner(lockPath, owner) {
  if (owner) {
    await unlink(join(lockPath, owner.name)).catch((error) => {
      if (error?.code !== 'ENOENT') throw error
    })
  }
  try {
    // Never rename or recursively remove the shared path: it may now contain
    // a replacement owner. Every new owner is published with a unique file,
    // so rmdir cannot remove a live replacement after a stale observation.
    await rmdir(lockPath)
    return true
  } catch (error) {
    if (error?.code === 'ENOENT') return true
    if (['ENOTEMPTY', 'EEXIST', 'EACCES', 'EPERM', 'EBUSY'].includes(error?.code)) return false
    throw error
  }
}

async function tryReclaimDeadLock(lockPath, staleMs) {
  let info
  try {
    info = await stat(lockPath)
  } catch (error) {
    if (error?.code === 'ENOENT') return true
    throw error
  }
  if (!info.isDirectory()) {
    throw new Error(`Lock path is not a directory: ${lockPath}`)
  }

  const owner = await readOwner(lockPath)
  const ownerPid = Number(owner?.data?.pid)
  const hasOwnerPid = Number.isInteger(ownerPid) && ownerPid > 0
  if (hasOwnerPid && processIsAlive(ownerPid)) return false
  if (owner && !hasOwnerPid && Date.now() - info.mtimeMs < staleMs) return false

  // An empty directory can remain if a process exits between unlink and
  // rmdir during release. New acquisitions are always published nonempty,
  // so an empty remnant can be removed immediately without a grace delay.
  return removeObservedOwner(lockPath, owner)
}

export async function acquireDirectoryLock(lockPath, {
  timeoutMs = 10_000,
  staleMs = 300_000,
  retryMs = 25
} = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs < 0) throw new Error('lock timeoutMs must be non-negative')
  if (!Number.isFinite(staleMs) || staleMs <= 0) throw new Error('lock staleMs must be positive')
  const token = randomUUID()
  const ownerName = `owner.${token}.json`
  const temporary = `${lockPath}.acquire.${token}`
  const deadline = Date.now() + timeoutMs
  await mkdir(dirname(lockPath), { recursive: true })

  // Publish a fully initialized, nonempty directory atomically. A contender
  // can never see partially written JSON or an empty live acquisition.
  await mkdir(temporary)
  try {
    await writeFile(join(temporary, ownerName), `${JSON.stringify({
      version: 2,
      token,
      pid: process.pid,
      acquired_at: new Date().toISOString()
    })}\n`, 'utf8')
    for (;;) {
      try {
        await rename(temporary, lockPath)
        break
      } catch (error) {
        if (!['EEXIST', 'ENOTEMPTY', 'EACCES', 'EPERM'].includes(error?.code)) throw error
        const reclaimed = await tryReclaimDeadLock(lockPath, staleMs)
        if (Date.now() >= deadline) throw new Error(`Timed out waiting for lock: ${lockPath}`)
        if (!reclaimed) await delay(Math.max(1, retryMs))
      }
    }
  } finally {
    await rm(temporary, { recursive: true, force: true })
  }

  let released = false
  return async () => {
    if (released) return
    const owner = await readOwner(lockPath)
    if (owner?.data?.token !== token) {
      throw new Error(`Lock ownership changed before release: ${lockPath}`)
    }
    await removeObservedOwner(lockPath, owner)
    released = true
  }
}

export async function withDirectoryLock(lockPath, operation, options = {}) {
  const release = await acquireDirectoryLock(lockPath, options)
  try {
    return await operation()
  } finally {
    await release()
  }
}
