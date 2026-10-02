"""テストは本物のイデアに触れない。

テストの間は、使い捨てのイデアを作って NIRAI_IDEA に指定する（外から指定されていても上書きする）。
人格は `fixtures/persona/`（Serinaの移住時点の人格の写し。正本はイデアにある）。
本物のイデアを読む評価（`eval_*.py`）は、pytestではなく単体で実行する。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_IDEA = Path(tempfile.mkdtemp(prefix="nirai-test-idea-"))
(_IDEA / "identity.toml").write_text('name = "テスト用の住人"\n', encoding="utf-8")
shutil.copytree(Path(__file__).parent / "fixtures" / "persona", _IDEA / "persona")
os.environ["NIRAI_IDEA"] = str(_IDEA)


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ANN001, ARG001
    shutil.rmtree(_IDEA, ignore_errors=True)
