#!/usr/bin/env python3
"""매뉴얼용 실제 스크린샷 생성기 — docs/SCREENSHOT_SCENARIO.md 방식 ①(Textual SVG).

진짜 서버(임시 소켓)를 띄우고 **실제 클라이언트 앱**(`build_client_app`)을 Textual
헤드리스(`run_test`)로 운전해, 각 장면을 클라가 실제로 그리는 그대로 **SVG** 로 떠
`docs/img/` 에 저장한다. 위젯 상태 단언이 아니라 사용자가 보는 화면 그 자체다.

  python3 scripts/gen_screenshots.py            # 전체 생성 → docs/img/*.svg
  python3 scripts/gen_screenshots.py 02-split   # 이름에 매칭되는 장면만

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

# Claude Code CLI 화면을 흉내 내는 스탠드인. 서버의 Claude 휴리스틱
# (pytmuxlib/claude.py)이 **출력 텍스트**로 패널을 Claude 로 감지하므로(명령 이름이
# 아님), 이 화면을 패널에서 띄우면 진짜 서버 경로로 헤더 예약·상태아이콘·토큰 집계가
# 동작한다 — 즉 Claude 연동 스크린샷도 가짜 상태 주입 없이 실제로 캡처된다.
FAKE_CLAUDE = """\
import sys, time
F = '''\\
\\u273b Welcome to Claude Code

> \\ub9ac\\ud329\\ud130\\ub9c1\\ud558\\uace0 \\ud14c\\uc2a4\\ud2b8 \\ucd94\\uac00\\ud574\\uc918

\\u25cf Crunching\\u2026 (38s \\u00b7 \\u2193 1.9k tokens \\u00b7 esc to interrupt)
  Read pytmuxlib/model.py (94 lines)
  Update pytmuxlib/model.py
  Update tests/test_model.py
  Bash(python3 tests/run.py)
  \\u23bf  278 passed, 0 failed

  12.3k tokens \\u00b7 ctx 48%

\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500
 >
\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500\\u2500
  \\u23f5\\u23f5 auto-accept edits on (shift+tab to cycle)
'''
sys.stdout.write("\\x1b[2J\\x1b[H" + F)
sys.stdout.flush()
time.sleep(120)
"""
_FC_PATH = "/tmp/pytmux_fakeclaude.py"


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


async def _run_fake_claude(pilot, app):
    """활성 패널에서 Claude 흉내 스탠드인을 띄우고, 서버가 Claude 로 감지할 때까지
    대기한다(출력 기반 휴리스틱이라 진짜 서버 경로로 동작)."""
    with open(_FC_PATH, "w", encoding="utf-8") as f:
        f.write(FAKE_CLAUDE)
    for ch in f"python3 {_FC_PATH}":
        await pilot.press("space" if ch == " " else ch)
    await pilot.press("enter")
    for _ in range(120):
        await pilot.pause(0.05)
        ci = app.pane_claude.get(_aid(app), {})
        if ci.get("claude") and ci.get("prompt"):
            return True
    return False


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
    app.send_cmd("split", orient="lr")
    await _settle(pilot, app, want_panes=2)
    app.open_prompt("confirm", "kill-pane? (y/N)",
                    action=lambda: app.send_cmd("kill_pane"))
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


async def claude(app, pilot):
    app.send_cmd("rename_window", name="claude")
    await pilot.pause(0.3)
    await _run_fake_claude(pilot, app)
    await pilot.pause(0.5)


async def claude_autoresume(app, pilot):
    app.send_cmd("rename_window", name="claude")
    await pilot.pause(0.3)
    await _run_fake_claude(pilot, app)
    app.send_cmd("set_autoresume")     # prefix R = 토큰리밋 자동재개 토글 → 상태줄 AR
    await pilot.pause(0.6)


async def perm_mode(app, pilot):
    from pytmuxlib.clientscreens import PermModeScreen
    app.push_screen(PermModeScreen("auto"))
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


async def prompt_history(app, pilot):
    # Claude 패널의 스티키 헤더(첫 줄, 처리 중 프롬프트)를 클릭하면 프롬프트
    # 히스토리 팝업이 열린다. 대표적인 히스토리를 넣어 시간순 목록을 보인다.
    app.send_cmd("rename_window", name="claude")
    await pilot.pause(0.3)
    await _run_fake_claude(pilot, app)
    aid = _aid(app)
    info = app.pane_claude.get(aid) or {}
    info["history"] = [
        "버그 재현 케이스 먼저 작성해줘",
        "model.py 의 렌더 캐시 무효화 정리",
        "리팩터링하고 테스트 추가해줘",
    ]
    app.pane_claude[aid] = info
    app.open_prompt_history(aid)
    await pilot.pause(0.4)


async def restart_check(app, pilot):
    # restart-all 드라이런(restart-check) — 실제로 안 하고 안전 점검 PASS/FAIL 팝업.
    app._show_restart_check_popup({
        "reexec_supported": True, "has_sessions": True, "serialize_ok": True,
        "panes": 3, "panes_with_fd": 3,
        "running_version": "p4:5281", "disk_version": "p4:5290",
    })
    await pilot.pause(0.4)


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
    ("07-kill-pane-prompt", "패널 닫기 확인 프롬프트(prefix x)", kill_pane_prompt),
    ("08-tabs-multi", "탭 여러 개 + 이름변경", tabs_multi),
    ("09-calendar", "큰 달력 오버레이(cal) — 블록-숫자", calendar_big, BIG),
    ("10-confirm-tab", "탭 닫기 확인 박스(탭 2개 이상)", confirm_tab),
    ("11-claude", "Claude 처리중 — 탭 아이콘 ◐·스티키 헤더·토큰", claude),
    ("12-claude-autoresume", "Claude + 토큰리밋 자동재개(상태줄 AR)", claude_autoresume),
    ("13-perm-mode", "Claude 권한모드 선택 팝업(auto/default/plan)", perm_mode),
    ("14-info-popup", "통합 정보 팝업(캡처·토큰·서버)", info_popup),
    ("15-scrollback", "스크롤백(복사) 모드 — 지난 출력", scrollback),
    ("16-degraded", "네트워크 degraded — 패널 외곽선 빨강", degraded),
    ("17-command-popup", "명령 목록 팝업(? / help) — 카테고리 탭·검색·스크롤", command_popup),
    ("18-clock", "시계 모드 — 큰 블록 시계", clock),
    ("19-confirm-tab-last", "마지막 탭 닫기 — pytmux 종료 경고 팝업", confirm_tab_last),
    ("20-prompt-history", "Claude 프롬프트 히스토리 팝업(헤더 클릭)", prompt_history),
    ("21-restart-check", "restart-check 드라이런 — 작업보존 재시작 안전점검", restart_check),
]


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
    finally:
        await teardown(srv, task, sock)


async def shoot(name, desc, drive, retries=4, size=SIZE):
    path = os.path.join(OUT_DIR, name + ".svg")
    for attempt in range(1, retries + 1):
        await _one_shot(name, desc, drive, path, size)
        if not _is_blank(path):
            print(f"  ✓ {name}.svg  — {desc}")
            return path
        print(f"  … {name} 빈 프레임, 재시도 {attempt}/{retries}")
    print(f"  ✗ {name}.svg  — {retries}회 모두 빈 프레임(레이스)")
    return path


async def _worker(filt):
    """워커 모드: filt 에 매칭되는 장면을 **이 프로세스에서** 생성한다."""
    todo = [s for s in SCENES if filt in s[0]]
    if not todo:
        print(f"매칭 장면 없음: {filt!r}\n사용 가능: " +
              ", ".join(s[0] for s in SCENES))
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
        # 워커 모드(장면 이름 지정) — 이 프로세스에서 생성.
        sys.exit(asyncio.run(_worker(sys.argv[1])))
    # 인자 없음 — 장면별 서브프로세스로 전체 생성(권장 경로).
    sys.exit(_orchestrate())
