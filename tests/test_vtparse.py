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
from run import skip
from pytmuxlib.model import Pane
from pytmuxlib.nativescreen import NativeScreen
from pytmuxlib.vtparse import VTTokenizer, _sgr_params_from_raw

# pyte 는 M4b(2026-07-18)에 런타임 은퇴했다. 여기선 **선택적 차분 오라클**(독립 VT
# 구현과의 대조 — 공허하지 않음)로만 쓴다: 설치돼 있으면 차분 테스트가 native 파서를
# pyte.ByteStream 과 대조하고, 없으면 그 테스트만 skip 한다(렌더 절대 가드는
# test_vt_parser_equivalence 의 골든해시가 담당). 파서 고유 속성(DoS/DCS/우회 흡수/alt/
# OSC/SGR 단위)은 native 화면만으로 검증하므로 pyte 유무와 무관하게 항상 돈다.
try:
    import pyte
    _HAVE_PYTE = True
except Exception:
    pyte = None
    _HAVE_PYTE = False

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "claude")


def _ref_screen(chunks, cols, rows):
    """차분 기준 경로: pyte.ByteStream(선택적 오라클 — 호출 전 _HAVE_PYTE 확인)."""
    s = pyte.Screen(cols, rows)
    st = pyte.ByteStream(s)
    for ch in chunks:
        st.feed(ch)
    return s


