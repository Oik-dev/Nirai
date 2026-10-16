import { clampEnclosure, withinEnclosure, CAMERA_CENTER } from '../sea/habitat.js';

// 現在の位置だけから毎回決める。過去のフレームは保持しない。
const radius = body => body.asleep ? .88 : .4;
const height = body => body.asleep ? .9 : 1.65;
const age = body => body.activity?.since ? Date.parse(body.activity.since) : -Infinity;

const smooth = v => { const t = Math.max(0, Math.min(1, v)); return t * t * (3 - 2 * t); };

function steer(body, point, fixed, gap, verticalWeight = 1) {
  // 接触する前から進行方向の横へ曲げる。正面同士なら右、片側なら空いている側。
  const forward = { x: Math.sin(body.pose.yaw), z: Math.cos(body.pose.yaw) };
  const right = { x: forward.z, z: -forward.x };
  const dx = fixed.x - body.pose.x, dz = fixed.z - body.pose.z;
  const ahead = dx * forward.x + dz * forward.z;
  const lateral = -dx * right.x - dz * right.z;
  const lead = gap + 1.7;
  if (Math.abs(ahead) >= lead || lateral >= gap || lateral <= -lead) return point;
  const turn = 1;
  const amount = Math.max(0, gap - lateral + .003);
  const blend = smooth((lead - ahead) / (lead - gap)) * smooth((lead + ahead) / (lead - gap))
    * smooth((lateral + lead) / (lead - gap)) * (body.pose.avoidStrength ?? 1) * verticalWeight;
  return { ...point, x: point.x + turn * right.x * amount * blend,
    z: point.z + turn * right.z * amount * blend };
}

function clearOf(body, point, fixed) {
  if (!withinEnclosure(point, radius(body)) || body.asleep && point.z > CAMERA_CENTER.z) return false;
  return fixed.every(other => Math.abs(point.y - other.pose.y) >= (height(body) + height(other)) / 2
    || Math.hypot(point.x - other.pose.x, point.z - other.pose.z) >= radius(body) + radius(other) - 1e-6);
}

function settleStill(body, fixed, origin = body.pose) {
  const bounded = clampEnclosure(origin, radius(body));
  if (clearOf(body, bounded, fixed)) return { ...bounded };
  let best = null, cost = Infinity;
  // 衝突している円の縁から、すでに落ち着いた全員と重ならない最寄りの点を選ぶ。
  for (const other of fixed) {
    if (Math.abs(origin.y - other.pose.y) >= (height(body) + height(other)) / 2) continue;
    const distance = radius(body) + radius(other) + .003;
    const candidates = [];
    const dx = origin.x - other.pose.x, dz = origin.z - other.pose.z;
    if (Math.hypot(dx, dz) > 1e-7) candidates.push(Math.atan2(dz, dx));
    for (let i = 0; i < 72; i++) candidates.push(i * Math.PI / 36);
    for (const a of candidates) {
      const point = { ...origin, x: other.pose.x + distance * Math.cos(a),
        z: other.pose.z + distance * Math.sin(a) };
      if (!clearOf(body, point, fixed)) continue;
      const d = (point.x - origin.x) ** 2 + (point.z - origin.z) ** 2;
      if (d < cost) { best = point; cost = d; }
    }
  }
  return best ?? clampEnclosure(origin, radius(body));
}

// 泳いで近づくSerinaには、静止中の体が手前で道をあける。
// 進行方向を正本とし、重なってから半径の反対側へ飛び移らない。
function yieldToSwimmer(body, point, other) {
  if (other.name !== 'Serina' || other.activity?.name !== '海の中を泳ぐ') return point;
  const vertical = (height(body) + height(other)) / 2 - Math.abs(point.y - other.pose.y);
  if (vertical <= -.45) return point;
  const rx = point.x - other.pose.x, rz = point.z - other.pose.z;
  const d = Math.hypot(rx, rz);
  const gap = radius(body) + radius(other), reach = gap + 1.4;
  if (d >= reach) return point;
  const right = { x: Math.cos(other.pose.yaw), z: -Math.sin(other.pose.yaw) };
  const lateral = rx * right.x + rz * right.z;
  // この泳ぐ輪では進行方向の右が輪の内側。外縁へ押し出さず、通過時も同じ側を使う。
  const side = 1;
  const amount = Math.max(0, gap - side * lateral);
  const weight = smooth((reach - d) / (reach - gap)) * smooth((vertical + .45) / .55);
  return { ...point, x: point.x + side * right.x * amount * weight,
    z: point.z + side * right.z * amount * weight };
}

// 入力：{name, activity, asleep, pose}。Serinaは固定、残りを開始順で確定。
export function avoidBodies(bodies) {
  const ordered = [...bodies].sort((a, b) => a.name === 'Serina' ? -1
    : b.name === 'Serina' ? 1 : age(a) - age(b) || a.name.localeCompare(b.name));
  const fixed = [];
  const result = {};
  for (const body of ordered) {
    let point = { ...body.pose };
    if (body.name !== 'Serina') {
      // 到着の瞬間に moving が変わっても、よける基準は連続する。
      for (const other of fixed) {
        point = yieldToSwimmer(body, point, other);
        const vertical = (height(body) + height(other)) / 2 - Math.abs(point.y - other.pose.y);
        if (vertical > -.45 && body.pose.moving) point = steer(body, point, other.pose,
          radius(body) + radius(other), smooth((vertical + .45) / .55));
      }
      point = settleStill(body, fixed, point);
    }
    result[body.name] = point;
    fixed.push({ ...body, pose: point });
  }
  return result;
}
