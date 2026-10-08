import { ACTIVITIES, HOME_ACTIVITY } from './activities.js';

// 固定の場所の中心（立っているときの腰の高さ。足元から0.85 m上）と、そこで砂に腰を下ろすか（sit）。
// 家のカメラから見て、左下の話す欄に隠れないところに置く。
export const PLACES = Object.freeze({
  '居場所': Object.freeze({ x: 0, y: 0.85, z: -0.55, sit: 0 }),
  '砂地': Object.freeze({ x: 2.2, y: 0.85, z: -2.6, sit: 1 }),
  '水面の近く': Object.freeze({ x: -1.6, y: 3.0, z: -3.0, sit: 0 }),
});
// 座ると、腰は立っているときより0.75 m下がる（砂から0.1 m）。座る姿勢そのものは体が組む（sit を重みにして）。
export const SIT_DROP = 0.75;

const HOME_CAMERA = Object.freeze({ x: 0, z: 3.65 });
const WINDOW = Object.freeze({ ahead: 1.8, below: 0.6, minY: 0.85, maxY: 3.2, reach: 5.5 });
const SWIM = Object.freeze({ x: 0, y: 1.6, z: -2.4, rx: 2.6, rz: 1.6, bob: 0.3, pitch: 1.0, speed: 0.35 });
const SWIM_PERIOD = 2 * Math.PI * Math.sqrt((SWIM.rx ** 2 + SWIM.rz ** 2) / 2) / SWIM.speed; // 秒
const TRAVEL = Object.freeze({ speed: 0.5, min: 3, max: 30, lift: 0.5, liftPerMetre: 0.15, pitch: 0.6, near: 0.01 });

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
// t=0 で a、t=1 で b そのものになる。
const mix = (a, b, t) => a * (1 - t) + b * t;
// 角度を、短いほうへ回して混ぜる。
const mixAngle = (a, b, t) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * t;
// ISO の時刻をミリ秒にする。無い・読めないときは NaN。
const timeOf = since => (since ? Date.parse(since) : NaN);
// 知らない活動は、居場所でくつろぐ と同じ。
const placeOf = name => (Object.hasOwn(ACTIVITIES, name) ? ACTIVITIES[name] : ACTIVITIES[HOME_ACTIVITY]).place;

// 固定の場所：中心にいて、家のカメラの方を向く。
function restPose(place) {
  const p = PLACES[place];
  return { x: p.x, y: p.y, z: p.z, yaw: Math.atan2(HOME_CAMERA.x - p.x, HOME_CAMERA.z - p.z), pitch: 0, sit: p.sit };
}

// 窓辺：見ている方向の少し前。海の縁（居場所から5.5 m）の外には出ず、カメラの方を向く。
function windowPose(view) {
  const home = PLACES['居場所'];
  let x = view.x - Math.sin(view.yaw) * WINDOW.ahead;
  let z = view.z - Math.cos(view.yaw) * WINDOW.ahead;
  const dx = x - home.x, dz = z - home.z;
  const far = Math.hypot(dx, dz);
  if (far > WINDOW.reach) {
    x = home.x + dx * WINDOW.reach / far;
    z = home.z + dz * WINDOW.reach / far;
  }
  return { x, y: clamp(view.y - WINDOW.below, WINDOW.minY, WINDOW.maxY), z, yaw: Math.atan2(view.x - x, view.z - z), pitch: 0, sit: 0 };
}

// 海の中：楕円を回り続ける。位置は時刻だけで決まる。
function swimPose(since, now) {
  const start = timeOf(since);
  const theta = 2 * Math.PI * ((now - (Number.isFinite(start) ? start : 0)) / 1000) / SWIM_PERIOD;
  const s = Math.sin(theta), c = Math.cos(theta);
  return {
    x: SWIM.x + SWIM.rx * s,
    y: SWIM.y + SWIM.bob * Math.sin(2 * theta),
    z: SWIM.z + SWIM.rz * c,
    yaw: Math.atan2(SWIM.rx * c, -SWIM.rz * s), // 動いている向き
    pitch: SWIM.pitch,
    sit: 0,
  };
}

// 活動がその時刻にいる姿勢（移動の途中は含まない）。y は立っているときの腰の高さ。
function poseOf(name, since, now, view) {
  const place = placeOf(name);
  if (place === '海の中') return swimPose(since, now);
  if (place === '窓辺') return windowPose(view);
  return restPose(place);
}

// 座っている重みのぶん、腰を下ろす。
const settled = pose => ({ ...pose, y: pose.y - SIT_DROP * pose.sit });

// 体の中心（腰）の位置・向き・前傾と、座っている重み（sit）。活動が切り替わった直後は、前の位置から移動する
// （座っていれば立ち上がってから動き、着いてから座る）。同じ入力には同じ答えを返すので、窓を開き直しても同じ姿になる。
export function placeAt(activity, now, view) {
  const { name, since, from } = activity;
  const start = from ? timeOf(since) : NaN;
  if (!Number.isFinite(start)) return settled(poseOf(name, since, now, view));

  const origin = poseOf(from.name, from.since, start, view); // 出発点（切り替えた時刻）
  const startGoal = poseOf(name, since, start, view);        // 切り替えた時刻の目的地
  const target = poseOf(name, since, now, view);             // 今の目的地
  const dist = Math.hypot(startGoal.x - origin.x, startGoal.y - origin.y, startGoal.z - origin.z);
  const seconds = clamp(dist / TRAVEL.speed, TRAVEL.min, TRAVEL.max);
  const u = clamp((now - start) / (seconds * 1000), 0, 1);
  const s = u * u * (3 - 2 * u);
  const lift = Math.min(TRAVEL.lift, TRAVEL.liftPerMetre * dist) * Math.sin(Math.PI * s);
  const moving = Math.sin(Math.PI * u);
  const hx = target.x - origin.x, hz = target.z - origin.z;
  const heading = Math.hypot(hx, hz) < TRAVEL.near ? target.yaw : Math.atan2(hx, hz);
  const end = u < 0.5 ? origin : target;
  return settled({
    x: mix(origin.x, target.x, s),
    y: mix(origin.y, target.y, s) + lift,
    z: mix(origin.z, target.z, s),
    yaw: mixAngle(end.yaw, heading, moving),
    pitch: mix(end.pitch, TRAVEL.pitch, moving),
    sit: end.sit * (1 - moving),
  });
}
