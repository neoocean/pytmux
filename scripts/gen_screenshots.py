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
import getpass as _getpass
import os
import socket as _socket
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_UNIT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_UNIT, "tests"))
sys.path.insert(0, _UNIT)

import harness  # noqa: E402
from harness import make_app, server_only, teardown  # noqa: E402

# ── 공개 안전: 패널 셸 프롬프트를 합성값으로 고정 ──────────────────────────────
# gen_screenshots SVG 는 모든 글리프를 벡터 path 로 굽는다(폰트 비의존). 그래서 셸
# 프롬프트에 박힌 실제 사용자명/호스트명은 `<text>` 가 아니라 path 라 grep 게이트가
# 못 잡는다. _redact_svg 는 `<text>` 단계의 문자열 치환(실제 사용자명→user)이라 프롬프트가
# **온전히** 보일 때만 유효하고, 팝업이 프롬프트를 잘라 'neooce…' 처럼 부분만 남으면
# 그 부분 사용자명이 그대로 구워져 공개 이미지에 노출됐다(팝업/모달 컷 전반).
# 근본 차단: 스크린샷 전용 ZDOTDIR 에 합성 프롬프트 .zshrc 를 깔아, 실제 사용자명이
# 애초에 SVG 에 들어가지 않게 한다(잘려도 'user@host' 일 뿐). 테스트와 분리된 전용
# 디렉토리라 harness 의 히스토리 격리(별도 ZDOTDIR)와 무관하다.
_SHOT_ZDOTDIR = tempfile.mkdtemp(prefix="pytmux-shot-zdot-")
with open(os.path.join(_SHOT_ZDOTDIR, ".zshrc"), "w", encoding="utf-8") as _f:
    # %1~ = cwd 의 마지막 성분(기존 컷의 'pytmux' 와 동일). 사용자명/호스트명은 리터럴.
    _f.write("PROMPT='user@host %1~ %# '\nRPROMPT=''\n")
os.environ["ZDOTDIR"] = _SHOT_ZDOTDIR
# bash 폴백(SHELL=bash 환경)도 동일 프롬프트로.
os.environ["PS1"] = r"user@host \W \$ "

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


async def compose_prompt(app, pilot):
    # 프롬프트 작성창(ComposePromptScreen) — ESC→Insert 로 여는 블록 선택 멀티라인
    # 편집기(코어). 활성 패널 프롬프트 위에 떠 상단 힌트로 Enter 전송·Shift+Enter
    # 줄바꿈·Ctrl+A 전체 선택을 안내하고, 우상단에 IME(한/영) 배지를 띄운다. OS 실측
    # 폴링이 캡처 머신 입력기로 덮어쓰지 않게 _ime_os=False(폴백)로 두고 'EN' 을 박는다.
    # 실 작업 내용 노출 방지로 합성 프롬프트를 시드로 넣는다(외형은 실제와 동일).
    from pytmuxlib.clientscreens import ComposePromptScreen
    app._ime_os = False
    app.ime_show = True
    app.ime_state = "EN"
    seed = ("이 모듈의 공개 API 를 정리해서 사용 예시와\n"
            "함께 README 의 '빠른 시작' 절에 추가해줘")
    app.push_screen(ComposePromptScreen(seed))
    await pilot.pause(0.5)


