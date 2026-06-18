#!/usr/bin/env python3
"""매뉴얼용 실제 스크린샷 생성기 — docs/internal/SCREENSHOT_SCENARIO.md 방식 ①(Textual SVG).

진짜 서버(임시 소켓)를 띄우고 **실제 클라이언트 앱**(`build_client_app`)을 Textual
헤드리스(`run_test`)로 운전해, 각 장면을 클라가 실제로 그리는 그대로 **SVG** 로 떠
`docs/image/` 에 저장한다. 위젯 상태 단언이 아니라 사용자가 보는 화면 그 자체다.

  python3 scripts/gen_screenshots.py            # 결정적 장면 전체 → docs/image/*.svg
  python3 scripts/gen_screenshots.py 02-split   # 이름에 매칭되는 결정적 장면만
  python3 scripts/gen_screenshots.py claude-suite  # 라이브: 진짜 claude 실행해 §11 컷 5장

결정적 장면(API 불필요)과 라이브 Claude 컷(진짜 `claude` 한 세션 실행 — 11/12/13/20/22)
두 갈래다. 라이브 컷은 실제 API 호출이라 무인자 전체 생성에서 제외하고 claude-suite 로만
돈다. 저장 SVG 는 _redact_svg 가 후처리한다: 계정 PII(이메일·환영 배너 이름) 마스킹 +
한글 등 와이드 문자의 자간 보정(Rich textLength 버그 교정, _fix_cjk_textlength).

POSIX 전용(서버/PTY 가 stdlib pty 기반). 헤드리스라 디스플레이/실TTY 불필요.
시계·호스트명 등 환경값은 실제값이 박힌다(재생성 시 그 부분만 diff 날 수 있음).
"""
from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_UNIT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_UNIT, "tests"))
sys.path.insert(0, _UNIT)

import harness  # noqa: E402
from harness import make_app, server_only, teardown  # noqa: E402

OUT_DIR = os.path.join(_UNIT, "docs", "image")
SIZE = (90, 26)

async def _settle(pilot, app, want_panes=None, tries=40):
    """레이아웃/화면 메시지가 흘러들어 합성될 때까지 잠깐 기다린다."""
    for _ in range(tries):
        await pilot.pause(0.05)
        panes = app.layout.get("panes", [])
        if want_panes is None or len(panes) >= want_panes:
            if app.view._cells:
                return
    await pilot.pause(0.1)


async def _wait_painted(pilot, app, tries=60):
    """모든 패널이 실제 화면 내용(pane_content)을 받을 때까지 대기.

    분할 직후엔 새 패널의 screen 메시지가 아직 안 와서 _composite 가 그 패널을
    건너뛰고, 잠깐 빈 프레임이 잡힌다(스크린샷 레이스). 모든 패널 id 가 콘텐츠를
    가질 때까지 기다려 빈 캡처를 막는다."""
    for _ in range(tries):
        panes = app.layout.get("panes", [])
        if panes and all(app.pane_content.get(p["id"]) for p in panes):
            return True
        await pilot.pause(0.05)
    return False


async def _type(pilot, text):
    for ch in text:
        await pilot.press("space" if ch == " " else ch)


def _aid(app):
    return app.layout.get("active")


def _pane_text(app):
    c = app.pane_content.get(_aid(app))
    return "\n".join("".join(s[0] for s in row) for row in c[0]) if c else ""


async def _launch_claude(pilot, app):
    """활성 패널에서 **진짜** `claude` 를 실행하고 TUI 가 뜰 때까지 대기."""
    app.send_cmd("rename_window", name="claude")
    await pilot.pause(0.3)
    await _type(pilot, "claude")
    await pilot.press("enter")
    for _ in range(250):                       # TUI 기동 대기(단축키 힌트/입력박스)
        await pilot.pause(0.1)
        if "shortcuts" in _pane_text(app).lower():
            return True
    raise RuntimeError("claude TUI 기동 실패")


async def _send_prompt(pilot, app, text):
    await _type(pilot, text)
    await pilot.pause(0.4)
    await pilot.press("enter")


def _check_api_error(app):
    t = _pane_text(app)
    if "Overloaded" in t or "API Error" in t:
        raise RuntimeError("claude API 오류(예: 529 Overloaded) — 재시도")


async def _wait_claude(pilot, app, target, tries=600):
    """target("busy"/"idle")까지 대기. idle 은 한 번 busy 를 거친 뒤의 idle(=응답완료)."""
    became_busy = False
    for _ in range(tries):
        await pilot.pause(0.1)
        _check_api_error(app)
        st = app.pane_claude.get(_aid(app), {}).get("claude")
        if st == "busy":
            became_busy = True
            if target == "busy":
                return
        if target == "idle" and became_busy and st == "idle":
            return
    raise RuntimeError(f"claude {target} 대기 시간 초과")


# ─────────────────────────── 장면 정의 ───────────────────────────
# 각 장면: (파일이름, 설명, async 운전함수(app, pilot)). 함수가 목표 상태를 만들면
# 호출부가 save_screenshot 한다.

async def first_run(app, pilot):
    await _type(pilot, "ls")
    await pilot.press("enter")
    await pilot.pause(0.6)


async def split_lr(app, pilot):
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    await pilot.pause(0.4)


async def split_nested(app, pilot):
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    app.send_cmd("split", orient="tb")
    await _settle(pilot, app, want_panes=3)
    await pilot.pause(0.4)


async def zoom(app, pilot):
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    app.send_cmd("zoom")
    await pilot.pause(0.5)


async def menu(app, pilot):
    app.open_menu()
    await pilot.pause(0.4)


