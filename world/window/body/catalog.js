// 体が持つ表情の名前と、世界が持つ身振りの名前の正本。海と窓が同じ対応を使う。
const EXPRESSION_LABELS = Object.freeze({
  neutral: '平常',
  happy: '喜び',
  angry: '怒り',
  sad: '悲しみ',
  relaxed: '楽しさ',
  surprised: '驚き',
});
const LEGACY_PRESETS = Object.freeze({
  neutral: 'neutral', joy: 'happy', angry: 'angry', sorrow: 'sad', fun: 'relaxed',
  blink: 'blink', blink_l: 'blinkLeft', blink_r: 'blinkRight',
  lookup: 'lookUp', lookdown: 'lookDown', lookleft: 'lookLeft', lookright: 'lookRight',
  a: 'aa', i: 'ih', u: 'ou', e: 'ee', o: 'oh',
});

// 瞬き・視線・発声の形は生理側が持ち、本人の選ぶ表情には入れない。VRM 0.x の名前も同じ約束。
const PHYSIOLOGICAL_EXPRESSIONS = Object.freeze([
  'blink', 'blinkLeft', 'blinkRight', 'lookUp', 'lookDown', 'lookLeft', 'lookRight',
  'aa', 'ih', 'ou', 'ee', 'oh',
]);
export const OWNED_EXPRESSIONS = Object.freeze([...new Set([
  ...PHYSIOLOGICAL_EXPRESSIONS,
  ...Object.keys(LEGACY_PRESETS).filter(name => PHYSIOLOGICAL_EXPRESSIONS.includes(LEGACY_PRESETS[name])),
])]);

// 世界の.vrma（使い捨てのイデアではなく、全員共通の身振り）。
export const WORLD_GESTURES = Object.freeze({ '伸び': '伸び' });
export const GESTURE_NAMES = Object.freeze([
  'うなずく', '首を振る', '首をかしげる', '小さく手を振る', '身体を傾ける', 'おじぎ',
  ...Object.keys(WORLD_GESTURES),
]);

export function expressionLabel(raw) {
  const name = Object.hasOwn(LEGACY_PRESETS, raw) ? LEGACY_PRESETS[raw] : raw;
  return Object.hasOwn(EXPRESSION_LABELS, name) ? EXPRESSION_LABELS[name] : raw;
}

// VRMのプリセットとして読める名前だけを、窓で使う1.0の名前へ直す。
export function expressionPresetName(raw, version) {
  if (typeof raw !== 'string') return null;
  if (version === '0') return Object.hasOwn(LEGACY_PRESETS, raw) ? LEGACY_PRESETS[raw] : null;
  return Object.hasOwn(EXPRESSION_LABELS, raw) || PHYSIOLOGICAL_EXPRESSIONS.includes(raw) ? raw : null;
}

export function expressionKey(label, availableRawNames) {
  if (typeof label !== 'string' || !label.trim() || label === 'なし' || label === 'そのまま') return null;
  if (availableRawNames.includes(label) && !OWNED_EXPRESSIONS.includes(label)) return label;
  return availableRawNames.find(name => !OWNED_EXPRESSIONS.includes(name) && expressionLabel(name) === label) ?? null;
}
