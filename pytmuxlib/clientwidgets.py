"""클라이언트 위젯 — 패널 합성 뷰(MultiplexerView)·상단 탭바(TabBar)·하단
상태표시줄(StatusBar).

client.py 의 거대 클로저(build_client_app)에서 분리(§10 LLM 친화 리팩토링).
config/sock_path 미캡처 — 데이터는 status/layout 메시지로 받고 앱 상호작용은
self.app 으로 런타임에 한다. client.py(PytmuxApp)가 이름으로 import 해 compose 에
쓴다."""
from __future__ import annotations

import os
import socket
from datetime import datetime

from textual.widget import Widget
from textual import events
from textual.strip import Strip
from rich.segment import Segment
from rich.style import Style

from .clientutil import _DATE_STRFTIME, _TIME_STRFTIME, _char_cells, _fmt_tokens, theme_color


class MultiplexerView(Widget):
    can_focus = True

    def __init__(self):
        super().__init__(id="view")
        self._cells: list[list] = []
        self._dragging = None  # (split_id, orient, rect)
        self._hover_divider = None  # 마우스가 올라간 경계선 rect (x,y,w,h)
        self._sel = None       # 선택 영역 (x0,y0,x1,y1) 전역 좌표
        self._sel_start = None
        self._mouse_fwd = None     # 패스스루 중인 패널 id(버튼 다운~업)
        self._mouse_fwd_btn = 0    # 그 시퀀스의 버튼(드래그/릴리스 인코딩용)
        self._pane_swap = None     # Shift+드래그 swap 중인 소스 패널 id
        self._pane_swap_over = None  # 드래그 중 가리키는 swap 대상 패널 id

    def _extract_selection(self):
        if not self._sel or not self._cells:
            return ""
        x0, y0, x1, y1 = self._sel
        if (y0, x0) > (y1, x1):
            x0, y0, x1, y1 = x1, y1, x0, y0
        out = []
        for y in range(y0, y1 + 1):
            if not (0 <= y < len(self._cells)):
                continue
            row = self._cells[y]
            sx = x0 if y == y0 else 0
            ex = x1 if y == y1 else len(row) - 1
            text = "".join(row[x][0] for x in range(max(0, sx),
                                                     min(len(row), ex + 1)))
            out.append(text.rstrip())
        return "\n".join(out)

    def set_frame(self, cells):
        self._cells = cells
        self.refresh()

    def render_line(self, y: int) -> Strip:
        if y >= len(self._cells):
            return Strip.blank(self.size.width)
        row = self._cells[y]
        segs = []
        run = []
        run_st = None
        for ch, st in row:
            if ch == "":
                continue  # 와이드 문자의 연속 셀 → 앞 문자가 2칸을 차지함
            if st is run_st:
                run.append(ch)
            else:
                if run:
                    segs.append(Segment("".join(run), run_st))
                run = [ch]
                run_st = st
        if run:
            segs.append(Segment("".join(run), run_st))
        return Strip(segs)

    # --- 마우스 ---
    def _pane_at(self, x, y):
        for p in self.app.layout.get("panes", []):
            bx, by, bw, bh = p.get("box") or (p["x"], p["y"], p["w"], p["h"])
            if bx <= x < bx + bw and by <= y < by + bh:
                return p
        return None

    def _divider_at(self, x, y):
        for d in self.app.layout.get("dividers", []):
            if d["x"] <= x < d["x"] + d["w"] and d["y"] <= y < d["y"] + d["h"]:
                return d
        return None

    def _pane_by_id(self, pid):
        for p in self.app.layout.get("panes", []):
            if p["id"] == pid:
                return p
        return None

    # --- 내부 앱 마우스 패스스루(p4v-tui 등 마우스 1급 TUI) ---
    def _mouse_target(self, x, y):
        """패스스루 대상 패널을 반환. 내부 앱이 마우스 모드를 켰고, 좌표가 그
        패널의 **content 영역**(테두리 제외) 안이며, pytmux 가 normal 모드일
        때만. prefix/copy-mode/팝업이면 None → pytmux 가 가로챈다(tmux 와 동일)."""
        if self.app.mode != "normal":
            return None
        p = self._pane_at(x, y)
        if not p or not p.get("mouse"):
            return None
        if not (p["x"] <= x < p["x"] + p["w"]
                and p["y"] <= y < p["y"] + p["h"]):
            return None   # 테두리/타이틀바 위 → pytmux
        return p

    def _encode_mouse(self, p, x, y, kind, button):
        """마우스 이벤트를 내부 앱이 이해하는 바이트로 인코딩한다.
        kind: press/release/drag/move/wheelup/wheeldown. 좌표는 패널 content
        기준 1-based. 패널이 1006 을 켰으면 SGR, 아니면 레거시 X10 인코딩."""
        col = x - p["x"] + 1
        row = y - p["y"] + 1
        if col < 1 or row < 1 or col > p["w"] or row > p["h"]:
            return b""
        if kind == "wheelup":
            cb = 64
        elif kind == "wheeldown":
            cb = 65
        else:
            base = {1: 0, 2: 1, 3: 2}.get(button, 0)
            cb = (base + 32 if kind == "drag"
                  else 35 if kind == "move" else base)
        if p.get("mouse_sgr"):
            final = "m" if kind == "release" else "M"
            return f"\x1b[<{cb};{col};{row}{final}".encode()
        # 레거시 X10: 릴리스는 버튼 3, 좌표/버튼은 32 오프셋(223 캡).
        if kind == "release":
            cb = 3
        return b"\x1b[M" + bytes([32 + min(cb, 223), 32 + min(col, 223),
                                  32 + min(row, 223)])

    def on_mouse_down(self, event: events.MouseDown):
        self.app._log_mouse("down", event.x, event.y, event.button)
        if not self.app.mouse_enabled:
            return
        if self.app.mode == "scroll":  # copy-mode: 드래그로 선택
            self._sel_start = (event.x, event.y)
            self._sel = (event.x, event.y, event.x, event.y)
            self.capture_mouse()
            self.app._composite()
            event.stop()
            return
        # Shift+드래그 = 패널 swap. 좌버튼+Shift 로 패널을 잡아 다른 패널에 놓으면
        # 두 패널 위치를 맞바꾼다(내용 앱은 그대로). passthrough/divider 보다 먼저
        # 가로채 마우스 모드 앱 위에서도 동작한다. 패널이 둘 이상일 때만.
        if (getattr(event, "shift", False) and event.button == 1
                and self.app.mode == "normal"
                and len(self.app.layout.get("panes", [])) >= 2):
            p = self._pane_at(event.x, event.y)
            if p:
                self._pane_swap = p["id"]
                self._pane_swap_over = None
                self.capture_mouse()
                self.app._composite()
                event.stop()
                return
        # Ctrl+Click 은 무동작 — 컨텍스트 메뉴는 순수 우클릭(button 3)으로만 연다.
        # (단, 터미널이 Ctrl+Click 을 그냥 button 3 으로 합쳐 보내면 ctrl 플래그가
        #  안 와 구분 불가 — 그 경우 우클릭으로 취급됨. 터미널 의존 한계.)
        if event.ctrl and self.app.mode == "normal":
            event.stop()
            return
        # 우클릭: 마우스 모드(패스스루) 앱 위여도 pytmux 컨텍스트 메뉴를 우선한다.
        # 커서 아래 패널을 먼저 활성화한 뒤 그 패널을 대상으로 메뉴를 연다.
        if event.button == 3 and self.app.mode == "normal":
            p = self._pane_at(event.x, event.y)
            if p and p["id"] != self.app.layout.get("active"):
                self.app.send_cmd("select_pane_id", id=p["id"])
            self.app.open_menu(p["id"] if p else None)
            event.stop()
            return
        # 시계/달력 오버레이가 켜진 패널을 클릭하면 닫는다([x] 버튼 폐지).
        op = self._pane_at(event.x, event.y)
        if op and self.app._close_overlay(op["id"]):
            event.stop()
            return
        # Claude 프롬프트 헤더 클릭 → 프롬프트 히스토리 팝업(#7)
        for pid, (zx0, zx1, zy) in self.app._claude_header_zones.items():
            if zy == event.y and zx0 <= event.x < zx1:
                self.app.open_prompt_history(pid)
                event.stop()
                return
        # Claude 권한모드 footer 클릭 → 권한모드 선택 팝업(§10 item 2). 패스스루
        # 보다 먼저 가로채 마우스 모드 앱 위에서도 동작한다.
        for pid, (zx0, zx1, zy) in self.app._perm_zone.items():
            if zy == event.y and zx0 <= event.x < zx1:
                self.app.open_perm_mode(pid)
                event.stop()
                return
        # Claude 'Remote Control active' 클릭 → 원격제어 정보 팝업(§10 item 3)
        for pid, (zx0, zx1, zy) in self.app._remote_zone.items():
            if zy == event.y and zx0 <= event.x < zx1:
                self.app.open_remote_control(pid)
                event.stop()
                return
        # 현재 탭 닫기 버튼([x]) 클릭(콘텐츠 오른쪽 위)
        z = self.app._tab_close_zone
        if z and z[2] == event.y and z[0] <= event.x < z[1]:
            self.app.confirm_kill_tab()
            event.stop()
            return
        d = self._divider_at(event.x, event.y)
        if d:
            self._dragging = d
            self._hover_divider = None   # 드래그 시작 → 호버 강조는 해제
            self.capture_mouse()
            event.stop()
            return
        # 내부 앱 마우스 패스스루(content 영역, 마우스 모드 on). 포커스도 옮긴다.
        tp = self._mouse_target(event.x, event.y)
        if tp is not None:
            if not tp.get("active"):     # 비활성 패널 클릭 시에만 포커스 이동
                self.app.send_cmd("select_pane_id", id=tp["id"])
            data = self._encode_mouse(tp, event.x, event.y, "press",
                                      event.button)
            if data:
                self.app.send_mouse(tp["id"], data)
                self._mouse_fwd = tp["id"]
                self._mouse_fwd_btn = event.button
                self.capture_mouse()
            event.stop()
            return
        p = self._pane_at(event.x, event.y)
        if p:
            self.app.send_cmd("select_pane_id", id=p["id"])
        event.stop()

    def on_mouse_move(self, event: events.MouseMove):
        # Shift+드래그 패널 swap 중 — 대상 패널 추적(시각 강조 갱신)
        if self._pane_swap is not None:
            p = self._pane_at(event.x, event.y)
            over = p["id"] if (p and p["id"] != self._pane_swap) else None
            if over != self._pane_swap_over:
                self._pane_swap_over = over
                self.app._composite()
            event.stop()
            return
        if self._sel_start is not None:
            self._sel = (self._sel_start[0], self._sel_start[1],
                         event.x, event.y)
            self.app._composite()
            event.stop()
            return
        # 패스스루 드래그(버튼 다운 후 이동) — 1002+(드래그 추적) 앱에만 전달
        if self._mouse_fwd is not None:
            pd = self._pane_by_id(self._mouse_fwd)
            if pd and pd.get("mouse", 0) >= 2:
                data = self._encode_mouse(pd, event.x, event.y, "drag",
                                          self._mouse_fwd_btn)
                if data:
                    self.app.send_mouse(pd["id"], data)
            event.stop()
            return
        if not self._dragging:
            # 경계선(divider) 위 호버 → 배경 강조(리사이즈 가능 암시)(#27).
            # divider 는 테두리라 패스스루 content 영역과 분리됨 → 호버 우선.
            if self.app.mouse_enabled:
                dv = self._divider_at(event.x, event.y)
                new_hov = (dv["x"], dv["y"], dv["w"], dv["h"]) if dv else None
                if new_hov != self._hover_divider:
                    self._hover_divider = new_hov
                    self.app._composite()   # 변경 시에만 재합성(떨림 방지)
                if dv:
                    event.stop()
                    return
            # 버튼 없는 모션 — any-motion(1003) 앱에만 전달
            pd = self._mouse_target(event.x, event.y)
            if pd is not None and pd.get("mouse", 0) >= 3:
                data = self._encode_mouse(pd, event.x, event.y, "move", 0)
                if data:
                    self.app.send_mouse(pd["id"], data)
                    event.stop()
            return
        d = self._dragging
        sx, sy, sw, sh = d["rect"]
        if d["orient"] == "lr":
            avail = sw - 1
            ratio = (event.x - sx) / avail if avail > 0 else 0.5
        else:
            avail = sh - 1
            ratio = (event.y - sy) / avail if avail > 0 else 0.5
        self.app.send_cmd("resize", split_id=d["split_id"],
                          ratio=max(0.05, min(0.95, ratio)))
        event.stop()

    def on_leave(self, event=None):
        # 위젯 밖으로 나가면 경계선 호버 강조 해제(#27).
        if self._hover_divider is not None:
            self._hover_divider = None
            self.app._composite()

    def on_mouse_up(self, event: events.MouseUp):
        # Shift+드래그 패널 swap 완료 — 대상이 있으면 서버에 swap 요청
        if self._pane_swap is not None:
            src = self._pane_swap
            self._pane_swap = None
            self._pane_swap_over = None
            try:
                self.release_mouse()
            except Exception:
                pass
            p = self._pane_at(event.x, event.y)
            if p and p["id"] != src:
                self.app.send_cmd("swap_pane_to", id=src, to_id=p["id"])
            else:
                self.app._composite()   # 강조 해제만(제자리 놓음)
            event.stop()
            return
        if self._sel_start is not None:
            text = self._extract_selection()
            self._sel_start = None
            self._sel = None
            self.release_mouse()
            if text:
                self.app.copy_text(text)
            self.app._composite()
            event.stop()
            return
        # 패스스루 버튼 릴리스
        if self._mouse_fwd is not None:
            pd = self._pane_by_id(self._mouse_fwd)
            if pd is not None:
                data = self._encode_mouse(pd, event.x, event.y, "release",
                                          self._mouse_fwd_btn)
                if data:
                    self.app.send_mouse(pd["id"], data)
            self._mouse_fwd = None
            self.release_mouse()
            event.stop()
            return
        if self._dragging:
            self._dragging = None
            self.release_mouse()
            event.stop()

    def on_mouse_scroll_up(self, event):
        # 진단 로그는 어떤 가드보다 먼저 — "이벤트가 도달했는가"를 본다.
        self.app._log_mouse("scroll_up", event.x, event.y)
        if not self.app.mouse_enabled:
            return
        # 마우스 모드 앱(less/htop/Claude 등)은 휠을 직접 처리하도록 전달.
        tp = self._mouse_target(event.x, event.y)
        if tp is not None:
            data = self._encode_mouse(tp, event.x, event.y, "wheelup", 0)
            if data:
                self.app.send_mouse(tp["id"], data)
            event.stop()
            return
        p = self._pane_at(event.x, event.y) or self._active_pane()
        if p:
            self.app.send_scroll(p["id"], delta=3)
        event.stop()

    def on_mouse_scroll_down(self, event):
        self.app._log_mouse("scroll_down", event.x, event.y)
        if not self.app.mouse_enabled:
            return
        tp = self._mouse_target(event.x, event.y)
        if tp is not None:
            data = self._encode_mouse(tp, event.x, event.y, "wheeldown", 0)
            if data:
                self.app.send_mouse(tp["id"], data)
            event.stop()
            return
        p = self._pane_at(event.x, event.y) or self._active_pane()
        if p:
            self.app.send_scroll(p["id"], delta=-3)
        event.stop()

    def _active_pane(self):
        aid = self.app.layout.get("active")
        for p in self.app.layout.get("panes", []):
            if p["id"] == aid:
                return p
        return None