async def menu_submenu(app, pilot):
    # §8.1: 그룹 메뉴에서 "패널 ▸" 서브메뉴를 펼친 모습(자식 MenuScreen).
    app.open_menu()
    await pilot.pause(0.3)
    app.screen_stack[-1]._open_group("pane")
    await pilot.pause(0.4)


async def command_prompt(app, pilot):
    app.open_prompt("command", initial="split-window -h")
    await pilot.pause(0.4)


async def kill_pane_prompt(app, pilot):
    # ESC → : 로 명령 프롬프트를 열고 kill-pane 명령을 입력한 모습(실제 키 입력).
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    await _wait_painted(pilot, app)
    await pilot.press("escape")        # 명령 모드(ESC)
    await pilot.pause(0.2)
    await pilot.press("colon")         # 명령 프롬프트 열기
    await pilot.pause(0.2)
    for ch in "kill-pane":             # 명령 입력
        await pilot.press("minus" if ch == "-" else ch)
    await pilot.pause(0.4)


async def tabs_multi(app, pilot):
    app.send_cmd("new_window")
    await pilot.pause(0.4)
    app.send_cmd("new_window")
    await pilot.pause(0.4)
    app.send_cmd("rename_window", name="build")
    await pilot.pause(0.5)


async def calendar(app, pilot):
    app.set_calendar(_aid(app), True)
    app._composite()
    await pilot.pause(0.4)


async def confirm_tab(app, pilot):
    # 탭이 하나뿐이면 "종료" 경고, 둘 이상이면 일반 확인 박스(ConfirmScreen).
    app.send_cmd("new_window")
    await pilot.pause(0.4)
    app.confirm_kill_tab()
    await pilot.pause(0.4)


async def info_popup(app, pilot):
    from pytmuxlib.clientscreens import InfoTabsScreen
    tabs = [
        ("캡처(REC)", ["녹화: off",
                       "경로: ~/.cache/pytmux/captures/default",
                       "capture-output on|off 로 토글"]),
        ("토큰", ["계정별 사용량 합계",
                  "  default   12.3k tok",
                  "  (세션 누적 · token-log 로 파일 기록)"]),
        ("서버", ["연결: 정상", "RTT: 3 ms", "소켓: /tmp/pytmux-…/default.sock"]),
    ]
    app.push_screen(InfoTabsScreen(tabs, title="정보"))
    await pilot.pause(0.4)


async def scrollback(app, pilot):
    for ch in "for i in $(seq 1 60); do echo \"line $i — 스크롤백 테스트\"; done":
        await pilot.press("space" if ch == " " else ch)
    await pilot.press("enter")
    await pilot.pause(0.7)
    app.mode = "scroll"                 # prefix [ = 스크롤백(복사) 모드
    app.send_scroll(_aid(app), delta=18)  # 위로 스크롤(지난 출력)
    await pilot.pause(0.5)


async def degraded(app, pilot):
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    await _wait_painted(pilot, app)
    app._net_degraded = True           # §10: IPC 지연 → 패널 외곽선 빨강
    app._net_last_rtt = 1.8
    await pilot.pause(0.3)


async def command_popup(app, pilot):
    # 명령 프롬프트(prefix :)에서 ? / help / commands 로 여는 명령 목록 팝업.
    from pytmuxlib.clientscreens import CommandListScreen, COMMANDS
    app.push_screen(CommandListScreen(COMMANDS))
    await pilot.pause(0.5)


async def clock(app, pilot):
    # prefix t / 하단 바 시계 클릭 → 시계 모드(현재 패널을 큰 블록 시계로 덮음).
    app.set_clock(_aid(app), True)
    app._composite()
    await pilot.pause(0.4)


async def prompt_history(app, pilot):
    # claude-prompt-history 플러그인 팝업(prompt-history). 공개 저장소에 실제 작업
    # 내용이 노출되지 않도록 **합성 프롬프트** 목록을 직접 PromptHistoryScreen 에 넣어
    # 띄운다(실서버 ph_list 응답 대신). 외형(시안 라운드 보더·시간순·미리보기 행수)은
    # 실제와 동일하다.
    from importlib import import_module
    screen = import_module("pytmuxlib.plugins.claude-prompt-history.screen")
    hist = [
        "이 저장소 구조를 한눈에 설명해줘",
        "tests/run.py 가 왜 실패하는지 찾아서 고쳐줘",
        "방금 고친 부분에 회귀 테스트를 추가해줘",
        "변경사항을 한 줄 요약으로 커밋해줘",
    ]
    app.push_screen(screen.PromptHistoryScreen(_aid(app), hist, 2))
    await pilot.pause(0.5)


async def p4_changes(app, pilot):
    # p4-show-submitted-changelists 플러그인(p4changes). 실 퍼포스 서버·계정·경로가
    # 노출되지 않도록 **합성 CL 목록**을 직접 ChangesScreen 에 넣어 띄운다(실서버
    # p4 changes 응답 대신). 외형(풀스크린·CL/시각/사용자/설명 열·시안 보더)은 동일.
    from importlib import import_module
    screen = import_module(
        "pytmuxlib.plugins.p4-show-submitted-changelists.screen")
    rows = [
        {"change": "58694", "when": "2026/06/13", "user": "dev",
         "desc": "docs: IMPROVEMENT stale 헤딩 5건 정정"},
        {"change": "58681", "when": "2026/06/13", "user": "dev",
         "desc": "패널 닫기 [x] 버튼 한 칸 위로"},
        {"change": "58672", "when": "2026/06/13", "user": "dev",
         "desc": "원격 중첩 자동 승격 구현 N1~N3"},
        {"change": "58660", "when": "2026/06/13", "user": "dev",
         "desc": "p4-show-submitted-changelists 플러그인"},
        {"change": "58648", "when": "2026/06/12", "user": "dev",
         "desc": "claude-prompt-history 플러그인 재구현"},
    ]
    app.push_screen(screen.ChangesScreen(rows, info={"port": "perforce:1666"}))
    await pilot.pause(0.5)


