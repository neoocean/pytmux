"""replay 골든 해시 — 캡처 재생 결과 동결(로드맵 test-infra §10-3④).

원안은 "native≡pyte 하네스 → SHA-256 오라클"이었으나 pyte 는 M4b 에서 완전 은퇴했으므로
(상대 기준 소멸) **절대 가드**로 재정의한다: `replay.replay()`(= Pane.feed +
`render_pane_lines` 합성)의 텍스트 프레임을 SHA-256 으로 동결한다.

`test_vt_parser_equivalence` 의 골든과 **다른 층**을 덮는다는 점이 이 파일의 존재 이유다:
저쪽은 `pane.render()` 의 (텍스트, 스타일) 행과 셀 SGR 을 해시하고, 여기는 replay 전용
합성기 `render_pane_lines`(와이드 문자 → 2칸 + 연속셀 제거, 줄 폭 = cols)를 해시한다 —
연속셀 처리를 한 칸만 어긋나게 해도 저쪽 골든은 그대로 통과한다(실측: 뮤테이션 M1).
경계도 명시해 둔다: `render_pane_lines` 는 **텍스트만** 만들므로 스타일/커서 표시
(`pane.render(with_cursor=…)`)는 이 해시에 원리적으로 안 들어온다 — SGR·커서 회귀는
`vt_render_golden` 이 담당한다(실측: with_cursor 를 뒤집는 뮤테이션은 여기서 무증상).

**의도적 변경 시** `PYTMUX_REGEN_GOLDEN=1 python3 tests/run.py test_replay_golden` 후
diff 를 리뷰한다(vt_render_golden·claude_scan_golden 과 같은 관례).

실 캡처(`captures/`)를 코퍼스로 쓰지 않는 이유: git 미러에 없어(gitignore, p4 전용)
CI 에서 골든이 성립하지 않는다. 대신 git 에 있는 `tests/fixtures/claude/*.txt`(실 Claude
화면 덤프)를 쓴다.
"""
import gzip
import hashlib
import json
import os
import tempfile

import harness  # noqa: F401 (경로 설정)
from pytmuxlib import cellwidth, replay as replay_mod

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "claude")
_GOLDEN = os.path.join(os.path.dirname(__file__), "fixtures", "replay_golden.json")

# 합성 코퍼스 — `render_pane_lines` 계약을 정면으로 겨눈다(와이드 경계·스크롤·alt).
_SYNTH = {
    "wide_at_right_edge": "가나다라마바사아자차카타파하" * 8,
    "wide_split_by_cr": "한글ABC\r漢字\r\n두번째 줄 가나다\r\n",
    "scroll_up_su": "".join(f"line{i}\r\n" for i in range(40)) + "\x1b[5S" + "after",
    "alt_screen": "main\r\n\x1b[?1049halt screen 내용\r\n\x1b[?1049lback",
    "combining_zero_width": "é̀x 조합문자 ​ zw\r\n",
    "emoji_and_box": "⏺ ✻ ⚠️ 🚀 │├─┤ 완료\r\n",
    "cursor_addressing": "\x1b[2J\x1b[H\x1b[5;10HX\x1b[A\x1b[2DY\x1b[1;1Htop",
    "long_wrap": ("가" * 60) + ("A" * 60) + "\r\n",
}
_GEOMETRIES = ((80, 24), (40, 10), (120, 30))


def _frames(data: bytes, cols: int, rows: int) -> str:
    return "\n".join(replay_mod.replay(data, cols, rows))


def _digest(data: bytes, cols: int, rows: int) -> str:
    return hashlib.sha256(_frames(data, cols, rows).encode()).hexdigest()


def _corpus():
    """(이름, 원시바이트) 목록 — 픽스처(실 화면 덤프) + 합성 경계 케이스."""
    items = [(f"synth_{k}", v.encode()) for k, v in sorted(_SYNTH.items())]
    for path in sorted(os.listdir(FIXTURES)):
        if path.endswith(".txt"):
            with open(os.path.join(FIXTURES, path), "rb") as fp:
                items.append((f"fixture_{path}", fp.read()))
    return items


def _compute():
    return {f"{name}@{cols}x{rows}": _digest(data, cols, rows)
            for name, data in _corpus() for cols, rows in _GEOMETRIES}


