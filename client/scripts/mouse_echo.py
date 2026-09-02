#!/usr/bin/env python3
"""마우스를 1급으로 쓰는 **최소한의 앱**. 패스스루 확인용 장치다.

# 왜 이게 필요한가

"Shift+드래그를 패널 안 앱에게 넘긴다"는 확인하려면 **마우스를 원하는 앱**이 패널 안에서
돌고 있어야 한다. 그런 앱(vim·htop·p4v-tui)이 이 상자에 없다는 이유로 슬라이스 7이
패스스루를 미뤘는데, P6 에서 배운 것이 여기에도 그대로 적용된다 — *"헤드리스 불가"의 절반은
사람이 아니라 그 OS 의 장치가 필요한 것이고, 장치는 프로그램이 만들 수 있다.*

이 스크립트가 하는 일은 셋뿐이다:

1. 마우스 추적을 켠다(`?1002h` = 버튼 눌린 동안의 이동까지 · `?1006h` = SGR 좌표).
   서버는 이 DECSET 을 보고 **그 패널이 마우스를 원한다**고 판정한다(`layout` 의 `mouse`).
2. 들어오는 바이트를 읽어 **받은 리포트를 화면에 적는다**. 화면에 적히는 그 줄이 곧
   "넘어왔다"의 증거다.
3. `q` 를 받으면 추적을 끄고 끝낸다(끄지 않고 죽으면 그 패널의 마우스 상태가 남는다).

# Windows 주의

ConPTY 안에서도 콘솔은 기본적으로 VT 입력을 **키 이벤트로 번역**한다. 그대로 두면 우리가
보낸 리포트가 앱까지 안 온다 — `ENABLE_VIRTUAL_TERMINAL_INPUT` 을 켜야 원문이 온다.
"""

import os
import sys

WINDOWS = os.name == "nt"


def enable_mouse_input():
    """콘솔 입력을 **마우스 레코드까지** 받도록 바꾼다(Windows). 이전 모드를 돌려준다.

    두 가지를 켜고 두 가지를 끈다:

    - 켠다: `ENABLE_MOUSE_INPUT`(0x10) · `ENABLE_EXTENDED_FLAGS`(0x80). 후자를 같이 안
      켜면 아래 QuickEdit 끄기가 먹지 않는다.
    - 끈다: `ENABLE_QUICK_EDIT_MODE`(0x40) — 켜져 있으면 **콘솔이 마우스를 선택 UI 로
      먼저 먹는다**. 그리고 줄 단위 입력(0x2)·에코(0x4).

    `ENABLE_VIRTUAL_TERMINAL_INPUT` 은 **안 켠다** — 그걸 켜면 마우스가 다시 VT 바이트로
    번역되어 레코드로는 안 온다. 우리가 물으려는 것은 "리포트가 앱까지 왔나"이고, 그
    답은 레코드에 있다.
    """
    if not WINDOWS:
        return None
    import ctypes

    k32 = ctypes.windll.kernel32
    handle = k32.GetStdHandle(-10)  # STD_INPUT_HANDLE
    old = ctypes.c_uint32()
    if not k32.GetConsoleMode(handle, ctypes.byref(old)):
        return None
    mode = (old.value | 0x0010 | 0x0080) & ~0x0040 & ~0x0002 & ~0x0004
    k32.SetConsoleMode(handle, mode)
    out = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    omode = ctypes.c_uint32()
    if k32.GetConsoleMode(out, ctypes.byref(omode)):
        k32.SetConsoleMode(out, omode.value | 0x0004)  # VT 출력 처리
    return old.value


def restore_input(old):
    if old is None or not WINDOWS:
        return
    import ctypes

    k32 = ctypes.windll.kernel32
    k32.SetConsoleMode(k32.GetStdHandle(-10), old)


