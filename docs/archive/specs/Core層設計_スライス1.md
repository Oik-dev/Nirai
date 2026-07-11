# Core層 設計書 — スライス1（最小の司令塔）

最終更新: 2026-06-28 / 設計: Opus / 実装: Cursor（予定）
位置づけ: 設計書.txt §3・§4・§7 を具体化。フルCore（司令塔）を**目標**とし、その縦の背骨を最初のスライスとして確定する。

---

## 1. ゴール
CLIで動く対話ループ。Coreが1ターンを統括する:
人格(Prompt層)読込 → Memory検索で文脈注入 → ルート決定（v1は常に対話Skill） → Aurora(対話モデル)呼出 → 応答 → 履歴保存。

これにより「Coreが"どのSkillで応えるか決めて"応答を返す」司令塔の型を最小実装で動かし、後続スライス（本格ルーター・他Skill・reflection）を**同じインターフェースに差し込む**形で拡張可能にする。

## 2. 設計原則（設計書 §5 厳守）
- 依存方向: **Core → Skills → Connectors → External**。Memory / Prompt は**参照のみ**。逆流禁止。
- 責務分離: Coreは「決めるだけ」/ Skillは「やるだけ」/ Connectorは「繋ぐだけ」/ Memoryは「覚えるだけ」/ Promptは「性格だけ」。
- 人格はモデルに焼かない。**Coreが実行時にsystemプロンプトとして注入**する。

## 3. フォルダ構成（`serina/` 配下）
```
serina/
  core/
    runtime.py      # Core本体（1ターン統括）
    context.py      # 文脈組み立て（persona + 記憶ブロック / 履歴→messages）
    router.py       # Skill選択（v1は対話Skillを返すだけ）
    config.py       # Core設定値
  prompt/
    loader.py       # 人格ファイル読込
    persona.md      # Serina.Modelfile.txt のSECTION1〜3を切り出した人格本体
  skills/
    base.py         # Skill インターフェース（契約）
    chat.py         # 直接応答Skill（Connector経由でAurora呼出）
  connectors/
    embedder.py     # 既存（埋め込み）
    chat_llm.py     # 新規: Ollama /api/chat で Aurora を叩く対話Connector
  memory/           # 既存（Coreから参照のみ）
  app/
    repl.py         # CLIチャット入口（人間検証用）
  tests/
    smoke_core.py   # Core層スモーク（記憶注入が見える形で検証）
```

## 4. コンポーネント仕様

### 4.1 Prompt層 — `prompt/loader.py` + `prompt/persona.md`
- `persona.md`: `Serina.Modelfile.txt` の SECTION1〜3（人格・口調・境界）をそのまま移植。Modelfile固有ヘッダ（`# [SYSTEM PROMPT...]` 等）は不要部分を除く。
- `load_persona(path=None) -> str`: ファイルを読んで文字列で返すだけ。ロジックを持たない。

### 4.2 Connector — `connectors/chat_llm.py`
- インターフェース（Protocol）`ChatConnector.chat(system: str, messages: list[dict], options: dict | None, on_token: Callable[[str], None] | None) -> str`。
- 実装 `OllamaChatConnector(model, base_url="http://127.0.0.1:11434", timeout)`:
  - `POST /api/chat`。`on_token` 省略時は `stream=False` の一括応答（v1互換）。
  - `on_token` 指定時は `stream=True` で NDJSON を逐次消費し、チャンクごとに `on_token(chunk)` を呼びつつ全文を組み立てて返す（ストリーミング表示）。
  - リクエスト: `{"model", "messages": [{"role":"system","content":system}, *messages], "options": options, "stream": <on_tokenの有無>}`。
  - レスポンスの `message.content` を返す。空・失敗時は例外。
- ビジネスロジックを持たない（設計書 §3 Connector制約）。

### 4.3 Skill層 — `skills/base.py` + `skills/chat.py`
- `SkillContext`（dataclass）: `user_input: str` / `system_prompt: str` / `history: list[dict]`（chat messages形式）/ `on_token: Callable | None = None`（ストリーミング表示用。非対応Skillは無視してよい）。
- `Skill`（Protocol）: `name: str` / `can_handle(user_input: str) -> bool` / `run(ctx: SkillContext) -> str`。
- `ChatSkill(connector, options)`:
  - `name="chat"`, `can_handle` は常に `True`（既定フォールバック）。
  - `run`: `connector.chat(ctx.system_prompt, ctx.history + [{"role":"user","content":ctx.user_input}], options)` を返す。
- 他Skillを直接呼ばない / 外部APIを直接叩かない（Connector経由のみ）。