async def test_replay_render_golden_hash_frozen():
    """재생 프레임의 SHA-256 동결 — 합성기가 바뀌면 정확히 어느 표본인지 보고한다."""
    # 모호폭 wide 모드는 char_cells 를 바꿔 해시를 흔든다 — 골든은 기본(narrow) 기준.
    old = cellwidth.ambiguous_wide()
    if old:
        cellwidth.set_ambiguous_wide(False)
    try:
        cur = _compute()
        if os.environ.get("PYTMUX_REGEN_GOLDEN"):
            with open(_GOLDEN, "w", encoding="utf-8") as fp:
                json.dump(cur, fp, indent=1, sort_keys=True)
                fp.write("\n")
            print(f"  (골든 재생성: {len(cur)} 표본)")
            return
        assert os.path.exists(_GOLDEN), "골든 없음 — PYTMUX_REGEN_GOLDEN=1 로 최초 생성"
        with open(_GOLDEN, encoding="utf-8") as fp:
            golden = json.load(fp)
        assert set(cur) == set(golden), {
            "새 표본": sorted(set(cur) - set(golden))[:10],
            "사라진 표본": sorted(set(golden) - set(cur))[:10]}
        drift = [k for k in sorted(golden) if golden[k] != cur[k]]
        assert not drift, ("replay 렌더 회귀(의도된 변경이면 PYTMUX_REGEN_GOLDEN=1 "
                           f"재생성): {drift[:12]} / {len(drift)}건")
    finally:
        if old:
            cellwidth.set_ambiguous_wide(True)


async def test_replay_lines_are_exactly_cols_wide():
    """골든만으로는 '해시가 같다'뿐이라, 계약 자체(줄의 **시각 폭** = cols)도 못박는다.

    와이드 문자는 2칸을 먹고 연속셀은 제거되므로 문자 수 ≠ 폭이다 — 폭으로 세야 한다.
    (해시는 회귀를 잡고, 이 불변식은 '무엇이 옳은가'를 말한다.)
    """
    old = cellwidth.ambiguous_wide()
    if old:
        cellwidth.set_ambiguous_wide(False)
    try:
        for name, data in _corpus():
            for cols, rows in ((80, 24), (40, 10)):
                lines = replay_mod.replay(data, cols, rows)
                assert len(lines) == rows, (name, cols, rows, len(lines))
                for i, ln in enumerate(lines):
                    w = sum(cellwidth.char_cells(c) for c in ln)
                    assert w == cols, (name, f"{cols}x{rows}", f"row{i}", w,
                                       repr(ln[:40]))
    finally:
        if old:
            cellwidth.set_ambiguous_wide(True)


async def test_replay_is_identical_through_gzip_capture():
    """닫힌 캡처는 `.log.gz` 로 보관한다(REC) — 재생 결과가 원본과 **같아야** 한다.
    read_capture 의 투명 해제가 깨지면 골든이 아니라 이 왕복이 먼저 잡는다."""
    data = _SYNTH["wide_split_by_cr"].encode() + b"\x1b[2K\r\ntail\r\n"
    with tempfile.TemporaryDirectory() as td:
        raw = os.path.join(td, "pane-1.log")
        gz = raw + ".gz"
        with open(raw, "wb") as fp:
            fp.write(data)
        with gzip.open(gz, "wb") as fp:
            fp.write(data)
        a = _frames(replay_mod.read_capture(raw), 80, 24)
        b = _frames(replay_mod.read_capture(gz), 80, 24)
        assert a == b, "gzip 투명 해제 결과가 원본 재생과 다르다"
        assert "한글ABC" not in a and "漢字" in a, \
            "CR 덮어쓰기가 재생에 반영돼야 한다(공허 통과 방지)"


#: 이 파일 안에서만 쓰는 글자 상수(위 코퍼스 원문과 섞이지 않게).
NEWLINE = "\n"
CR_LF = "\r\n"

async def test_the_corpus_lines_survive_their_zero_width_characters():
    """골든이 **내용 손실을 얼려 두고 있었다**(pytmux-407 · 2026-08-26).

    ⛔ 해시만 얼리면 «무엇이 얼었는지»를 아무도 안 본다. 이 코퍼스 둘은 폭 0 글자를
    품고 있는데, 종전 서버 격자는 그 글자를 만나면 **그 줄의 나머지를 통째로 버렸다** —
    그래서 이모지 줄이 경고 기호에서 잘린 채 얼어 있었고 해시는 초록이었다.

    그 해시를 다시 구울 때 「무엇이 맞는 그림인가」를 사람이 눈으로 정하는 대신
    **여기 적어 둔다** — 다음에 이 골든이 흔들리면 이 시험이 먼저 운다.
    """
    for key in ("emoji_and_box", "combining_zero_width"):
        line = _frames(_SYNTH[key].encode(), 40, 4).split(NEWLINE)[0]
        assert line.strip(), f"{key}: 아무것도 안 그렸다"
        # 코퍼스 원문에서 **폭 0 글자 뒤에 오는 것**이 살아 있어야 한다.
        tail = _SYNTH[key].rstrip(CR_LF).split()[-1]
        assert tail in line, f"{key}: 폭 0 글자 뒤가 잘렸다({tail!r}): {line!r}"
    # 그리고 변이 선택자 자체를 잃지 않는다(색 이모지의 조건).
    line = _frames(_SYNTH["emoji_and_box"].encode(), 40, 4).split(NEWLINE)[0]
    assert "⚠️" in line, f"변이 선택자를 잃었다: {line!r}"
