"""Serinaの記憶テスト（docs/設計書.md §4.9）。

記憶の形ではなく、記録（イデアの lifelog/）に照らして測る。問題は「記録のどこが思い出されるべきか」を、
記録から写した目印の言葉で持つ。思い出したものに目印が入っていれば当たり。だから、記憶の仕組みを替えても、
同じ問題で比べられる（旧い記憶と作り直した記憶の比較は、計画書 §11）。

問題・判定の控え・結果は、イデアの data/memory_test/ に置く（中身は住人の人生なので、リポジトリには置かない）。
問題集の組み立ては question_set.py、思い出したものが話に関係あるかの判定（手元のGemma）は judge.py。

使い方（mind の親フォルダーで）:
    mind\\.venv\\Scripts\\python -m mind.memory_test --idea D:\\Products\\Residents\\Serina
"""
