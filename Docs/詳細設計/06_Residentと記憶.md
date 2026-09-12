# Nirai 詳細設計 06：Resident定義と記憶

Product Goalは [Nirai_基本設計.md](../Nirai_基本設計.md)、設計判断ルールは [Nirai_設計ガバナンス.md](../Nirai_設計ガバナンス.md) を正とする。配布可能なStandard WorldのAvatar規格は [09_3DビジュアルとAvatarパイプライン.md](09_3DビジュアルとAvatarパイプライン.md)、Private DNA WorldのBody／Voiceは [Nirai_DNA_UE427_Architecture_2026-09-08.md](../Nirai_DNA_UE427_Architecture_2026-09-08.md) v0.5を正とする。Whisper Private Channel / Provider Working Contextの旧Referenceは [Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md](../archive/research-and-old-design/Nirai_Reference-First調査_Whisper長期Conversation_2026-09-06.md) に退避している。

## 概要

本章は、Resident 1人分の人格・設定と、Nirai全体の記憶構造を定義する。

記憶はResidentごとに同じ公開情報を複製しない。

- Say、公開Resident会話、世界イベントは**World Memory**として1つだけ保存する
- Whisperは宛先Residentだけの**Private Memory**として保存する
- Public / Privateとも数年単位で蓄積する前提とし、最低5年間の日常利用で根本作り直しを必要としない構造を目標にする
- 現在進行中の会話履歴は一時Contextとして扱うが、長期記憶の唯一の正本にはしない
- 各CLI自身のMemoryやProvider native Session / Threadは会話Context継続に利用してよいが、NiraiのIdentity・長期記憶の唯一の正本にはしない

MemoryはPublic / Privateそれぞれで次の3層へ責務分離する。

1. **Raw Durable Source** — losslessな発話・事実・Eventの永続正本
2. **Structured Continuity Memory** — Rawから根拠付きで生成する継続状態。内部は`Atomic Memory / Fact`、`Episode / Scenario`、`Durable Continuity`の段階化を候補とする
3. **Derived Retrieval Index** — FTS / Embedding / chunk等の再生成可能な検索派生データ

表示用excerpt、Episode要約、Retriever chunk、`context.md`等をRaw Durable Sourceの代わりにしない。

### Memory Ownership / Scope Contract

Memoryの物理実装より先に、誰が正本を所有し、誰へ見せてよいかを固定する。

```text
MemoryOwner
  nirai-world
  nirai-resident:<resident_id>
  external-resident:<resident_id>

MemoryScope
  public
  private:<resident_id>
```

- World Memoryの正本Ownerは`nirai-world`
- 通常ResidentのPrivate MemoryはNiraiがOwnerとなる
- Serina等、既に独立した長期Memory Systemを持つResidentは`external-resident` Adapterを選択できる。Nirai側へ同じPrivate Memoryを二重保存しない
- `MemoryScope`はRetrieverへ渡す任意filterではなく、Storage / Query / Index生成前段で強制するPrivacy Boundaryとする
- Derived Indexを作る時点でもPublic / Privateを混ぜない

### Structured Continuityの候補段階

2026-09-06のReference-First調査では、Rawと単一Summaryの二段構造より、次の段階構造を比較する価値が高いと判断した。

1. **Atomic Memory / Fact** — fact / promise / preference / relationship等の小さな意味単位
2. **Episode / Scenario** — ある会話・出来事・期間の意味まとまり
3. **Durable Continuity** — 現在有効な好み・関係・未解決事項・Identity隣接状態等

各Structured Memoryは、必ず元のRaw Entry / Event IDへ遡れるprovenanceを持つ。LLMが生成したStructured Textだけを証拠にしない。

この段階化を最終方式として先に固定しない。旧Evaluation設計は`../archive/research-and-old-design/Nirai_MemoryEvaluation設計_2026-09-06.md`へ退避済みで、単純構成を上回る価値が実測できた層だけ採用する。Current Product判断は本書と最新Evidenceを優先する。

## フォルダ構造

以下は**概念上の責務構造**を示す。物理Storage形式・DB選定・Index方式は大規模Memory再設計の実装前にReference-First Gateを通し、公式機能・成熟OSS・長期運用事例を比較して確定する。

```text
residents\<住人名>\
  persona.md
  config.toml
  private\
    raw\              # Private Raw Durable Source
    continuity\       # Structured Continuity Memory
    index\            # Private Retriever派生データ
    context.md         # 必要なら人間可読の派生View。正本ではない

world_memory\
  world_memory.sqlite3  # Current Public Raw + Structured + FTS + sqlite-vec。Rawが正本
  episodes\             # 旧M1互換Episode View。派生 / Legacy fallbackであり正本ではない
  index\                # 旧Episode Retrieverの派生Index。Legacy Session移行用

runtime\
  chat_sessions\       # UI履歴。Memory Raw Sourceとは別責務
```

現行の`whispers.jsonl`、`events/*.jsonl`、`episodes/*.md`等は移行元データとして保持する。新しい物理配置・DBへ移行する場合も、移行完了・検証前に既存Rawを破棄しない。

