# Nirai v2 AI Entry

Niraiで設計・実装を行うAIは、必要な範囲だけを次の順で読む。

1. `WORLD_RULES.md`
2. `Nirai_v2_基本設計書.md`
3. UIを扱う場合のみ`prototype/`

この3つを現行仕様の正本とする。

## 移行中の既存実装

`core/`、`world/`、`tools/`にはv1からの実装が残っている。これらは再利用候補であり、v2仕様の根拠ではない。

既存実装を利用する前に、次を確認する。

- v2で責務が一つに定まるか。
- Task / Step / Attempt等の状態正本を独自に持たないか。
- Chat / Conversation / UI状態をTask制御へ混ぜないか。
- 既存の互換処理、Recovery、例外経路を持ち込むより削減・統合できないか。

条件を満たさない場合は、そのまま移植せず必要な責務だけを分離して再構成する。

現在のHolo Local連携とWorkflow Toolは、v2のControl interfaceへ置換されるまで開発経路として保持する。これをv2 Control Planeの設計根拠にはしない。

Provider固有仕様や外部APIを変更する場合は、その時点の公式資料と実機で再確認する。過去の検証記録をCurrent仕様の代わりに使わない。
