#!/usr/bin/env python3
"""세션 뷰 마우스 **시나리오** 픽스처 — 같은 손짓에 두 클라가 서버로 같은 것을 보내는가(pytmux-569).

# 왜 필요한가

`gen_mouse_fixture.py` 는 **한 이벤트의 바이트**(인코딩)를 잰다. 그 위 — 누름·이동·뗌·휠이
**이어질 때** 정본이 무엇을 보내는지(클릭인가 끌기인가 · 앱에게 넘기나 우리가 먹나 · 어느
패널로 포커스가 가나)는 어느 자도 안 쟀고, 손으로 찾은 갈림이 여섯이었다(임계 · 가장자리
자동 스크롤 · 헤더 끌기 · any-motion · `mouse off` 의 휠 · 가운데/Ctrl 클릭).

그래서 정본 `clientwidgets.MultiplexerView` 의 `on_mouse_down/move/up`·`on_mouse_scroll_*`
에 **입력열을 그대로 먹이고**, 서버로 나간 와이어 프레임과 화면 상태를 단계마다 적는다.
`gui/src/session_view_mouse_conformance_tests.rs`(pytmux-569) 가 같은 입력열을 GUI 뷰에 넣어 대조한다.

# 어떻게 세우나 (Textual 앱 없이)

`MultiplexerView()` 는 앱 없이 생성된다. `self.app` 은 `textual._context.active_app` 를
읽으므로 거기에 **가짜 앱**(`_FakeApp`)을 세우면 핸들러를 보통 메서드로 부를 수 있다.
가짜 앱의 `send_cmd`·`send_mouse`·`send_scroll` 은 `client.py` 가 소켓에 쓰는 **그 dict** 를
그대로 적는다 — 그래서 픽스처의 `wire` 는 서버가 받는 프레임 그 자체다.

    python3 scripts/gen_mouse_scenarios.py [--pytmux ..] [--out …]
"""

import argparse
import base64
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "mouse_scenarios.json")

# 좌우 두 패널 + border-status 머리줄(y=0). 왼쪽(1)=앱(마우스 추적 가변) · 오른쪽(2)=셸.
COLS, ROWS = 41, 10


def layout(track, active=1):
    return {
        "t": "layout", "cols": COLS, "rows": ROWS, "active": active,
        "border_status": True,
        "panes": [
            {"id": 1, "x": 0, "y": 1, "w": 20, "h": 9, "title": "app",
             "active": active == 1, "mouse": track, "mouse_sgr": True},
            {"id": 2, "x": 21, "y": 1, "w": 20, "h": 9, "title": "sh",
             "active": active == 2, "mouse": 0, "mouse_sgr": False},
        ],
        "dividers": [{"x": 20, "y": 0, "w": 1, "h": 10, "split_id": 7,
                      "orient": "lr", "rect": [0, 0, COLS, ROWS], "ratio": 0.5}],
    }


# 자리(캔버스 좌표).
BODY = (5, 4)          # 왼쪽 패널 본문
BODY2 = (25, 4)        # 오른쪽 패널 본문(마우스 안 켬)
HEADER = (5, 0)        # 왼쪽 패널 머리줄
DIVIDER = (20, 4)      # 분할 경계


def _ev(kind, x, y, button=0, shift=False, ctrl=False, alt=False):
    return {"ev": kind, "x": x, "y": y, "button": button,
            "shift": shift, "ctrl": ctrl, "alt": alt}


def gestures(at, button, shift, ctrl, alt=False):
    """손짓 셋 — 클릭 · 임계 미만 끌기 · 임계(3) 이상 끌기."""
    x, y = at
    mods = dict(shift=shift, ctrl=ctrl, alt=alt)
    down = _ev("down", x, y, button, **mods)
    yield "click", [down, _ev("up", x, y, button, **mods)]
    yield "nudge", [down, _ev("move", x + 1, y, button, **mods),
                    _ev("up", x + 1, y, button, **mods)]
    yield "drag", [down, _ev("move", x + 4, y + 1, button, **mods),
                   _ev("up", x + 4, y + 1, button, **mods)]


