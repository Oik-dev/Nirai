import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { NaturalGaze } from './gaze.js';
import { prepareAvatarMaterials } from './avatar-materials.js';
import { completeMorphDeltas } from './morph-deltas.js';
import { applyDefaultAppearance } from './appearance.js';

export function setRelaxedArmPose(humanoid) {
  for (const side of ['left', 'right']) {
    const upperArm = humanoid.getNormalizedBoneNode(`${side}UpperArm`);
    const lowerArm = humanoid.getNormalizedBoneNode(`${side}LowerArm`);
    if (!upperArm || !lowerArm) continue;
    const restDirection = lowerArm.position.clone().normalize();
    const relaxedDirection = new THREE.Vector3(
      Math.sign(restDirection.x) * Math.cos(1.15),
      -Math.sin(1.15),
      0,
    ).normalize();
    upperArm.quaternion.setFromUnitVectors(restDirection, relaxedDirection);
  }
}

function applyBlink(vrm, value) {
  const manager = vrm.expressionManager;
  const presets = manager?.presetExpressionMap ?? {};
  if (presets.blink?.binds.length) manager.setValue('blink', value);
  else if (presets.blinkLeft?.binds.length && presets.blinkRight?.binds.length) {
    manager.setValue('blinkLeft', value);
    manager.setValue('blinkRight', value);
  }
}

export async function loadAvatar(bytes, opticsUniforms) {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (!url.startsWith('blob:') && !url.startsWith('data:')) {
      throw new Error('VRMの外部参照は読み込めません。');
    }
    return url;
  });
  const loader = new GLTFLoader(manager);
  loader.register(parser => ({
    name: 'NiraiMorphDeltas',
    beforeRoot() { completeMorphDeltas(parser.json); },
  }));
  loader.register(parser => new VRMLoaderPlugin(parser));
  const arrayBuffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const gltf = await loader.parseAsync(arrayBuffer, '');
  const vrm = gltf.userData.vrm;
  if (!vrm) {
    VRMUtils.deepDispose(gltf.scene);
    throw new Error('VRMを読み込めませんでした。');
  }
  try {
    VRMUtils.rotateVRM0(vrm);
    setRelaxedArmPose(vrm.humanoid);
    await applyDefaultAppearance(gltf);
    vrm.update(0);
    vrm.scene.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(vrm.scene);
    const height = box.max.y - box.min.y;
    if (!Number.isFinite(height) || height < 0.05 || height > 20) {
      throw new Error('モデルの大きさを確認できません。');
    }
    const root = new THREE.Group();
    root.scale.setScalar(1.55 / height);
    vrm.scene.position.y -= box.min.y;
    root.add(vrm.scene);
    vrm.scene.traverse(object => { object.frustumCulled = false; });
    const materials = prepareAvatarMaterials(vrm, opticsUniforms);
    const gaze = new NaturalGaze(vrm, root);
    const restY = vrm.scene.position.y;
    let elapsed = 0;
    return {
      root,
      vrm,
      gaze,
      materials,
      update(delta, camera, focused) {
        elapsed += delta;
        vrm.scene.position.y = restY + Math.sin(elapsed * 1.2) * 0.008;
        const blink = Math.max(0, 1 - Math.abs((elapsed % 4.8) - 4.6) / 0.10);
        applyBlink(vrm, blink);
        gaze.update(delta, camera, focused);
        vrm.update(delta);
      },
      dispose() {
        root.removeFromParent();
        VRMUtils.deepDispose(vrm.scene);
      },
    };
  } catch (error) {
    VRMUtils.deepDispose(vrm.scene);
    throw error;
  }
}
