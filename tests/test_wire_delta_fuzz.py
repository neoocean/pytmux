"""와이어 델타 퍼저 — 서버 `screen-delta` 생성과 **실 클라 병합**의 두 경로 오라클.

층 지도(§10-12 조사 2026-07-28e). 화면이 클라에 도착하기까지 층이 넷이다:
VT 파싱 → 서버 화면 모델 → **행캐시(dirty)** → **클라별 델타 기준(`_sent_rows`)** →
클라 행 병합. 앞의 둘은 이미 오라클이 있었고(`test_model.py` 의 빠른경로/전체경로
비교, `vt_render_golden`), **뒤의 둘은 이 파일이 처음**이다.

이 층의 결함은 조용하다 — 서버가 "이 행은 이미 보냈다"고 기억하면 그 행은 **영영**
안 나가고, 화면은 전체 재전송(`prefix r`)이나 재attach 전까지 옛 내용으로 굳는다.

오라클: 랜덤 바이트를 먹인 뒤 **클라가 병합으로 재구성한 rows** 가 같은 시점 패널의
**전체경로 render** 와 바이트 동일해야 한다. 양쪽 끝이 전부 프로덕션 코드다 —
서버는 `serverio.ServerIOMixin._screen_frame`, 클라는 실 App 의 `_dispatch`(병합을
여기서 다시 구현하면 **내 재구현을 시험**하게 되고 진짜 클라가 바뀌어도 안 깨진다).

되돌리면 실패해야 하는 것(`FUZZ_MUTATE` 로 실증 — 개발용, 평소엔 0):
  · 1 = 델타에서 '한 런짜리 행'(색만 바뀐 모양)을 뺀다
  · 2 = 델타의 마지막 항목을 뺀다
둘 다 이 오라클이 잡는 것을 확인했다(각 60시드 중 5건). **0 divergence 라는 결과는
이 확인이 있어야 값이 된다.**
"""
import json
import os
import random

import harness  # noqa: F401
from harness import make_app

from pytmuxlib.model import Pane
from pytmuxlib.protocol import frame_msg
from pytmuxlib.serverio import ServerIOMixin

SEEDS = int(os.environ.get("PYTMUX_FUZZ_SEEDS", "120"))
STEPS = int(os.environ.get("PYTMUX_FUZZ_STEPS", "40"))
MUTATE = int(os.environ.get("FUZZ_MUTATE", "0"))

_HALF = "▀▄█░▌▐"          # 제보된 표면(배너 마스코트)과 같은 반칸 블록


class _Srv:
    """`_screen_frame` 만 쓰는 최소 서버 — 그 메서드는 `_DELTA_MAX_RATIO` 만 참조한다."""
    _DELTA_MAX_RATIO = ServerIOMixin._DELTA_MAX_RATIO
    _screen_frame = ServerIOMixin._screen_frame


class _Conn:
    """서버가 클라마다 들고 있는 델타 기준."""

    def __init__(self):
        self._sent_rows = {}


def _full_render(p):
    """전체경로 render — **행캐시를 보존**한다.

    캐시를 그냥 날리면 다음 스텝이 항상 전체경로가 되어, 빠른 경로(dirty) 결함이
    델타 층으로 전파되는 조합을 오라클이 **스스로 지운다**(실제로 밟은 함정)."""
    keep, keep_key = p._row_cache, p._row_cache_key
    p._row_cache = None
    p._row_cache_key = None
    rows = p.render(True)[0]
    p._row_cache, p._row_cache_key = keep, keep_key
    return rows


def _color(rng):
    k = rng.randrange(5)
    if k == 0:
        return "\x1b[38;2;%d;%d;%dm" % tuple(rng.randrange(256) for _ in range(3))
    if k == 1:
        return "\x1b[48;2;%d;%d;%dm" % tuple(rng.randrange(256) for _ in range(3))
    if k == 2:
        return f"\x1b[38;5;{rng.randrange(256)}m"
    if k == 3:
        return f"\x1b[{rng.choice([30, 33, 36, 90, 93, 96, 97])}m"
    return f"\x1b[{rng.choice([40, 43, 46, 100, 103, 105, 107])}m"


