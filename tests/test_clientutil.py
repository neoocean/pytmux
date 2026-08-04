"""clientutil 순수 유틸 + 클립보드 정화 테스트(§2.13).

strip_box_drawing 은 OS 네이티브 선택으로 복사된 텍스트의 패널 테두리(박스드로잉)
오염을 타깃 제거하는 순수 함수다. paste-clipboard(Ctrl+V) 텍스트 경로가 이 필터를
strip_box_drawing 토글에 따라 적용/생략하는지도 함께 검증한다."""
import harness  # noqa: F401  (경로 설정)
from harness import make_app, server_only, teardown, wait_until


async def _with_app(coro, size=(100, 30), cfg=None, session=None):
    srv, task, sock = await server_only()
    app = make_app(sock, cfg, session)
    try:
        async with app.run_test(size=size) as pilot:
            await pilot.pause(0.4)
            await coro(app, pilot, srv)
    finally:
        await teardown(srv, task, sock)


async def test_strip_box_drawing_filter():
    """§2.13: OS 네이티브 선택으로 복사된 텍스트의 패널 테두리(박스드로잉)를 타깃 제거.
    줄 끝/앞 테두리 런·테두리 전용 줄만 떼고, 줄 내부 박스드로잉·원래 빈 줄·들여쓰기·
    ASCII 파이프 표·trailing CRLF 는 보존/정리해 일반 붙여넣기에 안전하다."""
    from pytmuxlib.clientutil import strip_box_drawing as s
    # 문제1: 우측 테두리 │ 가 줄 끝에 붙음 → 제거
    assert s("foo │\ntext │") == "foo\ntext"
    # 좌·우 테두리 + 가로 구분선 전용 줄(├──┤) 제거, 데이터만 남김
    assert s("│ left │\n├──────┤"
             "\n│ data │") == "left\ndata"
    # CRLF: trailing \r 도 테두리와 함께 정리
    assert s("code = 1 │\r\nmore │\r") == "code = 1\nmore"
    # 박스드로잉 없으면 무변경(빈 줄·들여쓰기·trailing 공백 보존 — no-op)
    assert s("def foo():\n\n    return 1   ") == "def foo():\n\n    return 1   "
    # markdown ASCII 파이프(U+007C)는 박스드로잉이 아님 → 보존
    assert s("| a | b |\n|---|---|") == "| a | b |\n|---|---|"
    # 줄 내부 박스드로잉(아트)은 보존(끝/앞 런만 대상)
    assert s("a─b─c") == "a─b─c"


def _fold(cmd, pane_w=80, marker="> ", hang=2):
    """앱(Claude Code 등)이 하는 것처럼 낱말 단위로 접어 **화면 줄들**을 만든다 —
    첫 줄은 마커 뒤에서 시작하고 이어지는 줄은 매달림 들여쓰기가 붙는다."""
    rows, cur = [], marker
    for w in cmd.split(" "):
        cand = cur + (" " if cur.strip() and not cur.endswith(" ") else "") + w
        if len(cand) > pane_w:
            rows.append(cur.rstrip())
            cur = " " * hang + w
        else:
            cur = cand
    rows.append(cur.rstrip())
    return rows


def _drag(rows, x0):
    """첫 줄 x0 부터 마지막 줄 끝까지 긁은 선택 텍스트(중간·끝 줄은 열 0 부터 —
    _extract_selection·extract_range 의 규칙)."""
    return "\n".join((r[x0:] if i == 0 else r) for i, r in enumerate(rows))


# 제보의 실물: 긁어 복사해 셸에 바로 붙여넣고 싶은 `!` 명령(공백으로 접힌다).
_BANG_CMD = ("! p4 opened -c 68575 | sed 's/#.*//' | grep -v \"/pytmux/\" | "
             "xargs p4 reopen -c default && p4 opened -c 68575 | "
             "grep -vc \"/pytmux/\"")


