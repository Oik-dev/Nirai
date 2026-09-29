// Adapted from Nirai v1: environment rendering only.
import * as THREE from 'three';
import { createSeededRandom } from './sea-random.js';

const AMBIENT_BUBBLE_ANCHOR_BANDS = [
    [[-4.3, -2.2], [-2.7, -3.6], [-6.0, -7.0]],
    [[0.0, -2.8], [-2.5, -8.5], [1.5, -7.0], [0.0, -12.0]],
    [[3.0, -3.7], [4.6, -2.4], [5.5, -8.5]]
];
export const AMBIENT_BUBBLE_HORIZONTAL_DENSITY = 2.8;
export const AMBIENT_BUBBLE_STREAM_COUNT = 3;
export const AMBIENT_BUBBLE_TIME_SCALE = 1.8;
const BUBBLE_PLACEMENT_SEED = 0x4255424c;
const BUBBLE_ANCHOR_SEED = 0x5354524d;
const VERTEX_SHADER = /* glsl */ `
  uniform float time;
  uniform float horizontalDensity;
  attribute float size;
  attribute float speed;
  attribute float phase;
  attribute float cluster;
  attribute vec2 streamOffset;
  attribute float shape;
  varying float vAlpha;
  varying float vPhase;
  varying float vShape;
  varying float vDetail;

  void main() {
    vec3 animated = position;
    float cycle = mod(position.y + time * speed + phase, 5.7);
    float rise = cycle / 5.7;
    float spread = mix(0.42, 1.16, smoothstep(0.0, 1.0, rise));
    float crossSectionScale = inversesqrt(max(horizontalDensity, 0.2));
    animated.y = 0.08 + cycle;
    animated.x += (
      streamOffset.x * spread
      + sin(time * 0.27 + rise * 4.8 + phase * 3.1) * mix(0.05, 0.15, cluster)
    ) * crossSectionScale;
    animated.z += (
      streamOffset.y * spread
      + cos(time * 0.21 + rise * 3.9 + phase * 2.7) * mix(0.04, 0.12, cluster)
    ) * crossSectionScale;
    vec4 viewPosition = modelViewMatrix * vec4(animated, 1.0);
    gl_PointSize = size * clamp(10.0 / max(-viewPosition.z, 0.8), 0.90, 3.0);
    gl_Position = projectionMatrix * viewPosition;
    float lifecycle = smoothstep(0.0, 0.08, rise) * (1.0 - smoothstep(0.86, 1.0, rise));
    vAlpha = mix(0.58, 0.94, cluster)
      * lifecycle
      * (0.90 + 0.10 * sin(time * 0.72 + phase * 8.0));
    vPhase = phase;
    vShape = shape;
    vDetail = smoothstep(4.6, 6.4, size);
  }
`;
const FRAGMENT_SHADER = /* glsl */ `
  uniform float time;
  varying float vAlpha;
  varying float vPhase;
  varying float vShape;
  varying float vDetail;

  void main() {
    vec2 bubble = gl_PointCoord - vec2(0.5);
    float shapeMotion = sin(time * 0.46 + vPhase * 5.2);
    float aspect = 1.0 + (vShape - 0.5) * 0.24 + shapeMotion * 0.030;
    bubble.x *= aspect;
    bubble.y /= aspect;

    float angle = atan(bubble.y, bubble.x);
    float edgeRadius = 0.39
      + 0.018 * sin(angle * 2.0 + vPhase * 6.28318)
      + 0.009 * sin(angle * 3.0 - time * 0.34 + vPhase * 2.7);
    float radius = length(bubble);
    float distanceToFilm = edgeRadius - radius;
    if (distanceToFilm < 0.0) discard;

    float litSide = mix(0.66, 1.0, 0.5 + 0.5 * sin(angle + 2.25));
    float rim = (1.0 - smoothstep(0.0, 0.085, distanceToFilm)) * litSide;
    vec2 highlightUv = (bubble - vec2(-0.145, 0.14)) * vec2(0.92, 1.55);
    float highlightDistance = length(highlightUv);
    float crescent = smoothstep(0.055, 0.105, highlightDistance)
      * (1.0 - smoothstep(0.13, 0.19, highlightDistance));
    float film = (1.0 - smoothstep(0.0, edgeRadius, radius)) * 0.10;
    float microGlint = (1.0 - smoothstep(0.04, 0.25, radius)) * (1.0 - vDetail);
    float alpha = (rim * 0.98 + crescent * 0.78 + film + microGlint * 0.58) * vAlpha;
    vec3 color = mix(
      vec3(0.68, 0.91, 0.98),
      vec3(0.94, 0.99, 1.0),
      max(crescent, microGlint)
    );
    gl_FragColor = vec4(color, alpha);
  }
`;
export function createAmbientBubbleField(capacity, streamCount = AMBIENT_BUBBLE_STREAM_COUNT) {
    const random = createSeededRandom(BUBBLE_PLACEMENT_SEED);
    const activeAnchors = selectAmbientBubbleAnchors(streamCount);
    const positions = new Float32Array(capacity * 3);
    const sizes = new Float32Array(capacity);
    const speeds = new Float32Array(capacity);
    const phases = new Float32Array(capacity);
    const clusters = new Float32Array(capacity);
    const streamOffsets = new Float32Array(capacity * 2);
    const shapes = new Float32Array(capacity);
    for (let index = 0; index < capacity; index += 1) {
        const anchored = random() < 0.94;
        const anchor = activeAnchors[index % activeAnchors.length];
        const centerX = anchored ? anchor[0] : (random() - 0.5) * 17;
        const centerZ = anchored ? anchor[1] : 1.0 - random() * 16;
        const offsetAngle = random() * Math.PI * 2;
        const offsetRadius = Math.pow(random(), anchored ? 1.8 : 1.2);
        positions[index * 3] = centerX;
        positions[index * 3 + 1] = random() * 5.7;
        positions[index * 3 + 2] = centerZ;
        streamOffsets[index * 2] = Math.cos(offsetAngle) * offsetRadius * (anchored ? 0.62 : 1.35);
        streamOffsets[index * 2 + 1] = Math.sin(offsetAngle) * offsetRadius * (anchored ? 0.82 : 1.55);
        const largeBubble = random() < 0.08;
        sizes[index] = largeBubble
            ? 4.8 + random() * 2.0
            : 1.5 + Math.pow(random(), 1.7) * 2.2;
        speeds[index] = 0.16 + random() * 0.38;
        phases[index] = random() * 5.7;
        clusters[index] = anchored ? 0.62 + random() * 0.38 : 0.12 + random() * 0.28;
        shapes[index] = random();
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));
    geometry.setAttribute('speed', new THREE.BufferAttribute(speeds, 1));
    geometry.setAttribute('phase', new THREE.BufferAttribute(phases, 1));
    geometry.setAttribute('cluster', new THREE.BufferAttribute(clusters, 1));
    geometry.setAttribute('streamOffset', new THREE.BufferAttribute(streamOffsets, 2));
    geometry.setAttribute('shape', new THREE.BufferAttribute(shapes, 1));
    const material = new THREE.ShaderMaterial({
        uniforms: {
            time: { value: 0 },
            horizontalDensity: { value: AMBIENT_BUBBLE_HORIZONTAL_DENSITY }
        },
        vertexShader: VERTEX_SHADER,
        fragmentShader: FRAGMENT_SHADER,
        transparent: true,
        depthWrite: false,
        blending: THREE.NormalBlending
    });
    const points = new THREE.Points(geometry, material);
    points.name = 'Environment:bubbles:ambient';
    return points;
}
function selectAmbientBubbleAnchors(streamCount) {
    const safeCount = Math.max(2, Math.min(3, Math.round(streamCount)));
    const random = createSeededRandom(BUBBLE_ANCHOR_SEED ^ safeCount);
    const selectedBands = safeCount === 2
        ? [AMBIENT_BUBBLE_ANCHOR_BANDS[0], AMBIENT_BUBBLE_ANCHOR_BANDS[2]]
        : AMBIENT_BUBBLE_ANCHOR_BANDS;
    return selectedBands.map((band) => band[Math.floor(random() * band.length)]);
}
