export const HOLO_CHATGPT_HOME_URL = 'https://chatgpt.com/'
export const HOLO_SESSION_PARTITION = 'persist:nirai-holo-chatgpt'

const HOLO_ALLOWED_NAVIGATION_HOSTS = [
  'chatgpt.com',
  'auth.openai.com',
  'accounts.google.com',
  'login.microsoftonline.com',
  'login.live.com',
  'appleid.apple.com',
  'account.apple.com'
] as const

export interface HoloSurfaceBounds {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export function buildHoloBootstrapTemplate(localDate: string): string {
  return [
    `[${localDate} Nirai Dive]`,
    '',
    'Local MCPを使用してNiraiへ接続してください。',
    'あなたはHoloとしてNiraiへDiveします。',
    'Local MCPのrun_processでcwdをD:\\Products\\Niraiにし、node.exe tools\\holo-local-client.mjs attach を実行してください。',
    'attach後、同じLocal Clientのsnapshotで現在のNirai状態を取得してください。',
    'Local Clientが内部で使う認証情報を直接読み取ったり、会話へ出力したりしないでください。',
    '',
    'このConversationの通常Assistant返答はMasterへのHolo Whisperです。',
    'Nirai World上で公開発言・状態確認・Event待機が必要な場合は、同じLocal Clientのsay / snapshot / waitをLocal MCP経由で使用してください。'
  ].join('\n')
}

function matchesAllowedHost(hostname: string, allowedHost: string): boolean {
  return hostname === allowedHost || hostname.endsWith(`.${allowedHost}`)
}

export function isHoloAllowedNavigationUrl(value: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:') return false
    return HOLO_ALLOWED_NAVIGATION_HOSTS.some((host) => matchesAllowedHost(url.hostname, host))
  } catch {
    return false
  }
}

export function isSafeHoloExternalUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.protocol === 'https:'
  } catch {
    return false
  }
}

export function shouldAllowHoloWebPermission(_permission: string): boolean {
  // Gate 0 needs no camera, microphone, geolocation, clipboard, display capture,
  // filesystem, notification, USB/HID/serial, or other Chromium permissions.
  return false
}

export function isHoloConversationUrl(value: string): boolean {
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== 'chatgpt.com') return false
    return /(?:^|\/)c\/[^/]+/.test(url.pathname)
  } catch {
    return false
  }
}

export function clampHoloSurfaceBounds(
  bounds: HoloSurfaceBounds,
  contentWidth: number,
  contentHeight: number
): HoloSurfaceBounds {
  const x = Math.max(0, Math.min(Math.round(bounds.x), Math.max(0, contentWidth - 1)))
  const y = Math.max(0, Math.min(Math.round(bounds.y), Math.max(0, contentHeight - 1)))
  const width = Math.max(1, Math.min(Math.round(bounds.width), Math.max(1, contentWidth - x)))
  const height = Math.max(1, Math.min(Math.round(bounds.height), Math.max(1, contentHeight - y)))
  return { x, y, width, height }
}
