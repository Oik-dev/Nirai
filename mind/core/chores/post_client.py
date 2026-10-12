"""郵便局の公式MCP入口。読取は行わず、道具は送信と済印だけ。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "letters.toml"
LETTER_ID = re.compile(r"L\d{8}-\d{6}-[a-f0-9]+")


@dataclass(frozen=True)
class PostConfig:
    url: str
    friends: tuple[str, ...]
    work: str


def load_post_config(path: Path = DEFAULT_CONFIG) -> PostConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))["post"]
    return PostConfig(url=data["url"], friends=tuple(data["friends"]), work=data["work"])


@dataclass(frozen=True)
class PostResult:
    """確実に送信できた場合だけidを持つ。結果不明はfailed=False、id=None。"""
    letter_id: str | None = None
    failed: str | None = None


async def _call(config: PostConfig, name: str, arguments: dict):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(config.url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(name, arguments=arguments)


def _text(result) -> str:
    return "\n".join(x.text for x in result.content if x.type == "text")


def send_letter(config: PostConfig, *, to: str, body: str, reply_to: str | None = None) -> PostResult:
    if to not in config.friends or not body.strip():
        return PostResult(failed="宛先か本文が無効")
    # 返信は元の作業場を郵便局が継ぐ。新しい手紙だけ既定の作業場へ。
    args = {"to": to, "body": body}
    if reply_to:
        args["reply_to"] = reply_to
    else:
        args["work"] = config.work
    try:
        result = asyncio.run(_call(config, "send_letter", args))
    except Exception as error:  # noqa: BLE001
        # 通信切断・時間切れは、郵便局が追記を終えた可能性があるので失敗扱いしない。
        # 明白な接続拒否だけを確実な失敗として返す。
        if _connection_refused(error):
            return PostResult(failed="郵便局につながらない")
        return PostResult()
    if getattr(result, "isError", False):
        return PostResult(failed=_text(result) or "郵便局が拒否した")
    match = LETTER_ID.search(_text(result))
    return PostResult(letter_id=match.group(0) if match else None)


def mark_done(config: PostConfig, letter: str) -> bool:
    try:
        result = asyncio.run(_call(config, "mark_done", {"letter": letter}))
        return not getattr(result, "isError", False)
    except Exception:  # noqa: BLE001
        return False


def _connection_refused(error: BaseException) -> bool:
    """ExceptionGroupの内側にある接続失敗も判定する。"""
    import httpx
    def walk(exc: BaseException) -> bool:
        if isinstance(exc, BaseExceptionGroup):
            return bool(exc.exceptions) and all(walk(item) for item in exc.exceptions)
        return isinstance(exc, httpx.ConnectError) and ("refused" in str(exc).lower() or "接続を拒否" in str(exc))
    return walk(error)