def _tok_screen(chunks, cols, rows, with_alt=False):
    """대상 경로: VTTokenizer → 자작 NativeScreen. with_alt 면 alt_hook 으로 main/alt
    화면을 스왑한다(model.Pane 의 _enter_alt/_leave_alt 등가). 현재 활성 화면 반환."""
    main = NativeScreen(cols, rows)
    state = {"alt": False, "s": None}
    tk = VTTokenizer(main)

    def _hook(enter):
        if enter and not state["alt"]:
            state["s"] = NativeScreen(cols, rows)
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
    if not _HAVE_PYTE:
        skip("pyte 런타임 은퇴(M4b) — 차분 오라클 미설치. 렌더 가드는 골든해시.")
    cols, rows = 40, 6
    cases = {
        "plain": [b"hello\r\nworld\r\n"],
        "clear+addr": [b"\x1b[2J\x1b[3;5HX"],
        "column abs": [b"a\x1b[20Gb"],
        "sgr basic": [b"\x1b[1mBOLD\x1b[0m \x1b[31mRED\x1b[0m"],
        "cursor moves": [b"\x1b[5B\x1b[3Cxyz"],
        "wide cjk": ["가나다ABC".encode()],
        "cr overwrite": [b"AAAA\rBB"],
        # ⚠ DL 이 **빈 줄을 끌어올리는** 갈래는 여기 두지 않는다 — pyte 가 거기서
        #   틀린다(아래 test_delete_lines_pulling_an_empty_row_diverges_from_pyte).
        "ins/del line": [b"row\r\n" * 5 + b"\x1b[2;1H\x1b[2L\x1b[3;1H\x1b[1M"],
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
        if _HAVE_PYTE:      # 선택적 차분: pyte.ByteStream 도 동일 carry
            _assert_same(_ref_screen(chunks, cols, rows), tok, f"mb-split@{cut}")
    # 한 바이트씩 흘려도(최악 경계) 동일.
    onebyte = _tok_screen([raw[i:i + 1] for i in range(len(raw))], cols, rows)
    assert "�" not in onebyte.display, "native 1바이트 스트림에 U+FFFD"
    if _HAVE_PYTE:
        _assert_same(_ref_screen([bytes([b]) for b in raw], cols, rows),
                     onebyte, "mb-split@1byte")


async def test_conformance_corpus_against_pyte():
    """vttest급 경계 케이스 — 스크롤영역/원점모드/탭스톱/문자셋/soft-reset/삽입모드를
    pyte.ByteStream 과 차분 비교(선택적 오라클). 파서가 올바른 인자로 디스패치하면
    독립 구현(pyte)과 동일해야 한다. pyte 미설치면 skip(렌더 가드는 골든해시)."""
    if not _HAVE_PYTE:
        skip("pyte 런타임 은퇴(M4b) — 차분 오라클 미설치. 렌더 가드는 골든해시.")
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


# ── 미종결 CSI 의 «기울기»를 재는 자 (pytmux-433) ─────────────────────────────
# ⛔ 절대 시간 문턱(옛 `dt < 2.0`)은 쓰지 않는다. 이 편은 수 MB 를 먹이므로 **회귀가
# 없어도** 부하가 있는 상자에서 그 문턱을 넘는다(2026-09-01 실측 · macOS 부하 ~13 에서
# 격리 실행 3.20s · 그 트리의 파이썬 코드는 depot HEAD 와 바이트가 같았다). 그때 크기를
# 바꿔 가며 재 보니 배가마다 대략 2배 — **선형이고 절대 시간이 넘었을 뿐**이었다.
# 그래서 「느린 상자」와 「O(n²) 회귀」를 가르는 축(= 기울기)으로 잰다: 선형이면 ×2,
# 이차면 ×4. 형제 편 test_csi_partial_no_quadratic 이 이미 쓰는 축이다.
# ⚠ 기울기 가드는 느린 상자를 통과시키는 것이 목적이라 **통과만 봐서는 「아무것도 안
# 재는 상태」와 구별이 안 된다** — 그래서 양성 대조군을 아래에 함께 둔다.
_CSI_GROWTH_MAX = 3.0
_CSI_GROWTH_N = 1_000_000


def _feed_unterminated_csi(sink, n):
    """종결자 **없는** `ESC[` + `1`×n 을 64KB 청크로 먹인다."""
    big = b"[" + b"1" * n
    for i in range(0, len(big), 65536):
        sink.feed(big[i:i + 65536])


def _csi_growth(make_sink, n=_CSI_GROWTH_N, attempts=2):
    """t(2n)/t(n) 과 **2n 회차의 sink** 를 돌려준다.

    부하 스파이크가 두 표본 중 한 쪽만 때리면 비가 흔들린다. 그래서 문턱을 넘은
    회차만 한 번 더 재고 **작은 비**를 쓴다 — 부하는 시간을 늘리기만 하므로 여러
    표본의 최솟값이 곧 「가장 덜 방해받은 표본」이다. 통과하는 회차는 한 번만 재므로
    값이 안 비싸다(실측 이 상자 1M+2M = 0.75s).
    """
    _feed_unterminated_csi(make_sink(), 100_000)     # 예열 — 첫 표본은 늘 부풀어 있다
    best, sink = None, None
    for _ in range(attempts):
        s1 = make_sink()
        t0 = time.perf_counter()
        _feed_unterminated_csi(s1, n)
        t1 = time.perf_counter() - t0
        s2 = make_sink()
        t0 = time.perf_counter()
        _feed_unterminated_csi(s2, 2 * n)
        t2 = time.perf_counter() - t0
        ratio = t2 / max(t1, 1e-6)
        if best is None or ratio < best:
            best, sink = ratio, s2
        if best < _CSI_GROWTH_MAX:
            break
    return best, sink


async def test_csi_raw_param_buffer_bounded():
    """[보안 F2 회귀, 2026-07-17] 미종결 CSI 파라미터 본문(`_raw`)은 _RAW_MAX 로 캡된다.

    N1(OSC)의 **살아남은 형제**: `_OSC_MAX` 만 있고 `_raw` 엔 상한이 없어, 종결자 없는
    `ESC[` + 숫자 스트림이 ① `self._raw += ch` 의 O(n²) 로 CPU 를 태우고(400k자=1.15s,
    10MB=120초+) ② **종결자가 없어도 되므로** feed 를 넘어 본문이 영구 잔류해 메모리
    DoS 가 됐다. 캡은 자원 상한과 O(n) 을 동시에 준다.

    ⇒ 그래서 여기서 재는 것도 둘이다: **기울기**(O(n) 인가 — 위 _csi_growth 주석)와
    **캡**(`_raw` 가 상한 안인가). 앞엣것만 있으면 자원 누수를 놓치고, 뒤엣것만 있으면
    O(n²) 를 놓친다.
    """
    cols, rows = 20, 3
    ratio, tok = _csi_growth(lambda: VTTokenizer(NativeScreen(cols, rows)))
    assert ratio < _CSI_GROWTH_MAX, (
        f"미종결 CSI 입력 2배에 시간 {ratio:.1f}배 — O(n²) 회귀(F2)")
    assert len(tok._raw) <= VTTokenizer._RAW_MAX, f"_raw 미캡 {len(tok._raw)}"
    # 캡 뒤에도 파서는 정상 복귀한다: 종결자를 만나면 시퀀스를 닫고 이후 글자는 출력.
    # (캡된 파라미터가 거대 행번호로 해석돼 커서는 마지막 행에 클램프되므로 행을
    # 특정하지 않고 화면 어딘가에 찍혔는지만 본다 — 요지는 "파서가 안 죽었다"이다.)
    tok.feed(b"Htail")
    assert any("tail" in row for row in tok.screen.display), repr(tok.screen.display)


async def test_csi_growth_guard_bites_on_quadratic():
    """양성 대조군 — 위 기울기 가드가 **진짜 O(n²) 에 실제로 문다**(pytmux-433).

    옛 F2 결함의 모양을 그대로 세운다: 상한 없는 `self._raw += ch`. 속성이 참조를
    쥐고 있어 CPython 의 「refcount 1 이면 제자리에서 잇는다」 최적화가 **안 걸리므로**
    이것은 실제로 이차다(이 상자 실측 50k=0.026s · 100k=0.098s · 200k=0.532s).
    같은 자(_csi_growth)로 재서 문턱을 넘는지 본다.
    """
    class _QuadraticSink:
        def __init__(self):
            self._raw = ""

        def feed(self, data):
            for b in data:
                self._raw += chr(b)

    # ⚠ 이차라 크기를 키우면 초가 아니라 분이 된다 — 100k/200k 로 잰다(합 0.6s 대).
    ratio, _ = _csi_growth(_QuadraticSink, n=100_000, attempts=1)
    assert ratio >= _CSI_GROWTH_MAX, f"O(n²) 를 {ratio:.1f}배로 재 — 가드가 안 문다"


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


# ── (+) 캡처 픽스처 재생 동등성(선택적 차분 오라클) ──────────────────────────
async def test_capture_fixtures_match_pyte():
    """실제 캡처(claude/*.txt)를 native·pyte 양 경로로 재생해 화면이 동일(독립 구현
    대조). pyte 미설치면 skip — 캡처 렌더 절대 가드는 골든해시(test_vt_parser_
    equivalence)가 담당한다."""
    if not _HAVE_PYTE:
        skip("pyte 런타임 은퇴(M4b) — 차분 오라클 미설치. 렌더 가드는 골든해시.")
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

# ── 문자소 군집 (pytmux-407) ──────────────────────────────────────────────────
async def test_a_variation_selector_does_not_swallow_the_rest_of_the_line():
    """폭 0 글자 하나가 **그 줄의 나머지를 통째로 버리던** 자리(pytmux-407).

    ⛔ 색 이야기가 아니라 **내용 손실**이다. 실측(2026-08-26) 그대로:
    `|A⚠️B| tail` → `|A⚠` — 뒤따르는 멀쩡한 글자가 전부 사라졌다.

    뿌리는 `unicodedata.combining()` 이 「결합 문자인가」가 아니라 **결합 «클래스»**를
    돌려준다는 것이었다. 변이 선택자(U+FE0F)와 ZWJ(U+200D)는 클래스가 **0** 이라
    그 갈래를 못 타고 `else: break` 로 떨어졌고, `break` 는 남은 데이터를 버린다.
    """
    from pytmuxlib.model import Pane

    pane = Pane(-1, -1, 20, 3, vt_parser="native")
    pane.feed("|A⚠️B| tail".encode("utf-8"))
    line = pane._main.buffer[0]
    text = pane._serialize_row(line, 20)[0][0]
    assert "B| tail" in text, f"선택자 뒤가 잘렸다: {text!r}"
    # 그리고 그 글자는 **앞 글자에 얹혀** 한 셀에 있다(칸을 안 먹는다).
    assert line[2].data == "⚠️", repr(line[2].data)
    assert line[3].data == "B", "선택자가 제 칸을 먹었다"


async def test_an_unprintable_char_is_skipped_not_a_truncation():
    """⛔ 대조군 — 못 찍는 글자 하나로도 줄을 버리면 안 된다.

    폭이 음수인 글자는 얹히지도 않는다(범주가 표시·서식이 아니다). 그때도 **건너뛸 뿐**
    스트림을 끊지 않는 것이 실제 단말의 손이다.
    """
    from pytmuxlib.model import Pane

    pane = Pane(-1, -1, 20, 3, vt_parser="native")
    pane.feed("abcd".encode("utf-8"))     # BEL 은 파서가 먹지만 폭도 없다
    text = pane._serialize_row(pane._main.buffer[0], 20)[0][0]
    assert "cd" in text, f"못 찍는 글자 뒤가 잘렸다: {text!r}"


async def test_a_combining_accent_still_composes_the_way_it_always_did():
    """⛔ 두 번째 대조군 — 종전에 되던 것(악센트 결합)이 그대로여야 한다.

    범주로 판정을 바꾸면서 결합 클래스가 큰 글자(U+0301 = 230)를 놓치면, 이 슬라이스가
    고친 것보다 많은 것을 깨뜨린 셈이 된다.
    """
    from pytmuxlib.model import Pane

    pane = Pane(-1, -1, 20, 3, vt_parser="native")
    pane.feed("éx".encode("utf-8"))
    line = pane._main.buffer[0]
    assert line[0].data == "é", repr(line[0].data)   # NFC 로 합쳐진다
    assert line[1].data == "x", "악센트가 칸을 먹었다"


# ── pytmux-495: 폭 2 문자 쌍의 격자 불변식 ──────────────────────────────────
# 격자는 폭 2 글자를 「앞칸 = 글자 · 뒤칸 = 빈 data」 두 셀로 들고, 직렬화
# (`Pane._serialize_row`)는 **빈 data 를 건너뛰어** 그 쌍을 복원한다. 쌍의 한쪽만
# 덮이면 반쪽이 고아로 남아 그 줄의 렌더 폭이 격자 폭과 달라지고 뒤가 한 칸씩 밀린다 —
# 제보(2026-09-14)의 「pytmux 안 Claude Code 글자 겹침·중복·깨짐」이 그것이다.

_W = "가나다"          # 폭 2 셋


def _row_cells(screen, y, cols):
    return [screen.buffer[y][x].data for x in range(cols)]


def _render_width(screen, y, cols):
    """`_serialize_row` 와 **같은 규칙**으로 그 행이 실제로 차지하는 칸 수."""
    from pytmuxlib.cellwidth import char_cells
    w = 0
    for data in _row_cells(screen, y, cols):
        if data == "":
            continue                     # 뒤칸 — 앞칸이 이미 두 칸을 센다
        w += char_cells(data[0]) if data else 1
    return w


def _feed(cols, *chunks):
    s = NativeScreen(cols, 3)
    t = VTTokenizer(s)
    for c in chunks:
        t.feed(c if isinstance(c, bytes) else c.encode("utf-8"))
    return s


async def test_wide_pair_survives_every_cell_mutation():
    """⛔ 어떤 조작을 해도 **행의 렌더 폭 == 격자 열 수**여야 한다(pytmux-495).

    종전엔 쌍의 한쪽만 덮는 조작마다 폭이 11/13 으로 어긋났다(격자 12).
    """
    cases = {
        "앞칸을 좁은 글자로 덮기": (_W, b"\x1b[1;1HA"),
        "뒤칸을 좁은 글자로 덮기": (_W, b"\x1b[1;2HB"),
        "쌍 한가운데부터 EL(0)": (_W, b"\x1b[1;4H\x1b[K"),
        "쌍 한가운데까지 EL(1)": (_W, b"\x1b[1;4H\x1b[1K"),
        "쌍 한가운데부터 ECH": (_W, b"\x1b[1;2H\x1b[3X"),
        "쌍 한가운데에서 DCH": ("가나다라마", b"\x1b[1;2H\x1b[3P"),
        "쌍 한가운데에서 ICH": (_W, b"\x1b[1;2H\x1b[3@"),
        "좁은 글자 위에 폭 2 겹쳐쓰기": ("abcdefghijkl", b"\x1b[1;3H", "한글"),
    }
    for label, chunks in cases.items():
        s = _feed(12, *chunks)
        got = _render_width(s, 0, 12)
        assert got == 12, (
            f"{label}: 렌더 폭 {got} != 격자 12 — 고아 반쪽이 남았다 "
            f"{_row_cells(s, 0, 12)!r}")


async def test_wide_char_that_does_not_fit_wraps_instead_of_straddling():
    """마지막 한 칸에는 폭 2 글자가 앉지 않는다 — 다음 줄로 접는다(pytmux-495).

    종전엔 뒤칸 없이 앉아 그 줄만 한 칸 넘쳤다(홀수 폭 패널에서 상시).
    """
    s = _feed(7, _W + "가")
    assert _render_width(s, 0, 7) == 7, _row_cells(s, 0, 7)
    assert s.buffer[1][0].data == "가", "넘친 글자가 다음 줄로 안 갔다"


async def test_ink_style_repaint_over_hangul_leaves_no_orphan_glyph():
    """제보 재현 — 한글 줄을 Ink 식으로 다시 그리면 옛 글자가 박혀 남았다.

    Claude Code(Ink)는 커서를 올려 덮어쓰고 `ESC[K` 로 꼬리를 지운다. 한글이 섞이면 그
    경계가 폭 2 글자 한가운데에 떨어져, 종전엔 `Ran 4 shell commands스` 처럼 옛 줄의
    반쪽이 남았다(스크린샷의 `미결a사항은r빼고` · `관계름AND절소속` 과 같은 결함).
    """
    cols = 62
    old = "● Good — 읽은 문서: 스킬시스템 팀 설정 통합 제안입니다."
    new = "Ran 4 shell commands"
    s = _feed(cols, old, b"\x1b[1;1H", new, b"\x1b[1;22H\x1b[K")
    shown = "".join(d for d in _row_cells(s, 0, cols) if d != "").rstrip()
    assert shown == new, f"옛 줄의 고아 글자가 남았다: {shown!r}"
    assert _render_width(s, 0, cols) == cols


async def test_grid_width_invariant_under_random_mutation():
    """무작위 조작 조합에도 격자 불변식이 버틴다(pytmux-495 속성 시험).

    단위 사례는 내가 떠올린 경계만 덮는다 — ICH→DCH 처럼 **두 조작이 엮일 때만** 나던
    갈래는 이 시험이 잡았다(격자 밖 열에 셀을 쌓던 손).
    """
    import random
    chars = "가나다라abc한글x 一二"
    rnd = random.Random(20260914)
    for _ in range(3000):
        cols = rnd.randint(4, 24)
        ops = []
        for _ in range(rnd.randint(1, 14)):
            k = rnd.random()
            if k < .42:
                ops.append("".join(rnd.choice(chars)
                                   for _ in range(rnd.randint(1, 12))))
            elif k < .56:
                ops.append(f"\x1b[{rnd.randint(1, 3)};{rnd.randint(1, cols)}H")
            elif k < .66:
                ops.append(f"\x1b[{rnd.randint(0, 2)}K")
            elif k < .74:
                ops.append(f"\x1b[{rnd.randint(1, 8)}X")
            elif k < .82:
                ops.append(f"\x1b[{rnd.randint(1, 8)}P")
            elif k < .90:
                ops.append(f"\x1b[{rnd.randint(1, 8)}@")
            else:
                ops.append(f"\x1b[{rnd.randint(0, 2)}J")
        s = _feed(cols, *ops)
        for y in range(3):
            got = _render_width(s, y, cols)
            assert got == cols, (
                f"cols={cols} row={y} 렌더 폭 {got} — 조작 {ops!r} "
                f"셀 {_row_cells(s, y, cols)!r}")


async def test_resize_narrower_does_not_orphan_a_wide_lead():
    """열을 줄여 뒤칸이 잘려 나가면 앞칸도 함께 지운다(pytmux-495)."""
    s = _feed(12, _W)
    s.resize(columns=5)            # '다' 의 뒤칸(5열)이 잘린다
    assert _render_width(s, 0, 5) == 5, _row_cells(s, 0, 5)


# ── pytmux-506: 밀고 당기는 조작은 경계에서 «시작하는» 쌍을 안 부순다 ──────────
#
# pytmux-495 는 셀을 건드리는 경로마다 경계의 폭 2 쌍을 끊게 했는데, ICH/DCH 는
# **덮어쓰는** 조작이 아니라 **미는/당기는** 조작이라 경계에서 시작하는 쌍은 통째로
# 옮겨져 온전히 살아남아야 한다. 종전엔 그것까지 공백으로 부숴 글자를 잃었다.
# 아래 기대값은 전부 **tmux 3.6a 실측**이다(같은 바이트열을 같은 폭의 tmux 패널에
# 먹이고 capture-pane 으로 읽었다).

def _feed_rows(cols, rows, *chunks):
    s = NativeScreen(cols, rows)
    t = VTTokenizer(s)
    for c in chunks:
        t.feed(c if isinstance(c, bytes) else c.encode("utf-8"))
    return s


def _shown(screen, y, cols):
    """그 행이 클라에 보내지는 대로 — 뒤칸(빈 data)은 건너뛴다(`_serialize_row`)."""
    return "".join(d for d in _row_cells(screen, y, cols) if d != "").rstrip()


async def test_push_and_pull_keep_a_wide_pair_that_starts_at_the_boundary():
    """ICH/DCH·IRM 삽입은 경계에서 시작하는 폭 2 쌍을 **밀거나 당길 뿐** 안 부순다.

    tmux 3.6a 대조 실측(2026-09-14). 종전(pytmux-495 수정 직후)엔 가드 한 줄을 뺀
    넷이 글자를 잃어 사용자 화면의 좌우가 계속 깨졌다 — 제보 「여전히 화면 좌우측에
    깨지는 부분이…」.
    """
    cases = {
        # 라벨: (열, 바이트열, tmux 가 보여준 첫 행)
        "ICH 가 앞칸 위 — 쌍이 통째로 밀린다":
            (10, ["가나", b"\x1b[1G\x1b[3@"], "   가나"),
        "DCH 가 앞칸 위 — 그 쌍만 지우고 뒤를 당긴다":
            (10, ["가나다", b"\x1b[3G\x1b[2P"], "가다"),
        "ICH 로 오른쪽이 밀려 나가도 남는 쌍은 온전하다":
            (6, ["가나다", b"\x1b[1G\x1b[2@"], "  가나"),
        "IRM 삽입 — 폭 2 글자 위에 폭 1":
            (18, [b"\x1b[4h", "나", b"\r", "b"], "b나"),
        "IRM 삽입 — 앞칸 위에 폭 1(뒤가 통째로 밀린다)":
            (12, ["가나다", b"\x1b[3G\x1b[4h", "Z"], "가Z나다"),
        # ↓ 이 줄은 **원래 멀쩡했다**(끊을 쌍이 없다) — 회귀 가드로만 둔다.
        "IRM 삽입 — 폭 1 줄 위에 폭 2(가드)":
            (10, ["abcdef", b"\x1b[1G\x1b[4h", "가"], "가abcdef"),
    }
    for label, (cols, chunks, want) in cases.items():
        s = _feed_rows(cols, 3, *chunks)
        got = _shown(s, 0, cols)
        assert got == want, f"{label}: {got!r} != tmux {want!r}"
        assert _render_width(s, 0, cols) == cols, _row_cells(s, 0, cols)


async def test_a_pair_straddling_the_boundary_is_still_broken():
    """반대쪽 규약은 그대로다 — 경계를 **가로지르는** 쌍은 끊는다(pytmux-495).

    끊지 않으면 반쪽이 고아로 남고, 직렬화가 그 칸을 건너뛰어 뒤가 한 칸씩 밀린다
    (클라에 가는 것은 셀이 아니라 **런**이라 tmux 처럼 「그리지 않고 넘기기」를 못 한다).
    """
    s = _feed_rows(10, 3, "가나", b"\x1b[2G\x1b[3@")   # ICH 가 '가' 한가운데
    assert _shown(s, 0, 10) == "     나", _row_cells(s, 0, 10)
    assert _render_width(s, 0, 10) == 10


async def test_delete_lines_blanks_the_row_when_the_source_row_is_empty():
    """DL 은 끌어올릴 원본 행이 **빈 줄**이어도 대상 행을 비운다(pytmux-506).

    `buffer` 는 희소 defaultdict 라 한 번도 안 그린 줄은 키가 없다. 종전엔 그때
    아무것도 안 해서 지운 줄이 화면에 **그대로 남았다**(폭 2 와 무관 — ASCII 도 같다).
    """
    for text in ("가나", "a"):
        s = _feed_rows(10, 4, text, b"\x1b[1M")
        assert _shown(s, 0, 10) == "", f"{text!r}: 지운 줄이 남았다 {_row_cells(s, 0, 10)!r}"
    # 원본 행이 있을 때의 정상 경로는 그대로다.
    s = _feed_rows(10, 4, "가나\r\n다라", b"\x1b[1;1H\x1b[1M")
    assert _shown(s, 0, 10) == "다라", _row_cells(s, 0, 10)
    assert _shown(s, 1, 10) == ""


async def test_delete_lines_pulling_an_empty_row_diverges_from_pyte():
    """DL 이 **빈 줄**을 끌어올리는 갈래는 pyte 와 갈린다 — 우리가 맞다(pytmux-506).

    pyte 의 `delete_lines` 는 `if y + count in self.buffer` 로 원본 행을 **있을 때만**
    옮긴다. 화면 버퍼가 희소 dict 라 한 번도 안 그린 줄은 키가 없고, 그때 대상 행은
    옛 내용 그대로 남는다 — 지운 줄이 화면에 잔상으로 선다. nativescreen 이 그 관용구를
    물려받았던 자리다.

    tmux 3.6a 대조 실측(2026-09-14): 아래 바이트열의 첫 행은 **빈 줄**이다.
    """
    if not _HAVE_PYTE:
        skip("pyte 미설치 — 이 시험은 pyte 와의 «의도적 갈림»을 못박는 자리다.")
    cols, rows = 40, 6
    chunks = [b"row\r\n" * 5 + b"\x1b[2;1H\x1b[2L\x1b[1;1H\x1b[1M"]
    tok = _tok_screen(chunks, cols, rows)
    ref = _ref_screen(chunks, cols, rows)
    want = ["", "", "row", "row", "row", ""]          # = tmux 3.6a
    assert [d.rstrip() for d in tok.display] == want, tok.display
    assert ref.display[0].rstrip() == "row", (
        "pyte 가 고쳐졌다면 이 시험과 위 차분 코퍼스를 함께 되돌려라")


# ── 내용 차분 속성 시험(pytmux-506) ─────────────────────────────────────────
#
# ⛔ 왜 또 만드나 — pytmux-495 가 세운 「행의 렌더 폭 == 격자 열 수」 불변식은 이번
#    결함을 **못 잡는다**: ICH/DCH 가 쌍을 공백 둘로 바꿔도 폭은 그대로 12 다. 폭은
#    맞고 **내용**이 틀린 갈래라, 오라클도 내용을 봐야 한다.
#
# 참조는 「셀은 글자 아니면 뒤칸」이라는 가장 단순한 모델이다. 기대값은 tmux 3.6a
# 실측으로 맞췄다(경계에서 시작하는 쌍은 옮기고, 가로지르는 쌍만 끊는다).

# 폭 판정은 격자와 **같은 SSOT**(cellwidth)를 쓴다 — 여기서 재는 것은 폭 분류가
# 아니라 **배치**다(폭까지 새로 쓰면 무엇이 틀렸는지 안 갈린다).
from pytmuxlib.vtconst import wcwidth as _wcwidth   # noqa: E402

_CONT = object()


class _RefGrid:
    """차분용 독립 격자 — nativescreen 과 **공유 코드가 없다**."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.g = [[" "] * cols for _ in range(rows)]
        self.x = self.y = 0

    # ★ 「펜딩 랩」 — 줄을 꽉 채우면 커서는 열 밖(`x == cols`)에 선다. 그 상태의
    #   셀 조작은 **아무 일도 안 한다**(가리키는 칸이 없다). tmux 3.6a 실측:
    #   18칸을 꽉 채운 뒤 ICH/DCH/ECH/EL(0) 넷 다 줄이 그대로였고 EL(1) 만 줄을
    #   통째로 지웠다(그쪽은 범위가 [0, cols) 로 잘려 온 줄을 덮기 때문이다).
    def _cx(self):
        return self.x

    def _clear(self, lo, hi):
        """[lo,hi) 를 공백으로 — 양 경계를 **가로지르는** 쌍은 통째로 끊는다."""
        row = self.g[self.y]
        lo, hi = max(0, lo), min(hi, self.cols)
        if lo >= hi:
            return
        if row[lo] is _CONT and lo:
            row[lo - 1] = " "
        if hi < self.cols and row[hi] is _CONT:
            row[hi] = " "
        for x in range(lo, hi):
            row[x] = " "

    def _fix_tail(self, row):
        last = row[self.cols - 1]
        if last is not _CONT and _wcwidth(last) == 2:
            row[self.cols - 1] = " "       # 뒤칸을 잃은 앞칸은 못 앉는다
        return row

    def _cut_straddle(self, row, x):
        if row[x] is _CONT and x:
            row[x - 1] = row[x] = " "

    def put(self, ch):
        w = _wcwidth(ch)
        if w < 1:
            return
        if self.x + w > self.cols:
            self.x = 0
            self.lf()
        self._clear(self.x, self.x + w)
        self.g[self.y][self.x] = ch
        if w == 2:
            self.g[self.y][self.x + 1] = _CONT
        self.x = min(self.x + w, self.cols)

    def lf(self):
        if self.y == self.rows - 1:
            self.g.pop(0)
            self.g.append([" "] * self.cols)
        else:
            self.y += 1

    def cr(self):
        self.x = 0

    def cup(self, y, x):
        self.y = max(0, min(y, self.rows - 1))
        self.x = max(0, min(x, self.cols - 1))

    def el(self, how):
        x = self._cx()
        self._clear(*((x, self.cols) if how == 0 else
                      (0, x + 1) if how == 1 else (0, self.cols)))

    def ech(self, n):
        self._clear(self._cx(), self._cx() + n)

    def ich(self, n):
        x, row = self._cx(), self.g[self.y]
        if x >= self.cols:
            return                          # 펜딩 랩 — 가리키는 칸이 없다
        self._cut_straddle(row, x)
        self.g[self.y] = self._fix_tail(
            (row[:x] + [" "] * n + row[x:])[:self.cols])

    def dch(self, n):
        x, row = self._cx(), self.g[self.y]
        if x >= self.cols:
            return                          # 펜딩 랩 — 가리키는 칸이 없다
        self._cut_straddle(row, x)
        end = min(x + n, self.cols)
        if end < self.cols and row[end] is _CONT:
            row[end] = " "
        self.g[self.y] = self._fix_tail(
            (row[:x] + row[end:] + [" "] * (end - x))[:self.cols])

    def dl(self, n):
        for _ in range(min(n, self.rows - self.y)):
            self.g.pop(self.y)
            self.g.append([" "] * self.cols)
        self.x = 0

    def il(self, n):
        for _ in range(min(n, self.rows - self.y)):
            self.g.pop(self.rows - 1)
            self.g.insert(self.y, [" "] * self.cols)
        self.x = 0

    def text(self, y):
        return "".join(c for c in self.g[y] if c is not _CONT)


def _ref_ops(rnd, cols, rows):
    """(바이트열, 참조에 먹일 콜러블) 쌍 하나."""
    k = rnd.random()
    if k < .40:
        txt = "".join(rnd.choice("가나다라abc한글x 一二")
                      for _ in range(rnd.randint(1, 14)))
        return txt.encode(), lambda r: [r.put(c) for c in txt]
    if k < .52:
        y, x = rnd.randint(0, rows - 1), rnd.randint(0, cols - 1)
        return (f"\x1b[{y + 1};{x + 1}H".encode(), lambda r: r.cup(y, x))
    if k < .60:
        n = rnd.randint(0, 2)
        return f"\x1b[{n}K".encode(), lambda r: r.el(n)
    if k < .68:
        n = rnd.randint(1, 8)
        return f"\x1b[{n}X".encode(), lambda r: r.ech(n)
    if k < .78:
        n = rnd.randint(1, 8)
        return f"\x1b[{n}P".encode(), lambda r: r.dch(n)
    if k < .88:
        n = rnd.randint(1, 8)
        return f"\x1b[{n}@".encode(), lambda r: r.ich(n)
    if k < .92:
        n = rnd.randint(1, 2)
        return f"\x1b[{n}M".encode(), lambda r: r.dl(n)
    if k < .96:
        n = rnd.randint(1, 2)
        return f"\x1b[{n}L".encode(), lambda r: r.il(n)
    if k < .98:
        return b"\r", lambda r: r.cr()
    return b"\n", lambda r: r.lf()


async def test_grid_content_matches_an_independent_model():
    """무작위 조작에도 **보이는 내용**이 독립 참조와 같다(pytmux-506 속성 시험).

    폭 불변식만 보던 종전 오라클은 ICH/DCH 가 폭 2 쌍을 공백 둘로 **바꿔치기**해도
    통과했다 — 사용자 화면에서는 글자가 사라지는데. 여기서는 행이 보여주는 글자열을
    통째로 맞춘다.
    """
    import random
    rnd = random.Random(20260914)
    for _ in range(1200):
        cols, rows = rnd.randint(4, 20), rnd.randint(2, 5)
        s = NativeScreen(cols, rows)
        tk = VTTokenizer(s)
        ref = _RefGrid(cols, rows)
        trace = []
        for _ in range(rnd.randint(1, 16)):
            raw, run_ref = _ref_ops(rnd, cols, rows)
            trace.append(raw)
            tk.feed(raw)
            run_ref(ref)
        for y in range(rows):
            got = "".join(d for d in _row_cells(s, y, cols) if d != "")
            assert got == ref.text(y), (
                f"cols={cols} row={y}\n  native={got!r}\n  참조  ={ref.text(y)!r}"
                f"\n  조작={b''.join(trace)!r}")


async def test_ed3_clears_the_scrollback():
    """`ESC[3J`(ED 3)는 화면뿐 아니라 **스크롤백까지** 지운다(pytmux-510).

    `/clear` 류가 ED 2 와 **함께** 보내는 것이 이 시퀀스다. 종전엔 `_NativeBase` 가
    2 와 3 을 같게 다뤄 뷰포트만 비고 history 는 그대로였다 — 「지웠는데 위로 올리면
    옛 대화가 그대로」.

    되돌리면 실패해야 하는 오라클:
      · `erase_in_display` 재정의를 지우면 → ①이 실패
      · `how != 3` 가드를 없애 ED 2 도 비우게 하면 → ②가 실패
      · `on_history_cleared` 호출을 지우면 → ③이 실패
    """
    from pytmuxlib.nativescreen import NativeScrollbackScreen

    def filled(rows=4, cols=20):
        s = NativeScrollbackScreen(cols, rows, history=100, ratio=0.5)
        tk = VTTokenizer(s)
        for i in range(rows + 3):        # 화면 높이보다 많이 써서 위로 밀어낸다
            tk.feed(("line%d\r\n" % i).encode())
        return s, tk

    # ① ED 3 은 스크롤백을 비운다.
    s, tk = filled()
    assert len(s.history.top) > 0, "선행조건: 스크롤백이 쌓여 있어야 한다"
    tk.feed(b"\x1b[3J")
    assert len(s.history.top) == 0, (
        "ED 3 인데 스크롤백이 남았다: %d줄" % len(s.history.top))

    # ② ED 2 는 화면만 비운다(스크롤백은 남는다) — 대조군.
    s, tk = filled()
    before = len(s.history.top)
    tk.feed(b"\x1b[2J")
    assert len(s.history.top) == before, (
        "ED 2 가 스크롤백까지 비웠다 %d → %d" % (before, len(s.history.top)))
    assert all(not row.strip() for row in s.display), "ED 2 는 화면을 비운다"

    # ③ 비웠으면 패널에 알린다(스크롤 위치·검색 매치가 절대 인덱스라 함께 되돌려야).
    s, tk = filled()
    called = []
    s.on_history_cleared = lambda: called.append(1)
    tk.feed(b"\x1b[3J")
    assert called == [1], "스크롤백을 비웠는데 on_history_cleared 를 안 불렀다"
    # 비울 것이 없으면 훅도 안 부른다(공회전 방지).
    tk.feed(b"\x1b[3J")
    assert called == [1], "빈 스크롤백에 ED 3 — 훅을 또 불렀다"


async def test_pane_resets_scroll_when_app_clears_the_scrollback():
    """호출부 오라클(pytmux-510): 훅을 **실제로 꽂았고** 패널이 좌표계를 되돌린다.

    ⛔ 화면 모델만 시험하면 `_make_main_screen` 에서 `on_history_cleared = …` 한 줄을
    지워도 통과한다(공허 통과 — CLAUDE.md §표시 기능은 호출부까지 단언).
    """
    p = Pane(-1, -1, 20, 4, vt_parser="native")
    for i in range(8):
        p.feed(("line%d\r\n" % i).encode())
    assert len(p.screen.history.top) > 0, "선행조건: 스크롤백"
    p.scroll = 3
    p._match_abs = 2
    p.feed(b"\x1b[3J")
    assert len(p.screen.history.top) == 0, "패널 경로에서도 스크롤백이 비어야 한다"
    assert p.scroll == 0, "스크롤백이 사라졌는데 scroll 이 %d 로 남았다" % p.scroll
    assert p._match_abs is None, "검색 매치의 절대 인덱스가 남았다"