### 4.4 Router — `core/router.py`
- `select(user_input: str, skills: list[Skill]) -> Skill`: `can_handle` がTrueの先頭を返す。v1はChatSkillのみなので常にChat。
- スライス2で本格的な選択ロジック（ルール/LLM function-calling）に差し替える接点。

### 4.5 Core本体 — `core/runtime.py`
```
class Core:
    def __init__(self, store: MemoryStore, persona: str,
                 skills: list[Skill], router, config: CoreConfig): ...

    def turn(self, session_id: str, user_input: str) -> dict:
        # ① 記憶検索（pinned込み）
        mems = store.search(user_input, k=config.memory_k)
        # ② 直近履歴
        history = store.get_recent_history(session_id, config.history_n)
        history_msgs = context.to_messages(history)
        # ③ systemプロンプト組立（人格 + 関連記憶ブロック）
        system = context.build_system(persona, mems, config)
        # ④ ルート決定
        skill = router.select(user_input, skills)
        # ⑤ Skill実行
        ctx = SkillContext(user_input, system, history_msgs)
        reply = skill.run(ctx)
        # ⑥ 履歴のみ書戻し（長期記憶の自動生成はしない＝reflection/スライス3）
        store.add_history(session_id, "user", user_input)
        store.add_history(session_id, "assistant", reply)
        # 検証用に内部情報も返す
        return {"reply": reply, "skill": skill.name,
                "retrieved": [m for m in mems], "system_len": len(system)}
```
- 注意: 直近履歴は「今回のinput保存前」に取得し、inputは最新userメッセージとして別途渡す（二重計上を防ぐ）。

### 4.6 文脈組立 — `core/context.py`
- `build_system(persona, mems, config) -> str`: `persona` の後ろに `## 関連する記憶` セクションを付与。各記憶を簡潔に列挙（type/contentを1行）。総量は `config.memory_block_char_cap` で打ち切り。記憶0件なら記憶ブロックは省略。
- `to_messages(history) -> list[dict]`: `get_recent_history` の `{role, content}` を chat messages へ変換。

### 4.7 設定 — `core/config.py`
```
@dataclass
class CoreConfig:
    model: str = "<Auroraの実タグ>"   # Cursorが ollama list で確認して設定
    base_url: str = "http://127.0.0.1:11434"
    memory_k: int = 6
    history_n: int = 8
    memory_block_char_cap: int = 1500
    temperature: float = 0.8
```

### 4.8 CLI入口 — `app/repl.py`
- 起動時: `MemoryStore`（埋め込み器注入）/ `persona` / `OllamaChatConnector` / `ChatSkill` / `Core` を組み立て。`session_id` を1つ発番。
- ループ: 入力受付 → `core.turn` → `reply` 表示。空入力・`/exit` で終了。

## 5. データフロー
```
CLI入力 → Core.turn
  → Memory.search + Memory.get_recent_history（参照）
  → context.build_system（人格＋記憶）
  → Router.select → ChatSkill.run → ChatConnector.chat → Ollama(Aurora)
  → Memory.add_history（user/assistant）
→ CLI出力
```

## 6. エラー処理（REPLを落とさない）
- Ollama接続失敗 / タイムアウト → ユーザーへやさしいメッセージを返し、ループは継続。例外内容はログ。
- 記憶検索失敗 → 人格のみで縮退してターン続行（落とさない）。
- 起動時にモデルタグがOllamaに無い → 明確なエラーメッセージで停止（タグ設定ミスを即発見）。
- 空入力 → スキップ。

## 7. 検証（人間が目で確認できる形）
1. `rtk python serina/tests/smoke_core.py`:
   - 既知の移行済み記憶に関する質問を1ターン投げ、**(a)取得された記憶の見出し一覧 (b)systemプロンプト長 (c)使われたSkill名 (d)Auroraの応答** を日本語で表示。
   - 2ターン目で1ターン目の内容を踏まえるか（履歴注入）を確認。
2. `rtk python serina/app/repl.py`:
   - 実対話。固定記憶に絡む話題で人格・記憶が効いているかをマスターが体感。
- 合格条件: 記憶取得ログに関連記憶が出る／応答が人格(セリナ)らしい／2ターン目が文脈を保持。

## 8. スコープ外（後続スライス）
- 本格ルーティング・2つ目以降の実Skill（スライス2）
- reflection（会話→長期記憶の自動蒸留・整理）（スライス3）
- External Services 連携 / ストリーミング表示 / GUI / 2台間DB同期

## 9. 実装前の要確認（Cursorが先頭で実施）
- `ollama list` でAuroraの**実タグ**を確認し `CoreConfig.model` に設定。
- 設計上は**人格を焼いていない素のAurora**を使う。人格入りの「serina」モデルしか無い場合は、素のAuroraを用意するか、その旨を申告して指示を仰ぐ。
