import * as THREE from 'three';
import { UnderwaterEnvironment } from './environment.js';
import { loadAvatar } from './avatar.js';
import { WorldCamera, installCameraInput } from './camera.js';
import { WorldFrameLoop } from './frame-loop.js';
import { readSeaSettings, installSeaDebug } from './sea-debug.js';
import { installEnvironmentDebug } from './environment-debug.js';
import { environmentHourFromDate } from './environment-profiles.js';

const ENVIRONMENT_CLOCK_INTERVAL_MS = 30_000;

class WorldDisplay {
  constructor() {
    this.canvas = document.getElementById('worldCanvas');
    this.status = document.getElementById('worldStatus');
    this.motion = document.getElementById('worldMotion');
    this.focusButton = document.getElementById('worldFocus');
    this.media = matchMedia('(prefers-reduced-motion: reduce)');
    this.ready = false;
    this.disposed = false;
    this.lost = false;
    this.paused = false;
    this.frames = 0;
    this.clock = new WorldFrameLoop({
      available: () => this.ready && !this.disposed && !this.lost && !document.hidden,
      continuous: () => this.animating || this.input?.active,
      draw: delta => { this.input?.update(delta); this.render(this.animating ? delta : 0); },
    });
    this.avatarSerial = 0;
    this.avatarKey = null;
    this.avatars = [];
    this.avatarMessage = '';
    this.abort = new AbortController();
    this.seaSettings = readSeaSettings();
    this.environmentHour = environmentHourFromDate();
    this.environmentTimeLive = true;
    const seaDebug = document.getElementById('seaDebug');
    this.environmentDebug = installEnvironmentDebug(seaDebug, this.environmentHour, (environmentHour, live) => {
      this.environmentHour = environmentHour;
      this.environmentTimeLive = live;
      this.environment?.setEnvironmentHour(environmentHour);
      this.requestRender();
    }, this.abort.signal);
    // Transitions span hours, so a 30-second clock sample is visually continuous while staying idle-friendly.
    this.environmentClock = window.setInterval(() => {
      this.environmentDebug?.syncLive(environmentHourFromDate());
    }, ENVIRONMENT_CLOCK_INTERVAL_MS);
    installSeaDebug(seaDebug, this.seaSettings, settings => {
      this.seaSettings = settings;
      this.environment?.configure(settings);
      this.requestRender();
    }, this.abort.signal);
    const options = { signal: this.abort.signal };
    this.motion.addEventListener('click', () => { this.paused = !this.paused; this.syncMotion(); }, options);
    document.getElementById('worldHome').addEventListener('click', () => {
      this.input?.clear(); this.rig?.home(); this.updateMode(); this.requestRender(); this.canvas.focus();
    }, options);
    this.focusButton.addEventListener('click', () => { this.input?.clear(); this.rig?.unlock(); this.updateMode(); this.requestRender(); this.canvas.focus(); }, options);
    this.media.addEventListener('change', () => this.syncMotion(), options);
    document.addEventListener('visibilitychange', () => this.syncMotion(), options);
    window.addEventListener('pagehide', () => this.dispose(), options);
    window.addEventListener('nirai:avatar-changed', () => {
      this.avatarKey = null;
      window.niraiDashboard.snapshot().then(snapshot => this.acceptSnapshot(snapshot)).catch(() => {});
    }, options);
    this.canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      this.lost = true;
      this.canvas.hidden = true;
      this.reportAvatars('unavailable');
      this.input?.clear();
      this.setStatus('描画を復旧中です。海の静止画を表示しています。');
      this.syncMotion();
    }, options);
    this.unsubscribeDisconnect = window.niraiDashboard?.onHubDisconnected(() => {
      // Rebind model reads and observations to the next Hub connection together.
      // A failed read or a lost acknowledgement must not be cached as current.
      this.avatarKey = null;
      this.avatarSerial++;
    });
    this.unsubscribe = window.niraiDashboard?.onSnapshotChanged(snapshot => this.acceptSnapshot(snapshot));
    window.niraiDashboard?.snapshot().then(snapshot => this.acceptSnapshot(snapshot)).catch(() => {});
    void this.start();
  }

  setStatus(text) { this.status.textContent = text; this.status.hidden = !text; }
  get frame() { return this.clock.frame; }
  get animating() { return !this.paused && !this.media.matches; }
  requestRender() { this.clock.invalidate(); }
  updateMode() {
    this.focusButton.hidden = !this.rig?.focus;
    this.focusButton.textContent = this.rig?.focus ? `Focus: ${this.rig.focus.name} ×` : 'Focus解除';
  }

  async start() {
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true, powerPreference: 'low-power' });
      // Three must rebuild its GPU state before our first restored frame, including when motion is paused.
      this.canvas.addEventListener('webglcontextrestored', () => {
        this.lost = false;
        if (!this.ready || this.disposed) return;
        this.canvas.hidden = false;
        this.renderer.setClearColor(0x0a4f78);
        this.setStatus(this.avatarMessage);
        this.resize(); this.syncMotion();
      }, { signal: this.abort.signal });
      this.renderer.setClearColor(0x0a4f78);
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      // The sea and the characters share one linear lighting model; highlights roll off instead of clipping.
      this.renderer.toneMapping = THREE.NeutralToneMapping;
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      this.renderer.info.autoReset = false;
      this.renderer.debug.onShaderError = () => { throw new Error('海の描画プログラムを利用できません。'); };
      this.scene = new THREE.Scene();
      this.camera = new THREE.PerspectiveCamera(55, 1, .05, 220);
      this.rig = new WorldCamera(this.camera);
      this.environment = new UnderwaterEnvironment(this.scene, this.environmentHour);
      this.environment.configure(this.seaSettings);
      await this.environment.load();
      if (this.disposed) return;
      this.ready = true;
      this.canvas.hidden = this.lost;
      this.input = installCameraInput(this.canvas, this.rig, {
        avatars: () => this.avatars, changed: () => this.updateMode(),
        invalidate: () => this.requestRender(),
      });
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(document.querySelector('.world'));
      this.resizeObserver.observe(document.getElementById('dashboard'));
      this.dashboardObserver = new MutationObserver(() => this.resize());
      this.dashboardObserver.observe(document.getElementById('dashboard'), { attributes: true, attributeFilter: ['class'] });
      this.resize();
      if (!this.lost) this.setStatus('');
      this.syncMotion();
      if (this.pendingSnapshot) this.acceptSnapshot(this.pendingSnapshot);
    } catch (error) {
      if (!this.disposed) { console.error('World initialization:', error); this.fail(); }
    }
  }

  fail() {
    this.ready = false; this.canvas.hidden = true;
    this.setStatus('海の描画を利用できないため、静止画を表示しています。');
    this.releaseGraphics(); this.motion.disabled = true;
  }

  acceptSnapshot(snapshot) {
    if (this.disposed) return;
    if ((snapshot.revision ?? 0) < (this.pendingSnapshot?.revision ?? 0)) return;
    this.pendingSnapshot = snapshot;
    if (!this.ready) return;
    const paths = snapshot.settings?.value?.resident_avatars ?? {};
    const entries = (snapshot.residents ?? []).filter(resident => paths[resident.id]);
    const key = JSON.stringify(entries.map(resident => [resident.id, paths[resident.id]]));
    if (key === this.avatarKey) {
      this.applySavedAppearance(snapshot);
      return;
    }
    this.avatarKey = key;
    const serial = ++this.avatarSerial;
    this.input?.clear(); this.rig.unlock(); this.updateMode();
    this.reportAvatars('unavailable');
    for (const avatar of this.avatars) avatar.dispose();
    this.avatars = [];
    this.environment.fitShadow(this.avatars);
    this.avatarMessage = entries.length ? 'キャラクターを読み込み中…' : 'Resident設定からVRMを選択できます。';
    if (!this.lost) this.setStatus(this.avatarMessage);
    this.requestRender();
    // One decoder at a time, including rapid replacement requests.
    this.loading = (this.loading ?? Promise.resolve()).catch(() => {}).then(async () => {
      if (serial !== this.avatarSerial || this.disposed || !this.ready) return;
      const failed = [];
      const warnings = [];
      for (const resident of entries) {
        if (serial !== this.avatarSerial || this.disposed || !this.ready) return;
        try {
          const source = await window.niraiDashboard.readAvatar(resident.id);
          if (serial !== this.avatarSerial || this.disposed || !this.ready) return;
          if (!source) throw new Error('モデルが未設定です。');
          const avatar = await loadAvatar(source.bytes, this.environment.optics.uniforms);
          if (serial !== this.avatarSerial || this.disposed || !this.ready) { avatar.dispose(); return; }
          avatar.id = resident.id;
          avatar.modelId = source.model_id;
          avatar.token = source.token;
          avatar.appearanceRevision = null;
          avatar.name = resident.display_name ?? resident.id;
          warnings.push(...avatar.warnings.map(warning => `${avatar.name}: ${warning}`));
          const index = entries.indexOf(resident);
          const angle = index * Math.PI * 2 / entries.length;
          avatar.root.position.set(entries.length === 1 ? 0 : Math.sin(angle) * 1.4, .12,
            -.55 + (entries.length === 1 ? 0 : Math.cos(angle) * 1.4));
          this.avatars.push(avatar);
          this.scene.add(avatar.root);
          this.environment.fitShadow(this.avatars);
          this.applySavedAppearance(this.pendingSnapshot);
          this.requestRender();
        } catch (error) {
          if (serial !== this.avatarSerial || this.disposed || !this.ready) return;
          console.warn('Avatar load:', resident.id, error);
          failed.push(resident.display_name ?? resident.id);
        }
      }
      this.avatarMessage = failed.length ? `${failed.join('、')}のVRMを読み込めません。Resident設定から選び直してください。`
        : entries.length ? '' : 'Resident設定からVRMを選択できます。';
      this.avatarMessage = [this.avatarMessage, ...warnings].filter(Boolean).join(' ');
      if (!this.lost) this.setStatus(this.avatarMessage);
    });
  }

  applySavedAppearance(snapshot) {
    let needsFrame = false;
    for (const avatar of this.avatars) {
      const state = snapshot.avatar_states?.find(item => item.resident_id === avatar.id);
      if (state?.token !== avatar.token && !avatar.reportPending) {
        avatar.reportKey = null; // Hub reconnected: re-observe this runtime.
        needsFrame = true;
      }
      if (state?.model_id !== avatar.modelId || state.token !== avatar.token || !state.desired) continue;
      if (avatar.appearanceRevision === state.desired.revision) continue;
      needsFrame = true;
      try {
        avatar.applyAppearance(state.desired.appearance, { immediate: this.paused || this.media.matches || document.hidden });
        avatar.appearanceRevision = state.desired.revision;
        avatar.appearanceError = null;
      } catch (error) {
        avatar.appearanceError = String(error.message ?? error);
        this.setStatus(`${avatar.name}の表現を反映できません。${avatar.appearanceError}`);
      }
    }
    if (needsFrame) this.requestRender();
  }

  reportAvatars(status) {
    for (const avatar of this.avatars) {
      if (status === 'ready' && !avatar.settled) continue;
      const report = {
        status: avatar.appearanceError ? 'unavailable' : status,
        applied_revision: avatar.appearanceRevision,
        ...(avatar.appearanceError ? { error: avatar.appearanceError } : {}),
      };
      // The catalog is fixed for this loaded Avatar. Compare only changing
      // observations each frame; attach the full catalog only when sending.
      const key = JSON.stringify(report);
      if (key === avatar.reportKey) continue;
      avatar.reportKey = key;
      report.capabilities = avatar.catalog;
      // Ordered observations cannot let an old settled frame overwrite a later outage.
      const pending = (avatar.reportPending ?? Promise.resolve()).catch(() => {}).then(() =>
        window.niraiDashboard.reportAvatar(avatar.id, avatar.token, report));
      avatar.reportPending = pending;
      void pending.catch(() => {}).finally(() => {
        if (avatar.reportPending === pending) avatar.reportPending = null;
      });
    }
  }

  resize() {
    if (!this.ready || this.disposed || this.lost) return;
    const width = Math.max(1, this.canvas.clientWidth), height = Math.max(1, this.canvas.clientHeight);
    const ratio = Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(1_600_000 / (width * height)));
    this.renderer.setPixelRatio(ratio);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    const dashboard = document.getElementById('dashboard');
    const box = dashboard.getBoundingClientRect();
    let area = { x: 0, y: 0, width, height };
    if (dashboard.classList.contains('is-open')) {
      area = box.top > height * .15
        ? { x: 0, y: 0, width, height: Math.max(60, box.top) }
        : { x: box.right, y: 0, width: Math.max(45, width - box.right), height };
    }
    // Shift the lens toward the exposed World; characters stay at fixed world coordinates.
    this.camera.setViewOffset(width, height, width / 2 - (area.x + area.width / 2), height / 2 - (area.y + area.height / 2), width, height);
    this.camera.fov = Math.min(78, Math.max(55, 2 * THREE.MathUtils.radToDeg(Math.atan(1.15 / 4.2 * height / Math.max(100, area.height * .8)))));
    this.camera.updateProjectionMatrix();
    this.environment.resize(this.renderer.domElement.height, this.camera.fov);
    this.requestRender();
  }

  render(delta = 0) {
    if (!this.ready || this.lost || this.disposed) return;
    try {
      this.renderer.info.reset();
      this.environment.update(delta);
      this.environment.renderCaustics(this.renderer);
      this.rig.updateFocus();
      for (const avatar of this.avatars) avatar.update(delta, this.camera, this.rig.focus === avatar);
      this.renderer.render(this.scene, this.camera);
      this.frames++;
      this.reportAvatars(document.hidden ? 'unavailable' : 'ready');
    } catch (error) { console.error('World render:', error); this.fail(); }
  }

  syncMotion() {
    this.motion.textContent = this.paused ? '動きを再開' : '動きを止める';
    this.motion.setAttribute('aria-pressed', String(this.paused));
    this.motion.disabled = this.media.matches || !this.ready;
    if (this.media.matches) this.motion.textContent = '動きを抑えています';
    if (document.hidden) this.reportAvatars('unavailable');
    else if (this.paused || this.media.matches) {
      for (const avatar of this.avatars) avatar.applyAppearance(avatar.appearance, { immediate: true });
    }
    this.clock.sync();
  }

  releaseGraphics() {
    this.clock.stop(); this.avatarSerial++;
    this.input?.dispose(); this.resizeObserver?.disconnect(); this.dashboardObserver?.disconnect();
    this.reportAvatars('unavailable');
    for (const avatar of this.avatars) avatar.dispose();
    this.avatars = [];
    this.environment?.dispose(); this.renderer?.dispose();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    window.clearInterval(this.environmentClock);
    this.abort.abort(); this.unsubscribe?.(); this.unsubscribeDisconnect?.(); this.releaseGraphics();
  }
}

export const world = new WorldDisplay();
