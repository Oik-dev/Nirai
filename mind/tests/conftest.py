"""テストは本物の魂に触れない。

テストの間は、使い捨ての魂を作って NIRAI_SOUL に指定する（外から指定されていても上書きする）。
人格は `fixtures/persona/`（Serinaの移住時点の人格の写し。正本は魂にある）。
本物の魂を読む評価（`eval_*.py`）は、pytestではなく単体で実行する。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_SOUL = Path(tempfile.mkdtemp(prefix="nirai-test-soul-"))
(_SOUL / "identity.toml").write_text('name = "テスト用の住人"\n', encoding="utf-8")
shutil.copytree(Path(__file__).parent / "fixtures" / "persona", _SOUL / "persona")
os.environ["NIRAI_SOUL"] = str(_SOUL)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001, ARG001
    shutil.rmtree(_SOUL, ignore_errors=True)
