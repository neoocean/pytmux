"""화면을 그리는 Textual 클라이언트."""
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

from . import cellwidth, clientclip, clientrender, i18n, ipc, plugins, proc, version
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
    _normalize_key, _shell_argv, key_to_bytes, make_style, strip_box_drawing,
    theme_color)
from .clientscreens import (  # noqa: F401  (클로저에서 push_screen 으로 사용)
    ChooseBufferScreen, ChooseLayoutScreen, ChooseTreeScreen,
    CommandListScreen, CommandOptionsScreen, ComposePromptScreen, ConfirmScreen,
    InfoScreen, InfoTabsScreen, MenuScreen, MergeRemoteTabScreen,
    PluginManagerScreen, PromptScreen, SettingsScreen, TabSwitcherScreen)
from .clientwidgets import (  # noqa: F401  (PytmuxApp.compose·ghost suggester)
    MultiplexerView, SepInsensitiveSuggester, StatusBar, TabBar,
    _visual_tab_order)
from .keymap import (_key_to_ctrl_bytes, _tmux_key_to_textual,
                     config_path_for_write, load_config, normalize_binding_key,
                     set_config_option, textual_key_to_tmux)
from .protocol import MIN_H, MIN_W, PROTO_VERSION, read_msg, write_msg
from .clientconn import (  # noqa: F401  (PytmuxApp 믹스인 — 4-1 파일 분할)
    _NetReconnectMixin, _RestartVersionMixin)
from .clientcmd import _CommandMixin  # noqa: F401
from .clientio import _InputMixin, _RenderMixin  # noqa: F401


# ── §5.4: PytmuxApp 책임별 믹스인(모듈 레벨) ─────────────────────────────────────
# 종전 build_client_app 팩토리 안 한 덩어리(거대 클래스)였던 PytmuxApp 의 응집된
# 메서드 군을 모듈 레벨 믹스인으로 분리한다. 믹스인은 client.py 의 모듈 전역(textual/
# rich 심볼·clientutil 헬퍼·ipc/i18n/plugins 등)을 그대로 공유하므로 이름 해석이
# 깨지지 않는다(팩토리 지역인 config/session_name 은 __init__ 에서만 쓰여 분리 대상
# 메서드와 무관). PytmuxApp 은 이 믹스인들을 상속하고, 렌더/입력 코어(_composite·
# on_key·_run_command 등 강결합 메서드)는 클래스 본문에 남긴다. self 경유 호출은 MRO
# 로 그대로 동작한다 — 거동 불변, 위치만 분리.
class _ClipboardMixin:
    """OS 클립보드/페이스트 버퍼 연동(copy/paste·이미지 경로 폴백·버퍼 선택)."""

    def copy_text(self, text):
        # 서버 페이스트 버퍼 + OS 클립보드 양쪽에 저장
        self.send_cmd("set_buffer", text=text)
        clip = clientclip.copy(text)
        self.display_message(
            i18n.t("msg.copied_chars", n=len(text))
            + (i18n.t("msg.clipboard_suffix") if clip else ""))

    def paste_os_clipboard(self):
        """OS 클립보드 내용을 활성 패널에 붙여넣는다(명령 `paste-clipboard`, Ctrl+V).

        - **텍스트**: 직접 읽어 bracketed 패스스루로 패널에 주입한다(우선).
        - **이미지**(§10-A #11): 클라이언트는 PTY 너머로 비트맵을 옮길 수 없으므로,
          클립보드 이미지를 **임시 PNG 파일로 저장**하고 그 **파일 경로 문자열**을
          패널에 붙여넣는다(결정 ① — Claude Code CLI 등은 경로를 첨부 이미지로 인식).
          파일은 클라이언트 머신에 생기므로 클라이언트=서버(로컬)일 때 유효하다.
          저장에 실패하면(도구 부재/원격 등) 내부 앱이 **공유 OS 클립보드**에서 직접
          읽도록 Alt+V(=ESC v) 키스트로크로 폴백한다."""
        # 클립보드 읽기/이미지 저장은 PowerShell 등 외부 도구라 수백 ms~수초
        # 걸린다. 이벤트 루프에서 동기로 돌리면 화면이 멈춘 듯 보이고, 그동안 친
        # 키가 붙여넣기 끝난 뒤 패널로 새어 들어간다(요청). 그래서 ① 즉시 안내
        # 메시지를 띄우고 ② 실제 IO 는 워커(thread)로 돌려 루프를 안 막으며 ③
        # 끝날 때까지 ESC 외 키를 무시한다(on_key 의 _pasting 가드). 진행 중 재호출 무시.
        if self._pasting:
            return
        self._pasting = True
        self.display_message(i18n.t("msg.paste_in_progress"))
        self.run_worker(self._do_paste_clipboard(), exclusive=False)

    def _active_compose_screen(self):
        """열려 있는 작성창(ComposePromptScreen)을 돌려준다(스택 어디든, 위에서부터).
        없으면 None. 붙여넣기(텍스트/이미지 경로)를 활성 패널이 아니라 **작성창으로**
        넣을지 판단하는 데 쓴다 — 작성창이 떠 있을 땐(esc→Insert) 프롬프트가 아니라
        작성 버퍼에 넣어야 붙여넣은 요소가 작성창에 그대로 유지된다(사용자 요청)."""
        from .clientscreens import ComposePromptScreen
        for scr in reversed(self.screen_stack):
            if isinstance(scr, ComposePromptScreen):
                return scr
        return None

    def _paste_image_path(self, path, compose, active):
        """이미지 파일 경로를 붙여넣는다.

        작성창이 열려 있으면 커서 위치에 **경로 텍스트**로 넣는다(pytmux 는 작성창
        TextArea 에 썸네일을 못 그리므로 이미지=경로 텍스트로 표현 — 사용자 확인).
        적용(Ctrl+S) 시 그 경로가 통째 붙여넣어져 Claude 가 다시 첨부 이미지로 인식한다.

        작성창이 없으면 활성 패널에 붙여넣고, **현재 프롬프트 추정(_prompt_buf)에도
        경로를 기록**한다. Claude 는 경로를 `[Image #N]` 첨부로 바꿔 화면에서 원본
        경로를 되돌릴 수 없으므로(사용자 확인), 클라가 붙여넣은 경로를 스스로 추적해야
        이후 esc→Insert 작성창 시드에 이미지가 '딸려온다'(붙여넣기는 on_paste/on_key
        패스스루를 안 거쳐 추적에서 누락되던 지점)."""
        if compose is not None:
            compose.paste_text(path)
            return
        self.send_cmd("paste", text=path)
        self._compose_track_input(active, path.encode("utf-8", "replace"))

    async def _do_paste_clipboard(self):
        try:
            # 작성창(esc→Insert)이 떠 있으면 프롬프트가 아니라 작성 버퍼로 라우팅한다
            # (요청: 팝업이 열린 상태에서 esc→:→paste-clipboard 로 이미지/텍스트를
            #  팝업에 넣고, 팝업으로 돌아가도 그대로 유지). active 는 작성창이 없을 때만 쓴다.
            compose = self._active_compose_screen()
            active = self.layout.get("active")
            txt = await asyncio.to_thread(clientclip.paste)
            if txt:
                # §2.13: OS 네이티브 선택은 복사 시점에 못 막으므로, 다시 들어오는
                # 이 명시적 클립보드 경로에서 패널 테두리(박스드로잉) 오염을 제거한다.
                # 터미널 bracketed paste(on_paste)는 의도적 표 붙여넣기 보존 위해 제외.
                if getattr(self, "strip_box_drawing", True):
                    txt = strip_box_drawing(txt)
                if compose is not None:
                    compose.paste_text(txt)   # 작성창 커서에 삽입(요청)
                else:
                    self.send_cmd("paste", text=txt)
                return
            if await asyncio.to_thread(clientclip.has_image):
                path = await asyncio.to_thread(clientclip.save_image)
                if path:
                    rhost = self._active_remote_host()
                    if rhost:
                        # 원격 탭: scp 로 원격 /tmp/ 에 복사 후 원격 경로를 붙여넣는다.
                        remote_path = "/tmp/" + os.path.basename(path)
                        ok = await asyncio.to_thread(
                            clientclip.scp_to_remote, rhost, path, remote_path)
                        if ok:
                            self._paste_image_path(remote_path, compose, active)
                            self.display_message(
                                i18n.t("msg.paste_image_remote", path=remote_path))
                        else:
                            # SCP 실패 시 로컬 경로 폴백 + 경고
                            self._paste_image_path(path, compose, active)
                            self.display_message(
                                i18n.t("msg.paste_image_remote_fail", path=path))
                    else:
                        # 로컬 탭: 경로를 붙여넣어 앱이 첨부 이미지로 인식하게 한다(결정 ①).
                        self._paste_image_path(path, compose, active)
                        self.display_message(
                            i18n.t("msg.paste_image_path", path=path))
                    return
                # 폴백: 내부 앱이 공유 클립보드에서 직접 읽도록 Alt+V. 단 작성창엔
                # 경로 없인 이미지를 넣을 수 없으니(Alt+V 는 앱 전용) 안내만 한다.
                if compose is not None:
                    self.display_message(i18n.t("msg.paste_image_compose_fail"))
                else:
                    self.send_input(b"\x1bv")   # ESC v = Alt+V
                    self.display_message(i18n.t("msg.paste_image_app"))
                return
            self.display_message(i18n.t("msg.clipboard_empty"))
        finally:
            self._pasting = False

    def choose_buffer(self):
        self._want_buffers = True
        self.send_cmd("request_buffers")

    def _open_choose_buffer(self, items):
        def handle(idx):
            if idx is not None:
                self.send_cmd("paste_buffer", index=idx)
        self.push_screen(ChooseBufferScreen(items), handle)


class _StatusFocusMixin:
    """ESC 모드의 하단 상태바 버튼 포커스 동선(←→ 순환·Enter 실행·복귀). 상태바에
    실제 그려진 버튼만 대상으로 편입한다(model/usage/rec/host/clock/date/perm)."""

    def _status_buttons(self):
        """ESC 모드 하단 포커스에서 ←→ 로 순환할 대상 키 목록(현재 화면에 있는
        것만, 왼→오 시각 순서). 모델·토큰사용량은 Claude 활성 시, REC 는 캡처
        중일 때만. host(ssh:서버명)·clock(시계)·date(달력)는 우측 상태에 그려질
        때, perm("auto mode on" footer)은 활성 Claude 패널에 권한모드 footer 가
        보일 때만 편입한다(요청)."""
        sb = self.status
        btns = []
        # 수동 닫기 메시지(remote-attach 핸드셰이크 실패 등)가 떠 있으면 줄 전체를
        # 덮으므로, 그때는 메시지 닫기('msg')만 포커스 대상으로 둔다(요청).
        if sb.message is not None and getattr(self, "_msg_dismissable", False):
            return ["msg"]
        if getattr(sb, "_model_zone", None):
            btns.append("model")
        if getattr(sb, "_usage_zone", None):
            btns.append("usage")
        if getattr(sb, "_rec_zone", None):
            btns.append("rec")
        if getattr(sb, "_host_zone", None):
            btns.append("host")
        if getattr(sb, "_clock_zone", None):
            btns.append("clock")
        if getattr(sb, "_date_zone", None):
            btns.append("date")
        # 활성 패널의 권한모드 footer("auto mode on")도 같은 동선에 편입(요청).
        # _perm_zone 은 claude-code 플러그인이 채운다(없으면 빈 dict → perm 미편입).
        act = self.layout.get("active")
        if act is not None and act in getattr(self, "_perm_zone", {}):
            btns.append("perm")
        return btns

    def _enter_status_focus(self):
        """최하단 패널에서 ↓ → 상태바 버튼 포커스 진입(요청). 버튼이 없으면 무동작."""
        btns = self._status_buttons()
        if not btns:
            return False
        self._set_status_focus(btns[0])
        return True

    def _set_status_focus(self, key):
        """하단 포커스 대상을 key 로 바꾸고 다시 그린다. 상태바 버튼은
        focus_btn 으로 강조하고, perm("auto mode on" footer)은 패널 footer 줄에
        그려지므로 _composite 로 강조한다(둘 다 갱신)."""
        self._status_focus = key
        self.status.focus_btn = key
        self.status.refresh()
        self._composite()   # perm footer 포커스 강조(요청)

    def _exit_status_focus(self):
        self._status_focus = None
        self.status.focus_btn = None
        self.status.refresh()
        self._composite()

    def _handle_status_focus(self, event):
        """상태바 버튼 포커스 동선: ←→ 버튼 순환, Enter 실행, ↑/Esc/그 외 복귀."""
        k = event.key
        btns = self._status_buttons()
        if not btns:               # 버튼이 사라짐 → 포커스 해제
            self._exit_status_focus()
            return
        cur = self._status_focus if self._status_focus in btns else btns[0]
        if k in ("left", "right"):
            i = (btns.index(cur) + (1 if k == "right" else -1)) % len(btns)
            self._set_status_focus(btns[i])
        elif k == "enter":
            act = self.layout.get("active")
            self._exit_status_focus()
            self._exit_esc()
            if cur == "msg":
                self._dismiss_message()   # 메시지 위에서 Enter → 즉시 닫기(요청)
            elif cur == "model":
                # 모델 팝업은 claude-code 플러그인이 설치(없으면 no-op).
                fn = getattr(self, "open_model_config", None)
                fn and fn()
            elif cur == "usage":
                # 마우스 클릭(clientwidgets _usage_zone)과 동일하게 시간(hour) 뷰로 연다
                # — "N%/5h used" 세그먼트라 5h% 막대가 핵심(사용자 요청 2026-06-18).
                fn = getattr(self, "open_token_log", None)  # 플러그인 설치
                fn and fn("hour")
            elif cur == "rec":
                fn = getattr(self, "show_capture_info", None)  # rec 플러그인 설치
                fn and fn(getattr(self.status, "capture_path", None),
                          getattr(self.status, "capture_size", 0))
            elif cur == "host":
                self.show_status_tabs(initial=2)   # 서버 탭(#12, host 클릭과 동일)
            elif cur == "clock":
                fn = getattr(self, "toggle_clock", None)  # clock 플러그인 설치
                fn and fn(act)                     # 시계 오버레이 토글
            elif cur == "date":
                fn = getattr(self, "toggle_calendar", None)  # calendar 플러그인 설치
                fn and fn(act)                     # 달력 오버레이 토글
            elif cur == "perm":
                fn = getattr(self, "open_perm_mode", None)  # 플러그인 설치
                fn and fn(act)                     # 권한모드 선택 팝업
        elif k in ("up", "escape"):
            self._exit_status_focus()   # 패널로 복귀(esc 모드 유지)
            if k == "escape":
                self._exit_esc()
        else:
            self._exit_status_focus()


