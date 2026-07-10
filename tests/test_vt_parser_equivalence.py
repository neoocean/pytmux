"""VT 파서 동등성 상시 회귀 하네스 — pyte 경로 ≡ native 경로 (옵션 B, C9).

docs/internal/VT_PARSER_TRADEOFF_2026-06-15.md §7. vt_parser="native"(자작 토크나이저) 패널이
기본 "pyte" 패널과 **클라이언트에 가는 출력(render rows+cursor)·화면 셀 SGR 속성·
스크롤백**까지 동일함을 영구 불변식으로 고정한다. 라이브 기본 전환(별도 사인) 전까지의
안전망이자, 전환 후 native 회귀를 잡는 게이트.

비교 축:
  ① render(True) rows + cursor      — 클라가 실제로 받는 직렬화 출력
  ② screen 셀 속성(fg/bg/bold/…)    — render 가 인코딩하지만 명시적으로도 못박음
  ③ 임의 슬라이싱 불변               — serverpty 가 FEED_SLICE 로 쪼개 먹이므로,
                                       어떻게 쪼개도 통짜 feed 와 동일해야 함
  ④ 스크롤백 뷰포트                  — 위로 스크롤한 렌더도 동일

의도적 제외: generic(non-NEST) DCS — native 는 본문을 소비하고 pyte 경로는 출력으로
흘리는 의도적 개선 차이(test_vtparse.test_dcs_consumed_intentional_divergence 참조).
"""
import glob
import os

import harness  # noqa: F401 (경로 설정)
from pytmuxlib.model import Pane

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "claude")


def _cells(screen):
    out = []
    for y in range(screen.lines):
        row = screen.buffer[y]
        out.append([(row[x].data, row[x].fg, row[x].bg, row[x].bold,
                     row[x].italics, row[x].underscore, row[x].reverse,
                     row[x].strikethrough) for x in range(screen.columns)])
    return out


def _assert_panes_equal(pa, pb, label):
    ra, ca = pa.render(True)
    rb, cb = pb.render(True)
    assert ra == rb, f"{label}: render rows 불일치"
    assert ca == cb, f"{label}: cursor 불일치 {ca} vs {cb}"
    assert pa.alt_active == pb.alt_active, f"{label}: alt_active 불일치"
    assert _cells(pa.screen) == _cells(pb.screen), f"{label}: 셀 SGR 속성 불일치"


def _new_pair(cols, rows):
    # 기준 패널은 **반드시 pyte 명시** — 기본값이 native 로 바뀐 뒤(2026-06-16)
    # 기본 생성에 의존하면 native≡native 비교가 되어 이 하네스가 조용히 무력화된다
    # (그래도 green 이라 회귀 그물이 사라진 걸 못 잡음). pyte≡native 를 지키는 핵심 핀.
    return (Pane(-1, -1, cols, rows, vt_parser="pyte"),
            Pane(-1, -1, cols, rows, vt_parser="native"))


