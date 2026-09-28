import * as THREE from 'three';
import { UNDERWATER_OPTICS_GLSL } from './optics.js';

// Fine particulate that drifts around the viewer. It lights up where it crosses a
// sun shaft, which is what makes the shafts read as volume rather than a painted glow.
const VERTEX_SHADER = /* glsl */ `
  ${UNDERWATER_OPTICS_GLSL}
  uniform vec3 box;
  uniform float pixelScale;
  attribute float size;
  attribute vec3 drift;
  attribute float phase;
  varying float vAlpha;
  varying vec3 vLight;

  void main() {
    vec3 seed = position + drift * uwTime;
    vec3 world;
    world.xz = cameraPosition.xz + ( fract( seed.xz - cameraPosition.xz / box.xz ) - 0.5 ) * box.xz;
    world.y = uwFloorY + 0.12 + fract( seed.y ) * ( uwSurfaceY - uwFloorY - 0.3 );
    world += vec3( sin( uwTime * 0.31 + phase * 6.2831 ), sin( uwTime * 0.23 + phase * 4.1 ), cos( uwTime * 0.27 + phase * 5.3 ) ) * 0.035;
    vec4 view = viewMatrix * vec4( world, 1.0 );
    float depth = max( -view.z, 0.05 );
    float radius = length( world - cameraPosition );
    float edge = 1.0 - smoothstep( box.x * 0.34, box.x * 0.5, length( world.xz - cameraPosition.xz ) );
    float near = smoothstep( 0.25, 0.9, radius );
    float lit = min( uwShaftPattern( world, vec2( 0.0 ) ), 1.0 );
    vLight = uwSunColor * uwSunAttenuation( world.y ) * ( 0.05 + lit * 0.55 ) + uwZenithColor * 0.25;
    vLight *= uwTransmittance( radius );
    float pixels = size * pixelScale / depth;
    vAlpha = edge * near * clamp( pixels, 0.0, 1.0 ) * ( 0.35 + 0.65 * lit );
    gl_PointSize = clamp( pixels, 1.0, 5.0 );
    gl_Position = projectionMatrix * view;
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  varying float vAlpha;
  varying vec3 vLight;

  void main() {
    float r = length( gl_PointCoord - 0.5 );
    float disc = 1.0 - smoothstep( 0.18, 0.5, r );
    if ( disc <= 0.0 ) discard;
    gl_FragColor = vec4( vLight * disc * vAlpha, 1.0 );
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`;

export function createSuspendedParticleField(opticsUniforms, count = 1600) {
  const random = createRandom(0x534e4f57);
  const positions = new Float32Array(count * 3);
  const sizes = new Float32Array(count);
  const drifts = new Float32Array(count * 3);
  const phases = new Float32Array(count);
  for (let index = 0; index < count; index++) {
    positions.set([random(), random(), random()], index * 3);
    sizes[index] = 0.004 + Math.pow(random(), 2.2) * 0.011;
    // Box-relative drift per second: a slow current plus individual wander.
    drifts.set([0.0021 + (random() - 0.5) * 0.002, 0.0009 + (random() - 0.5) * 0.0028, -0.0012 + (random() - 0.5) * 0.002], index * 3);
    phases[index] = random();
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));
  geometry.setAttribute('drift', new THREE.BufferAttribute(drifts, 3));
  geometry.setAttribute('phase', new THREE.BufferAttribute(phases, 1));
  const material = new THREE.ShaderMaterial({
    uniforms: { ...opticsUniforms, box: { value: new THREE.Vector3(14, 1, 14) }, pixelScale: { value: 900 } },
    vertexShader: VERTEX_SHADER,
    fragmentShader: FRAGMENT_SHADER,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(geometry, material);
  points.name = 'Environment:particles:suspended';
  points.frustumCulled = false;
  points.userData.renderMode = 'camera-wrapped-shaft-lit-particulate';
  return {
    points,
    resize(heightPixels, fovDegrees) {
      material.uniforms.pixelScale.value = heightPixels / (2 * Math.tan(THREE.MathUtils.degToRad(fovDegrees) / 2));
    },
  };
}

function createRandom(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}