async def claude_resume(app, pilot):
    # claude-resume 플러그인 리줌 피커(claude-resume). 실 머신의 세션 경로·제목이
    # 노출되지 않도록 **합성 세션 목록**을 직접 ClaudeResumeScreen 에 넣어 띄운다
    # (실서버 claude_list_sessions 응답 대신). 외형(시각·프로젝트·AI 제목 3열·시안
    # 라운드 보더·[x]·안내줄)은 실제와 동일. mtime 은 결정적 로컬시각으로 고정.
    import time
    from importlib import import_module
    screen = import_module("pytmuxlib.plugins.claude-resume.screen")
    base = time.mktime((2026, 6, 20, 15, 40, 0, 0, 0, -1))
    sessions = [
        {"id": "a1b2c3", "cwd": "/work/web-dashboard", "project": "web-dashboard",
         "title": "리스트 가상 스크롤 성능 개선", "mtime": base - 300},
        {"id": "d4e5f6", "cwd": "/work/api-gateway", "project": "api-gateway",
         "title": "JWT 갱신 레이스 컨디션 수정", "mtime": base - 5400},
        {"id": "071829", "cwd": "/work/pytmux", "project": "pytmux",
         "title": "갤러리 스크린샷 자동 생성기 보강", "mtime": base - 23400},
        {"id": "3a4b5c", "cwd": "/work/notes-cli", "project": "notes-cli",
         "title": "마크다운 내보내기 옵션 추가", "mtime": base - 97200},
    ]
    app.push_screen(screen.ClaudeResumeScreen(sessions))
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
            {"index": 1, "name": "⇄remote:cmd", "active": True, "remote": True,
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
    # 결정적 기준일을 2026-02-06 으로 고정한다(_calendar_now 훅). 2026-02 는 일요일
    # 시작·28일이라 정확히 4주 → 블록 달력이 표준 SIZE(90×26)에 그대로 들어가(다른 장면과
    # 동일 세로 길이), 6일(금)이 '오늘'로 강조된다. 훅이 없으면(production) 현재 달을 그린다.
    import datetime as _dt
    app._calendar_now = _dt.datetime(2026, 2, 6, 14, 0)
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
    # Claude 설정 팝업(token-saver) — 자동재개·세션종료 토큰화면·권한 오토모드·
    # 프롬프트 단위 클리어·장기 턴/반복 루프 경고 설정행.
    # ClaudeSaverScreen 은 claude-code 플러그인으로 이전됐다(패키지명 하이픈→import_module).
    from importlib import import_module
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    app.push_screen(screens.ClaudeSaverScreen())
    await pilot.pause(0.5)


async def settings(app, pilot):
    # 통합 설정 화면(:settings / config / preferences / 옵션) — 흩어진 pytmux 설정을 한 곳에.
    # 좌측 카테고리 세로 탭(표시·입력·동작·상태줄·Claude·고급·키) + 우측 전체 설정 목록.
    # 행에서 ←→(또는 클릭) = bool 토글·enum 순환·숫자 증감을 즉시 적용+영속, 문자열은
    # Enter 입력 모달, 링크 행(Claude·플러그인)은 Enter 로 전용 화면을 연다.
    from pytmuxlib.clientscreens import SettingsScreen
    app.push_screen(SettingsScreen(prefix_key="ctrl+b",
                                   user_bindings={"ctrl+g": "split-h",
                                                  "f5": "restart-check"}))
    await pilot.pause(0.6)


def _tklog_data():
    """토큰 사용량 팝업용 합성 데이터(2026-06-12 재설계 반영). 공개 저장소에 실제
    사용량이 노출되지 않게 가상의 며칠치 레코드·계정·실측 한도를 만든다. 일별 뷰가
    여러 행으로 차고 요약줄(5h%·주%·~Σ)이 의미 있게 보이도록 구성한다."""
    import datetime as _dt
    # 결정적 기준일(스크린샷 라벨 고정): 2026-06-18 기준. 여러 날 × 여러 세션을 깔아
    # 기간 트리(주→일)와 세션 목록(탭:패널)이 둘 다 의미있게 차도록 한다.
    base = _dt.datetime(2026, 6, 18, 14, 0)
    recs = []
    sid = 2100
    # (며칠 전, [(탭, 패널, 토큰), ...]) — 최근일수록 세션이 많다.
    plan = [
        (0,  [(1, 1, 36_100), (3, 4, 16_700), (2, 2, 13_200)]),
        (1,  [(1, 12, 31_900), (1, 12, 22_900), (3, 19, 9_600), (2, 22, 5_900)]),
        (2,  [(1, 12, 6_000), (2, 22, 3_500)]),
        (3,  [(1, 1, 7_200), (3, 4, 4_500)]),
        (6,  [(1, 1, 51_300), (2, 3, 27_800)]),
        (9,  [(1, 1, 64_900), (3, 2, 31_400)]),
        (12, [(1, 1, 58_700), (2, 1, 42_100)]),
        (15, [(1, 1, 33_200)]),
    ]
    for days_ago, sessions in plan:
        day = base - _dt.timedelta(days=days_ago)
        for j, (tab, pane, tok) in enumerate(sessions):
            sid += 1
            t = day - _dt.timedelta(hours=j)
            recs.append({"ts": t.timestamp(), "tab": tab, "pane": pane,
                         "session": sid, "account": "default", "tokens": tok})
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


async def token_log_session(app, pilot):
    # 토큰 사용량 팝업(세션 뷰) — [p] 로 기간→세션 전환. 행=Claude 세션별 합(대표
    # 탭:패널 라벨)+타임스탬프+토큰. 닫히고 재사용되는 패널 id 대신 안정적 세션 id 로
    # 묶는다(설계 §8). 활성 세션 행은 모델 팔레트 색으로 강조.
    from importlib import import_module
    screens = import_module("pytmuxlib.plugins.claude-code.screens")
    recs, usage = _tklog_data()
    app.push_screen(screens.TokenLogScreen(recs, usage=usage))
    await pilot.pause(0.3)
    await pilot.press("p")          # 기간 → 세션 뷰
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


# 장면: (이름, 설명, 운전함수[, 크기]). 크기 생략 시 SIZE(90×26).
# 큰 달력(블록-숫자)도 표준 SIZE(90×26)에서 그대로 렌더된다: 블록 달력은 패널 높이
# ph≥22 면 뜨는데(render.py nl_big), 26행 터미널의 단일 패널이 이미 그 높이를 준다.
# 따라서 다른 장면과 동일한 세로 길이로 갤러리에 들어간다. 단 2026-02 는 4주(28일)라
# 4주치 높이 기준이며, 5주 달로 기준일을 바꾸면 한 줄 더 필요하니 재확인할 것.
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
    ("09-calendar", "큰 달력 오버레이(cal) — 블록-숫자", calendar_big),
    ("10-confirm-tab", "탭 닫기 확인 박스(탭 2개 이상)", confirm_tab),
    ("14-info-popup", "통합 정보 팝업(캡처·토큰·서버)", info_popup),
    ("15-scrollback", "스크롤백(복사) 모드 — 지난 출력", scrollback),
    ("16-degraded", "네트워크 degraded — 패널 외곽선 빨강", degraded),
    ("17-command-popup", "명령 목록 팝업(? / help) — 카테고리 탭·검색·스크롤", command_popup),
    ("18-clock", "시계 모드 — 큰 블록 시계", clock),
    ("19-confirm-tab-last", "마지막 탭 닫기 — pytmux 종료 경고 팝업", confirm_tab_last),
    ("21-restart-check", "restart-check 드라이런 — 작업보존 재시작 안전점검", restart_check),
    ("26-restart-confirm", "재시작 확인 — 드라이런 FAIL 시 '그래도 재시작?'(기본 취소)", restart_confirm),
    ("23-token-saver", "Claude 설정 팝업(token-saver) — 자동재개·세션종료 토큰화면·오토모드·클리어·경고", token_saver),
    ("24-token-log", "토큰 사용량 팝업(일별) — 노트북 탭+요약줄+기간:토큰 표", token_log),
    ("42-token-log-session", "토큰 팝업(세션) — Claude 세션별 합·탭:패널·타임스탬프", token_log_session),
    ("37-token-log-hour", "토큰 팝업(시간) — 시각별 5h 한도 계단식 막대 + 1w% 열", token_log_hour),
    ("38-token-log-limit", "토큰 팝업(한도) — /usage 막대·창Σ·리셋 카운트다운 통합 탭", token_log_limit),
    ("25-remote-control", "원격 제어 팝업 — [r] 로 /rc 토글", remote_control),
    ("27-ncd", "디렉토리 트리(ncd) — 루트→cwd 펼침·시안 선택 막대·찾기 안내줄", ncd),
    ("28-claude-rules", "시작 규칙 편집(claude-rules) — 멀티라인 에디터·Ctrl+S 저장", claude_rules),
    ("29-usage-panel", "사용 한도(/usage) — 세션 5h·주 전체·주 Sonnet 막대 그래프", usage_panel),
    ("30-usage-view", "usage-view 팝업 — 한도 막대(% 우측정렬)+다음 리셋 블록 카운트다운", usage_view),
    ("31-prompt-history", "프롬프트 히스토리 팝업(prompt-history) — 시간순·미리보기 행수", prompt_history),
    ("39-claude-resume", "Claude 세션 리줌 피커(claude-resume) — 시각·프로젝트·AI 제목 3열", claude_resume),
    ("32-p4changes", "submitted CL 목록(p4changes) — 풀스크린·CL/시각/사용자/설명", p4_changes),
    ("35-p4-describe", "p4changes 상세 — CL 에 Enter → p4 describe 팝업(이중 보더·@CL·변경파일)", p4_describe),
    ("33-ime", "IME 한/영 배지(ime-indicator) — 우상단 상태 배지", ime_badge),
    ("34-remote-attach", "원격 pytmux 탭 어태치 — 분홍 탭바·분홍 패널 외곽선", remote_attach),
    ("40-settings", "통합 설정 화면(:settings) — 좌측 카테고리 탭+우측 전체 설정 목록·←→ 값 변경·링크 행", settings),
    ("41-compose-prompt", "프롬프트 작성창(ESC→Insert) — 블록 선택 멀티라인·Enter 전송·Ctrl+A 전체선택", compose_prompt),
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

# 실제 로그인 사용자명·호스트명(셸 프롬프트 `user@host`·상태줄 `ssh:host`·`/Users/<user>`
# 경로)이 공개 이미지에 베이킹돼 내부 환경이 노출되지 않도록 마스킹한다. 현재 실행 환경에서
# 동적으로 읽어 스크립트에 PII 를 하드코딩하지 않는다(다른 머신에서 돌리면 그 머신 값으로
# 자동 적용). textLength 가 폭을 유지하므로 길이 차이는 자간으로 흡수된다.
_REAL_USER = _getpass.getuser()
_REAL_HOST = _socket.gethostname()
_REAL_HOST_SHORT = _REAL_HOST.split(".")[0]

# 상태줄 host 런(우측 `ssh:host`)은 host/clock/date 가 한 블록으로 **우측 정렬**되므로,
# 실 호스트명이 짧으면 host 가 시계 바로 왼쪽에 붙는다. 그런데 _redact_svg 의 사후
# 문자열치환(긴 실호스트명→"host")은 host 런만 줄이고 뒤따르는 시계/날짜 런의 절대 x 는
# 그대로 둬서(host 와 clock 은 색이 달라 별개 <text>·별개 베이킹 런) 둘 사이에 가짜 간격이
# 생긴다 — 실제 앱에선 없는 거리다. 해결: 렌더 시점부터 마스킹된 짧은 이름("host")을 쓰도록
# socket.gethostname 을 패치해 레이아웃이 처음부터 짧은 host 를 시계 옆에 우측정렬하게 한다
# (셸 프롬프트 `user@host`·홈경로 등 **패널 내용**은 실셸 출력이라 _REAL_* 사후치환 유지 —
# 이쪽은 같은 색 연속 런이라 reflow 돼 간격이 안 생긴다). _REAL_* 캡처 이후에 패치한다.
_socket.gethostname = lambda: "host"


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
# 한글 폴백 폰트: 임베드된 Fira Code 에는 한글 글리프가 없어, 뷰어가 generic
# `monospace` 의 시스템 CJK 폴백(보통 좌우로 널찍한 fullwidth 고딕)으로 한글을 그린다.
# 체인에 macOS·iOS 기본 한글 폰트(Apple SD Gothic Neo)를 끼워넣으면 그 기기에서
# 익숙한 모양으로 렌더된다(타 OS 는 그대로 monospace 폴백). 폭은 textLength 가
# 강제하므로 어떤 폰트로 떨어지든 셀 정렬은 유지된다.
_FONT_FAMILY_RE = _re.compile(r'font-family:\s*Fira Code,\s*monospace')
_FONT_FAMILY_NEW = 'font-family: Fira Code, "Apple SD Gothic Neo", monospace'

_TEXT_RE = _re.compile(r'<text\b([^>]*)>([^<]*)</text>')
_X_RE = _re.compile(r'\bx="([0-9.]+)"')
_TL_RE = _re.compile(r'\btextLength="([0-9.]+)"')


def _svg_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace("\xa0", "&#160;"))