def read_events_windows():
    """콘솔 입력 레코드를 그대로 읽는다(Windows).

    ★ `msvcrt.getwch()` 로는 **마우스가 안 온다** — 그 함수는 키 이벤트만 꺼내고 마우스
    레코드는 버린다(실측 2026-07-28: 리포트가 서버까지 갔는데 앱이 아무것도 못 봤다).
    ConPTY 는 우리가 보낸 VT 마우스 리포트를 MOUSE_EVENT 레코드로 번역해 주므로, 그
    레코드를 직접 읽으면 "넘어왔나"에 답할 수 있다.
    """
    import ctypes
    from ctypes import wintypes

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class MOUSE_EVENT_RECORD(ctypes.Structure):
        _fields_ = [
            ("dwMousePosition", COORD),
            ("dwButtonState", wintypes.DWORD),
            ("dwControlKeyState", wintypes.DWORD),
            ("dwEventFlags", wintypes.DWORD),
        ]

    class KEY_EVENT_RECORD(ctypes.Structure):
        _fields_ = [
            ("bKeyDown", wintypes.BOOL),
            ("wRepeatCount", wintypes.WORD),
            ("wVirtualKeyCode", wintypes.WORD),
            ("wVirtualScanCode", wintypes.WORD),
            ("UnicodeChar", ctypes.c_wchar),
            ("dwControlKeyState", wintypes.DWORD),
        ]

    class EVENT_UNION(ctypes.Union):
        _fields_ = [
            ("KeyEvent", KEY_EVENT_RECORD),
            ("MouseEvent", MOUSE_EVENT_RECORD),
            ("pad", ctypes.c_byte * 16),
        ]

    class INPUT_RECORD(ctypes.Structure):
        _fields_ = [("EventType", wintypes.WORD), ("Event", EVENT_UNION)]

    k32 = ctypes.windll.kernel32
    handle = k32.GetStdHandle(-10)
    buf = (INPUT_RECORD * 8)()
    read = wintypes.DWORD()
    while True:
        if not k32.ReadConsoleInputW(handle, buf, 8, ctypes.byref(read)):
            return
        for i in range(read.value):
            rec = buf[i]
            if rec.EventType == 0x0002:  # MOUSE_EVENT
                m = rec.Event.MouseEvent
                yield ("mouse", (m.dwMousePosition.X, m.dwMousePosition.Y,
                                 m.dwButtonState, m.dwEventFlags))
            elif rec.EventType == 0x0001 and rec.Event.KeyEvent.bKeyDown:
                yield ("key", rec.Event.KeyEvent.UnicodeChar)


def main():
    # `--any-motion` 은 1002 대신 **1003**(버튼을 안 눌러도 모든 이동)을 켠다.
    # 그 레벨은 Windows 에서 기본으로 drag(2) 로 캡되어 있고(`serverio.
    # _advertised_mouse_track` · 옵션 `win-mouse-motion`), 껐던 근거가
    # 「주입된 any-motion SGR 이 ConPTY 를 못 지나 프롬프트로 새어 나온다」였다.
    # ⇒ 그 근거가 **지금도 참인지**를 재려면 1003 을 «원하는» 앱이 필요하다
    # (pytmux-423 의 관문). 1002 만 있으면 그 레벨은 영영 못 재고, 실제로 못 쟀다.
    any_motion = "--any-motion" in sys.argv[1:]
    level = "1003" if any_motion else "1002"
    old = enable_mouse_input()
    # ?1002 = 버튼을 누른 채 움직이는 동안의 이동까지 보고 · ?1003 = 버튼과 무관한
    # 모든 이동 · ?1006 = SGR 확장 좌표(255칸 넘는 화면에서도 좌표가 안 접힌다).
    sys.stdout.write(f"\x1b[?{level}h\x1b[?1006h")
    sys.stdout.write(
        f"마우스 대기 중(추적 {level}) — 움직여 보고, q 로 끝낸다.\r\n")
    sys.stdout.flush()
    try:
        for kind, data in read_events_windows():
            if kind == "key":
                if data == "q":
                    break
                continue
            x, y, buttons, flags = data
            what = "이동" if flags & 0x0001 else ("휠" if flags & 0x0004 else "버튼")
            sys.stdout.write(f"받음: ({x},{y}) 버튼={buttons} {what}\r\n")
            sys.stdout.flush()
    finally:
        sys.stdout.write(f"\x1b[?1006l\x1b[?{level}l")
        sys.stdout.write("\r\n끝.\r\n")
        sys.stdout.flush()
        restore_input(old)


if __name__ == "__main__":
    main()
