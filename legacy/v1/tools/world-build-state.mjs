import { randomUUID, createHash } from 'node:crypto'
import { createReadStream } from 'node:fs'
import { spawn } from 'node:child_process'
import { lstat, mkdir, readFile, readdir, realpath, rename, unlink, writeFile } from 'node:fs/promises'
import { dirname, isAbsolute, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { withDirectoryLock } from './file-lock.mjs'

const DEFAULT_NIRAI_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const BUILD_STAMP_RELATIVE_PATH = join('runtime', 'holo', 'world-build.json')
const BUILD_LOCK_RELATIVE_PATH = join('runtime', 'holo', 'world-build.lock')
const WORLD_BUILD_INPUTS = [
  join('world', 'src'),
  join('world', 'public'),
  join('world', 'resources'),
  join('world', 'electron.vite.config.ts'),
  join('world', 'tsconfig.json'),
  join('world', 'package.json'),
  join('world', 'package-lock.json')
]

async function collectInputFingerprints(root, path, rows) {
  let info
  try {
    info = await lstat(path, { bigint: true })
  } catch (error) {
    if (error?.code === 'ENOENT') return
    throw error
  }
  const resolvedRelative = relative(root, await realpath(path))
  if (info.isSymbolicLink() || resolvedRelative === '..'
    || resolvedRelative.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`)
    || isAbsolute(resolvedRelative)) {
    throw new Error(`World build input must stay inside the project without symbolic links: ${relative(root, path)}`)
  }
  const relativePath = relative(root, path).replaceAll('\\', '/')
  // Content hashing detects edits even when size/mtime are preserved. Change
  // metadata additionally detects a file that was modified during the build
  // and then restored byte-for-byte before the post-build snapshot. Directory
  // metadata also catches transient add/remove membership changes.
  const changeMetadata = [
    info.size,
    info.mtimeNs,
    info.ctimeNs,
    info.ino,
    info.dev,
    info.mode
  ].map(String).join(':')
  if (info.isDirectory()) {
    rows.push(`dir\0${relativePath}\0${changeMetadata}`)
    const entries = await readdir(path, { withFileTypes: true })
    entries.sort((left, right) => left.name.localeCompare(right.name))
    for (const entry of entries) {
      await collectInputFingerprints(root, join(path, entry.name), rows)
    }
    return
  }
  if (!info.isFile()) return
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(path)) hash.update(chunk)
  rows.push(`file\0${relativePath}\0${changeMetadata}\0${hash.digest('hex')}`)
}

export async function computeWorldBuildFingerprint(niraiRoot = DEFAULT_NIRAI_ROOT) {
  const root = await realpath(niraiRoot)
  const rows = []
  for (const input of WORLD_BUILD_INPUTS) {
    await collectInputFingerprints(root, join(root, input), rows)
  }
  return createHash('sha256').update(rows.join('\n')).digest('hex')
}

export function worldBuildStampPath(niraiRoot = DEFAULT_NIRAI_ROOT) {
  return join(niraiRoot, BUILD_STAMP_RELATIVE_PATH)
}

export async function readWorldBuildStamp(niraiRoot = DEFAULT_NIRAI_ROOT) {
  try {
    const parsed = JSON.parse(await readFile(worldBuildStampPath(niraiRoot), 'utf8'))
    if (parsed?.version !== 2
      || typeof parsed.fingerprint !== 'string'
      || typeof parsed.built_at !== 'string'
      || typeof parsed.build_id !== 'string') {
      return null
    }
    return parsed
  } catch (error) {
    if (error?.code === 'ENOENT') return null
    throw error
  }
}

export async function getWorldBuildStatus(niraiRoot = DEFAULT_NIRAI_ROOT) {
  const fingerprint = await computeWorldBuildFingerprint(niraiRoot)
  const stamp = await readWorldBuildStamp(niraiRoot)
  return {
    fingerprint,
    stamp,
    current: Boolean(stamp && stamp.fingerprint === fingerprint)
  }
}

export async function assertWorldBuildReadyForWorkflowCompletion(startFingerprint, niraiRoot = DEFAULT_NIRAI_ROOT) {
  const currentFingerprint = await computeWorldBuildFingerprint(niraiRoot)
  if (typeof startFingerprint === 'string' && startFingerprint === currentFingerprint) {
    return { required: false, currentFingerprint }
  }
  const stamp = await readWorldBuildStamp(niraiRoot)
  if (!stamp || stamp.fingerprint !== currentFingerprint) {
    throw new Error('workflow-complete blocked: Nirai World build inputs changed during this workflow. Finish implementation and verification first, then run npm run build once before completing the workflow.')
  }
  return { required: true, currentFingerprint }
}

async function writeWorldBuildStamp(niraiRoot, expectedFingerprint, now, buildId) {
  const currentFingerprint = await computeWorldBuildFingerprint(niraiRoot)
  if (currentFingerprint !== expectedFingerprint) {
    throw new Error('World build inputs changed after the successful build; build stamp was not published')
  }
  const path = worldBuildStampPath(niraiRoot)
  await mkdir(dirname(path), { recursive: true })
  const temporary = `${path}.${randomUUID()}.tmp`
  const stamp = {
    version: 2,
    fingerprint: expectedFingerprint,
    built_at: now.toISOString(),
    build_id: buildId
  }
  try {
    await writeFile(temporary, `${JSON.stringify(stamp, null, 2)}\n`, 'utf8')
    await rename(temporary, path)
  } finally {
    await unlink(temporary).catch(() => undefined)
  }
  return stamp
}

async function runElectronViteBuild({ niraiRoot }) {
  const worldRoot = join(niraiRoot, 'world')
  const entry = join(worldRoot, 'node_modules', 'electron-vite', 'bin', 'electron-vite.js')
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [entry, 'build'], {
      cwd: worldRoot,
      env: process.env,
      stdio: 'inherit',
      windowsHide: true
    })
    child.once('error', reject)
    child.once('exit', (code, signal) => {
      if (code === 0) resolve()
      else reject(new Error(`electron-vite build failed (${signal ? `signal ${signal}` : `exit ${code}`})`))
    })
  })
}

export async function runWorldBuild(niraiRoot = DEFAULT_NIRAI_ROOT, {
  builder = runElectronViteBuild,
  now = () => new Date(),
  lockTimeoutMs = 1_000
} = {}) {
  const lockPath = join(niraiRoot, BUILD_LOCK_RELATIVE_PATH)
  return withDirectoryLock(lockPath, async () => {
    const inputFingerprint = await computeWorldBuildFingerprint(niraiRoot)
    const buildId = randomUUID()
    // A failed or interrupted build may have partially replaced output. Never
    // leave an older success stamp able to certify the result of this attempt.
    await unlink(worldBuildStampPath(niraiRoot)).catch((error) => {
      if (error?.code !== 'ENOENT') throw error
    })
    await builder({ niraiRoot, inputFingerprint, buildId })
    const afterBuildFingerprint = await computeWorldBuildFingerprint(niraiRoot)
    if (afterBuildFingerprint !== inputFingerprint) {
      throw new Error('World build inputs changed while electron-vite was building; rerun the final build after edits stop')
    }
    return writeWorldBuildStamp(niraiRoot, inputFingerprint, now(), buildId)
  }, { timeoutMs: lockTimeoutMs, staleMs: 1_800_000 })
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const command = process.argv[2]
  if (command === 'build') {
    console.log(JSON.stringify(await runWorldBuild()))
  } else if (command === 'status') {
    console.log(JSON.stringify(await getWorldBuildStatus()))
  } else {
    console.error('Usage: node tools/world-build-state.mjs <build|status>')
    process.exitCode = 1
  }
}