# 광범위 합성 코퍼스: 우회 대상 + 일반 VT + 경계 케이스의 합집합.
_CORPUS = [
    b"\x1b[2J\x1b[Hplain text line\r\nsecond line\r\n",
    b"\x1b[1mBOLD\x1b[0m \x1b[3mITAL\x1b[0m \x1b[4mUNDER\x1b[0m \x1b[7mREV\x1b[0m",
    b"\x1b[31mred\x1b[42mgreenbg\x1b[0m \x1b[38;5;82m256\x1b[0m \x1b[38;2;1;2;3mtrue\x1b[0m",
    b"\x1b[4:3mcurly\x1b[4:0moff \x1b[38:2::10:20:30mcolonRGB\x1b[0m",   # 콜론 SGR
    b"\x1b[58:2::1:2:3munderlinecolor\x1b[0m",                          # 58: 버림
    b"\x1b[>4;2mXTMOD\x1b[>4;0m \x1b[>1ukittyA\x1b[<u \x1b[?1ukittyQ",  # private 드롭
    "와이드 가나다 ABC 漢字\r\n".encode(),
    b"\x1b[5;10HX\x1b[A\x1b[A\x1b[2DY\x1b[1;1Htop",                     # 커서 이동
    b"abcdefgh\x1b[1;2H\x1b[3X\x1b[1;5H\x1b[2@ins\x1b[1;5H\x1b[2P",     # ECH/ICH/DCH
    b"\x1b[3;6r\x1b[6;1Hscroll\r\nregion\r\ntest\r\nlines\r\n",         # 스크롤영역
    b"\x1b[?6h\x1b[1;1Horigin\x1b[?6l",                                 # 원점모드
    b"\x1b7\x1b[5;5Hsaved\x1b8restored",                                # DECSC/DECRC
    b"line scroll\r\n" * 30,                                            # 스크롤백 누적
    b"\x1b[?1049h\x1b[2J\x1b[Halt content here\x1b[3;1Hmore",           # alt 진입
    b"\x1b[?1049lback to main",                                         # alt 복귀
    b"tab\there\tcols\x1b[1;1H\x1bH\tHTS",                              # 탭스톱
]


async def test_corpus_pane_equivalence_whole_and_sliced():
    """코퍼스를 통짜·여러 슬라이스 폭으로 native 에 먹여, 기본 pyte 통짜 feed 와
    매 단계 동일(렌더·셀·alt). serverpty 의 FEED_SLICE 분할을 모사한다."""
    blob = b"".join(_CORPUS)
    for cols, rows in [(40, 12), (80, 24), (24, 8)]:
        # 통짜 비교(코퍼스 누적)
        pa, pb = _new_pair(cols, rows)
        for i, chunk in enumerate(_CORPUS):
            pa.feed(chunk)
            pb.feed(chunk)
            _assert_panes_equal(pa, pb, f"corpus[{cols}x{rows}#{i}]")
        # 슬라이스 불변: pyte 통짜 vs native 슬라이스(여러 폭). 기준은 pyte 명시(핀).
        ref = Pane(-1, -1, cols, rows, vt_parser="pyte")
        ref.feed(blob)
        for width in (1, 3, 7, 64, 997):
            nat = Pane(-1, -1, cols, rows, vt_parser="native")
            for off in range(0, len(blob), width):
                nat.feed(blob[off:off + width])
            _assert_panes_equal(ref, nat, f"slice[{cols}x{rows} w={width}]")


async def test_fixture_pane_equivalence():
    """실제 캡처(claude/*.txt) 양 경로 Pane 동등(렌더·셀·스크롤백)."""
    files = sorted(glob.glob(os.path.join(FIXTURES, "*.txt")))
    assert files, "캡처 픽스처 없음"
    for path in files:
        with open(path, "rb") as f:
            data = f.read()
        for cols, rows in [(80, 24), (100, 30)]:
            pa, pb = _new_pair(cols, rows)
            pa.feed(data)
            pb.feed(data)
            _assert_panes_equal(pa, pb, f"fixture[{os.path.basename(path)} {cols}x{rows}]")


async def test_scrollback_view_equivalence():
    """스크롤백을 위로 스크롤한 뷰포트 렌더도 양 경로 동일."""
    cols, rows = 40, 8
    pa, pb = _new_pair(cols, rows)
    blob = b"".join(f"scrollback line {i:03d}\r\n".encode() for i in range(60))
    pa.feed(blob)
    pb.feed(blob)
    for up in (1, 5, 20, 52):
        pa.scroll = pb.scroll = 0          # 동일 출발
        pa.scroll_by(up)
        pb.scroll_by(up)
        ra, _ = pa.render(True)
        rb, _ = pb.render(True)
        assert ra == rb, f"scrollback up={up} 불일치"
    # 라이브(맨 아래) 복귀도 동일
    pa.scroll_to("bottom")
    pb.scroll_to("bottom")
    _assert_panes_equal(pa, pb, "scrollback live")


