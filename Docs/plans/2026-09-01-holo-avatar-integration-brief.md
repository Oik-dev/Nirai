# Holo Avatar統合 Decision Brief

- 状態: checking（自動検証完了。Windows実機の見た目QAのみMaster待ち）
- 最終更新: 2026-09-01
- 決定者: Master（2026-09-01依頼文で完成像・境界・完了条件を明示済み。依頼文自体を最終承認として扱う）

## 目的と完成像

HoloをNirai Worldの1キャラクターとして統合する。ユーザーから見ればHoloは他のResidentと同じようにWorldに存在し、選択（Focus）するとHolo Whisper（ChatGPT Web）へ自然につながる。頭脳だけが通常Brain Driverではなく既存Holo Addonである。

## Fact

- Holo Gate 0とHolo Addon / Holo Whisper製品化は完了済み（`Docs/plans/2026-08-31-holo-addon-productization-brief.md`）。
- Resident数の3人固定上限は撤廃済み（Core側）。World側の初期配置は2人・3人専用のみ実装済みで、4人目は原点付近に出て重なり得る。
- Whisper Surfaceの入口は現在Debugメニューのみ。World FocusとHolo Surfaceは未配線。
- `holo_say`はCore Sessionへ`sender="Holo"`固定で保存され、World側の吹き出し演出は未配線。
- 旧正本`12_HoloAddonとChatGPTDive.md`は「Holoは通常Residentではない」と記述（今回の統合で改訂対象）。

## Decision

### HA-001 Holoの内部表現
- **内容:** Holoは通常Residentとして`residents/<name>/`で作成・管理する。頭脳は新しいbrain kind `holo-addon`とし、Brain Driverを偽装せず、既存Holo Addonへ接続する特殊Brain Kindとして扱う。Identity / Avatar / 配置 / 並び順 / 削除は通常Resident基盤を再利用する。
- **決定者:** Master（依頼文）
- **状態:** confirmed

### HA-002 holo-addon Residentは1人まで
- **内容:** Holo AddonはChatGPT Web / Current Dive / 保存状態が単一のため、brain=holo-addonのResidentは同時に1人だけ作成できる。2人目はCoreが明確なエラーで拒否する。
- **決定者:** AI（Addonがsingletonである既存事実からの帰結）
- **状態:** confirmed

### HA-003 Brain Driver非接続の境界
- **内容:** holo-addon Residentは `master_say` の応答ループ・`master_whisper`のBrain呼び出し・`resident_chat`参加のすべてから除外する。`_get_brain_driver("holo-addon")`は防御的にエラーとする。HoloへのWhisperはWorld側でHolo Whisper Surfaceへ誘導し、Coreは受け取っても保存せずWARNで案内する（ChatGPT Conversationが正本。Nirai側へ私的履歴を複製しない）。
- **決定者:** Master（会話境界の指示）+ AI（実装点）
- **状態:** confirmed

### HA-004 Focus導線
- **内容:** World上のHolo（VRMあり）をクリックFocusすると、Holo Whisper Surfaceが自動で開く。Focusは維持し、カメラはHoloへ寄る（半透明Glass越しにHolo本人が見える）。Surfaceを閉じるとFocusを解除してWorldへ戻る。Surface表示中は通常チャット（ChatBar / ChatHistory）を出さない。Debugメニューの入口は診断用に残す。VRM未設定でもResident設定のHoloカードから開ける。
- **決定者:** AI（既存デザイン言語に沿うUX設計）
- **状態:** confirmed

### HA-005 4人以上の初期配置
- **内容:** 2人・3人の承認済み専用配置は変更しない。4人以上は画面安全幅を等間隔（(i+1)/(n+1)比率、同一Z）に並べる汎用初期配置を追加する。Separation・screen-safe・Cameraの既存設計と整合させ、CameraのGroup距離は既存の自動フィットに任せる。
- **決定者:** AI（Master「必要であれば自然な方式を設計」）
- **状態:** confirmed

### HA-006 holo_sayの発言者
- **内容:** `holo_world_say`の発言者名は、holo-addon Residentが存在すればそのResident名、なければ従来どおり`Holo`とする。World側は`holo_say`受信時にそのResidentの吹き出しを表示する（既存Resident発言演出を再利用）。ChatGPT内部状態（thinking等）の推測表示は追加しない。
- **決定者:** AI（「Holo Avatarが発言者として演出される」既存要件の充足）
- **状態:** confirmed

### HA-007 作成UI
- **内容:** Resident新規作成 / AI変更のAI選択肢に`Holo Addon`を追加する（Coreの`brain_provider_list`へ追加）。holo-addon選択時はModel / Reasoning / VOICE等の意味のない設定を表示しない。Holo専用の作成画面は作らない。
- **決定者:** Master（依頼文）
- **状態:** confirmed

