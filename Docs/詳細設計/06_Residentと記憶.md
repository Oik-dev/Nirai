# Nirai 詳細設計 06：Resident定義と記憶

正本は [Nirai_基本設計.md](../Nirai_基本設計.md)。Avatar規格の正は [09_3DビジュアルとAvatarパイプライン.md](09_3DビジュアルとAvatarパイプライン.md)。

## 概要

本章は、Resident 1人分の人格・設定と、Nirai全体の記憶構造を定義する。

記憶はResidentごとに同じ公開情報を複製しない。

- Say、公開Resident会話、世界イベントは**World Memory**として1つだけ保存する
- Whisperは宛先Residentだけの**Private Memory**として保存する
- 現在進行中の会話履歴は一時Contextとして扱う
- 各CLI自身のMemoryやセッション履歴はNiraiの記憶の正本にしない

## フォルダ構造

```text
residents\<住人名>\
  persona.md
  config.toml
  private\
    context.md        # Whisperの長期継続用。小さく保つ
    whispers.jsonl    # Whisper全文の正本

world_memory\
  events\
    YYYY-MM-DD.jsonl      # チャット以外のWorld Event
  episodes\
    <session-id>-E001.md  # 公開会話Episode。1セッション複数可
  index\              # Retriever用派生Index。削除・再生成可能

avatars\                  # Nirai管理Avatar Root。サブフォルダ利用可
  <任意>.vrm
  （必要な場合のみ同フォルダにavatar.toml）
```

