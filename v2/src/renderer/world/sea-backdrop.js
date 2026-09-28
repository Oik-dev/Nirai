import * as THREE from 'three';
import { UNDERWATER_OPTICS_GLSL } from './optics.js';

const DOME_RADIUS = 190;
const DOME_WIDTH_SEGMENTS = 48;
const DOME_HEIGHT_SEGMENTS = 24;
const OPEN_WATER_RENDER_ORDER = 10;

const BACKDROP_VERTEX = /* glsl */ `
varying vec3 vWorld;
void main() {
  vec4 p = modelMatrix * vec4( position, 1.0 );
  vWorld = p.xyz;
  gl_Position = projectionMatrix * viewMatrix * p;
}`;

const BACKDROP_FRAGMENT = /* glsl */ `
${UNDERWATER_OPTICS_GLSL}
varying vec3 vWorld;
void main() {
  vec3 dir = normalize( vWorld - cameraPosition );
  gl_FragColor = vec4( uwInscatter( cameraPosition, dir, 400.0, 12 ), 1.0 );
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  gl_FragColor = uwDither( gl_FragColor, gl_FragCoord.xy );
}`;

export function createOpenWaterDome(opticsUniforms) {
  const material = new THREE.ShaderMaterial({
    uniforms: opticsUniforms, vertexShader: BACKDROP_VERTEX, fragmentShader: BACKDROP_FRAGMENT,
    side: THREE.BackSide, depthWrite: false,
  });
  // Drawn after the floor and surface so only the uncovered band of open water is shaded.
  const dome = new THREE.Mesh(new THREE.SphereGeometry(DOME_RADIUS, DOME_WIDTH_SEGMENTS, DOME_HEIGHT_SEGMENTS), material);
  dome.name = 'Environment:openWater';
  dome.renderOrder = OPEN_WATER_RENDER_ORDER;
  dome.frustumCulled = false;
  return dome;
}