async def p4_describe(app, pilot):
    # p4changes 목록에서 한 CL 에 Enter → `p4 describe` 상세 팝업(DescribeScreen).
    # 실 퍼포스 서버·계정·클라이언트 경로가 노출되지 않게 request_p4_describe(실서버
    # 호출) 대신 DescribeScreen 을 직접 띄워 **합성 describe 텍스트**(//depot 일반
    # 경로)로 채운다. 외형(목록 위 중앙 모달·이중 보더·@CL 제목·스크롤 본문)은 동일.
    from importlib import import_module
    screen = import_module(
        "pytmuxlib.plugins.p4-show-submitted-changelists.screen")
    rows = [
        {"change": "58694", "when": "2026/06/13", "user": "dev",
         "desc": "docs: IMPROVEMENT stale 헤딩 5건 정정"},
        {"change": "58681", "when": "2026/06/13", "user": "dev",
         "desc": "패널 닫기 [x] 버튼 한 칸 위로"},
        {"change": "58672", "when": "2026/06/13", "user": "dev",
         "desc": "원격 중첩 자동 승격 구현 N1~N3"},
        {"change": "58660", "when": "2026/06/13", "user": "dev",
         "desc": "p4-show-submitted-changelists 플러그인"},
        {"change": "58648", "when": "2026/06/12", "user": "dev",
         "desc": "claude-prompt-history 플러그인 재구현"},
    ]
    app.push_screen(screen.ChangesScreen(rows, info={"port": "perforce:1666"}))
    await pilot.pause(0.3)
    desc = screen.DescribeScreen("58660")
    app.push_screen(desc)
    await pilot.pause(0.3)
    describe_text = (
        "Change 58660 by dev@dev-ws on 2026/06/13 14:22:07\n"
        "\n"
        "\tp4-show-submitted-changelists 플러그인 — submitted CL 목록 풀스크린 뷰\n"
        "\n"
        "\t현재 패널 cwd 의 퍼포스 설정(P4PORT/P4CLIENT) 그대로 `p4 changes -s\n"
        "\tsubmitted` 를 돌려 CL 목록을 풀스크린으로 띄운다. ↑↓ 스크롤, Enter 로\n"
        "\t`p4 describe` 상세, Esc 로 닫기. 서버측은 cwd 환경에서 subprocess argv\n"
        "\t로 실행(셸 미경유)하고 출력 길이를 상한해 과대 응답을 막는다.\n"
        "\n"
        "Affected files ...\n"
        "\n"
        "... //depot/scripts/pytmux/pytmuxlib/plugins/"
        "p4-show-submitted-changelists/__init__.py#1 add\n"
        "... //depot/scripts/pytmux/pytmuxlib/plugins/"
        "p4-show-submitted-changelists/screen.py#1 add\n"
        "... //depot/scripts/pytmux/pytmuxlib/plugins/"
        "p4-show-submitted-changelists/server.py#1 add\n"
        "... //depot/scripts/pytmux/tests/test_p4_changes.py#1 add\n"
    )
    desc.fill(describe_text, None)
    await pilot.pause(0.5)


async def remote_attach(app, pilot):
    # 원격 pytmux 탭 어태치(remote-attach) — 보는 탭이 원격이면 탭바(활성=분홍 배경)
    # 와 패널 외곽선(분홍)이 로컬과 구분된다(§1.7-a). 실 ssh 페더레이션 없이, 보는
    # 탭이 원격(active+remote)임을 status.windows 에 박아 분홍 렌더를 합성한다 —
    # 외형(분홍 탭/외곽선·노트북 연결부)은 실제와 동일. 실서버 status flush 가
    # windows 를 로컬로 덮을 수 있어(레이스) 캡처 직전 재주입 후 재합성한다.
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    await _wait_painted(pilot, app)

    def inject():
        app.status.windows = [
            {"index": 0, "name": "main", "active": False, "remote": False,
             "bell": False, "activity": False, "claude_done": False},
            {"index": 1, "name": "⇄office1:cmd", "active": True, "remote": True,
             "bell": False, "activity": False, "claude_done": False},
        ]
        app._update_tabbar()
        app._composite()
    inject()
    await pilot.pause(0.3)
    inject()
    await pilot.pause(0.2)


async def ime_badge(app, pilot):
    # ime-indicator 플러그인 — 화면 우상단 IME(한/영) 상태 배지. OS 실측 폴링이
    # 캡처 머신의 현재 입력기(영문)로 덮어쓰지 않게 _ime_os=False(폴백 경로)로 두고
    # 한글 모드를 박아 '[한]' 배지를 합성한다. 배지는 client_render 훅이 매 합성 때
    # 우상단 첫 행에 그린다(작지만 확정 표식).
    app._ime_os = False
    app.ime_show = True
    app.ime_state = "한"
    app._composite()
    await pilot.pause(0.4)


async def calendar_big(app, pilot):
    # 큰 달력(숫자가 블록 문자) — 패널이 충분히 크면 자동으로 블록-숫자 달력이 된다.
    app.set_calendar(_aid(app), True)
    app._composite()
    await pilot.pause(0.4)


async def confirm_tab_last(app, pilot):
    # 탭이 하나뿐일 때 탭을 닫으면 pytmux 종료 경고 팝업이 뜬다.
    app.confirm_kill_tab()
    await pilot.pause(0.4)