class _ChooseScreensMixin:
    """선택 팝업 열기 묶음 — 탭/패널 트리(전환·종료), 통합 상태 탭(REC·서버),
    저장 레이아웃 목록. 서버에 목록을 요청하고 회신을 ChooseScreen 으로 띄운다."""

    def request_tree(self, purpose="choose"):
        self._want_tree = True
        self._tree_purpose = purpose   # "choose"(전환/종료) | "status_tabs"
        self.send_cmd("request_tree")

    def _open_status_tabs(self, tree):
        """플러그인 기여 탭(rec 의 'REC' 탭·claude-code 등) + 서버 정보를 **한 팝업의
        탭**으로 연다(#10, §10-A #12). REC 탭과 그 [c]/[o] 동작은 rec 플러그인이
        client_status_tabs 훅으로 기여한다(코어 하드코딩에서 이전, REC_SCENARIO §4.2).
        어느 버튼으로 열었는지(_status_tab_initial)에 따라 초기 탭만 다르다 — 범위 밖이면
        끝 탭으로 클램프(host 클릭 initial=2 가 서버 탭에 안착, rec 부재 시도 무탈)."""
        server = self._server_info_lines()
        initial = getattr(self, "_status_tab_initial", 0)
        # 플러그인 탭은 (제목, 줄) 또는 (제목, 줄, 동작리스트). 동작은 탭 인덱스로
        # InfoTabsScreen 에 전달한다. rec 디렉토리 삭제 시 REC 탭·동작이 통째로 빠진다.
        tabs = []
        actions = {}
        for t in self.plugins.client_status_tabs(self, tree):
            tabs.append((t[0], t[1]))
            if len(t) > 2 and t[2]:
                actions[len(tabs) - 1] = t[2]
        tabs.append(("서버", server))
        initial = max(0, min(initial, len(tabs) - 1))
        self.push_screen(InfoTabsScreen(
            tabs, initial=initial, title=i18n.t("dialog.status_title"),
            actions=actions))

    def _open_choose_tree(self, tree):
        def handle(res):
            if not res:
                return
            act, e = res
            self.send_cmd("select_window", index=e["index"])
            if e["kind"] == "pane":
                self.send_cmd("select_pane_id", id=e["pid"])
            if act == "kill":
                if e["kind"] == "win":
                    self.confirm_kill_tab()
                else:
                    self.open_prompt("confirm", "kill-pane? (y/N)",
                                     action=lambda: self.send_cmd("kill_pane"))
        self.push_screen(ChooseTreeScreen(tree), handle)

    # ---- 탭 스위처(esc → Tab) ----
    def open_tab_switcher(self):
        """열려 있는 탭 목록을 띄워 Tab/Shift+Tab 으로 고르고 Enter 로 전환한다
        (사용자 요청 2026-07-15 — Alt+Tab 동선). 첫 화면부터 **다음 탭**이 선택돼
        있어 'esc Tab Enter' 가 곧 '다음 탭으로 전환'이다.

        표시 순서·번호는 탭바와 같은 **시각 순서**(비고정→고정, _visual_tab_order)를
        쓴다 — 목록의 3번이 탭바의 3번, esc+3 과도 같은 탭이다(07-14 규약).
        탭이 하나뿐이면 고를 게 없어 열지 않는다(활성 탭 깜빡임으로 안내).
        """
        tabs = list(self.tabbar.tabs)
        if len(tabs) < 2:
            self.tabbar.blink_active()
            return
        by_index = {t["index"]: t for t in tabs}
        order = [i for i in _visual_tab_order(tabs) if i in by_index]
        entries, initial = [], 0
        for pos, idx in enumerate(order):
            t = by_index[idx]
            mark = "▸" if t.get("active") else " "
            pin = "* " if t.get("pinned") else ""     # 탭바와 같은 핀 글리프
            # 원격(⇄) 탭은 이름에 이미 ⇄host: 접두사가 있어 따로 표식하지 않는다.
            entries.append({"index": idx,
                            "label": f"{mark} {pin}{pos + 1}:{t.get('name', '')}"})
            if t.get("active"):
                initial = (pos + 1) % len(order)     # 시작 선택 = '다음 탭'
        def handle(index):
            if index is not None:
                self.send_cmd("select_window", index=index)
        self.push_screen(TabSwitcherScreen(entries, initial=initial), handle)

    # ---- 레이아웃 저장/불러오기 ----
    def save_layout_prompt(self):
        self.open_prompt("save_layout", i18n.t("screen.save_layout_prompt"))

    def request_layouts(self, mode):
        """저장된 레이아웃 목록을 요청(mode: 'over'=현재 탭 덮어쓰기, 'new'=새 탭)."""
        self._want_layouts = mode
        self.send_cmd("list_layouts")

    def _open_choose_layout(self, names, mode):
        title = (i18n.t("screen.layout_to_new") if mode == "new"
                 else i18n.t("screen.layout_to_over"))

        def handle(name):
            if name:
                self.send_cmd("load_tab_layout", name=name,
                              new=(mode == "new"))
        self.push_screen(ChooseLayoutScreen(names, title), handle)

    # ---- 원격 탭 → 현재 원격 탭에 pane 으로 머지(피커) ----
    def merge_remote_tab_picker(self):
        """같은 원격 서버(호스트)의 **다른 원격 탭**을 지금 보는 원격 탭에 pane 으로
        머지한다 — 드래그 머지(TabBar.on_mouse_up)의 키보드/명령 대체 경로(§1.7-c
        예외). 지금 보는 탭이 원격이 아니면 안내만. 같은 호스트의 다른 원격 탭을
        피커로 띄우고, 고르면 그 탭의 활성 패널을 현재 활성 패널 옆에 분할로 붙인다:
        `select_pane_id`(대상=현재 활성 패널) + `join_pane(src=전역 index, orient)`.
        서버 remote_relay_join 이 전역 src→원격 로컬 index 변환·업스트림 릴레이하고
        원격 서버가 실제 트리를 합친다(원격끼리라 로컬 트리 불변)."""
        host = self._active_remote_host()
        if host is None:
            self.display_message(i18n.t("msg.merge_remote_not_remote"))
            return
        # 같은 호스트의 다른(=활성 아닌) 원격 탭만 후보. _tab_host 로 병합 탭바 이름
        # '⇄host:name' 에서 host 를 파싱(드래그 머지 _drag_merge_ok 와 동일 기준).
        items = [{"i": t["index"], "name": t.get("name", "")}
                 for t in self.tabbar.tabs
                 if t.get("remote") and not t.get("active")
                 and self.tabbar._tab_host(t["index"]) == host]
        if not items:
            self.display_message(i18n.t("msg.merge_remote_no_peers"))
            return
        dst = self.layout.get("active")   # 현재 활성 패널 = 머지 대상 패널

        def handle(res):
            if res is None:
                return
            src, orient = res
            if dst is not None:
                self.send_cmd("select_pane_id", id=dst)
            self.send_cmd("join_pane", src=src, orient=orient)
        self.push_screen(MergeRemoteTabScreen(items), handle)


_PytmuxAppMixins = (_ClipboardMixin, _NetReconnectMixin, _RestartVersionMixin,
                    _StatusFocusMixin, _ChooseScreensMixin, _CommandMixin, _InputMixin, _RenderMixin)


