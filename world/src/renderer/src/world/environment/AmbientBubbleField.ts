import * as THREE from 'three'

const VERTEX_SHADER = /* glsl */ `
  uniform float time;
  attribute float size;
  attribute float speed;
  attribute float phase;
  attribute float cluster;
  varying float vAlpha;

  void main() {
    vec3 animated = position;
    animated.y = 0.08 + mod(position.y + time * speed + phase, 5.7);
    animated.x += sin(time * 0.31 + phase * 4.1) * mix(0.05, 0.16, cluster);
    animated.z += cos(time * 0.24 + phase * 3.3) * mix(0.04, 0.12, cluster);
    vec4 viewPosition = modelViewMatrix * vec4(animated, 1.0);
    gl_PointSize = size * clamp(3.8 / max(-viewPosition.z, 0.8), 0.45, 2.4);
    gl_Position = projectionMatrix * viewPosition;
    vAlpha = mix(0.16, 0.34, cluster) * (0.82 + 0.18 * sin(time + phase * 8.0));
  }
`

const FRAGMENT_SHADER = /* glsl */ `
  varying float vAlpha;

  void main() {
    vec2 centered = gl_PointCoord - vec2(0.5);
    float radius = length(centered);
    if (radius > 0.5) discard;
    float ring = smoothstep(0.46, 0.30, radius) - smoothstep(0.29, 0.14, radius);
    float highlight = 1.0 - smoothstep(0.0, 0.08, length(centered - vec2(-0.15, 0.16)));
    gl_FragColor = vec4(vec3(0.32, 0.66, 0.70), (ring * 0.72 + highlight * 0.46) * vAlpha);
  }
`

export function createAmbientBubbleField(count: number): THREE.Points<
  THREE.BufferGeometry,
  THREE.ShaderMaterial
> {
  const random = createRandom(0x4255424c)
  const positions = new Float32Array(count * 3)
  const sizes = new Float32Array(count)
  const speeds = new Float32Array(count)
  const phases = new Float32Array(count)
  const clusters = new Float32Array(count)

  for (let index = 0; index < count; index += 1) {
    const clustered = random() < 0.72
    const clusterSide = random() < 0.5 ? -1 : 1
    const centerX = clustered ? clusterSide * (0.7 + random() * 2.3) : (random() - 0.5) * 17
    const centerZ = clustered ? -1.2 - random() * 5.0 : 1.5 - random() * 16
    positions[index * 3] = centerX + (random() - 0.5) * (clustered ? 1.05 : 3.2)
    positions[index * 3 + 1] = random() * 5.7
    positions[index * 3 + 2] = centerZ + (random() - 0.5) * (clustered ? 1.8 : 3.4)
    sizes[index] = 0.65 + Math.pow(random(), 1.8) * 1.25
    speeds[index] = 0.16 + random() * 0.52
    phases[index] = random() * 5.7
    clusters[index] = clustered ? 1 : 0
  }

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1))
  geometry.setAttribute('speed', new THREE.BufferAttribute(speeds, 1))
  geometry.setAttribute('phase', new THREE.BufferAttribute(phases, 1))
  geometry.setAttribute('cluster', new THREE.BufferAttribute(clusters, 1))

  const material = new THREE.ShaderMaterial({
    uniforms: { time: { value: 0 } },
    vertexShader: VERTEX_SHADER,
    fragmentShader: FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    blending: THREE.NormalBlending
  })
  const points = new THREE.Points(geometry, material)
  points.name = 'Environment:bubbles:ambient'
  return points
}

function createRandom(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0
    return state / 0x100000000
  }
}