async def restart_check(app, pilot):
    # restart-all 드라이런(restart-check) — 실제로 안 하고 안전 점검 PASS/FAIL 팝업.
    app._show_restart_check_popup({
        "reexec_supported": True, "has_sessions": True, "serialize_ok": True,
        "panes": 3, "panes_with_fd": 3,
        "running_version": "p4:5281", "disk_version": "p4:5290",
    })
    await pilot.pause(0.4)


async def restart_confirm(app, pilot):
    # 드라이런 FAIL → "그래도 재시작?" 재확인 팝업(MANUAL §15.2). 위험 동작이라
    # danger=True(취소 붉은 강조, 기본 취소). 대표 FAIL 메시지로 직접 띄운다.
    msg = "\n".join([
        "드라이런 FAIL — 전체 재시작 안전 점검에서 문제가 있습니다:", "",
        "  [FAIL] 서버 re-exec 지원(POSIX·이벤트루프)",
        "  [FAIL] 패널 master fd 보유 (0/4)",
        "", "그래도 재시작할까요?"])
    app.confirm_popup(msg, action=lambda: None, title="재시작 확인",
                      yes_label="재시작", danger=True)
    await pilot.pause(0.4)


async def token_saver(app, pilot):
    # 토큰 절감 설정 팝업(token-saver) — 자동 개입 토글·잔량 임계·예산·경고 설정행.
    # ClaudeSaverScreen 은 claude-code 플러그인으로 이전됐다(패키지명 하이픈→import_module).
    from importlib import import_module
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    app.push_screen(screens.ClaudeSaverScreen())
    await pilot.pause(0.5)


def _tklog_data():
    """토큰 사용량 팝업용 합성 데이터(2026-06-12 재설계 반영). 공개 저장소에 실제
    사용량이 노출되지 않게 가상의 며칠치 레코드·계정·실측 한도를 만든다. 일별 뷰가
    여러 행으로 차고 요약줄(5h%·주%·~Σ)이 의미 있게 보이도록 구성한다."""
    import datetime as _dt
    # 결정적 기준일(스크린샷 라벨 고정): 2026-06-18 을 기준으로 최근 6일.
    base = _dt.datetime(2026, 6, 18, 14, 0)
    recs = []
    daily_tokens = [42_100, 58_700, 31_400, 64_900, 27_800, 51_300]
    for i, tok in enumerate(daily_tokens):
        day = base - _dt.timedelta(days=i)
        recs.append({"ts": day.timestamp(), "tab": i % 3, "pane": i + 1,
                     "session": i + 1, "account": "default", "tokens": tok})
    usage = {"session": {"pct": 38, "reset": "2pm (Asia/Seoul)"},
             "week_all": {"pct": 61, "reset": "Jun 22 at 3am (Asia/Seoul)"},
             "week_sonnet": {"pct": 12, "reset": "Jun 22 at 3am (Asia/Seoul)"},
             "account": "default"}
    return recs, usage


async def token_log(app, pilot):
    # 토큰 사용량 팝업(일별 뷰) — 노트북 탭(기간/계정/세션/한도/대사/경고)+서브옵션
    # (시간/일/주/월·정렬)+요약줄(5h%·주%·~Σ)+기간:토큰 표. 2026-06-12 재설계.
    # TokenLogScreen 은 claude-code 플러그인으로 이전됐다(패키지명 하이픈→import_module).
    from importlib import import_module
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    recs, usage = _tklog_data()
    app.push_screen(screens.TokenLogScreen(recs, usage=usage))
    await pilot.pause(0.5)


async def token_log_hour(app, pilot):
    # 토큰 사용량 팝업(시간 뷰) — 재설계의 시그니처: 시각별 세션 5h 한도 누적%를
    # **계단식 가로 막대**(초록/노랑/빨강 임계)로, 옆에 주간(1w%) 숫자 열을 둔다.
    # 권위 /usage 실측을 시각 단위로 조인(hourly_pct/hourly_week_pct). initial_mode=hour.
    from importlib import import_module
    import datetime as _dt
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    base = _dt.datetime(2026, 6, 18, 9, 0)     # 09시부터 시각별
    recs, hourly_pct, hourly_week_pct = [], {}, {}
    # 5h 창이 한 번 차오르다 리셋되는 모습: 누적 5h% 가 오르다 14시에 0 으로 떨어진다.
    series = [(9, 18, 9), (10, 41, 11), (11, 63, 12), (12, 86, 13),
              (13, 97, 14), (14, 22, 15), (15, 49, 16)]
    for hour, s5h, swk in series:
        t = base.replace(hour=hour)
        key = t.strftime("%Y-%m-%d %H:00")
        recs.append({"ts": t.timestamp(), "tab": 0, "pane": 1, "session": 1,
                     "account": "default", "tokens": 6000 + hour * 700})
        hourly_pct[key] = s5h
        hourly_week_pct[key] = swk
    usage = {"session": {"pct": 49, "reset": "2pm (Asia/Seoul)"},
             "week_all": {"pct": 16, "reset": "Jun 22 at 3am (Asia/Seoul)"},
             "account": "default"}
    app.push_screen(screens.TokenLogScreen(
        recs, usage=usage, hourly_pct=hourly_pct,
        hourly_week_pct=hourly_week_pct, initial_mode="hour"))
    await pilot.pause(0.5)


async def token_log_limit(app, pilot):
    # 토큰 사용량 팝업(한도 뷰) — /usage 실측 한도 상세(세션 5h·주 전체·주 Sonnet
    # 막대+% 사용+리셋), 현재 창 추정 Σ, 다음 리셋까지 블록-숫자 카운트다운을 한 탭으로
    # 통합(2026-06-17, 옛 별도 usage-view 팝업을 흡수). initial_mode=limit.
    from importlib import import_module
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    recs, usage = _tklog_data()
    app.push_screen(screens.TokenLogScreen(
        recs, usage=usage, initial_mode="limit"))
    await pilot.pause(0.5)