async def test_unwrap_copy_text_folds_app_wrapped_lines():
    """copy-unwrap(제보 2026-07-30): Claude Code 화면의 `! …` 명령을 긁어 복사하면 앱이
    스스로 접은 자리마다 개행+매달림 들여쓰기가 섞여 그대로 붙여넣을 수 없었다. 접힘을
    펴 **원문 한 줄로 되돌리는지**(폭·매달림 깊이 전반) + 진짜 줄바꿈은 건드리지 않는지."""
    from pytmuxlib.clientutil import unwrap_copy_text as u
    # ① 왕복: 폭/매달림 들여쓰기를 바꿔 가며 접어도 원문 한 줄로 복원된다.
    for pane_w in (46, 50, 60, 70, 80, 100, 120):
        rows = _fold(_BANG_CMD, pane_w)
        assert u(_drag(rows, 2), pane_w, 2) == _BANG_CMD, (pane_w, rows)
    for hang in (2, 3, 4, 6, 8, 10):
        rows = _fold(_BANG_CMD, 80, marker=" " * hang, hang=hang)
        assert u(_drag(rows, hang), 80, hang) == _BANG_CMD, (hang, rows)
    # ② 현행 입력박스: 위아래 가로줄 구획(─)이 함께 긁혀도 그 줄만 사라진다.
    rows = _fold(_BANG_CMD, 80, marker=" ❯ ", hang=3)
    assert u(_drag(rows, 3) + "\n" + "─" * 44, 80, 3) == _BANG_CMD
    # ②' 지워진 구분선 자리는 **경계**다 — 윗 대화 끝줄과 입력줄이 맞붙어 이어붙으면
    #    안 된다(구분선을 그냥 버리면 아래가 한 줄로 합쳐진다).
    assert u("⏺ 앞 대화의 마지막 줄이 화면 폭 근처까지 길게 이어져 있었다고 하자!!\n"
             "────────────────────────────────\n"
             " ❯ ! echo hi", 68, 0) == (
        "⏺ 앞 대화의 마지막 줄이 화면 폭 근처까지 길게 이어져 있었다고 하자!!\n"
        " ❯ ! echo hi")
    # ③ 옛 모서리 박스(세로 테두리 │)도 안쪽 패딩만 남기고 떨어진다.
    assert u("! echo this command was folded by the app right about here │\n"
             "│   and continues on the next line", 62, 2) == (
        "! echo this command was folded by the app right about here "
        "and continues on the next line")
    # ④ 한 줄 선택은 사용자가 고른 그대로 — 손대지 않는다(공백까지 보존).
    assert u("  ! echo hi  ", 80, 2) == "  ! echo hi  "
    # ⑤ 폭을 모르면(구 경로·정보 없음) 판정 근거가 없어 원문 그대로.
    assert u("aaa\n  bbb", 0, 0) == "aaa\n  bbb"
    # ⑥ 오탐 배터리 — **이어붙이면 안 되는** 진짜 줄바꿈들(줄 수가 그대로여야 한다).
    keep = (
        # 열 0 에서 시작하는 긴 줄들(로그·ls) — 매달림 들여쓰기가 없다.
        "2026-07-30 12:00:01 INFO something happened here in the log detail\n"
        "2026-07-30 12:00:02 INFO short",
        # 코드: 블록을 여는 `:`/`;` 로 끝난 줄은 의도된 줄 끝.
        "    if some_condition and another_condition and yet_another_cond:\n"
        "        do_something()",
        "  const x = computeSomethingVeryLongAndImportant(a, b, c, d, e);\n"
        "    next_statement()",
        # 셸 이어짐(\\) — 그대로 둬야 붙여넣기가 산다.
        "docker run --rm -it -v /very/long/path:/data --name mycontainer \\\n"
        "    ubuntu:24.04",
        # 짧은 블록(YAML 목록) — 애초에 접힐 폭이 아니다.
        "items:\n  - alpha\n  - beta",
        # 빈 줄(문단 경계)을 넘어 잇지 않는다.
        "! echo one two three four five six seven eight nine ten eleven twe\n"
        "\n  indented after blank",
    )
    for t in keep:
        assert u(t, 80, 0).count("\n") == t.count("\n"), t
    # ⑦ 반대로, 낱말 하나가 들어갈 자리도 없던 자리는 이어붙인다(인자 목록 접힘).
    assert u("foo = bar(alpha, beta, gamma, delta, epsilon, zeta, eta, iota,\n"
             "          kappa)", 64, 0) == (
        "foo = bar(alpha, beta, gamma, delta, epsilon, zeta, eta, iota, kappa)")


