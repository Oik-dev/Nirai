// storage.sessionは拡張の読み直しで消え、service workerの再起動では残る。
export function shouldReloadExtension(saved, current) {
  return typeof current === "string" && current.length > 0
    && typeof saved === "string" && saved.length > 0 && saved !== current;
}
