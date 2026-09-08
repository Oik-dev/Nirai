# Conversation Continuity 検証結果 2026-09-06

## 判定

**PARTIAL SAFE**

通常ResidentのCursor / Codex Conversation ContinuityとToken再送削減は自動回帰でSAFE。Whisper長期Conversationを追加Reference-Firstで再確認し、Nirai独自の定期Sessionローテーションではなく、永続Private Channel + Provider native Working Context + provider-native compaction / failure rebuildへ整理した。Cursorは実Provider 2-turn SmokeでもSAFE。Gemini Interactions continuationは実装・自動回帰済みだがLive 2-turn Smoke未実施。Codex Live 2-turn SmokeはProvider利用枠上限により保留。

## 成立したContract

- 公開Say / resident_chatは`Chat Session + Resident`単位でProvider native Contextを持つ。
- 新しい公開Chat Sessionは新しいnative Contextから開始する。
- 別Chat SessionのRaw履歴は新Sessionへ自動連結せず、過去はWorld Memory Retrieverから関連分だけ参照する。
- 同一native Contextの通常Turnでは既送Raw履歴を再送せず、前回以降の差分だけを送る。
- Persona / Nirai Skills / 固定会話ルール等の静的Contextも毎Turn再送せず、初回・Core再起動後のrefresh・内容変更時・検知可能なProvider compaction後だけ再注入する。
- Whisperは公開Chat Sessionへ従属しない`private:whisper:<resident>`のResident単位Private Channelとする。
- WhisperはProvider native Working Contextを同じPrivate Channel上で継続し、Nirai独自の一定Turnローテーションを行わない。native Context rebuild時だけ最大20件のrecent Raw等から復元し、通常Turnは前回marker以降の差分だけを送る。5年分Raw WhisperをPromptへ全投入しない。
- 同じWorld Memory Evidenceを同一native Working Contextへ一度注入した後は、内容が変わらない限り毎Turn再注入しない。Core再起動・Provider reset・検知されたcompaction後は再注入可能とする。
- Codex `contextCompaction`通知を検知した場合、Thread IDは維持してdelivery cacheだけ無効化し、次Turnで必要なstatic / Memory contextをrefreshする。
- Public / Whisperは別native Contextを使い、Privacy境界を跨がない。
- Cursor Conversation HomeはProvider状態を保持する一方、Turn終了後に注入Credential copyを残さない。

## 検証

### Core

`\.tools\test-core.ps1`

- **323 passed**

### Cursor Live 2-turn Smoke

`\.tools\smoke-resident-native-conversation.ps1 cursor`

結果：

```text
SAFE provider=cursor native_session_reused=true first_has_reply=True remembered_token=true credential_copy_retained=false
```

1Turn目の合言葉を2Turn目Promptへ再送せず、同一native Sessionで想起できることを確認した。

## 保留

- Codex Live 2-turn Smoke：2026-09-06時点のProvider利用枠上限により実施不能。自動回帰は通過済み。
- Gemini Live 2-turn Smoke：既存専用Smokeがないため未実施。Interactions APIの`previous_interaction_id`経路と自動Testは実装済み。
- Claude Code：現在Provider利用不可。復旧時に現行公式continuation能力をReference-Firstで確認する。

## 長期Memoryとの境界

このSliceはProvider transport/contextのToken浪費を減らすものであり、長期Memory自体をProvider native Contextへ委譲しない。

- World Memory / Private MemoryはNiraiが正本。
- Provider Working Contextが失われてもPrivate Channel Identityを維持し、Nirai Memoryから再構成できることを最終Contractとする。
- 現行Private Memoryの`context.md`は直近12 Whisper由来の移行用派生Viewであり、5年長期Memoryの最終方式ではない。
- 現行`whispers.jsonl`のrecent/delta取得は全File scanを残しているため、History / Memory Scaling SliceでIndex / DB化する。
