#!/usr/bin/env python3
"""매뉴얼용 실제 스크린샷 생성기 — docs/SCREENSHOT_SCENARIO.md 방식 ①(Textual SVG).

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
    app.open_claude_saver()
    await pilot.pause(0.5)


async def token_log(app, pilot):
    # 토큰 사용량 팝업 — 마우스 서브탭(시간/일/주/월) + M19 /usage 실측 한도.
    from pytmuxlib.clientscreens import TokenLogScreen
    recs = [
        {"ts": 1_700_000_000.0, "tab": 0, "pane": 1, "session": 1,
         "account": "default", "tokens": 12300},
        {"ts": 1_700_400_000.0, "tab": 1, "pane": 2, "session": 2,
         "account": "default", "tokens": 8200},
    ]
    usage = {"session": {"pct": 2, "reset": "2pm (Asia/Seoul)"},
             "week_all": {"pct": 14, "reset": "Jun 13 at 3am (Asia/Seoul)"},
             "week_sonnet": {"pct": 0, "reset": "Jun 13 at 3am (Asia/Seoul)"}}
    app.push_screen(TokenLogScreen(recs, usage=usage))
    await pilot.pause(0.5)


async def remote_control(app, pilot):
    # 원격 제어(Remote Control) 정보+토글 팝업 — [r] 로 /rc 주입해 켜고 끈다.
    app.open_remote_control(_aid(app))
    await pilot.pause(0.5)


async def ncd(app, pilot):
    # ncd(Norton Change Directory 풍 디렉토리 트리) — 명령으로 열고 실제 서버의
    # 루트→cwd 사슬 응답(nc_list)으로 NcdScreen 이 뜰 때까지 기다린다.
    from pytmuxlib.plugins.ncd.screen import NcdScreen
    app.request_nc_list()
    for _ in range(80):
        await pilot.pause(0.05)
        if isinstance(app.screen, NcdScreen):
            break
    await pilot.pause(0.6)


# 진짜 Claude Code 한 세션에서 캡처하는 §11 컷 묶음(라이브 — 실제 API 호출).
CLAUDE_OUTPUTS = ["11-claude", "12-claude-autoresume", "13-perm-mode",
                  "20-prompt-history", "22-claude-real"]


async def _claude_suite_once():
    """진짜 `claude` 한 세션을 운전해 §11 세부 컷 5장을 모두 캡처한다.

    프롬프트 1회로 idle(응답완료)·autoresume·권한모드 팝업·히스토리 팝업을 찍고,
    프롬프트 2회째의 busy 상태로 처리중(◐) 컷을 찍는다(환영 배너가 위로 밀려 계정
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
            # ── 프롬프트 히스토리 팝업(헤더 클릭) — 보낸 프롬프트 2개가 시간순
            app.open_prompt_history(aid)
            await pilot.pause(0.5)
            await shot(pilot, "20-prompt-history")
            await pilot.press("escape")
            await pilot.pause(0.2)
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
    ("05-menu", "메뉴(prefix Enter)", menu),
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
    ("24-token-log", "토큰 사용량 팝업 — 마우스 서브탭(시간/일/주/월) + /usage 실측 한도", token_log),
    ("25-remote-control", "원격 제어 팝업 — [r] 로 /rc 토글", remote_control),
    ("27-ncd", "디렉토리 트리(ncd) — 루트→cwd 펼침·시안 선택 막대·찾기 안내줄", ncd),
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
# 문자가 절반 폭으로 압축돼 자간이 좁아지고 글자가 겹쳐 보인다. x 좌표는 cell_len 기준이라
# 정상이므로 textLength 만 cell_len 기준으로 다시 늘리면 그리드에 맞게 펼쳐진다.
_TEXT_RE = _re.compile(
    r'(<text\b[^>]*?textLength=")([0-9.]+)("[^>]*>)([^<]*)(</text>)'
)


def _fix_cjk_textlength(svg):
    """와이드 문자가 든 <text> 의 textLength 를 셀폭 기준으로 보정한다."""

    def repl(m):
        pre, length, mid, content, end = m.groups()
        text = _html.unescape(content)
        n = len(text)
        cells = _cell_len(text)
        if n == 0 or cells == 0 or cells == n:
            return m.group(0)            # 와이드 문자 없음 → 그대로
        char_width = float(length) / n   # 셀당 px (모노스페이스)
        return f"{pre}{char_width * cells:g}{mid}{content}{end}"

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