class TabBar(Widget):
    """상단 탭 인터페이스. 각 탭과, 마지막 탭 바로 오른쪽의 [+] 새 탭 버튼을
    표시한다. (탭 닫기 [x] 는 콘텐츠 영역 오른쪽 위 모서리로 이동했다.)

    마우스 클릭과 ESC 모드 방향키(←→ 선택, Enter 전환)로 조작. 탭이 하나뿐이면
    기본 숨김이나, 설정 tab-bar always 면 항상 표시한다."""

    def __init__(self):
        super().__init__(id="tabbar")
        self.tabs = []          # [{index,name,active,bell,activity}]
        self.sel = 0            # ESC 모드 선택 인덱스(= tab.index)
        self.bar_focus = False  # ESC 모드 포커스가 탭바에 있는지
        self._scroll = 0        # 가로 스크롤(첫 표시 탭의 리스트 위치)
        self._zones = []        # [(x0, x1, kind, payload)] 클릭 히트테스트
        self._drag = None       # 드래그 중인 탭 index(재정렬)
        self._drag_over = None  # 드래그 중 현재 가리키는 드롭 대상 탭 index
        self._blink_idx = None  # 깜빡일 탭 index(ESC+없는 숫자 안내)
        self._blink_on = False  # 깜빡임 현재 위상(True=경고색 표시)
        self._blink_left = 0    # 남은 on/off 토글 횟수
        self._blink_timer = None

    def set_tabs(self, tabs, active_idx):
        self.tabs = tabs
        if not self.bar_focus:
            self.sel = active_idx
        self.refresh()

    def scroll_by(self, delta):
        self._scroll = max(0, min(self._scroll + delta,
                                  max(0, len(self.tabs) - 1)))
        self.refresh()

    def blink_active(self, times: int = 3, period: float = 0.12):
        """현재 활성(하이라이트) 탭을 times 번 깜빡여 '여기서 더 이동 불가'를 시각적
        으로 알린다(ESC+없는 숫자). render_line 이 _blink_on 위상일 때 그 탭을 경고색
        으로 그린다. 활성 탭이 없으면 무시."""
        aidx = next((t["index"] for t in self.tabs if t.get("active")), None)
        if aidx is None:
            return
        self._blink_idx = aidx
        self._blink_on = True
        self._blink_left = max(1, times) * 2     # on/off 토글 횟수
        if self._blink_timer is not None:
            self._blink_timer.stop()
        self._blink_timer = self.set_interval(period, self._blink_step)
        self.refresh()

    def _blink_step(self):
        self._blink_left -= 1
        if self._blink_left <= 0:
            self._blink_on = False
            self._blink_idx = None
            if self._blink_timer is not None:
                self._blink_timer.stop()
                self._blink_timer = None
        else:
            self._blink_on = not self._blink_on
        self.refresh()

    # Claude Code 상태 아이콘(탭): 대기 ○ / 처리중 ◐ / 리밋 멈춤 ⊘
    CLAUDE_ICON = {"idle": "○", "busy": "◐", "limit": "⊘"}

    # 탭바 왼쪽 여백 — 첫 탭을 한 칸 오른쪽에서 시작(사용자 요청). lead 엔트리로
    # 넣어 render_line/active_tab_xrange 가 같은 오프셋을 공유한다.
    LEAD = 1

    def _labels(self):
        out = []
        for t in self.tabs:
            flag = "!" if t.get("bell") else ("#" if t.get("activity") else "")
            ic = self.CLAUDE_ICON.get(t.get("claude"))
            ic = (ic + " ") if ic else ""
            # 표시는 1부터(사용자 요청 #21). 내부 index 는 0-based 리스트 위치 그대로
            # 두고(select_window 등 좌표 계산 호환), **보여줄 때만** +1 한다.
            out.append(f" {ic}{t['index'] + 1}:{t['name']}{flag} ")
        return out

    def _entries(self):
        """현재 상태(탭·스크롤·폭)에서 탭바에 그릴 항목을 (kind, payload, text)
        순서 리스트로 만든다(스타일 무관, 기하만). render_line(세그먼트·스타일)과
        active_tab_xrange(연결부 x 좌표)가 같은 기하를 공유해, 합성 시점이나
        직전 렌더 상태와 무관하게 일치한다(#23 — 예전엔 후자가 render_line 부산물인
        _zones 를 읽어 탭 전환 직후 stale 값으로 연결부가 어긋났다). 스크롤 보정은
        render_line 과 동일하게 여기서 수행(부수효과로 self._scroll 갱신)."""
        w = self.size.width
        labels = self._labels()
        widths = [sum(_char_cells(c) for c in s) for s in labels]
        n = len(self.tabs)
        idxs = [t["index"] for t in self.tabs]
        selpos = idxs.index(self.sel) if self.sel in idxs else 0
        # [+] 새 탭 버튼: 왼쪽 탭과 한 칸 더 띄운다(사용자 요청 — 앞 공백 2칸).
        # 왼쪽 여백(LEAD)도 폭 예산에서 뺀다.
        addtxt = "  [+]"
        mid_w = max(1, w - len(addtxt) - self.LEAD)
        # 선택 탭이 보이도록 스크롤 보정
        self._scroll = max(0, min(self._scroll, max(0, n - 1)))
        if selpos < self._scroll:
            self._scroll = selpos
        while (self._scroll < selpos and
               sum(widths[self._scroll:selpos + 1]) > mid_w - 2):
            self._scroll += 1
        entries, mid_used = [], 0
        if self.LEAD:                              # 왼쪽 여백(첫 탭 한 칸 오른쪽)
            entries.append(("lead", None, " " * self.LEAD))
        if self._scroll > 0:                       # 왼쪽에 더 있음
            entries.append(("scroll_left", None, "◀"))
            mid_used += 1
        i = self._scroll
        while i < n:
            tw = widths[i]
            reserve = 1 if i < n - 1 else 0        # 오른쪽 화살표 자리
            if mid_used + tw > mid_w - reserve and i > self._scroll:
                break
            entries.append(("tab", self.tabs[i]["index"], labels[i]))
            mid_used += tw
            i += 1
        if i < n:                                  # 오른쪽에 더 있음
            entries.append(("scroll_right", None, "▶"))
        # [+] 새 탭 버튼(§10 #16): 앞 간격칸은 터미널 배경(녹색 아님)으로 분리해
        # 그려, 간격까지 녹색으로 칠해지지 않게 한다. 간격칸은 클릭 무시(lead 처럼).
        entries.append(("addgap", None, addtxt[:2]))   # 간격(터미널 배경)
        entries.append(("add", None, addtxt[2:]))      # "[+] "(녹색 버튼)
        return entries

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        fg = theme_color(self, "foreground")
        # 비활성 탭·여백 배경은 터미널 기본 배경(bgcolor=None)을 따른다 — 패널
        # 내용 셀이 터미널 색을 보이는 것과 같은 메커니즘. 활성/선택/[+]/화살표
        # 배지는 자체 bgcolor 유지(의도된 강조).
        base = Style(color=fg, bgcolor=None)
        add_st = Style(color="black", bgcolor=theme_color(self, "success"),
                       bold=True)
        active_st = Style(color="white", bgcolor=theme_color(self, "primary"),
                          bold=True)
        sel_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                       bold=True)
        arrow_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                         bold=True)
        # 비활성 탭의 Claude 작업 완료 알림(보면 해제). 배경을 바꾸면 너무 튄다는
        # 피드백(#31) → **배경은 그대로 두고 탭 이름 글자색만** 호박색(warning)+굵게로
        # 바꿔 알린다. 활성(primary 배경)·선택(accent 배경)과 자연히 구분된다.
        done_st = Style(color=theme_color(self, "warning"), bold=True)
        # 드래그 재정렬 시각 피드백: 들고 있는 탭(소스)은 흐리게, 놓을 위치
        # (드롭 대상)은 밑줄+강조색으로 표시(놓으면 그 자리로 이동).
        dragging = self._drag is not None
        drop_st = Style(color="black", bgcolor=theme_color(self, "warning"),
                        bold=True, underline=True)
        # ESC+없는 숫자 안내용 깜빡임(현재 활성 탭을 경고색으로 번쩍).
        blink_st = Style(color="black", bgcolor=theme_color(self, "warning"),
                         bold=True)
        by_idx = {t["index"]: t for t in self.tabs}
        segs, zones = [], []
        x = 0
        for kind, payload, text in self._entries():
            if kind in ("lead", "addgap"):         # 여백/[+] 간격칸(터미널 배경, 클릭 무시)
                st = base
            elif kind in ("scroll_left", "scroll_right"):
                st = arrow_st
            elif kind == "add":
                # ESC 모드에서 [+] 가 커서 대상으로 선택되면 강조(#26)
                st = sel_st if (self.bar_focus and self.sel == "+") else add_st
            else:                                  # tab
                t = by_idx.get(payload, {})
                if self._blink_on and payload == self._blink_idx:
                    st = blink_st  # ESC+없는 숫자 → 활성 탭 깜빡임(이동 불가 안내)
                elif dragging and payload == self._drag_over and payload != self._drag:
                    st = drop_st   # 드롭 대상(놓으면 여기로 이동)
                elif dragging and payload == self._drag:
                    st = base + Style(dim=True)  # 들고 있는 탭(소스) 흐리게
                elif self.bar_focus and payload == self.sel:
                    st = sel_st
                elif t.get("active"):
                    st = active_st
                elif t.get("claude_done"):
                    st = done_st   # 비활성 탭 Claude 완료 알림(#22)
                else:
                    st = base
            wdt = sum(_char_cells(c) for c in text)
            zones.append((x, x + wdt, kind, payload))
            segs.append(Segment(text, st))
            x += wdt
        pad = w - x
        if pad > 0:
            segs.append(Segment(" " * pad, base))
            x += pad
        self._zones = zones
        return Strip(segs).adjust_cell_length(w, base)

    def _hit(self, x):
        for x0, x1, kind, payload in self._zones:
            if x0 <= x < x1:
                return kind, payload
        return None, None

    def active_tab_xrange(self):
        """현재 활성 탭의 화면 x 범위 (x0, x1). 콘텐츠 상단 테두리를 활성 탭과
        연결(노트북 탭 모양)하는 데 쓴다(#23). _zones(직전 렌더 부산물) 대신
        _entries() 로 현재 self.tabs+스크롤에서 직접 계산해, 탭 전환 직후
        render_line 재실행 전에 합성돼도 새 활성 탭을 정확히 가리킨다."""
        aidx = next((t["index"] for t in self.tabs if t.get("active")), None)
        if aidx is None:
            return None
        x = 0
        for kind, payload, text in self._entries():
            wdt = sum(_char_cells(c) for c in text)
            if kind == "tab" and payload == aidx:
                return (x, x + wdt)
            x += wdt
        return None

    def on_mouse_down(self, event):
        if not self.app.mouse_enabled:
            return
        kind, payload = self._hit(event.x)
        if kind == "add":
            self.app.send_cmd("new_window")
        elif kind == "scroll_left":
            self.scroll_by(-1)
        elif kind == "scroll_right":
            self.scroll_by(1)
        elif kind == "tab":
            # 탭 클릭=드래그 시작(놓을 때 같은 탭이면 선택, 다른 탭이면 재정렬)
            self._drag = payload
            self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event):
        # 드래그 중에만(capture_mouse 로 이동 이벤트가 여기로 옴) 시각 피드백 갱신.
        if self._drag is None:
            return
        # 탭바 아래(콘텐츠 영역)로 끌어내리면 패널 분할 드롭 모드(#19): 커서 아래 패널과
        # 분할 방향을 미리보기로 표시한다. 탭바는 1행이라 event.y>=1 이 콘텐츠 행이다.
        if event.y >= 1:
            drop = self.app._tabdrop_at(event.x, event.y - 1)
            if drop != self.app._drag_split:
                self.app._drag_split = drop
                self._drag_over = None
                self.app._composite()           # 분할 미리보기 갱신
            event.stop()
            return
        if self.app._drag_split is not None:    # 탭바로 되올라옴 → 미리보기 해제
            self.app._drag_split = None
            self.app._composite()
        kind, payload = self._hit(event.x)
        over = payload if (kind == "tab" and payload != self._drag) else None
        if over != self._drag_over:
            self._drag_over = over
            self.refresh()
        event.stop()

    def on_mouse_up(self, event):
        if self._drag is None:
            return
        src = self._drag
        drop = self.app._drag_split
        self._drag = None
        self._drag_over = None
        self.app._drag_split = None
        self.refresh()
        try:
            self.release_mouse()
        except Exception:
            pass
        # 콘텐츠 위에 놓았으면(드롭 대상 패널 있음) 그 패널을 활성화하고, 끌어온 탭의
        # 패널을 그 패널에 분할로 합친다(#19 탭→패널). 아니면 기존 재정렬/전환.
        if event.y >= 1 and drop is not None:
            pane_id, orient = drop
            self.app.send_cmd("select_pane_id", id=pane_id)
            self.app.send_cmd("join_pane", src=src, orient=orient)
            self.app._composite()
            event.stop()
            return
        kind, payload = self._hit(event.x)
        if kind == "tab" and payload != src:
            # index==위치(연속) 이므로 그대로 사용
            self.app.send_cmd("move_tab", index=src, to=payload)
        else:
            self.app.send_cmd("select_window", index=src)
        event.stop()

