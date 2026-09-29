import * as THREE from 'three';

// Two environment keyframes blended into the lighting numbers and water colors
// UnderwaterEnvironment writes onto the scene. This module does not touch it.
const lerp = (from, to, mix) => from + (to - from) * mix;
const lerpColor = (from, to, mix) => new THREE.Color(from).lerp(new THREE.Color(to), mix);
const lerpLinearColor = (from, to, mix) => new THREE.Color().setRGB(...from).lerp(new THREE.Color().setRGB(...to), mix);

function hueShiftedWaterColor(from, to, mix, hueOffset) {
  const color = new THREE.Color(from).lerp(new THREE.Color(to), mix);
  const hsl = color.getHSL({});
  return color.setHSL(hsl.h + hueOffset, hsl.s, hsl.l);
}

export function blendEnvironmentProfile(from, to, mix, blueHueOffset) {
  const water = {};
  for (const property of ['horizon', 'zenith', 'abyss', 'glow', 'floorAverage']) {
    water[property] = hueShiftedWaterColor(from.water[property], to.water[property], mix, blueHueOffset);
  }
  return {
    sun: {
      radiance: lerpLinearColor(from.sun.radiance, to.sun.radiance, mix),
      absorption: [
        lerp(from.sun.absorption[0], to.sun.absorption[0], mix),
        lerp(from.sun.absorption[1], to.sun.absorption[1], mix),
        lerp(from.sun.absorption[2], to.sun.absorption[2], mix),
      ],
      lightColor: lerpColor(from.sun.lightColor, to.sun.lightColor, mix),
      lightIntensity: lerp(from.sun.lightIntensity, to.sun.lightIntensity, mix),
    },
    hemisphere: {
      skyColor: lerpColor(from.hemisphere.skyColor, to.hemisphere.skyColor, mix),
      groundColor: lerpColor(from.hemisphere.groundColor, to.hemisphere.groundColor, mix),
      intensity: lerp(from.hemisphere.intensity, to.hemisphere.intensity, mix),
    },
    fill: {
      color: lerpColor(from.fill.color, to.fill.color, mix),
      intensity: lerp(from.fill.intensity, to.fill.intensity, mix),
    },
    surface: {
      horizon: lerpLinearColor(from.surface.horizon, to.surface.horizon, mix),
      zenith: lerpLinearColor(from.surface.zenith, to.surface.zenith, mix),
      glowColor: lerpLinearColor(from.surface.glowColor, to.surface.glowColor, mix),
      glowWide: lerp(from.surface.glowWide, to.surface.glowWide, mix),
      glowTight: lerp(from.surface.glowTight, to.surface.glowTight, mix),
      discColor: lerpLinearColor(from.surface.discColor, to.surface.discColor, mix),
      discIntensity: lerp(from.surface.discIntensity, to.surface.discIntensity, mix),
    },
    water,
    shaftIntensityMultiplier: lerp(from.shaftIntensityMultiplier, to.shaftIntensityMultiplier, mix),
    causticsIntensityMultiplier: lerp(from.causticsIntensityMultiplier, to.causticsIntensityMultiplier, mix),
  };
}