async def remote_control(app, pilot):
    # 원격 제어(Remote Control) 정보+토글 팝업 — [r] 로 /rc 주입해 켜고 끈다.
    app.open_remote_control(_aid(app))
    await pilot.pause(0.5)


async def ncd(app, pilot):
    # ncd(Norton Change Directory 풍 디렉토리 트리). 공개 저장소에 실제 파일시스템
    # 경로·디렉토리 이름이 노출되지 않도록, 실서버 nc_list 응답 대신 **합성 트리**
    # (가상의 /home/user/…)를 직접 NcdScreen 에 넣어 띄운다. 기능·외형(DOS 블루 패널·
    # 시안 선택 막대·루트→cwd 펼침·찾기 안내줄)은 실제와 동일하다.
    from pytmuxlib.plugins.ncd.screen import NcdScreen
    cwd = "/home/user/projects/webapp"
    chain = [
        ("/", ["/home", "/etc", "/opt", "/usr", "/var"]),
        ("/home", ["/home/user"]),
        ("/home/user", ["/home/user/documents", "/home/user/downloads",
                        "/home/user/projects"]),
        ("/home/user/projects", ["/home/user/projects/api",
                                 "/home/user/projects/cli",
                                 "/home/user/projects/webapp"]),
        ("/home/user/projects/webapp", ["/home/user/projects/webapp/docs",
                                        "/home/user/projects/webapp/src",
                                        "/home/user/projects/webapp/tests"]),
    ]
    app.push_screen(NcdScreen("/", chain=chain, cwd=cwd, dirs=None))
    await pilot.pause(0.6)


async def claude_rules(app, pilot):
    # 시작 규칙 편집(claude-rules) — RulesEditScreen 에 예시 규칙을 넣어 띄운다.
    # 저장하면 새 세션/`/clear` 후 첫 프롬프트에 자동 주입되는 '항상 지킬 규칙'.
    from importlib import import_module
    # 패키지명에 하이픈이 있어 from-import 가 안 되므로 import_module 로 로드한다.
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    sample = ("- 답변은 한국어로, 핵심부터 간결하게.\n"
              "- 코드를 고치기 전 관련 파일을 먼저 읽고 기존 스타일을 따른다.\n"
              "- 테스트를 추가/수정하면 반드시 실행해 통과를 확인한다.\n"
              "- 커밋 메시지는 한 줄 요약 + 본문(왜).")
    app.push_screen(screens.RulesEditScreen(sample))
    await pilot.pause(0.5)


async def usage_panel(app, pilot):
    # 사용량 한도 조회(/usage) — 그림자 세션이 가져온 세션 5h·주 전체·주 Sonnet 한도를
    # 막대 그래프 전용 화면(open_usage_panel)으로 띄운다. 예시 한도 값을 주입.
    app.status.usage_limits = {
        "session": {"pct": 38, "reset": "2pm (Asia/Seoul)"},
        "week_all": {"pct": 61, "reset": "Jun 13 at 3am (Asia/Seoul)"},
        "week_sonnet": {"pct": 12, "reset": "Jun 13 at 3am (Asia/Seoul)"},
        "account": "default",
    }
    app.open_usage_panel()
    await pilot.pause(0.5)


async def usage_view(app, pilot):
    # claude-token-usage-view 플러그인 팝업(usage-view) — 한도 막대(% 우측정렬)+다음
    # 리셋 블록 카운트다운. 예시 한도를 주입한다. 실서버 status flush 가 usage_limits 를
    # None 으로 덮어 1초 틱이 '데이터 없음'으로 재렌더할 수 있으므로(레이스), 캡처 직전
    # 데이터를 다시 박고 화면을 재렌더해 안정적으로 찍는다(usage_panel 의 스냅샷 대응).
    data = {
        "session": {"pct": 18, "reset": "5:59pm (Asia/Seoul)"},
        "week_all": {"pct": 3, "reset": "Jun 18, 12:59pm (Asia/Seoul)"},
        "week_sonnet": {"pct": 0, "reset": "Jun 18, 12:59pm (Asia/Seoul)"},
        "account": "default",
    }
    app.status.usage_limits = data
    app.status.usage_age_sec = 5
    # popup 모드는 이제 token-log '한도' 탭으로 통합돼(2026-06-17) UsageScreen 을 안
    # 띄운다(그 화면은 38-token-log-limit 에서 다룬다). 이 컷은 플러그인 고유 화면인
    # UsageScreen 자체를 보여주려는 것이므로 직접 띄운다(델리트-투-디세이블 폴백 경로).
    from importlib import import_module
    uvscreen = import_module("pytmuxlib.plugins.claude-token-usage-view.screen")
    app.push_screen(uvscreen.UsageScreen(full=False))
    await pilot.pause(0.4)
    app.status.usage_limits = data
    app.screen_stack[-1]._redraw()
    await pilot.pause(0.2)


# 진짜 Claude Code 한 세션에서 캡처하는 §11 컷 묶음(라이브 — 실제 API 호출).
CLAUDE_OUTPUTS = ["11-claude", "12-claude-autoresume", "13-perm-mode",
                  "22-claude-real"]


