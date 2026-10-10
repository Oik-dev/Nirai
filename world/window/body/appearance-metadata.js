// VRM外見の検査はGLB内のJSONだけで行う。海のcatalogも窓の描画もこの定義を使う。
const validName = value => typeof value === 'string' && value.length > 0 && [...value].length <= 40
  && value.trim() === value && !/[\r\n]/u.test(value)
  && value !== 'そのまま' && value !== 'なし';
// 「なし」は身振りでは操作しない意味だが、外見の選択肢では
// チョーカーなどを外すための正当な表示名。
const validOptionLabel = value => validName(value) || value === 'なし';

function assertRenderable(json, reference) {
  if (!reference || !Number.isInteger(reference.node) || reference.node < 0 ||
    !json.nodes?.[reference.node]) throw new Error('外見のNode参照が不正です。');
  const node = json.nodes[reference.node];
  if (!Number.isInteger(node.mesh) || !json.meshes?.[node.mesh] ||
    typeof reference.nodeName !== 'string' || reference.nodeName !== (node.name ?? '')) {
    throw new Error('外見のNode名・描画対象が不正です。');
  }
  const pending = [...(node.children ?? [])];
  const visited = new Set([reference.node]);
  while (pending.length) {
    const index = pending.pop();
    if (!Number.isInteger(index) || !json.nodes?.[index] || visited.has(index)) {
      throw new Error('外見のNode階層が不正です。');
    }
    visited.add(index);
    if (json.nodes[index].mesh !== undefined) {
      throw new Error('外見の切替が子Nodeの描画対象に影響します。');
    }
    pending.push(...(json.nodes[index].children ?? []));
  }
  return node;
}

function expressionTargets(json) {
  const used = new Set();
  const expressions = json.extensions?.VRMC_vrm?.expressions ?? {};
  for (const group of [...Object.values(expressions.preset ?? {}), ...Object.values(expressions.custom ?? {})]) {
    for (const bind of group?.morphTargetBinds ?? []) used.add(`${bind.node}:${bind.index}`);
  }
  for (const group of json.extensions?.VRM?.blendShapeMaster?.blendShapeGroups ?? []) {
    for (const bind of group.binds ?? []) json.nodes?.forEach((node, index) => {
      if (node.mesh === bind.mesh) used.add(`${index}:${bind.index}`);
    });
  }
  return used;
}

// Throws when the metadata can affect an unverified node, expression or another control.
// Labels missing from old VRMs remain usable for default rendering but are not exposed to the mind.
export function inspectAppearance(json) {
  const metadata = json.extras?.nirai?.capabilities?.appearance;
  if (metadata === undefined) return [];
  if (!metadata || metadata.schemaVersion !== 1 || !Array.isArray(metadata.controls)
    || !metadata.controls.length || metadata.controls.length > 64) {
    throw new Error('外見Metadataの形式に対応していません。');
  }
  const expressionMorphs = expressionTargets(json);
  const owned = new Set();
  const referencedNodes = new Set();
  const controls = [];
  const ids = new Set();
  let operations = 0;
  for (const control of metadata.controls) {
    if (!control || typeof control.id !== 'string' || !control.id || ids.has(control.id)
      || typeof control.defaultOption !== 'string' || !Array.isArray(control.options)
      || control.options.length < 2 || control.options.length > 32) {
      throw new Error('外見Metadataの項目が不正です。');
    }
    ids.add(control.id);
    const optionIds = new Set();
    let targets;
    let hasDefault = false;
    for (const option of control.options) {
      if (!option || typeof option.id !== 'string' || !option.id || optionIds.has(option.id)
        || !Array.isArray(option.visibility) || !Array.isArray(option.morphs)) {
        throw new Error('外見の操作一覧が不正です。');
      }
      optionIds.add(option.id);
      hasDefault ||= option.id === control.defaultOption;
      operations += option.visibility.length + option.morphs.length;
      if (operations > 4096) throw new Error('外見の操作数が多すぎます。');
      const current = new Set();
      for (const reference of option.visibility) {
        const node = assertRenderable(json, reference);
        if (typeof reference.value !== 'boolean' || !node) throw new Error('外見の表示指定が不正です。');
        const key = `visible:${reference.node}`;
        if (current.has(key)) throw new Error('同じ外見の操作が重複しています。');
        current.add(key);
        referencedNodes.add(reference.node);
      }
      for (const reference of option.morphs) {
        const node = assertRenderable(json, reference);
        const mesh = json.meshes[node.mesh];
        if (!Number.isInteger(reference.index) || reference.index < 0
          || typeof reference.morphName !== 'string'
          || mesh.extras?.targetNames?.[reference.index] !== reference.morphName
          || !mesh.primitives?.length || !mesh.primitives.every(p => p.targets?.[reference.index])
          || typeof reference.weight !== 'number' || !Number.isFinite(reference.weight)
          || reference.weight < -1 || reference.weight > 1) {
          throw new Error('外見の変形参照または値が不正です。');
        }
        const key = `morph:${reference.node}:${reference.index}`;
        if (expressionMorphs.has(`${reference.node}:${reference.index}`)) {
          throw new Error('外見の変形が表情・瞬き・視線と競合しています。');
        }
        if (current.has(key)) throw new Error('同じ外見の操作が重複しています。');
        current.add(key);
        referencedNodes.add(reference.node);
      }
      if (!current.size || (targets &&
        (targets.size !== current.size || [...targets].some(key => !current.has(key))))) {
        throw new Error('外見の選択肢には同じ対象の完全な指定が必要です。');
      }
      targets = current;
    }
    if (!hasDefault) throw new Error('外見Metadataの既定値が不正です。');
    for (const target of targets) {
      if (owned.has(target)) throw new Error('複数の外見項目が同じ対象を変更します。');
      owned.add(target);
    }
    controls.push(control);
  }
  // Parent and child cannot be independently controlled, even across controls.
  for (const index of referencedNodes) {
    const pending = [...(json.nodes[index].children ?? [])];
    while (pending.length) {
      const child = pending.pop();
      if (referencedNodes.has(child)) throw new Error('外見の操作が別の対象に影響します。');
      pending.push(...(json.nodes[child].children ?? []));
    }
  }
  return controls;
}

export function appearanceLabels(json) {
  const controls = inspectAppearance(json);
  const names = controls.map(control => control.label);
  const duplicateNames = new Set(names.filter((name, index) => names.indexOf(name) !== index));
  return controls.flatMap(control => {
    if (!validName(control.label) || duplicateNames.has(control.label)) return [];
    const options = control.options.map(option => option.label);
    if (!options.every(validOptionLabel) || new Set(options).size !== options.length) return [];
    return [{ name: control.label, options }];
  });
}
