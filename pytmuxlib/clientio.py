"""입력 이벤트·렌더 믹스인 — client.py 에서 분리한 PytmuxApp 믹스인(§5.4 파일 분할, CODE_REVIEW 4-1).

거동 불변·위치만 분리: 메서드는 self 경유라 MRO 로 그대로 동작한다. import 헤더는
client.py 원본을 복제(over-import + noqa F401)해 이름 해석 누락을 원천 차단했다.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shlex
import socket
import subprocess
import time
import traceback

# textual/rich 심볼(종전 build_client_app 안에서 지연 import 하던 것 — §5.4 de-nest).
# client.py 는 이미 모듈 최상위에서 clientwidgets/clientscreens 를 import 하고 그들이
# textual 을 끌어오므로, 이 import 를 최상위로 올려도 기동 비용은 동일하다(textual 은
# 이미 로드됨). 최상위에 둬야 PytmuxApp/믹스인이 팩토리 클로저 밖(모듈 레벨)에서도
# 이 심볼을 참조할 수 있다(CLI 경량 기동은 pytmux.py 의 _LAZY 가 client import 자체를
# 미뤄 달성 — 이 심볼 위치와 무관).
from rich.style import Style
from textual import events
from textual.app import App, ComposeResult
from textual.await_complete import AwaitComplete
from textual.binding import Binding
from textual.geometry import Offset
from textual.suggester import SuggestFromList

from . import clientclip, clientrender, i18n, ipc, plugins, proc, version
from .clientutil import (  # noqa: F401  (클로저에서 이름으로 사용)
    COMMAND_ARGHIST, COMMAND_NOARG, COMMAND_OPTIONS, COMMANDS, COMPLETIONS,
    DEFAULT_STYLE, norm_sep,
    SETTINGS, SETTINGS_CATS,
    REMOTE_PINK, REMOTE_PINK_DIM,
    _BOX_BITS, _BOX_REV, _JAMO, _KEY_DIAG,
    _TB_ACTIVE_STYLE, _TB_BORDER_STYLE, _TB_INACTIVE_STYLE,
    _char_cells, _client_relaunch_ok, _darken_style, _dim_inactive_style,
    _first_int, _first_signed_int, _is_emoji, _opt_value, _restart_check_eval,
    _signed_int, _with_reverse,
    has_hangul, hangul_to_qwerty,
    _normalize_key, _shell_argv, key_to_bytes, make_style, theme_color)
from .clientscreens import (  # noqa: F401  (클로저에서 push_screen 으로 사용)
    ChooseBufferScreen, ChooseLayoutScreen, ChooseTreeScreen,
    CommandListScreen, CommandOptionsScreen, ComposePromptScreen, ConfirmScreen,
    InfoScreen, InfoTabsScreen, MenuScreen, PluginManagerScreen, PromptScreen,
    SettingsScreen)
from .clientwidgets import (  # noqa: F401  (PytmuxApp.compose·ghost suggester)
    MultiplexerView, SepInsensitiveSuggester, StatusBar, TabBar)
from .keymap import (_key_to_ctrl_bytes, _tmux_key_to_textual,
                     config_path_for_write, load_config, normalize_binding_key,
                     set_config_option, textual_key_to_tmux)
from .protocol import MIN_H, MIN_W, PROTO_VERSION, read_msg, write_msg


class _InputMixin:
    # §5.4 입력/키보드 클러스터(전역 키 디스패치·모드 핸들러·ESC 동선·display 모드) — 모듈 레벨 분리, PytmuxApp 은 MRO 로 상속
    def on_paste(self, event: events.Paste):
        # 외부 터미널의 붙여넣기(멀티라인 포함)를 활성 패널로 패스스루.
        # 내부 앱이 bracketed paste 를 켰으면 서버가 마커로 감싼다.
        # (이미지 붙여넣기는 내부 Claude Code 가 공유 OS 클립보드에서 읽음)
        if len(self.screen_stack) > 1:
            return  # 프롬프트/모달 입력은 그 스크린이 처리
        if self.writer and event.text:
            self.send_cmd("paste", text=event.text)
            # IME 한글 확정 입력은 Textual 이 Paste 이벤트로 전달한다(개별 Key 가
            # 아님) — 컴포즈 '프롬프트 인계' 시드가 비던 원인. 붙여넣기/IME 확정분도
            # 현재 프롬프트 추정에 누적한다(on_key 패스스루와 같은 _prompt_buf).
            self._compose_track_input(self.layout.get("active"),
                                      event.text.encode("utf-8", "replace"))
        event.stop()

    # ---- 이벤트 ----
    async def on_resize(self, event):
        if self.writer:
            cols, rows = self._content_size()
            await write_msg(self.writer, {"t": "resize", "cols": cols, "rows": rows})
        # iOS(Blink 등)에서 소프트 키보드를 닫았다 열면 resize 가 키보드
        # 애니메이션 중간 크기로만 오고 최종 크기 이벤트가 누락될 수 있다.
        # 그 결과 마지막 행(하단 테두리)이 갱신되지 않은 채 남는다. 잠시 뒤
        # 정착된 크기로 한 번 더 통지해 새 프레임을 받아 재합성한다.
        self.set_timer(0.3, self._send_resize)

    def on_key(self, event: events.Key):
        # 진단: 어떤 가드/모드 분기보다 먼저 — 휠이 화살표로 새는지 본다
        # (mouse-debug 켜진 경우에만, 내비게이션 키만 기록).
        self._log_key(event.key)
        # 메뉴/프롬프트 등 모달이 떠 있으면 그 스크린이 처리
        if len(self.screen_stack) > 1:
            return
        # 클립보드 붙여넣기 진행 중엔 ESC 외 키를 무시한다(요청): 외부 도구로
        # 붙여넣는 동안 친 키가 완료 후 패널로 새는 것을 막는다. ESC/Shift+ESC 는
        # 그대로 흘려 보내(빠져나갈 수단) 아래에서 평소대로 처리한다.
        if self._pasting and event.key not in ("escape", "shift+escape"):
            event.prevent_default()
            event.stop()
            return
        # ESC 오토리핏 디바운스(#32, #36): 터미널은 키 뗌(release) 이벤트를 주지
        # 않아 키를 누르고 있으면 같은 ESC 가 반복 도착해 모드가 깜빡인다. 직전 ESC
        # **도착** 후 _ESC_DEBOUNCE 초 안에 온 ESC/Shift+ESC 는 오토리핏으로 보고
        # 무시한다. _last_esc_ts 를 (무시하든 처리하든) **매번** 갱신해 창을 슬라이딩
        # 시킨다 — 빠른 연속 스트림은 직전과 늘 가까워 계속 무시된다.
        # #36: 창을 OS 오토리핏 **반복 간격**(보통 ~33ms)보다는 크고 사람이 의도적으로
        # 두 번 누르는 간격(보통 100ms+)보다는 작게(0.06s) 잡는다. 그래야 빠른 더블탭
        # (ESC 두 번 → esc 모드 진입/해제)이 살아난다. 트레이드오프: 키를 길게 누르면
        # 오토리핏 **첫 반복**(repeat-delay 250~500ms 뒤)은 이 창을 벗어나 모드가 한 번
        # 토글될 수 있다(이후 빠른 스트림은 모두 무시). 타이밍만으로는 '첫 오토리핏'과
        # '의도적 두 번째 누름'을 구분할 수 없어 받아들인 절충이다(앱에 ESC 전달은
        # 항상 Shift+ESC/`send-escape` 경로라 이 토글은 셸로 새지 않는다).
        if event.key in ("escape", "shift+escape"):
            now = time.monotonic()
            prev = self._last_esc_ts
            self._last_esc_ts = now          # 매 ESC 갱신(슬라이딩 창)
            if now - prev < self._ESC_DEBOUNCE:
                event.prevent_default()
                event.stop()
                return
        if self.mode == "prefix":
            self._handle_prefix(event)
            event.prevent_default()
            event.stop()
            return
        if self.mode == "scroll":
            self._handle_scroll_key(event)
            event.prevent_default()
            event.stop()
            return
        if self.mode == "display":
            self._handle_display_key(event)
            event.prevent_default()
            event.stop()
            return
        if self.mode == "esc":
            self._handle_esc_mode(event)
            event.prevent_default()
            event.stop()
            return
        # normal
        # ESC: 활성 패널에 시계/달력 오버레이가 떠 있으면 그것부터 닫는다(요청 —
        # Shift+ESC 뿐 아니라 단순 ESC 로도 닫힘). 오버레이가 없을 때만 명령(esc)
        # 모드로 진입한다(키를 명령으로 받음; 셸로는 전달하지 않음).
        if event.key == "escape":
            if self._close_active_overlay():
                event.prevent_default()
                event.stop()
                return
            self.mode = "esc"
            self.status.cmd_mode = True
            self.status.refresh()
            event.prevent_default()
            event.stop()
            return
        # ` (backtick): ESC 와 더불어 esc(명령) 모드 진입(요청 2026-06-12). ESC 는
        # 누른 직후 빠른 숫자가 \x1b+숫자=Alt+숫자로 병합되는 터미널 타이밍 이슈가
        # 있는데, ` 진입키는 그 영향을 안 받는 대체 경로다. 패널에 **리터럴 `** 를
        # 넣으려면 esc 모드에서 ` 를 한 번 더(double-tap) — tmux prefix 관례와 동일.
        if event.key == "grave_accent" or event.character == "`":
            if self._close_active_overlay():
                event.prevent_default()
                event.stop()
                return
            self.mode = "esc"
            self.status.cmd_mode = True
            self.status.refresh()
            event.prevent_default()
            event.stop()
            return
        # ESC 모드 진입은 위에서 즉시(동기) 이뤄지지만, 'ESC' 한 키를 누른 직후
        # 아주 빨리 숫자를 누르면 터미널이 둘을 한 escape 시퀀스(\x1b<digit>)로
        # 합쳐 **Alt+숫자**로 보내, ESC 가 단독 키로 안 와 모드 진입이 누락되고
        # 숫자가 셸로 새던 문제(요청). Alt+숫자를 ESC 모드의 숫자키와 동일하게
        # 그 번호(1-based) 탭으로 전환해 'esc 다음 숫자' 가 빠르게도 동작하게 한다.
        if (len(event.key) == 5 and event.key.startswith("alt+")
                and event.key[4].isdigit()):
            idx = int(event.key[4]) - 1
            if any(t["index"] == idx for t in self.tabbar.tabs):
                self.send_cmd("select_window", index=idx)
            else:
                self.tabbar.blink_active()   # 없는 번호 → 활성 탭 깜빡임 안내
            event.prevent_default()
            event.stop()
            return
        # F10: 전체 메뉴(컨텍스트 메뉴 최상위) 직진 진입 — GUI/TUI 의 "메뉴바 활성"
        # 관례(F10_MENU_SCENARIO.md). 종전엔 prefix Enter·우클릭만이 진입로였다.
        # F12(명령 프롬프트)와 같은 normal-mode 단일키 진입이며, 메뉴가 떠 있을 땐
        # MenuScreen.on_key 가 F10 을 Esc 처럼 받아 닫는다(메뉴바 토글).
        if event.key == "f10":
            self.open_menu()
            event.prevent_default()
            event.stop()
            return
        # F12: 바로 명령 프롬프트 진입(ESC 모드가 아닐 때).
        # 중첩 prefix 가로채기 토글은 prefix F12 로 이동.
        if event.key == "f12":
            self.open_prompt("command", "")
            event.prevent_default()
            event.stop()
            return
        if self.prefix_enabled and _normalize_key(event.key) == self.prefix_key:
            self.mode = "prefix"
            event.prevent_default()
            event.stop()
            return
        # Ctrl+V(윈도우) / Command+V(맥): OS 클립보드를 활성 패널에 붙여넣기.
        # (맥 터미널은 보통 Cmd+V 를 가로채 on_paste 로 넘기지만, Cmd/Win 메타
        #  키가 super+v 로 직접 들어오는 환경을 위해 함께 처리한다.)
        if event.key in ("ctrl+v", "super+v"):
            self.paste_os_clipboard()
            event.prevent_default()
            event.stop()
            return
        # Shift+ESC: 활성 패널에 시계/달력 오버레이가 떠 있으면 그것부터 닫는다
        # (오버레이 [x] 버튼 폐지). 오버레이가 없으면 기존처럼 ESC 를 패널로 전달.
        if event.key == "shift+escape" and self._close_active_overlay():
            event.prevent_default()
            event.stop()
            return
        # 활성 패널에 플러그인 오버레이(달력 등)가 떠 있으면 네비게이션 키를
        # 플러그인이 가로챈다(예: 달력 ←/→ 이전·다음 달). 소비되면 그 키는 패널로
        # 보내지 않는다. 플러그인이 없으면 False → 아래 일반 입력 경로로 흐른다.
        if self.plugins.client_overlay_key(self, event):
            event.prevent_default()
            event.stop()
            return
        # 사용자 root 바인딩(`bind -n`, §2.5): 내장 크롬 키·오버레이 키 다음,
        # 패널 패스스루 직전에 가로챈다. 토큰 매칭은 prefix 테이블과 동일
        # (IME 자모 → QWERTY 정규화, 글자는 character·그 외는 key).
        if self.root_bindings:
            rk = _normalize_key(event.key)
            rch = event.character
            rnch = _JAMO.get(rch, rch) if rch else rch
            token = (rnch if (rnch and rnch.isprintable()
                              and not rk.startswith("ctrl+")) else rk)
            rcmd = self.root_bindings.get(token)
            if rcmd:
                self._run_command(rcmd)
                event.prevent_default()
                event.stop()
                return
        # 패널로 보낼 확정 입력을 플러그인이 관찰하게 한다(ime-indicator 가 한/영
        # 상태 추정). send_input 보다 먼저 호출해 패널 부재/전송 실패와 무관하게
        # 상태가 갱신되게 한다. 플러그인 없으면 no-op(delete-to-disable).
        self.plugins.client_key(self, event)
        data = key_to_bytes(event)
        if data:
            self.send_input(data)
            # 컴포즈 '프롬프트 인계' 시드용: 활성 패널에 보낸 입력을 패널별로 누적해
            # 현재 프롬프트 텍스트를 추정한다(open_compose 가 시드+비우기에 사용).
            self._compose_track_input(self.layout.get("active"), data)
        event.prevent_default()
        event.stop()

    def _handle_prefix(self, event: events.Key):
        # Windows 콘솔의 Shift/Ctrl/Alt 단독 키다운 아티팩트(character "\x00" →
        # ctrl+@; _handle_esc_mode 주석 참고)는 prefix 를 소비하면 안 된다 — 안 그러면
        # `prefix %`(분할)·`prefix "` 등 **Shift 조합 바인딩**이 수정자 이벤트에서
        # 모드가 normal 로 풀려 셸로 새던 문제. 무시하고 prefix 대기 상태를 유지한다.
        if event.character == "\x00":
            return
        self.mode = "normal"
        # IME(한글 자모)가 켜져 있어도 동작하도록 키를 QWERTY 로 정규화
        k = _normalize_key(event.key)
        ch = event.character
        nch = _JAMO.get(ch, ch) if ch else ch  # 자모 → 영문
        # prefix 를 한 번 더 누르면 prefix 키 자체를 셸로 전송
        if k == self.prefix_key:
            self.send_input(self.prefix_bytes)
            return
        # 사용자 정의 바인딩 우선 (config 의 bind)
        token = nch if (nch and nch.isprintable() and not k.startswith("ctrl+")) else k
        if token in self.bindings:
            self._run_command(self.bindings[token])
            return
        if k == "f12":   # prefix F12: 중첩 prefix 가로채기 토글
            self.prefix_enabled = not self.prefix_enabled
            self.status.prefix_off = not self.prefix_enabled
            self.status.refresh()
            self.display_message("outer prefix " +
                                 ("ON" if self.prefix_enabled else "OFF (중첩)"))
            return
        if k == "percent_sign" or ch == "%":
            self.send_cmd("split", orient="lr")
        elif k == "quotation_mark" or ch == '"':
            self.send_cmd("split", orient="tb")
        elif k == "x":
            self.open_prompt("confirm", "kill-pane? (y/N)",
                             action=lambda: self.send_cmd("kill_pane"))
        elif k == "z":
            self.send_cmd("zoom")
        elif k == "o":
            self.send_cmd("cycle_pane")
        elif k == "semicolon" or ch == ";":
            self.send_cmd("last_pane")
        elif k == "q":
            self._enter_display()
        elif k == "space":
            self.send_cmd("cycle_layout")
        elif k == "ctrl+o":
            self.send_cmd("rotate", forward=True)
        elif ch == "{":
            self.send_cmd("swap_pane", forward=False)
        elif ch == "}":
            self.send_cmd("swap_pane", forward=True)
        elif ch == "!":
            self.send_cmd("break_pane")
        elif k in ("left", "right", "up", "down"):
            self.send_cmd("select_pane", dir=k)
        elif k in ("H", "J", "K", "L"):
            self.send_cmd("resize_dir",
                          dir={"H": "left", "L": "right",
                               "K": "up", "J": "down"}[k], cells=3)
        elif k == "c":
            self.send_cmd("new_window")
        elif k == "comma" or ch == ",":
            cur = self._active_window_name()
            self.open_prompt("rename_window", cur or "rename-tab",
                             suggest=cur)   # ghost(타이핑=덮어쓰기)
        elif k == "ampersand" or ch == "&":
            self.confirm_kill_tab()
        elif k == "T":
            cur = self._active_pane_title()
            self.open_prompt("rename_pane", cur or "set pane title",
                             suggest=cur)   # 현재 패널 제목=ghost
        elif k == "t":
            fn = getattr(self, "toggle_clock", None)  # clock 플러그인 설치
            fn and fn(self.layout.get("active"))
        elif k == "R":
            self.send_cmd("set_autoresume")
        elif k == "r":
            # prefix r: 화면 전체 강제 재그리기(§2.12) — 깨진/잔상 화면 회복.
            self._run_command("redraw")
        elif k == "colon" or ch == ":":
            self.open_prompt("command", "")
        elif k == "n":
            self.send_cmd("next_window")
        elif k == "p":
            self.send_cmd("prev_window")
        elif k == "l":
            self.send_cmd("last_window")
        elif k == "w":
            self.request_tree()
        elif k == "period" or ch == ".":
            self.open_prompt("move_window", "move-tab to index")
        elif k.isdigit():
            n = int(k) - 1     # prefix+숫자: 1-based 표시 → 0-based 내부(#21)
            if n >= 0:
                self.send_cmd("select_window", index=n)
        elif k == "d":
            self.exit(message="detached")
        elif k == "left_square_bracket" or ch == "[":
            self.mode = "scroll"
        elif k == "right_square_bracket" or ch == "]":
            self.send_cmd("paste_buffer", index=0)
        elif k == "equals_sign" or ch == "=":
            self.choose_buffer()
        elif k == "enter":
            self.open_menu()
        # 그 외 키는 무시

    # ---- display-panes (prefix q) ----
    def _enter_display(self):
        self.mode = "display"
        self._composite()
        self.set_timer(1.5, self._auto_exit_display)

    def _auto_exit_display(self):
        if self.mode == "display":
            self._exit_display()

    def _exit_display(self):
        self.mode = "normal"
        self._composite()

    def _handle_display_key(self, event: events.Key):
        panes = self.layout.get("panes", [])
        k = event.key
        if k.isdigit() and int(k) < len(panes):
            self.send_cmd("select_pane_id", id=panes[int(k)]["id"])
        self._exit_display()

    def _pane_id_by_index(self, idx):
        """display-panes 오버레이의 0-based 패널 번호(문자열/None)를 현재
        레이아웃의 패널 id 로 변환한다. None·비숫자·범위밖이면 None.
        swap-pane -s/-t 가 사용자에게 보이는 그 번호로 패널을 지정하게 한다
        (_handle_display_key 의 숫자→id 매핑과 같은 0-based 규칙)."""
        if idx is None or not idx.isdigit():
            return None
        panes = self.layout.get("panes", [])
        i = int(idx)
        return panes[i]["id"] if 0 <= i < len(panes) else None

    def _tab_target_index(self, args):
        """select/move/swap-tab 의 대상 탭을 0-based 인덱스로 해석한다.

        `-t N` 또는 첫 정수 토큰. **양수는 1-based**(1=첫 탭), **음수는 끝에서**
        (-1=마지막, -2=뒤에서 둘째). 인덱스가 아예 없으면 조용히 None(무동작).
        인덱스는 주어졌으나 범위 밖(0·-99·탭수 초과)이면 상태줄에 알리고 None —
        과거엔 음수·범위밖을 모두 조용히 무시해 `move-tab -2` 등이 먹통이었다
        (§2.8 인덱스 명령 음수/침묵 실패)."""
        raw = _signed_int(_opt_value(args, "-t"))
        val = raw if raw is not None else _first_signed_int(args)
        if val is None:
            return None                       # 인덱스 미지정 → 무동작(기존)
        n = len(self.tabbar.tabs)
        i = (val - 1) if val > 0 else (n + val if val < 0 else -1)
        if 0 <= i < n:
            return i
        self.display_message(i18n.t(
            "msg.bad_tab_index", default="탭 번호 범위 초과: {v}", v=val))
        return None

    # ---- ESC(명령) 모드 ----
    def _exit_esc(self):
        self.mode = "normal"
        self.status.cmd_mode = False
        if self.tabbar.bar_focus:
            self.tabbar.bar_focus = False
            self.tabbar.refresh()
        if self._close_focus:              # 닫기 [x] 포커스 해제
            self._close_focus = False
            self._composite()
        if self._status_focus is not None:   # 상태바 버튼 포커스 해제(요청)
            self._status_focus = None
            self.status.focus_btn = None
        self.status.refresh()

    def _focus_tabbar(self):
        self._close_focus = False
        self.tabbar.sel = self._active_tab_index()
        self.tabbar.bar_focus = True
        self._composite()
        self.tabbar.refresh()

    def _handle_close_focus(self, event: events.Key):
        """ESC 모드 닫기 [x] 버튼 포커스 동선(#31 — 최상단 패널에서 ↑ 로 진입).
        Enter=탭 닫기, ↑=탭바, ↓/←=패널 복귀, Esc=모드 종료. (프롬프트 헤더
        포커스 동선(#5)은 헤더와 함께 2026-06-13 제거 — [x] 동선만 남음.)"""
        k = event.key
        if k == "enter":
            self._close_focus = False
            self.confirm_kill_tab()
        elif k == "up":
            self._focus_tabbar()
        elif k in ("down", "left"):
            self._close_focus = False    # 패널로 복귀
            self._composite()
        elif k == "escape":
            self._close_focus = False
            self._composite()
            self._exit_esc()

    def _handle_esc_mode(self, event: events.Key):
        """ESC 명령 모드: 방향키=패널 이동, 위로 더 가면 상단 탭바 포커스.
        탭바 포커스에서는 ←→ 탭 선택, Enter 전환, +/x 추가/삭제, ↓/Esc 복귀."""
        k = event.key
        ch = event.character
        # Windows 콘솔(ConPTY)은 Shift/Ctrl/Alt **단독** 키다운에도 KEY_EVENT 를 주고
        # 그 UnicodeChar 가 0(\x00)이라, Textual 의 XTerm 파서가 이를 ctrl+@(character
        # "\x00") 키 이벤트로 만든다. `:`·`?` 처럼 **Shift 가 필요한** esc 모드 명령을
        # 누르면 이 수정자 단독 아티팩트가 진짜 글자보다 **먼저** 도착해, 아래 catch-all
        # `else: self._exit_esc()` 를 때려 esc 모드가 글자를 받기도 전에 풀려버렸다
        # (증상: ESC 후 :/? 가 무반응으로 esc 모드만 해제). 의미 없는 수정자 단독
        # 이벤트는 esc 모드에서 명령이 될 수 없으니 그냥 무시해 모드를 유지한다.
        # (셸로 NUL 을 보내는 Ctrl+Space 등은 normal 모드 패스스루에서만 살아있다.)
        if ch == "\x00":
            return
        # IME(두벌식)가 켜져 있어도 ESC 모드 단축키가 동작하도록 입력 문자를 물리
        # QWERTY 키로 되돌린다 — 'n'(새 탭) 키가 'ㅜ' 로, 'p'(분할)가 'ㅔ' 로,
        # 'h'/'a'/'x'/'d' 등도 자모로 들어온다. 한 글자 자모만 정규화하고 비-한글·
        # 방향키/Enter/Esc(IME 무관)는 그대로 둔다 — 모든 단축키가 IME 무관하게 동작.
        if ch and len(ch) == 1 and has_hangul(ch):
            ch = hangul_to_qwerty(ch)
        # ` (double-tap): ` 로 esc 모드에 들어온 뒤 ` 를 한 번 더 → 패널에 리터럴
        # backtick 을 전달하고 모드 종료(요청 2026-06-12). esc 모드 어디서든(탭바·헤더
        # 포커스 포함) 일관되게 동작하도록 하위 포커스 동선보다 먼저 처리한다.
        if event.key == "grave_accent" or ch == "`":
            self.send_input(b"`")
            self._exit_esc()
            return
        tb = self.tabbar
        if self._close_focus:                 # 닫기 [x] 포커스 동선(#31)
            self._handle_close_focus(event)
            return
        if self._status_focus is not None:    # 하단 상태바 버튼 포커스 동선(요청)
            self._handle_status_focus(event)
            return
        # Shift+ESC: esc 모드에서도 활성 패널에 ESC(\x1b)를 전달한다(#22 — 예전엔
        # esc 모드에서 shift+escape 가 그 외 키와 함께 모드만 종료하고 ESC 를 안
        # 보냈다). 오버레이가 떠 있으면 그것부터 닫고, 아니면 ESC 전달 후 모드 종료.
        if event.key == "shift+escape":
            if not self._close_active_overlay():
                self.send_input(b"\x1b")
            self._exit_esc()
            return
        # 숫자키: 그 번호의 탭으로 즉시 전환(ESC 모드 어디서든). 표시는 1-based 라
        # 입력 숫자도 1-based → 내부 0-based 로 -1(#21). 해당 탭이 없으면 모드만 빠짐.
        if ch and ch.isdigit():
            idx = int(ch) - 1
            if any(t["index"] == idx for t in tb.tabs):
                self.send_cmd("select_window", index=idx)
                self._exit_esc()
            else:
                # 없는 번호 → 전환 불가. 현재 활성 탭을 깜빡여 안내하고 esc 모드는
                # 유지(다른 번호로 재시도 가능)(요청).
                tb.blink_active()
            return
        # n = 새 탭, p = 새 패널(상하 분할, 새 패널은 아래). ESC 모드 어디서든
        # 동작하고 액션 후 모드를 빠져 새 탭/패널에서 바로 입력하게 한다. 분할
        # 경계는 마우스로 끌어 바로 재배치할 수 있다(요청).
        if ch == "n":
            self.send_cmd("new_window")
            self._exit_esc()
            return
        if ch == "p":
            self.send_cmd("split", orient="tb")
            self._exit_esc()
            return
        if ch == "e":
            # esc e: 활성 패널에 ESC(\x1b) 전달 후 모드 종료 — Shift+ESC 로 ESC 를 못
            # 보내는 터미널(WT 등 Shift 수정자 누락)에서 빠르게 ESC 를 보내는 동선
            # (사용자 요청 2026-06-18). Shift+ESC·send-escape 와 같은 ESC-전달 통로의
            # 키보드 단축(2단: ESC→e). 패널 ESC 는 의도된 통로(Shift+ESC/send-escape/
            # 이제 esc e)에서만 — 단독 ESC 두 번은 여전히 전달 없음(56632 불변).
            self.send_input(b"\x1b")
            self._exit_esc()
            return
        if k == "insert":
            # esc Insert: 블록 선택(Shift+방향키/Home/End)이 되는 멀티라인 작성창을
            # 연다(옵트인, 필요할 때 매번). 자식 프롬프트 입력기가 범위 선택 편집을
            # 지원하지 않을 때, pytmux 자체 편집기에서 작성→완료 시 활성 패널에
            # bracketed paste 로 투입(권고안 B). 모드는 빠지고 모달이 포커스를 잡는다.
            self._exit_esc()
            self.open_compose()
            return
        if tb.bar_focus:
            tabs = tb.tabs
            idxs = [t["index"] for t in tabs]
            if k == "shift+left" and tb.sel in idxs:   # 선택 탭 왼쪽으로 이동
                pos = idxs.index(tb.sel)
                if pos > 0:
                    self.send_cmd("move_tab", index=pos, to=pos - 1)
                    tb.sel = pos - 1
                    tb.refresh()
            elif k == "shift+right" and tb.sel in idxs:  # 오른쪽으로 이동
                pos = idxs.index(tb.sel)
                if pos < len(idxs) - 1:
                    self.send_cmd("move_tab", index=pos, to=pos + 1)
                    tb.sel = pos + 1
                    tb.refresh()
            elif k in ("left", "up", "right") and idxs:
                # 탭들 + 맨 오른쪽 [+] 버튼을 한 줄로 순환(#26).
                positions = idxs + ["+"]
                cur = (positions.index(tb.sel) if tb.sel in positions else 0)
                step = -1 if k in ("left", "up") else 1
                tb.sel = positions[(cur + step) % len(positions)]
                tb.refresh()
            elif k == "enter":
                # [+] 선택이면 새 탭, 아니면 그 탭으로 전환. 둘 다 ESC 모드 종료.
                if tb.sel == "+":
                    self.send_cmd("new_window")
                else:
                    self.send_cmd("select_window", index=tb.sel)
                self._exit_esc()   # bar_focus 해제·refresh 포함(#3)
            elif ch in ("+", "a"):
                self.send_cmd("new_window")
            elif (ch in ("x", "d") or k == "delete") and tb.sel in idxs:
                self.confirm_kill_tab()
            elif k in ("down", "escape"):
                tb.bar_focus = False
                tb.refresh()
                if k == "escape":
                    self._exit_esc()
            return
        if k == "up" and not self._pane_above():
            # 최상단 패널에서 ↑: 우상단 닫기 [x] 포커스로(#31 — 거기서 다시 ↑ 면
            # 탭바). [x] 는 항상 그려진다.
            self._close_focus = True
            self._composite()
        elif k == "down" and not self._pane_below() \
                and self._enter_status_focus():
            # 최하단 패널에서 ↓ → 하단 상태바 버튼 포커스(요청). 버튼이 없으면
            # _enter_status_focus 가 False 라 아래 일반 select_pane 으로 떨어진다.
            pass
        elif k in ("left", "right", "up", "down"):
            # 전환된 새 활성 패널을 깜빡여 선택을 가시화(서버가 layout 으로
            # active 를 바꿔 보내면 _dispatch 가 _flash_pane 을 띄운다).
            self._flash_pending = True
            self.send_cmd("select_pane", dir=k)  # 모드 유지(연속 이동)
        elif ch == ":" or k == "colon":
            self._exit_esc()
            self.open_prompt("command", "")
        elif ch == "?":                       # ':' 대신 '?' → 바로 help 팝업
            self._exit_esc()
            self._run_command("help")
        elif k == "escape":
            # ESC 모드에서 ESC 를 한 번 더 → **모드만 빠진다(패널로 ESC 전달 없음)**.
            # 패널(앱)에 실제 ESC(\x1b)를 보내는 통로는 **항상 Shift+ESC 일 때만**
            # 이어야 한다(사용자 요청). 즉 esc 모드 진입/종료에 쓴 ESC 가 앱으로
            # 새지 않게 한다. 앱에 ESC 가 필요하면 Shift+ESC(패스스루) 또는
            # `send-escape` 명령/전용 바인딩을 쓴다(아래 enter/i/그 외 키와 동일하게
            # 전달 없이 종료).
            self._exit_esc()
        else:
            # enter/i/그 외 → 명령 모드 종료(셸 입력 복귀, ESC 전달 없음)
            self._exit_esc()

    def _handle_scroll_key(self, event: events.Key):
        aid = self.layout.get("active")
        # Windows 콘솔의 Shift/Ctrl/Alt 단독 키다운 아티팩트(character "\x00" → ctrl+@;
        # _handle_esc_mode 주석 참고)는 무시한다. 현재 이 핸들러엔 모드를 빠지는
        # catch-all else 가 없어 아티팩트가 무해한 no-op 이지만, copy/scroll 모드의
        # Shift 키(`G`=맨끝·`N`=역방향 검색·`/`=검색)마다 선행하므로 — 향후 분기가
        # 실수로 ctrl+@/\x00 를 잡지 않도록, 그리고 esc/prefix 와 동작을 일관되게
        # 하려고 명시적으로 가드한다.
        if event.character == "\x00":
            return
        k = _normalize_key(event.key)  # IME 무관 (j/k/g/G/n/N/q 등)
        ch = event.character
        half = max(1, self.layout.get("rows", 24) // 2)
        vi = self.mode_keys == "vi"
        emacs = self.mode_keys == "emacs"
        if k == "up" or (vi and k == "k") or (emacs and k == "ctrl+p"):
            self.send_scroll(aid, delta=1)
        elif k == "down" or (vi and k == "j") or (emacs and k == "ctrl+n"):
            self.send_scroll(aid, delta=-1)
        elif k == "pageup" or (vi and k == "ctrl+u") or (emacs and k == "alt+v"):
            self.send_scroll(aid, delta=half)
        elif k == "pagedown" or (vi and k == "ctrl+d") or (emacs and k == "ctrl+v"):
            self.send_scroll(aid, delta=-half)
        elif k == "g":
            self.send_scroll(aid, top=True)
        elif k in ("G", "end"):
            self.send_scroll(aid, bottom=True)
        elif ch == "/" or k == "slash":
            self.open_prompt("search", i18n.t("search.prompt_up"))
            return  # 프롬프트 모드로 전환
        elif k == "n":
            self.send_cmd("search", direction="up")
        elif k == "N":
            self.send_cmd("search", direction="down")
        elif k in ("q", "escape", "enter"):
            self.send_scroll(aid, bottom=True)
            self.mode = "normal"


class _RenderMixin:
    # §5.4 렌더/합성 클러스터(_composite 화면 합성·합성 디바운스·팝업 dim 오버라이드·탭닫기 존) — 모듈 레벨 분리, PytmuxApp 은 MRO 로 상속. push_screen/pop_screen 의 super() 는 MRO 상 App 으로 해석(마지막 믹스인)
    def _request_composite(self):
        """B9: 합성 코얼레싱 — 한 read 버스트(서버가 B4 로 배치 송신한 여러 screen/
        delta)에서 메시지마다 합성하지 않고, 루프 틱당 _composite 를 1회만 돈다.
        이미 예약돼 있으면 no-op. 버퍼된 메시지들은 reader 가 양보 없이 연속 처리
        하므로 call_soon 콜백이 버스트 끝에 한 번 실행된다(시각 결과 동일·1프레임)."""
        if self._composite_pending:
            return
        self._composite_pending = True
        try:
            asyncio.get_running_loop().call_soon(self._do_pending_composite)
        except RuntimeError:        # 러닝 루프 밖(직접 호출/테스트) — 즉시 합성
            self._composite_pending = False
            self._composite()

    def _do_pending_composite(self):
        self._composite_pending = False
        self._composite()

    # 셀 그리드 합성 헬퍼는 앱 비의존이라 clientrender.py 로 분리(#12). 호출은
    # clientrender.put_cell(...) 로 직접 한다(과거 self._put_cell). 시계/달력
    # 오버레이 그리기는 clock·calendar 플러그인의 client_overlay 훅으로 옮겼고,
    # 그리기 자유함수(draw_clock_overlay/draw_calendar_overlay)도 각 플러그인의
    # render.py(plugins/clock·calendar)로 옮겨 디렉토리째 지우면 함께 사라진다.
    def _composite(self):
        W = self.layout.get("cols", self.size.width)
        H = self.layout.get("rows", max(1, self.size.height - 1))
        cells = [[(" ", DEFAULT_STYLE) for _ in range(W)] for _ in range(H)]
        active = self.layout.get("active")
        # 비활성 패널 dim(§2.9): 패널이 둘 이상일 때만 — 단일 패널은 구분할 대상이 없다.
        _dim_on = self.inactive_dim and len(self.layout.get("panes", [])) > 1
        _dim_ratio = self.inactive_dim_ratio
        # 활성 패널 커서 셀의 전역 좌표(gx,gy) — 아래 반전커서 그리는 곳에서 채운다.
        # 매 합성마다 None 으로 초기화해 stale 좌표가 남지 않게 한다(IME preedit 동기화용).
        self._active_cursor_xy = None
        # 활성 패널의 오른쪽 경계 x(exclusive) — IME 배지를 화면 끝이 아니라 활성
        # 패널 우측 끝(테두리 위)에 그리려는 client_render 훅이 읽는다(2026-06-16).
        # 테두리 박스가 있으면 그 오른쪽 테두리 칸(bx+bw-1)에 덮도록 bx+bw, 없으면
        # 콘텐츠 끝 x+w. 매 합성마다 None 으로 초기화(stale 방지).
        self._active_pane_right = None
        # 활성 패널 콘텐츠 박스 (x, y, w, h) — 커서가 숨겨졌을 때(예: Claude '생각 중'
        # DECTCEM off) IME 배지가 화면 맨 위가 아니라 활성 패널 안(하단 프롬프트 영역)
        # 으로 떨어지게, client_render 훅이 폴백 앵커로 읽는다(요청 2026-06-21).
        self._active_pane_box = None
        # Claude footer 클릭존(§10 item 2/3) 재계산은 claude-code 플러그인의
        # client_render 훅(아래)이 매 합성마다 비우고 다시 채운다.
        for p in self.layout.get("panes", []):
            if p["id"] == active:
                _abox = p.get("box")
                self._active_pane_right = (_abox[0] + _abox[2]) if _abox \
                    else (p["x"] + p["w"])
                self._active_pane_box = (p["x"], p["y"], p["w"], p["h"])
            content = self.pane_content.get(p["id"])
            if not content:
                continue
            rows, cursor = content
            # 비활성 패널이면 이 패널 셀 스타일을 한 톤 옅게(§2.9). 활성 패널은 원색.
            p_dim = _dim_on and p["id"] != active
            for ry, row in enumerate(rows):
                if ry >= p["h"]:
                    break
                gy = p["y"] + ry
                if not (0 <= gy < H):
                    continue
                cx = p["x"]
                for text, style_d in row:
                    st = make_style(style_d)
                    if p_dim:
                        st = _dim_inactive_style(st, _dim_ratio)
                    for chh in text:
                        if cx - p["x"] >= p["w"]:
                            break
                        wch = _char_cells(chh)
                        # §2.10: 비활성(딤) 패널의 **컬러 이모지**는 터미널이 셀
                        # 전경색을 무시하고 자체 색 글리프로 그려 안 어두워진다 →
                        # dim 패널에 한해 **폭 보존 중간점(·)**으로 치환해 함께 어둡게
                        # 한다(활성화되면 재합성이 원본에서 다시 그려 자동 원복 —
                        # 별도 저장 불필요, 모달 배경 딤의 #25 치환과 같은 방식). 폭2
                        # 이모지는 두 칸 모두 ·· 로 채워 폭을 보존한다.
                        if p_dim and chh and _is_emoji(chh):
                            for k in range(wch):
                                if 0 <= cx + k < W and (cx + k - p["x"]) < p["w"]:
                                    cells[gy][cx + k] = ("·", st)
                            cx += wch
                            continue
                        if 0 <= cx < W:
                            cells[gy][cx] = (chh, st)
                        # 와이드 문자: 다음 칸은 연속 셀(렌더 시 건너뜀)
                        if wch == 2 and 0 <= cx + 1 < W and \
                                (cx + 1 - p["x"]) < p["w"]:
                            cells[gy][cx + 1] = ("", st)
                        cx += wch
            # 활성 패널 커서
            if cursor and p["id"] == active:
                ccx, ccy = cursor
                gx, gy = p["x"] + ccx, p["y"] + ccy
                if 0 <= gx < W and 0 <= gy < H:
                    ch, st = cells[gy][gx]
                    cells[gy][gx] = (ch, _with_reverse(st))   # C3: 캐시된 반전
                    # 하드웨어(터미널) 커서를 둘 좌표 — set_frame 직전에 적용한다.
                    # 와이드 글자 위면 ccx 가 이미 시작 칸이므로 gx 가 맞다.
                    self._active_cursor_xy = (gx, gy)
        # 패널 테두리 박스: 비활성=회색, 활성=파란색. 경계 셀은 인접 패널이
        # 공유하므로, 비활성 박스를 먼저 그리고 활성 박스를 마지막에 덮어
        # 활성 패널의 경계 전체가 파란색이 되도록 한다.
        # P4: 이 Style 들은 (테마, 원격뷰, degraded) 시그니처에만 의존하고 프레임
        # 내내 불변이므로, 시그니처가 바뀔 때만 재생성해 self 에 캐시한다(예전엔
        # 매 _composite 마다 ~5개 Style 신규 할당). 우선순위 degraded>원격>기본.
        # §1.7-a 원격 탭(remote-attach 병합 탭)을 보면 분홍, §10 degraded 면 error(빨강).
        _box_sig = (getattr(self.app, "theme", ""),
                    self._viewing_remote(), self._net_degraded)
        if getattr(self, "_box_style_sig", None) != _box_sig:
            if self._net_degraded:
                err = theme_color(self, "error")
                ib = Style(color=err)
                ab = Style(color=err, bold=True)
            elif self._viewing_remote():
                ib = Style(color=REMOTE_PINK_DIM)
                ab = Style(color=REMOTE_PINK, bold=True)
            else:
                ib = Style(color="grey42")
                ab = Style(color=theme_color(self, "primary"), bold=True)
            # 패널 선택 깜빡임(ESC 모드 방향키): warning 색 테두리로 active 와 교차 점멸.
            fb = Style(color=theme_color(self, "warning"), bold=True)
            self._box_styles = (ib, ab, fb)
            self._box_style_sig = _box_sig
        inactive_box, active_box, flash_box = self._box_styles
        show_title = self.layout.get("border_status")
        # 박스 문자 ↔ 변 비트(U=8,D=4,L=2,R=1): 겹치는 경계를 합쳐 ┬┴├┤┼ 로 연결.
        # C3: 상수 dict 를 매 _composite 마다 새로 만들지 않고 모듈 상수를 재사용.
        bbits = _BOX_BITS
        brev = _BOX_REV

        def _draw_box(p):
            box = p.get("box")
            if not box:
                return
            bx, by, bw, bh = box
            if self._pane_flash_on and p["id"] == self._pane_flash_id:
                st = flash_box
            elif self._cmd_target_pane is not None \
                    and p["id"] == self._cmd_target_pane:
                st = flash_box   # 명령 대상 패널 — 밝게(요청)
            elif p["id"] == active:
                st = active_box
            else:
                st = inactive_box
            x2, y2 = bx + bw - 1, by + bh - 1

            def put(gx, gy, chc):
                if not (0 <= gx < W and 0 <= gy < H):
                    return
                cur = cells[gy][gx][0]
                if cur in bbits and chc in bbits:   # 경계끼리 만나면 변을 합침
                    chc = brev[bbits[cur] | bbits[chc]]
                cells[gy][gx] = (chc, st)

            for gx in range(bx + 1, x2):      # 모서리 제외(상/하)
                put(gx, by, "─")
                put(gx, y2, "─")
            for gy in range(by + 1, y2):      # 모서리 제외(좌/우)
                put(bx, gy, "│")
                put(x2, gy, "│")
            put(bx, by, "┌")                  # 모서리는 인접 박스와만 병합
            put(x2, by, "┐")
            put(bx, y2, "└")
            put(x2, y2, "┘")

        def _draw_title(p):
            """패널 이름을 위쪽 테두리 중앙에 표기(리네임됐거나 border-status).

            테두리를 모두 그린 뒤 별도 패스로 호출해, 인접 패널의 경계선이
            이름을 덮어쓰지 않게 한다. 색은 박스 색(활성=파랑/비활성=회색)."""
            box = p.get("box")
            if not box:
                return
            bx, by, bw, _bh = box
            title = (p.get("title") or "").strip()
            renamed = title and title != "shell"
            if not ((show_title or renamed) and title and bw >= 4):
                return
            st = active_box if p["id"] == active else inactive_box
            label = f" {title} "[: bw - 2]
            start = bx + max(1, (bw - len(label)) // 2)  # 중앙 정렬
            for i, chc in enumerate(label):
                gx = start + i
                if bx < gx < bx + bw - 1 and 0 <= by < H:  # 모서리 침범 방지
                    cells[by][gx] = (chc, st)

        boxes = self.layout.get("panes", [])
        for p in boxes:               # 1) 비활성 테두리 → 2) 활성 테두리(위에)
            if p["id"] != active:
                _draw_box(p)
        for p in boxes:
            if p["id"] == active:
                _draw_box(p)
        for p in boxes:               # 3) 이름은 테두리 위에(활성 이름 최상위)
            if p["id"] != active:
                _draw_title(p)
        for p in boxes:
            if p["id"] == active:
                _draw_title(p)
        # 활성 탭을 아래 콘텐츠와 연결(노트북 탭 모양, #23): 상단 탭바가 보이면
        # 콘텐츠 최상단 테두리(row 0)의 활성 탭 x 범위를 탭의 파란색으로 이어
        # 그린다. **셀 전체 배경(공백+bg)** 으로 칠하면 본문 상단 테두리(─, 셀
        # 중앙선) 자리를 셀 높이 전체로 덮어 아웃라인을 침범한다(사용자 보고).
        # 위 절반 블록 ▀(fg=primary, bg=터미널 기본)로 그려 **윗절반(탭 쪽)만**
        # 파랗게 채우고 셀 중앙(=─ 테두리 높이)에서 정확히 멈춘다 → 아웃라인
        # 침범 없이 탭→아웃라인까지만 연결된다. (일부 모바일 폰트가 ▀ 를 칸
        # 사이 벌어지게 그릴 수 있으나 데스크탑 정확도를 우선 — 사용자 요청.)
        if self._tabbar_visible() and H > 0:
            xr = self.tabbar.active_tab_xrange()
            if xr:
                tx0, tx1 = xr
                # §1.7-a: 원격 탭이 활성이면 연결부도 탭과 같은 분홍.
                conn_color = (REMOTE_PINK if self._viewing_remote()
                              else theme_color(self, "primary"))
                conn = Style(color=conn_color, bgcolor=None)
                for xx in range(max(0, tx0), min(tx1, W)):
                    cells[0][xx] = ("▀", conn)
        # 패널 제목 경계선(pane-border-status)
        for tb in self.layout.get("titlebars", []):
            is_active = tb.get("active")
            st = _TB_ACTIVE_STYLE if is_active else _TB_INACTIVE_STYLE  # C3
            label = f" {tb['title']} "
            gy = tb["y"]
            if not (0 <= gy < H):
                continue
            for i in range(tb["w"]):
                gx = tb["x"] + i
                chh = label[i] if i < len(label) else "─"
                s = st if i < len(label) else _TB_BORDER_STYLE   # C3
                if 0 <= gx < W:
                    cells[gy][gx] = (chh, s)
        # copy-mode 선택 영역 하이라이트(추출과 동일하게 시작 패널 가로 범위로
        # 중간 줄을 한정 — 분할 경계 넘어 강조/복사되던 오염 방지, §2.4)
        sel = self.view._sel
        if sel:
            sx0, sy0, sx1, sy1 = sel
            if (sy0, sx0) > (sy1, sx1):
                sx0, sy0, sx1, sy1 = sx1, sy1, sx0, sy0
            srect = self.view._sel_rect
            if srect:
                left, right = srect[0], srect[0] + srect[2] - 1
            else:
                left, right = 0, W - 1
            for yy in range(max(0, sy0), min(H, sy1 + 1)):
                a = sx0 if yy == sy0 else left
                b = sx1 if yy == sy1 else right
                for xx in range(max(0, a), min(W, b + 1)):
                    c, sstl = cells[yy][xx]
                    cells[yy][xx] = (c, _with_reverse(sstl))   # C3: 캐시된 반전
        # display-panes 오버레이: 각 패널 중앙에 번호 표시
        if self.mode == "display":
            for i, p in enumerate(self.layout.get("panes", [])):
                label = str(i)
                cx0 = p["x"] + max(0, (p["w"] - len(label)) // 2)
                cy0 = p["y"] + p["h"] // 2
                st = Style(color="black", bold=True,
                           bgcolor="green" if p["id"] == active else "yellow")
                for j, chh in enumerate(label):
                    clientrender.put_cell(cells, cx0 + j, cy0, chh, st, W, H)
        # Claude Code 콘텐츠-레이어 장식(footer 클릭존 스캔)·플러그인 오버레이는
        # client_render 훅이 그린다(없으면 no-op — delete-to-disable).
        self.plugins.client_render(self, cells, W, H)
        # 현재 탭 닫기 [x]: 활성 패널 상단 테두리 행 우측(2026-06-13 한 칸 위로)
        self._draw_tab_close(cells, W, H)
        # 패널 오버레이(시계/달력 등, 패널 전체 덮기·뒤 화면 dim) — clock·calendar
        # 플러그인이 client_overlay 훅으로 그린다(플러그인 없으면 no-op).
        self.plugins.client_overlay(self, cells, W, H, active)
        # 경계선(divider) 호버/드래그 강조: 그 칸의 글자는 두고 배경만 살짝
        # 입혀 리사이즈 가능함을 알린다(#27).
        hov = self.view._hover_divider
        if hov is None and self.view._dragging:
            d = self.view._dragging
            hov = (d["x"], d["y"], d["w"], d["h"])
        if hov:
            hx, hy, hw, hh = hov
            tint = Style(bgcolor=theme_color(self, "primary"))
            for yy in range(hy, min(hy + hh, H)):
                for xx in range(hx, min(hx + hw, W)):
                    if 0 <= yy < H and 0 <= xx < W:
                        c, st = cells[yy][xx]
                        cells[yy][xx] = (c, st + tint)
        # Claude footer(권한모드/원격제어) 클릭존 강조: ESC 모드에서 "auto mode
        # on"(perm) 키보드 포커스 시에만 그 줄 배경을 한 톤 입힌다(글자색 유지).
        # **마우스 호버로는 배경을 바꾸지 않는다**(요청 — 호버 강조 폐지). 클릭존은
        # 위에서 막 재계산됐으므로 대상이 아직 유효할 때만 칠한다(떨림 없음).
        _perm_zone = getattr(self, "_perm_zone", {})
        _remote_zone = getattr(self, "_remote_zone", {})
        _fh = None
        if self._status_focus == "perm":
            _act = self.layout.get("active")
            if _act is not None and _act in _perm_zone:
                _fh = (_act, "perm")
        if _fh is not None:
            _fpid, _fkind = _fh
            _fzone = (_perm_zone if _fkind == "perm"
                      else _remote_zone).get(_fpid)
            if _fzone:
                zx0, zx1, zy = _fzone
                ftint = Style(bgcolor=theme_color(self, "secondary"))
                if 0 <= zy < H:
                    for xx in range(max(0, zx0), min(zx1, W)):
                        c, st = cells[zy][xx]
                        cells[zy][xx] = (c, st + ftint)
        # 컨텍스트 메뉴가 열려 있으면 대상 패널 외 나머지를 흐리게(#18) — 중앙
        # 모달이라 위치로 패널을 가리킬 수 없어 배경 dim 으로 대상을 구분한다.
        if self._menu_open and self._menu_pane is not None:
            for p in self.layout.get("panes", []):
                if p["id"] == self._menu_pane:
                    continue
                for yy in range(p["y"], min(p["y"] + p["h"], H)):
                    for xx in range(p["x"], min(p["x"] + p["w"], W)):
                        c, st = cells[yy][xx]
                        cells[yy][xx] = (c, _darken_style(st))
        # 패널 pick-up(헤더 드래그) 중: 들고 있는 소스 패널은 흐리게(dim), 놓을
        # 대상 패널은 배경 강조(놓으면 두 패널이 자리를 맞바꾼다). 탭바 위로 끌면
        # _pickup_over 가 None 이라 소스만 dim — "들고 있음"을 표시(탭/[+] 드롭 후보).
        if self.view._pickup is not None:
            stint = Style(bgcolor=theme_color(self, "warning"))
            for p in self.layout.get("panes", []):
                if p["id"] == self.view._pickup:
                    darken = True       # 들고 있는 소스 패널: 실색 블렌드로 흐리게
                elif p["id"] == self.view._pickup_over:
                    darken = False      # 놓을 대상 패널: 배경 강조(warning)
                else:
                    continue
                for yy in range(p["y"], min(p["y"] + p["h"], H)):
                    for xx in range(p["x"], min(p["x"] + p["w"], W)):
                        if 0 <= yy < H and 0 <= xx < W:
                            c, st = cells[yy][xx]
                            cells[yy][xx] = (c, _darken_style(st) if darken
                                             else st + stint)
        # 탭→패널 드래그 미리보기(#19): 드롭 대상 패널에서 새 패널이 들어갈 절반
        # (lr→오른쪽, tb→아래쪽)을 강조색으로 칠해 분할 결과를 미리 보여준다.
        if self._drag_split is not None:
            pane_id, orient = self._drag_split
            tp = next((p for p in self.layout.get("panes", [])
                       if p["id"] == pane_id), None)
            if tp:
                px, py, pw, ph = tp["x"], tp["y"], tp["w"], tp["h"]
                if orient == "lr":
                    hx0, hy0, hx1, hy1 = px + pw // 2, py, px + pw, py + ph
                else:
                    hx0, hy0, hx1, hy1 = px, py + ph // 2, px + pw, py + ph
                hl = Style(bgcolor=theme_color(self, "accent"))
                for yy in range(max(0, hy0), min(hy1, H)):
                    for xx in range(max(0, hx0), min(hx1, W)):
                        c, st = cells[yy][xx]
                        cells[yy][xx] = (c, st + hl)
        # 팝업(모달)이 떠 있으면 뒤 본문을 어둡게 칠하고, 스타일을 무시하고 컬러로
        # 그려지는 이모지는 placeholder(·)로 치환한다(#25). 팝업을 닫으면 다음
        # _composite 가 원본에서 다시 그려 자연히 복원된다(별도 저장 불필요).
        # 단, 최상위 모달이 `_no_backdrop_dim`(예: 컴포즈 작성창)이면 뒤 패널을
        # 어둡게 하지 않는다 — 사용자가 이전 출력을 보면서 입력해야 하기 때문.
        _top = self.screen_stack[-1] if len(self.screen_stack) > 1 else None
        if _top is not None and not getattr(_top, "_no_backdrop_dim", False):
            for yy in range(H):
                if yy in self._undim_rows:   # 클릭 원천 줄은 밝게 유지(#29)
                    continue
                row = cells[yy]
                for xx in range(W):
                    ch, st = row[xx]
                    if ch and _is_emoji(ch):
                        ch = "·"
                    row[xx] = (ch, _darken_style(st))
        # IME preedit 동기화(docs/internal/IME_PREEDIT_CURSOR_SCENARIO.md): 하드웨어(터미널)
        # 커서를 활성 패널 커서 셀로 옮긴다. 호스트 터미널은 IME 조합 문자열
        # (preedit)을 하드웨어 커서 자리에 덧그리므로, 안 옮기면 stale 커서 자리
        # (흔히 패널 테두리 행)에 조합 글자가 박제돼 잔상으로 보인다. Textual 의
        # Input/TextArea 가 app.cursor_position = cursor_screen_offset 로 하는 것과
        # 동일 패턴 — Textual 은 매 프레임 끝에 move_to(cursor_position) 를 출력한다
        # (textual.app._display). 모달(Input/TextArea)이 떠 있으면(screen_stack>1)
        # 그 위젯이 cursor_position 을 소유하므로 덮어쓰지 않는다(경합 방지).
        if len(self.screen_stack) == 1 and self._active_cursor_xy is not None:
            self.cursor_position = Offset(*self._active_cursor_xy)
        self.view.set_frame(cells)

    def push_screen(self, *args, **kwargs):
        # 팝업이 열리면 곧장 뒤 본문을 어둡게(#25). §10-A #4: 예전엔 call_after_refresh
        # 로만 재합성을 예약해, idle 상태에선 dim 이 다음 refresh(최악엔 1초 clock
        # tick)까지 늦게 적용돼 "팝업 디밍이 ~1초 걸린다"는 보고가 있었다. 이제
        # **같은 턴에 _composite() 를 즉시 호출**해 dim 을 바로 적용하고(set_frame 이
        # view.refresh() 호출 → 다음 프레임에 표시), 마운트 후 레이아웃 안정화를 위해
        # call_after_refresh 도 한 번 더 둔다(둘 다 캐시된 _darken_style 로 경량).
        r = super().push_screen(*args, **kwargs)
        if getattr(self, "view", None) is not None:
            self._composite()
            self.call_after_refresh(self._composite)
        return r

    def pop_screen(self, *args, **kwargs):
        # 팝업을 닫으면 어둡게/치환을 풀고 원본으로 재합성(#25) — 즉시 + 마운트 후.
        # stale/중복 dismiss 가드: 기본 화면만 남았는데 또 pop 하면 Textual 이
        # ScreenStackError 로 클라 전체를 크래시시킨다. 예: 팝업(InfoScreen/Plugin/
        # 토큰)이 이미 닫힌 뒤에도 큐에 남아 있던 백드롭/우클릭 Click 이 그 화면의
        # on_click 을 늦게 발화 → dismiss(None) → pop. 모달 팝업이 공유하는 단일
        # 길목이라 여기서 한 번만 no-op 으로 막는다(restart-check 우클릭 크래시 재현).
        # ★반환값은 None 이 아니라 빈 AwaitComplete: Screen.dismiss 가 pop_screen()
        # 반환값에 set_pre_await_callback 을 곧장 호출하므로 None 을 주면 그 자리에서
        # AttributeError 로 다시 크래시한다(즉, 막으려던 stale-dismiss 경로). 빈
        # AwaitComplete 는 즉시 완료되는 awaitable 이라 안전한 no-op 이다.
        if len(self.screen_stack) <= 1:
            return AwaitComplete()
        r = super().pop_screen(*args, **kwargs)
        if getattr(self, "view", None) is not None:
            self._composite()
            self.call_after_refresh(self._composite)
        return r

    def _draw_tab_close(self, cells, W, H):
        """현재 탭(윈도우) 닫기 [x] 버튼을 **활성 패널의 상단 테두리 행** 우측
        (모서리 바로 안쪽)에 그린다(2026-06-13 요청 — 콘텐츠 첫 행에서 한 칸 위로:
        콘텐츠를 안 가리고 IME 배지([한]/[EN], 첫 행 우상단)와도 안 겹친다).
        테두리가 없으면(단일 패널 single-border off 등) 종전대로 콘텐츠 첫 행.
        활성 패널의 실제 box/x/y/w/h 를 써서 분할(split) 상태에서도 그 패널에
        붙는다. 클릭 우선순위: on_mouse_down 이 [x] 존을 헤더 드래그(pick-up)보다
        먼저 검사하므로 테두리 행에 있어도 클릭이 드래그로 새지 않는다."""
        self._tab_close_zone = None
        active = self.layout.get("active")
        ap = next((p for p in self.layout.get("panes", [])
                   if p["id"] == active), None)
        if ap is None:
            return
        px, py, pw, ph = ap["x"], ap["y"], ap["w"], ap["h"]
        if pw < 4 or ph < 1:
            return
        # ESC 모드에서 닫기 [x] 가 포커스되면 강조색(accent)으로(#31 방향키 동선).
        if self._close_focus:
            st = Style(color="black", bgcolor=theme_color(self, "accent"),
                       bold=True)
        else:
            st = Style(color="white", bgcolor=theme_color(self, "error"),
                       bold=True)
        bbox = ap.get("box")    # 테두리 박스(있으면 상단 테두리 행이 한 칸 위)
        by = bbox[1] if bbox else (py - 1 if py >= 1 else py)
        bx0 = px + pw - 3       # 콘텐츠 우측 끝 3칸("[x]") — 우측 테두리 안쪽
        if not (0 <= by < H):
            return
        for j, chh in enumerate("[x]"):
            gx = bx0 + j
            if 0 <= gx < W:
                cells[by][gx] = (chh, st)
        self._tab_close_zone = (bx0, bx0 + 3, by)

    # ---- 송신 헬퍼 ----
