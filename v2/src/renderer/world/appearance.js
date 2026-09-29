import { defaultAppearance, validateAppearance, validateCatalog } from '../../shared/appearance.ts';
import { readAppearanceControls } from './appearance-controls.js';

const EMOTIONS = ['happy', 'angry', 'sad', 'relaxed', 'surprised'];
const TRANSITION_SECONDS = .25;

function renderableNode(json, reference) {
  if (!reference || !Number.isInteger(reference.node) || reference.node < 0 || reference.node >= json.nodes?.length) {
    throw new Error('衣服のNode参照が不正です。');
  }
  const node = json.nodes[reference.node];
  if (!Number.isInteger(node?.mesh) || !json.meshes?.[node.mesh]) throw new Error('衣服のNodeに描画対象がありません。');
  if (typeof reference.nodeName !== 'string' || reference.nodeName !== (node.name ?? '')) {
    throw new Error('衣服のNode名が成果物と一致しません。');
  }
  return reference.node;
}

function assertIndependentItem(json, index) {
  const pending = [...(json.nodes[index].children ?? [])];
  const visited = new Set([index]);
  while (pending.length) {
    const childIndex = pending.pop();
    if (!Number.isInteger(childIndex) || !json.nodes[childIndex] || visited.has(childIndex)) {
      throw new Error('衣服のNode階層が不正です。');
    }
    visited.add(childIndex);
    const child = json.nodes[childIndex];
    // Visibility hides descendants too. Never let one item hide another renderable, including undeclared body parts.
    // The item's own glTF mesh primitives belong to this node and remain valid.
    if (child.mesh !== undefined) throw new Error('衣服の切替が子Nodeの描画対象に影響します。');
    if (child.children) pending.push(...child.children);
  }
}

async function readWardrobe(gltf) {
  const json = gltf.parser.json;
  const metadata = json.extras?.nirai?.capabilities?.wardrobe;
  if (metadata === undefined) return { catalog: [], nodes: new Map() };
  if (!metadata || metadata.schemaVersion !== 1 || metadata.mode !== 'visibility'
    || !Array.isArray(metadata.items) || !metadata.items.length || !Array.isArray(metadata.bodyBaseNodes)) {
    throw new Error('衣服Metadataの形式に対応していません。');
  }
  const catalog = validateCatalog({ expressions: [], wardrobe: metadata.items.map(item => ({
    id: item?.id, category: item?.category, tags: item?.tags,
    default_visible: item?.defaultVisible, removable: item?.removable,
  })) }).wardrobe;
  const references = [...metadata.bodyBaseNodes, ...metadata.items];
  if (references.length > 512) throw new Error('衣服のNode参照が多すぎます。');
  const indices = references.map(reference => renderableNode(json, reference));
  if (new Set(indices).size !== indices.length) throw new Error('衣服と身体のNode参照が重複しています。');
  indices.slice(metadata.bodyBaseNodes.length).forEach(index => assertIndependentItem(json, index));
  const objects = await Promise.all(indices.map(index => gltf.parser.getDependency('node', index)));
  const selected = new Set(objects);
  if (selected.size !== objects.length) throw new Error('衣服の描画対象が重複しています。');
  for (const object of objects) {
    let renderable = false;
    object?.traverse?.(child => { if (child.isMesh) renderable = true; });
    if (!renderable) throw new Error('衣服の描画対象を読み込めません。');
    let parent = object.parent;
    while (parent && parent !== gltf.scene) {
      if (selected.has(parent)) throw new Error('衣服の切替が別の衣服や身体に影響します。');
      parent = parent.parent;
    }
    if (parent !== gltf.scene) throw new Error('衣服が表示中のAvatarに属していません。');
  }
  const itemObjects = objects.slice(metadata.bodyBaseNodes.length);
  return { catalog, nodes: new Map(catalog.map((item, index) => [item.id, itemObjects[index]])) };
}

export async function createAvatarAppearance(vrm, gltf) {
  const manager = vrm.expressionManager;
  // The VRM loader normalizes VRM 0.x presets. Source-specific or custom names are not inferred here.
  const presets = manager?.presetExpressionMap ?? {};
  const expressions = EMOTIONS.filter(id => presets[id]?.binds.length > 0)
    .map(id => ({ id, is_binary: presets[id].isBinary === true }));
  const warnings = [];
  let wardrobe;
  try { wardrobe = await readWardrobe(gltf); }
  catch (error) {
    wardrobe = { catalog: [], nodes: new Map() };
    warnings.push(`衣服の切替を無効にしました。${error.message}`);
  }
  let controls;
  try { controls = await readAppearanceControls(gltf, { renderableNode, assertIndependentItem, wardrobeNodes: wardrobe.nodes }); }
  catch (error) {
    controls = { catalog: [], apply() {}, matches() { return true; } };
    warnings.push(`外見の選択を無効にしました。${error.message}`);
  }
  const catalog = validateCatalog({ expressions, wardrobe: wardrobe.catalog, controls: controls.catalog });
  let appearance = defaultAppearance(catalog);
  let from = null;
  let progress = TRANSITION_SECONDS;
  const weight = id => appearance.expression?.id === id ? appearance.expression.weight : 0;
  const update = delta => {
    progress = Math.min(TRANSITION_SECONDS, progress + (Number.isFinite(delta) ? Math.max(0, delta) : 0));
    const fraction = progress / TRANSITION_SECONDS;
    const blend = fraction * fraction * (3 - 2 * fraction);
    for (const item of expressions) {
      const initial = from?.get(item.id) ?? 0;
      manager.setValue(item.id, initial + (weight(item.id) - initial) * blend);
    }
  };
  const apply = (input, { immediate = false } = {}) => {
    // Validate the complete request before changing any expression or mesh visibility.
    const next = validateAppearance(input, catalog);
    if (from && next.expression?.id === appearance.expression?.id && next.expression?.weight === appearance.expression?.weight
      && catalog.wardrobe.every(item => next.wardrobe[item.id] === appearance.wardrobe[item.id])
      && (catalog.controls ?? []).every(item => next.choices[item.id] === appearance.choices[item.id])) {
      if (immediate) update(TRANSITION_SECONDS);
      return;
    }
    from = new Map(expressions.map(item => [item.id, manager.getValue(item.id) ?? 0]));
    appearance = next;
    for (const [id, object] of wardrobe.nodes) object.visible = appearance.wardrobe[id];
    controls.apply(appearance.choices);
    progress = expressions.every(item => from.get(item.id) === weight(item.id)) ? TRANSITION_SECONDS : 0;
    update(immediate ? TRANSITION_SECONDS : 0);
  };
  apply(appearance, { immediate: true });
  return {
    get catalog() { return structuredClone(catalog); },
    warnings, apply, update,
    // During a transition this is the accepted target; settled identifies when it is fully displayed.
    get appearance() { return structuredClone(appearance); },
    get settled() { return progress === TRANSITION_SECONDS && controls.matches(appearance.choices); },
    blink(value) {
      if (presets.blink?.binds.length) manager.setValue('blink', value);
      else if (presets.blinkLeft?.binds.length && presets.blinkRight?.binds.length) {
        manager.setValue('blinkLeft', value); manager.setValue('blinkRight', value);
      }
      // VRMExpressionManager combines these body weights with authored overrideBlink/LookAt/Mouth rules.
    },
  };
}
