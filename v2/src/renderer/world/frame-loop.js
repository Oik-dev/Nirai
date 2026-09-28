const FRAME_INTERVAL = 1000 / 30;

// Input, animation and invalidations share one frame budget. Keep the actual
// elapsed time separate from the render limit so no elapsed fraction is counted twice.
export class WorldFrameLoop {
  constructor({ available, continuous, draw, now = () => performance.now(),
    request = callback => requestAnimationFrame(callback), cancel = id => cancelAnimationFrame(id) }) {
    Object.assign(this, { available, continuous, draw, now, request, cancel });
    this.frame = 0;
    this.pending = false;
    this.previous = 0;
    this.lastDraw = -Infinity;
    this.tick = this.tick.bind(this);
  }

  invalidate() {
    this.pending = true;
    if (!this.available() || this.frame) return;
    // An idle or hidden interval must not become camera motion on the next input.
    this.previous = this.now();
    this.frame = this.request(this.tick);
  }

  sync() {
    if (!this.available()) this.stop();
    else this.invalidate();
  }

  tick(now) {
    this.frame = 0;
    if (!this.available()) { this.stop(); return; }
    // RAF timestamps can fall just below 33.333 ms on a 60 Hz display. A small
    // tolerance avoids alternating between two and three display refreshes.
    if (now - this.lastDraw >= FRAME_INTERVAL - .5) {
      const delta = Math.min(Math.max(0, now - this.previous) / 1000, .05);
      this.previous = now;
      this.lastDraw = now;
      this.pending = false;
      this.draw(delta);
    }
    if (this.available() && (this.pending || this.continuous())) this.frame = this.request(this.tick);
  }

  stop() {
    this.cancel(this.frame);
    this.frame = 0;
    this.pending = false;
  }
}
