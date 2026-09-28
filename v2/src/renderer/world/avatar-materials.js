import * as THREE from 'three';
import { MToonMaterial } from '@pixiv/three-vrm';
import { UNDERWATER_OPTICS_GLSL, UNDERWATER_SURFACE_GLSL } from './optics.js';

const PROGRAM_KEY = 'nirai-underwater-character-2';
const MTOON_OUTPUT = 'gl_FragColor = vec4( col, diffuseColor.a );';
// MToon inlines the light loop; the built-in materials take it from this chunk.
const LIGHT_LOOP = '#include <lights_fragment_begin>';
const DIRECTIONAL_LIGHT = 'getDirectionalLightInfo( directionalLight, directLight );';

// Lit replacement for materials that could not react to light: the author's texture,
// a soft terminator for diffuse underwater light and a shade cooled by the surrounding water.
export const CONVERTED_TOON = Object.freeze({
  shadeTint: Object.freeze([0.6, 0.69, 0.8]),
  shadingShift: -0.05,
  shadingToony: 0.6,
  giEqualization: 0.9,
});

export const CHARACTER_LIGHT = Object.freeze({ caustics: 0.4, bounce: 0.4, rim: 0.5 });

const CHARACTER_GLSL = /* glsl */ `
${UNDERWATER_OPTICS_GLSL}
${UNDERWATER_SURFACE_GLSL}
uniform float uwCausticStrength;
uniform float uwBounceStrength;
uniform float uwRimStrength;
varying vec3 uwWorldPosition;

// Sunlight reaching a character crossed the same waves and depth as the light on the sand.
// The moving caustic net is part of that sunlight, so it appears only on the lit side.
// At the home view the sun is behind the character: the front is in shadow and shows no net,
// while the back carries the same caustic pattern as the sand. A flat front is expected.
vec3 uwSunlight( vec3 normalView, vec3 lightView ) {
  if ( dot( lightView, normalize( ( viewMatrix * vec4( uwSunDir, 0.0 ) ).xyz ) ) < 0.999 ) return vec3( 1.0 );
  vec3 net = uwCausticLight( uwProjectToFloor( uwWorldPosition ), 1.0 );
  float sunlit = saturate( dot( normalView, lightView ) * 3.0 );
  return uwSunAttenuation( uwWorldPosition.y ) * mix( vec3( 1.0 ), net, sunlit * uwCausticStrength );
}

vec3 uwShadeCharacter( vec3 color, vec3 albedo, vec3 viewNormal ) {
  vec3 normalWorld = inverseTransformDirection( viewNormal, viewMatrix );
  vec3 toCamera = normalize( cameraPosition - uwWorldPosition );
  vec3 sunlight = uwSunColor * uwSunAttenuation( uwWorldPosition.y );
  // The sand throws its caustics back up: dappled light on legs and undersides, softer with height.
  float height = max( uwWorldPosition.y - uwFloorY, 0.0 );
  float bounce = uwCausticWeb( uwWorldPosition.xz, 1.2 + height * 1.2 );
  float below = saturate( 0.25 - normalWorld.y * 0.8 );
  color += albedo * sunlight * uwFloorAverage * ( bounce * bounce * below * exp( -height * 0.9 ) * uwBounceStrength );
  float rim = pow( 1.0 - saturate( dot( normalWorld, toCamera ) ), 3.0 );
  color += uwWaterColor( reflect( -toCamera, normalWorld ) ) * ( rim * uwRimStrength );
  return uwApplyMedium( color, uwWorldPosition, 6 );
}
`;

const luminance = color => color.r * 0.2126 + color.g * 0.7152 + color.b * 0.0722;

export function classifyAvatarMaterial(material) {
  if (material.isMToonMaterial) return 'mtoon';
  if (material.isMeshStandardMaterial) {
    const glow = material.emissive.clone().multiplyScalar(material.emissiveIntensity ?? 1);
    // A black base with full emission is an unlit look baked into glTF: light can never reach it.
    return luminance(material.color) < 0.02 && luminance(glow) > 0.5 ? 'emission-baked' : 'lit';
  }
  if (material.isMeshBasicMaterial) return 'unlit';
  if (material.isMeshLambertMaterial || material.isMeshPhongMaterial || material.isMeshToonMaterial) return 'lit';
  return 'unsupported';
}

function albedoOf(material, kind) {
  if (kind !== 'emission-baked') return { map: material.map ?? null, color: material.color.clone() };
  const color = material.emissive.clone().multiplyScalar(material.emissiveIntensity ?? 1);
  return { map: material.emissiveMap ?? null, color: new THREE.Color(Math.min(color.r, 1), Math.min(color.g, 1), Math.min(color.b, 1)) };
}

// Alpha is read from the base texture. Moving emission there is only safe when both are the same image.
function alphaSurvives(material, map) {
  if (!material.map || !(material.transparent || material.alphaTest > 0)) return true;
  return material.map === map || Boolean(map && material.map.source === map.source);
}

function toToon(material, kind) {
  const { map, color } = albedoOf(material, kind);
  if (!alphaSurvives(material, map)) return null;
  const toon = new MToonMaterial({
    map, color,
    shadeMultiplyTexture: map,
    shadeColorFactor: new THREE.Color(...CONVERTED_TOON.shadeTint),
    shadingShiftFactor: CONVERTED_TOON.shadingShift,
    shadingToonyFactor: CONVERTED_TOON.shadingToony,
    giEqualizationFactor: CONVERTED_TOON.giEqualization,
    transparent: material.transparent, opacity: material.opacity, alphaTest: material.alphaTest,
    side: material.side, depthWrite: material.depthWrite, depthTest: material.depthTest,
  });
  toon.name = material.name;
  toon.userData = { ...material.userData, niraiLitFrom: kind };
  return toon;
}

