"""VT 렌더 회귀 하네스 — 골든해시 절대 가드 + 슬라이스 불변(로드맵 #6 M4b).

역사: 이 파일은 원래 pyte 경로 ≡ native 경로의 **상대 등가** 오라클이었다(vt_parser=
"pyte"/screen_impl="pyte" 기준 vs native). M4b(2026-07-18)에서 pyte 를 **완전 은퇴**
하며 그 상대 기준이 사라졌으므로(pyte 화면/파서 경로 삭제), 상대 등가 테스트는 제거
했다 — native≡native 공허통과를 남기지 않기 위함이다. 대신 두 축을 남긴다:

  ① **골든해시 절대 가드**(`test_render_golden_hash_frozen`): 현재 파이프라인(native)의
     코퍼스·픽스처·스크롤백 렌더를 SHA-256 으로 동결한다. 이 golden 은 **pyte 로 검증된
     베이스라인**이다 — 은퇴 직전 pyte≡native 가 성립하던 값을 그대로 재생성했고(원
     코퍼스는 은퇴 후에도 frozen 해시를 재현·M3 확장분은 pyte 와 직접 대조해 확인),
     이후 native 가 이 해시를 못 내면 렌더 회귀다. **의도적 변경 시** PYTMUX_REGEN_GOLDEN=1.
  ② **슬라이스 불변**(feed 경계 무관): serverpty 가 FEED_SLICE 로 임의 폭 쪼개 먹이므로,
     통짜 feed 와 여러 폭 슬라이스 feed 가 동일 화면을 내야 한다. 증분 토크나이저의
     경계-이월 버그를 잡는 **실질 속성**이다(통짜 vs 슬라이스 비교라 공허하지 않다).
"""
import glob
import hashlib
import json
import os

import harness  # noqa: F401 (경로 설정)
from pytmuxlib.model import Pane

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "claude")
_GOLDEN_PATH = os.path.join(FIXTURES, "..", "vt_render_golden.json")


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


# 광범위 합성 코퍼스: 우회 대상 + 일반 VT + 경계 케이스의 합집합. 뒤 4개는 구
# _M3_SAMPLES(IL/DL·DECSCNM·charset ESC(0/SI/SO·혼합 편집)를 병합한 것으로, pyte 은퇴
# 전 pyte≡native 로 검증됐고 이제 골든 corpus 에 포함돼 절대 가드가 이들까지 덮는다.
# (DECCOLM/resize 같은 geometry 변형은 별도 슬라이스 테스트에서 다룬다 — golden 격리.)
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
    b"\x1b[2J\x1b[Habc\r\ndef\r\nghi\r\njkl\r\n\x1b[1;1H\x1b[2L\x1b[4;1H\x1b[1M",  # IL/DL
    b"\x1b[2J\x1b[Hnormal\x1b[?5h reverse-screen \x1b[?5l back",        # DECSCNM
    b"\x1b[2J\x1b[H\x1b(0lqk\x0ex y\x0fmqj\x1b(B ascii",                # charset+SI/SO
    b"\x1b[2J\x1b[H" + b"".join(b"L%02d\r\n" % i for i in range(8))
    + b"\x1b[3;1H\x1b[2M\x1b[2;1H\x1b[3L\x1b[5;1H\x1b[2@ins\x1b[5;1H\x1b[2P",       # 혼합 편집
]


# ── 슬라이스 불변(feed 경계 무관): 통짜 native vs 슬라이스 native ─────────────────
async def test_corpus_slice_invariance():
    """코퍼스 통짜 feed 와 여러 폭 슬라이스 feed 가 동일 화면(렌더·셀·alt)을 낸다.
    증분 토크나이저의 feed 경계 이월 버그를 잡는다(serverpty FEED_SLICE 모사)."""
    blob = b"".join(_CORPUS)
    for cols, rows in [(40, 12), (80, 24), (24, 8)]:
        ref = Pane(-1, -1, cols, rows)
        ref.feed(blob)
        for width in (1, 3, 7, 64, 997):
            nat = Pane(-1, -1, cols, rows)
            for off in range(0, len(blob), width):
                nat.feed(blob[off:off + width])
            _assert_panes_equal(ref, nat, f"slice[{cols}x{rows} w={width}]")