`index`は記憶の正本ではない。削除・破損時はRaw Durable SourceとStructured Continuityから再生成できること。

`world_memory\`と`residents\*\private\`は個人会話を含むためGit管理外を既定とする。personaや設定をGit管理する場合もPrivate Memoryを同じ対象へ巻き込まない。

## Resident定義

### persona.md

人格の設定書。以下の見出しを必須とする。

- `# 名前`
- `## 性格`
- `## 口調`
- `## 日課`
- `## 得意`
- `## 決めごと`

人格はMasterと決める。実装AIが勝手に変更しない。

### config.toml

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| role | string | `resident` | Task上の役割。`resident / executor / integrated_auditor / commander`の4種。最大1名だけ`commander`を持てる |
| brain | string | - | Brain Provider名。既存データでは未設定を読み込み可能だが、新規作成UIでは必須選択。特殊値`holo-addon`はHolo Addon（ChatGPT Web）を頭脳にし、通常Brain Driverへ接続しない。`holo-addon`は同時に1人だけ（詳細は`12_HoloAddonとChatGPTDive.md`） |
| brain_model | string | - | Resident固有のModel ID。未設定時はProvider既定Modelを使う。Provider変更時に旧ProviderのModel IDを流用しない |
| brain_reasoning_effort | string | - | Codex専用。Resident固有の推論強度。未設定時はCodex既存Configを継承する。`low / medium / high / xhigh / ultra / max`のうち、選択Modelが対応する値だけUI候補にする |
| avatar | string | - | Standard World用の`avatars\\`配下VRM相対パス。未設定可。Private World固有Body割当の正本にはしない |
| tick_interval_min | int | 30 | 生活ティック間隔 |
| tick_budget | int | 頭脳別既定 | 生活ティック1日予算 |
| spawn_location | string | "center" | Standard World互換の初期Location。Private WorldではScene／Binding Runtimeが意味状態を管理 |
| tts.enabled | bool | true | **Current Standard World v1互換**。旧TTS再生の有効/無効 |
| tts.provider | string | `voicevox` | **Current Standard World v1互換**。旧TTS Provider。Private DNA / Protocol v2の共通Voice正本にはしない |
| tts.speaker_uuid / style_id | string? / int? | - | **Current Standard World v1互換**。旧Speaker / Style識別子 |
| tts.speed / pitch / intonation | number | 1.0 / 0.0 / 1.0 | **Current Standard World v1互換**の音声調整 |

Role、Brain Provider / Brain Model / Codex Reasoning、Standard World Avatar、Voice設定は互いに独立して差し替えられる。Resident新規作成時はRoleとBrain Providerを選択でき、ModelはProvider既定でも任意指定でもよい。CodexだけReasoningも任意指定でき、未指定ではProvider既定を継承する。Avatar / Voiceは未設定でもよい。既存ResidentのRole / Provider / Model / Reasoningは設定UIから差し替えられる。ただし`executor / integrated_auditor / commander`へ設定する場合は、そのResidentの現在Brain / ModelがAgent Work可能であることをCoreが再検証する。

### Resident Role

Task上の役割はResident IdentityやBrain Providerとは別のDomain状態として`config.toml`へ永続化する。

- `resident`：会話・生活用。通常Taskを受けない
- `executor`：通常Taskの自動候補。実装・調査・修正等の一般作業を担当する
- `integrated_auditor`：統合監査向けの専門Role。通常Taskの自動候補へは入れない。Commanderが上位Workflowで「大区切りの統合監査が必要」と判断した時、またはMasterが明示的に監査を要求した時だけ、専用`IA-*` Taskとして使う。統合監査はread-only Reviewではなく、必要ならSource修正・Refactor・Test追加まで実施できる
- `commander`：Task全体の指揮・割当・Fallback判断を担う。実行Capabilityを持つ場合は実行者不在時のFallback実行も可能

`commander`は同時に最大1名とし、新しいResidentへ変更した時は旧Commanderを実行Capabilityに応じて`executor`または`resident`へ降格する。Holo Addonは現行移行時にCommanderへ割り当てるが、Role自体はHolo専用概念にしない。将来別ResidentをCommanderへ変更できる。

Legacy ResidentにRoleがない場合のCurrent migrationは、Holo AddonをCommander、Agent Work可能なCursor / Codex / ClaudeをExecutor、それ以外をResidentへ寄せる。Model名から統合監査者を推測しない。Migration後はRoleを永続化し、再起動のたびに再推測しない。

### Provider Usage Budget

Resident設定UIは対応Providerの利用枠を同じResident設定面へ表示する。Provider固有のRaw応答をWorldへ漏らさず、Coreで`UsageBudgetSnapshot`へ正規化する。

- Windowは複数持てる。CodexはProviderが返した5h / weekly等、CursorはCursor Models / Other Models等を個別に保持する
- 各Windowは使用率、残量、reset時刻、resetまでの残時間、limit到達状態を持てる。Providerが返さない値を推測で補わない
- 取得は起動後、Task routing直前、Task終了後、明示Refresh、低頻度Pollingで行う
- 取得失敗時は直近成功値を`stale`として保持し、成功値が無ければ`UNKNOWN`へ縮退する。未知状態を0%や100%へ捏造しない
- Usage BudgetはTask routingの判断材料でありResidentの人格・記憶・Provider Identityの正本ではない