def _fix_cjk_textlength(svg):
    """와이드 문자가 섞인 <text> 를 폭-동질 런으로 쪼개 셀 그리드에 다시 정렬한다.

    멱등: 이미 처리된 와이드 런(lengthAdjust="spacingAndGlyphs" 보유)은 건너뛴다.
    다시 돌리면 cell_w 를 textLength/n 으로 오산해 textLength 가 배로 부푼다."""

    def repl(m):
        attrs, content = m.group(1), m.group(2)
        if "spacingAndGlyphs" in attrs:
            return m.group(0)            # 이미 처리된 와이드 런 → 그대로(멱등)
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


# ---------- <text> → 폰트 비의존 벡터/도형 굽기 ----------
# README 스크린샷 SVG 는 GitHub 에서 <img> 로 삽입돼 "이미지 보안 모드"로 렌더된다. 이
# 모드는 @font-face(CDN url() 이든 base64 임베드든)를 적용하지 않아, 무엇을 해도 글자가
# 뷰어의 generic monospace 로 폴백한다 → 박스선 끊김·블록문자 행간 갭·자간 왜곡·한글 두부.
# 폰트 임베드로는 못 고친다. 견고한 해법은 글자를 폰트 비의존 벡터로 굽는 것이다
# ([[svg-text-cjk-breaks-bake-paths]] 의 한글 path 굽기를 전 글자로 확장):
#   ① 박스드로잉·블록·음영(U+2500–259F) → 셀 격자에서 계산한 도형(line/rect). 폰트
#      글리프는 em(=font-size 20px) 높이라 row pitch 24.4px 를 못 채워 세로 갭이 남는다
#      → 도형만이 셀을 정확히 채우고(블록) 선을 셀 경계까지 이어붙인다(박스선).
#   ② ASCII·숫자·기호 등 narrow 글자 → Fira Code 글리프 path. Fira advance(≈0.615em)가
#      셀폭(12.2px)과 거의 같아 셀 원점 좌측정렬만으로 모노스페이스 격자가 보존된다.
#   ③ CJK(2칸) → Apple SD Gothic Neo 글리프 path. 단 2 셀(24.4px)에 억지로 늘리지 않고
#      (옛 가로 stretch ×1.41 제거) 자연 폭 그대로 2셀 박스 가운데 배치한다.
# 폰트에 없는 글리프(이모지 ✅ 등)나 fontTools/폰트 부재(비 macOS) 시엔 굽지 않고 <text>
# 로 남겨 font-family 폴백에 맡긴다. macOS 로컬 생성이 권위.
_CJK_TTC = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
_CJK_FACE = {"regular": 0, "bold": 6}
_CJK_FILL = 0.90        # CJK 글리프가 2셀 박스에서 채우는 폭 비율(균등 확대·뚱뚱X·갭X)
_SYM_TTF = "/System/Library/Fonts/Apple Symbols.ttf"  # Fira 에 없는 기호(▸ ⚙ 등) 폴백
_MENLO_TTC = "/System/Library/Fonts/Menlo.ttc"        # ❯ ✻ ✕ 등 추가 폴백
_MENLO_FACE = {"regular": 0, "bold": 1}
_FIRA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FIRA_TTF = {"regular": os.path.join(_FIRA_DIR, "FiraCode-Regular.ttf"),
             "bold": os.path.join(_FIRA_DIR, "FiraCode-Bold.ttf")}