def build_client_app(sock_path: str, config: dict | None = None,
                     session_name: str | None = None):
    config = config or {}
    # textual/rich 심볼은 모듈 최상위로 올렸다(§5.4 — 위 import 블록 주석 참조). 종전
    # 여기 지연 import 는 clientwidgets/clientscreens 가 이미 textual 을 끌어와 기동
    # 비용 절감 효과가 없었고, 모듈 레벨 믹스인이 이 심볼을 참조하려면 최상위라야 한다.

    class PytmuxApp(*_PytmuxAppMixins, App):
        ENABLE_COMMAND_PALETTE = False
        # Textual 기본 App 은 ctrl+q 를 priority quit 으로 바인딩한다 — 이걸 덮어
        # 종료가 아니라 활성 패널로 전달한다(앱 종료는 detach 명령으로만). #25
        BINDINGS = [Binding("ctrl+q", "ctrl_q", show=False, priority=True)]
        CSS = """
        Screen { layout: vertical; }
        #tabbar { width: 100%; height: 1; dock: top; }
        #view { width: 100%; height: 1fr; }
        #status { width: 100%; height: 1; dock: bottom; }
        """

        def __init__(self, sock_path: str):
            super().__init__()
            self.sock_path = sock_path
            self.session_name = session_name
            self.reader = None
            self.writer = None
            # 선택적 플러그인(pytmuxlib/plugins/*). 명령 검색·자동완성·디스패치·메시지
            # 처리를 기여한다(ncd 등). attach_client 로 인스턴스 글루(app.request_nc_list
            # 등)를 설치한다. 디렉토리를 지우면 해당 명령은 프롬프트에서 조용히 사라진다.
            self.plugins = plugins.load()
            self.plugins.attach_client(self)
            # version 명령 팝업용(이 클라가 로드한 코드 버전·런치 시각). 버전 캡처는
            # ~수십 ms p4 호출이라 startup(첫 페인트) 핫패스를 막지 않게 on_mount 에서
            # executor 로 비동기 계산해 채운다(그 전까진 "…"). 업타임은 런치 시각 기준.
            self._boot_time = time.time()
            self._code_version = "…"
            # 작업 보존 재시작(re-exec): 서버가 {"t":"restarting"} 을 보내면 다음
            # 연결 끊김을 종료가 아닌 재접속으로 다룬다(docs/internal/RESTART_SCENARIO.md ⓔ).
            self._reconnecting = False
            # 전체 재시작(restart-all): 서버 re-exec 시 in-place 재접속 대신 **클라
            # 자신도 relaunch**(새 클라 코드로 재attach)하려는 요청. 끊김 처리에서
            # _relaunch 를 세우고 종료하면 run_client 가 os.execv 로 새 클라를 띄운다.
            self._relaunch_on_restart = False
            self._relaunch = False
            # Windows: restart-all 을 execv 대신 제자리 재접속으로 처리했음을 재접속
            # 완료 메시지에서 한 번 안내하기 위한 1회성 플래그(clientconn 참조).
            self._win_restart_note = False
            # restart-server/restart-all 은 실행 전 드라이런(request_restart_check)을
            # 먼저 돌린다. 회신(restart_check)이 올 때 어떤 재시작을 대기 중인지
            # ("server"|"all") 기억해 두고, 안전하면 곧장 실행, FAIL 이면 재확인 팝업.
            self._pending_restart = None
            # IPC 강제 재접속(§10 degraded 회복): 정체된 소켓을 버리고 새로 세울 때
            # 옛 reader 태스크가 EOF 로 깨어나 self.exit() 하지 않게 세대 번호로 구분
            # 한다. _start_reader 가 띄우는 각 reader 태스크는 자기 (reader, gen) 을
            # 들고 돌고, EOF 시 gen != _conn_gen 이면 이미 새 연결로 교체된 것이므로
            # 조용히 종료한다. _force_reconnecting = 재접속 진행 중(중복 트리거 방지).
            self._conn_gen = 0
            self._force_reconnecting = False
            self.layout = {"panes": [], "dividers": [], "active": None,
                           "cols": 80, "rows": 24}
            self.pane_content = {}   # id -> (rows, cursor)
            self.pane_wrap = {}      # id -> set(soft-wrap 연속원 행 인덱스, 프레임 상대)
            # §1.7 페더레이션 회복: baseline(직전 full) 없이 screen-delta 만 온 패널.
            # redraw 를 1회 요청해 full 을 끌어오고, full 수신 시 비운다(중복 요청 디바운스).
            self._delta_no_base = set()
            #   copy-mode 선택 추출에서 자동 줄바꿈 줄을 한 줄로 잇는 데 쓴다(매 screen/
            #   screen-delta 메시지가 전체 리스트를 실어 보내므로 통째로 교체).
            self.mode = "normal"     # normal | prefix | scroll | prompt | display
            self._want_tree = False  # choose-tree 응답 대기
            self._tree_purpose = "choose"  # tree 응답 용도(choose|usage)
            self._want_buffers = False  # choose-buffer 응답 대기
            self._want_layouts = None  # 레이아웃 목록 응답 대기(모드: "new"/"over")
            self._want_token_log = False  # 토큰 로그 집계 팝업 응답 대기(#7)
            # 시계/달력 오버레이 상태(clock_panes/calendar_panes)와 토글
            # (toggle_clock/set_clock/toggle_calendar/set_calendar)은 clock·calendar
            # 플러그인이 attach_client(위 96줄)에서 이 인스턴스에 설치한다. 디렉토리를
            # 지우면 그 상태/메서드가 없고, 코어는 getattr·레지스트리 훅으로만 닿아 no-op.
            self._menu_pane = None  # 컨텍스트 메뉴가 열린 대상 패널 id(배경 강조용)
            self._menu_open = False  # 컨텍스트 메뉴 표시 중(배경 dim 합성용)
            self.single_border_on = True  # 단일 패널 테두리 표시(single-border on|off)
            # 서버 권위 옵션 현재값(:settings 화면 표시용). status 메시지가 채운다 —
            # coalesce_repaints·nest_auto_attach·vt_parser(전역) + auto_rename·
            # border_status·monitor_activity·monitor_bell(활성 윈도우/탭).
            self.server_opts = {}
            # Claude Code 클릭존 상태(pane_claude·_perm_zone·_remote_zone)는
            # claude-code 플러그인이 attach_client 로 이 인스턴스에 설치한다(Phase 2c).
            # 코어는 클릭 핸들러에서 getattr 로만
            # 읽으므로, 디렉토리를 지우면 클릭존이 전혀 나타나지 않는다
            # (delete-to-disable). (프롬프트 스티키 헤더는 2026-06-13 완전 제거.)
            self._close_focus = False   # ESC 모드 닫기 [x] 버튼 포커스(#31 동선)
            # ESC 모드에서 최하단 패널 ↓ → 하단 상태바 버튼 포커스(요청). 값은 현재
            # 포커스된 버튼 키(None=비활성). ←→ 로 버튼 순환, Enter 로 실행, ↑/Esc 복귀.
            self._status_focus = None
            # display-message 임시 메시지의 타이머(교체/수동 닫기 시 취소용)와
            # 수동 닫기 허용 플래그(요청: remote-attach 핸드셰이크 실패처럼 놓치면
            # 안 되는 알림은 3초 유지 + 클릭/Enter 로 즉시 닫기).
            self._msg_timer = None
            self._msg_dismissable = False
            self._tab_close_zone = None  # 현재 탭 닫기 [x] 영역 (x0, x1, y)
            self._drag_split = None      # 탭→패널 드래그 미리보기 (pane_id, orient)(#19)
            self._undim_rows = set()     # 팝업 dim 에서 제외할 콘텐츠 행(클릭 원천, #29)
            self._last_esc_ts = 0.0      # ESC 오토리핏 디바운스용 직전 도착 시각(#32)
            self._ESC_DEBOUNCE = 0.06    # 이 초 안의 연속 ESC 만 오토리핏으로 무시(#36)
            # ESC 모드 방향키로 패널을 옮기면 새로 활성화된 패널 테두리를 잠깐 깜빡여
            # (warning↔active 교차) 어느 패널이 선택됐는지 또렷이 보이게 한다(요청).
            # 클립보드 붙여넣기 진행 중 플래그(요청): 클립보드 읽기·이미지 저장이
            # 외부 도구라 느려, 끝날 때까지 ESC 외 키 입력을 무시해 스트레이 키가
            # 붙여넣기 후 패널로 새는 것을 막는다. 워커가 끝나면 해제.
            self._pasting = False
            # 패널에 적용되는 명령(rename-pane 등)을 명령 프롬프트에서 작성 중일 때,
            # 그 명령이 적용될 패널(보통 활성 패널) 테두리를 밝게 표시해 어느 패널에
            # 적용될지 보이게 한다(요청). PromptScreen 이 _set_cmd_target 로 세운다.
            self._cmd_target_pane = None
            self._flash_pending = False  # 다음 active 변경 시 깜빡일지(방향키가 세움)
            self._pane_flash_id = None   # 깜빡이는 중인 패널 id
            self._pane_flash_on = False  # 깜빡임 위상(True=warning 강조)
            self._pane_flash_left = 0    # 남은 on/off 토글 횟수
            self._pane_flash_timer = None
            # 네트워크 응답성(§10): 클라↔서버 ping/pong 왕복지연(RTT)을 주기 측정하고
            # 히스테리시스로 degraded 판정 — degraded 면 패널 외곽선을 빨강으로(회복 시
            # 원복). _net_ping_ts=미응답 ping 의 송신 monotonic 시각(None=응답됨).
            self._net_ping_ts = None
            self._net_degraded = False
            self._net_bad = 0      # 연속 느림 표본 수
            self._net_good = 0     # 연속 양호 표본 수
            self.net_rtt_threshold = config.get("net_rtt_threshold", 0.4)  # 초
            self.net_ping_interval = config.get("net_ping_interval", 0.5)  # 초
            self.net_bad_n = config.get("net_bad_n", 3)    # ON 전이 지속 표본
            self.net_good_n = config.get("net_good_n", 3)  # OFF 전이 지속 표본
            # 자동 회복(§10): degraded 가 net_recover_n 회 연속(=표시 임계보다 훨씬
            # 길게) 지속되면 IPC 를 강제 재접속한다(서버 PTY/세션은 보존). 기본 20
            # 표본 ≈ 10초(net_ping_interval 0.5초 기준). off 면 수동 `reconnect` 만.
            self.net_auto_reconnect = config.get("net_auto_reconnect", True)
            self.net_recover_n = config.get("net_recover_n", 20)
            self._net_last_rtt = None   # 마지막 측정 RTT(초) — 서버정보 팝업·진단용
            # 최근 60분 RTT 표본 이력(서버 정보 팝업의 그래프용). (monotonic_ts, rtt초)
            # 튜플을 append, _net_sample 에서 창(_RTT_WINDOW) 밖 앞쪽을 잘라낸다.
            self._net_rtt_hist = []
            # 60분 RTT 그래프를 서버/클라 재시작에도 유지한다(요청): 표본을 벽시계
            # 타임스탬프로 디스크에 영속하고 기동 때 창 안 표본만 복원한다. 서버가
            # 꺼져 있던 구간은 표본이 없어 그래프에서 자연히 공백으로 남는다(건너뜀).
            self.net_rtt_persist = config.get("net_rtt_persist", True)
            self._rtt_save_n = 0   # 마지막 저장 이후 누적 표본 수(디바운스용)
            self._load_rtt_hist()
            # 명령 인자 이력(remote-attach 호스트 등) 복원 — 프롬프트 추천·자동완성용.
            self._load_arghist()
            # 로컬(AF_UNIX·루프백 TCP) 연결이면 클라↔서버가 같은 머신이라
            # 네트워크 열화 개념이 없다 → degraded(빨강 외곽선)·자동 재접속을
            # 억제한다(§10-F Windows degraded 오탐). 진짜 원격 호스트 연결에서만
            # 히스테리시스/자동회복 유지. RTT 측정/로깅 자체는 계속한다(진단용).
            self._net_local = ipc.is_local_endpoint(self.sock_path)
            # ---- 설정(config) 적용 ----
            self.prefix_key = config.get("prefix", "ctrl+b")
            self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            self.prefix_enabled = True  # 중첩 시 F12 로 outer prefix 일시 해제
            self.bindings = config.get("bindings", {})
            # root table(`bind -n`, §2.5): prefix 없이 노멀 모드에서 바로 발동하는
            # 바인딩. 내장 크롬 키(ESC/`/F12/prefix/Ctrl+V)·오버레이 키가 우선이고,
            # 매칭되면 그 키는 패널로 전달하지 않는다.
            self.root_bindings = config.get("root_bindings", {})
            # 설정 로드 중 모은 경고(잘못된 키 표기 등) — startup 에서 한 번 표시.
            self._config_warnings = list(config.get("warnings", []))
            self.mouse_enabled = config.get("mouse", True)
            # §2.4 마우스 드래그 복사(기본 on): normal 모드 좌드래그를 pytmux 패널-클램프
            # 선택→OS 클립보드 자동복사로 잡는다(클릭은 앱에 전달). 호스트 터미널이 Shift
            # 선택을 가로채 pane 외곽선까지 긁히던 불편을 없앤다(사용자 요청 2026-07-11).
            # off 면 종전대로 좌드래그를 마우스 앱에 패스스루한다(선택은 Shift·copy-mode).
            self.mouse_drag_copy = config.get("mouse_drag_copy", True)
            # 마우스 이벤트 진단 로그(원격 SSH 휠 스크롤 미동작 등 환경 의존 문제용).
            # `set mouse-debug on` 으로 켜면 클라이언트가 받은 마우스/휠 이벤트와
            # **내비게이션 키**(↑/↓/페이지/홈/엔드 — `_KEY_DIAG` 화이트리스트)를
            # <sock>.mouse.log 로 남긴다 → 휠이 (a)Textual 까지 도달하는지, 아니면
            # (b)상위 터미널이 1007 변환으로 화살표 키로 바꿔 보내는지를 切り分け 한다
            # (문자/단축키는 입력 유출 방지로 기록하지 않음).
            self.mouse_debug = config.get("mouse_debug", False)
            self._mouse_log_path = (ipc.state_base(sock_path) if sock_path
                                    else "pytmux") + ".mouse.log"
            # 대체 스크롤 모드(DECSET 1007) 비활성 여부. 켜져 있으면 일부 터미널이
            # 휠을 화살표 키로 바꿔 보내 pytmux 스크롤백이 안 열린다 → 기본으로 끈다.
            # `set alt-scroll on` 으로 다시 켜면(=1007 비활성화 해제) 터미널 기본 동작.
            self.disable_alt_scroll = config.get("disable_alt_scroll", True)
            # 시작 시 CPR 모호폭 감지값(run 진입부가 config 에 캐시) — `:set
            # ambiguous-width auto` 가 Textual 점유 중 CPR 재프로브 없이 이 값으로 복귀.
            self._ambig_auto_wide = config.get("_ambig_auto_wide", False)
            self.mode_keys = config.get("mode_keys", "vi")
            self.status_position = config.get("status_position", "bottom")
            self.status_interval = config.get("status_interval", 15)
            self._status_timer = None
            self.set_titles = config.get("set_titles", False)
            self.title_fmt = config.get("title_fmt", "#S:#I:#W")
            # 비활성 패널 dim(§2.9, 요청): 한 탭에 패널이 둘 이상일 때 비활성 패널을
            # 활성 대비 한 톤 옅게 그려 외곽선 없이도 활성 패널을 구분한다. config 로
            # on/off·세기(0~0.8) 조정, 런타임 `inactive-dim` 명령으로 세션 토글(영속=config).
            self.inactive_dim = bool(config.get("inactive_dim", True))
            try:
                self.inactive_dim_ratio = max(0.0, min(0.8,
                    float(config.get("inactive_dim_ratio", 0.18))))
            except (TypeError, ValueError):
                self.inactive_dim_ratio = 0.18
            # §2.13 OS 네이티브 선택으로 복사된 텍스트를 paste-clipboard(Ctrl+V)로 다시
            # 넣을 때 패널 테두리(박스드로잉) 오염을 제거(기본 on). 런타임 토글(영속=config).
            self.strip_box_drawing = bool(config.get("strip_box_drawing", True))
            self.aliases = config.get("aliases", {})
            self.hooks = config.get("hooks", {})
            # 새 탭/패널 시작 디렉토리(current/home/<경로>). :settings 에서 변경 가능.
            self.default_path = config.get("default_path", "current")
            # :settings 가 config-scoped 설정을 되쓸 대상 파일(load_config 와 같은
            # 탐색 순서; 없으면 ~/.config/pytmux/config 를 생성 경로로).
            self._config_path = config_path_for_write()
            # 로케일(§6 i18n): 우선순위 = 영속된 `lang` 명령 선택 > config `lang` >
            # 환경 LANG. 클라이언트-로컬(표현 계층, per-user)이라 서버 왕복 없음.
            self.lang = (i18n.load_persisted(sock_path)
                         or i18n.resolve(config.get("lang"), os.environ))
            i18n.set_locale(self.lang)
            self._attached = False
            self._composite_pending = False  # B9: 합성 코얼레싱 예약 플래그
            self._prev_winc = 0
            self._prev_bell = False
            self._prompt_purpose = None
            self._prompt_action = None
            self.tab_bar_always = config.get("tab_bar_always", True)
            self.view = MultiplexerView()
            self.tabbar = TabBar()
            self.status = StatusBar(
                bg=config.get("status_bg"),       # None = 테마(textual-dark) 사용
                fg=config.get("status_fg"),
                left=config.get("status_left", " "),
                right=config.get("status_right",
                                 " #{pane_title}#h %H:%M %Y-%m-%d "))
            # Claude 상태 속성(claude_active/tokens/예산 등)은 claude-code 플러그인이
            # 위젯에 설치한다(client_statusbar_init). 플러그인 부재 시 no-op — 코어
            # _render_main 은 이 속성을 읽지 않아 안전(delete-to-disable).
            self.plugins.client_statusbar_init(self, self.status)

        def compose(self) -> ComposeResult:
            yield self.tabbar
            yield self.view
            yield self.status

        def _term_write(self, seq):
            """터미널에 raw 이스케이프 시퀀스를 직접 쓴다(Textual 드라이버 경유).
            드라이버가 아직 없거나(테스트 run_test) write 실패는 조용히 무시한다."""
            drv = getattr(self, "_driver", None)
            if drv is None:
                return
            try:
                drv.write(seq)
                drv.flush()
            except OSError:
                pass

        async def on_mount(self):
            self.tabbar.display = self.tab_bar_always
            self.view.focus()
            # 클라 코드 버전을 백그라운드(executor)로 계산해 채운다 — startup 핫패스
            # (첫 페인트)를 ~수십 ms p4 호출로 막지 않는다. version 명령 전에 끝난다.
            async def _load_version():
                try:
                    loop = asyncio.get_running_loop()
                    self._code_version = await loop.run_in_executor(
                        None, version.code_version)
                except (OSError, RuntimeError):
                    self._code_version = "unknown"
            asyncio.create_task(_load_version())
            if self.status_position == "top":
                self.status.styles.dock = "top"
            self._restart_status_timer()
            self.set_interval(1.0, self._clock_tick)  # clock-mode 초 단위 갱신
            self.set_interval(self.net_ping_interval, self._net_ping)  # RTT 측정(§10)
            # 대체 스크롤 모드(DECSET 1007) 끄기 — 일부 터미널(iTerm2, 일부 SSH
            # 클라이언트)은 기본적으로 alt-screen 에서 마우스 휠을 ↑/↓ 화살표 키로
            # 변환해 보낸다(§10 "원격 SSH 휠 스크롤백 미동작"). 그러면 pytmux 는
            # 진짜 휠 이벤트(on_mouse_scroll_up)를 못 받고 화살표만 활성 패널로 새어
            # 스크롤백이 안 열린다. 1007 을 끄면 터미널이 SGR(1006) 휠 이벤트를 그대로
            # 넘겨 pytmux 자체 스크롤백 처리가 동작한다. 종료 시 on_unmount 가 복원.
            if self.disable_alt_scroll:
                self._term_write("\x1b[?1007l")
            try:
                # OS 별 전송 분기(Unix=AF_UNIX, Windows=TCP 루프백)는 ipc 가 담당.
                self.reader, self.writer = await ipc.open_connection(self.sock_path)
            except (ConnectionError, FileNotFoundError, OSError):
                self.exit(message=i18n.t("msg.connect_failed"))
                return
            cols, rows = self._content_size()
            hello = {"t": "hello", "proto": PROTO_VERSION, "cols": cols, "rows": rows}
            if cellwidth.ambiguous_wide():    # 서버 pyte 격자도 모호폭=2 로 맞추도록
                hello["ambig"] = "wide"
            tok = ipc.read_token(self.sock_path)   # 연결 인증(F1)
            if tok:
                hello["token"] = tok
            if self.session_name:
                hello["session"] = self.session_name
            await write_msg(self.writer, hello)
            self._start_reader()
            # 설정 파일의 키 바인딩 경고가 있으면 startup 직후 한 번 보여준다
            # (과거엔 잘못된 키가 조용히 묻혀 사용자가 원인을 몰랐다).
            if self._config_warnings:
                msg = " / ".join(self._config_warnings[:3])
                if len(self._config_warnings) > 3:
                    msg += i18n.t("msg.config_warn_more",
                                  n=len(self._config_warnings) - 3)
                self.set_timer(0.5, lambda: self.display_message(msg, secs=5.0))

        def on_unmount(self):
            # 마운트 시 끈 대체 스크롤 모드(1007)를 복원해 터미널을 원상태로 둔다.
            if getattr(self, "disable_alt_scroll", False):
                self._term_write("\x1b[?1007h")
            # 종료 시점의 60분 RTT 이력을 확정 저장(재시작 후 그래프 유지). 디바운스
            # 미저장분(_RTT_SAVE_EVERY 미만)까지 마지막으로 디스크에 남긴다.
            self._save_rtt_hist()
            # attach_client 의 짝 — 플러그인이 띄운 인스턴스 자원(예: ime-indicator
            # 입력소스 감시 헬퍼 프로세스)을 종료 시 정리한다(없으면 no-op).
            self.plugins.client_unload(self)

        def _tabdrop_at(self, cx, cy):
            """탭을 끌어 내린 콘텐츠 좌표(cx,cy)의 패널과, 커서가 그 패널의 어느 쪽에
            있느냐로 정해지는 분할 방향을 (pane_id, orient) 로 돌려준다(#19). 좌우
            가장자리에 가까우면 'lr'(좌/우 분할), 위아래면 'tb'(상/하). 없으면 None."""
            for p in self.layout.get("panes", []):
                px, py, pw, ph = p["x"], p["y"], p["w"], p["h"]
                if px <= cx < px + pw and py <= cy < py + ph and pw > 1 and ph > 1:
                    dx = (cx - px) / pw - 0.5
                    dy = (cy - py) / ph - 0.5
                    return p["id"], ("lr" if abs(dx) >= abs(dy) else "tb")
            return None

        def _tabbar_visible(self):
            return self.tab_bar_always or len(self.status.windows) >= 2

        def set_tab_bar_always(self, flag):
            """상단 탭바 항상 표시 옵션을 런타임에 바꾼다(표시/뷰 크기 동기화)."""
            flag = bool(flag)
            if flag == self.tab_bar_always:
                return
            self.tab_bar_always = flag
            self._update_tabbar()

        def set_status_lines(self, n):
            """상태표시줄 줄 수(0~5)를 런타임에 바꾼다. 위젯 높이·뷰 크기를 동기화하고
            서버에 새 크기를 알린다(레이아웃 재계산)."""
            n = max(0, min(5, int(n)))
            if n == self.status.lines:
                return
            self.status.lines = n
            self.status.styles.height = n
            self.status.display = n > 0
            self.status.refresh()
            # 콘텐츠 영역(패널)이 줄어/늘어나므로 서버에 새 크기 통지.
            if self.writer:
                cols, rows = self._content_size()
                self.run_worker(write_msg(
                    self.writer, {"t": "resize", "cols": cols, "rows": rows}))

        def _content_size(self):
            size = self.size
            extra = 1 if self._tabbar_visible() else 0   # 상단 탭바 1줄
            status = self.status.lines                   # 상태표시줄 줄 수(0~5)
            return (max(MIN_W, size.width),
                    max(MIN_H, size.height - status - extra))

        def _active_tab_index(self):
            for t in self.status.windows:
                if t.get("active"):
                    return t["index"]
            return 0

        def _viewing_remote(self) -> bool:
            """§1.7-a: 지금 보는(활성) 탭이 remote-attach 병합 원격 탭인가 —
            탭바·외곽선 분홍 구분과 드래그 가드의 클라측 판정 기준."""
            return any(t.get("active") and t.get("remote")
                       for t in self.status.windows)

        def _active_remote_host(self):
            """활성 탭이 remote-attach 병합 원격 탭이면 그 호스트, 아니면 None.
            탭 이름은 서버 _remote_tabs 가 만든 `⇄{host}:{name}` 형식 — ⇄ 와 첫
            ':' 사이가 host(=link.host)다. 닫기[x]/esc x 를 kill_window 대신
            remote_detach 로 라우팅하는 데 쓴다(원격 탭은 로컬 셸이 아니라 서버가
            kill_window 를 §1.7-c 로 거부한다)."""
            for t in self.status.windows:
                if t.get("active") and t.get("remote"):
                    name = t.get("name", "")
                    if name.startswith("⇄"):
                        return name[1:].split(":", 1)[0]
            return None

        def _update_tabbar(self):
            """상태 갱신 시 탭바 데이터/표시 여부를 동기화. 표시가 바뀌면 뷰 크기가
            달라지므로 서버에 새 크기를 통지한다."""
            visible = self._tabbar_visible()
            new_active = self._active_tab_index()
            prev_active = next((t["index"] for t in self.tabbar.tabs
                                if t.get("active")), None)
            if prev_active != new_active:
                self._log_tab_debug(prev_active, new_active)
            # 활성 탭 이름 길이가 바뀌면 탭 폭(=연결부 x 범위)도 바뀌므로 재합성
            # 트리거에 포함한다(예전엔 활성 index 변화만 봐, 이름만 길어지면 탭은
            # 늘어나도 노트북 연결부는 옛 폭에 머물렀다 — 사용자 보고).
            prev_xr = self.tabbar.active_tab_xrange() if self.tabbar.display else None
            self.tabbar.set_tabs(self.status.windows, new_active)
            # 상단 탭바가 보이면 하단 상태줄의 탭 목록은 생략(중복 방지)
            if self.status.hide_tabs != visible:
                self.status.hide_tabs = visible
                self.status.refresh()
            if self.tabbar.display != visible:
                self.tabbar.display = visible
                self._send_resize()
            # 활성 탭이 바뀌면 콘텐츠 상단 연결부(노트북 탭, #23)가 따라오도록 즉시
            # 재합성한다. 연결부는 _composite 에서 그리는데 평소엔 layout/screen
            # 메시지에만 돌아, status 메시지로 활성 탭만 바뀌면 다음 합성까지 연결부가
            # 옛 탭 위치에 남았다(active_tab_xrange 는 이제 _entries 로 현재 탭에서
            # 직접 계산하므로 여기서 합성만 다시 돌리면 정확한 위치로 그려진다).
            new_xr = self.tabbar.active_tab_xrange() if self.tabbar.display else None
            if self.tabbar.display and (prev_active != new_active
                                        or prev_xr != new_xr):
                self._composite()

        def _log_tab_debug(self, prev_active, new_active):
            """env-gated(PYTMUX_TAB_DEBUG) 탭바 active 전이 진단을 `<state>.tabdbg.jsonl`
            에 한 줄씩 append. §10-F(원격 탭 보는 중 탭바가 로컬 탭으로 한 프레임 튐)
            라이브 판정용 — 매 active 변경의 이전/새 index·새 탭이 원격(⇄)인지·지금
            원격 탭을 보는 중인지를 남겨 'viewing_remote 인데 active 가 비원격(로컬)
            탭으로 바뀐' 의심 프레임(suspect=True)을 바로 잡아낸다. 수정(CL #3:
            auto-rename 방송 per-client 화) 후엔 suspect 줄이 안 나와야 한다. 기본
            OFF·best-effort(로깅이 표시 흐름을 절대 막지 않음)."""
            if not os.environ.get("PYTMUX_TAB_DEBUG"):
                return
            try:
                import json
                wins = self.status.windows
                new_t = next((t for t in wins
                              if t.get("index") == new_active), None)
                new_remote = bool(new_t and new_t.get("remote"))
                viewing_remote = self._viewing_remote()
                rec = {
                    "ts": round(time.time(), 3),
                    "prev": prev_active, "new": new_active,
                    "new_remote": new_remote,
                    "viewing_remote": viewing_remote,
                    "suspect": viewing_remote and not new_remote,
                }
                path = ipc.state_base(self.sock_path) + ".tabdbg.jsonl"
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception:
                pass

        def _send_resize(self):
            if self.writer:
                cols, rows = self._content_size()
                import asyncio as _a
                _a.create_task(write_msg(
                    self.writer, {"t": "resize", "cols": cols, "rows": rows}))

        def confirm_popup(self, message, action, title=None,
                          yes_label=None, danger=False):
            """중앙 확인 팝업을 띄우고, '예'면 action 실행. title/yes_label 미지정 시
            ConfirmScreen 이 로케일 기본(확인/닫기)을 채운다(§6 i18n)."""
            def done(ok):
                if ok and action:
                    action()
            self.push_screen(
                ConfirmScreen(message, yes_label=yes_label, title=title,
                              danger=danger), done)

        def confirm_kill_tab(self):
            # 원격 탭(remote-attach 병합)은 로컬 셸이 아니다 — 닫기[x]/esc x 로
            # kill_window 를 보내면 서버가 §1.7-c 로 거부해 "원격 탭에서는 사용할 수
            # 없는 명령입니다" 만 떴다(사용자 보고 2026-06-20). 원격 탭을 닫는 정문은
            # 그 링크를 분리(remote-detach)하는 것이므로 그렇게 라우팅한다.
            rhost = self._active_remote_host()
            if rhost is not None:
                # 지금 보는(활성) 원격 탭의 병합 전역 index 를 함께 보내 그 **탭
                # 하나만** 분리한다(서버 remote_detach_tab) — 같은 호스트의 다른
                # 원격 탭은 유지된다. index 를 안 실으면 호스트 전체가 끊긴다.
                gi = self._active_tab_index()
                self.confirm_popup(
                    i18n.t("dialog.detach_remote_msg", host=rhost),
                    action=lambda: self.send_cmd("remote_detach", host=rhost,
                                                 index=gi),
                    title=i18n.t("dialog.detach_remote_title"))
                return
            # 이 탭을 닫으면 pytmux 가 끝나는가 = 로컬 탭이 이것 하나뿐인가.
            # 원격(remote-attach ⇄) 탭은 서버 세션의 로컬 창이 아니라 페더레이션
            # 뷰다 — 마지막 로컬 창을 죽이면 서버 세션이 비어 앱 전체가 분리(종료)
            # 된다. 그래서 전체 탭 수가 아니라 **로컬 탭 수**로 판정한다(원격 탭이
            # 함께 열려 있으면 total>1 이라 예전엔 last=False 로 경고가 빠져,
            # 마지막 로컬 탭을 실수로 닫으면 확인 없이 앱이 통째로 디태치됐다).
            local_tabs = sum(1 for t in self.tabbar.tabs if not t.get("remote"))
            has_remote = any(t.get("remote") for t in self.tabbar.tabs)
            last = local_tabs <= 1
            pinned = (not last and any(t.get("active") and t.get("pinned")
                                       for t in self.tabbar.tabs))
            if last and has_remote:
                # 원격 탭이 함께 열려 있는데 마지막 로컬 탭을 닫는 경우 — 앱 전체가
                # 종료되며 원격 탭 보기도 함께 끊긴다는 점을 명시해 실수를 막는다.
                msg = i18n.t("dialog.kill_pytmux_remote_msg")
                title = i18n.t("dialog.kill_pytmux_title")
            elif last:
                msg = i18n.t("dialog.kill_pytmux_msg")
                title = i18n.t("dialog.kill_pytmux_title")
            elif pinned:
                # 항목7: 고정 탭은 "상시 유지" 의도라 실수 닫기를 한 단계 더 막는다.
                name = self._active_window_name() or ""
                msg = i18n.t("dialog.kill_pinned_msg", name=name)
                title = i18n.t("dialog.kill_pinned_title")
            else:
                msg = i18n.t("dialog.kill_tab_msg")
                title = i18n.t("dialog.kill_tab_title")
            self.confirm_popup(
                msg, action=lambda: self.send_cmd("kill_window"),
                title=title, yes_label=None, danger=(last or pinned))

        def confirm_kill_server(self):
            # kill-server 는 서버와 **모든** 탭·셸을 내려 이 pytmux 세션 전체를
            # 끝낸다(자기 호스트 자살). 패널 안에서 벤치마크/스크립트로 무심코
            # 호출돼 세션이 통째로 날아간 사례가 있어, 위험 확인 팝업으로 한 번
            # 막는다(in-app 명령/메뉴 공통). CLI 경로는 launcher 가 따로 가드한다.
            self.confirm_popup(
                i18n.t("dialog.kill_server_msg"),
                action=lambda: self.send_cmd("kill_server"),
                title=i18n.t("dialog.kill_server_title"),
                yes_label=i18n.t("dialog.kill_server_yes"), danger=True)

        def _set_cmd_target(self, on):
            """패널 대상 명령을 프롬프트에서 작성 중일 때 대상 패널(활성)을 밝게
            표시할지 토글한다(요청). 값이 바뀔 때만 재합성한다. 프롬프트가 닫히거나
            대상 외 명령으로 바뀌면 off 로 해제한다."""
            new = self.layout.get("active") if on else None
            if new != self._cmd_target_pane:
                self._cmd_target_pane = new
                self._composite()

        def _flash_pane(self, pid):
            """패널 pid 의 테두리를 잠깐 깜빡인다(warning↔active 교차, ~0.4초). ESC
            모드 방향키로 막 활성화된 패널을 또렷이 보이게 한다(요청). 타이머가 위상을
            토글하며 _composite 를 다시 돌려 테두리 색만 바꾼다(레이아웃 영향 없음)."""
            self._pane_flash_id = pid
            self._pane_flash_on = True
            self._pane_flash_left = 4          # on/off 4회 = 2번 깜빡임
            if self._pane_flash_timer is not None:
                self._pane_flash_timer.stop()
            self._pane_flash_timer = self.set_interval(0.1, self._pane_flash_step)
            self._composite()

        def _pane_flash_step(self):
            self._pane_flash_left -= 1
            if self._pane_flash_left <= 0:
                self._pane_flash_on = False
                self._pane_flash_id = None
                if self._pane_flash_timer is not None:
                    self._pane_flash_timer.stop()
                    self._pane_flash_timer = None
            else:
                self._pane_flash_on = not self._pane_flash_on
            self._composite()

        def _pane_above(self):
            """활성 패널 위쪽(같은 열 범위)에 다른 패널이 있는지."""
            act = self.layout.get("active")
            panes = self.layout.get("panes", [])
            ap = next((p for p in panes if p["id"] == act), None)
            if not ap:
                return False
            ax, ay = ap["x"], ap["y"]
            aw = ap["w"]
            for p in panes:
                if p["id"] == act:
                    continue
                if p["y"] + p["h"] <= ay and not (
                        p["x"] + p["w"] <= ax or p["x"] >= ax + aw):
                    return True
            return False

        def _pane_below(self):
            """활성 패널 아래쪽(같은 열 범위)에 다른 패널이 있는지(상태바 포커스 진입 판정)."""
            act = self.layout.get("active")
            panes = self.layout.get("panes", [])
            ap = next((p for p in panes if p["id"] == act), None)
            if not ap:
                return False
            ax, aw, abot = ap["x"], ap["w"], ap["y"] + ap["h"]
            for p in panes:
                if p["id"] == act:
                    continue
                if p["y"] >= abot and not (
                        p["x"] + p["w"] <= ax or p["x"] >= ax + aw):
                    return True
            return False

        # ---- ESC 모드 상태바 버튼 포커스(_status_buttons·_enter_status_focus·
        # _set_status_focus·_exit_status_focus·_handle_status_focus)는
        # _StatusFocusMixin(모듈 레벨, §5.4)으로 분리. ----

        # ---- IPC reader·재접속(_start_reader·_reader_task·_connect_and_hello·
        # _reconnect·_force_reconnect·reconnect_now)은 _NetReconnectMixin
        # (모듈 레벨, §5.4)으로 분리. ----

        def _fire_hook(self, event, env=None):
            cmd = self.hooks.get(event)
            if not cmd:
                return
            if not env:
                self._run_command(cmd)
                return
            # M16: 훅 명령(run-shell 래핑 시 subprocess 가 os.environ 상속)이 참조할
            # PYTMUX_* 컨텍스트를 잠깐 심고 복원한다(§8).
            saved = {k: os.environ.get(k) for k in env}
            try:
                for k, v in env.items():
                    os.environ[k] = str(v)
                self._run_command(cmd)
            finally:
                for k, old in saved.items():
                    if old is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = old

        def _dispatch(self, msg):
            t = msg.get("t")
            if t == "layout":
                prev_active = self.layout.get("active")
                self.layout = msg
                new_active = msg.get("active")
                # ESC 모드 방향키로 패널 전환을 요청했고(active 가 실제로 바뀜) 깜빡임이
                # 예약돼 있으면 새 활성 패널을 깜빡인다(요청 — 선택 패널 가시화).
                if (self._flash_pending and new_active is not None
                        and new_active != prev_active):
                    self._flash_pending = False
                    self._flash_pane(new_active)
                self._request_composite()
                if not self._attached:
                    self._attached = True
                    self._fire_hook("client-attached")
            elif t == "screen":
                self.pane_content[msg["pane"]] = (msg["rows"], msg.get("cursor"))
                self.pane_wrap[msg["pane"]] = set(msg.get("wrap") or ())
                self._delta_no_base.discard(msg["pane"])  # full 수신 → baseline 회복
                self._request_composite()
            elif t == "screen-delta":
                # B2: 바뀐 행만 받아 캐시된 rows 에 행 단위로 적용. base 가 없는
                # per-client 모델이라 직전 full/델타가 만든 캐시에 그대로 덮어쓴다.
                pid = msg["pane"]
                prev = self.pane_content.get(pid)
                if prev is None:
                    # §1.7 회복: baseline(직전 full) 없이 델타만 오면 바뀐 행을 둘
                    # 기준 캐시가 없어 행이 유실된다 — 원격 attach 시 업스트림의 초기
                    # full 이 '보는 클라 없음'으로 드롭되면 발생(원격 패널 영구 공백).
                    # 드롭 대신 redraw 를 1회 요청해 full 을 끌어온다(원격 패널이면
                    # request_redraw 가 업스트림으로 릴레이돼 그 원격 서버의 _send_full
                    # 을 받아온다). full 수신 전까지 중복 요청은 _delta_no_base 로 디바운스.
                    if pid not in self._delta_no_base:
                        self._delta_no_base.add(pid)
                        self.send_cmd("request_redraw")
                else:
                    rows = list(prev[0])
                    for y, segs in msg["rows"]:
                        if 0 <= y < len(rows):
                            rows[y] = segs
                        elif y == len(rows):
                            rows.append(segs)
                    self.pane_content[pid] = (rows, msg.get("cursor"))
                    self.pane_wrap[pid] = set(msg.get("wrap") or ())
                    self._request_composite()
            elif t == "status":
                # 플러그인 관리(PLUGIN_MANAGER_SCENARIO): 서버가 보낸 비활성 집합을 이
                # 클라 레지스트리에 반영한다(바뀔 때만). set_disabled 가 self.plugins 를
                # 활성 부분집합으로 다시 만들어 명령 자동완성·클라 훅(오버레이/배지/탭)이
                # 비활성 플러그인을 즉시 거른다.
                dp = msg.get("disabled_plugins")
                if dp is not None and set(dp) != self.plugins.disabled:
                    self.plugins.set_disabled(dp)
                    scr = getattr(self, "_plugin_screen", None)
                    if scr is not None:
                        scr.refresh_labels()   # 관리 팝업 열려 있으면 라벨 확정
                self.status.update_status(msg)
                # Claude 상태(claude_rules 동기화, pane_claude 갱신,
                # /usage 자동 팝업)는 claude-code 플러그인이 client_status 훅으로 흡수
                # 한다(없으면 no-op — delete-to-disable). 코어는 호출만 한다.
                self.plugins.client_status(self, msg)
                # single-border 전역 상태도 서버 권위값으로 반영(opts.json 영속)
                if "single_border" in msg:
                    self.single_border_on = bool(msg["single_border"])
                # 나머지 서버 옵션 현재값(:settings 표시용)도 권위값으로 갱신.
                for _k in ("coalesce_repaints", "nest_auto_attach", "vt_parser",
                           "window_size", "auto_rename", "border_status",
                           "monitor_activity", "monitor_bell"):
                    if _k in msg:
                        self.server_opts[_k] = msg[_k]
                # 컨텍스트 메뉴가 열려 있으면 토글 라벨(on/off)을 실제 상태로 갱신
                ms = getattr(self, "_menu_screen", None)
                if ms is not None:
                    ms.refresh_labels()
                # 토큰 절감 설정 팝업이 열려 있으면 토글/값 라벨을 권위값으로 갱신
                ss = getattr(self, "_saver_screen", None)
                if ss is not None:
                    ss.refresh_labels()
                # M19: 토큰로그 팝업이 열려 있고 새 /usage 결과가 왔으면 반영
                tl = getattr(self, "_token_log_screen", None)
                if tl is not None and "usage_limits" in msg:
                    tl.update_usage(msg.get("usage_limits"))
                self._update_tabbar()
                if self.set_titles:
                    self.title = self.status._expand(self.title_fmt)
                wins = msg.get("windows", [])
                n = len(wins)
                if self._prev_winc and n > self._prev_winc:
                    self._fire_hook("after-new-window")
                self._prev_winc = n
                anybell = any(w.get("bell") for w in wins)
                if anybell and not self._prev_bell:
                    self._fire_hook("alert-bell")
                self._prev_bell = anybell
                # M16 절감 신호 에스컬레이션 훅(claude-limit/budget 등)은 claude-code
                # 플러그인의 client_status 훅(plugins.client_status, 위 766줄)으로 이전했다
                # (S5a) — 코어가 더는 claude.py(saver_hook_events)를 import 하지 않는다.
            elif t == "tree":
                if self._want_tree:
                    self._want_tree = False
                    purpose = getattr(self, "_tree_purpose", "choose")
                    if purpose == "status_tabs":
                        self._open_status_tabs(msg)
                    else:
                        # (purpose=="usage" 트리 팝업은 token-usage→token-log 통합
                        #  (2026-06-12)으로 제거 — 요청자가 더 없다.)
                        self._open_choose_tree(msg)
            elif t == "version":
                if getattr(self, "_want_version", False):
                    self._want_version = False
                    self._show_version_popup(msg)
            elif t == "restart_check":
                if getattr(self, "_want_restart_check", False):
                    self._want_restart_check = False
                    self._show_restart_check_popup(msg)
                elif getattr(self, "_pending_restart", None):
                    # restart-server/restart-all 의 선행 드라이런 회신 — 안전성으로
                    # 실제 재시작을 게이트한다(FAIL 이면 재확인).
                    self._gate_restart_on_check(msg)
            elif t == "layouts":
                if self._want_layouts:
                    mode = self._want_layouts
                    self._want_layouts = None
                    self._open_choose_layout(msg.get("names", []), mode)
            elif t == "buffers":
                if self._want_buffers:
                    self._want_buffers = False
                    self._open_choose_buffer(msg.get("items", []))
            elif t == "pong":
                self._on_pong()   # 네트워크 RTT 표본(§10)
            elif t == "notice":
                # 서버발 일반 알림(§1.7 remote-attach 결과 등) — 상태줄 메시지로.
                # secs/dismissable 를 실으면 유지 시간·수동 닫기 가능 여부를 따른다
                # (실패 알림은 3초 유지 + 클릭/Enter 닫기, 아래 serverio).
                # 서버는 로케일을 모르므로(per-user 클라-로컬) key(rnotice.*)+kw 를
                # 실어 보낸다 — 여기서 자기 로케일로 번역한다. text 는 한국어 폴백.
                self.display_message(self._notice_text(msg),
                                     secs=float(msg.get("secs", 2.0)),
                                     dismissable=bool(msg.get("dismissable")))
            elif t == "captured":
                self.display_message(i18n.t("msg.captured_chars",
                                            n=msg.get('chars', 0)))
            elif t == "restarting":
                # 작업 보존 재시작 통지(ⓔ): 곧 끊길 연결을 재접속으로 다룬다.
                self._reconnecting = True
                # 서버가 relaunch 를 실으면(외부 CLI restart-all) in-place 재접속
                # 대신 클라를 relaunch 한다 — 끊김 처리(_start_reader)에서 _relaunch
                # 를 세워 os.execv(새 클라 코드). 클라-측 begin_restart("all")이
                # 로컬로 세우던 플래그를, 외부 트리거에서는 서버 통지로 세운다.
                if msg.get("relaunch"):
                    self._relaunch_on_restart = True
                self.display_message(i18n.t("msg.server_restarting"))
            elif t == "bye":
                self.exit(message=i18n.t("msg.server_terminated"))
            else:
                # 코어가 모르는 메시지(t)는 플러그인에 위임한다(ncd 의 nc_list 등).
                self.plugins.handle_message(self, msg)

        # Claude Code 상태 메서드(_update_claude)는 claude-code 플러그인
        # attach_client 가 인스턴스 클로저로 설치한다(Phase 2c). 코어는 client_status
        # 훅·getattr 로만 닿는다(delete-to-disable).
        # REC(출력 캡처) 정보 줄·show_capture_info 는 rec 플러그인 clientside 로 이전
        # (attach_client 가 app.show_capture_info 설치). REC 탭 자체도 client_status_tabs
        # 훅이 기여한다. 코어는 getattr 로만 닿는다(delete-to-disable).
        def show_status_tabs(self, initial=0):
            # 통합 상태 팝업(서버 탭 등)을 연다. REC 탭은 플러그인이 기여한다(있으면).
            self._status_tab_initial = initial
            self.request_tree(purpose="status_tabs")

        # 원격제어 토글/팝업(_toggle_remote_control·open_remote_control)은 claude-code
        # 플러그인 attach_client 가 설치한다(Phase 2c). clientwidgets 의 'Remote Control
        # active' 클릭은 getattr(app,"open_remote_control") 가드로 호출(없으면 no-op).
        # ---- 패널 오버레이(시계/달력) — clock·calendar 플러그인 ----
        # toggle_clock/set_clock/toggle_calendar/set_calendar 와 clock_panes/
        # calendar_panes 는 플러그인이 attach_client 로 이 인스턴스에 설치한다(없으면
        # 아래 코어 호출부가 getattr 로 no-op). 1초 틱·오버레이 닫기는 레지스트리
        # 훅으로만 닿아, 플러그인 디렉토리를 지우면 조용히 비활성화된다.
        def _close_overlay(self, pane_id):
            """해당 패널의 플러그인 오버레이(시계/달력 등)를 닫는다(패널 클릭/Shift+ESC
            공용 경로 — clientwidgets 의 패널 클릭이 모든 클릭마다 이걸 부른다). 닫은
            플러그인이 있으면 재합성하고 True(코어가 입력을 소비), 없으면 False(코어가
            기본 동작 수행). 플러그인이 하나도 없으면 항상 False라 코어에 얇은 위임자로
            남겨 둔다."""
            if self.plugins.client_close_overlay(self, pane_id):
                self._composite()
                return True
            return False

        def _close_active_overlay(self):
            """활성 패널의 오버레이를 닫는다(Shift+ESC). 닫았으면 True."""
            return self._close_overlay(self.layout.get("active"))

        def _clock_tick(self):
            # 1초마다: 시간 갱신이 필요한 오버레이(시계/달력 등)를 띄운 플러그인이
            # 있으면 재합성한다(없으면 idle — 아무 일도 안 함). 시계는 초, 달력은
            # 자정 넘김 '오늘' 강조 이동을 위해 갱신된다.
            if self.plugins.client_tick(self):
                self._composite()

        # ---- 네트워크 응답성 측정(§10): ping/pong RTT + 히스테리시스 ----
        # ---- RTT 핑/히스테리시스(_net_ping·_on_pong·_net_sample)는
        # _NetReconnectMixin(모듈 레벨, §5.4)으로 분리. ----

        # ---- 화면 합성(_composite·_request_composite·_do_pending_composite·
        # push_screen·pop_screen·_draw_tab_close)은 _RenderMixin(모듈 레벨, §5.4)으로
        # 분리. ----
        def send_cmd(self, action, **kw):
            if self.writer:
                kw["t"] = "cmd"
                kw["action"] = action
                # 새 탭/패널은 설정된 시작 디렉토리(default-path)를 함께 보낸다.
                # 서버가 current/home/<경로> 를 해석한다. 호출부에서 명시하면 우선.
                if action in ("split", "new_window") and "path" not in kw:
                    kw["path"] = getattr(self, "default_path", "current")
                asyncio.create_task(write_msg(self.writer, kw))

        def send_input(self, data: bytes):
            if self.writer and data:
                # 라이브 PTY 팝업(#10-1)이 열려 있으면 입력을 **팝업 패널**로 보낸다
                # (팝업은 항상 포커스 — 트리 활성 패널 대신). 팝업 패널은 트리 밖이라
                # 서버는 pane_by_id 로 못 찾고 popup 직송 분기(serverio)로 그 PTY 에
                # 쓴다. 팝업이 없으면 평소대로 활성 패널로.
                pu = self.layout.get("popup")
                target = pu["id"] if pu else self.layout.get("active")
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": target,
                    "data": base64.b64encode(data).decode("ascii")}))

        def send_input_pane(self, pane_id, data: bytes):
            """send_input 의 명시-패널 버전 — 활성 패널을 바꾸지 않고 특정 패널 PTY 로
            입력 바이트를 보낸다(서버는 pane_by_id 로 라우팅). Claude footer 의
            'esc to interrupt' 클릭 → 그 패널에 ESC 주입 등에 쓴다."""
            if self.writer and data and pane_id is not None:
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": pane_id,
                    "data": base64.b64encode(data).decode("ascii")}))

        def action_ctrl_q(self):
            # Ctrl+Q 는 앱 종료가 아니라(종료는 detach 명령) 활성 패널로 전달(#25).
            # normal 모드에서만 패스스루 — 다른 모드는 그 모드 키 해석을 위해 무시.
            # (참고: 터미널 흐름제어 IXON 이 Ctrl+Q(XON)를 먹으면 앱까지 안 올 수
            #  있다 — 그건 터미널 설정 영역. 여기선 Textual quit 가로채기만 해제.)
            if self.mode == "normal":
                self.send_input(b"\x11")

        def send_mouse(self, pane_id, data: bytes):
            """마우스 패스스루: 특정 패널 PTY 로만 raw 마우스 시퀀스를 보낸다
            (입력 동기화/프롬프트 추적 제외 — 서버가 mouse 플래그로 구분)."""
            if self.writer and data:
                # 진단(mouse-debug): 클라가 "이 패널은 마우스 추적 ON"이라 믿고
                # 실제로 SGR/X10 바이트를 PTY 로 흘리는 순간을 그 믿음(mouse/sgr)과
                # 함께 남긴다. restart-all 후 SGR 시퀀스가 프롬프트에 텍스트로 박히는
                # Windows 버그(HANDOFF §10-H) 진단의 결정 신호 — 여기 찍히면 클라는
                # mouse>=1 로 믿고 보냈는데 앱은 추적 OFF 라 텍스트로 받은 것이다.
                if self.mouse_debug:
                    pl = next((q for q in self.layout.get("panes", [])
                               if q.get("id") == pane_id), None)
                    mt = pl.get("mouse") if pl else "?"
                    sgr = pl.get("mouse_sgr") if pl else "?"
                    self._log_mouse("pass", 0, 0,
                                    note=f"pane={pane_id} mouse={mt} sgr={sgr} "
                                         f"bytes={data!r}")
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": pane_id, "mouse": True,
                    "data": base64.b64encode(data).decode("ascii")}))

        def _log_mouse(self, kind, x, y, button=0, note=""):
            """마우스 진단 로그 한 줄(켜졌을 때만). 원격에서 휠 이벤트가 클라이언트
            (Textual)까지 도달하는지 확인하는 용도. 도달하면 여기 찍히고, 그래도
            스크롤이 안 되면 서버 scroll 처리/터미널 재그리기 쪽을 본다."""
            if not self.mouse_debug:
                return
            try:
                with open(self._mouse_log_path, "a") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {kind} "
                            f"x={x} y={y} b={button} mode={self.mode} {note}\n")
            except OSError:
                pass

        def _log_key(self, key):
            """진단: mouse-debug 가 켜졌을 때 '내비게이션 키'만 mouse.log 에 남긴다.
            휠 이벤트(scroll_up/down)가 한 줄도 안 찍히는데 휠을 굴릴 때마다 여기에
            `key up`/`key down` 이 쏟아지면, 상위 터미널이 휠을 화살표로 변환(1007
            미지원)해 보내는 것 — 1007 끄기(alt-scroll on)가 안 듣는 터미널이다.
            반대로 둘 다 안 찍히면 터미널이 휠을 아예 안 넘긴다(터미널 자체 스크롤백
            가로채기 등). **문자/단축키는 기록하지 않는다**(`_KEY_DIAG` 화이트리스트
            만) — 패널 입력 유출 방지."""
            if not self.mouse_debug:
                return
            if _normalize_key(key) not in _KEY_DIAG:
                return
            try:
                with open(self._mouse_log_path, "a") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} key "
                            f"{_normalize_key(key)} mode={self.mode}\n")
            except OSError:
                pass

        def send_scroll(self, pane_id, delta=0, bottom=False, top=False):
            if self.writer:
                m = {"t": "scroll", "pane": pane_id}
                if bottom:
                    m["bottom"] = True
                elif top:
                    m["top"] = True
                else:
                    m["delta"] = delta
                asyncio.create_task(write_msg(self.writer, m))

        def _apply_ambiguous_wide(self, wide: bool):
            """모호폭 wide 모드를 런타임 전환한다(:set ambiguous-width). 클라 폭 모델
            (char_cells·Rich/Textual 측정)을 바꾸고, 서버에 통지(set_ambig)해 서버 pyte
            격자도 같은 폭으로 맞춘 뒤 앱을 SIGWINCH repaint 시킨다. 같은 모드면 no-op."""
            from . import cellwidth
            if cellwidth.ambiguous_wide() == wide:
                return
            cellwidth.set_ambiguous_wide(wide)        # 클라측 Textual/char_cells 폭
            if self.writer:                            # 서버 pyte 격자 + 앱 repaint
                asyncio.create_task(write_msg(
                    self.writer, {"t": "set_ambig", "wide": wide}))
            self._update_tabbar()                      # 탭바 라벨 폭 재계산
            self.status.refresh()
            self._composite()                          # 패널 합성 새 폭으로

        # ---- 복사/버퍼 ----
        # OS 클립보드 입출력은 앱 상태 비의존이라 clientclip.py 모듈 자유함수로
        # 분리했다(#12). 클로저는 거기에 위임만 한다.
        # ---- 클립보드/페이스트 버퍼: copy_text·paste_os_clipboard·
        # _do_paste_clipboard·choose_buffer·_open_choose_buffer 는 _ClipboardMixin
        # (모듈 레벨, §5.4)으로 분리. ----

        # ---- choose-tree ----
        # 코어 _open_status_tabs(통합 REC/서버 탭)는 client_status_tabs 훅으로 플러그인
        # 탭을 받아 끼운다(현재 기여 플러그인 없음 — 구 '토큰 사용량' 탭은 2026-06-12
        # token-log 팝업으로 통합·제거). (인패널 /usage 자동 팝업은 2026-06-17 제거 —
        # §3.9. 수동 usage-panel 명령은 유지.)
        # ---- 선택 팝업(request_tree·_open_status_tabs·_open_choose_tree·
        # save_layout_prompt·request_layouts·_open_choose_layout)은
        # _ChooseScreensMixin(모듈 레벨, §5.4)으로 분리. 버전/재시작/서버정보는
        # _RestartVersionMixin 으로 분리(둘 다 §5.4). ----

        # ---- 메뉴 ----
        def open_menu(self, pane_id=None):
            # 메뉴 대상 패널(우클릭한 패널, 없으면 활성). 배경 강조(#18)·동작 대상.
            self._menu_pane = pane_id or self.layout.get("active")
            self._menu_open = True
            self._composite()        # 대상 외 패널을 흐리게(메뉴 모달 아래로 보임)
            def handle(result):
                self._menu_open = False
                self._composite()    # dim 해제
                if result:
                    self._run_menu_action(result)
            self.push_screen(MenuScreen(), handle)

        def _run_menu_action(self, key):
            if key == "split_lr":
                self.send_cmd("split", orient="lr")
            elif key == "split_tb":
                self.send_cmd("split", orient="tb")
            elif key == "zoom":
                self.send_cmd("zoom")
            elif key == "kill_pane":
                self.send_cmd("kill_pane")
            elif key == "rotate":
                self.send_cmd("rotate", forward=True)
            elif key == "swap_pane":
                self.send_cmd("swap_pane", forward=True)
            elif key == "break_pane":
                self.send_cmd("break_pane")
            elif key == "join_pane":
                # join-pane 은 대상 탭 인자가 필요 — 명령 프롬프트에 미리 채워 준다
                # (rename-pane 메뉴와 동일 패턴, §2.7).
                self.open_prompt("command", "", initial="join-pane ")
            elif key == "merge_remote_tab":
                self.merge_remote_tab_picker()
            elif key == "rename_pane":
                self.open_prompt("command", "", initial="rename-pane ")
            elif key == "next_layout":
                self.send_cmd("cycle_layout")
            elif key == "select_layout":
                # 레이아웃 프리셋 선택기(명령 옵션 모달 재사용 — 키보드만으로 선택).
                opts = COMMAND_OPTIONS.get("select-layout")
                if opts:
                    def _run(line):
                        if line:
                            self._run_command(line)
                    self.push_screen(CommandOptionsScreen(
                        "select-layout", i18n.t("screen.layout_preset"), opts),
                        _run)
            elif key == "search":
                self.open_prompt("search", i18n.t("search.scrollback"))
            elif key == "sync":
                self.send_cmd("set_sync")
            elif key == "autoresume":
                self.send_cmd("set_autoresume")
            elif key == "prompt_clear":
                self.send_cmd("set_prompt_clear")
            elif key == "choose_tree":
                self.request_tree()
            elif key == "new_window":
                self.send_cmd("new_window")
            elif key == "rename_window":
                cur = self._active_window_name()
                self.open_prompt("rename_window", cur or "rename-tab",
                                 suggest=cur)   # 현재 이름=ghost(타이핑=덮어쓰기)
            elif key == "kill_window":
                self.confirm_kill_tab()
            elif key == "toggle_pin":
                # 항목7: 활성 탭 고정 토글(원격 탭 거부 가드 포함 — clientcmd 경유).
                self._run_command("pin-toggle")
            elif key == "next_window":
                self.send_cmd("next_window")
            elif key == "prev_window":
                self.send_cmd("prev_window")
            elif key == "layout_save":
                self.save_layout_prompt()
            elif key == "layout_load_over":
                self.request_layouts("over")
            elif key == "layout_load_new":
                self.request_layouts("new")
            elif key == "command":
                self.open_prompt("command", "")
            elif key == "mouse_help":
                self._run_command("mouse-help")   # list-keys "키 · 마우스" 팝업(§2.2)
            elif key == "detach":
                self.exit(message="detached")
            elif key == "kill_server":
                self.confirm_kill_server()
            else:
                # 플러그인 메뉴 항목(§2.7): key = 그 플러그인의 명령 이름 —
                # 명령 디스패치로 폴백(미지 키는 _run_command 가 조용히 무시).
                self._run_command(key)

        # ---- 프롬프트 / 명령 ----
        def _notice_text(self, msg):
            """서버발 notice 를 현재 로케일 문자열로. 서버는 로케일을 모르므로(per-user
            클라-로컬) key(rnotice.*)+kw 를 싣고, 실패 원인은 detail(키 포함)로 싣는다.
            여기서 detail 을 먼저 자기 로케일로 합성해 {why} 에 채운 뒤 본문 키를 번역한다.
            key 가 없으면(구서버/플러그인) text 를 그대로 — 한국어 폴백."""
            kw = dict(msg.get("kw") or {})
            detail = msg.get("detail")
            if detail:
                dkey = detail.get("key")
                kw["why"] = (i18n.t(dkey, default=detail.get("text", ""),
                                    **(detail.get("kw") or {})) if dkey
                             else detail.get("text", ""))
            key = msg.get("key")
            if key:
                return i18n.t(key, default=str(msg.get("text", "")), **kw)
            return str(msg.get("text", ""))

        def display_message(self, text, secs=2.0, dismissable=False):
            self.status.message = text
            self._msg_dismissable = dismissable
            self.status.refresh()
            # 이전 메시지 타이머가 살아 있으면 멈춘다 — 새 메시지를 옛 타이머가
            # 조기에 지우지 않도록(수동 닫기 dismissable 메시지에서 특히 중요).
            if self._msg_timer is not None:
                self._msg_timer.stop()
            self._msg_timer = self.set_timer(secs, self._clear_message)

        def _clear_message(self):
            self.status.message = None
            self._msg_dismissable = False
            self._msg_timer = None
            # 메시지가 ESC 모드 하단 포커스 대상이었으면 포커스도 해제(잔상 방지).
            if self.status.focus_btn == "msg":
                self.status.focus_btn = None
                self._status_focus = None
            self.status.refresh()

        def _dismiss_message(self):
            """수동 닫기(클릭/터치·ESC 모드 Enter): 타이머를 멈추고 즉시 지운다."""
            if self._msg_timer is not None:
                self._msg_timer.stop()
            self._clear_message()

        def _restart_status_timer(self):
            if self._status_timer is not None:
                self._status_timer.stop()
            self._status_timer = self.set_interval(
                self.status_interval, self.status.refresh)

        def apply_option(self, name, val):
            """클라이언트 측 옵션을 런타임에 적용."""
            if name == "prefix":
                self.prefix_key = _tmux_key_to_textual(val)
                self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            elif name == "mouse":
                self.mouse_enabled = val.lower() in ("on", "true", "1", "yes")
            elif name in ("mouse-drag-copy", "mouse_drag_copy"):
                self.mouse_drag_copy = val.lower() in ("on", "true", "1", "yes")
            elif name in ("mouse-debug", "mouse-log"):
                self.mouse_debug = val.lower() in ("on", "true", "1", "yes")
                if self.mouse_debug:
                    self.display_message(
                        i18n.t("msg.mouse_log", path=self._mouse_log_path))
            elif name == "alt-scroll":
                # on = 대체 스크롤 모드(1007) 비활성(휠을 실제 마우스 이벤트로) — 기본.
                # off = 터미널 기본 동작에 맡김(휠이 화살표로 갈 수 있음).
                self.disable_alt_scroll = val.lower() in ("on", "true", "1", "yes")
                self._term_write(
                    "\x1b[?1007l" if self.disable_alt_scroll else "\x1b[?1007h")
                self.display_message(
                    "휠 스크롤백: " + ("pytmux 처리(1007 끔)"
                                       if self.disable_alt_scroll else "터미널 기본"))
            elif name in ("ambiguous-width", "ambiguous_width"):
                # 모호폭(East Asian Ambiguous: → · — 등) 폭 모드 런타임 전환:
                # narrow(1칸)|wide(2칸)|auto. 단말과 앱(Claude Code 등)의 모호폭 셈법이
                # 다를 때(예: 단말 2칸 + Claude 1칸 → 스크롤 부분갱신 글자 겹침) 사용자가
                # 직접 맞춘다. 클라 폭 모델 + 서버 pyte 격자를 함께 전환하고 화면을
                # 새 폭으로 다시 그린다(set_ambiguous_wide).
                v = val.strip().lower()
                if v == "auto":
                    wide = bool(getattr(self, "_ambig_auto_wide", False))
                elif v in ("wide", "2", "double", "full"):
                    wide = True
                else:                              # narrow|1|single|half|off
                    wide = False
                self._apply_ambiguous_wide(wide)
                self.display_message(
                    "모호폭: " + ("wide(2칸)" if wide else "narrow(1칸)")
                    + (" · auto" if v == "auto" else ""))
            elif name == "status-bg":
                self.status.bg = val
                self.status.refresh()
            elif name == "status-fg":
                self.status.fg = val
                self.status.refresh()
            elif name == "mode-keys":
                self.mode_keys = "emacs" if val == "emacs" else "vi"
            elif name == "status-left":
                self.status.left_fmt = val
                self.status.refresh()
            elif name == "status-right":
                self.status.right_fmt = val
                self.status.refresh()
            elif name == "status":
                # status N — 상태표시줄 줄 수(0~5). on/off 는 1/0 으로.
                v = val.strip().lower()
                if v in ("on", "true", "yes"):
                    self.set_status_lines(1)
                elif v in ("off", "false", "no"):
                    self.set_status_lines(0)
                else:
                    try:
                        self.set_status_lines(int(v))
                    except ValueError:
                        pass
            elif name == "status-format":
                # status-format <line> <fmt...> — 보조 줄(line>=1) 포맷 지정.
                parts = val.split(None, 1)
                if parts:
                    try:
                        idx = int(parts[0])
                    except ValueError:
                        idx = -1
                    if idx >= 1:
                        self.status.extra[idx] = parts[1] if len(parts) > 1 else ""
                        self.status.refresh()
            elif name == "status-position":
                self.status_position = "top" if val == "top" else "bottom"
                self.status.styles.dock = self.status_position
            elif name == "status-interval":
                try:
                    self.status_interval = max(1, int(val))
                    self._restart_status_timer()
                except ValueError:
                    pass
            elif name == "set-titles":
                self.set_titles = val.lower() in ("on", "true", "1", "yes")
            elif name == "set-titles-string":
                self.title_fmt = val
            elif name in ("tab-bar", "tabbar"):
                self.set_tab_bar_always(
                    val.lower() in ("always", "on", "true", "1", "yes"))
            elif name in ("default-path", "default_path"):
                # 새 탭/패널 시작 디렉토리(current/home/<경로>). 기존엔 config 로딩
                # 전용이라 런타임 set 이 무시됐다 — :settings 에서 바꿀 수 있게 보강.
                self.default_path = val.strip()

        def apply_setting(self, desc, value):
            """:settings 화면의 한 설정을 런타임 적용 + 영속한다(흩어진 적용 로직을
            재구현하지 않고 기존 명령 경로를 그대로 호출). value 는 정규형 문자열.

            적용: f"{desc['cmd']} {value}" 를 _run_command — config 옵션은 `set …`
            경로(apply_option), inactive-dim·서버 토글·lang 은 각자의 기존 분기로 간다.
            영속: backend=='config' 면 config 파일에 set 줄 기록(없으면 추가), 서버·
            lang 은 그 명령이 이미 opts.json/.lang 에 영속하므로 추가 작업 없음."""
            cmd = desc.get("cmd")
            if not cmd:
                return
            self._run_command(f"{cmd} {value}".strip())
            if desc.get("backend") == "config":
                try:
                    set_config_option(desc["key"], value, self._config_path)
                except OSError as e:
                    self.display_message(i18n.t("msg.setting_save_failed",
                                                err=str(e)))

        def setting_current(self, key):
            """:settings 행에 표시할 현재 값(정규형 문자열). 클라가 추적하지 않는
            서버 토글(coalesce/nest-auto-attach/vt-parser/monitor-*/automatic-rename/
            pane-border-status)은 None — 화면이 '현재값 미상(서버)'으로 표시한다."""
            st = self.status
            if key == "inactive-dim":
                return "on" if self.inactive_dim else "off"
            if key == "inactive-dim-ratio":
                return f"{self.inactive_dim_ratio:.2f}"
            if key == "strip-box-drawing":
                return "on" if self.strip_box_drawing else "off"
            if key in ("tab-bar", "tabbar"):
                return "always" if self.tab_bar_always else "auto"
            if key == "status-position":
                return self.status_position
            if key == "single-border":
                return "on" if self.single_border_on else "off"
            if key == "language":
                return self.lang
            if key == "mouse":
                return "on" if self.mouse_enabled else "off"
            if key in ("mouse-drag-copy", "mouse_drag_copy"):
                return "on" if self.mouse_drag_copy else "off"
            if key == "mode-keys":
                return self.mode_keys
            if key == "alt-scroll":
                return "on" if self.disable_alt_scroll else "off"
            if key in ("ambiguous-width", "ambiguous_width"):
                from . import cellwidth
                return "wide" if cellwidth.ambiguous_wide() else "narrow"
            if key == "prefix":
                return textual_key_to_tmux(self.prefix_key) or self.prefix_key
            if key == "default-path":
                return getattr(self, "default_path", "current")
            if key == "set-titles":
                return "on" if self.set_titles else "off"
            if key == "status-interval":
                return str(self.status_interval)
            if key == "status-left":
                return self.status.left_fmt or ""
            if key == "status-right":
                return self.status.right_fmt or ""
            if key == "status-bg":
                return self.status.bg or ""
            if key == "status-fg":
                return self.status.fg or ""
            if key == "synchronize-panes":
                return "on" if getattr(st, "sync", False) else "off"
            # 서버 권위 옵션(status 가 self.server_opts 에 채움). 미수신이면 None.
            _srv = {"coalesce-repaints": "coalesce_repaints",
                    "win-mouse-motion": "win_mouse_motion",
                    "nest-auto-attach": "nest_auto_attach",
                    "vt-parser": "vt_parser",
                    "window-size": "window_size",
                    "automatic-rename": "auto_rename",
                    "pane-border-status": "border_status",
                    "monitor-activity": "monitor_activity",
                    "monitor-bell": "monitor_bell"}
            if key in _srv:
                v = self.server_opts.get(_srv[key])
                if v is None:
                    return None
                if key in ("vt-parser", "window-size"):  # enum: 값 그대로
                    return v
                return "on" if v else "off"
            return None   # 링크 등


        def show_options(self):
            lines = [
                f"prefix      {self.prefix_key}",
                f"mouse       {'on' if self.mouse_enabled else 'off'}",
                f"mouse-drag-copy {'on' if self.mouse_drag_copy else 'off'}",
                f"mouse-debug {'on' if self.mouse_debug else 'off'}",
                f"alt-scroll  {'on' if self.disable_alt_scroll else 'off'}",
                f"status-bg   {self.status.bg}",
                f"status-fg   {self.status.fg}",
                f"mode-keys   {self.mode_keys}",
            ]
            self.push_screen(InfoScreen(lines, title="options"))

        def reload_config(self, path=None):
            cfg = load_config(path)
            self.prefix_key = cfg["prefix"]
            self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            self.bindings = cfg["bindings"]
            self.root_bindings = cfg.get("root_bindings", {})
            self.aliases = cfg.get("aliases", {})
            self.hooks = cfg.get("hooks", {})
            self.mouse_enabled = cfg["mouse"]
            self.mode_keys = cfg["mode_keys"]
            self.status.bg = cfg["status_bg"]
            self.status.fg = cfg["status_fg"]
            if "status_left" in cfg:
                self.status.left_fmt = cfg["status_left"]
            if "status_right" in cfg:
                self.status.right_fmt = cfg["status_right"]
            self.set_tab_bar_always(cfg.get("tab_bar_always", True))
            self.default_path = cfg.get("default_path", "current")
            self.status.refresh()

        def _active_window_name(self):
            for w in self.status.windows:
                if w.get("active"):
                    return w.get("name", "")
            return ""

        def _active_pane_title(self):
            act = self.layout.get("active")
            for p in self.layout.get("panes", []):
                if p["id"] == act:
                    return (p.get("title") or "").strip()
            return ""

    return PytmuxApp(sock_path)


