import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { NaturalGaze } from './gaze.js';
import { createAvatarAppearance } from './appearance.js';
import { prepareAvatarMaterials } from './avatar-materials.js';
import { completeMorphDeltas } from './morph-deltas.js';

export async function loadAvatar(bytes, opticsUniforms) {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (!url.startsWith('blob:') && !url.startsWith('data:')) throw new Error('VRMの外部参照は読み込めません。');
    return url;
  });
  const loader = new GLTFLoader(manager);
  loader.register(parser => ({ name: 'NiraiMorphDeltas', beforeRoot() { completeMorphDeltas(parser.json); } }));
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await loader.parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
  const vrm = gltf.userData.vrm;
  if (!vrm) { VRMUtils.deepDispose(gltf.scene); throw new Error('VRMを読み込めませんでした。'); }
  try {
    VRMUtils.rotateVRM0(vrm);
    // A relaxed neutral stance is a body default, not an inferred emotion.
    vrm.humanoid.setNormalizedPose({
      leftUpperArm: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, -1.15)).toArray() },
      rightUpperArm: { rotation: new THREE.Quaternion().setFromEuler(new THREE.Euler(0, 0, 1.15)).toArray() },
    });
    vrm.update(0);
    vrm.scene.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(vrm.scene);
    const height = box.max.y - box.min.y;
    if (!Number.isFinite(height) || height < .05 || height > 20) throw new Error('モデルの大きさを確認できません。');
    const root = new THREE.Group();
    const scale = 1.55 / height;
    root.scale.setScalar(scale);
    vrm.scene.position.y -= box.min.y;
    root.add(vrm.scene);
    vrm.scene.traverse(object => { object.frustumCulled = false; });
    // Lighting is part of placing the model in the sea, not a change to the model's expression.
    const materials = prepareAvatarMaterials(vrm, opticsUniforms);
    const gaze = new NaturalGaze(vrm, root);
    const appearance = await createAvatarAppearance(vrm, gltf);
    const restY = vrm.scene.position.y;
    let elapsed = 0;
    return {
      root, vrm, gaze, materials, catalog: appearance.catalog, warnings: appearance.warnings,
      get appearance() { return appearance.appearance; },
      get settled() { return appearance.settled; },
      applyAppearance(value, options) { appearance.apply(value, options); },
      update(delta, camera, focused) {
        elapsed += delta;
        appearance.update(delta);
        vrm.scene.position.y = restY + Math.sin(elapsed * 1.2) * .008;
        const blink = Math.max(0, 1 - Math.abs((elapsed % 4.8) - 4.6) / .10);
        appearance.blink(blink);
        gaze.update(delta, camera, focused);
        vrm.update(delta);
      },
      dispose() { root.removeFromParent(); VRMUtils.deepDispose(vrm.scene); },
    };
  } catch (error) { VRMUtils.deepDispose(vrm.scene); throw error; }
}