`world_memory\index\`は記憶の正本ではない。壊れたら`events`と`episodes`から作り直す。

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
| brain | string | - | Brain Provider名。既存データでは未設定を読み込み可能だが、新規作成UIでは必須選択。特殊値`holo-addon`はHolo Addon（ChatGPT Web）を頭脳にし、通常Brain Driverへ接続しない。`holo-addon`は同時に1人だけ（詳細は`12_HoloAddonとChatGPTDive.md`） |
| brain_model | string | - | Resident固有のModel ID。未設定時はProvider既定Modelを使う。Provider変更時に旧ProviderのModel IDを流用しない |
| brain_reasoning_effort | string | - | Codex専用。Resident固有の推論強度。未設定時はCodex既存Configを継承する。`low / medium / high / xhigh / ultra / max`のうち、選択Modelが対応する値だけUI候補にする |
| avatar | string | - | `avatars\\`からのVRM相対パス。未設定可 |
| tick_interval_min | int | 30 | 生活ティック間隔 |
| tick_budget | int | 頭脳別既定 | 生活ティック1日予算 |
| spawn_location | string | "center" | 初期Location |
| tts.enabled | bool | true | このResidentがTTSを使うか |
| tts.provider | string | "voicevox" | TTS Provider |
| tts.speaker_uuid | string | - | VOICEVOX Speaker UUID。表示上のSpeaker識別用 |
| tts.style_id | int | - | VOICEVOX Style ID。`/audio_query` / `/synthesis`の`speaker`引数へ渡す値 |
| tts.speed | number | 1.0 | `AudioQuery.speedScale` |
| tts.pitch | number | 0.0 | `AudioQuery.pitchScale` |
| tts.intonation | number | 1.0 | `AudioQuery.intonationScale` |

Brain Provider / Brain Model / Codex Reasoning、Avatar、TTS設定は互いに独立して差し替えられる。Resident新規作成時はBrain Providerを必須選択し、ModelはProvider既定でも任意指定でもよい。CodexだけReasoningも任意指定でき、未指定ではProvider既定を継承する。Avatar / TTSは未設定でもよい。既存ResidentのProvider / Model / Reasoningは設定Sidebarの`AI変更`で差し替えられる。

VOICEVOXのSpeaker名・Style名はEngineから取得する表示情報であり、正本として保存しない。Engine APIの`speaker`引数には`style_id`を使用する。

## Avatar定義

AvatarのRuntime標準形式・設定UI入力ともにVRMとする。Residentへ保存する`avatar`は`avatars\\`配下のVRM相対Pathだけとし、UnityPackage / FBX等の自動変換は行わない。

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

公開チャットの全文は`runtime\chat_sessions\<session-id>.jsonl`をUI履歴の正本とし、World Memory側へ全文を二重保存しない。

#### episodes

公開チャットの一区切りごとに、**Sayと公開Resident発話だけ**を短いEpisodeへ要約して保存する。同じUIチャットセッションを後から再開した場合は、新しいEpisodeを追加してよい。

WhisperはEpisode生成入力から必ず除外する。UI上でSayとWhisperが同じチャットセッションに混在していても、Private内容をWorld Memoryへ混ぜない。

Residentごとに同じ要約を作らない。

M1では記憶生成だけの追加Brain呼び出しを避けるため、公開発言から機械的に抜粋した`E001`簡易EpisodeをSessionごとに1つ作り、同一Entryは二重記録しない。Whisperはこの入力へ渡さない。M3でRetrieverを導入する際に、実測上必要なら会話の一区切り単位の要約Episodeへ拡張する。

各Episodeは元のUIチャットSession IDを保持する。1つのUIチャットセッションから複数Episodeが作られてもよい。これによりUI履歴とAIの記憶を独立して操作できる。

- チャット履歴削除：`runtime\chat_sessions`側だけ削除し、Episodeは残す
- 世界の記憶から忘れさせる：そのUIチャットSession IDに紐づく公開Episodeを全て削除・Retriever対象外にし、同じUI履歴も削除する

Whisperの`Private Memory`はWorld Memoryとは別系統なので、これらのSession操作では削除しない。

要約生成に失敗した場合は、セッションの主要発言を機械的に抜き出した簡易Episodeで代替し、記憶を失わない。

### 3. Private Memory

Masterと特定ResidentのWhisperだけを保持する。

Private Memoryは量が少ない前提なので、初期実装ではVector DBやResident別RAGを持たない。

#### whispers.jsonl

Whisper全文を時系列で永久追記する正本。

- Master→Resident
- Resident→Master
- セッションID
- 時刻

を保持する。

Whisper内容はWorld Memoryへ書かない。他ResidentのBrain入力にも渡さない。

#### context.md

Whisperが長期間空いても自然に会話を再開するための小さなローリングContext。

内容は最大数KB程度に保つ。

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

Whisperセッション終了時に、既存`context.md`＋今回のWhisperから更新する。

重要なのは「直近3日」など時間で切らないこと。前回Whisperが1週間前でも3か月前でも、`context.md`と前回のWhisper履歴を次回へ渡す。

## Whisper開始時に渡すContext

Whisper開始時は次を渡す。

1. persona.md
2. World Memoryから現在話題に関連する公開記憶
3. 本人の`private/context.md`
4. 直近のWhisper履歴
5. 現在のWhisperセッション履歴

直近Whisper履歴は「日数」ではなく「件数/セッション数」で切る。初期値は直近20発言または直近1セッション程度とし、実測で調整する。

Private MemoryはSay、resident_chat、生活ティック、他Residentとの会話には渡さない。これによりWhisperで得た秘密を公開発話へ漏らす経路をCore側で遮断する。

## World MemoryのRetriever / RAG

World Memoryは長期利用で増え続けるため、全件を毎回Brainへ渡さない。

M3でRetrieverを導入し、現在の発話・話題から関連する過去Episodeを取り出してContextへ追加する。

### 実装原則

- RetrieverはWorld Memoryに1つだけ持つ。Resident別Indexは作らない
- 検索Indexは派生データとし、正本から再生成可能にする
- 従量課金Embedding APIへ依存しない
- AITuberKitのRAG設計はベンチマークにするが、OpenAI Embedding依存をそのまま採用しない
- 独自Vector DBを作らない

### 初期実装

まず既存の軽量全文検索/BM25相当を利用して`episodes`を検索する。SQLite FTS5等、ローカルで完結する既存手段を優先する。

これで想起品質が受入基準を満たすなら、そのまま採用する。意味検索が必要と実測で判明した場合のみ、ローカルEmbeddingを追加する。

つまり、最初からEmbeddingモデル・Vector DB・再ランキングまで積まない。

### 取得結果

Retrieverが返すのは上位数件のEpisodeと必要な原文参照だけとする。

- 既定Top Kは3〜5程度から開始
- 現在セッションと同内容の重複は除く
- 関連度が低い場合は0件でもよい
- Retrieverが失敗しても会話自体は継続する

## 記憶更新コスト

記憶のためだけにBrain呼び出しを乱発しない。

- 公開会話：会話の一区切りごとにEpisode要約を1回。長いUIチャットセッションを再開しても、既存Episodeを毎回作り直さない
- Whisper：Whisperの一区切りごとに`context.md`更新を1回
- 短いセッションで要約不要と判断できる場合は機械的記録だけでもよい
- 日次でResidentごとの同一日記を生成しない

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

設定UIからの新規作成でMasterが入力するのは**名前とAI Provider**を必須、**Model**を任意とする。Codexではさらに**Reasoning**を任意指定できる。`Holo Addon`選択時はModel / Reasoning / VOICE / Persona Promptを表示しない（Holoに意味がないため）。名前は空文字、既存Residentとの重複、Windowsフォルダ名として不正な文字を拒否する。AIは`brain_provider_list`で利用可能なProviderから必須選択し、Model候補は同ProviderのCatalogを使う。Codex Reasoning候補は選択ModelのCatalog Metadataを使う。Model / Reasoning空欄はProvider既定を意味する。

1. `residents\<名前>\`を作る
2. 名前だけ入った`persona.md`雛形を作る
3. 選択したBrain Providerと、指定されていれば`brain_model` / Codexの`brain_reasoning_effort`を`config.toml`へ保存する
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
- VOICE設定
- Resident固有状態

削除しない：

- `avatars\`配下のVRM本体
- World Memory
- 外部Brain CLI / Runtime
- VOICEVOX本体

World Memoryには、そのResidentが過去に存在した共有世界の歴史を残す。

### その他

- Brain交換：`config.toml`の`brain`と、そのProviderで選択した`brain_model`を変更する。Codexでは`brain_reasoning_effort`も変更する。Model / Reasoning未指定なら該当Keyを削除してProvider既定へ戻す。Provider変更時は旧ProviderのReasoningを残さない
- 身体交換：config.tomlの`avatar`だけ変更する
- World MemoryはResident追加前から存在する共有世界の過去として扱う。新規Residentへ過去をどこまで知識として与えるかという世界観上の制御が必要になった場合だけ、後から可視範囲設定を追加する