async def test_mouse_drag_copy_applies_unwrap_per_toggle():
    """호출부 오라클: 마우스 드래그 복사가 **선택 패널의 폭·시작 열로** copy-unwrap 을
    실제로 적용하는지(화면-내 추출 경로 + 서버 추출 `selection` 회신 경로), 그리고
    `set copy-unwrap off` 면 원문 그대로 가는지. 값을 만드는 unwrap_copy_text 만 테스트
    하면 이 호출을 지워도 통과한다(공허 통과 방지 — 뮤테이션에 '호출 제거' 포함)."""
    # 폭 74 패널 · 매달림 8칸(`⏺ Bash(…` 처럼 깊은 들여쓰기). 이 조합은 첫 줄의 **시작
    # 열**까지 넘겨야 접힘 폭 추정이 맞는 자리다 — first_col 을 0 으로 바꾸면 깨진다.
    rows = _fold(_BANG_CMD, 74, marker=" " * 8, hang=8)
    folded = _drag(rows, 8)
    assert "\n" in folded, rows                  # 실제로 접혔는지(전제 확인)

    async def body(app, pilot, srv):
        v = app.view
        app.layout = {"panes": [{"id": 7, "x": 1, "y": 1, "w": 74, "h": 20,
                                 "box": [0, 0, 76, 22], "active": True}],
                      "dividers": [], "active": 7, "cols": 80, "rows": 24}
        app.mode = "normal"
        app.mouse_drag_copy = True
        app.mouse_drag_threshold = 1
        copied = []
        app.copy_text = lambda t: copied.append(t)
        app.send_mouse = lambda pid, data: None
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        v._extract_selection = lambda: folded

        def drag():
            v.on_mouse_down(_MouseEv(9, 2))       # 패널 열 8 에서 시작(= 마커 뒤)
            v.on_mouse_move(_MouseEv(20, 3))
            v.on_mouse_up(_MouseEv(20, 3))

        # (1) on(기본): 드래그를 놓으면 접힘이 펴진 **한 줄**이 클립보드로 간다.
        app.copy_unwrap = True
        drag()
        assert copied == [_BANG_CMD], copied
        # (2) off: 종전 그대로(앱이 접은 개행·들여쓰기 보존).
        copied.clear()
        app.copy_unwrap = False
        drag()
        assert copied == [folded], copied
        # (3) 서버 추출 경로: 선택이 한 화면을 넘을 수 있어 절대 좌표를 알면 서버에
        # copy_range 를 요청한다(회신 `selection`). 회신에는 패널 정보가 없으니 요청 때
        # 폭·시작 열을 남겨야 하고, 그 값으로 펴야 한다 — 기록을 지우면 원문이 나온다.
        copied.clear()
        sent.clear()
        app.copy_unwrap = True
        app.pane_top = {7: 100}                   # 절대 좌표 가능 → copy_range 경로
        app._copy_unwrap_geom = (0, 0)            # 남기는 쪽을 오라클로
        drag()
        assert copied == [], "절대 좌표를 알면 클라가 스스로 복사하지 않는다"
        assert [a for a, _ in sent] == ["copy_range"], sent
        assert app._copy_unwrap_geom == (74, 8), app._copy_unwrap_geom
        app._dispatch({"t": "selection", "text": folded})
        assert copied == [_BANG_CMD], copied

    await _with_app(body, size=(80, 24))


class _MouseEv:
    """on_mouse_* 에 넣는 최소 마우스 이벤트(테스트 로컬)."""

    def __init__(self, x, y, button=1):
        self.x, self.y, self.button = x, y, button
        self.ctrl = self.shift = False

    def stop(self):
        pass