_CLASS_CSS_RE = _re.compile(r"\.(terminal-\d+-r\d+)\s*\{([^}]*)\}")
_MATRIX_FS_RE = _re.compile(r"-matrix\s*\{[^}]*?font-size:\s*([\d.]+)")
_CSS_FILL_RE = _re.compile(r"fill:\s*([^;]+)")
_CLASS_RE = _re.compile(r'\bclass="([^"]*)"')
_Y_RE = _re.compile(r'\by="([0-9.]+)"')
_CLIP_RE = _re.compile(r'\bclip-path="([^"]*)"')
_CLIPDEF_RE = _re.compile(
    r'<clipPath id="([^"]*-line-\d+)">\s*<rect x="0" y="([0-9.]+)"'
    r' width="\d+" height="([0-9.]+)"')
_bake_font_cache = {}


def _bake_font(kind, weight):
    """('fira'|'cjk', 'regular'|'bold') 의 폰트 정보 dict 또는 None(부재/미설치)."""
    key = (kind, weight)
    if key in _bake_font_cache:
        return _bake_font_cache[key]
    info = None
    try:
        from fontTools.ttLib import TTFont, TTCollection
        from fontTools.pens.svgPathPen import SVGPathPen
        if kind == "cjk":
            f = TTCollection(_CJK_TTC).fonts[_CJK_FACE[weight]]
        elif kind == "sym":
            f = TTFont(_SYM_TTF)                       # Apple Symbols(weight 무관)
        elif kind == "menlo":
            f = TTCollection(_MENLO_TTC).fonts[_MENLO_FACE[weight]]
        else:
            f = TTFont(_FIRA_TTF[weight])
        info = {
            "cmap": f.getBestCmap(),
            "glyphset": f.getGlyphSet(),
            "hmtx": f["hmtx"].metrics,
            "upm": f["head"].unitsPerEm,
            "pen": SVGPathPen,
        }
    except Exception:                                 # noqa: BLE001 (폰트/툴 부재)
        info = None
    _bake_font_cache[key] = info
    return info


