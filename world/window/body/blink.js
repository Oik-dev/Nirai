// 決まった周期でない瞬き。間は人の自然な瞬きに近い対数正規の揺れ（中央値3.4秒、1.2〜9秒）で、ときどき2回続く。
// 話している間は間が短くなる（pace < 1）。閉じるのは速く、開くのはゆっくり。
export const BLINK = Object.freeze({ close: .07, hold: .03, open: .16, median: 3.4, spread: .45, min: 1.2, max: 9, twice: .12, gap: .08 });
export const BLINK_SECONDS = BLINK.close + BLINK.hold + BLINK.open;

// 瞬きが始まってからの秒数 → 閉じ具合（0 開いている〜1 閉じている）
export function blinkShape(t) {
  if (t < 0 || t >= BLINK_SECONDS) return 0;
  if (t < BLINK.close) return (t / BLINK.close) ** 2;
  if (t < BLINK.close + BLINK.hold) return 1;
  const x = (t - BLINK.close - BLINK.hold) / BLINK.open;
  return 1 - x * x * (3 - 2 * x);
}

export class Blinker {
  constructor(random = Math.random) {
    this.random = random;
    this.clock = 0;
    this.start = this.wait(1);
  }

  wait(pace) {
    const u = Math.max(this.random(), 1e-9);
    const normal = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * this.random());
    const seconds = Math.exp(Math.log(BLINK.median) + BLINK.spread * normal) * pace;
    return Math.min(BLINK.max, Math.max(BLINK.min, seconds));
  }

  // delta 秒進めて、今の閉じ具合を返す。
  update(delta, pace = 1) {
    this.clock += delta;
    while (this.clock >= this.start + BLINK_SECONDS) {
      this.start += BLINK_SECONDS + (this.random() < BLINK.twice ? BLINK.gap : this.wait(pace));
    }
    return blinkShape(this.clock - this.start);
  }
}