async def _claude_suite_once():
    """진짜 `claude` 한 세션을 운전해 §11 세부 컷 4장을 모두 캡처한다.

    프롬프트 1회로 idle(응답완료)·autoresume·권한모드 팝업을 찍고, 프롬프트
    2회째의 busy 상태로 처리중(◐) 컷을 찍는다(환영 배너가 위로 밀려 계정
    이름이 안 보이는 상태). 저장 시 _redact_svg 가 이메일 등 PII 를 마스킹한다."""
    srv, task, sock = await server_only()
    app = make_app(sock, {}, "main")

    async def shot(pilot, name):
        app._composite()
        app.refresh()
        await pilot.pause(0.4)
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, name + ".svg")
        app.save_screenshot(p)
        _redact_svg(p)
        print(f"  ✓ {name}.svg")

    try:
        async with app.run_test(size=(100, 30)) as pilot:
            await _settle(pilot, app, want_panes=1)
            await _wait_painted(pilot, app)
            await _launch_claude(pilot, app)
            aid = _aid(app)
            # ── 프롬프트 1 → 응답완료(idle)
            await _send_prompt(pilot, app, "pytmux 가 무엇인지 두 문장으로 설명해줘")
            await _wait_claude(pilot, app, "idle")
            await pilot.pause(0.6)
            await shot(pilot, "22-claude-real")            # 실제 실행(응답완료)
            # ── 자동재개(AR) 토글 → 상태줄 AR 배지
            app.send_cmd("set_autoresume")
            await pilot.pause(0.6)
            await shot(pilot, "12-claude-autoresume")
            app.send_cmd("set_autoresume")                 # 원복(off)
            await pilot.pause(0.3)
            # ── 권한모드 선택 팝업(footer 클릭 상당)
            app.open_perm_mode(aid)
            await pilot.pause(0.5)
            await shot(pilot, "13-perm-mode")
            await pilot.press("escape")
            await pilot.pause(0.3)
            # ── 프롬프트 2 → 처리중(busy) 컷(◐ + 스티키 헤더 + 토큰)
            await _send_prompt(pilot, app, "스크롤백은 어떻게 보나요?")
            await _wait_claude(pilot, app, "busy")
            await pilot.pause(1.0)
            await shot(pilot, "11-claude")
    finally:
        await teardown(srv, task, sock)


async def claude_suite(retries=4):
    """라이브 Claude 컷 묶음 생성(실제 API 호출). 일시 오류(529 등)엔 재시도."""
    import shutil
    if not shutil.which("claude"):
        print("  ✗ claude CLI 가 PATH 에 없음 — 실제 Claude Code 가 필요합니다")
        return 1
    print(f"실제 Claude Code 캡처(라이브) → {OUT_DIR}")
    for attempt in range(1, retries + 1):
        try:
            await _claude_suite_once()
            return 0
        except Exception as e:  # noqa: BLE001  일시 오류는 통째로 재시도
            print(f"  … claude-suite 오류({type(e).__name__}: {e}), "
                  f"재시도 {attempt}/{retries}")
    print("  ✗ claude-suite 실패")
    return 1


# 장면: (이름, 설명, 운전함수[, 크기]). 크기 생략 시 SIZE(90×26). 큰 달력은 블록-숫자
# 모드가 뜨도록 더 큰(높은) 터미널이 필요하다.
BIG = (96, 44)
SCENES = [
    ("01-first-run", "첫 실행 — 단일 패널 + 탭바 + 상태줄", first_run),
    ("02-split-lr", "좌우 분할 — 활성 패널 파란 테두리", split_lr),
    ("03-split-nested", "중첩 분할 — ┬┴├┤ 경계", split_nested),
    ("04-zoom", "줌 — 상태줄 Z 표시", zoom),
    ("05-menu", "메뉴(prefix Enter) — 그룹(서브메뉴)+구분선", menu),
    ("36-menu-submenu", "메뉴 서브메뉴 — '패널 ▸' 펼침(§8.1)", menu_submenu),
    ("06-command-prompt", "명령 프롬프트(prefix :) + 고스트 자동완성", command_prompt),
    ("07-kill-pane-prompt", "패널 닫기 — ESC : 명령 프롬프트에 kill-pane 입력", kill_pane_prompt),
    ("08-tabs-multi", "탭 여러 개 + 이름변경", tabs_multi),
    ("09-calendar", "큰 달력 오버레이(cal) — 블록-숫자", calendar_big, BIG),
    ("10-confirm-tab", "탭 닫기 확인 박스(탭 2개 이상)", confirm_tab),
    ("14-info-popup", "통합 정보 팝업(캡처·토큰·서버)", info_popup),
    ("15-scrollback", "스크롤백(복사) 모드 — 지난 출력", scrollback),
    ("16-degraded", "네트워크 degraded — 패널 외곽선 빨강", degraded),
    ("17-command-popup", "명령 목록 팝업(? / help) — 카테고리 탭·검색·스크롤", command_popup),
    ("18-clock", "시계 모드 — 큰 블록 시계", clock),
    ("19-confirm-tab-last", "마지막 탭 닫기 — pytmux 종료 경고 팝업", confirm_tab_last),
    ("21-restart-check", "restart-check 드라이런 — 작업보존 재시작 안전점검", restart_check),
    ("26-restart-confirm", "재시작 확인 — 드라이런 FAIL 시 '그래도 재시작?'(기본 취소)", restart_confirm),
    ("23-token-saver", "토큰 절감 설정 팝업(token-saver) — 자동개입 토글·임계·예산·경고", token_saver),
    ("24-token-log", "토큰 사용량 팝업(일별) — 노트북 탭+서브옵션+요약줄+기간:토큰 표", token_log),
    ("37-token-log-hour", "토큰 팝업(시간) — 시각별 5h 한도 계단식 막대 + 1w% 열", token_log_hour),
    ("38-token-log-limit", "토큰 팝업(한도) — /usage 막대·창Σ·리셋 카운트다운 통합 탭", token_log_limit),
    ("25-remote-control", "원격 제어 팝업 — [r] 로 /rc 토글", remote_control),
    ("27-ncd", "디렉토리 트리(ncd) — 루트→cwd 펼침·시안 선택 막대·찾기 안내줄", ncd),
    ("28-claude-rules", "시작 규칙 편집(claude-rules) — 멀티라인 에디터·Ctrl+S 저장", claude_rules),
    ("29-usage-panel", "사용 한도(/usage) — 세션 5h·주 전체·주 Sonnet 막대 그래프", usage_panel),
    ("30-usage-view", "usage-view 팝업 — 한도 막대(% 우측정렬)+다음 리셋 블록 카운트다운", usage_view),
    ("31-prompt-history", "프롬프트 히스토리 팝업(prompt-history) — 시간순·미리보기 행수", prompt_history),
    ("32-p4changes", "submitted CL 목록(p4changes) — 풀스크린·CL/시각/사용자/설명", p4_changes),
    ("35-p4-describe", "p4changes 상세 — CL 에 Enter → p4 describe 팝업(이중 보더·@CL·변경파일)", p4_describe),
    ("33-ime", "IME 한/영 배지(ime-indicator) — 우상단 상태 배지", ime_badge),
    ("34-remote-attach", "원격 pytmux 탭 어태치 — 분홍 탭바·분홍 패널 외곽선", remote_attach),
]
# Claude 컷(11·12·13·20·22)은 결정적 장면이 아니라 진짜 `claude` 한 세션에서 캡처한다
# (claude_suite). 실제 API 호출이라 무인자 전체 생성에선 제외하고, `claude-suite` 또는
# 해당 이름을 지정했을 때만 돈다.
ALL_SCENES = SCENES