Current実装では配布可能なStandard World v1互換として`tts.*`をResident config / Protocolで現役利用している。これは既存Electron/Three.js Worldを維持するための互換境界であり、Private DNA WorldやProtocol v2の永久Voice Contractではない。

Private DNA / Protocol v2で採用するProvider非依存Voice境界は**未実装のTarget Contract**として、少なくとも`voice.presence = absent / present / unresolved`、`voice.provider`、Provider内Profileを指すopaque `voice.profile_id`、設定世代`voice.revision`を持てる形とする。Voice実装へ着手するP2段階でCurrent `tts.*` Schemaを再確認し、旧Speaker／Styleが有効なら`present`、有無を判定できなければ`unresolved`へ移行する。Provider停止を`absent`へ変換せず、空の既定値だけで「Voice設定なし」と断定しない。移行完了前に`tts.*`を消さず、移行後も旧VOICEVOX形式を新Architectureの正本へ昇格させない。

## Avatar定義

本節のAvatar定義は**配布可能なStandard World**を対象とし、Runtime標準形式・設定UI入力ともにVRMとする。Residentへ保存する`avatar`は`avatars\\`配下のVRM相対Pathだけとし、UnityPackage / FBX等の自動変換は行わない。Private DNA Worldではこの`avatar`を身体正本にせず、World側の排他的Body BindingでDNAプレイアブルBodyを割り当てる。

`avatar.toml`は必要なAvatarだけ、選択VRMと同じフォルダへ置く任意の補正ファイルとする。VRMそのものに含められないNirai固有補正だけを持つ。補正不要なら作らない。

| キー | 説明 |
|---|---|
| model | VRMファイルへの参照 |
| height_scale | 必要な場合だけ身長補正 |
| ground_offset | 接地補正 |
| bubble_anchor | 吹き出し位置補正 |
| look_anchor | 視線基準位置の補正 |

VRM標準Expression、Humanoid、LookAt等で解決できる情報を二重管理しない。

## 記憶の3区分

### 1. Temporary Context

現在進行中の会話に必要な直近履歴。

- 現在セッションの直近発言
- 現在時刻、Location、周囲の状態
- セッション終了後は正本として保持しない

公開会話ならWorld Memoryへ、WhisperならPrivate Memoryへ保存して役目を終える。

#### 公開Chat SessionとContext境界

公開Say / resident_chatのTemporary Context境界はNiraiの`Chat Session`と一致させる。

- 新しいChat Sessionを作成した場合、そのSessionの公開会話は新しいProvider native Contextから開始する
- 別Chat SessionのRaw会話履歴を新Sessionへ自動連結しない
- 過去の別Sessionで起きたことはWorld Memoryへ保存され、現在話題に関連するものだけRetrieverから戻す
- 同一Session内ではProvider native continuationを利用し、既送Raw履歴・Persona・Skills等を毎Turn再送しない
- native Context喪失時も、5年分の公開Rawを丸ごと再投入せず、Structured Continuity / World Retriever / 必要なrecent rawから再構成する

### 2. World Memory

全Residentが知りうる世界共通の過去。

対象：

- 公開チャットセッションのEpisode
- 生活ティックで世界に表出した主要な出来事
- タスクの公開結果
- Worldの主要イベント

同じ出来事をResident A用、B用、C用に複製しない。全Residentが同じWorld Memoryを参照する。

#### events

`world_memory\events\YYYY-MM-DD.jsonl`は、チャット以外のWorld Eventを1イベント1行で保持する。

`runtime\chat_sessions\<session-id>.jsonl`は**UI履歴の正本**であり、World MemoryのRaw Durable Sourceではない。

公開会話として長期記憶へ残す内容は、UI履歴とは独立したPublic Raw Durable Sourceへlosslessに保存する。UI履歴を削除してもWorld Memoryを残す操作では、Public Raw Durable Sourceを失わない。

同じTextを無目的に複数の長期正本へ複製するのではなく、Conversation Entry ID等でUI履歴とMemory Rawを関連付け、役割を分離する。2026-09-06 CurrentではPublic Rawを`world_memory/world_memory.sqlite3`の`raw_entries`へ全文保存し、`entry_id`がある場合はそれを安定IDへ使う。SQLiteはWAL + busy_timeoutを用い、1発言追記で全Memory Fileを書き直さない。

#### episodes / Structured Continuity

公開チャットの一区切りごとに、**Sayと公開Resident発話だけ**からEpisodeや構造化された継続情報を生成してよい。ただしEpisodeはRaw Durable Sourceの派生物であり、原文の代替正本にはしない。同じUIチャットセッションを後から再開した場合は、新しいEpisodeを追加してよい。

WhisperはEpisode生成入力から必ず除外する。UI上でSayとWhisperが同じチャットセッションに混在していても、Private内容をWorld Memoryへ混ぜない。