async def test_box_edge_cutters_match_old_regex_and_are_linear():
    """검수 2026-07-30 — 테두리 제거가 줄 길이의 **제곱**이던 것을 선형으로 바꾼 뒤,
    ① 판정이 **옛 정규식과 글자 단위로 동형**인지(차분 오라클) ② 긴 줄에서 실제로
    선형인지(복잡도 오라클)를 함께 고정한다.

    왜 이 결함이 아팠나: `paste-clipboard`(Ctrl+V, 기본 on)는 클립보드 읽기만 to_thread
    이고 **정화는 이벤트 루프에서** 돈다 — 오른쪽을 공백으로 채운 넓은 텍스트를 한 번
    붙여넣으면(스프레드시트·`column`·wide 터미널 복사) 클라가 통째로 멎었다. 실측:
    공백 한 줄 2만 자=1.3초 · 5만 자=8.1초 · **10만 자=32초**. 마우스 복사(copy-unwrap)도
    같은 헬퍼를 쓴다. 옛 정규식을 되살리면 아래 시간 단언이 확실히 깨진다(32초 vs 0.5초).
    """
    import random
    import re
    import time
    from pytmuxlib.clientutil import (_cut_lead_box, _cut_lead_box_pad,
                                      _cut_trail_box, strip_box_drawing)
    # ① 차분 오라클 — **옛 정규식을 그대로** 참조 구현으로 둔다(비-raw 문자열까지 동일).
    old_trail = re.compile("[ \t]*[─-╿]+[ \t]*\r?$")
    old_lead_pad = re.compile("^[─-╿]+[ \t]*")
    old_lead = re.compile("^[ \t]*[─-╿]+")
    fixed = ("", " ", "\t", "\r", "│", "─", "╿", "├──┤", "x", "가",
             "│ x │", " │", "│\r", "│ \r", "│\r\r", "x─y", "  ─  ", "│─│",
             "a │ ", "| a |", "─" * 5, " \t│\t ")
    for s in fixed:
        assert _cut_trail_box(s) == old_trail.sub("", s), repr(s)
        assert _cut_lead_box_pad(s) == old_lead_pad.sub("", s), repr(s)
        assert _cut_lead_box(s) == old_lead.sub("", s), repr(s)
    rnd = random.Random(20260730)          # 결정론(시드 고정)
    alpha = " \t\r│─╿┤├x가|"
    for _ in range(4000):
        s = "".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 12)))
        assert _cut_trail_box(s) == old_trail.sub("", s), repr(s)
        assert _cut_lead_box_pad(s) == old_lead_pad.sub("", s), repr(s)
        assert _cut_lead_box(s) == old_lead.sub("", s), repr(s)
    # ② 복잡도 오라클 — 공백 런이 길어도 선형. 옛 정규식은 여기서 분 단위였다.
    t0 = time.perf_counter()
    strip_box_drawing(" " * 200_000 + "│")
    strip_box_drawing("\n".join(" " * 1000 for _ in range(300)))
    dt = time.perf_counter() - t0
    assert dt < 1.0, f"테두리 제거가 다시 제곱으로 돌아갔다: {dt:.1f}s"