def scenarios():
    out = []

    def add(name, steps, track=2, mouse=True, drag_copy="on", mode="normal",
            active=1):
        out.append({"name": name, "layout": layout(track, active),
                    "config": {"mouse": mouse, "drag_copy": drag_copy,
                               "threshold": 3, "mode": mode},
                    "steps": steps})

    # A 본문 손짓 — 버튼 × 수식 × 앱 추적 × drag-copy × 손짓.
    for button in (1, 2, 3):
        for mods_name, shift, ctrl in (("plain", False, False),
                                       ("shift", True, False),
                                       ("ctrl", False, True)):
            for track in (0, 1, 2, 3):
                for dc in ("on", "off", "shift"):
                    for g, steps in gestures(BODY, button, shift, ctrl):
                        # 오른쪽 버튼은 누름에서 메뉴로 끝난다 — 손짓 하나면 된다.
                        if button == 3 and g != "click":
                            continue
                        add(f"A_b{button}_{mods_name}_t{track}_dc{dc}_{g}",
                            steps, track=track, drag_copy=dc)
    # A' 비활성 패널 위(포커스 이동) · 마우스 안 켠 패널.
    for button in (1, 2):
        for g, steps in gestures(BODY2, button, False, False):
            add(f"A2_b{button}_shellpane_{g}", steps, track=2)
    # Alt 는 정본이 안 본다(meta) — 평클릭과 같아야 한다.
    for g, steps in gestures(BODY, 1, False, False, alt=True):
        add(f"A3_alt_{g}", steps, track=2)

    # B 버튼 없는 이동.
    for track in (0, 1, 2, 3):
        for mouse in (True, False):
            for mode in ("normal", "scroll"):
                add(f"B_t{track}_mouse{int(mouse)}_{mode}",
                    [_ev("move", 5, 4), _ev("move", 6, 4), _ev("move", 25, 4)],
                    track=track, mouse=mouse, mode=mode)

    # C 휠.
    for track in (0, 1, 2, 3):
        for mouse in (True, False):
            add(f"C_t{track}_mouse{int(mouse)}_up", [_ev("wheel_up", 5, 4)],
                track=track, mouse=mouse)
            add(f"C_t{track}_mouse{int(mouse)}_down", [_ev("wheel_down", 25, 4)],
                track=track, mouse=mouse)
        # 끌기 중 휠 = 선택 확장(앱에 안 넘긴다).
        add(f"C_t{track}_during_drag",
            [_ev("down", 5, 4, 1), _ev("move", 9, 5, 1), _ev("wheel_down", 9, 5),
             _ev("up", 9, 5, 1)], track=track)

    # D 자리 — 머리줄 · 경계 · 캔버스 밖.
    for button in (1, 2):
        add(f"D_b{button}_header_click", [_ev("down", *HEADER, button),
                                          _ev("up", *HEADER, button)], active=2)
        add(f"D_b{button}_header_to_other_pane",
            [_ev("down", *HEADER, button), _ev("move", 25, 4, button),
             _ev("up", 25, 4, button)])
        add(f"D_b{button}_divider_drag",
            [_ev("down", *DIVIDER, button), _ev("move", 30, 4, button),
             _ev("up", 30, 4, button)])
    # 선택을 끌고 캔버스 밖으로 — 위(y<0)는 위로 구르고, 왼쪽(x<0 · y 는 안)은 안 구른다.
    add("D_drag_out_top", [_ev("down", 5, 4, 1), _ev("move", 9, 5, 1),
                           _ev("move", 9, -1, 1), _ev("up", 9, -1, 1)], track=0)
    add("D_drag_out_left", [_ev("down", 5, 4, 1), _ev("move", 9, 5, 1),
                            _ev("move", -1, 5, 1), _ev("up", -1, 5, 1)], track=0)
    add("D_drag_to_bottom_edge", [_ev("down", 5, 4, 1), _ev("move", 9, 9, 1),
                                  _ev("up", 9, 9, 1)], track=0)

    # E 모드 — 평소가 아닐 때.
    for mode in ("esc", "prefix", "scroll"):
        add(f"E_{mode}_click", [_ev("down", 5, 4, 1), _ev("up", 5, 4, 1)],
            track=2, mode=mode)
        add(f"E_{mode}_drag", [_ev("down", 5, 4, 1), _ev("move", 9, 5, 1),
                               _ev("up", 9, 5, 1)], track=2, mode=mode)
        add(f"E_{mode}_wheel", [_ev("wheel_up", 5, 4)], track=2, mode=mode)
    return out


class _Plugins:
    @staticmethod
    def client_click(app, x, y, button):
        return False


class _TabBar:
    tabs = [{"index": 0, "active": True}]

    @staticmethod
    def _hit(x):
        return (None, None)