class StatusBar(Widget):
    def __init__(self, bg=None, fg=None,
                 left=" ", right=" #{pane_title}#h %H:%M %Y-%m-%d "):
        super().__init__(id="status")
        self.session = ""
        self.windows = []
        self.zoomed = False
        self.sync = False
        self.pane_title = ""
        self.autoresume = False
        self.prompt_clear = False  # 프롬프트 단위 클리어 모드(활성 패널, #9)
        self.prompt_clear_queue = []  # 프롬프트 단위 클리어 큐(활성 패널, #4)
        self.capture = False     # 패널 출력 캡처 중(서버 옵션, 기본 OFF)
        self.prefix_off = False  # 중첩: outer prefix 해제 표시
        self.cmd_mode = False  # ESC 명령 모드 표시
        self.message = None    # display-message 임시 메시지
        self.hide_tabs = False  # 상단 탭바가 보이면 하단 탭 목록 생략
        self.claude_usage = None  # 활성 Claude 패널의 토큰/컨텍스트(best-effort)
        self.claude_tokens = 0    # 활성 계정 누적 토큰(§10 계정별 합계, 지속표시)
        self.claude_account = None  # 누적 토큰의 귀속 계정(표시에 곁들임)
        self.bg = bg
        self.fg = fg
        self.left_fmt = left
        self.right_fmt = right
        # 다중 줄 상태표시줄: lines = 상태줄 줄 수(0~5, 기본 1). 맨 아래 줄(bottom)이
        # 기존의 풍부한 상태(REC/사용량/시계 등), 그 위 줄들은 extra[i] 의 포맷
        # 문자열을 _expand 로 펼쳐 표시(tmux status-format[i] 와 동일하게 index 1
        # 이 바닥 바로 위). 0 이면 상태줄 숨김.
        self.lines = 1
        self.extra = {}          # {line_index(>=1): fmt 문자열}
        self._clock_zone = None  # (x0, x1) 시각(시계) 클릭 영역
        self._date_zone = None   # (x0, x1) 날짜(달력) 클릭 영역
        self._usage_zone = None  # (x0, x1) 토큰 사용량 클릭 영역(Claude 트리)
        self._rec_zone = None    # (x0, x1) REC 클릭 영역(캡처 정보 팝업)
        self.capture_path = None  # 활성 패널 캡처 파일 경로
        self.capture_size = 0     # 그 파일 크기(bytes)
        # 클라이언트가 SSH 원격 세션에서 도는지(attach 한 머신 기준, 시작 시 1회).
        self._is_remote = bool(os.environ.get("SSH_CONNECTION")
                               or os.environ.get("SSH_TTY"))

    def _expand(self, fmt):
        """#S/#h/#H/#{pane_title} 토큰과 strftime(%) 코드를 치환."""
        try:
            s = datetime.now().strftime(fmt)
        except ValueError:
            s = fmt
        host = socket.gethostname()
        tpane = (self.pane_title + " · ") if (self.pane_title
                 and self.pane_title != "shell") else ""
        aw = next((w for w in self.windows if w.get("active")), None)
        return (s.replace("#S", self.session)
                 .replace("#h", host.split(".")[0])
                 .replace("#H", host)
                 .replace("#I", str(aw["index"] + 1) if aw else "")
                 .replace("#W", aw["name"] if aw else "")
                 .replace("#{pane_title}", tpane))

    def _expand_parts(self, fmt):
        """오른쪽 포맷을 (kind, text) 런 목록으로 펼친다.
        kind ∈ {'host','time','date','plain'}. 호스트(원격 강조)·시각(시계
        클릭)·날짜(달력 클릭) 구간을 분리하기 위해 토큰/‌strftime 코드 단위로
        쪼갠 뒤 인접 동종을 병합한다. right_fmt 가 커스텀돼도 동작한다."""
        host = socket.gethostname()
        aw = next((w for w in self.windows if w.get("active")), None)
        tpane = (self.pane_title + " · ") if (self.pane_title
                 and self.pane_title != "shell") else ""
        runs = []
        i, n = 0, len(fmt)
        while i < n:
            c = fmt[i]
            if c == "#":
                if fmt.startswith("#{pane_title}", i):
                    runs.append(("plain", tpane)); i += len("#{pane_title}"); continue
                two = fmt[i:i + 2]
                if two == "#h":
                    runs.append(("host", host.split(".")[0])); i += 2; continue
                if two == "#H":
                    runs.append(("host", host)); i += 2; continue
                if two == "#S":
                    runs.append(("plain", self.session)); i += 2; continue
                if two == "#I":
                    runs.append(("plain", str(aw["index"] + 1) if aw else "")); i += 2; continue
                if two == "#W":
                    runs.append(("plain", aw["name"] if aw else "")); i += 2; continue
                runs.append(("plain", c)); i += 1; continue
            if c == "%" and i + 1 < n:
                code = fmt[i + 1]
                if code == "%":
                    runs.append(("plain", "%")); i += 2; continue
                try:
                    val = datetime.now().strftime("%" + code)
                except ValueError:
                    val = "%" + code
                kind = ("time" if code in _TIME_STRFTIME
                        else "date" if code in _DATE_STRFTIME else "plain")
                runs.append((kind, val)); i += 2; continue
            runs.append(("plain", c)); i += 1
        return self._merge_runs(runs)

    @staticmethod
    def _merge_runs(runs):
        # ① 같은 종류 strftime 코드 사이의 구분자(:,-,/,. )만 있는 plain 런을
        #    양옆과 같은 kind 로 흡수(%H:%M·%Y-%m-%d 를 한 구간으로 묶음).
        absorbed = []
        for idx, (kind, text) in enumerate(runs):
            if (kind == "plain" and text and all(ch in ":-/. " for ch in text)
                    and absorbed and absorbed[-1][0] in ("time", "date")
                    and idx + 1 < len(runs)
                    and runs[idx + 1][0] == absorbed[-1][0]):
                kind = absorbed[-1][0]
            absorbed.append((kind, text))
        # ② 인접 동일 kind 병합.
        merged = []
        for kind, text in absorbed:
            if merged and merged[-1][0] == kind:
                merged[-1] = (kind, merged[-1][1] + text)
            else:
                merged.append([kind, text])
        return [(k, t) for k, t in merged if t]

    def update_status(self, msg):
        self.session = msg.get("session", "")
        self.windows = msg.get("windows", [])
        self.zoomed = msg.get("zoomed", False)
        self.sync = msg.get("sync", False)
        self.pane_title = msg.get("pane_title", "")
        self.autoresume = msg.get("autoresume", False)
        self.prompt_clear = msg.get("prompt_clear", False)
        self.prompt_clear_queue = msg.get("prompt_clear_queue", [])
        self.capture = msg.get("capture", False)
        # §10 지속표시: usage/tokens/account 가 비어 와도(활성 패널이 Claude 가
        # 아니거나 한 프레임 파싱 실패) 마지막 비어있지 않은 값을 유지한다.
        # 계정이 바뀌면 서버가 새 비-0 값을 보내므로 자연히 갱신된다.
        cu = msg.get("claude_usage")
        if cu:
            self.claude_usage = cu
        ct = msg.get("claude_tokens", 0)
        if ct:
            self.claude_tokens = ct
        ca = msg.get("claude_account")
        if ca:
            self.claude_account = ca
        self.capture_path = msg.get("capture_path")
        self.capture_size = msg.get("capture_size", 0)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        # 다중 줄: 맨 아래 줄이 주 상태(아래 _render_main), 그 위는 extra 포맷.
        h = max(1, self.lines)
        base = Style(color=self.fg or theme_color(self, "foreground"),
                     bgcolor=self.bg)
        if y != h - 1:
            # bottom 위의 보조 줄. tmux 처럼 index 1 = 바닥 바로 위.
            idx = (h - 1) - y
            fmt = self.extra.get(idx, "")
            txt = self._expand(fmt) if fmt else ""
            return Strip([Segment(txt, base)]).adjust_cell_length(
                self.size.width, base)
        return self._render_main(base)

    def _render_main(self, base) -> Strip:
        w = self.size.width
        # 색상은 p4v-tui 와 동일한 textual-dark 테마를 따른다(설정으로 덮어쓰기 가능).
        tc = lambda n: theme_color(self, n)  # noqa: E731
        # 배경은 명시 설정(self.bg)이 없으면 터미널 기본(None)을 따른다 —
        # REC/SYNC/AR 등 개별 배지는 자체 bgcolor 유지(의도된 강조).
        if self.message is not None:
            ms = Style(color="black", bgcolor=tc("warning"), bold=True)
            return Strip([Segment(f" {self.message} ", ms)]).adjust_cell_length(
                w, ms)
        active = Style(color="white", bgcolor=tc("primary"), bold=True)
        segs = [Segment(self._expand(self.left_fmt), base)]
        if self.cmd_mode:
            segs.append(Segment("CMD(←↑↓→ 이동, : 명령) ",
                                Style(color="black", bgcolor=tc("accent"),
                                      bold=True)))
        if self.zoomed:
            segs.append(Segment("Z ", Style(color="black", bgcolor=tc("warning"),
                                             bold=True)))
        if self.sync:
            segs.append(Segment("SYNC ", Style(color="white", bgcolor=tc("error"),
                                                bold=True)))
        if self.autoresume:
            segs.append(Segment("AR ", Style(color="black", bgcolor=tc("accent"),
                                              bold=True)))
        self._rec_zone = None
        if self.capture:        # 패널 출력 캡처 중
            rx0 = sum(sum(_char_cells(c) for c in s.text) for s in segs)
            self._rec_zone = (rx0, rx0 + 4)   # "REC "
            segs.append(Segment("REC ", Style(color="white", bgcolor=tc("error"),
                                               bold=True)))
        self._usage_zone = None
        # 활성 Claude 패널: 컨텍스트 사용량(best-effort) + 세션 누적 토큰(#3, Σ)
        uparts = []
        if self.claude_usage:
            uparts.append(self.claude_usage)
        if self.claude_tokens:
            # 기호(Σ)와 숫자 사이 한 칸 띄움(§10). 계정이 있으면 @계정 곁들임.
            # 터미널 폭이 넉넉하면(≥80칸) 약어(6.3M) 대신 세 자리 콤마 전체 숫자로
            # 보여준다(#30 사용자 요청). 좁으면 기존 약어로 자리를 아낀다.
            num = (f"{self.claude_tokens:,}" if w >= 80
                   else _fmt_tokens(self.claude_tokens))
            tk = "Σ " + num
            if self.claude_account:
                tk += " @" + self.claude_account
            uparts.append(tk)
        if uparts:
            utext = " " + " · ".join(uparts) + " "
            ux0 = sum(sum(_char_cells(c) for c in s.text) for s in segs)
            self._usage_zone = (ux0, ux0 + sum(_char_cells(c) for c in utext))
            segs.append(Segment(utext,
                                Style(color="white", bgcolor=tc("secondary"),
                                      bold=True)))
        if self.prefix_off:
            segs.append(Segment("NEST ", Style(color="white",
                                               bgcolor=tc("secondary"), bold=True)))
        for win in ([] if self.hide_tabs else self.windows):
            flag = "!" if win.get("bell") else ("#" if win.get("activity") else "")
            label = f"{win['index'] + 1}:{win['name']}{flag} "   # 표시 1-based(#21)
            if win["active"]:
                st = active
            elif win.get("bell"):
                st = Style(color="white", bgcolor=tc("error"), bold=True)
            elif win.get("activity"):
                st = Style(color="black", bgcolor=tc("warning"))
            else:
                st = base
            segs.append(Segment(label, st))
        # 오른쪽은 host/시각/날짜를 별도 런으로 쪼개 그린다 — 원격이면 host 를
        # `ssh:` 접두사+붉은색으로, 시각/날짜는 각각 시계/달력 클릭 존으로.
        right_parts = self._expand_parts(self.right_fmt)
        host_style = Style(color=tc("error"), bgcolor=self.bg, bold=True)
        built = []   # (kind, text, style, cells)
        right_w = 0
        for kind, text in right_parts:
            st = base
            if kind == "host" and self._is_remote:
                text = "ssh:" + text
                st = host_style
            cells = sum(_char_cells(c) for c in text)
            built.append((kind, text, st, cells))
            right_w += cells
        used = sum(sum(_char_cells(c) for c in s.text) for s in segs)
        pad = max(0, w - used - right_w)
        if pad:
            segs.append(Segment(" " * pad, base))
        # 각 런 세그먼트를 붙이며 누적 x 로 시각(시계)/날짜(달력) 클릭 존 계산.
        self._clock_zone = None
        self._date_zone = None
        x = used + pad
        for kind, text, st, cells in built:
            segs.append(Segment(text, st))
            if cells and kind == "time":
                self._clock_zone = (x, x + cells)
            elif cells and kind == "date":
                self._date_zone = (x, x + cells)
            x += cells
        # 폭 맞추기(자르기)
        return Strip(segs).adjust_cell_length(w, base)

    def on_mouse_down(self, event: events.MouseDown):
        if not self.app.mouse_enabled:
            return
        # 클릭 존(REC/시계/날짜/사용량)은 주 상태가 그려지는 맨 아래 줄에만 있다.
        if event.y != self.size.height - 1:
            return
        rz = self._rec_zone
        if rz and rz[0] <= event.x < rz[1]:
            self.app.show_capture_info(self.capture_path, self.capture_size)
            event.stop()
            return
        z = self._clock_zone
        if z and z[0] <= event.x < z[1]:
            self.app.toggle_clock(self.app.layout.get("active"))
            event.stop()
            return
        dz = self._date_zone
        if dz and dz[0] <= event.x < dz[1]:
            self.app.toggle_calendar(self.app.layout.get("active"))
            event.stop()
            return
        uz = self._usage_zone
        if uz and uz[0] <= event.x < uz[1]:
            self.app.open_claude_usage_tree()   # 토큰 사용량 클릭 → Claude 트리
            event.stop()
