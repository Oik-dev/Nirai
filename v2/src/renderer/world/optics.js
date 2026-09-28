import * as THREE from 'three';
import { WAVE_PERIOD, WATER_INDEX } from './waves.js';

// One optical model is shared by the sea floor, surface, backdrop and characters.
// Every shader shades in linear light and lets the renderer tone map the result.
export const WATER_OPTICS = Object.freeze({
  surfaceY: 4.05,
  floorY: -0.05,
  refractiveIndex: WATER_INDEX,
  shaftRange: 22,
  shaftStrength: 0.4,
  shaftDepth: 8,
  surfaceClarity: 0,
  // Artistic visibility calibration: green contrast falls to 5% over 55 m.
  // This is a rendering parameter, not a measured Secchi depth.
  visibility: 55,
});

const SUN_IN_WATER = new THREE.Vector3(-0.16, 0.88, -0.44).normalize();

// Snell's law: horizontal components grow by the refractive index when leaving the water.
export function sunDirectionInAir(direction, index = WATER_OPTICS.refractiveIndex) {
  const horizontal = new THREE.Vector2(direction.x, direction.z);
  const sine = Math.min(horizontal.length() * index, 1);
  if (horizontal.lengthSq() > 0) horizontal.normalize();
  return new THREE.Vector3(horizontal.x * sine, Math.sqrt(1 - sine * sine), horizontal.y * sine);
}

const srgb = hex => new THREE.Color(hex);

export function createUnderwaterOptics() {
  const uniforms = {
    uwTime: { value: 0 },
    uwWaveTime: { value: 0 },
    uwCausticContrast: { value: 0.32 },
    uwSunDir: { value: SUN_IN_WATER.clone() },
    uwSunAirDir: { value: sunDirectionInAir(SUN_IN_WATER) },
    uwSurfaceY: { value: WATER_OPTICS.surfaceY },
    uwFloorY: { value: WATER_OPTICS.floorY },
    uwWaves: { value: null },
    uwCaustics: { value: null },
    uwCausticTexels: { value: 1 },
    uwSunColor: { value: new THREE.Color(1.0, 0.95, 0.84).multiplyScalar(3.1) },
    uwHorizonColor: { value: srgb(0x078cba) },
    uwZenithColor: { value: srgb(0x45cfdf) },
    uwAbyssColor: { value: srgb(0x075779) },
    uwGlowColor: { value: srgb(0x9beaf2) },
    uwFloorAverage: { value: srgb(0xbde7e8) },
    uwExtinction: { value: new THREE.Vector3(1.7, 1.0, 0.62).multiplyScalar(-Math.log(0.05) / WATER_OPTICS.visibility) },
    uwSunAbsorption: { value: new THREE.Vector3(0.080, 0.028, 0.022) },
    // Volumetric shafts are intentionally independent of floor-caustic opacity.
    uwShaftStrength: { value: WATER_OPTICS.shaftStrength },
    uwShaftRange: { value: WATER_OPTICS.shaftRange },
    uwShaftDepth: { value: WATER_OPTICS.shaftDepth },
    uwSurfaceClarity: { value: WATER_OPTICS.surfaceClarity },
  };
  return {
    uniforms,
    sunDirection: uniforms.uwSunDir.value,
    update(time) { uniforms.uwTime.value = time; },
  };
}