Residentごとに同じ要約を作らない。

**History / 現行移行元**：M1では記憶生成だけの追加Brain呼び出しを避けるため、公開発言から機械的に抜粋した`E001`簡易EpisodeをSessionごとに1つ作っていた。この方式は短期Milestoneとしては合理的だったが、長期Memoryでは240文字等のexcerptを原文正本にしてはならない。今後はRawをlosslessに保持し、Episode / chunk / summaryは派生として再生成可能にする。WhisperはPublic派生生成へ渡さない。

各Episodeは元のUIチャットSession IDを保持する。1つのUIチャットセッションから複数Episodeが作られてもよい。これによりUI履歴とAIの記憶を独立して操作できる。

- チャット履歴削除：`runtime\chat_sessions`側だけ削除し、World MemoryのRaw / Structured / Index対象は残す
- 世界の記憶から忘れさせる：そのUIチャットSession ID等に紐づくPublic Raw Durable Source、Structured Continuity、Derived Retrieval対象を削除・再構成し、同じUI履歴も削除する。派生Indexだけ消してRawを残す状態を「忘れた」と扱わない

Whisperの`Private Memory`はWorld Memoryとは別系統なので、これらのSession操作では削除しない。

要約生成に失敗した場合は、セッションの主要発言を機械的に抜き出した簡易Episodeで代替し、記憶を失わない。

### 3. Private Memory

Masterと特定ResidentのWhisperだけを保持する。

Private Memoryも年単位・数年単位で増える前提とする。Publicと同様にRaw Durable Source / Structured Continuity / Derived Retrievalを持てる構造にし、「量が少ないから常に全件または直近N件だけで足りる」という前提を置かない。

#### Whisper Private Channel / Provider Working Context

Whisperには公開Chatと同じ「新しいSession」を必須概念として持ち込まない。**Residentごとに1本のPrivate Channel**を長期的な会話Identityとする。

```text
Private Channel
  Master <-> Resident A   # 長期Identity。公開Chat Sessionに依存しない

Provider Working Context
  Provider native Session / Thread / Interaction
  # Transport Context。Provider resume / compactionを優先して継続し、失効時だけ再構成する
```

- 公開Chat Sessionを切り替えても、同じResidentとのWhisper Private Channelは継続する
- 各Whisper Raw Entryには発生時の公開Chat Session IDをprovenanceとして保持してよいが、それをWhisper会話Identityには使わない
- 通常TurnはProvider native Context + 前回以降の新規Whisper差分を使う
- Persona / Skills / 固定会話ルールは毎Turn再送せず、Conversation開始・Core再起動後の安全なrefresh・内容変更時・検知可能なProvider compaction後だけ再注入する
- Provider Working Contextが長大化した場合も、Nirai独自の一定Turnローテーションを先に作らず、Provider自身のnative compaction / resumeを優先する
- Codex等、compactionを通知できるProviderでは同じnative Conversation IDを維持し、Nirai側のcontext delivery cacheだけ無効化して次Turnで必要Contextをrefreshする
- Cursor等、現行公開Protocolでcompaction通知がないProviderでは推測でSessionを捨てず、resume/load失敗等の実Failure時だけWorking Contextを再構成する
- Working Context再構成時はStructured Private Memory + Private Retrieverの関連結果 + bounded recent rawを使い、5年分Raw Whisperを全投入しない
- Working Contextの再構成は長期Memoryの消失を意味しない。Private Channel IdentityとRaw / Structured MemoryはNirai側に残る

現行移行Sliceでは、Working Context再構成時のRaw Bootstrapを最大20件に抑え、通常Turnは前回marker以降の差分だけを渡す。最終的な件数・Token BudgetはMemory EvaluationとProvider別実測で決める。

#### Private Raw Durable Source

2026-09-06 Currentでは、Residentごとの`residents/<name>/private/private_memory.sqlite3`をPrivate Raw Durable Sourceとする。

- Master→Resident / Resident→MasterのWhisper全文をlossless保存
- 公開Chat Session IDはprovenanceとして保持するがPrivate Channel Identityにはしない
- stable Entry ID / sender / recipient / timestamp / request_idを保持
- SQLite WAL + busy_timeout
- Residentごとに物理DBを分離し、Private Scopeをquery filterだけへ依存させない
- `recent_whispers()`はSQL tail、`whispers_after()`はstable Entry marker以降だけをSQL取得し、通常Turnで全履歴Fileをparseしない
- Local FTS5を同じResident DB内にだけ作る

旧`whispers.jsonl`はLegacy import / compatibility mirrorとして残す。初回はsignatureを確認してSQLiteへidempotent importし、以後のnormal recent / delta / retrievalはJSONLをscanしない。

Whisper内容はWorld Memoryへ書かない。他ResidentのBrain入力にも渡さない。Presentation境界でも同じPrivate Scopeを維持し、`resident_whisper`本文はFocus中の個別Whisper UIだけへ表示し、World頭上吹き出し・TTS・公開会話Animationへ流さない。2026-09-07 Master判断により、Private WhisperのSemantic EmbeddingはGemini Free利用を許容する。ただしRaw正本はResident別SQLiteに保持し、Cloudへ送るのはEmbedding対象text/queryだけとする。