async def test_fixture_slice_invariance():
    """실 캡처(claude/*.txt) 통짜 feed vs 슬라이스 feed 동일(렌더·셀·스크롤백)."""
    files = sorted(glob.glob(os.path.join(FIXTURES, "*.txt")))
    assert files, "캡처 픽스처 없음"
    for path in files:
        with open(path, "rb") as f:
            data = f.read()
        for cols, rows in [(80, 24), (100, 30)]:
            ref = Pane(-1, -1, cols, rows)
            ref.feed(data)
            for width in (1, 7, 997):
                nat = Pane(-1, -1, cols, rows)
                for off in range(0, len(data), width):
                    nat.feed(data[off:off + width])
                _assert_panes_equal(
                    ref, nat,
                    f"fixture-slice[{os.path.basename(path)} {cols}x{rows} w={width}]")


async def test_resize_slice_invariance():
    """resize 왕복(폭·높이 축소/확대)·DECCOLM 을 낀 스트림도 통짜 vs 슬라이스 동일.
    화면 재flow(폭축소 wrap-guard·drop-from-top·탭스톱 재계산)가 feed 경계에 무관함을
    못박는다(geometry 변형은 golden 과 격리해 여기서 검증)."""
    blob = (b"\x1b[2J\x1b[H"
            + b"".join(("line %02d wide 가나다 漢字 ABCDEFGH\r\n" % i).encode()
                       for i in range(20))
            + b"\x1b[1mBOLD\x1b[0m tail")
    after = "\r\nafter-resize 추가 X\r\n".encode()
    for cols, rows in [(60, 10), (100, 30), (20, 6), (80, 24), (40, 40)]:
        ref = Pane(-1, -1, 80, 24)
        ref.feed(blob)
        ref.resize(rows, cols)
        ref.feed(after)
        for width in (1, 5, 64):
            nat = Pane(-1, -1, 80, 24)
            for off in range(0, len(blob), width):
                nat.feed(blob[off:off + width])
            nat.resize(rows, cols)
            for off in range(0, len(after), width):
                nat.feed(after[off:off + width])
            _assert_panes_equal(ref, nat, f"resize->{cols}x{rows} w={width}")
    # DECCOLM(?3h=132컬럼 전환→클리어, ?3l 복귀) 슬라이스 불변
    seq = b"\x1b[2J\x1b[Hnarrow\x1b[?3hwide-after-deccolm\x1b[?3lback"
    ref = Pane(-1, -1, 40, 10)
    ref.feed(seq)
    for width in (1, 3, 64):
        nat = Pane(-1, -1, 40, 10)
        for off in range(0, len(seq), width):
            nat.feed(seq[off:off + width])
        _assert_panes_equal(ref, nat, f"deccolm w={width}")


async def test_scrollback_view_slice_invariance():
    """스크롤백 위로 스크롤한 뷰포트 렌더도 통짜 vs 슬라이스 동일."""
    cols, rows = 40, 8
    blob = b"".join(f"scrollback line {i:03d}\r\n".encode() for i in range(60))
    ref = Pane(-1, -1, cols, rows)
    ref.feed(blob)
    for width in (1, 7, 997):
        nat = Pane(-1, -1, cols, rows)
        for off in range(0, len(blob), width):
            nat.feed(blob[off:off + width])
        for up in (1, 5, 20, 52):
            ref.scroll = nat.scroll = 0
            ref.scroll_by(up)
            nat.scroll_by(up)
            ra, _ = ref.render(True)
            rb, _ = nat.render(True)
            assert ra == rb, f"scrollback w={width} up={up} 불일치"


# ── 골든 해시 오라클(로드맵 #4·#6 안전망 — 절대 가드) ─────────────────────────
# 슬라이스 테스트는 통짜 vs 슬라이스 **상대** 비교라, 렌더 로직을 통째로 바꾸는 변경은
# 못 잡는다. 골든 해시는 **절대 기준**: 현재 파이프라인의 코퍼스·픽스처 렌더를 SHA-256
# 으로 동결한다. pyte 은퇴 후에도 native 가 이 golden(=pyte 검증 베이스라인)을 재현해야
# 한다 — 이게 M4b 안전의 증명이다. **의도적 변경 시**: PYTMUX_REGEN_GOLDEN=1 로 재생성.


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
    """골든 대상 {label: sha256}. 기본 파이프라인(native)로 렌더한다."""
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
