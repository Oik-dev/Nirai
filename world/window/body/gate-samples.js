// Chrome側の関門採取。D0の目視用とD1の工房で同じ骨・足裏の測り方を使う。
// 呼び出し前にBodyの初期姿勢を適用しておく（restはその姿勢の座標）。
export function createGateSampler(THREE, vrm, floorY) {
  const names = Object.keys(vrm.humanoid.humanBones).filter(name => vrm.humanoid.getRawBoneNode(name));
  const at = name => vrm.humanoid.getRawBoneNode(name).getWorldPosition(new THREE.Vector3());
  const ground = p => [p[0], p[1] - floorY, p[2]].map(v => +v.toFixed(5));
  const rest = Object.fromEntries(names.map(name => [name, ground(at(name).toArray())]));
  // Tポーズで足・つま先の骨が最も強く動かす頂点を、前後8×左右3升の底面から選ぶ。
  const boneName = new Map(names.map(name => [vrm.humanoid.getRawBoneNode(name), name]));
  const candidates = Object.fromEntries(['leftFoot', 'leftToes', 'rightFoot', 'rightToes']
    .filter(name => names.includes(name)).map(name => [name, []]));
  const vertex = new THREE.Vector3();
  // The physical body proxy needs silhouette points from every humanoid bone.
  // Keep gate.skin restricted to soles so the existing gate decision is unchanged.
  const bodyCandidates = Object.fromEntries(names.map(name => [name, []]));
  const skinned = [];
  vrm.scene.traverse(object => { if (object.isSkinnedMesh) skinned.push(object); });
  for (const mesh of skinned) {
    const { skinIndex, skinWeight, position } = mesh.geometry.attributes;
    for (let k = 0; k < position.count; k++) {
      const weights = [skinWeight.getX(k), skinWeight.getY(k), skinWeight.getZ(k), skinWeight.getW(k)];
      const indices = [skinIndex.getX(k), skinIndex.getY(k), skinIndex.getZ(k), skinIndex.getW(k)];
      const strongest = weights.indexOf(Math.max(...weights));
      const assigned = boneName.get(mesh.skeleton.bones[indices[strongest]]);
      if (!assigned) continue;
      const p = mesh.getVertexPosition(k, vertex).applyMatrix4(mesh.matrixWorld).toArray();
      if (weights[strongest] >= 0.45) bodyCandidates[assigned].push(p);
      if (candidates[assigned]) candidates[assigned].push({ mesh, k, p });
    }
  }
  const soles = {};
  for (const [name, list] of Object.entries(candidates)) {
    if (!list.length) continue;
    const range = axis => list.reduce(([lo, hi], c) => [Math.min(lo, c.p[axis]), Math.max(hi, c.p[axis])], [Infinity, -Infinity]);
    const [[x0, x1], [z0, z1]] = [range(0), range(2)];
    const cells = new Map();
    for (const c of list) {
      const key = Math.min(7, Math.floor((c.p[2] - z0) / (z1 - z0 + 1e-9) * 8)) * 3
        + Math.min(2, Math.floor((c.p[0] - x0) / (x1 - x0 + 1e-9) * 3));
      if (!cells.has(key) || c.p[1] < cells.get(key).p[1]) cells.set(key, c);
    }
    soles[name] = [...cells.values()];
  }
  const bodySkin = Object.fromEntries(Object.entries(bodyCandidates).map(([name, points]) => {
    // Evenly sample the central and outer regions rather than the arbitrary mesh index order.
    const sorted = points.sort((a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]);
    const stride = Math.max(1, Math.ceil(sorted.length / 64));
    return [name, { rest: sorted.filter((_, i) => i % stride === 0).slice(0, 64).map(ground) }];
  }).filter(([, sample]) => sample.rest.length > 0));
  const skinRest = Object.fromEntries(Object.entries(soles).map(([name, picks]) => [name, picks.map(c => ground(c.p))]));
  function sample(fps, frames, pose) {
    const bones = Object.fromEntries(names.map(name => [name, { position: [], rotation: [] }]));
    const skin = Object.fromEntries(Object.keys(soles).map(name => [name, { rest: skinRest[name], position: [] }]));
    for (let frame = 0; frame < frames; frame++) {
      pose(frame);
      for (const name of names) {
        bones[name].position.push(ground(at(name).toArray()));
        bones[name].rotation.push(vrm.humanoid.getNormalizedBoneNode(name).quaternion.toArray().map(v => +v.toFixed(6)));
      }
      for (const [name, picks] of Object.entries(soles)) skin[name].position.push(
        picks.map(c => ground(c.mesh.getVertexPosition(c.k, vertex).applyMatrix4(c.mesh.matrixWorld).toArray())));
    }
    return { fps, rest, bones, skin, bodySkin };
  }
  return { rest, soles, sample };
}
