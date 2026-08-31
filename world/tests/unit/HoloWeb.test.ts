import { describe, expect, it } from 'vitest'
import {
  buildHoloBootstrapTemplate,
  clampHoloSurfaceBounds,
  isHoloAllowedNavigationUrl,
  isHoloConversationUrl,
  isSafeHoloExternalUrl,
  shouldAllowHoloWebPermission
} from '../../src/main/holo/holoWeb'

describe('Holo Web Gate 0 helpers', () => {
  it('builds a Dive bootstrap without an automatic-send instruction', () => {
    const bootstrap = buildHoloBootstrapTemplate('2026-08-31')
    expect(bootstrap).toContain('[2026-08-31 Nirai Dive]')
    expect(bootstrap).toContain('Local MCPを使用してNiraiへ接続してください。')
    expect(bootstrap).toContain('tools\\holo-local-client.mjs attach')
    expect(bootstrap).toContain('同じLocal Clientのsnapshot')
    expect(bootstrap).toContain('認証情報を直接読み取ったり')
    expect(bootstrap).toContain('このConversationの通常Assistant返答はMasterへのHolo Whisperです。')
    expect(bootstrap).not.toContain('自動送信')
  })

  it('accepts only ChatGPT conversation URLs as persisted Dive references', () => {
    expect(isHoloConversationUrl('https://chatgpt.com/c/1234')).toBe(true)
    expect(isHoloConversationUrl('https://chatgpt.com/g/example/c/1234')).toBe(true)
    expect(isHoloConversationUrl('https://chatgpt.com/')).toBe(false)
    expect(isHoloConversationUrl('https://example.com/c/1234')).toBe(false)
    expect(isHoloConversationUrl('not-a-url')).toBe(false)
  })

  it('allows only ChatGPT and known authentication origins for in-surface navigation', () => {
    expect(isHoloAllowedNavigationUrl('https://chatgpt.com/c/1234')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://sub.chatgpt.com/path')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://auth.openai.com/login')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://accounts.google.com/o/oauth2/v2/auth')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://login.microsoftonline.com/common/oauth2/v2.0/authorize')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://appleid.apple.com/auth/authorize')).toBe(true)
    expect(isHoloAllowedNavigationUrl('https://chatgpt.com.attacker.example/')).toBe(false)
    expect(isHoloAllowedNavigationUrl('http://chatgpt.com/')).toBe(false)
    expect(isHoloAllowedNavigationUrl('https://example.com/')).toBe(false)
  })

  it('sends only HTTPS popup targets to the external browser', () => {
    expect(isSafeHoloExternalUrl('https://example.com/path')).toBe(true)
    expect(isSafeHoloExternalUrl('http://example.com/path')).toBe(false)
    expect(isSafeHoloExternalUrl('file:///C:/Windows/System32/calc.exe')).toBe(false)
    expect(isSafeHoloExternalUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeHoloExternalUrl('not-a-url')).toBe(false)
  })

  it('denies every remote-content permission in Gate 0', () => {
    for (const permission of [
      'media',
      'display-capture',
      'geolocation',
      'clipboard-read',
      'clipboard-sanitized-write',
      'notifications',
      'fileSystem',
      'window-management',
      'idle-detection',
      'unknown'
    ]) {
      expect(shouldAllowHoloWebPermission(permission)).toBe(false)
    }
  })

  it('clamps native WebContentsView bounds inside the BrowserWindow content area', () => {
    expect(clampHoloSurfaceBounds({ x: -20, y: 15, width: 2000, height: 900 }, 1280, 720)).toEqual({
      x: 0,
      y: 15,
      width: 1280,
      height: 705
    })
  })
})