class _FakeApp:
    """`MultiplexerView` 가 손대는 앱 표면의 최소 모양. 와이어는 `client.py` 의 모양 그대로."""

    def __init__(self, sc):
        cfg = sc["config"]
        self.layout = sc["layout"]
        self.mode = cfg["mode"]
        self.mouse_enabled = cfg["mouse"]
        self.mouse_drag_copy = cfg["drag_copy"]
        self.mouse_drag_threshold = cfg["threshold"]
        self.pane_cwds = {}
        self.pane_top = {1: 0, 2: 0}
        self._tab_close_zone = None
        self.mouse_captured = None
        self.plugins = _Plugins()
        self.tabbar = _TabBar()
        self.wire = []
        self.ui = []

    # 와이어 — client.py send_cmd / send_mouse / send_scroll 와 같은 dict.
    def send_cmd(self, action, **kw):
        self.wire.append(dict(kw, t="cmd", action=action))

    def send_mouse(self, pane_id, data):
        if data:
            self.wire.append({"t": "input", "pane": pane_id, "mouse": True,
                              "data": base64.b64encode(data).decode("ascii")})

    def send_scroll(self, pane_id, delta=0, bottom=False, top=False):
        m = {"t": "scroll", "pane": pane_id}
        if bottom:
            m["bottom"] = True
        elif top:
            m["top"] = True
        else:
            m["delta"] = delta
        self.wire.append(m)

    # 화면 쪽 효과.
    def open_menu(self, pane_id=None):
        self.ui.append("menu")

    def copy_text(self, text):
        self.ui.append("copy")

    def copy_selection_text(self, *a, **k):
        self.ui.append("copy")

    def display_message(self, *a, **k):
        pass

    def _expect_selection(self):
        pass

    def _close_overlay(self, pid):
        return False

    def _composite(self):
        pass

    def _log_mouse(self, *a, **k):
        pass

    def capture_mouse(self, widget=None):
        self.mouse_captured = widget


def run(clientwidgets, events, active_app, sc):
    app = _FakeApp(sc)
    tok = active_app.set(app)
    try:
        view = clientwidgets.MultiplexerView()
        view._autoscroll_set = _record_autoscroll(view)
        want = []
        for i, st in enumerate(sc["steps"]):
            app.wire, app.ui = [], []
            kind, x, y = st["ev"], st["x"], st["y"]
            args = (None, x, y, 0, 0, st["button"], st["shift"], st["alt"],
                    st["ctrl"])
            kw = dict(screen_x=x, screen_y=y)
            if kind == "down":
                view.on_mouse_down(events.MouseDown(*args, **kw))
            elif kind == "move":
                view.on_mouse_move(events.MouseMove(*args, **kw))
            elif kind == "up":
                view.on_mouse_up(events.MouseUp(*args, **kw))
            elif kind == "wheel_up":
                view.on_mouse_scroll_up(events.MouseScrollUp(*args, **kw))
            elif kind == "wheel_down":
                view.on_mouse_scroll_down(events.MouseScrollDown(*args, **kw))
            else:
                raise SystemExit(f"모르는 단계: {kind}")
            want.append({"step": i, "wire": app.wire, "ui": app.ui,
                         "selecting": view._sel is not None,
                         "autoscroll": view._autoscroll_delta})
        return want
    finally:
        active_app.reset(tok)


def _record_autoscroll(view):
    """타이머는 안 건다(박자는 `edge_scroll` 자가 잰다) — 판정된 델타만 남긴다."""
    def setter(delta):
        view._autoscroll_delta = delta
    return setter


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)
    sys.path.insert(0, root)
    from textual import events
    from textual._context import active_app
    from pytmuxlib import clientwidgets

    cases = []
    for sc in scenarios():
        cases.append(dict(sc, want=run(clientwidgets, events, active_app, sc)))
    sent = sum(1 for c in cases for w in c["want"] if w["wire"])
    if not sent:
        sys.exit("어느 단계도 아무것도 안 보냈다 — 뽑는 방법이 틀렸다")
    payload = {
        "_comment": "python3 scripts/gen_mouse_scenarios.py 로 생성. 출처 = "
                    "pytmuxlib/clientwidgets.py MultiplexerView.on_mouse_* · "
                    "wire 는 서버가 받는 프레임 그대로(client.py send_*).",
        "cases": cases,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    # 사례 하나 = 한 줄(픽스처가 크다 — 들여쓰면 수백 KB 가 되고 diff 가 읽히지 않는다).
    with open(args.out, "w", encoding="utf-8") as fp:
        fp.write('{"_comment": ' + json.dumps(payload["_comment"], ensure_ascii=False)
                 + ',\n "cases": [\n')
        fp.write(",\n".join(json.dumps(c, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":"))
                            for c in cases))
        fp.write("\n]}\n")
    print(f"{args.out} — 시나리오 {len(cases)}개 · 무엇인가 보낸 단계 {sent}개")


if __name__ == "__main__":
    main()
