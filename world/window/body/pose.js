import * as THREE from 'three';

// 体の向きの約束：VRM 1.0 の正規化された骨（体の前が +Z、左手が +X）で、角度 [x, y, z] を度で書く。
// 親の軸で x → y → z の順に回す（T ポーズの腕では、最初の x が骨の長さ方向のひねりになる）。
// VRM 0.x の骨は前後が逆向きなので、x と z の回転を逆にして使う（three-vrm-animation が .vrma に行うのと同じ直し）。
export function modelRotation([x, y, z], metaVersion, target = new THREE.Quaternion()) {
  const rad = THREE.MathUtils.degToRad;
  target.setFromEuler(new THREE.Euler(rad(x), rad(y), rad(z), 'ZYX'));
  if (metaVersion === '0') { target.x = -target.x; target.z = -target.z; }
  return target;
}

const curl = (side, degrees) => {
  const sign = side === 'left' ? -1 : 1;
  return Object.fromEntries(['Index', 'Middle', 'Ring', 'Little'].flatMap(finger =>
    ['Proximal', 'Intermediate', 'Distal'].map(joint => [`${side}${finger}${joint}`, [0, 0, sign * degrees]])));
};

// 基準姿勢：腕を下ろし、肘と指を少しゆるめる。書いていない骨は T ポーズ（回転なし）。身振りの最初と最後もここ。
export const REST_POSE = Object.freeze({
  leftUpperArm: [0, 0, -66], rightUpperArm: [0, 0, 66],
  leftLowerArm: [0, -14, 0], rightLowerArm: [0, 14, 0],
  ...curl('left', 12), ...curl('right', 12),
});

// 動きの姿勢：正規化された骨と同じ名前の、代わりの骨。初めは基準姿勢で、AnimationMixer はこちらへ書く
// （動きは基準姿勢と混ぜて始まり、終われば基準姿勢へ戻る）。体は毎フレームここから本物の骨へ写し、その上に揺らぎと視線を掛ける。
// AnimationMixer は前のフレームと同じ値を書き直さないので、本物の骨へ直接書かせると、揺らぎを掛けた骨が戻らない。
export class MotionPose {
  constructor(vrm) {
    this.root = new THREE.Object3D();
    this.bones = Object.entries(vrm.humanoid.normalizedHumanBones)
      .filter(([, bone]) => bone?.node)
      .map(([name, bone]) => {
        const source = new THREE.Object3D();
        source.name = bone.node.name;
        source.position.copy(bone.node.position);
        if (REST_POSE[name]) modelRotation(REST_POSE[name], vrm.meta?.metaVersion, source.quaternion);
        this.root.add(source);
        return { node: bone.node, source };
      });
    this.apply();
  }

  // 本物の骨を、今の動きの姿勢にする（前のフレームの回転に積み増さない）。
  apply() {
    for (const { node, source } of this.bones) {
      node.position.copy(source.position);
      node.quaternion.copy(source.quaternion);
    }
  }
}
