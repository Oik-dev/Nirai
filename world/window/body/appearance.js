import { inspectAppearance, appearanceLabels } from './appearance-metadata.js';

function renderableNode(json, reference) {
  if (!reference || !Number.isInteger(reference.node) || reference.node < 0 || reference.node >= (json.nodes?.length ?? 0)) {
    throw new Error('外見のNode参照が不正です。');
  }
  const node = json.nodes[reference.node];
  if (!Number.isInteger(node?.mesh) || !json.meshes?.[node.mesh]) {
    throw new Error('外見のNodeに描画対象がありません。');
  }
  if (typeof reference.nodeName !== 'string' || reference.nodeName !== (node.name ?? '')) {
    throw new Error('外見のNode名が成果物と一致しません。');
  }
  return reference.node;
}

function assertIndependentItem(json, index) {
  const pending = [...(json.nodes[index].children ?? [])];
  const visited = new Set([index]);
  while (pending.length) {
    const childIndex = pending.pop();
    if (!Number.isInteger(childIndex) || !json.nodes[childIndex] || visited.has(childIndex)) {
      throw new Error('外見のNode階層が不正です。');
    }
    visited.add(childIndex);
    const child = json.nodes[childIndex];
    if (child.mesh !== undefined) throw new Error('外見の切替が子Nodeの描画対象に影響します。');
    if (child.children) pending.push(...child.children);
  }
}

function expressionTargets(json) {
  const targets = new Set();
  const expressions = json.extensions?.VRMC_vrm?.expressions ?? {};
  for (const expression of [...Object.values(expressions.preset ?? {}), ...Object.values(expressions.custom ?? {})]) {
    for (const bind of expression.morphTargetBinds ?? []) targets.add(`${bind.node}:${bind.index}`);
  }
  for (const group of json.extensions?.VRM?.blendShapeMaster?.blendShapeGroups ?? []) {
    for (const bind of group.binds ?? []) {
      json.nodes?.forEach((node, index) => {
        if (node.mesh === bind.mesh) targets.add(`${index}:${bind.index}`);
      });
    }
  }
  return targets;
}

export async function applyDefaultAppearance(gltf) {
  const json = gltf.parser.json;
  const metadata = json.extras?.nirai?.capabilities?.appearance;
  if (metadata === undefined) return;
  // Sea's catalog and the window must agree on which JSON is safe to expose.
  inspectAppearance(json);
  if (!metadata || metadata.schemaVersion !== 1 || !Array.isArray(metadata.controls) || !metadata.controls.length) {
    throw new Error('外見Metadataの形式に対応していません。');
  }

  const expressionMorphs = expressionTargets(json);
  const objects = new Map();
  const owners = new Set();
  let operationCount = 0;

  async function resolve(reference) {
    const index = renderableNode(json, reference);
    assertIndependentItem(json, index);
    if (!objects.has(index)) {
      const object = await gltf.parser.getDependency('node', index);
      let parent = object;
      while (parent && parent !== gltf.scene) parent = parent.parent;
      if (parent !== gltf.scene) throw new Error('外見の対象が表示中のAvatarに属していません。');
      objects.set(index, object);
    }
    return [index, objects.get(index)];
  }

  const prepared = [];
  const allowedLabels = new Set(appearanceLabels(json).map(item => item.name));
  for (const control of metadata.controls) {
    if (!control || typeof control.id !== 'string' || !control.id
      || typeof control.defaultOption !== 'string' || !Array.isArray(control.options) || control.options.length < 2) {
      throw new Error('外見Metadataの項目が不正です。');
    }
    const defaultOption = control.options.find(option => option?.id === control.defaultOption);
    if (!defaultOption) throw new Error('外見Metadataの既定値が不正です。');

    let owned;
    let defaultWrites = null;
    const options = new Map();
    for (const option of control.options) {
      if (!option || typeof option.id !== 'string' || !option.id
        || !Array.isArray(option.visibility) || !Array.isArray(option.morphs)) {
        throw new Error('外見の操作一覧が不正です。');
      }
      operationCount += option.visibility.length + option.morphs.length;
      if (operationCount > 4096) throw new Error('外見の操作数が多すぎます。');

      const targets = new Set();
      const writes = [];
      const claim = key => {
        if (targets.has(key)) throw new Error('同じ外見の操作が重複しています。');
        targets.add(key);
      };

      for (const reference of option.visibility) {
        if (typeof reference.value !== 'boolean') throw new Error('外見の表示指定が不正です。');
        const [index, object] = await resolve(reference);
        claim(`visible:${index}`);
        let renderable = false;
        object.traverse(child => { if (child.isMesh) renderable = true; });
        if (!renderable) throw new Error('外見の描画対象がありません。');
        writes.push(() => { object.visible = reference.value; });
      }

      for (const reference of option.morphs) {
        const [index, object] = await resolve(reference);
        const mesh = json.meshes[json.nodes[index].mesh];
        if (!Number.isInteger(reference.index) || reference.index < 0 || typeof reference.morphName !== 'string'
          || mesh.extras?.targetNames?.[reference.index] !== reference.morphName
          || !mesh.primitives?.every(primitive => primitive.targets?.[reference.index])
          || typeof reference.weight !== 'number' || !Number.isFinite(reference.weight)
          || reference.weight < -1 || reference.weight > 1) {
          throw new Error('外見の変形参照または値が不正です。');
        }
        if (expressionMorphs.has(`${index}:${reference.index}`)) {
          throw new Error('外見の変形が表情・瞬き・視線と競合しています。');
        }
        claim(`morph:${index}:${reference.index}`);
        const meshes = [];
        object.traverse(child => {
          if (!child.isMesh) return;
          if (!child.morphTargetInfluences || child.morphTargetInfluences.length <= reference.index) {
            throw new Error('外見の変形を読み込めません。');
          }
          meshes.push(child);
        });
        if (!meshes.length) throw new Error('外見の変形対象がありません。');
        writes.push(() => {
          for (const child of meshes) child.morphTargetInfluences[reference.index] = reference.weight;
        });
      }

      if (!targets.size || owned && (owned.size !== targets.size || [...targets].some(key => !owned.has(key)))) {
        throw new Error('外見の選択肢には同じ対象の完全な指定が必要です。');
      }
      owned = targets;
      if (option === defaultOption) defaultWrites = writes;
      options.set(option.id, writes);
    }

    for (const target of owned) {
      if (owners.has(target)) throw new Error('複数の外見項目が同じ対象を変更します。');
      owners.add(target);
    }
    prepared.push({ control, defaultWrites, options });
  }

  const uniqueObjects = new Set(objects.values());
  if (uniqueObjects.size !== objects.size) throw new Error('外見の描画対象が重複しています。');
  for (const object of uniqueObjects) {
    let parent = object.parent;
    while (parent && parent !== gltf.scene) {
      if (uniqueObjects.has(parent)) throw new Error('外見の操作が別の対象に影響します。');
      parent = parent.parent;
    }
  }

  // Every option has already been checked and resolved against this exact VRM.
  // Subsequent choices switch only verified targets without reloading the avatar.
  const apply = chosen => {
    const choices = chosen && typeof chosen === 'object' && !Array.isArray(chosen) ? chosen : {};
    for (const { control, defaultWrites, options } of prepared) {
      const label = choices[control.label];
      const selected = allowedLabels.has(control.label)
        ? control.options.find(option => option.label === label) : null;
      const writes = selected ? options.get(selected.id) : defaultWrites;
      for (const write of writes) write();
    }
  };
  apply({});
  return { apply };
}
