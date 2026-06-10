"""공통: 상수 · 프로토콜 프레이밍 · 색/시각 헬퍼.

소켓 경로/엔드포인트 결정은 pytmuxlib.ipc 로 일원화됐다(구 default_socket_path
함수는 ipc.default_endpoint 로 대체되어 제거됨 — docs/WINDOWS_PORT.md §6-4)."""
from __future__ import annotations

import asyncio
import json
import os
import struct
# fcntl/termios 는 POSIX 전용이라 모듈 최상단에서 import 하면 Windows 에서 이 모듈
# (그리고 이를 끌어오는 model/server 전체)이 깨진다. 실제로 쓰는 곳은 set_winsize
# 하나뿐이므로 함수 안에서 지연 import 한다 → Windows 에서도 protocol 이 import 된다.
# (PTY 크기 조절의 정식 위치는 pytmuxlib.pty_backend.PtyProcess.set_winsize 이며,
#  서버 리팩터가 끝나면 이 함수는 그쪽으로 흡수된다 — docs/WINDOWS_PORT.md §6-1.)


MIN_W = 3       # 패널 최소 폭(열) — 테두리(좌/우) + 내용 1칸
MIN_H = 3       # 패널 최소 높이(행) — 테두리(상/하) + 내용 1칸
# 비정상·악의 클라가 거대 치수(예: 99999×99999)를 보내 레이아웃/셀 계산 메모리를
# 부풀리는 자원 고갈(F6, docs/SECURITY_REVIEW.md)을 막는 상한. 실 단말보다 충분히 크다.
MAX_W = 2000
MAX_H = 2000
FLUSH_HZ = 30   # 서버 화면 push 주기
HISTORY = 10000 # 패널당 스크롤백 보관 행 수
# 대량 출력(빌드 로그·cat 등) 시 PTY 한 읽기(최대 64KB)를 이 크기 슬라이스로 쪼개
# pyte 에 먹이고 슬라이스마다 이벤트 루프에 양보한다(server._feed_drain). pyte feed 는
# 순수 파이썬이라 64KB 를 한 번에 먹이면 ~50ms 동안 루프가 막혀 입력·flush 가 지연된다.
# 8KB(≈6ms/슬라이스)면 폭주 중에도 입력·렌더가 부드럽게 끼어든다. 데이터 손실 없음
# (reader 를 잠깐 떼어 커널 PTY 백프레셔 유지 — docs/HANDOFF.md §9 의 CL 참조).
FEED_SLICE = 8192

# 한 프레임 페이로드의 상한(64MiB). 길이프리픽스는 4바이트(최대 4GiB)라, 손상되거나
# 악의적인(또는 비-pytmux 클라가 붙어 보낸) 헤더 하나가 `readexactly(length)` 로
# 수 GiB 할당을 요구해 서버/클라를 즉시 OOM 시킬 수 있다. 정상 메시지(레이아웃·화면
# 델타·status)는 이보다 훨씬 작으므로 상한 초과 프레임은 연결 종료로 처리한다.
MAX_FRAME = 64 * 1024 * 1024

# 클라↔서버 와이어 프로토콜 버전. 프레이밍·메시지 스키마가 비호환으로 바뀔 때 올린다.
# 첫 프레임(hello/list/control 등)에 클라가 실어 보내고, 서버가 불일치를 명확히 거절해
# 구·신 버전 혼용 시 조용한 오작동/JSON 깨짐 대신 명시적 실패가 되게 한다. 필드가 아예
# 없으면(구버전 클라) 호환으로 간주해 받아들인다(점진 롤아웃 — docs IMPROVEMENT §5.3).
PROTO_VERSION = 1


async def read_msg(reader: asyncio.StreamReader):
    """길이-프리픽스(4바이트 빅엔디언) + JSON 한 프레임을 읽는다. EOF·비정상 프레임이면
    None(호출부가 연결 종료로 처리). 길이는 MAX_FRAME 으로 상한, 페이로드가 깨졌거나
    비-JSON 이어도 예외 대신 None 을 돌려 리더 루프가 죽지 않게 한다."""
    try:
        header = await reader.readexactly(4)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    length = int.from_bytes(header, "big")
    if length > MAX_FRAME:
        return None                     # 무제한 길이 → OOM 방지(연결 종료 신호)
    try:
        payload = await reader.readexactly(length)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    try:
        # json.loads 는 bytes 를 직접 받아 내부에서 utf-8 디코드한다(중간 str 할당 제거).
        return json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return None                     # 손상·비-JSON 프레임은 조용히 버림(리더 보호)


def frame_msg(obj) -> bytes:
    """obj 를 길이프리픽스 JSON 프레임 bytes 로(write/drain 없음). 여러 메시지를 모아
    한 번에 write+drain 하려는 배치 송신용(B4)."""
    data = json.dumps(obj).encode("utf-8")
    return len(data).to_bytes(4, "big") + data


async def write_msg(writer: asyncio.StreamWriter, obj) -> bool:
    # 연결이 이미 끊겼거나 아직 안 맺힌 경우 writer 가 None 일 수 있다(종료/재연결
    # 레이스). 그대로 .write 를 부르면 AttributeError 가 ConnectionError/RuntimeError
    # catch 를 빠져나가 awaited 안 된 백그라운드 태스크가 터진다 → None 가드로 흡수.
    if writer is None:
        return False
    try:
        writer.write(frame_msg(obj))
        await writer.drain()
        return True
    except (ConnectionError, RuntimeError):
        return False


async def write_frames(writer: asyncio.StreamWriter, frames) -> bool:
    """이미 프레이밍된 bytes 들을 한 번에 write 하고 drain 1회(B4). flush 한 프레임에서
    한 클라로 갈 여러 screen+status 를 모아 보낼 때 await/drain 왕복을 줄인다."""
    if not frames:
        return True
    if writer is None:                  # write_msg 와 동일한 None 가드(종료 레이스)
        return False
    try:
        writer.write(b"".join(frames))
        await writer.drain()
        return True
    except (ConnectionError, RuntimeError):
        return False


def clamp_dim(val, lo: int, hi: int, default: int) -> int:
    """클라가 보낸 치수(cols/rows) 필드를 [lo, hi] 로 자른다(F6). 정수로 못 바꾸면
    default — 음수/거대값/타입혼동으로 인한 자원 고갈·예외를 한곳에서 막는다."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl       # POSIX 전용 — Windows import 를 막기 위해 함수 안에서 import
    import termios
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

