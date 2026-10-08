import * as THREE from 'three';
import { UnderwaterEnvironment } from './sea/environment.js';
import { environmentHourFromDate } from './sea/environment-profiles.js';
import { loadAvatar } from './body/avatar.js';
import { BodyChoices } from './body/choices.js';
import { WorldCamera, installCameraInput } from './sea/camera.js';
import { WorldFrameLoop } from './sea/frame-loop.js';
import { startChat } from './chat.js';

class SeaWindow {
  constructor() {
    this.canvas = document.getElementById('worldCanvas');
    this.status = document.getElementById('worldStatus');
    this.motion = document.getElementById('worldMotion');
    this.fallback = document.getElementById('worldFallback');
    this.media = matchMedia('(prefers-reduced-motion: reduce)');
    this.ready = false;
    this.lost = false;
    this.paused = false;
    this.avatar = null;
    this.abort = new AbortController();
    this.choices = new BodyChoices({
      body: () => this.avatar?.body,
      reloadAvatar: signal => this.reloadAvatar(signal),
      invalidate: () => this.clock.invalidate(),
      onError: error => {
        console.error(error);
        this.status.textContent = error instanceof Error ? error.message : '体を読み込めませんでした。';
      },
    });
    this.bodyReady = new Promise(resolve => { this.resolveBodyReady = resolve; });
    this.clock = new WorldFrameLoop({
      available: () => this.ready && !this.lost && !document.hidden,
      continuous: () => (Boolean(this.avatar) && !this.paused && !this.media.matches) || Boolean(this.input?.active),
      draw: delta => {
        this.input?.update(delta);
        this.render(this.paused || this.media.matches ? 0 : delta);
      },
    });
  }

  async start() {
    try {
      this.renderer = new THREE.WebGLRenderer({
        canvas: this.canvas,
        antialias: true,
        alpha: false,
        powerPreference: 'high-performance',
      });
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
      this.renderer.toneMappingExposure = 1;
      this.renderer.info.autoReset = false;

      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(60, 1, 0.05, 220);
      this.rig = new WorldCamera(this.camera);
      this.environment = new UnderwaterEnvironment(this.scene, environmentHourFromDate());
      await this.environment.load();
      if (this.abort.signal.aborted) {
        this.environment.dispose();
        return;
      }
      this.resolveBodyReady(true);
      const bodyLoaded = await this.choices.refresh();
      if (this.abort.signal.aborted) return;

      this.input = installCameraInput(this.canvas, this.rig, {
        avatars: () => this.avatar ? [this.avatar] : [],
        changed: () => this.environment.fitShadow(this.avatar ? [this.avatar] : []),
        invalidate: () => this.clock.invalidate(),
      });

      this.installEvents();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(this.canvas);
      this.ready = true;
      if (bodyLoaded) this.status.textContent = '';
      this.syncMotion();
      this.resize();
      this.clock.invalidate();
    } catch (error) {
      this.resolveBodyReady(false);
      this.choices.dispose();
      console.error(error);
      this.status.textContent = error instanceof Error ? error.message : '海を開けませんでした。';
      this.showFallback();
    }
  }

  async refreshBody() {
    if (await this.bodyReady && !this.abort.signal.aborted && await this.choices.refresh()) this.status.textContent = '';
  }

  async reloadAvatar(signal) {
    const response = await fetch('/avatar.vrm', { cache: 'no-store', signal });
    let avatar = null;
    if (response.ok) {
      const bytes = new Uint8Array(await response.arrayBuffer());
      avatar = await loadAvatar(bytes, this.environment.optics.uniforms);
    } else if (response.status !== 404) throw new Error('Avatarを読み込めませんでした。');
    if (signal.aborted || this.abort.signal.aborted) {
      avatar?.dispose();
      return;
    }
    const previous = this.avatar;
    if (avatar) {
      avatar.root.position.copy(previous?.root.position ?? new THREE.Vector3(0, 0, -.55));
      this.scene.add(avatar.root);
    }
    this.avatar = avatar;
    if (previous && this.rig.focus === previous) {
      if (avatar) this.rig.focus = avatar;
      else this.rig.unlock();
    }
    previous?.dispose();
    this.environment.fitShadow(avatar ? [avatar] : []);
    // 体を試す操作は、窓を ?body 付きで開いたときだけ出す。
    document.getElementById('bodyPanel')?.remove();
    if (avatar && new URLSearchParams(location.search).has('body')) {
      const { mountBodyPanel } = await import('./body/panel.js');
      if (!signal.aborted && !this.abort.signal.aborted) mountBodyPanel(avatar.body, () => this.clock.invalidate());
    }
    this.clock.invalidate();
  }

  installEvents() {
    const options = { signal: this.abort.signal };
    document.addEventListener('visibilitychange', () => this.clock.sync(), options);
    this.media.addEventListener('change', () => this.syncMotion(), options);
    this.motion.addEventListener('click', () => {
      this.paused = !this.paused;
      this.syncMotion();
      this.clock.invalidate();
    }, options);
    this.canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      this.lost = true;
      this.clock.stop();
      this.showFallback();
    }, options);
  }

  syncMotion() {
    this.motion.disabled = this.media.matches || !this.ready;
    this.motion.textContent = this.media.matches
      ? '動きを抑えています'
      : this.paused ? '動きを再開' : '動きを止める';
    this.motion.setAttribute('aria-pressed', String(this.paused));
    this.clock.sync();
  }

  resize() {
    if (!this.ready || this.lost) return;
    const width = Math.max(1, this.canvas.clientWidth);
    const height = Math.max(1, this.canvas.clientHeight);
    const ratio = Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(1_600_000 / (width * height)));
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.environment.resize(this.renderer.domElement.height, this.camera.fov);
    this.clock.invalidate();
  }

  render(delta) {
    if (!this.ready || this.lost) return;
    this.renderer.info.reset();
    this.environment.update(delta);
    this.environment.renderCaustics(this.renderer);
    this.rig.updateFocus();
    this.avatar?.update(delta, this.camera, this.rig.focus === this.avatar);
    this.renderer.render(this.scene, this.camera);
  }

  showFallback() {
    this.fallback.hidden = false;
    this.canvas.hidden = true;
    this.motion.hidden = true;
  }

  dispose() {
    this.ready = false;
    this.clock.stop();
    this.abort.abort();
    this.resolveBodyReady(false);
    this.choices.dispose();
    this.input?.dispose();
    this.resizeObserver?.disconnect();
    this.avatar?.dispose();
    this.environment?.dispose();
    this.renderer?.dispose();
  }
}

const seaWindow = new SeaWindow();
let chatWindow = null;
void seaWindow.start();
// 会話の声（Masterが送った言葉・本人の届いた言葉）を体へ渡す。体は声の方へ顔を向け、話す間は話す型で揺れる。
void startChat({
  onVoice: (speaker, text) => seaWindow.avatar?.body.hear(speaker, text),
  onBody: records => seaWindow.choices.receive(records),
  onCatalog: () => { void seaWindow.refreshBody(); },
  onEventsOpen: () => { void seaWindow.refreshBody(); },
}).then(chat => { chatWindow = chat; }).catch(error => {
  console.error(error);
  document.getElementById('chatStatus').textContent = '会話を読み込めませんでした。';
});
window.addEventListener('pagehide', () => {
  chatWindow?.dispose();
  seaWindow.dispose();
}, { once: true });