async def test_win_clipboard_utf16_roundtrip_preserves_korean():
    """Windows 클립보드 코드페이지 mojibake 수정(제보 2026-07-13): clip.exe/
    Get-Clipboard 가 stdin/stdout 을 콘솔 코드페이지(cp949)로 해석해 UTF-8 한글이
    '洹몃┝…' 로 깨지던 것을, UTF-16LE→base64(ASCII) 왕복으로 코드페이지 무관하게 한다.

    실 PowerShell/OS 클립보드는 driver 로 못 몰지만, PowerShell 변환은 순수
    인코딩 왕복이라 모델링해 검증한다: 복사 stdin(base64(utf16le)) 을 PowerShell 이
    Set-Clipboard→Get-Clipboard→base64(utf16le) 로 되보내면 붙여넣기 stdout 과 동형이다.
    _win_paste_from_stdout(_win_copy_stdin(t)+CRLF) == t 이면 왕복 무손실이다.
    base64 payload 가 순수 ASCII(코드페이지 무관 증거)인 것도 함께 고정한다."""
    from pytmuxlib.clientclip import _win_copy_stdin, _win_paste_from_stdout
    for t in ("그림자 샤미", "그림자 미니에 = KumquatShadow",
              "1001900111 (그림자 오하나 = MandarinShadow)",
              "한글\n여러 줄\ttab 混在 emoji😀", "plain ascii", ""):
        stdin = _win_copy_stdin(t)
        assert stdin == stdin.decode("ascii").encode("ascii")  # 순수 ASCII
        # PowerShell 왕복 모델(+CRLF: Write-Output 이 붙이는 개행) → 원문 복원
        assert _win_paste_from_stdout(stdin + b"\r\n") == t
    # 빈 stdout(클립보드 비었거나 비-텍스트) → ""
    assert _win_paste_from_stdout(b"") == ""
    assert _win_paste_from_stdout(b"   \r\n") == ""


async def test_paste_clipboard_strips_box_drawing_per_toggle():
    """§2.13: paste-clipboard(텍스트 경로)가 strip_box_drawing 토글에 따라 테두리를
    제거(on, 기본)/보존(off)한다. on_paste(터미널 bracketed)는 대상 아님(이 경로만)."""
    from pytmuxlib import clientclip
    _orig = clientclip.paste

    async def body(app, pilot, srv):
        sent = []
        app.send_cmd = lambda action, **kw: sent.append((action, kw))
        clientclip.paste = lambda: "cmd --flag │\nnext │"
        # on(기본) → 테두리 제거
        app.strip_box_drawing = True
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: sent and sent[-1][0] == "paste")
        assert sent[-1] == ("paste", {"text": "cmd --flag\nnext"}), sent
        # off → 원문 그대로(의도적 박스드로잉 보존)
        sent.clear()
        app.strip_box_drawing = False
        app.paste_os_clipboard()
        await wait_until(pilot, lambda: sent and sent[-1][0] == "paste")
        assert sent[-1] == ("paste",
                            {"text": "cmd --flag │\nnext │"}), sent

    try:
        await _with_app(body)
    finally:
        clientclip.paste = _orig


async def test_wait_until_settled_stall_vs_progress_vs_met():
    """스톨 워치독(로드맵 #3) wait_until_settled 3거동 검증: ① 조건 즉시 충족
    →(True,None) ② 상태가 수렴(불변)인데 조건 미충족 → **타임아웃 전에** (False,진단)
    ③ 상태가 계속 변하며 진행 → timeout 까지 인내 후 (False,_). fake pilot 로 격리."""
    import asyncio
    from harness import wait_until_settled

    class _FakePilot:
        async def pause(self, step):
            await asyncio.sleep(0)

    pilot = _FakePilot()
    # ① 조건 충족.
    ok, diag = await wait_until_settled(pilot, lambda: True, lambda: 0)
    assert ok is True and diag is None

    # ② 스톨(수렴-오답): 상태 불변 + 조건 거짓 → settle 회 후 조기 실패.
    calls = {"n": 0}
    def snap_const():
        calls["n"] += 1
        return "frozen"
    ok, diag = await wait_until_settled(pilot, lambda: False, snap_const,
                                        timeout=100.0, step=0.0, settle=5)
    assert ok is False and diag == repr("frozen")
    assert calls["n"] < 30, ("타임아웃(=매우 큼) 전에 스톨로 조기 반환해야", calls["n"])

    # ③ 진행 중(상태 계속 변함): settle 에 안 걸리고 timeout 까지 인내.
    counter = {"n": 0}
    def snap_changing():
        counter["n"] += 1
        return counter["n"]
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    ok, diag = await wait_until_settled(pilot, lambda: False, snap_changing,
                                        timeout=0.2, step=0.0, settle=5)
    assert ok is False
    assert loop.time() - t0 >= 0.2 - 0.05, "진행 중이면 timeout 까지 인내"


