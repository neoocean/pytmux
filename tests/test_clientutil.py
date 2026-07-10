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