#### Private Retriever Current Slice

2026-09-07 Currentでは、Resident別SQLite Rawへ**Local FTS5 + Gemini Embedding 2**のabstention-oriented Retrieverを製品接続済み。

- Exact / lexicalな古いWhisperをrecent tailより外から取得できる
- Strong lexical / distinctive exactならGemini Query Embeddingを呼ばずLocalで返す
- Localだけで足りない場合、同Resident DBに保存済みの768次元`sqlite-vec`を`gemini-embedding-2`の`RETRIEVAL_QUERY`で検索する
- Current similarity floorは0.70。Gemini Product Golden 8 Caseで8/8・false recall 0を確認した
- current recent20に既に含まれるEntryはRetriever結果から除外する
- Gemini停止 / Quota枯渇 / Vector lag時はTemporal Queryを含めLocal FTSへfail-softする。Strong / distinctive exact、または`coverage >= 0.22`を満たす候補が1件だけなら返し、曖昧な複数候補は0件へ倒す
- Near-missは0件を正常系とする
- `private_memories`はWhisper Promptだけへ入り、Say / resident_chatへ渡さない
- Provider native Working Contextへ一度注入した同一Private Evidenceは、Contextが健全な間は再送しない
- Core restart / provider reset / detected compaction後は再注入可能
- Resident AのRetrieverはResident BのPrivate DBを開かない

Public / Privateは同じ`gemini-embedding-2`のrolling 24h quota authorityを共有し、Background最大400、Query最大500、合計最大900をNirai全体で超えない。Private用に別quotaを足し合わせない。Embedding Model / dimension変更時はPrivate DB内metadataとの差異を検出し、同一次元の別Modelへの変更でもPrivate `raw_vec`を派生Indexとして破棄してResident-local Rawからpending jobへ戻し再構築する。異なるEmbedding空間のVectorを同一Indexで比較しない。Current RuntimeはローカルBGE-M3 / Ollamaを使わず、SerinaのCPU Memory RuntimeとNirai Memory Runtimeを分離する。

#### Structured Continuity / context.md

Whisperが長期間空いても自然に会話を再開するため、重要事実・関係・約束・未解決事項・前回の継続点等をStructured Continuityへ昇格できる設計余地を残す。ただし**2026-09-07 Current Product Goldenでは、Structured Fact OverlayなしのRaw + FTS + Gemini Embedding 2 + 最小Temporal ruleだけでPrivate 8/8 / false recall 0**を確認したため、Structured層を現在の必須実装とはしない。Golden拡張でRaw Hybridが実際に落ちるCaseが確認された時だけChallengerからProductへ昇格する。

`context.md`は人間可読Viewまたは小さなPrompt用派生Contextとして利用してよいが、**Structured Continuityの唯一の正本を数KBの1枚へ潰さない**。

推奨構成：

```markdown
# Private Context

## 覚えておくこと
- 長期的に必要な事実

## 未解決の話
- 次回続ける可能性がある話題

## 前回のWhisper
- 日付
- 短い要約
```

Structured Continuityを採用する場合はWhisperの一区切り時にBackground更新し、既存Structured State + 今回の新しいRaw Entryを基準とし、古い重要事項を単なる件数超過で消さない。追加LLM call、追加常駐Model、Private cloud送信を「将来必要かもしれない」だけで導入しない。

重要なのは「直近3日」「直近12発言」等だけで長期継続を決めないこと。CurrentではRaw Hybrid Retriever + 必要なrecent rawで長期継続し、将来Structured層を採用した場合も同じRaw provenanceからContextを再構成する。

## Whisper Working Context再構成時に渡すContext

WhisperのProvider Working Contextを新規開始・再構成する時は次を渡す。

1. persona.md
2. 必要なNirai Skills / 固定Private会話ルール
3. World Memoryから現在話題に関連する公開記憶
4. 本人のStructured Continuity Memory
5. Private Retrieverで取得した関連過去
6. boundedな直近Whisper Raw

同一Provider Working Contextの通常Turnではnative continuationを利用し、Persona / Skills / 過去Rawを毎Turn固定件数で再送しない。前回以降の新規Whisper差分と、そのTurnで新たに必要になったMemoryだけを中心に追加する。同一Memory Evidenceを既にそのWorking Contextへ注入済みなら、内容が変わらない限り再注入しない。Core再起動・Provider reset・検知されたcompaction後は再注入可能にする。

Private MemoryはSay、resident_chat、生活ティック、他Residentとの会話には渡さない。これによりWhisperで得た秘密を公開発話へ漏らす経路をCore側で遮断する。

## World MemoryのRetriever / RAG

World Memoryは長期利用で増え続けるため、全件を毎回Brainへ渡さない。

M3でRetrieverを導入し、現在の発話・話題から関連する過去Episodeを取り出してContextへ追加する。

### 実装原則

