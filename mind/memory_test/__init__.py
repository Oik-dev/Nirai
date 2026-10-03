"""Serinaの記憶テスト（docs/plans/長期記憶の作り直し.md §11）。

記憶の形ではなく、記録（イデアの lifelog/）に照らして測る。問題は「記録のどこが思い出されるべきか」を、
記録から写した目印の言葉で持つ。思い出したものに目印が入っていれば当たり。だから、今の記憶と
作り直した記憶を、同じ問題で比べられる。

問題・判定の控え・結果は、イデアの data/memory_test/ に置く（中身は住人の人生なので、リポジトリには置かない）。
問題はGemmaの下書きをClaudeが記録と照らして監査して作る（make.py）。思い出したものが話に関係あるかは手元のGemmaが判定する（judge.py）。

使い方（mind の親フォルダーで）:
    mind\\.venv\\Scripts\\python -m mind.memory_test make --idea D:\\Products\\Residents\\Serina
    mind\\.venv\\Scripts\\python -m mind.memory_test run --idea D:\\Products\\Residents\\Serina
"""