def _op(rng, cols, lines):
    """색을 집중적으로 흔드는 연산 표(마스코트 = 반칸 블록 + 트루컬러)."""
    y, x = rng.randrange(1, lines + 1), rng.randrange(1, cols + 1)
    k = rng.randrange(14)
    if k == 0:                              # 마스코트 모사: 전경+배경 + 반칸 블록
        blocks = "".join(rng.choice(_HALF) for _ in range(rng.randrange(1, 8)))
        return (f"\x1b[{y};{x}H{_color(rng)}{_color(rng)}{blocks}\x1b[0m").encode()
    if k == 1:                              # 같은 글자를 **색만** 바꿔 재기록
        return (f"\x1b[{y};{x}H{_color(rng)}▀▀▀\x1b[0m").encode()
    if k == 2:
        return f"\x1b[{y};{x}H{'ab' * rng.randrange(1, 6)}".encode()
    if k == 3:
        return b"\x1b[2J"
    if k == 4:
        return f"\x1b[{y};1H\x1b[K".encode()
    if k == 5:
        return (f"line{rng.randrange(100)}\r\n" * rng.randrange(1, 5)).encode()
    if k == 6:
        return f"\x1b[{y};1H\x1b[{rng.randrange(1, 4)}L".encode()
    if k == 7:
        return f"\x1b[{y};1H\x1b[{rng.randrange(1, 4)}M".encode()
    if k == 8:                              # 스크롤 영역
        return f"\x1b[{rng.randrange(1, lines)};{rng.randrange(2, lines + 1)}r".encode()
    if k == 9:                              # SU/SD
        return f"\x1b[{rng.randrange(1, 4)}{rng.choice('ST')}".encode()
    if k == 10:
        return rng.choice([b"\x1bM", b"\x1bD", b"\x1bE"])
    if k == 11:
        return rng.choice([b"\x1b[?1049h", b"\x1b[?1049l"])
    if k == 12:                             # 탭 전개 갭(_fill_flanked_gaps 대상)
        return (f"\x1b[{y};1H\x1b[48;2;70;70;70mAA\x1b[49m    "
                f"\x1b[48;2;70;70;70mBB\x1b[0m").encode()
    return (f"\x1b[{y};{x}H{_color(rng)}▀가나\x1b[7mRV\x1b[0m").encode()


def _mutated(frame):
    """개발용 뮤테이션 — 이 오라클이 실제로 무는지 보이는 데 쓴다(평소 0)."""
    if not MUTATE:
        return frame
    msg = json.loads(frame[4:])
    if msg.get("t") != "screen-delta" or not msg["rows"]:
        return frame
    msg["rows"] = ([r for r in msg["rows"] if len(r[1]) != 1] if MUTATE == 1
                   else msg["rows"][:-1])
    return frame_msg(msg)


def _reset_client(app):
    app.pane_content.clear()
    app.pane_wrap.clear()
    app.pane_top.clear()
    app._delta_no_base.clear()


async def test_wire_delta_matches_full_render():
    """서버 델타 → 실 클라 병합이 전체경로 render 와 항상 같은 화면을 만든다."""
    nclients = 3
    apps = [make_app(f"/tmp/pytmux-wire-fuzz-{i}.sock") for i in range(nclients)]
    for app in apps:
        # 합성/송신은 이 테스트의 대상이 아니다(위젯 마운트·소켓 없음).
        app._request_composite = lambda: None
        app.send_cmd = lambda *a, **kw: None

    cols, lines = 60, 16
    for seed in range(SEEDS):
        rng = random.Random(seed)
        srv = _Srv()
        pane = Pane(-1, -1, cols, lines)
        conns = [_Conn() for _ in range(nclients)]
        for app in apps:
            _reset_client(app)

        for step in range(STEPS):
            pane.feed(_op(rng, cols, lines))
            if rng.randrange(10) == 0:              # 스크롤(캐시 무효화 경로)
                pane.scroll_by(rng.choice([1, 3, -2]))
                pane.dirty = True
            elif rng.randrange(20) == 0:
                pane.scroll_to("bottom")
                pane.dirty = True
            if rng.randrange(12) == 0:              # full 재동기(attach/prefix r)
                conns[rng.randrange(nclients)]._sent_rows.clear()
            if not pane.dirty:
                continue

            rows, cursor = pane.render(True)        # flush 루프와 같은 1회 render
            pane.dirty = False
            for c, app in zip(conns, apps):
                frame = _mutated(srv._screen_frame(
                    c, pane.id, rows, cursor, pane._last_wrap, pane._last_top))
                app._dispatch(json.loads(frame[4:]))
                if pane.id in app._delta_no_base:
                    # 실 클라는 기준 없는 델타에 redraw 를 요청한다 → 서버가 full 로
                    # 답한다(기준을 비우면 `_screen_frame` 이 full 을 낸다).
                    c._sent_rows.pop(pane.id, None)
                    f2 = srv._screen_frame(c, pane.id, rows, cursor,
                                           pane._last_wrap, pane._last_top)
                    app._dispatch(json.loads(f2[4:]))

            want = _full_render(pane)
            for i, app in enumerate(apps):
                got = app.pane_content.get(pane.id)
                if got is None:
                    continue
                assert got[0] == want, (
                    "클라 %d 화면이 어긋났다(seed=%d step=%d): 첫 어긋난 행 %r"
                    % (i, seed, step,
                       next(((y, got[0][y], want[y])
                             for y in range(min(len(got[0]), len(want)))
                             if got[0][y] != want[y]), None)))
