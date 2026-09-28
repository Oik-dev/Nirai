import * as THREE from 'three';
import { UNDERWATER_OPTICS_GLSL, UNDERWATER_SURFACE_GLSL } from './optics.js';
import { WATER_FIELD_GLSL, WATER_INDEX } from './waves.js';

const SURFACE_PARAMETER = 2;
const SURFACE_SEGMENTS = 192;
const SURFACE_REACH = 200;

// The Fresnel water/air boundary was originally adapted from WaterThreeJS.
// MIT, copyright (c) 2026 mohamedachrefelouafi; see THIRD_PARTY_NOTICES.md.
export function createWaterSurface(uniforms) {
  const material = new THREE.ShaderMaterial({
    uniforms, side: THREE.DoubleSide,
    vertexShader: /* glsl */ `
      uniform float uwTime, uwSurfaceY;
      ${WATER_FIELD_GLSL}
      varying vec3 vWorld;
      void main() {
        vec3 p = (modelMatrix * vec4(position, 1.0)).xyz;
        p.y = uwSurfaceY + textureLod(uwWaves, waterUV(p.xz), 0.0).x;
        vWorld = p;
        gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);
      }
    `,
    fragmentShader: /* glsl */ `
      ${UNDERWATER_OPTICS_GLSL}
      ${UNDERWATER_SURFACE_GLSL}
      ${WATER_FIELD_GLSL}
      varying vec3 vWorld;
      float fresnelWaterToAir(float cosI) {
        float eta = ${WATER_INDEX};
        float sinT2 = eta * eta * (1.0 - cosI * cosI);
        if (sinT2 >= 1.0) return 1.0;
        float cosT = sqrt(1.0 - sinT2);
        float rs = (eta * cosI - cosT) / (eta * cosI + cosT);
        float rp = (cosI - eta * cosT) / (cosI + eta * cosT);
        return 0.5 * (rs * rs + rp * rp);
      }
      vec3 skyRadiance(vec3 dir) {
        float up = clamp(dir.y, 0.0, 1.0);
        float sun = max(dot(dir, uwSunAirDir), 0.0);
        vec3 sky = mix(vec3(0.50, 0.76, 1.06), vec3(0.18, 0.43, 0.85), sqrt(up));
        sky += vec3(0.95, 0.98, 1.0) * (pow(sun, 12.0) * 0.28 + pow(sun, 100.0) * 1.1);
        sky += vec3(1.0, 0.98, 0.93) * smoothstep(0.99965, 0.9999, sun) * 18.0;
        return sky;
      }
      void main() {
        vec3 I = normalize(vWorld - cameraPosition);
        // Mip/anisotropic filtering removes unresolved ripples at grazing angles.
        vec3 down = -waterNormal(texture(uwWaves, waterUV(vWorld.xz)).xyz);
        float fresnel = fresnelWaterToAir(clamp(dot(-I, down), 0.0, 1.0));
        vec3 reflected = reflect(I, down);
        float floorDistance = (vWorld.y - uwFloorY) / max(-reflected.y, 0.025);
        vec2 floorPoint = vWorld.xz + reflected.xz * floorDistance;
        vec3 floorLight = uwFloorAverage * (0.85 + min(uwCausticLight(floorPoint, 1.0), vec3(3.0)) * 0.06);
        vec3 color = mix(uwWaterColor(reflected), floorLight, uwTransmittance(floorDistance));
        if (fresnel < 1.0) {
          vec3 skyRay = refract(I, down, ${WATER_INDEX});
          color = mix(skyRadiance(normalize(skyRay)), color, fresnel);
        }
        vec3 mediumColor = uwApplyMedium(color, vWorld, 12);
        // Debug clarity removes only the water-column haze between camera and surface.
        // It does not alter Fresnel/refraction itself, so the surface keeps its wave structure.
        gl_FragColor = vec4(mix(mediumColor, color, uwSurfaceClarity), 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
        gl_FragColor = uwDither(gl_FragColor, gl_FragCoord.xy);
      }
    `,
  });
  // Concentrate vertices near the navigable stage; the distant surface needs few.
  const geometry = new THREE.PlaneGeometry(SURFACE_PARAMETER, SURFACE_PARAMETER, SURFACE_SEGMENTS, SURFACE_SEGMENTS);
  geometry.rotateX(-Math.PI / 2);
  const points = geometry.attributes.position;
  for (let i = 0; i < points.count; i++) {
    const x = points.getX(i), z = points.getZ(i);
    points.setXYZ(i, Math.sign(x) * x * x * SURFACE_REACH, 0, Math.sign(z) * z * z * SURFACE_REACH);
  }
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = 'Environment:waterSurface';
  mesh.frustumCulled = false;
  return { mesh, material };
}
