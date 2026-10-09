"""振り分けルールの逆止弁のテスト。設計書 §3.4

厳しくなる方向（センシティブ拡大）は自動反映。緩む方向（クラウド解禁拡大）はマスター承認必須。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.state.routing_rules import RoutingRuleError, RoutingRules


def test_initial_rules_detect_nothing_sensitive() -> None:
    rules = RoutingRules()
    assert not rules.is_sensitive("今日の天気はいいね")


def test_tighten_is_applied_automatically() -> None:
    """§3.4: 厳しくなる方向は自動で反映"""
    rules = RoutingRules()
    rules.tighten("住所")
    assert rules.is_sensitive("俺の住所覚えてる？")


def test_loosen_without_master_approval_is_rejected() -> None:
    """§3.4: 緩む方向はマスター承認が必須"""
    rules = RoutingRules()
    rules.tighten("天気")
    try:
        rules.loosen("天気")
        raise AssertionError("承認なしで緩和が通ってしまった")
    except RoutingRuleError:
        pass
    assert rules.is_sensitive("天気の話をしよう"), "承認なしでは緩和されないはず"


def test_loosen_with_master_approval_is_applied() -> None:
    rules = RoutingRules()
    rules.tighten("天気")
    rules.loosen("天気", master_approved=True)
    assert not rules.is_sensitive("天気の話をしよう")


def test_pattern_detects_phone_number() -> None:
    """カテゴリB: 電話番号は形パターンで検出（キーワード登録不要）"""
    rules = RoutingRules()
    assert rules.is_sensitive("俺の番号は090-1234-5678だよ")


def test_pattern_detects_my_number() -> None:
    """カテゴリB: マイナンバー相当の12桁連続数字"""
    rules = RoutingRules()
    assert rules.is_sensitive("マイナンバーは1234 5678 9012です")


def test_pattern_detects_api_key_like_token() -> None:
    """カテゴリB: APIキーの典型的な接頭辞＋長いトークン"""
    rules = RoutingRules()
    assert rules.is_sensitive("キーはsk-ant-abcdefghijklmnopqrstuvwx1234だよ")


def test_pattern_detects_fullwidth_phone_number() -> None:
    """C-1回帰: 全角数字の携帯番号もNFKC正規化後に検出できる"""
    rules = RoutingRules()
    assert rules.is_sensitive("電話は０９０１２３４５６７８だよ")
    assert rules.is_sensitive("電話は０９０－１２３４－５６７８だよ")


def test_pattern_detects_dot_separated_phone_number() -> None:
    """C-1回帰: ドット/括弧区切りの番号も区切り除去後に検出できる"""
    rules = RoutingRules()
    assert rules.is_sensitive("電話は090.1234.5678だよ")
    assert rules.is_sensitive("電話は(090)1234-5678だよ")


def test_pattern_detects_unusual_dash_separated_phone_number() -> None:
    """Important#1回帰: en-dash/em-dash/マイナス記号/中黒区切りも検出できる（列挙ではなくUnicodeカテゴリで判定）"""
    rules = RoutingRules()
    assert rules.is_sensitive("電話は090–1234–5678だよ")  # en-dash
    assert rules.is_sensitive("電話は090—1234—5678だよ")  # em-dash
    assert rules.is_sensitive("電話は090−1234−5678だよ")  # 数学記号のマイナス
    assert rules.is_sensitive("電話は090・1234・5678だよ")  # 中黒


def test_fullwidth_registered_keyword_matches_after_normalization() -> None:
    """C-1回帰: 全角で登録したキーワードも正規化後のテキストに一致する"""
    rules = RoutingRules()
    rules.tighten("ＡＰＩキー")
    assert rules.is_sensitive("APIキーを教えて")


def test_pattern_does_not_flag_ordinary_conversation() -> None:
    """B系パターンは日常会話の短い数字・単語には反応しない"""
    rules = RoutingRules()
    assert not rules.is_sensitive("今日は3時に駅で待ち合わせしよう")


def test_proper_noun_registry_is_sensitive() -> None:
    """カテゴリC: 登録した固有名詞を含む断片はセンシティブ判定"""
    rules = RoutingRules()
    rules.add_proper_noun("架空商事")
    assert rules.is_sensitive("架空商事の案件が炎上しててさ")


def test_proper_noun_removal_without_approval_is_rejected() -> None:
    rules = RoutingRules()
    rules.add_proper_noun("架空商事")
    try:
        rules.remove_proper_noun("架空商事")
        raise AssertionError("承認なしで固有名詞の削除が通ってしまった")
    except RoutingRuleError:
        pass
    assert rules.is_sensitive("架空商事の話")


def test_tighten_survives_process_restart() -> None:
    """DECISIONS 2026-07-11持ち越しI-1: tightenの蓄積は再起動を跨いで残るべき"""
    with tempfile.TemporaryDirectory() as tmp:
        persist_path = Path(tmp) / "routing_rules.json"

        rules1 = RoutingRules(persist_path=persist_path)
        rules1.tighten("住所")

        # プロセス再起動を模して同じファイルから新規インスタンスを作る
        rules2 = RoutingRules(persist_path=persist_path)
        assert rules2.is_sensitive("俺の住所覚えてる？"), "tightenは再起動後も引き継がれるべき"


def test_proper_noun_survives_process_restart() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        persist_path = Path(tmp) / "routing_rules.json"

        rules1 = RoutingRules(persist_path=persist_path)
        rules1.add_proper_noun("架空商事")

        rules2 = RoutingRules(persist_path=persist_path)
        assert rules2.is_sensitive("架空商事の案件"), "固有名詞登録も再起動後に引き継がれるべき"


def test_approved_loosen_persists_across_restart() -> None:
    """承認済みの緩和はディスク上でも反映される（ロード経路自体が承認を迂回するわけではない）"""
    with tempfile.TemporaryDirectory() as tmp:
        persist_path = Path(tmp) / "routing_rules.json"

        rules1 = RoutingRules(persist_path=persist_path)
        rules1.tighten("天気")
        rules1.loosen("天気", master_approved=True)

        rules2 = RoutingRules(persist_path=persist_path)
        assert not rules2.is_sensitive("天気の話をしよう"), "承認済み緩和はディスクにも反映されるべき"


def test_persist_path_none_does_not_write_file() -> None:
    rules = RoutingRules()
    rules.tighten("住所")  # persist_path未指定なら保存処理自体を素通りする（例外なし）


def main() -> None:
    tests = [
        test_initial_rules_detect_nothing_sensitive,
        test_tighten_is_applied_automatically,
        test_loosen_without_master_approval_is_rejected,
        test_loosen_with_master_approval_is_applied,
        test_pattern_detects_phone_number,
        test_pattern_detects_my_number,
        test_pattern_detects_api_key_like_token,
        test_pattern_detects_fullwidth_phone_number,
        test_pattern_detects_dot_separated_phone_number,
        test_pattern_detects_unusual_dash_separated_phone_number,
        test_fullwidth_registered_keyword_matches_after_normalization,
        test_pattern_does_not_flag_ordinary_conversation,
        test_proper_noun_registry_is_sensitive,
        test_proper_noun_removal_without_approval_is_rejected,
        test_tighten_survives_process_restart,
        test_proper_noun_survives_process_restart,
        test_approved_loosen_persists_across_restart,
        test_persist_path_none_does_not_write_file,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