def _relaunch_self():
    """현재 클라이언트를 os.execv 로 같은 인자로 교체 재실행한다(restart-all·크래시
    자동복구 공용). `python pytmux.py …`(스크립트)면 인터프리터로, 설치된 콘솔
    스크립트(실행권한 있는 비-.py)면 그 실행파일로 원 동작(attach)을 재현한다.
    execv 실패 시 분리 프로세스로라도 새 클라를 띄운다. 정상 경로면 돌아오지 않는다."""
    import sys
    argv0 = sys.argv[0]
    if argv0.endswith(".py") or not os.access(argv0, os.X_OK):
        cmd = [sys.executable] + sys.argv
    else:
        cmd = list(sys.argv)
    try:
        os.execv(cmd[0], cmd)
    except OSError:
        try:
            proc.spawn_detached(cmd)
        except OSError:
            pass


def _log_client_crash(sock_path: str, tb: str):
    """클라이언트 미처리 예외 트레이스백을 `<sock>.client.crash.log` 에 append 한다.
    클라는 Terminal 안에서 도는데 크래시하면 트레이스백이 터미널 스크롤백(휘발)으로만
    가 사후분석이 불가능했다(docs/INVESTIGATION §7.2-A). 디스크에 남겨 다음 재발 시
    원인을 즉시 잡게 한다. 로깅 자체는 절대 실패를 전파하지 않는다(best-effort)."""
    try:
        path = ipc.state_base(sock_path) + ".client.crash.log"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n==== {stamp} pid={os.getpid()} "
                    f"ver={version.code_version()} ====\n")
            f.write(tb)
    except Exception:
        pass


