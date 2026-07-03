"""蒸留インテント Skill（経路A: 「今日の話を整理して」の検知）

実際の蒸留は REPL（app層）が行う。この Skill は「蒸留の意思表示を検知して
受け答えだけ返す」役——Core は決めるだけ、実行は上位が result["skill"] を見て行う。
"""

from __future__ import annotations

import re

from serina.skills.base import SkillContext

_PATTERN = re.compile(
    r"(今日|今回|これまで)の(話|会話|こと).{0,8}(整理|まとめ|日記に)"
    r"|会話を(整理|まとめ)"
    r"|日記にして"
)


class DistillIntentSkill:
    name = "distill"

    def can_handle(self, user_input: str) -> bool:
        return bool(_PATTERN.search(user_input))

    def run(self, ctx: SkillContext) -> str:
        return "うん、ここまでの話を整理して日記にしておきますね。少しだけ時間をください。"
