import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

// 体のファイル（VRM・.vrma）は自己完結したGLBだけを読む。外部参照は取りに行かずに断る（海の入口の検査と同じ約束）。
export function selfContainedLoader() {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (!url.startsWith('blob:') && !url.startsWith('data:')) {
      throw new Error('体のファイルの外部参照は読み込めません。');
    }
    return url;
  });
  return new GLTFLoader(manager);
}

export function parseGlb(loader, bytes) {
  return loader.parseAsync(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), '');
}
