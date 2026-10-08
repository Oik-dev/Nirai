import * as THREE from 'three';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { Body } from './body.js';
import { parseGlb, selfContainedLoader } from './gltf.js';
import { prepareAvatarMaterials } from './avatar-materials.js';
import { completeMorphDeltas } from './morph-deltas.js';
import { applyDefaultAppearance } from './appearance.js';

export async function loadAvatar(bytes, opticsUniforms) {
  const loader = selfContainedLoader();
  loader.register(parser => ({
    name: 'NiraiMorphDeltas',
    beforeRoot() { completeMorphDeltas(parser.json); },
  }));
  loader.register(parser => new VRMLoaderPlugin(parser));
  const gltf = await parseGlb(loader, bytes);
  const vrm = gltf.userData.vrm;
  if (!vrm) {
    VRMUtils.deepDispose(gltf.scene);
    throw new Error('VRMを読み込めませんでした。');
  }
  try {
    VRMUtils.rotateVRM0(vrm);
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
    const body = new Body(vrm, root);
    await body.loadActivities();
    vrm.scene.traverse(object => { object.frustumCulled = false; });
    const materials = prepareAvatarMaterials(vrm, opticsUniforms);
    return {
      root,
      vrm,
      body,
      materials,
      update(delta, camera, focused) {
        body.update(delta, camera, focused);
      },
      dispose() {
        body.dispose();
        root.removeFromParent();
        VRMUtils.deepDispose(vrm.scene);
      },
    };
  } catch (error) {
    VRMUtils.deepDispose(vrm.scene);
    throw error;
  }
}
