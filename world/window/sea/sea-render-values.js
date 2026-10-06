import * as THREE from 'three';
import { SEA_DEFAULTS } from './sea-settings.js';

// Volumetric shaft strength when the debug slider reads 100%.
// The slider default is 150%, which is what the sea actually draws (0.6).
export const SHAFT_STRENGTH_AT_FULL_SCALE = 0.4;

// Relative RGB extinction. Green contrast falls to 5% over the visibility distance.
// This is a rendering parameter, not a measured Secchi depth.
const EXTINCTION_RED = 1.7;
const EXTINCTION_GREEN = 1.0;
const EXTINCTION_BLUE = 0.62;
const REMAINING_CONTRAST = 0.05;

// Hue stays on the authored color at 50%. Each percent moves it by one turn / 500.
const BLUE_NEUTRAL = 50;
const BLUE_HUE_RANGE = 500;

export function extinctionForVisibility(visibility) {
  return new THREE.Vector3(EXTINCTION_RED, EXTINCTION_GREEN, EXTINCTION_BLUE)
    .multiplyScalar(-Math.log(REMAINING_CONTRAST) / visibility);
}

// Normalized slider values mapped onto the numbers the sea renderer applies:
// shader uniforms, particle and bubble draw counts, and the water-color hue offset.
export function seaRenderValues(settings = SEA_DEFAULTS) {
  return {
    waveScale: settings.waveSize / 100,
    waveDetail: settings.waveDetail / 100,
    surfaceClarity: settings.surfaceClarity / 100,
    shaftStrength: SHAFT_STRENGTH_AT_FULL_SCALE * settings.shaftStrength / 100,
    shaftRange: settings.shaftRange,
    shaftDepth: settings.shaftDepth,
    causticContrast: 1 - settings.causticTransparency / 100,
    extinction: extinctionForVisibility(settings.visibility),
    blueHueOffset: (settings.blue - BLUE_NEUTRAL) / BLUE_HUE_RANGE,
    particles: settings.particles,
    bubbles: settings.bubbles,
  };
}