def _scene_size(scene):
    return scene[3] if len(scene) > 3 else SIZE


def _is_blank(svg_path):
    """패널 콘텐츠 없이 크롬(탭바·상태줄)만 그려진 빈 프레임인지 판정.

    스크린샷 레이스로 패널이 페인트되기 전 프레임이 잡히면 본문 텍스트가 거의 없어
    SVG 크기가 빈 프레임(크롬만, ~11.4KB)에 수렴한다. 콘텐츠가 있는 프레임은 모두
    14KB 이상이라 크기로 안전하게 가려낸다(장면마다 글리프가 달라 마커 검사보다 견고)."""
    try:
        return os.path.getsize(svg_path) < 13000
    except OSError:
        return True


import html as _html
import re as _re

from rich.cells import cell_len as _cell_len

_EMAIL_RE = _re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


_WELCOME_RE = _re.compile(r">Welcome(?:&#160;|\s)back[^<]*</text>")


# Rich 의 export_svg 는 <text> 의 textLength 를 cell_len 이 아닌 len(글자수) 로 계산하는
# 버그가 있어(rich/console.py: `textLength=char_width * len(text)`), 한글 등 와이드(2칸)
# 문자가 절반 폭으로 압축돼 자간이 좁아지고 글자가 겹쳐 보인다. textLength 만 cell_len 기준
# 으로 늘려도 폭은 맞지만, SVG 기본 lengthAdjust="spacing" 은 그 늘림량을 글리프 사이마다
# 균등 분배한다 — 임베드 폰트의 CJK 글리프 advance 가 ASCII 의 정확히 2배가 아니라서, 한
# <text> 가 좁은(ASCII)·넓은(CJK) 문자를 섞고 있으면 CJK 가 많은 줄일수록 앞쪽 ASCII 구간
# 이 옆으로 밀려 열 정렬이 흐트러진다(명령목록 "이름  설명" 표의 설명 열이 행마다 어긋남).
#
# 해결: 폭이 같은 문자끼리의 최대 런(narrow|wide)으로 <text> 를 쪼개고, 각 런을 자신의 셀
# x 위치에 textLength=런셀수×셀폭 으로 다시 배치한다. 런 내부는 글리프 폭이 균일해 spacing
# 분배가 고르고, 각 런의 시작 x 가 셀 그리드에 고정돼 열이 정확히 맞는다.
_TEXT_RE = _re.compile(r'<text\b([^>]*)>([^<]*)</text>')
_X_RE = _re.compile(r'\bx="([0-9.]+)"')
_TL_RE = _re.compile(r'\btextLength="([0-9.]+)"')


def _svg_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace("\xa0", "&#160;"))


def _fix_cjk_textlength(svg):
    """와이드 문자가 섞인 <text> 를 폭-동질 런으로 쪼개 셀 그리드에 다시 정렬한다."""

    def repl(m):
        attrs, content = m.group(1), m.group(2)
        xm = _X_RE.search(attrs)
        tlm = _TL_RE.search(attrs)
        if not xm or not tlm:
            return m.group(0)            # x/textLength 없음 → 그대로
        text = _html.unescape(content)
        n = len(text)
        cells = _cell_len(text)
        if n == 0 or cells == 0 or cells == n:
            return m.group(0)            # 와이드 문자 없음 → 그대로
        line_x = float(xm.group(1))
        cell_w = float(tlm.group(1)) / n  # 셀당 px (Rich 의 모노스페이스 char_width)
        # 폭(1|2)이 같은 문자끼리 최대 런으로 묶는다(0폭 결합문자는 narrow 로 흡수).
        runs, cur, cur_wide = [], [], None
        for ch in text:
            wide = _cell_len(ch) >= 2
            if cur and wide != cur_wide:
                runs.append((cur_wide, "".join(cur)))
                cur = []
            cur.append(ch)
            cur_wide = wide
        if cur:
            runs.append((cur_wide, "".join(cur)))
        out, col = [], 0
        for wide, seg in runs:
            seg_cells = _cell_len(seg)
            seg_x = line_x + col * cell_w
            seg_attrs = _TL_RE.sub(f'textLength="{seg_cells * cell_w:g}"',
                                   _X_RE.sub(f'x="{seg_x:g}"', attrs))
            # 와이드 런은 글리프 advance 가 2셀보다 좁다 — spacing 으로 늘리면 글자
            # 사이가 벌어지므로 spacingAndGlyphs 로 글리프 자체를 셀에 맞춰 늘인다
            # (ASCII narrow 런은 이미 모노스페이스라 기본 spacing 으로 충분).
            if wide:
                seg_attrs += ' lengthAdjust="spacingAndGlyphs"'
            out.append(f"<text{seg_attrs}>{_svg_escape(seg)}</text>")
            col += seg_cells
        return "".join(out)

    return _TEXT_RE.sub(repl, svg)


