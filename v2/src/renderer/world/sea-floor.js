import * as THREE from 'three';
import { UNDERWATER_OPTICS_GLSL, UNDERWATER_SURFACE_GLSL, WATER_OPTICS } from './optics.js';

const FLOOR_SIZE = 400;
const FLOOR_SEGMENTS = 128;
const RIPPLE_X = 0.12;
const RIPPLE_Z = 0.07;
const RIPPLE_HEIGHT = 0.025;

const FLOOR_VERTEX = /* glsl */ `
#include <common>
#include <shadowmap_pars_vertex>
varying vec3 vWorld;
varying vec3 vNormalWorld;
void main() {
  vec3 transformed = position;
  vec3 transformedNormal = normalMatrix * normal;
  vec4 worldPosition = modelMatrix * vec4( transformed, 1.0 );
  vWorld = worldPosition.xyz;
  vNormalWorld = normalize( mat3( modelMatrix ) * normal );
  gl_Position = projectionMatrix * viewMatrix * worldPosition;
  #include <shadowmap_vertex>
}`;

const FLOOR_FRAGMENT = /* glsl */ `
#include <common>
#include <packing>
#include <shadowmap_pars_fragment>
${UNDERWATER_OPTICS_GLSL}
${UNDERWATER_SURFACE_GLSL}
uniform sampler2D sand, relief;
uniform float uwShadowSpan;
uniform float uwShadowDepth;
varying vec3 vWorld;
varying vec3 vNormalWorld;

vec2 uwDisk( int i, int count ) {
  float angle = float( i ) * 2.3999632;
  return vec2( cos( angle ), sin( angle ) ) * sqrt( ( float( i ) + 0.5 ) / float( count ) );
}

#if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
// Bilinear comparison, so a tap slides across texels instead of stepping between them.
float uwShadowTap( vec2 uv, float receiver, vec2 size ) {
  vec2 texel = uv * size - 0.5;
  vec2 base = ( floor( texel ) + 0.5 ) / size;
  vec2 f = fract( texel );
  vec2 next = 1.0 / size;
  return mix(
    mix( texture2DCompare( directionalShadowMap[ 0 ], base, receiver ), texture2DCompare( directionalShadowMap[ 0 ], base + vec2( next.x, 0.0 ), receiver ), f.x ),
    mix( texture2DCompare( directionalShadowMap[ 0 ], base + vec2( 0.0, next.y ), receiver ), texture2DCompare( directionalShadowMap[ 0 ], base + next, receiver ), f.x ),
    f.y );
}
#endif

// Light scattered by the water widens a penumbra with the occluder's height above the sand:
// crisp beside the feet, soft under the head. Nearby blockers in the map give that height.
float characterShadow() {
  float lit = 1.0;
  #if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
    DirectionalLightShadow light = directionalLightShadows[ 0 ];
    vec3 coord = vDirectionalShadowCoord[ 0 ].xyz / vDirectionalShadowCoord[ 0 ].w;
    float receiver = coord.z + light.shadowBias;
    if ( all( greaterThan( coord, vec3( 0.0 ) ) ) && all( lessThan( coord, vec3( 1.0 ) ) ) ) {
      float found = 0.0, blocker = 0.0;
      for ( int i = 0; i < 16; i ++ ) {
        float depth = unpackRGBAToDepth( texture2D( directionalShadowMap[ 0 ], coord.xy + uwDisk( i, 16 ) * ( 0.16 / uwShadowSpan ) ) );
        if ( depth < receiver ) { found += 1.0; blocker += depth; }
      }
      if ( found > 0.0 ) {
        float gap = ( receiver - blocker / found ) * uwShadowDepth;
        float radius = ( 0.004 + gap * 0.055 ) / uwShadowSpan;
        lit = 0.0;
        for ( int i = 0; i < 16; i ++ ) lit += uwShadowTap( coord.xy + uwDisk( i, 16 ) * radius, receiver, light.shadowMapSize );
        lit = mix( 1.0, lit / 16.0, light.shadowIntensity );
      }
    }
  #endif
  return lit;
}

void main() {
  vec2 uv = vWorld.xz * 0.11;
  vec3 grain = texture2D( sand, uv ).rgb;
  vec3 bump = texture2D( relief, uv ).rgb * 2.0 - 1.0;
  vec3 n = normalize( vNormalWorld + vec3( bump.x, 0.0, -bump.y ) * 0.45 );
  float grey = dot( grain, vec3( 0.2126, 0.7152, 0.0722 ) );
  vec3 albedo = vec3( 0.78, 0.84, 0.88 ) * ( 0.76 + grey * 0.28 ) * ( 0.9 + bump.z * 0.12 );

  vec3 caustic = uwCausticLight( vWorld.xz, 0.0 );
  float shadow = characterShadow();
  vec3 sun = uwSunColor * uwSunAttenuation( vWorld.y ) * max( dot( n, uwSunDir ), 0.0 );
  vec3 skylight = mix( uwHorizonColor, uwZenithColor, 0.75 ) * ( 0.8 + 0.2 * shadow );
  // Part of the sunlight arrives already scattered, so a shadow in water is never black.
  vec3 color = albedo * ( sun * ( caustic * shadow * 0.27 + 0.10 ) + skylight * 0.7 );
  color = uwApplyMedium( color, vWorld, 12 );
  gl_FragColor = vec4( color, 1.0 );
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
  gl_FragColor = uwDither( gl_FragColor, gl_FragCoord.xy );
}`;

export function createSeaFloor(sand, relief, opticsUniforms, shadowUniforms) {
  const material = new THREE.ShaderMaterial({
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.lights, { sand: { value: null }, relief: { value: null } }]),
    vertexShader: FLOOR_VERTEX,
    fragmentShader: FLOOR_FRAGMENT,
    lights: true,
  });
  // Merged uniforms are copies; the shared optics must stay live references.
  Object.assign(material.uniforms, opticsUniforms, shadowUniforms);
  material.uniforms.sand.value = sand;
  material.uniforms.relief.value = relief;
  // The sand stays level so it never shows an edge: distance alone dissolves it into the water.
  const floor = new THREE.PlaneGeometry(FLOOR_SIZE, FLOOR_SIZE, FLOOR_SEGMENTS, FLOOR_SEGMENTS);
  floor.rotateX(-Math.PI / 2);
  const positions = floor.attributes.position;
  for (let i = 0; i < positions.count; i++) {
    const x = positions.getX(i), z = positions.getZ(i);
    positions.setY(i, WATER_OPTICS.floorY + Math.sin(x * RIPPLE_X + z * RIPPLE_Z) * RIPPLE_HEIGHT);
  }
  floor.computeVertexNormals();
  const mesh = new THREE.Mesh(floor, material);
  mesh.name = 'Environment:seaFloor';
  mesh.receiveShadow = true;
  return mesh;
}
