import { describe, expect, it } from 'vitest'
import type { HoloAddonStatus } from '../../src/preload/api'
import {
  holoBridgeStatusLabel,
  holoPersistenceWarning,
  holoSurfaceStatusLabel
} from '../../src/renderer/src/ui/HoloGate0Surface'
import {
  shouldCloseHoloWhisperForWorldSelection,
  shouldOpenResidentChatForWorldSelection
} from '../../src/renderer/src/ui/worldClick'

function status(overrides: Partial<HoloAddonStatus>): HoloAddonStatus {
  return {
    phase: 'ready',
    visible: true,
    loaded: true,
    web_state: 'ready',
    dive_state: 'none',
    current_url: 'https://chatgpt.com/',
    current_dive_url: null,
    current_dive_session_id: null,
    title: 'ChatGPT',
    skin_mode: 'applied',
    issue: null,
    persistence_issue: null,
    ...overrides
  }
}

describe('Holo Whisper observable status labels', () => {
  it('uses only Web and Dive facts for Surface status', () => {
    expect(holoSurfaceStatusLabel(null)).toBe('ChatGPTを準備中')
    expect(holoSurfaceStatusLabel(status({ phase: 'unavailable', web_state: 'unavailable' })))
      .toBe('ChatGPTを表示できません')
    expect(holoSurfaceStatusLabel(status({ dive_state: 'current' }))).toBe('会話を継続できます')
    expect(holoSurfaceStatusLabel(status({ dive_state: 'preparing' }))).toBe('新しいDiveを準備中')
    expect(holoSurfaceStatusLabel(status({ dive_state: 'none' }))).toBe('ChatGPTを利用できます')
  })

  it('keeps persistence failure visible independently from Web readiness', () => {
    expect(holoPersistenceWarning(status({ persistence_issue: null }))).toBeNull()
    expect(holoPersistenceWarning(status({
      phase: 'ready',
      web_state: 'ready',
      persistence_issue: 'state_persistence_failed'
    }))).toContain('再起動すると失われる可能性があります')
  })

  it('maps the committed World selection result to the normal Resident chat state', () => {
    expect(shouldOpenResidentChatForWorldSelection(null, 'Holo')).toBe(false)
    expect(shouldOpenResidentChatForWorldSelection('Holo', 'Holo')).toBe(false)
    expect(shouldOpenResidentChatForWorldSelection('Codex', 'Holo')).toBe(true)
    expect(shouldOpenResidentChatForWorldSelection('Codex', null)).toBe(true)
  })

  it('closes Holo Whisper from the committed World selection result, even without Holo Focus', () => {
    // VRM未設定Holoカード / Debug入口: Focusなしの背景選択でも閉じる。
    expect(shouldCloseHoloWhisperForWorldSelection(true, null, 'Holo')).toBe(true)
    expect(shouldCloseHoloWhisperForWorldSelection(true, null, null)).toBe(true)
    // 通常Residentから@Holoへ誘導後、Worldで通常Resident/背景を選べば閉じる。
    expect(shouldCloseHoloWhisperForWorldSelection(true, 'Codex', 'Holo')).toBe(true)
    // Holo本人の再選択だけはWhisperを維持する。
    expect(shouldCloseHoloWhisperForWorldSelection(true, 'Holo', 'Holo')).toBe(false)
    // Surfaceが既に閉じている時は何もしない。
    expect(shouldCloseHoloWhisperForWorldSelection(false, null, 'Holo')).toBe(false)
  })

  it('shows Bridge success only when Core and Web agree on the same Dive id', () => {
    expect(holoBridgeStatusLabel(false, 'attached', 'DIVE-2', 'DIVE-2'))
      .toBe('Nirai連携を確認できません')
    expect(holoBridgeStatusLabel(true, 'attached', 'DIVE-2', 'DIVE-2'))
      .toBe('Nirai連携済み')
    expect(holoBridgeStatusLabel(true, 'attach_waiting', 'DIVE-2', 'DIVE-2'))
      .toBe('Nirai連携待ち')
    expect(holoBridgeStatusLabel(true, 'attached', 'DIVE-2', 'DIVE-1'))
      .toBe('Nirai連携不一致')
    expect(holoBridgeStatusLabel(true, 'not_started', 'DIVE-2', 'DIVE-2'))
      .toBe('Nirai連携未開始')
    expect(holoBridgeStatusLabel(true, 'not_started', null, null)).toBe('Dive未開始')
  })
})
