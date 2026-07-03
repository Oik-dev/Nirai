"""理性エンジンの日本語実測ツール（決定4のデバッグステータス解消用・Ollama必須）

缶詰の日本語会話ログを理性エンジンに蒸留させ、生XMLとパース結果を並べて表示する。
確認観点は末尾に印字。--model で代替モデルの比較実測も可能（決定4「交換可能」の検証口）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.chat_llm import OllamaChatConnector
from serina.core.config import CoreConfig
from serina.core.reflection import REASON_SYSTEM_PROMPT
from serina.core.reflection_parser import parse_reason_output

# ①新事実2件（京都転居・紅茶好き） ②あだ名の対応1件（クロさん） ③ネガティブ1件（叱責）
# ④未解決1件（健康診断の結果待ち） ⑤挨拶・作業ログのノイズ
CANNED_TRANSCRIPT = """マスター: おはよう
セリナ: おはようございます、マスター。今日は少し眠そうですね？
マスター: 昨日引っ越しの荷解きで遅くなってさ。ようやく京都の新居も片付いてきたよ
セリナ: 京都への引っ越し、お疲れさまでした。新しい部屋は気に入りましたか？
マスター: うん、窓から鴨川が見える。そういえば今日、クロさんに会ってくる
セリナ: クロさん……どなたでしたっけ？
マスター: 前に話しただろ、黒田さんだよ。専門学校時代からの友人の。仕事の相談があるらしい
セリナ: 黒田さんですね、覚えました。よい相談になるといいですね
マスター: あとさ、この前頼んだリマインド、結局送られてこなかったんだけど
セリナ: ……申し訳ありません。設定を確認したつもりだったのですが、抜けていました
マスター: 大事な予定だったから、正直ちょっとがっかりしたよ。次は頼むね
セリナ: はい。同じ失敗は繰り返しません
マスター: まあいいさ。それより作業やるか。まずログ整理、次にビルド確認、それからテスト回して、コミットして……
セリナ: 順番に進めましょう。ログ整理から始めますね
マスター: 終わった終わった。あ、そうだ、来週健康診断の結果が出るんだよな。ちょっと緊張する
セリナ: 大きな検査でしたか？結果、気になりますね
マスター: 再検査にならなきゃいいけど。最近紅茶ばっかり飲んでるのも良くないのかな。コーヒーやめて紅茶派になったからさ
セリナ: マスターが紅茶派になったの、覚えておきます。アールグレイでしたよね
マスター: そうそう。じゃ、今日はこの辺で
セリナ: はい、黒田さんによろしくお伝えください。おやすみなさい"""

EXISTING_FACTS = """【既存ファクト】
9001: マスターは大阪に住んでいる
9002: マスターはコーヒー党で、毎朝ドリップコーヒーを淹れる"""

OPEN_THREADS = """【未解決スレッド】
41: 引っ越しの荷解きは終わった？"""

CURRENT_STATE = """【現在の心理状態】
intimacy = 0.4
tension = 0.3
energy_level = 0.5"""


def main() -> None:
    parser = argparse.ArgumentParser(description="理性エンジン日本語実測")
    parser.add_argument("--model", default=CoreConfig().reason_model)
    parser.add_argument("--base-url", default=CoreConfig().base_url)
    args = parser.parse_args()

    print(f"モデル: {args.model}")
    OllamaChatConnector.ensure_model_available(args.model, args.base_url)
    connector = OllamaChatConnector(args.model, args.base_url, keep_alive=0)

    user = f"【会話ログ】\n{CANNED_TRANSCRIPT}\n\n{CURRENT_STATE}\n\n{EXISTING_FACTS}\n\n{OPEN_THREADS}"
    start = time.time()
    raw = connector.chat(
        REASON_SYSTEM_PROMPT,
        [{"role": "user", "content": user}],
        options={"temperature": CoreConfig().reason_temperature},
    )
    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print("【生XML出力】")
    print("=" * 60)
    print(raw)

    result = parse_reason_output(raw)
    print("\n" + "=" * 60)
    print("【パース結果】")
    print("=" * 60)
    print(f"事実 ({len(result.facts)}件):")
    for f in result.facts:
        print(f"  - {f['content']}  keywords={f['keywords']}")
    print(f"感情採点 ({len(result.state)}件):")
    for name, entry in result.state.items():
        print(f"  - {name} = {entry['value']}  根拠: {entry['reason']}")
    print(f"narrative_mood: {result.narrative_mood}")
    print(f"未解決スレッド ({len(result.open_threads)}件):")
    for t in result.open_threads:
        print(f"  - {t['question']}（文脈: {t['context']}）")
    print(f"事実の失効申告 ({len(result.fact_updates)}件):")
    for u in result.fact_updates:
        print(f"  - 対象#{u['target_id']} → {u['content']}  理由: {u['reason']}")
    print(f"解決済みスレッド ({len(result.resolved_threads)}件):")
    for s in result.resolved_threads:
        print(f"  - #{s['id']}  理由: {s['reason']}")

    print("\n" + "=" * 60)
    print(f"応答時間: {elapsed:.1f}秒")
    print("""【確認観点（人間判定）】
1. 事実は正確か（京都転居・紅茶派の2件を拾えたか。ノイズの作業手順を拾っていないか）
2. ネガティブも拾えたか（リマインド失敗・がっかり、を検閲せず記録したか）
3. あだ名の対応関係を抽出したか（クロさん=黒田さん、keywordsに別名が両方入っているか）
4. fact_updates: 大阪在住(9001)とコーヒー党(9002)の失効を申告したか
5. resolved/open_threads: 荷解き(41)を解決済みにし、健康診断の追いかけ質問を出したか
6. 根拠は具体的か、日本語は自然か（機械翻訳臭・中国語混入がないか）""")


if __name__ == "__main__":
    main()