// Expression binds hold the original object, so a bound material is relit where it stands.
function relightInPlace(material) {
  const { map, color } = albedoOf(material, 'emission-baked');
  if (!alphaSurvives(material, map)) return false;
  material.map = map ?? material.map;
  material.color.copy(color);
  material.emissive.setRGB(0, 0, 0);
  material.emissiveMap = null;
  material.metalness = 0;
  material.roughness = Math.max(material.roughness, 0.8);
  material.userData.niraiLitFrom = 'emission-baked';
  material.needsUpdate = true;
  return true;
}

function shaderSources(material) {
  if (material.isMToonMaterial) return { vertex: material.vertexShader, fragment: material.fragmentShader, mtoon: true };
  const id = material.isMeshStandardMaterial ? 'physical' : material.isMeshLambertMaterial ? 'lambert'
    : material.isMeshPhongMaterial ? 'phong' : material.isMeshToonMaterial ? 'toon' : null;
  const shader = id && THREE.ShaderLib[id];
  return shader ? { vertex: shader.vertexShader, fragment: shader.fragmentShader, mtoon: false } : null;
}

function patchShader(vertex, fragment, mtoon) {
  if (!vertex.includes('#include <common>') || !vertex.includes('#include <project_vertex>') || !fragment.includes('#include <common>')) return null;
  const patchedVertex = vertex
    .replace('#include <common>', '#include <common>\nvarying vec3 uwWorldPosition;')
    .replace('#include <project_vertex>', '#include <project_vertex>\n\tuwWorldPosition = ( modelMatrix * vec4( transformed, 1.0 ) ).xyz;');
  let patchedFragment = fragment.replace('#include <common>', `#include <common>\n${CHARACTER_GLSL}`);
  if (!mtoon) patchedFragment = patchedFragment.replace(LIGHT_LOOP, THREE.ShaderChunk.lights_fragment_begin);
  if (!patchedFragment.includes(DIRECTIONAL_LIGHT)) return null;
  patchedFragment = patchedFragment.replace(DIRECTIONAL_LIGHT,
    `${DIRECTIONAL_LIGHT}\n\t\tdirectLight.color *= uwSunlight( geometryNormal, directLight.direction );`);
  if (mtoon) {
    const index = patchedFragment.lastIndexOf(MTOON_OUTPUT);
    if (index < 0) return null;
    patchedFragment = patchedFragment.slice(0, index)
      + '#ifdef OUTLINE\n  col = uwApplyMedium( col, uwWorldPosition, 4 );\n#else\n  col = uwShadeCharacter( col, diffuseColor.rgb, normal );\n#endif\n  '
      + patchedFragment.slice(index);
  } else {
    if (!patchedFragment.includes('#include <opaque_fragment>')) return null;
    patchedFragment = patchedFragment.replace('#include <opaque_fragment>',
      'outgoingLight = uwShadeCharacter( outgoingLight, diffuseColor.rgb, normal );\n#include <opaque_fragment>');
  }
  return { vertex: patchedVertex, fragment: patchedFragment };
}

// Caustics, a rim of water light and the same absorption and scattering as the sea around it.
export function injectUnderwaterShading(material, uniforms) {
  if (material.userData.niraiUnderwater) return true;
  const sources = shaderSources(material);
  if (!sources || !patchShader(sources.vertex, sources.fragment, sources.mtoon)) return false;
  const compile = material.onBeforeCompile;
  const cacheKey = material.customProgramCacheKey;
  material.onBeforeCompile = function (shader, renderer) {
    compile.call(this, shader, renderer);
    const patched = patchShader(shader.vertexShader, shader.fragmentShader, sources.mtoon);
    if (!patched) return;
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = patched.vertex;
    shader.fragmentShader = patched.fragment;
  };
  material.customProgramCacheKey = function () { return `${cacheKey.call(this)}|${PROGRAM_KEY}`; };
  material.userData.niraiUnderwater = true;
  material.needsUpdate = true;
  return true;
}

export function prepareAvatarMaterials(vrm, opticsUniforms) {
  const uniforms = {
    ...opticsUniforms,
    uwCausticStrength: { value: CHARACTER_LIGHT.caustics },
    uwBounceStrength: { value: CHARACTER_LIGHT.bounce },
    uwRimStrength: { value: CHARACTER_LIGHT.rim },
  };
  const bound = new Set();
  for (const expression of vrm.expressionManager?.expressions ?? []) {
    for (const bind of expression.binds ?? []) if (bind.material) bound.add(bind.material);
  }
  const adapted = new Map();
  const summary = { relit: 0, underwater: 0, materials: 0 };
  const adapt = material => {
    if (!material) return material;
    if (adapted.has(material)) return adapted.get(material);
    const kind = classifyAvatarMaterial(material);
    let result = material;
    if ((kind === 'emission-baked' || kind === 'unlit') && !bound.has(material)) {
      const toon = toToon(material, kind);
      if (toon) {
        result = toon;
        // VRM.update keeps MToon uniforms such as opacity and alpha cutoff current.
        (vrm.materials ??= []).push(toon);
        summary.relit++;
      }
    } else if (kind === 'emission-baked' && relightInPlace(material)) summary.relit++;
    if (injectUnderwaterShading(result, uniforms)) summary.underwater++;
    summary.materials++;
    adapted.set(material, result);
    return result;
  };
  vrm.scene.traverse(object => {
    if (!object.isMesh) return;
    object.castShadow = true;
    object.receiveShadow = false;
    object.material = Array.isArray(object.material) ? object.material.map(adapt) : adapt(object.material);
  });
  for (const [source, result] of adapted) if (source !== result) source.dispose();
  return summary;
}
