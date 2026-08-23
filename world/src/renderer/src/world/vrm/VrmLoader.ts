import * as THREE from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { VRM, VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'

export interface LoadedVrm {
  vrm: VRM
  mixer: THREE.AnimationMixer
}

export class VrmLoader {
  async load(bytes: Uint8Array): Promise<LoadedVrm> {
    const loader = new GLTFLoader()
    loader.register((parser) => new VRMLoaderPlugin(parser))

    const arrayBuffer = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength
    ) as ArrayBuffer
    const gltf = await loader.parseAsync(arrayBuffer, '')
    const vrm = gltf.userData.vrm as VRM | undefined

    if (!vrm) {
      VRMUtils.deepDispose(gltf.scene)
      throw new Error('The selected file does not contain a VRM avatar')
    }

    VRMUtils.rotateVRM0(vrm)
    VRMUtils.removeUnnecessaryVertices(vrm.scene)
    vrm.scene.traverse((object) => {
      object.frustumCulled = false
    })

    return {
      vrm,
      mixer: new THREE.AnimationMixer(vrm.scene)
    }
  }

  update(loaded: LoadedVrm, delta: number): void {
    loaded.vrm.update(delta)
  }

  unload(loaded: LoadedVrm): void {
    loaded.mixer.stopAllAction()
    loaded.mixer.uncacheRoot(loaded.vrm.scene)
    VRMUtils.deepDispose(loaded.vrm.scene)
  }
}