def _glyph_path(kind, ch, weight, x, y, fsize, fill, *,
                center_in=None, cell_top=None, pitch=None):
    """글리프 외곽선을 <path> 로. 폰트/글리프 부재 시 None, 빈 글리프(공백) 시 ''.

    center_in 이 주어지면(=박스폭 px, CJK 2칸) 글리프를 가로 stretch(=뚱뚱) 없이 균등
    확대해 박스 폭의 _CJK_FILL 만큼을 채우고(advance 기준 → 음절 간격 균일) 가로 가운데
    배치한다. cell_top·pitch 도 주면 ink bbox(ymin..ymax)를 셀에 세로 가운데 정렬한다
    (ASCII 베이스라인에 앵커하면 균등 확대된 한글 윗부분이 셀 top 을 넘어 잘림). 아니면
    셀 원점 좌측정렬·ASCII 베이스라인(narrow)."""
    info = _bake_font(kind, weight)
    if info is None:
        return None
    gn = info["cmap"].get(ord(ch))
    if gn is None:
        return None
    s = fsize / info["upm"]
    pen = info["pen"](info["glyphset"])
    info["glyphset"][gn].draw(pen)
    d = pen.getCommands()
    if not d:
        return ""                                     # 공백 등 빈 path
    adv0 = info["hmtx"][gn][0] * s
    if center_in is not None and adv0 > 0:
        s *= _CJK_FILL * center_in / adv0             # 균등 확대(가로·세로 동일)
        x += max(0.0, (center_in - info["hmtx"][gn][0] * s) / 2.0)
        if cell_top is not None and pitch is not None:
            from fontTools.pens.boundsPen import BoundsPen
            bp = BoundsPen(info["glyphset"])
            info["glyphset"][gn].draw(bp)
            if bp.bounds:
                _, ymn, _, ymx = bp.bounds
                y = cell_top + (pitch - (ymx - ymn) * s) / 2.0 + ymx * s
    fa = f' fill="{fill}"' if fill else ""
    return (f'<path{fa} transform="translate({x:g} {y:g}) '
            f'scale({s:g} {-s:g})" d="{d}"/>')


# 박스드로잉·블록·음영 도형. 셀=(x0,top)~(x0+w, top+h). 박스선은 셀 경계까지 그어 인접
# 셀과 이어지게(butt cap), 블록은 인접 셀로 EPS 만큼 겹쳐 안티에일리어싱 실선 틈을 없앤다.
_LT = 1.8       # light 선 두께
_DOFF = 1.7     # double 선 중심 간 ±오프셋
_BLK_EPS = 0.8  # 블록 채움 겹침(인접 셀로 번져 AA 이음선 제거)


