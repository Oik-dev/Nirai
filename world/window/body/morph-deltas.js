// glTF morph attributes are DELTAS. Missing attributes mean zero displacement.
// three r167 GLTFLoader substitutes the base attribute when another target has
// that semantic. With negative baked-weight corrections this can cancel normals
// to zero (black skin), or even add base positions. Resolve this at the loader
// boundary, without changing the asset or the author's material/texture data.
export function completeMorphDeltas(json) {
  const accessors = json.accessors ?? [];
  let added = 0;
  for (const mesh of json.meshes ?? []) {
    for (const primitive of mesh.primitives ?? []) {
      const targets = primitive.targets ?? [];
      for (const semantic of ['POSITION', 'NORMAL', 'TANGENT', 'COLOR_0']) {
        if (!targets.some(target => target[semantic] !== undefined) || targets.every(target => target[semantic] !== undefined)) continue;
        const original = accessors[targets.find(target => target[semantic] !== undefined)[semantic]];
        if (!original || !Number.isInteger(original.count) || original.count <= 0) throw new Error('VRMの変形データが不正です。');
        // An accessor with no bufferView is zero-initialized by glTF definition.
        const zero = accessors.length;
        accessors.push({ componentType: 5126, count: original.count, type: original.type });
        for (const target of targets) if (target[semantic] === undefined) target[semantic] = zero;
        added++;
      }
    }
  }
  if (added) json.accessors = accessors;
  return added;
}