export const UNDERWATER_OPTICS_GLSL = /* glsl */ `
uniform float uwTime;
uniform float uwCausticContrast;
uniform vec3 uwSunDir;
uniform vec3 uwSunAirDir;
uniform float uwSurfaceY;
uniform float uwFloorY;
uniform sampler2D uwCaustics;
uniform float uwCausticTexels;
uniform vec3 uwSunColor;
uniform vec3 uwHorizonColor;
uniform vec3 uwZenithColor;
uniform vec3 uwAbyssColor;
uniform vec3 uwGlowColor;
uniform vec3 uwFloorAverage;
uniform vec3 uwExtinction;
uniform vec3 uwSunAbsorption;
uniform float uwShaftStrength;
uniform float uwShaftRange;
uniform float uwShaftDepth;
uniform float uwSurfaceClarity;

// Screen-space dither; deterministic so a paused frame is reproduced exactly.
float uwNoise( vec2 fragCoord ) {
  return fract( 52.9829189 * fract( dot( fragCoord, vec2( 0.06711056, 0.00583715 ) ) ) );
}

// Radiance of an unbounded water column seen along dir.
vec3 uwWaterColor( vec3 dir ) {
  float up = clamp( dir.y, -1.0, 1.0 );
  vec3 color = up >= 0.0
    ? mix( uwHorizonColor, uwZenithColor, pow( up, 0.72 ) )
    : mix( uwHorizonColor, uwAbyssColor, pow( -up, 0.6 ) );
  float toward = max( dot( dir, uwSunDir ), 0.0 );
  return color + uwGlowColor * ( pow( toward, 5.0 ) * 0.42 + pow( toward, 48.0 ) * 0.85 );
}

// Nearby water stays clear enough for a character to read; distance still dissolves into blue.
vec3 uwTransmittance( float dist ) {
  return exp( -uwExtinction * max( dist, 0.0 ) );
}

vec3 uwSunAttenuation( float y ) {
  float depth = max( uwSurfaceY - y, 0.0 );
  return exp( -uwSunAbsorption * depth / max( uwSunDir.y, 0.2 ) );
}

// Follow the refracted sunlight down to the floor plane so shafts, floor caustics
// and caustics on a character come from the same light paths.
vec2 uwProjectToFloor( vec3 p ) {
  return p.xz - uwSunDir.xz * ( ( p.y - uwFloorY ) / max( uwSunDir.y, 0.2 ) );
}

// Anisotropic filtering along a footprint integrates the texture over it: the major axis spans
// the ray segment a sample stands for, the minor axis keeps the shaft edges soft but defined.
float uwFilteredShaft( vec2 uv, vec2 segment ) {
  float minor = 1.0 / uwCausticTexels;
  vec2 major = dot( segment, segment ) > minor * minor ? segment : vec2( minor, 0.0 );
  vec2 across = normalize( vec2( -major.y, major.x ) ) * minor;
  return textureGrad( uwCaustics, uv, major, across ).g;
}

// Shafts are the focused light paths seen from the side: the pattern is followed back to the
// floor along the sunlight. travel is how far the floor projection moves over the sample.
// Sample the focused-light channel of the same map as the sand, integrating each
// ray segment with filtering instead of placing a separate layer of painted beams.
float uwShaftPattern( vec3 p, vec2 travel ) {
  vec2 q = uwProjectToFloor( p );
  return uwFilteredShaft( q / ${WAVE_PERIOD.toFixed(1)} + 0.5, travel / ${WAVE_PERIOD.toFixed(1)} );
}

// Single scattering along the view ray: ambient water glow plus sun shafts. Samples are
// deterministic and each one integrates its own stretch of the ray, so there is no grain.
vec3 uwInscatter( vec3 origin, vec3 dir, float dist, int samples ) {
  vec3 color = uwWaterColor( dir ) * ( 1.0 - uwTransmittance( dist ) );
  float range = min( dist, uwShaftRange );
  if ( range <= 0.0 ) return color;
  // Rays parallel to the sunlight stay on one shaft; rays across them sweep the pattern.
  vec2 sweep = dir.xz - uwSunDir.xz * ( dir.y / max( uwSunDir.y, 0.2 ) );
  vec3 shaft = vec3( 0.0 );
  for ( int i = 0; i < 16; i ++ ) {
    if ( i >= samples ) break;
    float u = ( float( i ) + 0.5 ) / float( samples );
    float t = range * u * u;
    float stride = 2.0 * u * range / float( samples );
    vec3 p = origin + dir * t;
    float depthBelowSurface = max( uwSurfaceY - p.y, 0.0 );
    float fadeStart = max( uwShaftDepth - 1.5, 0.0 );
    float depthFade = 1.0 - smoothstep( fadeStart, max( uwShaftDepth, 0.001 ), depthBelowSurface );
    float inWater = step( p.y, uwSurfaceY ) * step( uwFloorY - 0.2, p.y ) * depthFade;
    shaft += uwSunAttenuation( p.y ) * uwTransmittance( t ) * ( uwShaftPattern( p, sweep * stride ) * exp( -0.11 * t ) * stride * inWater );
  }
  float toward = max( dot( dir, uwSunDir ), 0.0 );
  float phase = 0.3 + 0.7 * pow( toward, 4.0 ) + 1.0 * pow( toward, 24.0 );
  return color + uwSunColor * vec3( 0.45, 0.8, 1.0 ) * shaft * ( uwShaftStrength * phase );
}

vec3 uwApplyMedium( vec3 color, vec3 worldPosition, int samples ) {
  vec3 toFragment = worldPosition - cameraPosition;
  float dist = length( toFragment );
  vec3 dir = toFragment / max( dist, 1e-4 );
  return color * uwTransmittance( dist ) + uwInscatter( cameraPosition, dir, dist, samples );
}

// 8-bit output bands in slow gradients of blue; one step of dither hides it.
vec4 uwDither( vec4 color, vec2 fragCoord ) {
  color.rgb += ( uwNoise( fragCoord + 17.0 ) - 0.5 ) / 255.0;
  return color;
}
`;

// Fragment-only: the density texture already contains evolving refracted light.
// Mip filtering preserves its average at a distance; no extra warp or sliding layer.
export const UNDERWATER_SURFACE_GLSL = /* glsl */ `
vec3 uwCausticLight( vec2 xz, float bias ) {
  vec2 uv = xz / ${WAVE_PERIOD.toFixed(1)} + 0.5;
  // The sun has a finite angular size: soften sub-texel folds instead of showing
  // rasterized ray triangles when the viewer approaches the sand.
  vec2 texel = vec2(1.0 / uwCausticTexels, 0.0);
  float density = texture( uwCaustics, uv, bias ).r * 0.5;
  density += (texture( uwCaustics, uv + texel, bias ).r + texture( uwCaustics, uv - texel, bias ).r
    + texture( uwCaustics, uv + texel.yx, bias ).r + texture( uwCaustics, uv - texel.yx, bias ).r) * 0.125;
  return vec3( mix( 1.0, density, uwCausticContrast ) );
}
float uwCausticWeb( vec2 xz, float bias ) {
  return smoothstep( 1.0, 3.5, uwCausticLight( xz, bias ).r );
}
`;