def _box_block(o, x0, top, w, h, fill):
    """U+2500–259F 한 글자를 도형 SVG 로(미지원 시 None → 폰트 path 폴백)."""
    c = fill or "#000000"
    r, b = x0 + w, top + h                            # right, bottom
    cx, cy = x0 + w / 2, top + h / 2
    E = _BLK_EPS

    def ln(d):                                        # 선(박스드로잉)
        return f'<path d="{d}" stroke="{c}" stroke-width="{_LT:g}" fill="none"/>'

    def rc(x, yy, ww, hh, op=None):                   # 채움(블록/음영)
        o2 = f' fill-opacity="{op}"' if op is not None else ""
        return f'<rect x="{x:g}" y="{yy:g}" width="{ww:g}" height="{hh:g}" fill="{c}"{o2}/>'

    # ----- 박스드로잉(light) -----
    if o == 0x2500:  # ─
        return ln(f"M{x0:g},{cy:g} H{r:g}")
    if o == 0x2502:  # │
        return ln(f"M{cx:g},{top:g} V{b:g}")
    if o == 0x250C:  # ┌
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x2510:  # ┐
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy:g} L{x0:g},{cy:g}")
    if o == 0x2514:  # └
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x2518:  # ┘
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy:g} L{x0:g},{cy:g}")
    if o == 0x251C:  # ├
        return ln(f"M{cx:g},{top:g} V{b:g} M{cx:g},{cy:g} H{r:g}")
    if o == 0x2524:  # ┤
        return ln(f"M{cx:g},{top:g} V{b:g} M{x0:g},{cy:g} H{cx:g}")
    if o == 0x252C:  # ┬
        return ln(f"M{x0:g},{cy:g} H{r:g} M{cx:g},{cy:g} V{b:g}")
    if o == 0x2534:  # ┴
        return ln(f"M{x0:g},{cy:g} H{r:g} M{cx:g},{top:g} V{cy:g}")
    if o == 0x253C:  # ┼
        return ln(f"M{x0:g},{cy:g} H{r:g} M{cx:g},{top:g} V{b:g}")
    # 둥근 모서리(arc). 반지름 rr.
    rr = min(w, h) * 0.45
    if o == 0x256D:  # ╭ down+right
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy + rr:g} Q{cx:g},{cy:g} "
                  f"{cx + rr:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x256E:  # ╮ down+left
        return ln(f"M{cx:g},{b:g} L{cx:g},{cy + rr:g} Q{cx:g},{cy:g} "
                  f"{cx - rr:g},{cy:g} L{x0:g},{cy:g}")
    if o == 0x2570:  # ╰ up+right
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy - rr:g} Q{cx:g},{cy:g} "
                  f"{cx + rr:g},{cy:g} L{r:g},{cy:g}")
    if o == 0x256F:  # ╯ up+left
        return ln(f"M{cx:g},{top:g} L{cx:g},{cy - rr:g} Q{cx:g},{cy:g} "
                  f"{cx - rr:g},{cy:g} L{x0:g},{cy:g}")
    # ----- 박스드로잉(double) -----
    d = _DOFF
    if o == 0x2550:  # ═
        return (ln(f"M{x0:g},{cy - d:g} H{r:g}") + ln(f"M{x0:g},{cy + d:g} H{r:g}"))
    if o == 0x2551:  # ║
        return (ln(f"M{cx - d:g},{top:g} V{b:g}") + ln(f"M{cx + d:g},{top:g} V{b:g}"))
    if o == 0x2554:  # ╔ down+right
        return (ln(f"M{cx - d:g},{b:g} L{cx - d:g},{cy - d:g} L{r:g},{cy - d:g}")
                + ln(f"M{cx + d:g},{b:g} L{cx + d:g},{cy + d:g} L{r:g},{cy + d:g}"))
    if o == 0x2557:  # ╗ down+left
        return (ln(f"M{cx + d:g},{b:g} L{cx + d:g},{cy - d:g} L{x0:g},{cy - d:g}")
                + ln(f"M{cx - d:g},{b:g} L{cx - d:g},{cy + d:g} L{x0:g},{cy + d:g}"))
    if o == 0x255A:  # ╚ up+right
        return (ln(f"M{cx - d:g},{top:g} L{cx - d:g},{cy + d:g} L{r:g},{cy + d:g}")
                + ln(f"M{cx + d:g},{top:g} L{cx + d:g},{cy - d:g} L{r:g},{cy - d:g}"))
    if o == 0x255D:  # ╝ up+left
        return (ln(f"M{cx + d:g},{top:g} L{cx + d:g},{cy + d:g} L{x0:g},{cy + d:g}")
                + ln(f"M{cx - d:g},{top:g} L{cx - d:g},{cy - d:g} L{x0:g},{cy - d:g}"))
    # ----- 블록(채움) -----
    if o == 0x2588:  # █ full
        return rc(x0, top, w + E, h + E)
    if o == 0x2580:  # ▀ upper half
        return rc(x0, top, w + E, h / 2 + E)
    if o == 0x2584:  # ▄ lower half
        return rc(x0, cy, w + E, h / 2 + E)
    if o == 0x2590:  # ▐ right half
        return rc(cx, top, w / 2 + E, h + E)
    # 좌측 8분할(▏▎▍▌▋▊▉ = 1/8..7/8)
    _LEFT = {0x258F: 1, 0x258E: 2, 0x258D: 3, 0x258C: 4,
             0x258B: 5, 0x258A: 6, 0x2589: 7}
    if o in _LEFT:
        return rc(x0, top, w * _LEFT[o] / 8 + E, h + E)
    # 하단 8분할(▁▂▃▄▅▆▇ = 1/8..7/8, 셀 아래쪽 정렬)
    _LOW = {0x2581: 1, 0x2582: 2, 0x2583: 3, 0x2584: 4,
            0x2585: 5, 0x2586: 6, 0x2587: 7}
    if o in _LOW:
        fr = _LOW[o] / 8
        return rc(x0, b - h * fr, w + E, h * fr + E)
    # 상단 8분할(▔ = 1/8, 셀 위쪽 정렬)
    if o == 0x2594:  # ▔ upper 1/8
        return rc(x0, top, w + E, h / 8 + E)
    if o == 0x2595:  # ▕ right 1/8
        return rc(r - w / 8, top, w / 8 + E, h + E)
    # 음영(░▒▓ = 25%/50%/75% 불투명 채움 근사)
    _SHADE = {0x2591: 0.25, 0x2592: 0.5, 0x2593: 0.75}
    if o in _SHADE:
        return rc(x0, top, w + E, h + E, op=_SHADE[o])
    # 사분면
    if o == 0x2598:  # ▘ UL
        return rc(x0, top, w / 2 + E, h / 2 + E)
    if o == 0x259D:  # ▝ UR
        return rc(cx, top, w / 2 + E, h / 2 + E)
    if o == 0x2596:  # ▖ LL
        return rc(x0, cy, w / 2 + E, h / 2 + E)
    if o == 0x2597:  # ▗ LR
        return rc(cx, cy, w / 2 + E, h / 2 + E)
    if o == 0x259B:  # ▛ UL+UR+LL
        return rc(x0, top, w + E, h / 2 + E) + rc(x0, cy, w / 2 + E, h / 2 + E)
    if o == 0x259C:  # ▜ UL+UR+LR
        return rc(x0, top, w + E, h / 2 + E) + rc(cx, cy, w / 2 + E, h / 2 + E)
    if o == 0x2599:  # ▙ UL+LL+LR
        return rc(x0, top, w / 2 + E, h + E) + rc(cx, cy, w / 2 + E, h / 2 + E)
    if o == 0x259F:  # ▟ UR+LL+LR
        return rc(cx, top, w / 2 + E, h + E) + rc(x0, cy, w / 2 + E, h / 2 + E)
    return None