# ── 골든 해시 오라클(로드맵 #4·#6 안전망) ─────────────────────────────────────
# 위 테스트들은 pyte≡native **상대** 비교라, 두 경로를 동일하게 바꾸는 변경(예: #6
# pyte.Screen→자작 native Screen 교체, 또는 공용 SGR/렌더 로직 변경)은 못 잡는다.
# 골든 해시는 **절대 기준**: 현재 기본 파이프라인의 코퍼스·픽스처 렌더를 SHA-256 으로
# 동결해, 렌더 결과가 바뀌면(의도치 않은 회귀) 잡는다. #6 에서 pyte 를 걷어내도 native
# Screen 이 이 골든을 재현해야 한다. **의도적 변경 시**: PYTMUX_REGEN_GOLDEN=1 로 재생성.
import hashlib   # noqa: E402
import json      # noqa: E402

_GOLDEN_PATH = os.path.join(FIXTURES, "..", "vt_render_golden.json")


def _pane_signature(pane) -> str:
    """패널의 관측 가능한 렌더 상태(클라 출력 rows+cursor·alt·화면 셀)를 결정적
    JSON 문자열로 직렬화 — 골든 해시 입력. 순서 안정(sort_keys)."""
    rows, cursor = pane.render(True)
    payload = {
        "rows": rows, "cursor": cursor,
        "alt": pane.alt_active, "cells": _cells(pane.screen),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _golden_signatures() -> dict:
    """골든 대상 {label: sha256}. 기본 파이프라인(native default)로 렌더한다 —
    #6 교체 후 새 Screen 도 이 해시를 재현해야 한다."""
    def sig(pane):
        return hashlib.sha256(_pane_signature(pane).encode("utf-8")).hexdigest()
    out = {}
    # 코퍼스 누적(대표 3폭).
    for cols, rows in [(40, 12), (80, 24), (24, 8)]:
        p = Pane(-1, -1, cols, rows)             # 기본(native)
        for chunk in _CORPUS:
            p.feed(chunk)
        out[f"corpus_{cols}x{rows}"] = sig(p)
    # 실 캡처 픽스처(80x24).
    for path in sorted(glob.glob(os.path.join(FIXTURES, "*.txt"))):
        with open(path, "rb") as f:
            data = f.read()
        p = Pane(-1, -1, 80, 24)
        p.feed(data)
        out[f"fixture_{os.path.basename(path)}"] = sig(p)
    # 스크롤백 뷰(위로 스크롤 상태).
    p = Pane(-1, -1, 40, 8)
    p.feed(b"".join(f"scrollback line {i:03d}\r\n".encode() for i in range(60)))
    p.scroll_by(20)
    out["scrollback_up20_40x8"] = sig(p)
    return out


async def test_render_golden_hash_frozen():
    """현재 기본 렌더 파이프라인이 동결된 골든 해시를 재현한다(절대 회귀 게이트).
    PYTMUX_REGEN_GOLDEN=1 이면 골든을 재생성(의도적 렌더 변경 시). 불일치는 어느
    입력이 드리프트했는지 라벨로 보고한다."""
    cur = _golden_signatures()
    if os.environ.get("PYTMUX_REGEN_GOLDEN"):
        with open(_GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        return
    assert os.path.exists(_GOLDEN_PATH), (
        "골든 파일 없음 — PYTMUX_REGEN_GOLDEN=1 로 최초 생성")
    with open(_GOLDEN_PATH, encoding="utf-8") as f:
        golden = json.load(f)
    # 라벨 집합 동일 + 각 해시 일치.
    assert set(cur) == set(golden), (
        f"골든 라벨 불일치: 신규={set(cur)-set(golden)} 누락={set(golden)-set(cur)}")
    drift = [k for k in cur if cur[k] != golden[k]]
    assert not drift, (
        f"렌더 골든 드리프트(의도적이면 PYTMUX_REGEN_GOLDEN=1 재생성): {drift}")
