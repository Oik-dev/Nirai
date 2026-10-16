// 体の状態は窓の上に重ねる。絵の位置だけを使い、状態の正本はLifeと問い。
import * as THREE from 'three';

const BONES = ['hips', 'head', 'leftEye', 'rightEye', 'leftHand', 'rightHand', 'neck', 'upperChest'];
const worldPoint = new THREE.Vector3();

export function limitLabel(until, known = true, now = Date.now()) {
  const remaining = Date.parse(until) - now;
  if (!Number.isFinite(remaining) || remaining <= 0) return null;
  if (!known) {
    const minutes = Math.floor(remaining / 60000);
    return `約${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`;
  }
  const seconds = Math.ceil(remaining / 1000);
  return `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export function markLabels(post, asked = false, now = Date.now()) {
  if (!post && !asked) return [];
  const result = [];
  const remaining = post?.limitUntil ? limitLabel(post.limitUntil, post.limitKnown, now) : null;
  if (remaining) result.push({ kind: 'limit', text: remaining });
  if (asked) result.push({ kind: 'ask', text: '?' });
  if (post?.stuck) result.push({ kind: 'stuck', text: '!' });
  if (post?.awake) result.push({ kind: 'thinking', text: '…' });
  return result;
}

export function markAnchor(body, out = new THREE.Vector3()) {
  const humanoid = body?.vrm?.humanoid;
  const hips = humanoid?.getNormalizedBoneNode?.('hips');
  if (!hips) return null;
  hips.getWorldPosition(out);
  const y = out.y;
  let top = y;
  for (const name of BONES) {
    const bone = humanoid.getNormalizedBoneNode(name);
    if (!bone) continue;
    bone.getWorldPosition(worldPoint);
    top = Math.max(top, worldPoint.y);
  }
  out.y = top + Math.max(0.13, top - y > 0.3 ? 0.10 : 0.16);
  return out;
}

export class StatusMarks {
  constructor(host = document.getElementById('world')) {
    this.host = host;
    this.nodes = new Map();
    this.point = new THREE.Vector3();
    this.projected = new THREE.Vector3();
  }

  update(avatars, camera, canvas, asks = []) {
    if (!this.host || !canvas) return;
    const now = Date.now();
    const waiting = new Set(asks.map(ask => ask.resident));
    const rect = canvas.getBoundingClientRect();
    const active = new Set();
    for (const [name, avatar] of avatars) {
      active.add(name);
      let node = this.nodes.get(name);
      if (!node) {
        node = document.createElement('div');
        node.className = 'resident-marks';
        this.host.append(node);
        this.nodes.set(name, node);
      }
      const anchor = markAnchor(avatar.body, this.point);
      if (!anchor) { node.hidden = true; continue; }
      this.projected.copy(anchor).project(camera);
      if (this.projected.z < -1 || this.projected.z > 1 ||
          Math.abs(this.projected.x) > 1.1 || Math.abs(this.projected.y) > 1.1) {
        node.hidden = true;
        continue;
      }
      node.hidden = false;
      node.style.left = `${rect.left + (this.projected.x + 1) * rect.width / 2}px`;
      node.style.top = `${rect.top + (1 - this.projected.y) * rect.height / 2}px`;
      const post = avatar.body.life?.post;
      const flags = markLabels(post, waiting.has(name), now);
      const unread = post?.unread > 0 ? `✉${post.unread > 1 ? ' ' + post.unread : ''}` : '';
      const key = flags.map(flag => flag.kind + ':' + flag.text).join('|') + '|' + unread;
      if (node.dataset.key === key) continue;
      node.dataset.key = key;
      node.replaceChildren();
      for (const flag of flags) {
        const el = document.createElement(flag.kind === 'ask' ? 'button' : 'span');
        el.className = `resident-mark resident-mark-${flag.kind}`;
        el.textContent = flag.text;
        if (flag.kind === 'ask') {
          el.type = 'button';
          el.setAttribute('aria-label', `${name}への確認を開く`);
          el.addEventListener('click', () => document.getElementById('asksButton')?.click());
        }
        node.append(el);
      }
      if (unread) {
        const el = document.createElement('span');
        el.className = 'resident-mark resident-mark-unread';
        el.textContent = unread;
        node.append(el);
      }
    }
    for (const [name, node] of this.nodes) {
      if (!active.has(name)) { node.remove(); this.nodes.delete(name); }
    }
  }

  dispose() {
    for (const node of this.nodes.values()) node.remove();
    this.nodes.clear();
  }
}
