import { validateCatalog } from '../../shared/appearance.ts';

// One control owns each property. Every option writes the same complete set,
// so changing order, reloading or returning to a previous outfit cannot leave residue.
export async function readAppearanceControls(gltf, { renderableNode, assertIndependentItem, wardrobeNodes }) {
  const json = gltf.parser.json;
  const metadata = json.extras?.nirai?.capabilities?.appearance;
  if (metadata === undefined) return { catalog: [], apply() {}, matches() { return true; } };
  if (!metadata || metadata.schemaVersion !== 1 || !Array.isArray(metadata.controls) || !metadata.controls.length) {
    throw new Error('外見Metadataの形式に対応していません。');
  }
  const catalog = validateCatalog({ expressions: [], wardrobe: [], controls: metadata.controls.map(control => ({
    id: control.id, label: control.label, category: control.category, default_option: control.defaultOption,
    options: control.options?.map(option => ({ id: option.id, label: option.label })),
  })) }).controls;
  const expressionTargets = new Set();
  const expressions = json.extensions?.VRMC_vrm?.expressions ?? {};
  for (const expression of [...Object.values(expressions.preset ?? {}), ...Object.values(expressions.custom ?? {})]) {
    for (const bind of expression.morphTargetBinds ?? []) expressionTargets.add(`${bind.node}:${bind.index}`);
  }
  for (const group of json.extensions?.VRM?.blendShapeMaster?.blendShapeGroups ?? []) {
    for (const bind of group.binds ?? []) json.nodes.forEach((node, index) => {
      if (node.mesh === bind.mesh) expressionTargets.add(`${index}:${bind.index}`);
    });
  }
  const owners = new Set();
  const objects = new Map();
  let count = 0;
  async function resolve(ref) {
    const index = renderableNode(json, ref);
    assertIndependentItem(json, index);
    if (!objects.has(index)) {
      const object = await gltf.parser.getDependency('node', index);
      let parent = object;
      while (parent && parent !== gltf.scene) parent = parent.parent;
      if (parent !== gltf.scene) throw new Error('外見の対象が表示中のAvatarに属していません。');
      if ([...wardrobeNodes.values()].some(node => node === object)) throw new Error('既存の衣服と外見の操作対象が重複しています。');
      objects.set(index, object);
    }
    return [index, objects.get(index)];
  }
  const actions = new Map();
  for (const control of metadata.controls) {
    let owned;
    const options = new Map();
    for (const option of control.options) {
      if (!Array.isArray(option.visibility) || !Array.isArray(option.morphs)) throw new Error('外見の操作一覧が不正です。');
      count += option.visibility.length + option.morphs.length;
      if (count > 4096) throw new Error('外見の操作数が多すぎます。');
      const targets = new Set();
      const writes = [];
      const checks = [];
      const claim = key => {
        if (targets.has(key)) throw new Error('同じ外見の操作が重複しています。');
        targets.add(key);
      };
      for (const ref of option.visibility) {
        if (typeof ref.value !== 'boolean') throw new Error('外見の表示指定が不正です。');
        const [index, object] = await resolve(ref);
        claim(`visible:${index}`);
        let renderable = false;
        object.traverse(child => { if (child.isMesh) renderable = true; });
        if (!renderable) throw new Error('外見の描画対象がありません。');
        writes.push(() => { object.visible = ref.value; });
        checks.push(() => object.visible === ref.value);
      }
      for (const ref of option.morphs) {
        const [index, object] = await resolve(ref);
        const mesh = json.meshes[json.nodes[index].mesh];
        if (!Number.isInteger(ref.index) || ref.index < 0 || typeof ref.morphName !== 'string'
          || mesh.extras?.targetNames?.[ref.index] !== ref.morphName
          || !mesh.primitives?.every(primitive => primitive.targets?.[ref.index])
          || typeof ref.weight !== 'number' || !Number.isFinite(ref.weight) || ref.weight < -1 || ref.weight > 1) {
          throw new Error('外見の変形参照または値が不正です。');
        }
        if (expressionTargets.has(`${index}:${ref.index}`)) throw new Error('外見の変形が表情・瞬き・視線と競合しています。');
        claim(`morph:${index}:${ref.index}`);
        const meshes = [];
        object.traverse(child => {
          if (child.isMesh) {
            if (!child.morphTargetInfluences || child.morphTargetInfluences.length <= ref.index) throw new Error('外見の変形を読み込めません。');
            meshes.push(child);
          }
        });
        if (!meshes.length) throw new Error('外見の変形対象がありません。');
        writes.push(() => { for (const mesh of meshes) mesh.morphTargetInfluences[ref.index] = ref.weight; });
        checks.push(() => meshes.every(mesh => Math.abs(mesh.morphTargetInfluences[ref.index] - ref.weight) < 1e-6));
      }
      if (!targets.size || owned && (owned.size !== targets.size || [...targets].some(key => !owned.has(key)))) {
        throw new Error('外見の選択肢には同じ対象の完全な指定が必要です。');
      }
      owned = targets;
      options.set(option.id, { writes, checks });
    }
    for (const target of owned) {
      if (owners.has(target)) throw new Error('複数の外見項目が同じ対象を変更します。');
      owners.add(target);
    }
    actions.set(control.id, options);
  }
  // Reject runtime aliasing or parent relationships even if the document's graph is inconsistent.
  const uniqueObjects = new Set(objects.values());
  if (uniqueObjects.size !== objects.size) throw new Error('外見の描画対象が重複しています。');
  for (const object of uniqueObjects) {
    let parent = object.parent;
    while (parent && parent !== gltf.scene) {
      if (uniqueObjects.has(parent) || [...wardrobeNodes.values()].includes(parent)) throw new Error('外見の操作が別の対象に影響します。');
      parent = parent.parent;
    }
  }
  return { catalog, apply(choices) {
    for (const [id, options] of actions) for (const write of options.get(choices[id]).writes) write();
  }, matches(choices) {
    return [...actions].every(([id, options]) => options.get(choices[id]).checks.every(check => check()));
  } };
}