- RetrieverはWorld Memoryに1つだけ持つ。Resident別Indexは作らない
- 検索Indexは派生データとし、正本から再生成可能にする
- 通常会話・Raw保存・Exact/Local RecallをCloud Embeddingの可用性や有料課金へ依存させない。Cloud Semantic Retrievalは交換可能な派生Adapterとして利用できる
- AITuberKitのRAG設計はベンチマークにするが、特定Embedding ProviderをNirai Identity / Raw正本のInvariantにはしない
- 独自Vector DBを作らない

### Retrieval方式

M3初期RetrieverはSQLite FTS5 / BM25相当のEpisode検索から開始した。2026-09-06のReference-First再調査とGolden Ablationを経て、Public World MemoryのCurrent Product Sliceは**lossless Raw Evidence + Structured Atomic/Current Fact + Local FTS5 + Gemini Embedding 2 Semantic + abstention-oriented gate**へ更新した。

CurrentはEvaluationを通った最小構成であり永久Invariantではない。Golden拡張・Scale・Provider条件が変化した場合は、`../archive/research-and-old-design/Nirai_MemoryEvaluation設計_2026-09-06.md`の比較思想を参考に、その時点の最新候補を同一条件Benchmarkで再比較する。Public Raw / FTSとPrivate Raw / FTSは250k Syntheticでsteady-state確認済みで、Private SemanticもGemini Embedding 2 Product Golden 8/8まで製品確認済み。Public / Private Cloud Vector Indexの年単位maintenanceとPrivate Structured Challengerは未完了。実測Evidenceは`../evidence/reports/Nirai_MemoryScale_検証結果_2026-09-06.md`を参照する。

候補Signal：

- Raw Durable SourceのFTS5 / BM25
- Atomic / Fact / Episode等Structured MemoryのFTS5 / BM25
- 必要な場合だけSemantic Vector Retrieval
- Temporal filter
- Entity / Participant filter
- Resident experience / pre-birth history filter
- importance / recency等のranking signal

複数Retrieverを使う場合、RRF等の単純で説明可能なFusionを比較起点にするが、Fusionへ候補を無条件投入しない。2026-09-06の初回Evaluationではnaive FTS + BGE RRFがBGE-M3単独よりNear-miss false recallを増やした。Current Product Sliceは次の順でRecallする。

1. Local Raw FTS5で候補生成
2. Current / Historical correctionは、Raw候補が属するStructured Fact familyに対してだけ解決する。Structured側だけで話題を推測して発火しない
3. distinctive exact anchorまたは十分強いLexical coverageならLocalだけで返し、Gemini Query Embeddingを消費しない
4. Local証拠が弱いSemantic queryで、保存済みVectorとrolling 24h Budgetに余裕がある時だけ`gemini-embedding-2`の`RETRIEVAL_QUERY`を使う
5. Semantic similarity floor 0.70未満は棄却し、必要なら0件を正常結果とする
6. Lexical / Semantic両方を使う場合も弱いLexical候補を無条件に復活させない

Gemini Embedding 2は768次元でDocument / Query task typeを分離し、Raw Document vectorはBackgroundで一度生成して`sqlite-vec`へ保存する。Embedding requestは`autoTruncate=false`とし、Provider上限超過時にMemory末尾等を黙って切ったVectorを成功扱いにしない。長すぎる入力等でEmbeddingできない場合もRaw正本は維持し、Semantic失敗としてLocal fallbackへ進む。2026-09-06実機GoldenではGemini Semantic 10/11、Temporal Fact + Gated Hybrid 11/11 / false recall 0。BGE-M3も比較Baselineとして同等の最終11/11を確認したが、Nirai Worldで常駐させる必要はなく、SerinaとのCPU競合を避けるためCurrent Runtimeには採用しない。Embedding Provider自体はAdapterで交換可能とする。

2026-09-06のMaster実環境Free Tier表示では、Gemini 3.5 Flash Liteは500 RPD、Gemini Embedding 2は1,000 RPD。Google公式上、RPDはPacific Time午前0時にresetされる。NiraiはOS / UTC / JSTとProvider日付境界のズレで上限を跨がないよう、より保守的な**rolling 24h guard**を使い、**Public + Private合算で**Background最大400、Query Embedding最大500、Embedding 2合計最大900として100件以上の余白を残す。Gemini Embedding 1にも別1,000 RPD枠があるが、**異なるEmbedding ModelのVector空間を同じIndexで混ぜない**ため、Embedding 1 + 2を同じIndexの2,000件枠として扱わない。QuotaはProvider Constraintであり、表示変更時は設定値を再評価する。

Cloud Semanticが停止・Quota枯渇・Index lagの場合はRaw FTS5 / Structuredへ縮退し、会話とRaw保存は継続する。**Gemini Semanticを有効化している状態でLocal fallbackが実際に発生した時は、対象World Chat Sessionへ`system` EntryとしてFallback理由を永続化・表示する。** 専用UIは作らず、例として`Semantic Memory fallback: World Memory は Local FTS で想起しました`のように観測可能にする。このSystem EntryはResidentへ渡す`public_history`から除外し、品質縮退の通知文自体を会話Contextへ混ぜない。Semantic自体を設定で無効化している場合はFallback扱いではないため通知しない。ただしSemantic確認不能時に弱いLexical候補を無条件で返さない。Strong / distinctive exactはLocalで返し、それ以外の非常用fallbackは`coverage >= 0.22`を満たす候補が1件だけの時に限定し、複数候補なら0件へ倒す。旧Episode Retrieverは**Raw行が存在しないLegacy Sessionだけ**に限定して移行fallbackとし、新Retrieverが0件と判断したSessionの記憶を旧Episodeから復活させない。Cross-Encoder reranker、Graph DB、追加常駐ModelはBenchmarkで明確な改善が確認できる場合だけ採る。

