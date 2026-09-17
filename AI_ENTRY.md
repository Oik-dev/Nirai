# Nirai v2 AI Entry

Niraiで設計・実装を行うAIは、必要な範囲だけを次の順で読む。

1. `WORLD_RULES.md`
2. `Nirai_v2_基本設計書.md`
3. 初期自走化の実装中のみ`Nirai_v2_初期自走化計画.md`
4. UIを扱う場合のみ`prototype/`

`Nirai_v2_初期自走化計画.md`はM1〜M8完了後にArchiveへ退役し、それ以降は現行設計の根拠として読まない。

既存`core/`、`world/`、`tools/`はv1からの再利用候補であり、v2仕様の根拠ではない。

再利用は「既に動くか」ではなく、「v2でゼロから作るより単純・安全・保守しやすくなるか」で判断する。旧Workflow、Auto Resume専用経路、Conversation ownership、Agent Session中心の状態管理等を前提に設計しない。

Task状態とTask単位のResume ON / OFFは別概念とする。Pause / 再開でResume設定を勝手に変更せず、Resume設定の切替でもTask状態を変更しない。

現在のHolo Local連携はv2 Control APIへ置換されるまで開発経路として保持する。旧Persona本文は変更せず移行する。

Provider固有仕様や外部APIを変更する場合は、その時点の公式資料と実機で確認する。
