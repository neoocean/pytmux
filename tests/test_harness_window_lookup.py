"""라이브 하네스(`client/scripts/*.ps1`)의 **창 찾기는 한 벌**이어야 한다.

왜 게이트가 필요한가 — 이 규칙이 깨졌을 때 무슨 일이 났는지(pytmux-32, 2026-08-03):

`pytmux-gui` 프로세스는 최상위 창을 여러 개 갖는다. 그중 winit 의 숨은
`Winit Thread Event Target` 은 **15×15 인데 보이고(visible) 소유자도 없다**(owner=0).
즉 하네스 여덟이 복붙해 쓰던 술어

    pid == want && IsWindowVisible(h) && GetWindow(h, GW_OWNER) == 0

를 **그대로 만족한다**. EnumWindows 는 Z 순서로 도니, 그 숨은 창이 앱 창보다 위로 올라오는
순간(예: 앱 창을 최소화하면 바로 그렇게 된다) 일곱 스크립트가 **그것을 앱 창으로 집었다**:
SendInput 은 성공을 찍고 글자는 아무 데도 안 들어가고, SetForegroundWindow 는 먹지 않아
전경이 바탕화면으로 가고, GetWindowRect 는 15×15 를 준다.

그 셋이 "최소화했다 복원하면 창이 15×15 로 남고 키를 하나도 안 받는다"로 **제품 결함처럼**
기록됐다. 실제로는 제품이 멀쩡했다(ShowWindow·WM_SYSCOMMAND 둘 다 정상 복원을 실측).
잘못은 **같은 술어가 여덟 군데 복사돼 있었다는 것**이고, 그래서 한 군데를 고쳐도 나머지가
계속 거짓말을 했다.

이 테스트는 그 복붙이 **돌아오는 것**을 막는다. 판정은 파일만 읽어 하므로 OS 를 안 탄다.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "client", "scripts")

# 창 목록을 스스로 훑어도 되는 예외.
#   winlib.ps1                — 공유 리졸버(정본)
#   launch_console_window.ps1 — pid 로 못 찾는 콘솔 창을 **띄우기 전후 목록 차이**로
#                               확정한다. pid 기반이 아니라 성격이 다르고, 자체 크기
#                               가드(200×120)를 갖는다.
ENUM_ALLOWED = {"winlib.ps1", "launch_console_window.ps1"}

CALL_ENUM = re.compile(r"EnumWindows\(\(")
DOT_SOURCE = re.compile(r'^\s*\.\s+"\$PSScriptRoot\\winlib\.ps1"', re.M)
OWN_FINDER = re.compile(r"\bTopLevel\s*\(")


def _scripts():
    if not os.path.isdir(SCRIPTS):
        from run import skip
        skip("client/scripts 가 없다(파이썬만 있는 체크아웃)")
    return sorted(f for f in os.listdir(SCRIPTS) if f.endswith(".ps1"))


def _read(name):
    with open(os.path.join(SCRIPTS, name), encoding="utf-8-sig") as f:
        return f.read()


async def test_the_shared_resolver_exists():
    src = _read("winlib.ps1")
    assert "function Get-AppWindow" in src, "공유 리졸버가 사라졌다"
    # 숨은 15×15 창을 거르는 축과, 최소화된 우리 창을 살리는 축 **둘 다** 있어야 한다.
    # 하나만 남으면 pytmux-32 의 두 얼굴 중 하나가 되살아난다:
    #   MIN_EDGE 만 → 최소화된 우리 창(158×26)까지 걸러 "창이 없다"로 떨어진다
    #   IsIconic 만 → 15×15 숨은 창을 다시 집는다
    assert "MIN_EDGE" in src, "크기 하한이 없다 — 숨은 이벤트 창을 다시 집는다"
    assert "IsIconic" in src, "최소화 판정이 없다 — 최소화된 우리 창을 놓친다"


async def test_no_script_reimplements_window_lookup():
    """창 목록을 스스로 훑는 스크립트는 예외 둘뿐이다."""
    offenders = [f for f in _scripts()
                 if f not in ENUM_ALLOWED and CALL_ENUM.search(_read(f))]
    assert not offenders, (
        "창 찾기를 다시 구현했다: " + ", ".join(offenders) +
        "\n  → `. \"$PSScriptRoot\\winlib.ps1\"` 후 `Get-AppWindow -ProcessId <pid>` 를 쓸 것.")


async def test_no_script_keeps_a_private_toplevel_finder():
    """`TopLevel(` 이라는 이름의 사설 파인더가 남아 있으면 안 된다(복붙의 자국)."""
    offenders = [f for f in _scripts()
                 if f not in ENUM_ALLOWED and OWN_FINDER.search(_read(f))]
    assert not offenders, "사설 TopLevel 파인더가 남았다: " + ", ".join(offenders)


async def test_every_user_of_the_resolver_dot_sources_it():
    """`Get-AppWindow` 를 부르면서 winlib 를 안 읽어들이면 **실행 시점에** 깨진다 —
    그건 게이트가 아니라 사고다. 정적으로 잡는다."""
    bad = []
    for f in _scripts():
        if f == "winlib.ps1":
            continue
        src = _read(f)
        if "Get-AppWindow" in src and not DOT_SOURCE.search(src):
            bad.append(f)
    assert not bad, "winlib 를 dot-source 하지 않고 Get-AppWindow 를 부른다: " + ", ".join(bad)


async def test_powershell_scripts_keep_the_utf8_bom():
    """Windows PowerShell 5.1 은 **BOM 없는 UTF-8 을 ANSI 로 읽는다** — 한글 주석이
    깨지는 정도가 아니라 따옴표가 끊겨 **파싱 오류로 죽는다**(이 세션에서 실측).
    이 저장소의 하네스는 전부 한글 주석을 달고 있으므로 BOM 이 필수다."""
    missing = []
    for f in _scripts():
        with open(os.path.join(SCRIPTS, f), "rb") as fh:
            if fh.read(3) != b"\xef\xbb\xbf":
                missing.append(f)
    assert not missing, "UTF-8 BOM 이 없다(PowerShell 5.1 이 파싱에 실패한다): " + ", ".join(missing)
