export type ParsedChatInput =
  | { readonly kind: 'say'; readonly text: string }
  | { readonly kind: 'whisper'; readonly to: string; readonly text: string }
  | { readonly kind: 'invalid-whisper'; readonly text: string }

export function parseChatInput(
  text: string,
  residentNames: readonly string[],
  focusedResidentName: string | null = null
): ParsedChatInput {
  const cleaned = text.trim()
  if (!cleaned.startsWith('@')) {
    if (focusedResidentName && residentNames.includes(focusedResidentName)) {
      return { kind: 'whisper', to: focusedResidentName, text: cleaned }
    }
    return { kind: 'say', text: cleaned }
  }

  const names = [...residentNames].sort((left, right) => right.length - left.length)
  for (const name of names) {
    const prefix = `@${name}`
    if (cleaned.length <= prefix.length) continue
    if (cleaned.slice(0, prefix.length).localeCompare(prefix, undefined, { sensitivity: 'accent' }) !== 0) continue
    const separator = cleaned.charAt(prefix.length)
    if (!/\s/u.test(separator)) continue
    const whisperText = cleaned.slice(prefix.length).trim()
    if (!whisperText) return { kind: 'invalid-whisper', text: cleaned }
    return { kind: 'whisper', to: name, text: whisperText }
  }

  return { kind: 'invalid-whisper', text: cleaned }
}

export function residentMentionCandidates(
  text: string,
  residentNames: readonly string[]
): readonly string[] {
  const match = text.match(/^@([^\s]*)$/u)
  if (!match) return []
  const query = match[1].toLocaleLowerCase()
  return residentNames
    .filter((name) => name.toLocaleLowerCase().startsWith(query))
    .sort((left, right) => left.localeCompare(right))
}

export function completeResidentMention(residentName: string): string {
  return `@${residentName} `
}