def _redact_svg(path):
    """저장한 SVG 의 PII 마스킹 + 한글 자간(textLength) 보정.

    PII: 실제 `claude` 실행 화면에는 로그인 계정 이메일이 환영 배너·상태줄에, 사용자 이름이
    "Welcome back <이름>!" 배너에 뜬다. 공개 저장소에 커밋되는 이미지라 이메일은
    user@example.com 으로, 환영 이름은 "Welcome back!" 으로 마스킹한다(다른 텍스트는
    그대로). textLength 속성이 있어 폭은 유지된다.

    자간: Rich 의 와이드 문자 textLength 버그를 _fix_cjk_textlength 로 교정한다."""
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return
    new = _EMAIL_RE.sub("user@example.com", svg)
    new = _WELCOME_RE.sub(">Welcome&#160;back!</text>", new)
    new = _fix_cjk_textlength(new)
    if new != svg:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)


async def _one_shot(name, desc, drive, path, size=SIZE):
    srv, task, sock = await server_only()
    app = make_app(sock, {}, "main")
    try:
        async with app.run_test(size=size) as pilot:
            await _settle(pilot, app, want_panes=1)
            await _wait_painted(pilot, app)      # 초기 패널 페인트까지 대기
            await pilot.pause(0.3)
            await drive(app, pilot)
            # 모든 패널이 화면 내용을 받을 때까지 대기(분할 직후 빈 프레임 레이스 방지).
            # pane_content/layout 은 모달 유무와 무관한 베이스 상태라 항상 안전하다.
            await _wait_painted(pilot, app)
            # 마지막 서버 메시지 반영 후 최신 프레임을 강제 합성(빈 프레임 캡처 방지).
            app._composite()
            app.refresh()
            await pilot.pause(0.4)
            os.makedirs(OUT_DIR, exist_ok=True)
            app.save_screenshot(path)
            _redact_svg(path)          # 이메일 등 PII 마스킹(라이브 Claude 장면 등)
    finally:
        await teardown(srv, task, sock)


async def shoot(name, desc, drive, retries=4, size=SIZE):
    path = os.path.join(OUT_DIR, name + ".svg")
    for attempt in range(1, retries + 1):
        try:
            await _one_shot(name, desc, drive, path, size)
            blank = _is_blank(path)
        except Exception as e:  # noqa: BLE001  라이브 장면의 일시 오류(예: 529)도 재시도
            print(f"  … {name} 오류({type(e).__name__}: {e}), 재시도 {attempt}/{retries}")
            continue
        if not blank:
            print(f"  ✓ {name}.svg  — {desc}")
            return path
        print(f"  … {name} 빈 프레임, 재시도 {attempt}/{retries}")
    print(f"  ✗ {name}.svg  — {retries}회 실패")
    return path


async def _worker(filt):
    """워커 모드: filt 에 매칭되는 장면을 **이 프로세스에서** 생성한다."""
    todo = [s for s in ALL_SCENES if filt in s[0]]
    if not todo:
        print(f"매칭 장면 없음: {filt!r}\n사용 가능: " +
              ", ".join(s[0] for s in ALL_SCENES))
        return 1
    print(f"스크린샷 생성 → {OUT_DIR}")
    for scene in todo:
        name, desc, drive = scene[0], scene[1], scene[2]
        try:
            await shoot(name, desc, drive, size=_scene_size(scene))
        except Exception as e:  # noqa: BLE001  한 장 실패가 전체를 막지 않게
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
    return 0


def _orchestrate():
    """전체 생성: 각 장면을 **별도 서브프로세스**로 돌린다.

    한 프로세스에서 서버·PTY 를 10개+ 연속 띄우면 정리(teardown)가 다음 장면에
    새어 빈 프레임 레이스가 잦다. 장면마다 새 인터프리터로 격리하면 결정적이다.
    """
    import subprocess
    print(f"스크린샷 생성(장면별 격리) → {OUT_DIR}")
    rc = 0
    for scene in SCENES:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), scene[0]])
        rc = rc or r.returncode
    return rc


if __name__ == "__main__":
    if os.name == "nt":
        print("이 생성기는 POSIX 전용입니다(서버 PTY 가 stdlib pty 기반).")
        sys.exit(2)
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        # 라이브 Claude 컷 묶음(실제 `claude` 실행 — 11/12/13/20/22).
        if arg in ("claude-suite", "claude") or arg in CLAUDE_OUTPUTS:
            sys.exit(asyncio.run(claude_suite()))
        # 워커 모드(결정적 장면 이름 지정) — 이 프로세스에서 생성.
        sys.exit(asyncio.run(_worker(arg)))
    # 인자 없음 — 장면별 서브프로세스로 결정적 장면 전체 생성(Claude 컷 제외).
    sys.exit(_orchestrate())
