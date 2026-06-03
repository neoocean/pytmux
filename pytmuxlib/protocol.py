"""공통: 상수 · 소켓 경로 · 프로토콜 프레이밍 · 색/시각 헬퍼."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import struct
import termios


MIN_W = 3       # 패널 최소 폭(열) — 테두리(좌/우) + 내용 1칸
MIN_H = 3       # 패널 최소 높이(행) — 테두리(상/하) + 내용 1칸
FLUSH_HZ = 30   # 서버 화면 push 주기
HISTORY = 10000 # 패널당 스크롤백 보관 행 수


def default_socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/pytmux-{os.getuid()}"
    os.makedirs(runtime, exist_ok=True)
    try:
        os.chmod(runtime, 0o700)
    except OSError:
        pass
    return os.path.join(runtime, "default.sock")


async def read_msg(reader: asyncio.StreamReader):
    """길이-프리픽스(4바이트 빅엔디언) + JSON 한 프레임을 읽는다. EOF 면 None."""
    try:
        header = await reader.readexactly(4)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    length = int.from_bytes(header, "big")
    try:
        payload = await reader.readexactly(length)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    return json.loads(payload.decode("utf-8"))


async def write_msg(writer: asyncio.StreamWriter, obj) -> bool:
    data = json.dumps(obj).encode("utf-8")
    try:
        writer.write(len(data).to_bytes(4, "big") + data)
        await writer.drain()
        return True
    except (ConnectionError, RuntimeError):
        return False


def set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, rows)
    cols = max(1, cols)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def conv_color(c):
    """pyte 색 이름 → Rich 색 토큰. 알 수 없으면 None(=기본색)."""
    if not c or c == "default":
        return None
    if c == "brown":
        return "yellow"
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return "#" + c
    if c.startswith("bright"):
        return "bright_" + c[6:]
    return c


# ---- Claude Code 휴리스틱은 pytmuxlib/claude.py 로 이전(docs/HANDOFF.md §11). ----
# 하위호환: 기존 `from .protocol import claude_state, ...` 임포트를 유지하기 위한
# re-export. 새 코드는 pytmuxlib.claude 에서 직접 가져올 것.
from .claude import (  # noqa: E402,F401
    claude_state, claude_usage, parse_reset_delay)

