"""공통: 상수 · 소켓 경로 · 프로토콜 프레이밍 · 색/시각 헬퍼."""
from __future__ import annotations

import asyncio
import datetime as _dt
import fcntl
import json
import os
import re
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


# ---- 토큰 리밋 자동 재개: 리밋 해제 시각 파서 ----
_RESET_RE12 = re.compile(r'(\d{1,2})(?::(\d{2}))?\s*([ap]m)', re.I)
_RESET_RE24 = re.compile(r'\b([01]?\d|2[0-3]):([0-5]\d)\b')

# 처리중(busy) 스피너: 현행 Claude Code 는 footer 에 "esc to interrupt" 대신
# "<글리프> <동명사>… (12s · ↑ 1.9k tokens · still thinking)" 형식의 애니메이션
# 줄을 그린다. 동명사(Crunching/Flowing/Baking/…)·글리프(✽✢✳✶✷✻ + 폰트에 따라
# `*`/`·`로 렌더되기도 함)는 매 프레임 바뀌므로 안정적인 시그널만 잡는다.
# 시간 숫자+s 를 요구해 "… +38 lines (ctrl+o)" 같은 도구 출력 오탐을 피한다.
# idle footer("shift+tab to cycle")가 busy 중에도 같이 보이므로 — busy 시그널은
# 가급적 여러 형태로 잡아두고 claude_state 에서 busy 를 먼저 판정한다.
_BUSY_SPINNER_RE = re.compile(
    r"…\s*\((?:\d+\s*m\s*)?\d+\s*s"      # "… (20s" / "… (2m 17s"
    r"|[✽✢✳✶✷✻*·]\s+\w+…"                # 스피너 글리프 + "동명사…"(시간 표시 전)
    r"|[↑↓]\s*[\d.,]+\s*[kKmM]?\s*tokens"  # "↑ 419 tokens" / "↓ 1.9k tokens"
    r"|still\s+thinking"                  # 명시적 진행 표기
)


def claude_state(text: str):
    """패널의 최근 화면 텍스트로 Claude Code CLI 상태를 추정한다.

    반환: "limit"(사용량 리밋으로 멈춤) / "busy"(프롬프트 처리중) /
    "idle"(입력 대기) / None(Claude Code 아님). 화면 특징 문자열로 휴리스틱 판별.

    현행 Claude Code(2026 기준)는 busy 시 작업 스피너("✽ Crunching… (38s …)"),
    idle 시 권한 모드 footer("⏵⏵ auto mode on (shift+tab to cycle)")를 그린다.
    모드 footer 는 busy 중에도 같이 보이므로 반드시 busy 를 먼저 판정한다.
    """
    low = text.lower()
    # 사용량 리밋 안내(자동재개 파서와 동일 신호)
    if "limit" in low and any(k in low for k in
                              ("reset", "again", "resume", "retry", "upgrade")):
        return "limit"
    # 처리중: 현행 작업 스피너 또는 레거시 "esc to interrupt"
    if (_BUSY_SPINNER_RE.search(text)
            or "esc to interrupt" in low or "interrupt)" in low):
        return "busy"
    # 입력 대기: 권한 모드 footer(shift+tab 순환) 또는 도움말/단축키 신호
    if ("shift+tab to" in low or "mode on (shift" in low
            or "? for shortcuts" in low or "for shortcuts" in low
            or "/help for help" in low or "bypass permissions" in low):
        return "idle"
    return None


_CTX_PCT_RES = [
    re.compile(r"context\s+(?:low|left|remaining)[^0-9%]*?(\d{1,3})\s*%", re.I),
    re.compile(r"(\d{1,3})\s*%\s*(?:context|remaining|"
               r"until\s+auto[- ]?compact)", re.I),
    re.compile(r"auto[- ]?compact[^0-9%]*?(\d{1,3})\s*%", re.I),
]
_TOK_RE = re.compile(r"([\d][\d.,]*\s?[kKmM]?)\s*tokens?\b", re.I)


def claude_usage(text: str):
    """Claude Code 화면 텍스트에서 컨텍스트 사용률/토큰 수를 best-effort 추출.

    Claude Code 가 항상 고정 위치에 토큰/컨텍스트를 출력하진 않으므로 휴리스틱이다.
    'ctx NN%' 또는 'NNk tok' 같은 짧은 문자열을 반환(못 찾으면 None).
    """
    for rx in _CTX_PCT_RES:
        m = rx.search(text)
        if m:
            return f"ctx {m.group(1)}%"
    m = _TOK_RE.search(text)
    if m:
        return f"{m.group(1).replace(' ', '')} tok"
    return None


def parse_reset_delay(text: str, now: "_dt.datetime | None" = None):
    """Claude Code 등의 사용량 리밋 안내 문구에서 해제 시각을 찾아
    지금부터 그때까지의 지연(초)을 반환. 못 찾으면 None."""
    low = text.lower()
    if "limit" not in low:
        return None
    if not any(k in low for k in ("reset", "again", "resume", "retry")):
        return None
    now = now or _dt.datetime.now()
    m = _RESET_RE12.search(text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ap = m.group(3).lower()
        if ap == "pm" and hour != 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
    else:
        m = _RESET_RE24.search(text)
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    delay = (target - now).total_seconds()
    return delay if 0 < delay <= 26 * 3600 else None