- 独自Vector DBや独自ANN Indexを最初から作らない
- Public / PrivateのIndexとAccess Boundaryを混ぜない
- Raw Durable Sourceを検索方式へ従属させない
- Structured抽出が失敗してもRaw Evidenceから検索可能にする
- Embedding / Vector Indexが停止・遅延しても、FTS / Structured Continuity等へ縮退して通常会話を継続できるようにする
- Retrievalのための重いEmbedding再生成をMasterの通常会話のBlocking条件にしない

### 取得結果

Retrieverは、Queryに必要な最小限のRaw Evidence / Atomic / Fact / Episode / Durable Continuity候補をContext Builderへ返す。どの層を何件返すかは固定せず、EvaluationでContext量と正確性を比較する。

- 小さなTop-K / Token Budgetから開始し、必要性が証明された時だけ広げる
- 同一EvidenceのRaw / Atomic / Episodeが無意味に重複してPromptへ入らないようContext Builderで整理する
- 現在セッションと同内容の重複は除く
- 関連度が低い場合は0件でもよい
- Retrieved Structured Memoryには可能な範囲でprovenance参照を付ける
- Retrieverが失敗しても会話自体は継続する

## 記憶更新コスト

記憶のためだけにBrain呼び出しを乱発しない。

- Raw Durable Sourceへの追記はlosslessかつ増分で行い、1発言ごとに巨大File全体をread + rewriteしない
- Rawは会話時に即時保存する。Public CurrentではStructured抽出とDocument Embeddingを`structured_jobs`からBackground増分処理し、通常会話を待たせない。失敗時はRawを残したまま指数Backoffで再試行する
- 旧Public Episodeはcompatibility Viewに降格し、Raw stable IDが新規の時だけappendする。既存Episode全文をread + rewriteしない
- 公開会話：一区切りごとに必要なStructured Continuity / Episodeを増分更新し、全履歴を毎回要約し直さない
- Whisper：Raw追加時にPrivate `embedding_jobs`へpendingを増分追加し、Gemini Embedding 2の`RETRIEVAL_DOCUMENT`をBackground処理する。Public / Privateで同じrolling 24h quota guardを共有する。Structured Continuity生成は未完了で、方式確定後も同様にhot pathから分離する
- Retrieval Indexは変更分を増分反映できる方式を優先し、必要なら再生成可能にする
- 短いセッションでStructured要約不要と判断できる場合はRaw記録だけでもよい
- 日次でResidentごとの同一日記を生成しない
- 直近N件取得のために全履歴Fileを毎回parseしない

## Brain交換

BrainをClaude CodeからCodex等へ交換しても、以下はそのまま残る。

- persona.md
- World Memory
- Private Memory
- Avatar設定
- TTS設定

CLI固有Memoryは補助であり、引っ越しの必須データに含めない。

## Residentの追加・削除

### Resident順

`config.toml`の`[residents].enabled`は有効Residentの集合だけでなく順序も正本とする。右Resident Sidebarの表示順と、複数Residentの初期Presentation配置順に同じ順序を使う。

- 2体時：上から左・右
- 3体時：上から左・中央・右
- 4体以上：画面安全幅の等間隔（(i+1)/(n+1)比率）・同一Z。2体・3体の専用配置は変更しない
- 上記は初期配置ルールであり、Resident作成数の上限ではない。Resident数には固定上限を設けない
- 並べ替えはIdentity・persona・World Memory・Private Memory・Brain・Avatar・VOICEを変更しない
- 並べ替えのためにResidentを削除・再作成しない
- 過去Chat Sessionに削除済みResidentの発言が残っていても、現在有効なResident一覧とは別情報として扱う

### 新規作成

設定UIからの新規作成でMasterが入力するのは**名前・AI Provider・Role**を基本入力とし、**Model**を任意とする。Roleの既定値は`resident`。Codexではさらに**Reasoning**を任意指定できる。`Holo Addon`選択時はModel / Reasoning / VOICE / Persona Promptを表示しない（Holoに意味がないため）。名前は空文字、既存Residentとの重複、Windowsフォルダ名として不正な文字を拒否する。AIは`brain_provider_list`で利用可能なProviderから必須選択し、Model候補は同ProviderのCatalogを使う。Codex Reasoning候補は選択ModelのCatalog Metadataを使う。Model / Reasoning空欄はProvider既定を意味する。実行系Roleを選択した場合はCoreがAgent Work Capabilityを再検証し、成立しない組み合わせは保存しない。