### HA-008 Whisper Surfaceの濃さ再調整
- **内容:** Masterの実機確認「まだ少し濃い・既存UIとの境目がはっきり分かる」を受け、非アクティブ時の濃さを既存Glass UIへ近づける（押下時に濃くなる仕様は維持）。
- **決定者:** Master
- **状態:** confirmed

## Scope外（Master指定）

生活Tick、自律生活、World Observation本格導入、Retriever / RAG、Agent Runtime、Task実行、Approval UI、Holo Voice / TTS新規実装、新認証方式、Remote Holo、Secure MCP Tunnel、ChatGPT DOM強依存、Conversation全文複製、M3着手。

## 維持するもの（再設計禁止）

ChatGPT Web Host、persistent login、Current Dive、保存済みConversation復元、Dive、Holo Whisper、Local Holo Client、Core起動ごとのLocal Secret、localhost接続、Snapshot、bounded Event Queue / wait、holo_say、状態通知、Skin applied / fallback、fail-open、Permission deny-by-default、Navigation / Popup制限、Approval / Decision非搭載、Holo異常の非波及。

## 成功条件

Master依頼文の完了条件一覧（作成・誤接続なし・Avatar・4人表示・Focus導線・復元・Dive・Skin・非波及・非漏洩・回帰なし・再起動復元・レスポンシブ・全テスト・typecheck・build・diff --check）。

## Risk

- ChatGPT側class名依存の背景透過は外部更新で再発しうる（既知の外部境界、fail-openで機能は維持）。
- 4人以上配置は新規経路のため、実機での見た目確認はMaster QAに依存する。

## 実装結果（2026-09-01）

### Core

- `core/residents/service.py`: brain kind `holo-addon`を追加（定数`HOLO_ADDON_BRAIN`）。作成・AI変更で選択可、Modelは常にNone、Reasoningは従来どおりCodex以外拒否、holo-addonは同時1人だけ（`_assert_holo_addon_slot_free`、自分自身の再保存は許可）。
- `core/server.py`: `brain_provider_list`へ`Holo Addon`（available固定、models空、custom model不可）を追加。`_get_brain_driver("holo-addon")`は防御的にBrainError。`master_say`応答ループ・`resident_chat`参加からholo-addonを除外。Holoへの`master_whisper`は保存せずWARN通知で案内。`holo_world_say`の発言者名はholo-addon Resident名（不在時`Holo`）。
- `core/sessions/manager.py`: `append_holo_say`にsender引数を追加。

### World

- `ResidentSidebar.tsx`: `brainLabel`へHolo Addon追加。holo-addon選択時はModel欄非表示（「頭脳はChatGPT（Holo Whisper）です」表示）、カードのModel / VOICE / Prompt項目非表示、`Holo Whisperを開く`ボタン追加（VRM未設定でも使える）。並び順注記を人数非依存の文言へ更新。
- `App.tsx`: Holo Resident Focusで`HoloWhisperSurface`を自動表示（Focusは維持、Glass越しにHoloが見える）。Surfaceを閉じるとFocus解除でWorldへ復帰。Surface表示中・Holo Focus中はchat dock非表示。`@Holo` Whisperは送信せずSurfaceへ誘導（INFO通知、入力テキストは保持）。`holo_say`受信でHolo Residentの吹き出しを表示（faceToMasterはしない）。
- `chatHistoryView.ts`: `holo_say`のラベルを実際の発言者名に変更。
- `worldConfig.ts` / `SceneRuntime.ts`: 4人以上の初期配置`createManyResidentInitialSlots`（等間隔・同一Z）を追加し、初期レイアウトを2人以上で有効化。自然Separationはスロット間隔を超えない値へ制限。2人・3人専用配置は不変。
- Glass再調整（HA-008）: Surfaceの非アクティブ透過を0.46→0.40、web slot背景10%→4%、Skinのbodyトーン14/18%→8/10%。押下時0.96へ濃くなる仕様は維持。

### CHECK結果

- Core pytest 121 passed（新規: holo-addon singleton・永続化復元・master_say除外・whisper境界・provider list・resident_chat拒否・holo_say発言者名）
- World Vitest 33 files / 164 tests passed（新規: 4人以上スロット・holo_sayラベル・isHoloAddonBrain）
- TypeScript typecheck成功、Production Build成功、`git diff --check`成功（LF/CRLF警告のみ）
- 未確認範囲: Windows実機での見た目（4人配置・Focus→Surfaceの流れ・Glassの濃さ・Skin表示）はMasterの次回起動時に確認する。既存Niraiが起動中のため二重起動を避け、AIからのアプリ起動QAは行っていない。

### 残存backup・作業残物

なし（バックアップ不要の追記型変更のみ。一時ファイルなし）。
