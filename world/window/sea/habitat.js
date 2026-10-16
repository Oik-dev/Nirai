// カメラ・海・窓が読む唯一の囲い。Three.jsに依存しない。
export const CAMERA_CENTER = Object.freeze({ x: 0, y: 1.05, z: -0.55 });
export const CAMERA_LIMITS = Object.freeze({
  floor: .35, ceiling: 3.60, radius: 10.25,
  focusNear: .9, focusFar: 4.8, pitch: Math.PI * .42,
});
export const CAMERA_FOV = 60;
// 縦長に寄った窓でも体全体が入るよう、正方形を基準にする。
export const ENCLOSURE_ASPECT = 1;
const HALF_TAN = Math.tan(CAMERA_FOV * Math.PI / 360);
const MAX_BODY_TOP = 4.6; // 水面近くの体の上端。腰だけでなく頭も画角へ収める。
const homePitch = Math.atan2(1.4 - CAMERA_CENTER.y, CAMERA_LIMITS.radius);
const vertical = CAMERA_LIMITS.radius - (MAX_BODY_TOP - 1.4) / Math.tan(CAMERA_FOV * Math.PI / 360 - homePitch);
const horizontal = CAMERA_LIMITS.radius * HALF_TAN * ENCLOSURE_ASPECT
  / Math.sqrt(1 + (HALF_TAN * ENCLOSURE_ASPECT) ** 2);
export const ENCLOSURE_RADIUS = Math.min(vertical, horizontal) - .12;
export const BODY_MARGIN = .43;

export function withinEnclosure(point, margin = BODY_MARGIN) {
  return Math.hypot(point.x - CAMERA_CENTER.x, point.z - CAMERA_CENTER.z)
    <= ENCLOSURE_RADIUS - margin + 1e-7;
}

export function clampEnclosure(point, margin = BODY_MARGIN) {
  const dx = point.x - CAMERA_CENTER.x, dz = point.z - CAMERA_CENTER.z;
  const distance = Math.hypot(dx, dz), allowed = ENCLOSURE_RADIUS - margin;
  return distance <= allowed ? point : { ...point, x: CAMERA_CENTER.x + dx * allowed / distance,
    z: CAMERA_CENTER.z + dz * allowed / distance };
}
