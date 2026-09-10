# Active Docs Audit — 2026-09-09

Docs cleanup / DNA Architecture v0.5反映後に、次のCurrent文書を対象として相対Markdown Linkと既知の旧Pathを機械検査した。

- `AI_ENTRY.md`
- `Docs/README.md`
- `Docs/Nirai_基本設計.md`
- `Docs/Nirai_設計ガバナンス.md`
- `Docs/Nirai_DNA_UE427_Architecture_2026-09-08.md`
- `Docs/詳細設計/*.md`

## 結果

- Broken Markdown Link: **0件**
- 旧`Nirai_DNA_UE4.27_WorldAddon方針_2026-09-06.md`をCurrent正本として指す参照: **0件**
- 旧`Docs/plans/`直下を指す既知参照: **0件**
- 旧Evidence配置（`Docs/*_検証結果.md`等）への主要な既知参照: **0件**
- VOICEVOX文字列: Current文書に残るものは、旧Standard Worldの実装履歴、Legacy移行、または「新Architectureから除外した」ことを説明する記述のみ。新しいVoice依存・初期Provider指定としては残していない。

旧AITuberKit / AIAvatarKit Blueprintは`Docs/archive/reference-blueprints/`へ移動済みで、Current Designを拘束しない。
