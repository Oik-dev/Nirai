export interface AppearanceCatalog {
  expressions: Array<{ id: string; is_binary: boolean }>;
  wardrobe: Array<{ id: string; category: string; tags: string[]; default_visible: boolean; removable: boolean }>;
  controls?: Array<{ id: string; label: string; category: string; default_option: string; options: Array<{ id: string; label: string }> }>;
}

export interface Appearance {
  expression: { id: string; weight: number } | null;
  wardrobe: Record<string, boolean>;
  choices?: Record<string, string>;
}

export interface DesiredAppearance { revision: string | null; appearance: Appearance }

export interface AvatarRuntimeReport {
  resident_id: string;
  model_path: string;
  model_id: string | null;
  token: string | null;
  capabilities: AppearanceCatalog | null;
  applied_revision: string | null;
  status: "ready" | "unavailable";
  error?: string;
}

export interface AvatarState extends AvatarRuntimeReport {
  desired: DesiredAppearance | null;
  display_applied: boolean;
}

function object(input: unknown, fields: readonly string[], label: string): Record<string, unknown> {
  if (!input || typeof input !== "object" || Array.isArray(input)
    || Object.keys(input).some(key => !fields.includes(key))) throw new Error(`invalid ${label}`);
  return input as Record<string, unknown>;
}

function text(input: unknown, label: string): string {
  if (typeof input !== "string" || !input.trim() || input.length > 160 || /[\u0000-\u001f]/.test(input)
    || ["__proto__", "constructor", "prototype"].includes(input)) throw new Error(`invalid ${label}`);
  return input;
}

export function validateCatalog(input: unknown): AppearanceCatalog {
  const value = object(input, ["expressions", "wardrobe", "controls"], "Avatar capabilities");
  if (!Array.isArray(value.expressions) || value.expressions.length > 128
    || !Array.isArray(value.wardrobe) || value.wardrobe.length > 128) throw new Error("invalid Avatar capability count");
  const expressions = value.expressions.map(item => {
    const entry = object(item, ["id", "is_binary"], "expression capability");
    if (typeof entry.is_binary !== "boolean") throw new Error("invalid expression binary flag");
    return { id: text(entry.id, "expression id"), is_binary: entry.is_binary };
  });
  const wardrobe = value.wardrobe.map(item => {
    const entry = object(item, ["id", "category", "tags", "default_visible", "removable"], "wardrobe capability");
    if (typeof entry.default_visible !== "boolean" || typeof entry.removable !== "boolean"
      || !Array.isArray(entry.tags) || entry.tags.length > 16) throw new Error("invalid wardrobe capability");
    return {
      id: text(entry.id, "wardrobe id"), category: text(entry.category, "wardrobe category"),
      tags: entry.tags.map(tag => text(tag, "wardrobe tag")),
      default_visible: entry.default_visible, removable: entry.removable,
    };
  });
  if (new Set(expressions.map(item => item.id)).size !== expressions.length
    || new Set(wardrobe.map(item => item.id)).size !== wardrobe.length) throw new Error("duplicate Avatar capability id");
  if (value.controls === undefined) return { expressions, wardrobe };
  if (!Array.isArray(value.controls) || value.controls.length > 64) throw new Error("invalid appearance control count");
  const controls = value.controls.map(item => {
    const entry = object(item, ["id", "label", "category", "default_option", "options"], "appearance control");
    if (!Array.isArray(entry.options) || entry.options.length < 2 || entry.options.length > 32) throw new Error("invalid appearance options");
    const options = entry.options.map(item => {
      const option = object(item, ["id", "label"], "appearance option");
      return { id: text(option.id, "option id"), label: text(option.label, "option label") };
    });
    if (new Set(options.map(item => item.id)).size !== options.length || !options.some(item => item.id === entry.default_option)) {
      throw new Error("invalid appearance default or duplicate option");
    }
    return { id: text(entry.id, "control id"), label: text(entry.label, "control label"),
      category: text(entry.category, "control category"), default_option: entry.default_option as string, options };
  });
  if (new Set(controls.map(item => item.id)).size !== controls.length) throw new Error("duplicate appearance control");
  return { expressions, wardrobe, ...(controls.length ? { controls } : {}) };
}

export function defaultAppearance(catalog: AppearanceCatalog): Appearance {
  return { expression: null, wardrobe: Object.fromEntries(catalog.wardrobe.map(item => [item.id, item.default_visible])),
    ...(catalog.controls?.length ? { choices: Object.fromEntries(catalog.controls.map(item => [item.id, item.default_option])) } : {}) };
}

export function validateAppearance(input: unknown, catalog: AppearanceCatalog): Appearance {
  const value = object(input, ["expression", "wardrobe", "choices"], "appearance");
  let expression: Appearance["expression"] = null;
  if (value.expression !== null) {
    const choice = object(value.expression, ["id", "weight"], "expression choice");
    const spec = catalog.expressions.find(item => item.id === choice.id);
    if (!spec || typeof choice.weight !== "number" || !Number.isFinite(choice.weight)
      || choice.weight < 0 || choice.weight > 1
      || spec.is_binary && choice.weight !== 0 && choice.weight !== 1) throw new Error("unsupported expression or weight");
    expression = { id: spec.id, weight: choice.weight };
  }
  const wardrobe = object(value.wardrobe, catalog.wardrobe.map(item => item.id), "wardrobe choice");
  const selected: Record<string, boolean> = {};
  for (const item of catalog.wardrobe) {
    const visible = wardrobe[item.id];
    if (typeof visible !== "boolean") throw new Error(`wardrobe choice is required: ${item.id}`);
    if (!item.removable && visible !== item.default_visible) throw new Error(`wardrobe item is fixed: ${item.id}`);
    selected[item.id] = visible;
  }
  if (!catalog.controls?.length) {
    if (value.choices !== undefined && Object.keys(object(value.choices, [], "appearance choices")).length) throw new Error("unsupported appearance choice");
    return { expression, wardrobe: selected };
  }
  const choices = object(value.choices, catalog.controls.map(item => item.id), "appearance choices");
  const chosen: Record<string, string> = {};
  for (const control of catalog.controls) {
    if (!control.options.some(option => option.id === choices[control.id])) throw new Error(`unsupported or missing appearance choice: ${control.id}`);
    chosen[control.id] = choices[control.id] as string;
  }
  return { expression, wardrobe: selected, choices: chosen };
}
