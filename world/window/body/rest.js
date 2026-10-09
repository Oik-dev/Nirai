// 寝転びの位置は、窓が見た眠りの切り替えと時刻だけで決める。
// 姿勢の長さは生成した .vrma の尺。途中で起きてもその位置から逆再生する。
const clamp = value => Math.max(0, Math.min(1, value));

// 最初の観測は at=0（ずっと前からの状態）。seatedAt は、砂地に着いて
// 腰を下ろす・座るへの混合を終えた時刻。起き上がりは待たせない。
export function restAt(changes, now, seatedAt, reclineSeconds) {
  if (!Array.isArray(changes) || changes.length === 0 || !Number.isFinite(now)
    || !Number.isFinite(seatedAt) || !(reclineSeconds > 0)) return null;
  const [first, ...rest] = changes;
  if (first.at !== 0 || typeof first.asleep !== 'boolean') return null;
  let level = first.asleep ? 1 : 0;
  let asleep = first.asleep;
  let since = 0;
  let seat = seatedAt;
  const length = reclineSeconds * 1000;
  for (const event of rest) {
    if (!(Number.isFinite(event.at) && event.at >= since) || typeof event.asleep !== 'boolean') return null;
    const until = Math.min(event.at, now);
    if (until > since) {
      const begin = asleep ? Math.max(since, seat) : since;
      level = clamp(level + (asleep ? 1 : -1) * Math.max(0, until - begin) / length);
    }
    if (event.at > now) break;
    asleep = event.asleep;
    since = event.at;
    // A later sleep may start from another activity; its arrival and seating
    // time is independent of the first sleep. Past segments keep their seat.
    if (asleep && event.seatedAt !== undefined) {
      if (!Number.isFinite(event.seatedAt) || event.seatedAt < event.at) return null;
      seat = event.seatedAt;
    }
  }
  if (now > since && (rest.length === 0 || rest.at(-1).at <= now)) {
    const begin = asleep ? Math.max(since, seat) : since;
    level = clamp(level + (asleep ? 1 : -1) * Math.max(0, now - begin) / length);
  }
  return {
    level,
    clipTime: level * reclineSeconds,
    asleep,
    phase: level === 1 && asleep ? 'sleeping'
      : level === 0 && !asleep ? 'sitting'
        : asleep ? 'reclining' : 'getting-up',
  };
}