1. `residents\<名前>\`を作る
2. 名前だけ入った`persona.md`雛形を作る
3. 選択したRole / Brain Providerと、指定されていれば`brain_model` / Codexの`brain_reasoning_effort`を`config.toml`へ保存する。Commanderへ設定する場合は既存CommanderのRole変更も同一操作として整合させる
4. VRM / VOICEは後から設定する
5. `Lapan`を再作成する場合だけ、`avatars\lapan\lapan.vrm`が存在すれば初期Avatarを再紐付けする。他Residentへ名前由来の自動Avatar推測は行わない

VRM未設定ならWorldへ身体をSpawnしない。既存データにBrain未設定Residentが残っている場合は読み込み可能とし、設定Sidebarの`AI変更`で復旧できる。

### Avatar選択

設定UIのAvatar選択はWindows File Pickerを使い、初期表示を`D:\Products\Nirai\avatars\`とする。選択可能なのは`.vrm`のみとし、選択したVRMへの`avatars\\`相対参照をそのまま保存する。

Character削除時に`avatars\`配下のVRM本体は削除しない。Avatar規格と読込方針は09を正とする。

### 削除

キャラクター削除は確認Dialogで`Delete`完全一致を要求する。

削除する：

- Residentフォルダ
- persona / config
- Private Memory / Whisper履歴
- Brain割当
- VRMとの紐付け
- Voice設定
- Resident固有状態

削除しない：

- `avatars\`配下のVRM本体
- World Memory
- 外部Brain CLI / Runtime
- 外部Voice Provider本体

World Memoryには、そのResidentが過去に存在した共有世界の歴史を残す。

新規Residentは入居前のWorld Memoryも世界史・共有知識として検索・参照できる。ただし各Memory Entry / Eventの発生時刻とResidentの入居時刻を比較し、入居前の出来事を「自分もその場にいた」「自分が体験した」と扱わせない。Structured Contextへ渡す際も、必要に応じて`experienced_by_self=false`相当の意味を保持する。世界の共有史とResident個人の経験履歴を分離する。

## Memoryの訂正とForget

### Chat履歴削除とMemory Forgetの分離

通常のChat履歴削除は、長期Memoryを忘れる操作ではない。Sidebar / Chat JSONLを削除しても、そのEntryがMemory Outboxで未同期なら、再同期に必要なindexed Chat SourceだけをUI非公開の派生復旧Sourceとして一時保持する。

- 未同期Sourceは他SessionのOutbox replayをBlockingしない
- Memoryへの冪等反映が成功した時点で、削除済みChatに属する一時Sourceも削除する
- 同期済みChatの通常削除では長期World Memoryを残す
- MasterがMemory自体の削除を望む場合は、通常履歴削除ではなく下記の明示Forgetを使う
- Forget tombstoneと通常削除用の一時Replay Sourceを混同しない。Forget済みEntryをOutboxから復活させない

これにより「履歴を消した」と「記憶を忘れさせた」を別契約として維持し、Memory同期障害中の履歴削除でも他の会話のRecoveryを巻き込まない。

### 訂正

Masterが「前に言ったXは違う。今はY」のように訂正した場合、過去Raw Durable Sourceを書き換えない。

- 過去発言・Eventは、その時点で実際に存在した履歴として保持する
- Structured Continuity側に旧情報が`superseded / corrected`であることと、現行値Yを記録する
- Retrieval時は現行情報を優先しつつ、必要なら「以前はXと話していたが後にYへ訂正された」と歴史として参照できる
- Derived IndexはStructured Stateの更新に合わせて更新・再生成する
- 単なる訂正をForgetとして扱わない
- 自動SupersedeをSemantic similarityだけで決めない。少なくとも同一Privacy Scope、Entity / Subject、compatibleなPredicate / Category、provenance、時系列、明示訂正または十分強い矛盾・更新Signalを組み合わせる
- 更新関係が不確実なら旧Factを消さず、`hypothesis`または並列Factとして保持して後続Evidenceを待つ

### 明示Forget

Masterが特定の記憶を「忘れて」と明示した場合は、対象を本当に長期Memoryから除去する。

- 対象Raw Durable Source
- 対象から生成されたStructured Continuity
- Episode / Summary / Chunk等の派生物
- Derived Retrieval Index上の対象

を同一Forget operationとして整合して削除・再構成する。

Forget対象が他のEntryと同一Episode / Summary / Chunkへ混在している場合は、対象部分だけを除いた派生データを再生成する。Indexだけを非表示にしてRawを残す、Rawだけ消してSummaryへ情報を残す等の半端な削除を成功扱いにしない。

### その他

- Brain交換：`config.toml`の`brain`と、そのProviderで選択した`brain_model`を変更する。Codexでは`brain_reasoning_effort`も変更する。Model / Reasoning未指定なら該当Keyを削除してProvider既定へ戻す。Provider変更時は旧ProviderのReasoningを残さない
- 身体交換：config.tomlの`avatar`だけ変更する
- World MemoryはResident追加前から存在する共有世界の過去として参照可能。ただし入居前の出来事は世界史・共有知識であり、そのResident自身の経験ではない
