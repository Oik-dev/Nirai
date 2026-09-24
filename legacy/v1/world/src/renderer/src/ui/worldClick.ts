export function shouldOpenResidentChatForWorldSelection(
  selectedResidentName: string | null,
  holoResidentName: string | null
): boolean {
  return selectedResidentName !== null && selectedResidentName !== holoResidentName
}

export function shouldCloseHoloWhisperForWorldSelection(
  holoWhisperOpen: boolean,
  selectedResidentName: string | null,
  holoResidentName: string | null
): boolean {
  if (!holoWhisperOpen) return false
  // If Holo has no selectable Avatar (or no Holo Resident exists in Debug),
  // any committed World selection leaves the private Surface. When Holo is
  // selectable, only selecting Holo itself keeps the Surface open.
  return holoResidentName === null || selectedResidentName !== holoResidentName
}