def _bake_glyphs(svg):
    """모든 <text> 를 폰트 비의존 벡터/도형으로 굽는다(불가 글자는 <text> 유지).

    ① U+2500–259F → 도형  ② narrow → Fira Code path  ③ CJK(2칸) → ASDGN path(가운데).
    멱등: 이미 <path>/<rect> 로 구워진 SVG 엔 남은 <text> 만 다시 처리된다."""
    fsm = _MATRIX_FS_RE.search(svg)
    if not fsm:
        return svg
    fsize = float(fsm.group(1))
    weight_of, fill_of = {}, {}
    for cls, css in _CLASS_CSS_RE.findall(svg):
        weight_of[cls] = ("bold" if ("bold" in css or "font-weight: 7" in css)
                          else "regular")
        fm = _CSS_FILL_RE.search(css)
        if fm:
            fill_of[cls] = fm.group(1).strip()
    # clip id → (셀 top, 셀 높이). row pitch 는 연속 라인 top 차로 구한다.
    clip_top = {}
    rects = []
    for cid, ry, rh in _CLIPDEF_RE.findall(svg):
        clip_top[cid] = float(ry)
        rects.append(float(ry))
    rects.sort()
    pitch = (rects[1] - rects[0]) if len(rects) >= 2 else fsize * 1.22
    # 셀폭: textLength/cell_len 의 최빈값(보통 12.2 = 0.61em).
    cw_votes = {}
    for am, content in ((m.group(1), m.group(2)) for m in _TEXT_RE.finditer(svg)):
        tlm = _TL_RE.search(am)
        if not tlm:
            continue
        n = _cell_len(_html.unescape(content))
        if n:
            cw = round(float(tlm.group(1)) / n, 3)
            cw_votes[cw] = cw_votes.get(cw, 0) + 1
    cell_w = max(cw_votes, key=cw_votes.get) if cw_votes else fsize * 0.61
    ascent = fsize * 0.925                             # baseline - 셀top(폰트20→18.5)

    def repl(m):
        attrs, content = m.group(1), m.group(2)
        text = _html.unescape(content)
        xm = _X_RE.search(attrs)
        ym = _Y_RE.search(attrs)
        clsm = _CLASS_RE.search(attrs)
        if not (xm and ym and clsm) or not text:
            return m.group(0)
        x, y, cls = float(xm.group(1)), float(ym.group(1)), clsm.group(1)
        weight = weight_of.get(cls, "regular")
        fill = fill_of.get(cls)
        clipm = _CLIP_RE.search(attrs)
        clip = clipm.group(1) if clipm else None
        top = None
        if clip:
            cid = clip[clip.find("#") + 1:].rstrip(")")
            top = clip_top.get(cid)
        if top is None:
            top = y - ascent
        out, col = [], 0
        for ch in text:
            cells = _cell_len(ch)
            if cells <= 0:
                continue                              # 0폭 결합문자 → 건너뜀
            cx0 = x + col * cell_w
            col += cells
            o = ord(ch)
            frag = None
            if ch == " ":
                frag = ""
            elif cells >= 2:                          # ③ CJK
                frag = _glyph_path("cjk", ch, weight, cx0, y, fsize, fill,
                                   center_in=cells * cell_w,
                                   cell_top=top, pitch=pitch)
            elif 0x2500 <= o <= 0x259F:               # ① 도형
                frag = _box_block(o, cx0, top, cell_w, pitch, fill)
            if frag is None and cells < 2:            # ② narrow → Fira → 기호 폴백
                for _k in ("fira", "sym", "menlo"):   # Fira 에 없는 기호(▸ ⚙ ❯ 등)
                    frag = _glyph_path(_k, ch, weight, cx0, y, fsize, fill)
                    if frag is not None:
                        break
            if frag is None:                          # 못 구움 → <text> 유지(폴백)
                frag = (f'<text class="{cls}" x="{cx0:g}" y="{y:g}" '
                        f'textLength="{cells * cell_w:g}" '
                        f'lengthAdjust="spacingAndGlyphs">'
                        f'{_svg_escape(ch)}</text>')
            out.append(frag)
        # per-line clip 은 붙이지 않는다: 굽힌 글리프·도형은 셀 크기로 그려져 행을 넘지
        # 않고, 블록은 인접 셀로 EPS 만큼 일부러 번지게 해 AA 이음선을 없앤다(per-line
        # clip 은 그 번짐을 0.25px 로 잘라 세로 이음선을 남긴다). 바깥 경계는 상위
        # clip-terminal <g> 가 처리하므로 넘침 위험 없다.
        return "".join(out)

    return _TEXT_RE.sub(repl, svg)


