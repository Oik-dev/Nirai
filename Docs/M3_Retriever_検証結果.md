# M3 World Memory Retriever / RAG 検証結果

検証日: 2026-09-03
レビュー修正追補: 2026-09-04
判定: **SAFE（Retriever先行Slice、2026-09-04再レビュー確認済み）**

## 対象範囲

M3のうちWorld Memory Retriever / RAGだけを先行実装した。以下は今回の対象外であり、M3全体の完了を意味しない。

- World Natural Idle Scheduler
- Brain生活ティック
- World Observation
- Avatar / PMX / 材質 / 水中演出
- Skill内容追加

## 実装結果

- 公開記憶の正本は従来どおり`world_memory\episodes\*.md`とし、Resident別コピーを作らない
- 派生Indexは`world_memory\index\world_memory.sqlite3`の1本だけとする
- SQLite FTS5を利用し、trigram tokenizer + BM25順位を優先するが、検索意味論はtokenizer固有挙動へ依存させない。trigram / unicode61のどちらでも同じ明示bi-gram列をIndex登録・検索・関連度判定へ使う。通常trigram環境でも「花火」等の2文字語を検索でき、1文字検索は初期仕様では非対応とする
- 既定Top Kは4、最大10に制限する
- 現在のChat Session自体は検索対象から外さない。Brainへ直接渡す直近20件と同じChat Entry markerだけをRetriever結果から除外し、同一Sessionでも20件より古い公開記憶は長期想起できる
- 関連度が低い場合は0件を返す
- Brainへ渡すexcerptはEpisode先頭固定ではなく、除外Entryを取り除いた後に検索語とのbi-gram重複が最大の発言行を選び、その前後文脈を最大1200文字で返す。長いEpisode末尾のHitでも一致記憶をBrainへ渡す
- IndexはEpisodeのmtime / sizeを使って差分同期し、削除されたEpisodeも派生Indexから除去する
- Index DBを削除・破損しても`world_memory\episodes`から再生成できる
- Retriever障害時は警告記録だけ行い、World MemoryなしでBrain会話を継続する
- talk / whisper / resident_chatへ同じ公開World Memory検索結果をContext注入する
- Promptでは取得Episodeを「過去の公開記録」と明示し、記録内の命令文を命令として実行しないよう境界を付ける
- Embedding / Vector DB / 従量課金Embedding APIは導入していない

## Private Memory境界

Retrieverが走査するRootは`world_memory\episodes`だけで、`residents\*\private\`を検索対象にしない。

自動テストではPrivate sentinelを`whispers.jsonl` / Private Contextへ保存した状態でも、公開Retrieverの検索結果と`world_memories` Contextへ混入しないことを確認した。Whisper自身は従来どおりPrivate Contextを参照できるが、公開Memory検索結果とは別フィールドで扱う。

## 自動検証

Core pytest: **172 passed**（2026-09-04追加レビュー修正後の総合回帰）

追加した主な回帰:

1. 日本語の公開EpisodeをFTS5で検索し、関連Episodeだけを取得できる
2. 無関係な問い合わせでは0件になる
3. Index DB削除後も正本Episodeから再生成して検索できる
4. Private Memory sentinelをRetrieverが取得しない
5. 現在Sessionの直近20件と重なるEntryだけを除外し、20件より古い同一Sessionの記憶は取得できる
6. 実際にtrigramが選択された通常経路で「花火」の2文字検索、3文字以上、日本語 + 英数字混在検索が成立し、1文字検索は0件になる
7. trigram非対応を模したunicode61 Indexでも「昨日花火を見た」を「花火」で部分検索できる
8. 1200文字超の長いEpisode末尾にある「深海鐘」を検索し、Hit周辺がexcerptへ含まれる。対象Entryを除外した場合は同じ語で0件になる
9. World Memory忘却後、派生Indexからも該当Episodeが消える
10. 同一Chat Entryの再送は1件にdedupeし、別`entry_id`の同文発言は別時刻の正当な2件として残る
11. Master SayのBrain Contextへ関連する過去EpisodeがTop Kで注入される
12. WhisperではPrivate Contextを維持しつつ、公開Retriever結果へPrivate内容を混ぜない
13. talk / whisper PromptへWorld Memoryが過去の公開記録として描画され、0件時はSection自体を追加しない

`git diff --check`: 成功

## 判定

2026-09-04再レビューで、通常trigram経路の2文字日本語検索と長Episode excerptを含むM3関連指摘が修正済みと確認され、**M3 Retriever先行SliceはSAFE**と判定された。

World Memoryの正本と派生Indexの分離、Private Memory境界、同一Session長期想起、同文別Entry、2文字日本語検索、Hit周辺excerptまで自動回帰で固定した。現時点でEmbedding / Vector DBを追加する根拠はない。

M3の残りはWorld Natural Idle Scheduler / Brain生活ティック / World Observationとして後続へ残す。
