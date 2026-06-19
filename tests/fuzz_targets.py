"""공유 퍼징 타깃(Tier 2) — 신뢰불가 바이트를 파서에 먹여 '절대 불변식'을 검사한다.

`test_fuzz_parsers.py`(결정론적 시드 — 일반 스위트, 신규 의존성 0)와
`tests/fuzz/*.py`(atheris 커버리지 가이드 — 설치 시 야간)가 **같은 타깃**을 공유한다.
각 check_*/a* 는 ① 파서가 신뢰불가 입력에 **예외를 던지면 그대로 전파**(= 버그를
atheris/테스트가 잡음) ② 추가 불변식 위반 시 AssertionError.

대상은 네트워크/패널에서 신뢰불가 바이트를 직접 받는 경계 함수들이다:
  · protocol.read_msg      — 클라↔서버 와이어(길이프리픽스 JSON)
  · ptyhostproto.read_frame — 서버↔pty-host 와이어(멀티플렉싱 프레임)
  · protocol.clamp_dim     — 클라 제공 치수 가드(F6)
  · model.Pane.feed        — 패널 PTY 출력(VTTokenizer, N1/N2 의 OSC 경로 포함)
"""
from __future__ import annotations

import asyncio

from pytmuxlib import protocol, ptyhostproto
from pytmuxlib.model import Pane
from pytmuxlib.protocol import MAX_W, MIN_W, clamp_dim
from pytmuxlib.vtparse import VTTokenizer


async def aread_protocol(data: bytes):
    """read_msg 계약: 어떤 바이트든 예외 없이 (JSON값|None) 반환."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return await protocol.read_msg(reader)


async def aread_ptyhost(data: bytes):
    """read_frame 계약: 예외 없이 None 또는 ('json',obj)/('data',id,bytes)."""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    r = await ptyhostproto.read_frame(reader)
    assert r is None or (isinstance(r, tuple) and r[0] in ("json", "data")), r
    return r


def check_clamp(data: bytes) -> None:
    """clamp_dim: 임의 입력(여러 타입)에 대해 결과는 늘 [MIN_W, MAX_W] 정수."""
    vals = [data, data.decode("latin1")]
    if data:
        vals.append(int.from_bytes(data[:8], "big", signed=True))
        vals.append(data.decode("latin1") + "x")     # 숫자 변환 실패 경로
    for v in vals:
        out = clamp_dim(v, MIN_W, MAX_W, 80)
        assert isinstance(out, int) and MIN_W <= out <= MAX_W, (v, out)


def _split_points(data: bytes, n: int) -> list[int]:
    """data 에서 파생한 결정론적 분할 경계(파서의 feed-경계 상태 이월을 흔든다)."""
    if not n:
        return []
    return sorted({b % (n + 1) for b in data[:6]})


def check_vtparse(data: bytes, cols: int = 80, rows: int = 24) -> None:
    """Pane.feed: ① 통짜·분할 feed 모두 **예외 없음**(신뢰불가 패널 출력이 파서를
    못 죽인다 — N1/R2 동류 DoS 차단) ② OSC 본문 ≤ _OSC_MAX(N2).

    분할 feed 는 escape/멀티바이트 시퀀스를 경계에서 쪼개 파서의 carry 상태를 흔든다
    (raise 탐지 목적). 통짜 vs 분할의 *화면 동일*은 불변식이 아니다 — mid-UTF8/
    mid-escape 분할은 정당하게 다른 결과를 낼 수 있어(서버는 PTY 한 read 를 통짜 feed)
    동일성은 단언하지 않는다. 화면 충실성의 pyte 차분 등가는 test_vtparse.py 가 담당."""
    whole = Pane(-1, -1, cols, rows)
    whole.feed(data)
    if whole._tok is not None:                        # native 파서면 OSC 상한 보장
        assert len(whole._tok._osc) <= VTTokenizer._OSC_MAX, len(whole._tok._osc)
    split = Pane(-1, -1, cols, rows)
    prev = 0
    for b in _split_points(data, len(data)) + [len(data)]:
        split.feed(data[prev:b])
        prev = b
    if split._tok is not None:
        assert len(split._tok._osc) <= VTTokenizer._OSC_MAX, len(split._tok._osc)


# ---- atheris/CLI 용 동기 래퍼 ----
def check_protocol(data: bytes) -> None:
    asyncio.run(aread_protocol(data))


def check_ptyhost(data: bytes) -> None:
    asyncio.run(aread_ptyhost(data))


TARGETS = {
    "protocol": check_protocol,
    "ptyhost": check_ptyhost,
    "clamp": check_clamp,
    "vtparse": check_vtparse,
}


def seed_corpus() -> list[bytes]:
    """시드 입력 — 정상 프레임·경계·알려진 악성(N1 OSC 등). 결정론적 퍼징과 atheris
    코퍼스 양쪽이 쓴다."""
    import json

    seeds: list[bytes] = []
    # 정상/경계 protocol 프레임
    for obj in ({"t": "hello", "cols": 80, "rows": 24},
                {"t": "list", "token": "0" * 64}, [1, 2, 3], 42, "x", None):
        body = json.dumps(obj).encode()
        seeds.append(len(body).to_bytes(4, "big") + body)
    seeds.append(b"\xff\xff\xff\xff")                 # 거대 길이프리픽스(헤더만)
    seeds.append(b"\x00\x00\x00\x05bad!!")            # 비-JSON 본문
    seeds.append(b"")
    # ptyhost 프레임(타입바이트 J/D)
    seeds.append((1 + 2).to_bytes(4, "big") + b"J{}")
    seeds.append((1 + 4).to_bytes(4, "big") + b"D\x00\x00\x00\x01")
    # VT 시퀀스(정상·alt·와이드·N1 거대 OSC·미종결 CSI/DCS)
    seeds += [b"hello\r\n", b"\x1b[31mred\x1b[0m", b"\x1b[2J\x1b[H",
              b"\x1b]0;title\x07", b"\x1b]0;" + b"A" * 9000 + b"\x07",
              b"\x1bP" + b"q" * 5000, b"\x1b[", b"\x1b[38;2;1;2;3m",
              "와이드문자🌟混在".encode()]
    return seeds