# --------------------------------------------------------------------------- #
# §10-21ⓧ2 패널 글의 경로 범위 — 네이티브 클라와 **한 벌**이어야 한다
# --------------------------------------------------------------------------- #
async def test_find_paths_is_narrow_on_purpose():
    """넓히면 아프다 — 산문 속 `a/b` 나 날짜 `2026/08/02` 도 경로처럼 보인다.

    ★ 이 규칙은 `client/crates/base/src/spans.rs` 와 한 벌이고, 픽스처
    (`client/scripts/gen_spans_fixture.py`)가 이 함수를 직접 불러 대조한다 — 두 클라가
    같은 줄에서 서로 다른 자리를 짚으면 밑줄도 복사한 값도 갈린다."""
    from pytmuxlib.clientutil import find_paths, path_at
    assert find_paths("2026/08/02 에 고쳤다") == [], "날짜를 경로로 잡았다"
    assert find_paths("a/b 를 보라") == [], "확장자가 없다"
    assert find_paths("readme.md 하나") == [], "구분자가 없다"
    got = find_paths("Update(server/test/x.mjs)")
    assert got == [(7, 24, "server/test/x.mjs")], got
    # 감싼 괄호는 범위에 안 든다 — 복사한 값이 그대로 경로라야 한다.
    assert path_at("Update(server/test/x.mjs)", 10)[2] == "server/test/x.mjs"
    assert path_at("Update(server/test/x.mjs)", 0) is None, "괄호 밖"
    # 링크는 링크의 것이다(§10-21ⓥ2 — 그쪽은 GUI 전용이다).
    assert find_paths("https://x.dev/a/b.html") == []


async def test_relative_path_resolves_against_that_pane_cwd():
    """상대경로의 기준은 **hover 한 그 패널**의 cwd 다(pytmux-24 남은 절반).

    ⚠ 활성 패널의 cwd 로 옆 패널 글을 풀면 밑줄은 멀쩡히 그어지고 **복사한 값만**
    틀린다 — 조용한 오답이라 사용자가 의심할 단서가 없다. 그래서 서버가 패널별로
    보내고(`cwd` 프레임) 클라도 패널별로 푼다.

    못 풀면 **존을 안 만든다**: 밑줄을 그어 놓고 눌러도 아무 일이 없으면 그 밑줄이
    거짓말이다(절대경로는 cwd 없이도 풀리므로 계속 눌린다)."""
    import os

    from pytmuxlib.clientwidgets import MultiplexerView

    class FakeApp:
        pane_cwds = {1: os.path.join(os.sep, "a", "one"),
                     2: os.path.join(os.sep, "b", "two")}

    # `app` 은 Textual 의 읽기전용 property 라 인스턴스를 못 만들고 세운다 — 재는 것은
    # 위젯이 아니라 **푸는 규칙**이므로 그 메서드만 떼어 가짜 앱에 물린다.
    class _Probe:
        app = FakeApp()
        _resolve_path = MultiplexerView._resolve_path

    resolve = _Probe()._resolve_path

    assert resolve("x.mjs", 1) == os.path.join(os.sep, "a", "one", "x.mjs")
    assert resolve("x.mjs", 2) == os.path.join(os.sep, "b", "two", "x.mjs"), \
        "다른 패널의 글을 남의 cwd 로 풀었다"
    assert resolve("x.mjs", 3) is None, "cwd 를 모르는 패널은 못 푼다(존 없음)"
    assert resolve("x.mjs", None) is None, "패널을 모르면 못 푼다"
    # ⚠ Windows 에서 앞이 `\` 하나뿐인 경로는 3.13 부터 **절대경로가 아니다**(드라이브가
    # 없다). 그 OS 의 진짜 절대경로를 만들어 쓴다 — 아니면 이 단언이 OS 를 탄다.
    absolute = os.path.abspath(os.path.join(os.sep, "abs", "x.mjs"))
    assert resolve(absolute, 3) == absolute, "절대경로는 cwd 없이도 그대로 풀린다"
