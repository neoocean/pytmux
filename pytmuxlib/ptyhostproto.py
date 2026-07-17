"""PTY host ↔ 서버 와이어 프로토콜(프레이밍·상수) — Windows 세션유지 재시작 옵션 C.

설계: `WINDOWS_RESTART_SCENARIO.md` §3 옵션 C / §4 로드맵.

ConPTY 소유를 서버 밖 **장수명 pty-host 프로세스**로 분리한다. 서버는 이 프로토콜로 host
와 통신해 셸을 spawn/구동하고, **서버가 재시작해도 host(=세션)는 그대로** 남아 새 서버가
재연결한다. 전송은 ipc 와 동일하게 POSIX=AF_UNIX, Windows=TCP 루프백(`ipc.parse_endpoint`).

프레임 형식(단일 양방향 스트림에 제어+데이터 멀티플렉싱):

    [4바이트 big-endian 총길이 N][1바이트 타입][N-1바이트 payload]

  타입 'J' (제어, JSON)  : payload = UTF-8 JSON dict. 양방향. `op` 필드로 분기.
  타입 'D' (데이터, raw) : payload = [4바이트 pane_id][raw PTY 바이트]. 양방향
                           (서버→host=자식 stdin, host→서버=자식 stdout). PTY 바이트를
                           base64/JSON 으로 부풀리지 않는 핫패스.

pane_id 는 **서버가 할당**하는 32비트 부호없는 정수다(OS pid 아님). 서버는 이 id 로 패널을
참조하므로, host 의 실제 자식 pid(소개·introspection 용)와 무관하게 재시작을 가로질러
안정적인 식별자가 된다.

JSON 제어 op (서버→host):
  spawn   {op,pane,argv,cwd,env,cols,rows}  자식 셸을 띄운다(fire-and-forget; host 가
                                            'spawned' 로 실제 pid 회신).
  resize  {op,pane,cols,rows}               의사콘솔 크기 변경.
  pause   {op,pane} / resume {op,pane}       읽기 일시정지/재개(백프레셔).
  signal  {op,pane,how}                      how∈{terminate,kill} 자식 종료.
  close   {op,pane}                          PTY 해제(자식 트리 hangup).
  list    {op}                               살아있는 패널 목록 요청(재연결 시).
  ping    {op}                               생존 확인.

JSON 제어 op (host→서버):
  hello     {op,version,pid}                 연결 직후 host 가 자기소개.
  spawned   {op,pane,pid}                    spawn 완료·실제 자식 pid.
  exit      {op,pane,status}                 자식 종료(EOF). 서버는 패널을 닫는다.
  list_reply{op,panes:[{pane,pid,cols,rows,alive}]}  재연결 시 host 가 보유 패널 보고.
  pong      {op}
"""
from __future__ import annotations

import asyncio
import json
import struct

PROTO_VERSION = 1

# 프레임 헤더: 총길이(payload+타입바이트 포함이 아니라 '타입+payload' 길이) + 타입.
# 단순화: 4바이트 길이 L = 1(타입)+len(payload), 이어서 타입 1바이트, payload L-1 바이트.
_LEN = struct.Struct(">I")
TYPE_JSON = b"J"
TYPE_DATA = b"D"
_PANE = struct.Struct(">I")          # 'D' 프레임 앞 4바이트 pane_id

MAX_FRAME = 16 * 1024 * 1024         # 방어적 상한(손상/악의 프레임 차단)


def encode_json(obj) -> bytes:
    payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    body = TYPE_JSON + payload
    return _LEN.pack(len(body)) + body


def encode_data(pane_id: int, data: bytes) -> bytes:
    body = TYPE_DATA + _PANE.pack(pane_id & 0xFFFFFFFF) + data
    return _LEN.pack(len(body)) + body


async def read_frame(reader: asyncio.StreamReader, *, max_frame: int = MAX_FRAME):
    """다음 프레임을 읽어 ("json", dict) 또는 ("data", pane_id, bytes) 로 돌려준다.
    EOF/손상 시 None. 호출부는 None 을 연결 종료로 다룬다.

    `max_frame` 은 이 한 번의 읽기에만 적용하는 상한이다 — **인증 전** 읽기는 훨씬
    작게 준다(HANDSHAKE_MAX_FRAME). 종전엔 MAX_FRAME(16MiB) 고정이라 무인가 피어가
    16MiB 를 광고하고 1바이트씩 흘리면 `readexactly` 가 그만큼 버퍼를 키운 채 매달렸다
    (보안검수 2026-07-17 PTYH-1)."""
    try:
        hdr = await reader.readexactly(_LEN.size)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    (n,) = _LEN.unpack(hdr)
    if n < 1 or n > max_frame:
        return None
    try:
        body = await reader.readexactly(n)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    typ, payload = body[:1], body[1:]
    if typ == TYPE_JSON:
        try:
            return ("json", json.loads(payload.decode("utf-8")))
        except (ValueError, UnicodeDecodeError):
            return None
    if typ == TYPE_DATA:
        if len(payload) < _PANE.size:
            return None
        (pane_id,) = _PANE.unpack(payload[:_PANE.size])
        return ("data", pane_id, payload[_PANE.size:])
    return None


async def write_frame(writer: asyncio.StreamWriter, raw: bytes) -> bool:
    """encode_json/encode_data 결과 bytes 를 보낸다. 끊긴 소켓은 False(예외 흡수)."""
    try:
        writer.write(raw)
        await writer.drain()
        return True
    except (ConnectionError, OSError, RuntimeError):
        return False