def _postprocess_cjk(svg):
    """한글 렌더 후처리(멱등): font-family 폴백 → 자간 보정 → path 굽기.

    Rich/Textual SVG 면 어떤 생성 경로(매뉴얼 컷·플러그인 screenshot.svg)든 동일하게
    적용 가능하다. 이미 처리된 SVG 에 다시 돌려도 결과가 같다."""
    svg = _FONT_FAMILY_RE.sub(_FONT_FAMILY_NEW, svg)
    svg = _fix_cjk_textlength(svg)
    svg = _bake_glyphs(svg)
    return svg


def _redact_svg(path):
    """저장한 SVG 의 PII 마스킹 + 한글 자간(textLength) 보정.

    PII: 실제 `claude` 실행 화면에는 로그인 계정 이메일이 환영 배너·상태줄에, 사용자 이름이
    "Welcome back <이름>!" 배너에 뜬다. 공개 저장소에 커밋되는 이미지라 이메일은
    user@example.com 으로, 환영 이름은 "Welcome back!" 으로 마스킹한다(다른 텍스트는
    그대로). textLength 속성이 있어 폭은 유지된다.

    자간: Rich 의 와이드 문자 textLength 버그를 _fix_cjk_textlength 로 교정한다.

    한글 폰트: ① font-family 체인에 Apple SD Gothic Neo 를 끼우고(_FONT_FAMILY_RE),
    ② CJK <text> 는 _bake_cjk_paths 가 글리프 외곽선 path 로 구워 뷰어 폰트 의존을
    없앤다(폰트에 없는 ✅ 등·비 macOS 환경에선 굽지 않고 ①폴백에 맡긴다)."""
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read()
    except OSError:
        return
    new = _EMAIL_RE.sub("user@example.com", svg)
    new = _WELCOME_RE.sub(">Welcome&#160;back!</text>", new)
    # 실제 사용자명/호스트명 마스킹(셸 프롬프트·상태줄·홈경로). full 호스트→short 순서로
    # 치환해 잔여 `.local` 이 남지 않게 한다.
    for _real, _repl in ((_REAL_HOST, "host"), (_REAL_HOST_SHORT, "host"),
                         (_REAL_USER, "user")):
        if _real and _real not in ("host", "user"):
            new = new.replace(_real, _repl)
    new = _postprocess_cjk(new)
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
