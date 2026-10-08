// 砂地へ着いた時刻と入りの長さだけから、姿勢の混合率を決める。
// 経過を別の状態にしないので、窓を開き直しても入りを初めから再生しない。
export const LANDING_SPEED = 0.5;
export const LANDING_FADE_IN = 0.6;
export const LANDING_FADE_TO_SIT = 1;

const clamp = value => Math.max(0, Math.min(1, value));

export function landingMix(elapsedSeconds, clipDuration) {
  if (!Number.isFinite(elapsedSeconds) || !Number.isFinite(clipDuration) || clipDuration <= 0 || elapsedSeconds < 0) return null;
  const entrySeconds = clipDuration / LANDING_SPEED;
  const sit = clamp((elapsedSeconds - entrySeconds) / LANDING_FADE_TO_SIT);
  return {
    swim: 1 - clamp(elapsedSeconds / LANDING_FADE_IN),
    entry: clamp(elapsedSeconds / LANDING_FADE_IN) * (1 - sit),
    sit,
    entryTime: Math.min(clipDuration, elapsedSeconds * LANDING_SPEED),
    sitTime: Math.max(0, elapsedSeconds - entrySeconds),
  };
}