# 크래시 자동복구 상한: 같은 클라가 연쇄 크래시(뜨자마자 또 죽음)하면 무한 execv
# 루프가 되므로 막는다. execv 는 프로세스 메모리를 갈아끼우니 카운터는 환경변수로
# 대를 넘긴다. 빠른 재크래시만 누적하고(아래 _CRASH_LOOP_WINDOW), 이 횟수를 넘으면
# 자동복구를 멈춘 채 사용자에게 알리고 종료한다.
_CLIENT_CRASH_RELAUNCH_MAX = 5
# 이 시간(초) 안에 다시 크래시하면 '연쇄 크래시 루프'로 보고 카운트한다. 오래
# 정상 동작하다 죽은 건 새 카운트(1)로 리셋 — 정상 사용 중 어쩌다 난 크래시가
# 복구 예산을 소진하지 않게 한다.
_CRASH_LOOP_WINDOW = 30.0


def _crash_relaunch_count() -> int:
    try:
        return int(os.environ.get("PYTMUX_CLIENT_CRASH_N", "0"))
    except ValueError:
        return 0


def run_client(sock_path: str, session: str | None = None):
    import sys
    config = load_config()
    # East Asian Ambiguous 폭 자동감지(cellwidth): Textual 이 단말을 점유하기 전,
    # 여기서 CPR 로 단말이 모호폭을 2칸으로 그리는지 측정해 폭 모델(클라 합성·Rich/
    # Textual 측정·서버 pyte)을 일관 전환한다. 기본 narrow 면 패치 미설치라 무영향.
    try:
        from . import cellwidth
        from .launcher import detect_ambiguous_width
        mode = detect_ambiguous_width(config.get("ambiguous_width", "auto"))
        cellwidth.set_ambiguous_wide(mode == "wide")
        # 런타임 `:set ambiguous-width auto` 전환이 CPR 을 재프로브하지 않도록(Textual
        # 이 단말을 점유한 뒤라 위험) 시작 시 감지값을 캐시한다.
        config["_ambig_auto_wide"] = (mode == "wide")
    except Exception:
        pass
    app = build_client_app(sock_path, config, session)
    crashed = None
    start = time.monotonic()
    try:
        app.run()
    except Exception:    # Textual 밖으로 샌 미처리 예외 = 클라이언트 크래시
        crashed = traceback.format_exc()
    elapsed = time.monotonic() - start
    if crashed is not None:
        # 크래시: 트레이스백을 영속화하고(사후분석), 한도 내에서 새 클라로 자가
        # 재기동해 화면을 회복한다. 데몬(서버)이 셸/PTY/세션을 들고 있어 멀쩡하므로
        # 새 클라가 재attach 하면 작업이 그대로 이어진다(tmux 모델).
        _log_client_crash(sock_path, crashed)
        n = (_crash_relaunch_count() + 1) if elapsed < _CRASH_LOOP_WINDOW else 1
        if n <= _CLIENT_CRASH_RELAUNCH_MAX:
            os.environ["PYTMUX_CLIENT_CRASH_N"] = str(n)
            _relaunch_self()       # 새 클라로 재기동(서버 생존 → 보통 즉시 재attach)
            return
        # 상한 초과(연쇄 크래시): 자동복구 중단 — 무한 루프 방지, 사용자에게 알림.
        sys.stderr.write(
            "pytmux: 클라이언트가 반복 크래시해 자동복구를 멈춥니다.\n"
            f"  트레이스백: {ipc.state_base(sock_path)}.client.crash.log\n")
        sys.exit(1)
    # 정상 종료: 크래시가 아니므로 복구 카운터를 비운다(다음 세션은 깨끗이 시작).
    os.environ.pop("PYTMUX_CLIENT_CRASH_N", None)
    # 전체 재시작(restart-all): app 이 _relaunch 를 세우고 종료했으면, textual 이
    # 터미널을 정상복구한 지금(run 반환 후) 자신을 os.execv 로 교체해 새 클라 코드로
    # 다시 attach 한다(이미 re-exec 된 서버에 재접속해 셸/세션 보존).
    if getattr(app, "_relaunch", False):
        _relaunch_self()
