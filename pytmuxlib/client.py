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

from . import clientclip, clientrender, ipc, proc, usagelog, version
from .claude import saver_hook_events
from .clientutil import (  # noqa: F401  (클로저에서 이름으로 사용)
    COMMAND_NOARG, COMMAND_OPTIONS, COMMANDS, COMPLETIONS, DEFAULT_STYLE,
    MENU_ITEMS, MENU_TOGGLES, SAVER_CYCLES, SPECIAL,
    _BOX_BITS, _BOX_REV, _DATE_STRFTIME, _JAMO, _KEY_DIAG, _ONOFF,
    _TB_ACTIVE_STYLE, _TB_BORDER_STYLE, _TB_INACTIVE_STYLE, _TIME_STRFTIME,
    _char_cells, _darken_style, _fmt_tokens, _is_emoji, _with_reverse,
    has_hangul, hangul_to_qwerty, norm_sep,
    _normalize_key, _shell_argv, key_to_bytes, make_style, theme_color)
from .clientscreens import (  # noqa: F401  (클로저에서 push_screen 으로 사용)
    ChooseBufferScreen, ChooseLayoutScreen, ChooseTreeScreen, ClaudeSaverScreen,
    CommandListScreen, CommandOptionsScreen, ConfirmScreen, InfoScreen,
    InfoTabsScreen, MenuScreen, ModelCtxScreen, PermModeScreen, PromptScreen,
    RulesEditScreen, TokenLogScreen)
from .clientwidgets import (  # noqa: F401  (PytmuxApp.compose 에서 사용)
    MultiplexerView, StatusBar, TabBar)
from .keymap import _key_to_ctrl_bytes, _tmux_key_to_textual, load_config
from .protocol import MIN_H, MIN_W, PROTO_VERSION, read_msg, write_msg

# IPC 소켓 재접속 재시도 파라미터 — 흩어져 있던 매직 상수를 한곳에 모았다(M4 #30).
_RECONNECT_DELAY = 0.02            # 재시도 간격(초)
_RECONNECT_RETRIES_RESTART = 300  # 서버 re-exec 재기동 대기(~6s)
_RECONNECT_RETRIES_FORCE = 150    # degraded 강제 재접속(~3s, 서버는 살아 있음)


def build_client_app(sock_path: str, config: dict | None = None,
                     session_name: str | None = None):
    config = config or {}
    # 빌드 시점에만 무거운 textual 을 끌어온다(모듈 import 는 가볍게). 아래는 실제로
    # 이 함수/클로저에서 쓰는 심볼만 — 미사용 import(Segment/Strip/Widget/ModalScreen/
    # Horizontal/Vertical/Label/ListItem/ListView)는 제거(A1 정리). 해당 textual 모듈은
    # 어차피 clientwidgets/clientscreens 가 로드하므로 제거로 import 시간은 안 줄지만
    # 죽은 의존을 없애 가독성을 높인다(textual 코어 ~57ms 는 TUI 상 불가피 — A1 §8.4).
    from rich.style import Style
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.suggester import SuggestFromList
    from textual.widgets import Input

    class SepInsensitiveSuggester(SuggestFromList):
        """ghost 자동완성에서 공백·언더바·하이픈을 동일 취급한다(norm_sep).

        'rename_'·'rename ' 를 쳐도 'rename-tab' 를 제안 — 명령 검색이 구분자
        선택에 좌우되지 않게 한다. 후보·입력을 모두 norm_sep 로 통일해 prefix 비교."""
        def __init__(self, suggestions, *, case_sensitive=False):
            sugg = list(suggestions)
            super().__init__(sugg, case_sensitive=case_sensitive)
            # base 의 casefold 는 부모와 동일 규칙(case_sensitive=False → casefold).
            base = sugg if case_sensitive else [s.casefold() for s in sugg]
            self._sep_orig = sugg
            self._sep_norm = [norm_sep(s) for s in base]

        async def get_suggestion(self, value):
            # 부모 _get_suggestion 이 case_sensitive=False 면 value 를 이미 casefold 함.
            v = norm_sep(value)
            for orig, norm in zip(self._sep_orig, self._sep_norm):
                if norm.startswith(v):
                    return orig
            return None

    class PytmuxApp(App):
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
            # version 명령 팝업용(이 클라가 로드한 코드 버전·런치 시각). 버전 캡처는
            # ~수십 ms p4 호출이라 startup(첫 페인트) 핫패스를 막지 않게 on_mount 에서
            # executor 로 비동기 계산해 채운다(그 전까진 "…"). 업타임은 런치 시각 기준.
            self._boot_time = time.time()
            self._code_version = "…"
            # 작업 보존 재시작(re-exec): 서버가 {"t":"restarting"} 을 보내면 다음
            # 연결 끊김을 종료가 아닌 재접속으로 다룬다(docs/RESTART_SCENARIO.md ⓔ).
            self._reconnecting = False
            # 전체 재시작(restart-all): 서버 re-exec 시 in-place 재접속 대신 **클라
            # 자신도 relaunch**(새 클라 코드로 재attach)하려는 요청. 끊김 처리에서
            # _relaunch 를 세우고 종료하면 run_client 가 os.execv 로 새 클라를 띄운다.
            self._relaunch_on_restart = False
            self._relaunch = False
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
            #   copy-mode 선택 추출에서 자동 줄바꿈 줄을 한 줄로 잇는 데 쓴다(매 screen/
            #   screen-delta 메시지가 전체 리스트를 실어 보내므로 통째로 교체).
            self.mode = "normal"     # normal | prefix | scroll | prompt | display
            self._want_tree = False  # choose-tree 응답 대기
            self._tree_purpose = "choose"  # tree 응답 용도(choose|usage)
            self._want_buffers = False  # choose-buffer 응답 대기
            self._want_layouts = None  # 레이아웃 목록 응답 대기(모드: "new"/"over")
            self._want_token_log = False  # 토큰 로그 집계 팝업 응답 대기(#7)
            self.clock_panes = set()   # clock-mode 가 켜진 패널 id 집합
            self.calendar_panes = set()   # 달력 오버레이가 켜진 패널 id 집합
            self._menu_pane = None  # 컨텍스트 메뉴가 열린 대상 패널 id(배경 강조용)
            self._menu_open = False  # 컨텍스트 메뉴 표시 중(배경 dim 합성용)
            # Claude Code: 패널별 상태/마지막 프롬프트
            self.pane_claude = {}      # id -> {"claude", "prompt", "history"}
            self.claude_header_on = True  # 프롬프트 헤더 표시(claude-header on|off)
            self.single_border_on = True  # 단일 패널 테두리 표시(single-border on|off)
            self._claude_header_zones = {}  # id -> (x0,x1,y) 헤더 클릭존(히스토리 팝업)
            # Claude 패널 PTY 안에 그려지는 하단 footer 클릭존(§10 item 2/3): 권한모드
            # footer("auto mode on (shift+tab)") → 권한모드 선택 팝업, "Remote Control
            # active" → 원격제어 정보+토글([r]=/rc) 팝업. _composite 가 채운다.
            self._perm_zone = {}     # id -> (x0,x1,y) 권한모드 footer 클릭존
            self._remote_zone = {}   # id -> (x0,x1,y) 원격제어 표시 클릭존
            self._footer_hover = None  # 호버 중인 footer 클릭존 (pane_id, "perm"|"remote")(§10)
            self._hdr_focus = None      # ESC 모드 Claude 헤더 포커스 대상 pane id(#5)
            # ESC 모드에서 최하단 패널 ↓ → 하단 상태바 버튼 포커스(요청). 값은 현재
            # 포커스된 버튼 키(None=비활성). ←→ 로 버튼 순환, Enter 로 실행, ↑/Esc 복귀.
            self._status_focus = None
            self._claude_hidden_panes = set()  # 헤더를 숨긴 패널 id(#6 ② 팝업서 토글)
            self._tab_close_zone = None  # 현재 탭 닫기 [x] 영역 (x0, x1, y)
            self._active_hdr_row = None  # 활성 패널 프롬프트 헤더 행(닫기 [x] 위치, #15)
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
            # ---- 설정(config) 적용 ----
            self.prefix_key = config.get("prefix", "ctrl+b")
            self.prefix_bytes = _key_to_ctrl_bytes(self.prefix_key)
            self.prefix_enabled = True  # 중첩 시 F12 로 outer prefix 일시 해제
            self.bindings = config.get("bindings", {})
            self.mouse_enabled = config.get("mouse", True)
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
            self.mode_keys = config.get("mode_keys", "vi")
            self.status_position = config.get("status_position", "bottom")
            self.status_interval = config.get("status_interval", 15)
            self._status_timer = None
            self.set_titles = config.get("set_titles", False)
            self.title_fmt = config.get("title_fmt", "#S:#I:#W")
            self.aliases = config.get("aliases", {})
            self.hooks = config.get("hooks", {})
            self._attached = False
            self._composite_pending = False  # B9: 합성 코얼레싱 예약 플래그
            self._prev_winc = 0
            self._prev_bell = False
            # M16: 절감 신호 에스컬레이션 훅의 직전 전이 상태(상승 에지 1회 발화용, §8)
            self._saver_prev = {"budget_level": 0, "pending_kind": None,
                                "limit": False}
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
                self.exit(message="pytmux: 서버에 연결할 수 없습니다")
                return
            cols, rows = self._content_size()
            hello = {"t": "hello", "proto": PROTO_VERSION, "cols": cols, "rows": rows}
            tok = ipc.read_token(self.sock_path)   # 연결 인증(F1)
            if tok:
                hello["token"] = tok
            if self.session_name:
                hello["session"] = self.session_name
            await write_msg(self.writer, hello)
            self._start_reader()

        def on_unmount(self):
            # 마운트 시 끈 대체 스크롤 모드(1007)를 복원해 터미널을 원상태로 둔다.
            if getattr(self, "disable_alt_scroll", False):
                self._term_write("\x1b[?1007h")

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

        def _update_tabbar(self):
            """상태 갱신 시 탭바 데이터/표시 여부를 동기화. 표시가 바뀌면 뷰 크기가
            달라지므로 서버에 새 크기를 통지한다."""
            visible = self._tabbar_visible()
            new_active = self._active_tab_index()
            prev_active = next((t["index"] for t in self.tabbar.tabs
                                if t.get("active")), None)
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

        def _send_resize(self):
            if self.writer:
                cols, rows = self._content_size()
                import asyncio as _a
                _a.create_task(write_msg(
                    self.writer, {"t": "resize", "cols": cols, "rows": rows}))

        def confirm_popup(self, message, action, title="확인",
                          yes_label="닫기", danger=False):
            """중앙 확인 팝업을 띄우고, '예'면 action 실행."""
            def done(ok):
                if ok and action:
                    action()
            self.push_screen(
                ConfirmScreen(message, yes_label=yes_label, title=title,
                              danger=danger), done)

        def confirm_kill_tab(self):
            # 이 탭을 닫으면 pytmux 가 끝나는가 = 탭이 하나뿐인가(#16).
            last = len(self.tabbar.tabs) <= 1
            if last:
                msg = ("이 탭을 닫으면 pytmux 가 종료됩니다(모든 셸 종료). 닫을까요?")
                title = "pytmux 종료"
            else:
                msg = "이 탭을 닫을까요? 탭의 셸이 종료됩니다."
                title = "탭 닫기"
            self.confirm_popup(
                msg, action=lambda: self.send_cmd("kill_window"),
                title=title, yes_label="닫기", danger=last)

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

        def _status_buttons(self):
            """ESC 모드 하단 포커스에서 ←→ 로 순환할 대상 키 목록(현재 화면에 있는
            것만, 왼→오 시각 순서). 모델·토큰사용량은 Claude 활성 시, REC 는 캡처
            중일 때만. host(ssh:서버명)·clock(시계)·date(달력)는 우측 상태에 그려질
            때, perm("auto mode on" footer)은 활성 Claude 패널에 권한모드 footer 가
            보일 때만 편입한다(요청)."""
            sb = self.status
            btns = []
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
            act = self.layout.get("active")
            if act is not None and act in self._perm_zone:
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
                if cur == "model":
                    self.open_model_config()
                elif cur == "usage":
                    self.open_token_log()
                elif cur == "rec":
                    self.show_capture_info(self.status.capture_path,
                                           self.status.capture_size)
                elif cur == "host":
                    self.show_status_tabs(initial=2)   # 서버 탭(#12, host 클릭과 동일)
                elif cur == "clock":
                    self.toggle_clock(act)             # 시계 오버레이 토글
                elif cur == "date":
                    self.toggle_calendar(act)          # 달력 오버레이 토글
                elif cur == "perm":
                    self.open_perm_mode(act)           # 권한모드 선택 팝업
            elif k in ("up", "escape"):
                self._exit_status_focus()   # 패널로 복귀(esc 모드 유지)
                if k == "escape":
                    self._exit_esc()
            else:
                self._exit_status_focus()

        def _start_reader(self):
            """현재 self.reader 에 묶인 새 reader 태스크를 띄운다. 연결 세대(_conn_gen)
            를 올려 각 태스크가 자기 세대를 들고 돌게 한다 — 강제 재접속으로 소켓이
            교체되면 옛 태스크는 EOF 시 세대 불일치를 보고 조용히 종료한다."""
            self._conn_gen += 1
            self.run_worker(self._reader_task(self.reader, self._conn_gen),
                            exclusive=False)

        async def _reader_task(self, reader, gen):
            while True:
                msg = await read_msg(reader)
                if msg is None:
                    # 이 reader 가 이미 새 연결로 교체됐으면(강제 재접속) 옛 태스크는
                    # 조용히 종료 — self.exit() 로 앱을 닫지 않는다(§10 degraded 회복).
                    if gen != self._conn_gen:
                        return
                    # 작업 보존 재시작 중이면 종료 대신 재접속(ⓔ). 단 전체 재시작
                    # (restart-all)이면 in-place 재접속 대신 클라를 relaunch 한다 —
                    # run_client 가 app.run() 반환 후 os.execv 로 새 클라를 띄운다
                    # (terminal 은 textual 종료가 정상복구). 새 클라가 re-exec 된 서버에
                    # 재접속해 셸/세션은 보존되고 클라 코드까지 갱신된다.
                    if self._reconnecting:
                        if self._relaunch_on_restart:
                            self._relaunch = True
                            self.exit()
                        else:
                            await self._reconnect()
                        return
                    self.exit()
                    return
                self._dispatch(msg)

        async def _connect_and_hello(self, retries):
            """소켓 재연결 + hello 송신 공통 경로(재시작 재개·degraded 강제 재접속 공용).
            listen 이 열릴 때까지 retries 회(_RECONNECT_DELAY 간격) 재시도하고, 붙으면
            현재 크기로 hello 를 보낸다. 성공 True / 시간초과 False(호출부가 메시지 처리)."""
            for _ in range(retries):
                try:
                    self.reader, self.writer = await ipc.open_connection(
                        self.sock_path)
                    break
                except (ConnectionError, FileNotFoundError, OSError):
                    await asyncio.sleep(_RECONNECT_DELAY)
            else:
                return False
            cols, rows = self._content_size()
            hello = {"t": "hello", "proto": PROTO_VERSION, "cols": cols, "rows": rows}
            tok = ipc.read_token(self.sock_path)   # 연결 인증(F1)
            if tok:
                hello["token"] = tok
            if self.session_name:
                hello["session"] = self.session_name
            await write_msg(self.writer, hello)
            return True

        async def _reconnect(self):
            """서버가 re-exec 로 재기동되는 동안 같은 소켓으로 재접속한다(ⓔ).
            새 서버가 listen 을 다시 열 때까지 잠깐 재시도한 뒤 hello 로 재개."""
            self._reconnecting = False
            if not await self._connect_and_hello(_RECONNECT_RETRIES_RESTART):
                self.exit(message="pytmux: 서버 재접속 실패")
                return
            self.display_message("pytmux: 서버 재시작 완료 — 재접속됨")
            self._start_reader()

        async def _force_reconnect(self, reason="manual"):
            """정체/degraded 된 IPC 연결을 강제로 새로 세워 반응성을 회복한다(§10).

            서버(데몬)의 셸·PTY·세션은 **건드리지 않고** 클라↔서버 소켓만 교체한다 —
            ssh 전송이 정체돼 `read_msg` 가 무한 블록되고 degraded(빨간 외곽선)가
            고착될 때, 옛 소켓을 강제로 닫아 블록을 깨우고 새 연결로 hello 를 보내면
            서버가 `_send_full` 로 전체 화면/레이아웃을 재전송해 회복된다. 옛 reader
            태스크는 세대 불일치로 조용히 종료한다(앱은 안 닫힘). Claude 등 실행 중인
            앱은 PTY 안에서 계속 돌고 있었으므로 그대로 이어진다(tmux 모델).
            reason: "manual"(reconnect 명령) | "auto"(degraded 워치독)."""
            if self._force_reconnecting:
                return
            self._force_reconnecting = True
            try:
                self.display_message(
                    "pytmux: 재접속 중…" + (" (자동 회복)" if reason == "auto" else ""))
                # 옛 소켓을 강제로 닫아 블록된 read_msg 를 깨운다(옛 태스크는 세대
                # 불일치로 종료). writer/reader 가 같은 전송을 공유하므로 writer 만 닫음.
                old_w = self.writer
                self.writer = None
                if old_w is not None:
                    try:
                        old_w.close()
                    except OSError:
                        pass
                # 새 연결(서버는 살아 있으니 보통 즉시 — 잠깐 재시도) + hello.
                if not await self._connect_and_hello(_RECONNECT_RETRIES_FORCE):
                    self.display_message("pytmux: 재접속 실패 — 네트워크 확인")
                    return
                # 네트워크 상태 리셋: 새 채널이니 degraded 해제하고 표본 카운터 비움.
                self._net_ping_ts = None
                self._net_bad = self._net_good = 0
                if self._net_degraded:
                    self._net_degraded = False
                    self._composite()
                self.display_message("pytmux: 재접속됨 — 화면 재동기")
                self._start_reader()   # 새 reader 태스크(새 세대) — 서버 _send_full 수신
            finally:
                self._force_reconnecting = False

        def reconnect_now(self, reason="manual"):
            """강제 재접속을 워커로 시작한다(명령/워치독에서 호출). 이미 진행 중이면
            _force_reconnect 가 즉시 반환한다."""
            self.run_worker(self._force_reconnect(reason), exclusive=False)

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
                self._request_composite()
            elif t == "screen-delta":
                # B2: 바뀐 행만 받아 캐시된 rows 에 행 단위로 적용. base 가 없는
                # per-client 모델이라 직전 full/델타가 만든 캐시에 그대로 덮어쓴다.
                pid = msg["pane"]
                prev = self.pane_content.get(pid)
                rows = list(prev[0]) if prev else []
                for y, segs in msg["rows"]:
                    if 0 <= y < len(rows):
                        rows[y] = segs
                    elif y == len(rows):
                        rows.append(segs)
                self.pane_content[pid] = (rows, msg.get("cursor"))
                self.pane_wrap[pid] = set(msg.get("wrap") or ())
                self._request_composite()
            elif t == "status":
                self.status.update_status(msg)
                # claude-header 전역 표시 상태를 서버 opts.json 권위값으로 반영(#6 ③)
                if "claude_header" in msg:
                    self.claude_header_on = bool(msg["claude_header"])
                # single-border 전역 상태도 서버 권위값으로 반영(opts.json 영속)
                if "single_border" in msg:
                    self.single_border_on = bool(msg["single_border"])
                # Claude 시작 규칙(#27): 에디터 초기값으로 쓰려고 마지막 값을 보관.
                if "claude_rules" in msg:
                    self._claude_rules = msg.get("claude_rules", "")
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
                self._update_claude(msg.get("panes_claude", []))
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
                # M16: 절감 신호 전이 → PTY 밖 에스컬레이션 훅(자리 비움 대응, §8).
                for ev, env in saver_hook_events(self._saver_prev, msg):
                    self._fire_hook(ev, env=env)
            elif t == "tree":
                if self._want_tree:
                    self._want_tree = False
                    purpose = getattr(self, "_tree_purpose", "choose")
                    if purpose == "status_tabs":
                        self._open_status_tabs(msg)
                    elif purpose == "usage":
                        self._open_usage_tree(msg)
                    else:
                        self._open_choose_tree(msg)
            elif t == "token_log":
                if getattr(self, "_want_token_log", False):
                    self._want_token_log = False
                    self.push_screen(TokenLogScreen(
                        msg.get("records") or [],
                        usage=getattr(self.status, "usage_limits", None)))
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
            elif t == "captured":
                self.display_message(f"{msg.get('chars', 0)} chars 버퍼에 캡처됨")
            elif t == "restarting":
                # 작업 보존 재시작 통지(ⓔ): 곧 끊길 연결을 재접속으로 다룬다.
                self._reconnecting = True
                self.display_message("pytmux: 서버 재시작 중…")
            elif t == "bye":
                self.exit(message="pytmux: 서버가 종료되었습니다")

        # ---- Claude Code 마지막 프롬프트 스티키 헤더 ----
        def _update_claude(self, panes_claude):
            # history 가 빠진 항목(§4.5: 서버가 변할 때만 실음)은 직전에 받은
            # history 를 유지한다 — 매 status 마다 30개 프롬프트를 재전송하지 않게.
            new = {}
            for e in panes_claude:
                pid = e["id"]
                if "history" not in e:
                    prev = self.pane_claude.get(pid)
                    if prev is not None and "history" in prev:
                        e = {**e, "history": prev["history"]}
                new[pid] = e
            self.pane_claude = new

        def set_claude_header(self, on: bool):
            # claude-header on|off — 프롬프트 헤더 표시 토글(전역). 낙관적으로 즉시
            # 반영하고 서버에 보내 opts.json 에 영속(#6 ③) — 서버가 status 로 회신.
            self.claude_header_on = on
            self.send_cmd("set_claude_header", value=bool(on))
            self._composite()

        def toggle_header_hidden(self, pane_id):
            # 특정 패널의 헤더만 숨기거나 다시 보이게(#6 ② — 히스토리 팝업서 토글).
            # 패널별·세션 한정(전역 claude-header off 와 별개). 숨겨도 prompt-history
            # 명령이나 ESC h 로 팝업을 열어 다시 보이게 할 수 있다.
            if pane_id in self._claude_hidden_panes:
                self._claude_hidden_panes.discard(pane_id)
            else:
                self._claude_hidden_panes.add(pane_id)
            self._composite()

        def _capture_info_lines(self, path=None, size=None):
            # REC(출력 캡처) 정보 줄. 인자를 안 주면 상태줄에 마지막으로 온 값을 쓴다.
            # 맨 앞에 현재 ON/OFF 를 보여 화면에서 [c] 로 토글한 결과가 바로 반영된다.
            on = bool(getattr(self.status, "capture", False))
            head = "상태: ON (캡처 중)" if on else "상태: OFF"
            if path is None:
                path = self.status.capture_path
                size = self.status.capture_size or 0
            if not on:
                return [head, "(캡처 꺼짐 — REC 미표시)"]
            if not path:
                return [head, "(캡처 파일 준비 중…)"]
            return [head,
                    f"파일: {path}",
                    f"크기: {size:,} bytes ({size / 1024:,.1f} KiB)",
                    f"탭 매핑: {os.path.join(os.path.dirname(path), 'sessions.log')}"]

        def show_capture_info(self, path, size):
            # REC 클릭 → 통합 상태 팝업의 '출력 캡처' 탭으로 연다(#10 — 토큰 사용량과
            # 한 팝업으로 합침). 캡처 줄은 즉시 만들고, 토큰 트리는 서버에서 받아온다.
            self._status_cap_lines = self._capture_info_lines(path, size)
            self._status_tab_initial = 0   # 0 = 캡처 탭(REC 가 왼쪽)
            self.request_tree(purpose="status_tabs")

        def show_status_tabs(self, initial=0):
            self._status_cap_lines = self._capture_info_lines()
            self._status_tab_initial = initial
            self.request_tree(purpose="status_tabs")

        def open_prompt_history(self, pane_id=None):
            # Claude 프롬프트 히스토리 팝업(시간순). 헤더 클릭/명령으로 연다(#7).
            # [h] 로 이 패널 헤더 숨김/표시 토글(#6 ②).
            pid = pane_id if pane_id is not None else self.layout.get("active")
            info = self.pane_claude.get(pid) or {}
            hist = info.get("history") or ([info["prompt"]]
                                           if info.get("prompt") else [])
            # 2칼럼 행: (번호, 본문) — 본문이 여러 줄이어도 번호 칼럼 아래로 새지
            # 않고 본문 칼럼 안에 머문다(사용자 요청). 번호는 우측정렬 폭 보존.
            rows = [(f"{i + 1}.", h) for i, h in enumerate(hist)]
            hidden = pid in self._claude_hidden_panes
            # 맨 나중에 입력한(최신) 프롬프트를 열 때 강조(#17). hist 는 시간순(오래된→
            # 최신)이라 마지막 프롬프트(len(hist)-1)를 선택해 그리로 스크롤한다.
            latest = (len(hist) - 1) if hist else None
            # §10-A #8: 마지막 항목과 [h] footer 사이에 구분선(─, 전폭 문자열 행).
            # nav 에서 구분선은 건너뛰므로, 마지막 프롬프트에서 ↓ 한 번에 [h] 로 점프.
            rows.append("─" * 24)
            rows.append("  [h] 이 헤더 " + ("다시 표시" if hidden else "숨기기"))
            self.push_screen(InfoScreen(
                [], title="프롬프트 히스토리(시간순)",
                hide_key="h", hide_cb=lambda: self.toggle_header_hidden(pid),
                initial_index=latest, max_width=110,   # 좌우로 넓게(#17)
                col_rows=rows))                        # 번호/본문 2칼럼 표시

        def open_model_config(self):
            """Claude 모델·컨텍스트 변경 팝업(요청). 상태줄 모델 배지 클릭 / `model`
            명령 / esc 모드 상태바 포커스로 연다. 고른 값은 활성 패널에 '/model <이름>'
            (+컨텍스트 토큰)으로 주입한다 — 사용자가 직접 친 것과 같은 입력 경로."""
            cur = getattr(self.status, "claude_model", None)
            self.push_screen(ModelCtxScreen(cur), self._apply_model_config)

        def _apply_model_config(self, res):
            if not res:
                return
            model, ctx = res
            arg = model if ctx in (None, "default") else f"{model} {ctx}"
            # '/model <이름>' + Enter 주입(사용자 확인). 짧은 슬래시 명령이라 한 번에.
            self.send_input(("/model " + arg + "\r").encode("utf-8"))
            self.display_message(f"/model {arg} 적용 요청")

        def open_perm_mode(self, pane_id):
            """Claude 권한모드 선택 팝업을 연다(하단 footer 클릭, §10 item 2). 현재
            모드(서버가 status 로 보낸 perm_mode)를 표시하고, 고른 목표를 서버로
            보내면 서버가 shift+tab 폐루프로 그 모드까지 순환 주입한다."""
            info = self.pane_claude.get(pane_id) or {}
            current = info.get("perm_mode")
            zone = self._perm_zone.get(pane_id)
            anchor_y = zone[2] if zone else None   # 클릭한 footer 행 → 팝업 세로 위치 기준
            anchor_x = zone[0] if zone else None   # footer 시작 x → 팝업 좌측 정렬 기준(#2)
            # 클릭한 그 줄(auto mode on 등)은 팝업 dim 에서 제외해 밝게 둔다(#29).
            self._undim_rows = {anchor_y} if anchor_y is not None else set()

            def _chosen(target):
                self._undim_rows = set()           # 닫히면 dim 제외 해제
                if target:
                    self.send_cmd("set_claude_perm_mode", id=pane_id,
                                  target=target)
                    self.display_message(f"권한모드 → {target} 전환 중…")
            self.push_screen(PermModeScreen(current, anchor_y=anchor_y,
                                            anchor_x=anchor_x), _chosen)

        def _toggle_remote_control(self, pane_id):
            """원격 제어 토글: 해당 Claude 패널에 `/rc` 슬래시 명령+Enter 를 주입한다.
            Claude Code CLI 가 `/rc` 로 원격 제어를 켜고 끈다 — 사용자가 직접 친 것과
            동일한 입력 경로(서버가 그 패널 PTY 에 그대로 쓴다)."""
            if self.writer and pane_id is not None:
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": pane_id,
                    "data": base64.b64encode(b"/rc\r").decode("ascii")}))

        def open_remote_control(self, pane_id):
            """Claude 원격제어('Remote Control active') 정보+토글 팝업(§10 item 3).
            원격 제어는 Claude Code CLI 의 `/rc` 슬래시 명령으로 켜고 끌 수 있으므로,
            이 팝업에서 [r] 로 바로 토글한다(해당 패널에 `/rc` 주입)."""
            lines = [
                "이 패널의 Claude Code 가 데스크탑 앱 '원격 제어'로 연결돼 있습니다.",
                "(패널 화면의 'Remote Control active' 표시)",
                "",
                "• 원격 제어는 Claude Code CLI 의 '/rc' 명령으로 켜고 끕니다.",
                "  → 이 화면에서 [r] 키로 바로 토글합니다(해당 패널에 /rc 주입).",
                "• 원격 제어로 입력된 프롬프트도 상단 프롬프트 헤더에 반영됩니다.",
                "",
                "[r] 원격 제어 토글(/rc)   ·   닫기: Esc 또는 바깥 클릭.",
            ]
            self.push_screen(InfoScreen(
                lines, title="원격 제어(Remote Control)",
                hide_key="r",
                hide_cb=lambda: self._toggle_remote_control(pane_id)))

        def _draw_claude_headers(self, cells, W, H):
            """Claude Code 패널 내부 맨 윗줄에 마지막 프롬프트를 스티키 헤더로 표시.
            스크롤과 무관(합성 시 항상 내용 최상단에 덮어 그림). 표시 여부는 전역
            옵션 claude_header_on(명령 `claude-header on|off`)으로 끄고 켠다."""
            self._claude_header_zones = {}
            # 활성 패널 헤더(프롬프트) 행 — 닫기 [x] 를 이 행으로 올려 그리기 위해
            # 기록한다(#15). 헤더가 없으면 None → [x] 는 콘텐츠 첫 행에 그대로.
            self._active_hdr_row = None
            active = self.layout.get("active")
            if not self.claude_header_on or not self.pane_claude:
                return
            # 헤더 배경은 진한 파랑(primary-darken-2) — 본문/활성 테두리(primary)보다
            # 한 단계 어둡게. ESC 모드 헤더 포커스(#5)면 강조색(accent)으로 구분한다.
            base_st = Style(color="white",
                            bgcolor=theme_color(self, "primary-darken-2"),
                            bold=True)
            # 비활성 패널의 헤더 바는 한 단계 더 어둡게(요청) — 활성(밝은 파랑)과
            # 비활성을 더 또렷이 구분한다. 활성 패널만 base_st(진파랑)를 쓴다.
            inactive_hdr_st = Style(color="white",
                                    bgcolor=theme_color(self, "primary-darken-3"),
                                    bold=True)
            focus_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                             bold=True)
            for p in self.layout.get("panes", []):
                if not p.get("claude_hdr"):   # 서버가 헤더 행을 예약한 패널만(#1)
                    continue
                if p["id"] in self._claude_hidden_panes:   # 팝업서 숨긴 헤더(#6 ②)
                    continue
                info = self.pane_claude.get(p["id"])
                if not info or not info.get("claude") or not info.get("prompt"):
                    continue
                # 서버가 내용 영역을 한 행 내렸으므로(cy=p["y"]) 헤더는 그 위 한 줄
                # (p["y"]-1, 예약된 행)에 그린다(#1).
                cx, cy, cw = p["x"], p["y"] - 1, p["w"]
                if cw < 6 or not (0 <= cy < H):
                    continue
                if p["id"] == self._hdr_focus:
                    hdr_st = focus_st
                elif p["id"] == active:
                    hdr_st = base_st
                else:
                    hdr_st = inactive_hdr_st   # 비활성 헤더 바는 더 어둡게(요청)
                for xx in range(cx, min(cx + cw, W)):   # 헤더 배경
                    cells[cy][xx] = (" ", hdr_st)
                # 헤더 본문 전체가 클릭존(프롬프트 히스토리 팝업, #7)
                self._claude_header_zones[p["id"]] = (cx, min(cx + cw, W), cy)
                text_start = cx + 1                      # 좌측 1칸 여백
                # 활성 패널은 이 헤더 행 우측 끝에 닫기 [x](3칸)가 올라오므로(#15),
                # 프롬프트 본문은 그 직전 한 칸까지만(= 3 + 1 칸 비움) 늘어나게 한다.
                if p["id"] == active:
                    self._active_hdr_row = cy
                    budget = max(0, cw - 1 - 4)
                    # 닫기 [x](우측 3칸)와 프롬프트 헤더 사이 한 칸은 헤더색이 아닌
                    # 터미널 배경으로 비운다(빈 Style → 터미널 기본 배경, #).
                    gapx = cx + cw - 4
                    if 0 <= gapx < W:
                        cells[cy][gapx] = (" ", Style())
                else:
                    budget = max(0, cw - 1)
                gx = text_start
                for chh in "▷ " + info["prompt"]:
                    wch = _char_cells(chh)
                    if gx - text_start + wch > budget:
                        break
                    if 0 <= gx < W:
                        cells[cy][gx] = (chh, hdr_st)
                        if wch == 2 and gx + 1 - text_start < budget and \
                                0 <= gx + 1 < W:
                            cells[cy][gx + 1] = ("", hdr_st)
                    gx += wch

        # ---- clock-mode(패널 전체를 덮는 큰 시계) ----
        def toggle_clock(self, pane_id):
            if pane_id is None:
                return
            if pane_id in self.clock_panes:
                self.clock_panes.discard(pane_id)
            else:
                self.clock_panes.add(pane_id)
                self.calendar_panes.discard(pane_id)  # 한 패널엔 한 오버레이만
            self._composite()

        def toggle_calendar(self, pane_id):
            if pane_id is None:
                return
            if pane_id in self.calendar_panes:
                self.calendar_panes.discard(pane_id)
            else:
                self.calendar_panes.add(pane_id)
                self.clock_panes.discard(pane_id)     # 한 패널엔 한 오버레이만
            self._composite()

        def set_clock(self, pane_id, on):
            """시계 오버레이를 명시적으로 켜거나(open-clock) 끈다(close-clock).
            토글이 아니라 멱등 — 이미 원하는 상태면 그대로. open 시 같은 패널의
            달력은 닫는다(한 패널엔 한 오버레이)."""
            if pane_id is None:
                return
            if on:
                self.clock_panes.add(pane_id)
                self.calendar_panes.discard(pane_id)
            else:
                self.clock_panes.discard(pane_id)
            self._composite()

        def set_calendar(self, pane_id, on):
            """달력 오버레이를 명시적으로 켜거나(open-calendar) 끈다(close-calendar).
            멱등 — open 시 같은 패널의 시계는 닫는다."""
            if pane_id is None:
                return
            if on:
                self.calendar_panes.add(pane_id)
                self.clock_panes.discard(pane_id)
            else:
                self.calendar_panes.discard(pane_id)
            self._composite()

        def _close_overlay(self, pane_id):
            """해당 패널의 시계/달력 오버레이를 닫는다. 닫았으면 True(없으면 False).
            오버레이 [x] 버튼을 폐지하고 패널 클릭/Shift+ESC 로 닫기 위한 공용 경로."""
            if pane_id is not None and (pane_id in self.clock_panes
                                        or pane_id in self.calendar_panes):
                self.clock_panes.discard(pane_id)
                self.calendar_panes.discard(pane_id)
                self._composite()
                return True
            return False

        def _close_active_overlay(self):
            """활성 패널의 시계/달력 오버레이를 닫는다(Shift+ESC). 닫았으면 True."""
            return self._close_overlay(self.layout.get("active"))

        def _clock_tick(self):
            # 1초마다: 시계/달력 오버레이가 있으면 갱신(뒤 화면도 함께 다시 합성).
            # 달력은 자정을 넘어가면 '오늘' 강조가 다음 날로 이동한다.
            if self.clock_panes or self.calendar_panes:
                self._composite()

        # ---- 네트워크 응답성 측정(§10): ping/pong RTT + 히스테리시스 ----
        def _net_ping(self):
            """주기적으로 서버에 ping 을 보내 RTT 를 잰다. 직전 ping 이 임계 안에
            응답(pong)되지 않았으면 그 자체를 느림 표본으로 치고(채널 지연/정체) 새
            ping 을 보낸다. 임계 안에 아직 대기 중이면 새 ping 을 보류(중복 방지)."""
            if not self.writer:
                return
            now = time.monotonic()
            if self._net_ping_ts is not None:
                if now - self._net_ping_ts <= self.net_rtt_threshold:
                    return                       # 응답 대기 중(임계 내) — 보류
                self._net_sample(now - self._net_ping_ts)   # 미응답 = 느림 표본
            self._net_ping_ts = now
            asyncio.create_task(write_msg(self.writer, {"t": "ping", "ts": now}))

        def _on_pong(self):
            """서버 pong 수신: 미응답 ping 의 왕복지연을 표본으로 기록."""
            if self._net_ping_ts is not None:
                rtt = time.monotonic() - self._net_ping_ts
                self._net_ping_ts = None
                self._net_sample(rtt)

        def _net_sample(self, rtt):
            """RTT 표본 하나로 히스테리시스를 갱신한다. 임계 초과가 net_bad_n 회
            연속이면 degraded ON, 임계 이하가 net_good_n 회 연속이면 OFF(깜빡임 방지).
            상태가 바뀌면 외곽선 색을 다시 그린다. 또 degraded 가 net_recover_n 회
            연속(표시 임계보다 훨씬 길게) 지속되면 IPC 강제 재접속으로 회복을 시도한다
            (§10 — 서버 PTY/세션 보존)."""
            self._net_last_rtt = rtt
            if rtt > self.net_rtt_threshold:
                self._net_bad += 1
                self._net_good = 0
            else:
                self._net_good += 1
                self._net_bad = 0
            new = self._net_degraded
            if self._net_bad >= self.net_bad_n:
                new = True
            elif self._net_good >= self.net_good_n:
                new = False
            if new != self._net_degraded:
                self._net_degraded = new
                self._composite()
            # 자동 회복(§10): 느림이 충분히 오래 지속되면 강제 재접속(중복 방지는
            # _force_reconnect 가). 재시도 간격을 두려고 카운터를 비워 다음 회복까지
            # 다시 net_recover_n 표본을 모은다.
            if (self.net_auto_reconnect and not self._force_reconnecting
                    and self._net_bad >= self.net_recover_n):
                self._net_bad = 0
                self.reconnect_now("auto")

        # 셀 그리드 합성 헬퍼는 앱 비의존이라 clientrender.py 로 분리(#12). 호출은
        # clientrender.put_cell(...) 로 직접 한다(과거 self._put_cell).
        def _draw_clock_overlay(self, cells, W, H, active):
            """clock-mode 패널을 큰 시계로 덮는다(테마 Style 해석 후 clientrender 의
            앱-비의존 자유함수에 위임, #12). 뒤의 패널 출력은 흐리게(dim) 계속 보인다.
            닫기는 패널 클릭 또는 (활성 패널일 때) Shift+ESC — 좁은 화면에서 잘 안
            보이던 우상단 [x] 버튼은 폐지했다."""
            if not self.clock_panes:
                return
            digit_st = Style(color=theme_color(self, "success"), bold=True)
            clientrender.draw_clock_overlay(
                cells, self.layout.get("panes", []), self.clock_panes,
                W, H, digit_st)

        def _draw_calendar_overlay(self, cells, W, H, active):
            """달력 모드 패널을 이번 달 달력으로 덮는다(테마 Style 해석 후 clientrender
            자유함수에 위임, #12). 뒤의 패널 출력은 흐리게(dim) 계속 보이고, 오늘
            날짜는 강조. 닫기는 패널 클릭·(활성 패널일 때) Shift+ESC·상태줄 날짜
            재클릭/명령 — 우상단 [x] 버튼은 폐지했다."""
            if not self.calendar_panes:
                return
            styles = {
                "day": Style(color=theme_color(self, "foreground")),
                "title": Style(color=theme_color(self, "success"), bold=True),
                "today": Style(color="black",
                               bgcolor=theme_color(self, "success"), bold=True),
                "big_today": Style(color=theme_color(self, "success"), bold=True),
                "border": Style(color=theme_color(self, "accent")),
            }
            clientrender.draw_calendar_overlay(
                cells, self.layout.get("panes", []), self.calendar_panes,
                W, H, styles)

        def _scan_footer_zones(self, p, rows, W, H):
            """Claude 패널 content 줄에서 ① 권한모드 footer(클릭→권한모드 팝업, item 2)
            와 ② 'Remote Control active'(클릭→원격제어 팝업, item 3)를 찾아 클릭존을
            등록한다(§10). 패널 content 좌표(ry)를 화면 좌표(gy)로 매핑하고, 가장 아래
            매치를 채택한다(footer 는 하단). Claude 패널만 대상."""
            ci = self.pane_claude.get(p["id"])
            if not (ci and ci.get("claude")):
                return
            for ry, row in enumerate(rows):
                if ry >= p["h"]:
                    break
                gy = p["y"] + ry
                if not (0 <= gy < H):
                    continue
                text = "".join(seg[0] for seg in row)
                low = text.lower()
                stripped = text.strip()
                if not stripped:
                    continue
                # 줄의 실제 글자 범위(앞뒤 공백 제외)를 클릭존 x 범위로 — 와이드 인지.
                lead = len(text) - len(text.lstrip())
                x0 = p["x"] + sum(_char_cells(c) for c in text[:lead])
                x1 = min(p["x"] + p["w"],
                         x0 + sum(_char_cells(c) for c in stripped))
                # 권한모드 footer(claude.py:claude_perm_mode 와 같은 신호)
                if ("shift+tab to" in low or "mode on (shift" in low
                        or "⏵⏵" in text or "auto-accept" in low):
                    self._perm_zone[p["id"]] = (x0, x1, gy)
                if "remote control" in low:
                    self._remote_zone[p["id"]] = (x0, x1, gy)

        def _footer_zone_at(self, x, y):
            """좌표 (x,y) 가 Claude footer 클릭존(권한모드/원격제어) 안이면
            (pane_id, "perm"|"remote") 반환, 아니면 None(§10 호버 강조·클릭 공용)."""
            for pid, (zx0, zx1, zy) in self._perm_zone.items():
                if zy == y and zx0 <= x < zx1:
                    return (pid, "perm")
            for pid, (zx0, zx1, zy) in self._remote_zone.items():
                if zy == y and zx0 <= x < zx1:
                    return (pid, "remote")
            return None

        def _composite(self):
            W = self.layout.get("cols", self.size.width)
            H = self.layout.get("rows", max(1, self.size.height - 1))
            cells = [[(" ", DEFAULT_STYLE) for _ in range(W)] for _ in range(H)]
            active = self.layout.get("active")
            # Claude footer 클릭존(§10 item 2/3) 재계산 — 매 합성마다 비우고 채운다.
            self._perm_zone = {}
            self._remote_zone = {}
            for p in self.layout.get("panes", []):
                content = self.pane_content.get(p["id"])
                if not content:
                    continue
                rows, cursor = content
                self._scan_footer_zones(p, rows, W, H)
                for ry, row in enumerate(rows):
                    if ry >= p["h"]:
                        break
                    gy = p["y"] + ry
                    if not (0 <= gy < H):
                        continue
                    cx = p["x"]
                    for text, style_d in row:
                        st = make_style(style_d)
                        for chh in text:
                            if cx - p["x"] >= p["w"]:
                                break
                            if 0 <= cx < W:
                                cells[gy][cx] = (chh, st)
                            wch = _char_cells(chh)
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
            # 패널 테두리 박스: 비활성=회색, 활성=파란색. 경계 셀은 인접 패널이
            # 공유하므로, 비활성 박스를 먼저 그리고 활성 박스를 마지막에 덮어
            # 활성 패널의 경계 전체가 파란색이 되도록 한다.
            inactive_box = Style(color="grey42")
            active_box = Style(color=theme_color(self, "primary"), bold=True)
            # 패널 선택 깜빡임(ESC 모드 방향키): 위상 on 일 때 그 패널 테두리를
            # warning 색으로 그려 active(파랑)와 교차 점멸시킨다(선택 가시화, 요청).
            flash_box = Style(color=theme_color(self, "warning"), bold=True)
            # 네트워크 응답성 저하(§10): RTT 히스테리시스로 degraded 면 모든 패널
            # 테두리를 error(빨강)로 덮어 사용자에게 알린다(회복되면 원복). 단일
            # ssh 채널을 전 패널이 공유하므로 전 패널 공통 상태.
            if self._net_degraded:
                err = theme_color(self, "error")
                inactive_box = Style(color=err)
                active_box = Style(color=err, bold=True)
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
                    conn = Style(color=theme_color(self, "primary"), bgcolor=None)
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
            # Claude Code 마지막 프롬프트 스티키 헤더(내용 최상단)
            self._draw_claude_headers(cells, W, H)
            # 현재 탭 닫기 [x]: 콘텐츠 영역 오른쪽 위 모서리(상단 테두리 위)
            self._draw_tab_close(cells, W, H)
            # clock-mode / 달력 오버레이(패널 전체 덮기, 뒤 화면 dim)
            self._draw_clock_overlay(cells, W, H, active)
            self._draw_calendar_overlay(cells, W, H, active)
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
            # Claude footer(권한모드/원격제어) 클릭존 호버 강조(§10): 클릭 가능 영역
            # 임을 알리려 그 줄 배경만 한 톤 입힌다(글자색 유지). 클릭존은 위에서 막
            # 재계산됐으므로 호버 대상이 아직 유효할 때만 칠한다(떨림 없음).
            # 호버, 또는 ESC 모드에서 "auto mode on"(perm) 포커스(요청) 시 강조.
            _fh = self._footer_hover
            if _fh is None and self._status_focus == "perm":
                _act = self.layout.get("active")
                if _act is not None and _act in self._perm_zone:
                    _fh = (_act, "perm")
            if _fh is not None:
                _fpid, _fkind = _fh
                _fzone = (self._perm_zone if _fkind == "perm"
                          else self._remote_zone).get(_fpid)
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
            # Shift+드래그 패널 swap 중: 들고 있는 소스 패널은 흐리게(dim), 놓을
            # 대상 패널은 배경 강조(놓으면 두 패널이 자리를 맞바꾼다).
            if self.view._pane_swap is not None:
                stint = Style(bgcolor=theme_color(self, "warning"))
                for p in self.layout.get("panes", []):
                    if p["id"] == self.view._pane_swap:
                        darken = True       # 들고 있는 소스 패널: 실색 블렌드로 흐리게
                    elif p["id"] == self.view._pane_swap_over:
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
            if len(self.screen_stack) > 1:
                for yy in range(H):
                    if yy in self._undim_rows:   # 클릭 원천 줄은 밝게 유지(#29)
                        continue
                    row = cells[yy]
                    for xx in range(W):
                        ch, st = row[xx]
                        if ch and _is_emoji(ch):
                            ch = "·"
                        row[xx] = (ch, _darken_style(st))
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
            r = super().pop_screen(*args, **kwargs)
            if getattr(self, "view", None) is not None:
                self._composite()
                self.call_after_refresh(self._composite)
            return r

        def _draw_tab_close(self, cells, W, H):
            """현재 탭(윈도우) 닫기 [x] 버튼을 **활성 패널의 외곽선 안쪽** 우상단(콘텐츠
            영역 첫 행 오른쪽 끝)에 그린다(§10 #15 — 이전엔 화면 상단 테두리 행 0 에
            얹혀 있었다). 활성 패널의 실제 x/y/w/h 를 써서 분할(split) 상태에서도 그
            패널 테두리 안쪽에 붙는다. _draw_claude_headers 뒤에 호출돼 헤더 위에 그려
            진다(겹치면 [x] 가 보이도록)."""
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
            if self._hdr_focus == "close":
                st = Style(color="black", bgcolor=theme_color(self, "accent"),
                           bold=True)
            else:
                st = Style(color="white", bgcolor=theme_color(self, "error"),
                           bold=True)
            # 활성 패널에 프롬프트 헤더가 있으면 그 행(한 줄 위)에, 없으면 콘텐츠 첫
            # 행에 그린다(#15 — 닫기 [x] 를 프롬프트 행으로 이동). 헤더 본문은 위에서
            # 이 [x] 직전까지만 늘어나도록 budget 을 줄여 둔다.
            by = self._active_hdr_row if self._active_hdr_row is not None else py
            bx0 = px + pw - 3       # 콘텐츠 우측 끝 3칸("[x]") — 우측 테두리 안쪽
            if not (0 <= by < H):
                return
            for j, chh in enumerate("[x]"):
                gx = bx0 + j
                if 0 <= gx < W:
                    cells[by][gx] = (chh, st)
            self._tab_close_zone = (bx0, bx0 + 3, by)

        # ---- 송신 헬퍼 ----
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
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": self.layout.get("active"),
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

        # ---- 복사/버퍼 ----
        # OS 클립보드 입출력은 앱 상태 비의존이라 clientclip.py 모듈 자유함수로
        # 분리했다(#12). 클로저는 거기에 위임만 한다.
        def copy_text(self, text):
            # 서버 페이스트 버퍼 + OS 클립보드 양쪽에 저장
            self.send_cmd("set_buffer", text=text)
            clip = clientclip.copy(text)
            self.display_message(
                f"{len(text)} chars 복사됨" + (" (클립보드)" if clip else ""))

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
            self.display_message("클립보드 붙여넣기 중… 잠시만요 (ESC 로 빠져나가기)")
            self.run_worker(self._do_paste_clipboard(), exclusive=False)

        async def _do_paste_clipboard(self):
            try:
                txt = await asyncio.to_thread(clientclip.paste)
                if txt:
                    self.send_cmd("paste", text=txt)
                    return
                if await asyncio.to_thread(clientclip.has_image):
                    path = await asyncio.to_thread(clientclip.save_image)
                    if path:
                        # 경로를 붙여넣어 앱이 첨부 이미지로 인식하게 한다(결정 ①).
                        self.send_cmd("paste", text=path)
                        self.display_message(f"클립보드 이미지 → 경로 붙여넣기: {path}")
                        return
                    # 폴백: 내부 앱이 공유 클립보드에서 직접 읽도록 Alt+V.
                    self.send_input(b"\x1bv")   # ESC v = Alt+V
                    self.display_message("이미지 붙여넣기 → 내부 앱(Alt+V)")
                    return
                self.display_message("클립보드가 비어있거나 읽을 수 없음")
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

        # ---- choose-tree ----
        def request_tree(self, purpose="choose"):
            self._want_tree = True
            self._tree_purpose = purpose   # "choose"(전환/종료) | "usage"(토큰 보기)
            self.send_cmd("request_tree")

        def open_claude_usage_tree(self):
            # 토큰 사용량 클릭 → 통합 상태 팝업의 '토큰 사용량' 탭으로 연다(#10).
            self.show_status_tabs(initial=1)   # 1 = 토큰 탭(오른쪽)

        def open_token_log(self):
            # 토큰 사용량 영속 로그 집계 팝업(#7). 서버에 최근 로그를 요청하고,
            # 응답(t==token_log)이 오면 TokenLogScreen 으로 시간/일/주/월×계정
            # (=클라이언트) 집계. 상태바 Σ 클릭의 진입점이기도 하다.
            self._want_token_log = True
            self.send_cmd("request_token_log", limit=5000)

        def open_version(self):
            # version 명령: 서버에 버전/업타임을 요청하고(t==version 회신), 클라 자신의
            # 버전/업타임과 합쳐 팝업(InfoScreen)을 띄운다.
            self._want_version = True
            self.send_cmd("request_version")

        @staticmethod
        def _client_relaunch_ok():
            """restart-all 의 클라 relaunch(os.execv)가 인자를 해석할 수 있는지(run_client
            의 재실행 로직과 동일 판정)."""
            import sys
            a0 = sys.argv[0]
            return bool(a0) and (a0.endswith(".py") or os.access(a0, os.X_OK))

        def open_restart_check(self):
            # restart-check: 작업 보존 전체 재시작이 안전한지 드라이런 점검(실행 안 함).
            # 서버 점검(re-exec/직렬화/fd)을 요청하고, 회신에 클라 측 점검을 합쳐 팝업.
            self._want_restart_check = True
            self.send_cmd("request_restart_check")

        def begin_restart(self, kind):
            # restart-server/restart-all 공통 진입: 실행 전 드라이런을 먼저 돌린다.
            # 회신(restart_check)에서 _pending_restart 를 보고 안전하면 곧장 실행,
            # FAIL 이면 재확인 팝업으로 진행 여부를 다시 묻는다(do_restart).
            self._pending_restart = kind
            self.display_message("pytmux: 재시작 전 드라이런 점검 중…")
            self.send_cmd("request_restart_check")

        def do_restart(self, kind):
            # 실제 재시작 수행. restart-all 은 클라 자신도 relaunch 한다.
            if kind == "all":
                self._relaunch_on_restart = True
                self.display_message(
                    "pytmux: 전체 재시작 — 서버 재시작 + 클라 재기동…")
            else:
                self.display_message("pytmux: 서버 재시작…")
            self.send_cmd("restart_server")

        @staticmethod
        def _restart_check_eval(m, cli_ok, kind="all"):
            """서버 restart_check 결과 + 클라 측 점검을 (safe, checks) 로 평가.
            kind="server" 는 클라를 relaunch 하지 않으므로 relaunch 점검을 제외한다."""
            panes, with_fd = m.get("panes", 0), m.get("panes_with_fd", 0)
            fd_ok = (panes == with_fd and panes > 0)
            checks = [
                (m.get("reexec_supported"), "서버 re-exec 지원(POSIX·이벤트루프)"),
                (m.get("has_sessions"), "복원할 세션 존재"),
                (m.get("serialize_ok"), "상태 직렬화 round-trip"),
                (fd_ok, f"패널 master fd 보유 ({with_fd}/{panes})"),
            ]
            if kind == "all":
                checks.append((cli_ok, "클라이언트 relaunch 인자 해석"))
            return all(ok for ok, _ in checks), checks

        def _gate_restart_on_check(self, m):
            """대기 중인 재시작(_pending_restart)을 드라이런 결과로 게이트한다.
            안전하면 곧장 실행, FAIL 이면 재시작 여부를 재확인 팝업으로 묻는다."""
            kind = self._pending_restart
            self._pending_restart = None
            safe, checks = self._restart_check_eval(
                m, self._client_relaunch_ok(), kind)
            if safe:
                self.do_restart(kind)
                return
            label = "전체 재시작" if kind == "all" else "서버 재시작"
            fails = [lbl for ok, lbl in checks if not ok]
            msg = "\n".join(
                [f"드라이런 FAIL — {label} 안전 점검에서 문제가 있습니다:", ""]
                + [f"  [FAIL] {lbl}" for lbl in fails]
                + ["", "그래도 재시작할까요?"])
            self.confirm_popup(
                msg, action=lambda: self.do_restart(kind),
                title="재시작 확인", yes_label="재시작", danger=True)

        def _show_restart_check_popup(self, m):
            """서버 restart_check 결과 + 클라 측 점검을 PASS/WARN/FAIL 로 팝업."""
            cli_ok = self._client_relaunch_ok()
            safe, checks = self._restart_check_eval(m, cli_ok)
            lines = [
                ("✅ 안전 — restart-all 수행 가능" if safe
                 else "⚠️ 주의 — 아래 FAIL 항목 확인 후 진행"),
                "",
            ]
            for ok, label in checks:
                lines.append(f"  [{'PASS' if ok else 'FAIL'}] {label}")
            if m.get("serialize_err"):
                lines.append(f"        직렬화 오류: {m['serialize_err']}")
            run_v, disk_v = m.get("running_version"), m.get("disk_version")
            lines += [
                "",
                f"  서버 버전: 실행={run_v}  디스크={disk_v}"
                + ("  → 재시작 시 갱신됨" if run_v != disk_v else "  (동일)"),
                f"  클라 버전: 실행={self._code_version}  디스크="
                f"{version.code_version()}",
                "",
                "  (버전 차이는 위험이 아니라 '재시작이 새 코드를 로드'를 뜻함)",
            ]
            self.push_screen(InfoScreen(lines, title="restart-check (드라이런)"))

        def _show_version_popup(self, msg):
            """서버 version 회신(version·uptime·pid) + 클라 자신의 값으로 팝업 구성."""
            cli_up = version.fmt_uptime(time.time() - self._boot_time)
            srv_up = version.fmt_uptime(msg.get("uptime", 0))
            lines = [
                "pytmux 버전 / 업타임",
                "",
                f"  클라이언트  {self._code_version:<14}  업타임 {cli_up}",
                f"  서버        {msg.get('version', '?'):<14}  업타임 {srv_up}",
                "",
                f"  (서버 pid {msg.get('pid', '?')} · 버전=동기화된 p4 CL,"
                " 폴백 git)",
            ]
            self.push_screen(InfoScreen(lines, title="version"))

        def open_rules_editor(self):
            # #27: Claude 시작 규칙 편집 팝업. 저장하면 서버 opts.json 에 영속하고,
            # 새 Claude 세션 또는 /clear 직후 첫 idle 에 프롬프트로 자동 주입한다.
            def _saved(text):
                if text is not None:
                    self.send_cmd("set_claude_rules", text=text)
                    self.display_message("시작 규칙 저장됨" if text.strip()
                                         else "시작 규칙 비움")
            self.push_screen(RulesEditScreen(getattr(self, "_claude_rules", "")),
                             _saved)

        def _open_usage_tree(self, tree):
            self.push_screen(InfoScreen(self._usage_tree_lines(tree),
                                        title="Claude 토큰 사용량(세션별)"))

        def _usage_tree_lines(self, tree):
            """트리 응답 → 사용량 표시 줄. ctx(컨텍스트 %)와 함께 실제 세션 누계
            토큰(Σ)을 탭 합계·패널별로 보인다(#18). 맨 아래에는 가로 구분선과
            **모든 세션 토큰 합계** 한 줄을 덧붙인다(§10-A #6)."""
            lines = []
            grand = 0
            for s in tree.get("sessions", []):
                for w in s.get("windows", []):
                    cps = [p for p in (w.get("panes") or [])
                           if isinstance(p, dict) and p.get("claude")]
                    if not cps:
                        continue
                    wtok = sum((p.get("tokens") or 0) for p in cps)  # 탭 합계
                    grand += wtok
                    lines.append(f"[{w['index'] + 1}] {w['name']}  —  Σ {_fmt_tokens(wtok)}")
                    for p in cps:
                        app = p.get("cmd") or "claude"
                        usage = p.get("usage") or "-"
                        state = p.get("claude")
                        tok = _fmt_tokens(p.get("tokens") or 0)
                        lines.append(f"    pane {p['id']} · {app} · {state} · "
                                     f"{usage} · Σ {tok}")
            if not lines:
                return ["(실행 중인 Claude 패널 없음)"]
            # 하단 가로 구분선 + 전 세션 토큰 합계(§10-A #6).
            lines.append("─" * 36)
            lines.append(f"전체 세션 합계  —  Σ {_fmt_tokens(grand)}")
            return lines

        def _server_info_lines(self):
            """서버 정보 탭(§10-A #12) 줄 — 호스트·로컬/원격·소켓 경로·RTT·응답성.
            상태줄 서버이름(host) 클릭 시 통합 상태 팝업의 '서버' 탭으로 보인다."""
            host = socket.gethostname()
            remote = bool(getattr(self.status, "_is_remote", False))
            lines = [
                f"호스트: {host}",
                f"연결: {'원격(ssh)' if remote else '로컬'}",
                f"소켓: {self.sock_path}",
            ]
            rtt = getattr(self, "_net_last_rtt", None)
            if rtt is not None:
                thr = getattr(self, "net_rtt_threshold", 0.4)
                lines.append(f"RTT: {rtt * 1000:.0f} ms (임계 {thr * 1000:.0f} ms)")
            degraded = bool(getattr(self, "_net_degraded", False))
            lines.append("응답성: " + ("저하(degraded) — 빨간 외곽선" if degraded
                                        else "정상"))
            lines.append("")
            lines.append("degraded 고착 시 reconnect / resync 명령으로 재접속")
            return lines

        def _open_status_tabs(self, tree):
            """REC(출력 캡처)·토큰 사용량·서버 정보를 **한 팝업의 세 탭**으로 연다(#10,
            §10-A #12). 상태줄 버튼 배치에 맞춰 탭 순서는 REC(0)·토큰(1)·서버(2)다.
            어느 버튼으로 열었는지(_status_tab_initial)에 따라 초기 탭만 다르다.
            REC 탭에선 [c] 로 출력 캡처를 켜고 끌 수 있다."""
            usage = self._usage_tree_lines(tree)
            cap = getattr(self, "_status_cap_lines", None) or self._capture_info_lines()
            server = self._server_info_lines()
            initial = getattr(self, "_status_tab_initial", 0)

            def _toggle_capture():
                # capture-output 토글 명령 전송 + 낙관적 로컬 반영 → 갱신된 캡처 줄.
                self._run_command("capture-output")
                self.status.capture = not bool(getattr(self.status, "capture", False))
                if not self.status.capture:
                    self.status.capture_path = None
                    self.status.capture_size = 0
                self.status.refresh()
                return self._capture_info_lines()

            def _open_capture_dir():
                # 기록 중인 캡처 파일이 있는 디렉터리를 OS 파일 관리자로 연다(요청).
                # 캡처 경로는 클라이언트 머신 기준(서버=로컬일 때 유효). 없으면 안내.
                path = getattr(self.status, "capture_path", None)
                if path and proc.open_in_file_manager(os.path.dirname(path)):
                    self.display_message("기록 폴더 열기")
                else:
                    self.display_message("열 기록 폴더가 없습니다(캡처 꺼짐)")
                return None   # 줄 갱신 없음(팝업 유지)

            actions = {0: [("c", "[c] 캡처 켜기/끄기", _toggle_capture),
                           ("o", "[o] 기록 폴더 열기", _open_capture_dir)]}
            self.push_screen(InfoTabsScreen(
                [("출력 캡처(REC)", cap), ("토큰 사용량", usage), ("서버", server)],
                initial=initial, title="상태", actions=actions))

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

        # ---- 레이아웃 저장/불러오기 ----
        def save_layout_prompt(self):
            self.open_prompt("save_layout", "레이아웃 이름으로 저장")

        def request_layouts(self, mode):
            """저장된 레이아웃 목록을 요청(mode: 'over'=현재 탭 덮어쓰기, 'new'=새 탭)."""
            self._want_layouts = mode
            self.send_cmd("list_layouts")

        def _open_choose_layout(self, names, mode):
            title = "레이아웃 → 새 탭" if mode == "new" else "레이아웃 → 현재 탭 덮어쓰기"

            def handle(name):
                if name:
                    self.send_cmd("load_tab_layout", name=name,
                                  new=(mode == "new"))
            self.push_screen(ChooseLayoutScreen(names, title), handle)

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
            elif key == "detach":
                self.exit(message="detached")
            elif key == "kill_server":
                self.send_cmd("kill_server")

        # ---- 토큰 절감 설정 팝업(token-saver, docs/TOKEN_SAVING_SCENARIO.md) ----
        def open_claude_saver(self):
            self.push_screen(ClaudeSaverScreen())

        @staticmethod
        def _fmt_budget(v):
            if not v:
                return "끔(무제한)"
            if v >= 1_000_000:
                return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
            if v >= 1_000:
                return f"{v // 1000}k"
            return str(v)

        def _saver_display(self, key):
            """설정 팝업의 한 행이 보일 현재값 문자열(토글 ●/○ 또는 cycle 값)."""
            st = self.status
            bools = {
                "autoresume": st.autoresume,
                "resume_gate": st.token_budget_resume_gate,
                "budget_plan": st.claude_budget_plan,
                "ctx_autoclear": st.claude_ctx_autoclear,
                "auto_doc_clear": st.auto_doc_clear,
                "auto_compact": st.auto_compact,
                "claude_auto_mode": st.claude_auto_mode,
                "prompt_clear": st.prompt_clear,
            }
            if key in bools:
                return "●" if bools[key] else "○"
            if key == "ctx_action":
                return "/compact" if st.claude_ctx_action == "compact" \
                    else "doc+/clear"
            if key == "ctx_threshold":
                return f"잔량<{st.claude_ctx_threshold}%"
            if key == "ctx_min_interval":
                iv = int(st.claude_ctx_min_interval)
                return "상한 없음" if iv <= 0 else f"{iv}초마다 최대 1회"
            if key == "budget_day":
                return self._fmt_budget(st.token_budget_day)
            if key == "budget_session":
                return self._fmt_budget(st.token_budget_session)
            if key == "budget_5h":
                return self._fmt_budget(st.token_budget_5h)
            if key == "budget_account":
                return self._fmt_budget(st.token_budget_account)
            if key == "long_turn":
                v = int(st.claude_long_turn_sec)
                return "끔" if v <= 0 else f"{v}초 이상"
            if key == "repeat_alert":
                v = int(st.claude_repeat_alert)
                return "끔" if v <= 0 else f"{v}회 이상"
            return ""

        @staticmethod
        def _cycle_next(key, cur):
            vals = SAVER_CYCLES[key]
            try:
                i = vals.index(cur)
            except ValueError:
                i = -1
            return vals[(i + 1) % len(vals)]

        def _saver_action(self, key):
            """설정 팝업에서 한 행을 Enter — 동작을 서버로 보내고 status 를 낙관적으로
            즉시 반영한다(서버 broadcast 가 권위값으로 확정). 토글은 set_* 를 인자
            없이(서버가 반전), cycle 은 다음 프리셋 값을 보낸다."""
            st = self.status
            if key == "autoresume":
                self.send_cmd("set_autoresume")
                st.autoresume = not st.autoresume
            elif key == "resume_gate":
                self.send_cmd("set_token_budget_resume_gate")
                st.token_budget_resume_gate = not st.token_budget_resume_gate
            elif key == "budget_plan":
                self.send_cmd("set_claude_budget_plan")
                st.claude_budget_plan = not st.claude_budget_plan
            elif key == "ctx_autoclear":
                self.send_cmd("set_claude_ctx_autoclear")
                st.claude_ctx_autoclear = not st.claude_ctx_autoclear
            elif key == "auto_doc_clear":
                self.send_cmd("set_auto_doc_clear", value=None)
                st.auto_doc_clear = not st.auto_doc_clear
            elif key == "auto_compact":
                self.send_cmd("set_auto_compact", value=None)
                st.auto_compact = not st.auto_compact
            elif key == "claude_auto_mode":
                self.send_cmd("set_claude_auto_mode", value=None)
                st.claude_auto_mode = not st.claude_auto_mode
            elif key == "prompt_clear":
                self.send_cmd("set_prompt_clear", value=None)
                st.prompt_clear = not st.prompt_clear
            elif key == "ctx_action":
                nxt = self._cycle_next("ctx_action", st.claude_ctx_action)
                self.send_cmd("set_claude_ctx_action", value=nxt)
                st.claude_ctx_action = nxt
            elif key == "ctx_threshold":
                nxt = self._cycle_next("ctx_threshold", st.claude_ctx_threshold)
                self.send_cmd("set_claude_ctx_threshold", value=nxt)
                st.claude_ctx_threshold = nxt
            elif key == "ctx_min_interval":
                nxt = self._cycle_next(
                    "ctx_min_interval", int(st.claude_ctx_min_interval))
                self.send_cmd("set_claude_ctx_min_interval", value=nxt)
                st.claude_ctx_min_interval = nxt
            elif key == "budget_day":
                nxt = self._cycle_next("budget_day", st.token_budget_day)
                self.send_cmd("set_token_budget", day=nxt)
                st.token_budget_day = nxt
            elif key == "budget_session":
                nxt = self._cycle_next("budget_session", st.token_budget_session)
                self.send_cmd("set_token_budget", session=nxt)
                st.token_budget_session = nxt
            elif key == "budget_5h":
                nxt = self._cycle_next("budget_5h", int(st.token_budget_5h))
                self.send_cmd("set_token_budget", h5=nxt)
                st.token_budget_5h = nxt
            elif key == "budget_account":
                nxt = self._cycle_next("budget_account",
                                       int(st.token_budget_account))
                self.send_cmd("set_token_budget", acct=nxt)
                st.token_budget_account = nxt
            elif key == "long_turn":
                nxt = self._cycle_next("long_turn", int(st.claude_long_turn_sec))
                self.send_cmd("set_claude_turn_warn", long_sec=nxt)
                st.claude_long_turn_sec = nxt
            elif key == "repeat_alert":
                nxt = self._cycle_next("repeat_alert", int(st.claude_repeat_alert))
                self.send_cmd("set_claude_turn_warn", repeat=nxt)
                st.claude_repeat_alert = nxt

        # ---- 프롬프트 / 명령 ----
        def display_message(self, text, secs=2.0):
            self.status.message = text
            self.status.refresh()
            self.set_timer(secs, self._clear_message)

        def _clear_message(self):
            self.status.message = None
            self.status.refresh()

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
            elif name in ("mouse-debug", "mouse-log"):
                self.mouse_debug = val.lower() in ("on", "true", "1", "yes")
                if self.mouse_debug:
                    self.display_message(f"마우스 진단 로그: {self._mouse_log_path}")
            elif name == "alt-scroll":
                # on = 대체 스크롤 모드(1007) 비활성(휠을 실제 마우스 이벤트로) — 기본.
                # off = 터미널 기본 동작에 맡김(휠이 화살표로 갈 수 있음).
                self.disable_alt_scroll = val.lower() in ("on", "true", "1", "yes")
                self._term_write(
                    "\x1b[?1007l" if self.disable_alt_scroll else "\x1b[?1007h")
                self.display_message(
                    "휠 스크롤백: " + ("pytmux 처리(1007 끔)"
                                       if self.disable_alt_scroll else "터미널 기본"))
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

        def show_options(self):
            lines = [
                f"prefix      {self.prefix_key}",
                f"mouse       {'on' if self.mouse_enabled else 'off'}",
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

        def open_prompt(self, purpose, placeholder="", initial="", action=None,
                        suggest=None):
            # 한 줄 입력을 Input 을 담은 바닥 모달(PromptScreen)로 받는다.
            # 모달은 별도 스크린이라 포커스가 안정적이다(메인 뷰/AUTO_FOCUS 와 무관).
            # suggest: rename 등에서 현재 이름을 **ghost(제안)** 로 띄운다 — Tab/→ 로
            #   채워 편집·덧붙이고, 그냥 타이핑하면 덮어쓴다(initial 로 미리 채우면
            #   타이핑이 덧붙던 문제, 요청). 빈 입력일 땐 placeholder 로도 흐리게 보인다.
            suggester = None
            if purpose == "command":
                suggester = SepInsensitiveSuggester(COMPLETIONS,
                                                    case_sensitive=False)
            elif suggest:
                suggester = SuggestFromList([suggest], case_sensitive=False)
            self.push_screen(
                PromptScreen(purpose, placeholder, initial, suggester),
                lambda val: self._prompt_done(purpose, action, val))

        def _prompt_done(self, purpose, action, val):
            if purpose == "search":
                self.mode = "scroll"  # 검색은 스크롤백 모드 유지/복귀
            if val is None:  # 취소(Esc)
                return
            val = val.strip()
            if purpose == "command":
                self._run_command(val)
            elif purpose == "rename_window":
                if val:
                    self.send_cmd("rename_window", name=val)
            elif purpose == "rename_pane":
                self.send_cmd("set_pane_title", title=val)
            elif purpose == "move_window":
                if val.lstrip("-").isdigit() and int(val) - 1 >= 0:
                    self.send_cmd("move_window", index=int(val) - 1)  # 1-based→0(#21)
            elif purpose == "save_layout":
                if val.strip():
                    self.send_cmd("save_tab_layout", name=val.strip())
            elif purpose == "search":
                if val:
                    self.send_cmd("search", query=val, direction="up")
            elif purpose == "confirm":
                if val.lower().startswith("y") and action:
                    action()

        @staticmethod
        def _opt_value(args, flag):
            if flag in args:
                i = args.index(flag)
                if i + 1 < len(args):
                    return args[i + 1]
            return None

        @staticmethod
        def _first_int(args):
            for a in args:
                # 플래그(-t)·음수 토큰은 인덱스로 보지 않고 **계속 스캔**한다 — 과거엔
                # 첫 음수에서 None 을 반환해 뒤따르는 양수 인덱스를 가려, `move-tab`
                # 등이 조용히 무시됐다.
                if a.startswith("-"):
                    continue
                if a.isdigit():
                    return int(a)
            return None

        def _run_shell(self, cmd):
            try:
                res = subprocess.run(_shell_argv(cmd), capture_output=True,
                                     timeout=15, **proc.no_window_kwargs())
                text = res.stdout.decode("utf-8", "ignore")
                rc = res.returncode
            except (OSError, subprocess.SubprocessError) as e:
                text, rc = str(e), 1
            if text.strip():
                self.send_cmd("set_buffer", text=text)
                self.push_screen(InfoScreen(text.splitlines()[:40], title="run-shell"))
            return rc

        def _if_shell(self, cond, then_cmd, else_cmd=None):
            try:
                rc = subprocess.run(_shell_argv(cond), capture_output=True,
                                    timeout=15,
                                    **proc.no_window_kwargs()).returncode
            except (OSError, subprocess.SubprocessError):
                rc = 1
            if rc == 0:
                self._run_command(then_cmd)
            elif else_cmd:
                self._run_command(else_cmd)

        _SENDKEYS = {"Enter": b"\r", "Tab": b"\t", "Space": b" ",
                     "Escape": b"\x1b", "BSpace": b"\x7f", "Up": b"\x1b[A",
                     "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D"}

        def _send_keys(self, args):
            literal = "-l" in args
            toks = [a for a in args if not a.startswith("-")]
            out = b""
            for a in toks:
                if not literal and a in self._SENDKEYS:
                    out += self._SENDKEYS[a]
                elif (not literal and a.startswith("C-") and len(a) == 3
                      and a[2].isalpha()):
                    out += bytes([ord(a[2].lower()) - 96])
                else:
                    out += a.encode("utf-8")
            if out:
                self.send_input(out)

        def _run_command(self, line, _depth=0):
            """tmux 류 명령 문자열을 해석해 서버 명령으로 변환한다."""
            if not line or _depth > 8:
                return
            try:
                parts = shlex.split(line)
            except ValueError:
                parts = line.split()
            if not parts:
                return
            # 한영 오타 복원: 명령 이름 토큰에 한글이 섞이면(IME 켠 채 입력) QWERTY 로
            # 되돌린다. 이름은 항상 ASCII 이므로 안전하고, 인자(한글 이름 등)는 안 건드린다.
            if has_hangul(parts[0]):
                parts[0] = hangul_to_qwerty(parts[0])
            c = parts[0].lower()
            args = parts[1:]
            # 사용자 별칭 확장
            if c in self.aliases:
                return self._run_command(
                    self.aliases[c] + (" " + " ".join(args) if args else ""),
                    _depth + 1)
            if c in ("help", "commands", "?", "list-commands"):
                # 명령 목록 선택기(#3): 옵션 스키마가 있으면 옵션 모달에서 값을 정해
                # 프롬프트 없이 바로 실행, 인자 없는 안전한 명령은 선택 즉시 실행,
                # 그 외(자유 텍스트 인자)는 기존처럼 명령 프롬프트에 채워 Enter 로 실행.
                def _picked(name):
                    if not name:
                        return
                    opts = COMMAND_OPTIONS.get(name)
                    if opts:
                        desc = next((d for n, d, *_ in COMMANDS if n == name), "")

                        def _run(line):
                            if line:
                                self._run_command(line)
                        self.push_screen(
                            CommandOptionsScreen(name, desc, opts), _run)
                    elif name in COMMAND_NOARG:
                        self._run_command(name)
                    else:
                        self.open_prompt("command", "", initial=name + " ")
                self.push_screen(CommandListScreen(COMMANDS), _picked)
                return
            if c in ("run-shell", "run"):
                if args:
                    self._run_shell(args[0])
                return
            if c in ("if-shell", "if"):
                if len(args) >= 2:
                    self._if_shell(args[0], args[1], args[2] if len(args) > 2 else None)
                return
            if c in ("split-window", "splitw"):
                # tmux 규약: -h = 좌우(side-by-side, lr), -v/기본 = 상하(tb).
                # (과거엔 -h→상하로 반전돼 prefix %/" · join-pane -h 와 어긋났다.)
                orient = "lr" if "-h" in args else "tb"
                self.send_cmd("split", orient=orient)
            elif c in ("kill-pane", "killp"):
                self.send_cmd("kill_pane")
            elif c in ("new-tab", "newt", "new-window", "neww"):
                self.send_cmd("new_window")
            elif c in ("kill-tab", "killt", "kill-window", "killw"):
                self.send_cmd("kill_window")
            elif c in ("next-tab", "next-window", "next"):
                self.send_cmd("next_window")
            elif c in ("previous-tab", "prev-tab", "previous-window", "prev"):
                self.send_cmd("prev_window")
            elif c in ("last-tab", "last-window", "last"):
                self.send_cmd("last_window")
            elif c == "automatic-rename" or (
                    c == "setw" and "automatic-rename" in args):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_auto_rename", value=val)
            elif (c in ("monitor-activity", "monitor-bell")) or (
                    c == "setw" and any("monitor-" in a for a in args)):
                which = "bell" if ("bell" in c or "monitor-bell" in args) else "activity"
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_monitor", which=which, value=val)
            elif c in ("move-tab-left", "move-tab-right",
                       "move-tab-first", "move-tab-last"):
                self.send_cmd("move_current_tab", where=c[len("move-tab-"):])
            elif c in ("move-tab", "movet", "move-window", "movew"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None and idx - 1 >= 0:   # 사용자 1-based → 0-based(#21)
                    self.send_cmd("move_window", index=idx - 1)
            elif c in ("swap-tab", "swapt", "swap-window", "swapw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None and idx - 1 >= 0:   # 사용자 1-based → 0-based(#21)
                    self.send_cmd("swap_window", index=idx - 1)
            elif c in ("choose-tree", "choose-tab", "choose-window",
                       "overview", "tree"):
                self.request_tree()
            elif c in ("select-pane", "selectp"):
                if "-T" in args:
                    title = " ".join(args[args.index("-T") + 1:])
                    self.send_cmd("set_pane_title", title=title)
                else:
                    for flag, d in (("-L", "left"), ("-R", "right"),
                                    ("-U", "up"), ("-D", "down")):
                        if flag in args:
                            self.send_cmd("select_pane", dir=d)
                            break
            elif c == "rename-pane":
                self.send_cmd("set_pane_title", title=" ".join(args))
            elif c in ("select-tab", "selectt", "select-window", "selectw"):
                idx = self._opt_value(args, "-t")
                idx = int(idx) if idx and idx.isdigit() else self._first_int(args)
                if idx is not None and idx - 1 >= 0:   # 사용자 1-based → 0-based(#21)
                    self.send_cmd("select_window", index=idx - 1)
            elif c in ("rename-tab", "renamet", "rename-window", "renamew"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("rename_window", name=name)
                else:
                    cur = self._active_window_name()
                    self.open_prompt("rename_window", cur or "rename-tab",
                                     suggest=cur)   # ghost(타이핑=덮어쓰기)
            elif c in ("resize-pane", "resizep"):
                if "-Z" in args:
                    self.send_cmd("zoom")
                else:
                    # tmux resize-pane -L/-R/-U/-D [N]: 분할선을 N칸(기본 3) 이동.
                    # 마우스 divider 드래그·prefix HJKL 과 같은 resize_dir 경로로 보내
                    # 키·명령·마우스 리사이즈를 대칭화한다(#17 — 과거엔 -Z 만 처리해
                    # 명령/팔레트로는 분할선 정밀 이동이 불가했다).
                    _dmap = {"-L": "left", "-R": "right", "-U": "up", "-D": "down"}
                    d = next((_dmap[a] for a in args if a in _dmap), None)
                    if d is not None:
                        self.send_cmd("resize_dir", dir=d,
                                      cells=(self._first_int(args) or 3))
            elif c == "zoom":
                self.send_cmd("zoom")
            elif c in ("select-layout", "selectl"):
                if args:
                    self.send_cmd("select_layout", preset=args[0])
                else:
                    self.send_cmd("cycle_layout")
            elif c in ("next-layout", "nextl"):
                self.send_cmd("cycle_layout")
            elif c in ("rotate-window", "rotatew"):
                self.send_cmd("rotate", forward=("-D" not in args))
            elif c in ("swap-pane", "swapp"):
                self.send_cmd("swap_pane", forward=("-U" not in args))
            elif c in ("break-pane", "breakp"):
                self.send_cmd("break_pane")
            elif c in ("join-pane", "joinp"):
                self.send_cmd("join_pane", orient=("lr" if "-h" in args else "tb"))
            elif c in ("respawn-pane", "respawnp"):
                self.send_cmd("respawn_pane")
            elif c in ("auto-resume", "autoresume"):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_autoresume", value=val)
            elif c in ("auto-resume-message", "autoresume-message"):
                self.send_cmd("set_autoresume", msg=" ".join(args))
            elif c in ("capture-output", "capture-toggle"):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_capture", value=val)
                self.display_message("출력 캡처 " + ("토글" if val is None else
                                     ("ON" if val else "OFF")) + " (상태줄 REC)")
            elif c in ("synchronize-panes", "syncp") or (
                    c == "setw" and "synchronize-panes" in args):
                val = None
                if "on" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_sync", value=val)
            elif c == "setw" and "pane-border-status" in args or \
                    c == "pane-border-status":
                val = None
                if "on" in args or "top" in args:
                    val = True
                elif "off" in args:
                    val = False
                self.send_cmd("set_border_status", value=val)
            elif c in ("detach-client", "detach"):
                if "-a" in args:
                    self.send_cmd("detach_others")
                else:
                    self.exit(message="detached")
            elif c == "kill-server":
                self.send_cmd("kill_server")
            elif c in ("restart-server", "restart"):
                # 작업 보존 재시작: 셸/PTY 를 살린 채 서버 코드만 교체(re-exec).
                # 화면이 잠깐 끊겼다 재접속된다(docs/RESTART_SCENARIO.md).
                # 실행 전 드라이런으로 안전성을 먼저 점검한다.
                self.begin_restart("server")
            elif c in ("restart-check", "restart-dry-run", "restart-all-check"):
                # restart-all 드라이런: 실제 재시작 없이 안전성만 점검해 팝업으로 보고.
                self.open_restart_check()
            elif c in ("restart-all", "full-restart", "restart-client-server"):
                # 전체 재시작: 서버는 work-preserving re-exec(셸/세션 보존), 동시에
                # 클라이언트도 자신을 relaunch(새 클라 코드로 재attach). 서버/클라
                # 코드를 모두 갱신하면서 작업은 보존한다(docs/RESTART_SCENARIO.md).
                # 실행 전 드라이런으로 안전성을 먼저 점검한다.
                self.begin_restart("all")
            elif c in ("reconnect", "resync"):
                # IPC 강제 재접속(§10): degraded(빨간 외곽선) 고착 시 정체된 소켓을
                # 버리고 새로 세워 회복한다. 서버 PTY/세션·실행 중 Claude 는 보존.
                self.reconnect_now("manual")
            elif c in ("paste-clipboard", "pasteb-clip"):
                self.paste_os_clipboard()  # bracketed 패스스루
            elif c in ("send-keys", "send"):
                self._send_keys(args)
            elif c in ("send-escape", "send-esc"):
                # 활성 패널에 ESC 1회 전달(= send-keys Escape 의 한 토큰 단축).
                # 한 키에 바인딩하기 쉽게 별도 명령으로 노출 — Shift+ESC 가 안 먹는
                # 터미널에서 `bind-key <key> send-escape` 로 전용 키를 둘 수 있다.
                self.send_input(b"\x1b")
            elif c in ("paste-buffer", "pasteb"):
                idx = self._first_int(args)
                self.send_cmd("paste_buffer", index=idx or 0)
            elif c in ("capture-pane", "capturep"):
                self.send_cmd("capture_pane", full=("-S" in args or "-a" in args))
            elif c in ("pipe-pane", "pipep"):
                self.send_cmd("pipe_pane",
                              cmd=" ".join(a for a in args if not a.startswith("-")))
            elif c == "save-layout":
                self.send_cmd("save_layout")
            elif c == "restore-layout":
                self.send_cmd("restore_layout")
            elif c in ("layout-save", "save-tab-layout"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("save_tab_layout", name=name)
                else:
                    self.save_layout_prompt()
            elif c in ("layout-load", "load-tab-layout"):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("load_tab_layout", name=name,
                                  new=("-n" in args))
                else:
                    self.request_layouts("new" if "-n" in args else "over")
            elif c in ("layout-load-new",):
                name = " ".join(a for a in args if not a.startswith("-"))
                if name:
                    self.send_cmd("load_tab_layout", name=name, new=True)
                else:
                    self.request_layouts("new")
            elif c in ("layout-list", "list-layouts"):
                self.request_layouts("over")
            elif c in ("choose-buffer", "list-buffers", "lsb"):
                self.choose_buffer()
            elif c in ("clear-history", "clearhist"):
                self.send_cmd("clear_history")
            elif c in ("clock-mode", "clock"):
                self.toggle_clock(self.layout.get("active"))
            elif c in ("calendar-mode", "calendar", "cal"):
                self.toggle_calendar(self.layout.get("active"))
            elif c == "open-clock":
                self.set_clock(self.layout.get("active"), True)
            elif c == "close-clock":
                self.set_clock(self.layout.get("active"), False)
            elif c in ("open-calendar", "open-cal"):
                self.set_calendar(self.layout.get("active"), True)
            elif c in ("close-calendar", "close-cal"):
                self.set_calendar(self.layout.get("active"), False)
            elif c == "claude-header":
                # claude-header [on|off|toggle] — 프롬프트 헤더 표시 제어(기본 toggle)
                arg = args[0].lower() if args else "toggle"
                self.set_claude_header(arg == "on" if arg in ("on", "off")
                                       else not self.claude_header_on)
            elif c in ("single-border", "pane-border"):
                # single-border [on|off|toggle] — 단일 패널 테두리 표시(기본 toggle).
                # 서버가 opts.json 에 영속하고 새 레이아웃을 다시 보낸다.
                arg = args[0].lower() if args else "toggle"
                val = (arg == "on") if arg in ("on", "off") \
                    else (not self.single_border_on)
                self.single_border_on = val           # 낙관적 즉시 반영
                self.send_cmd("set_single_border", value=bool(val))
            elif c in ("coalesce-repaints", "coalesce"):
                # coalesce-repaints [on|off|toggle] — alt-screen 리페인트 합치기(§10 대응
                # ②). 서버 내부 동작이라 클라 상태/렌더 변화 없음 — on/off 는 그대로,
                # 인자 없으면 toggle(서버가 반전). 서버가 opts.json 에 영속.
                arg = args[0].lower() if args else "toggle"
                val = (arg == "on") if arg in ("on", "off") else None
                self.send_cmd("set_coalesce", value=val)
            elif c in ("claude-rules", "rules", "startup-rules"):
                self.open_rules_editor()                       # #27 시작 규칙 편집
            elif c in ("prompt-history", "prompts"):
                self.open_prompt_history(self.layout.get("active"))
            elif c in ("token-usage", "tokens"):
                self.open_claude_usage_tree()
            elif c in ("token-log", "tokens-log", "token-usage-log"):
                self.open_token_log()
            elif c in ("claude-usage", "usage", "refresh-usage"):
                # M19 그림자 /usage 질의: 서버가 숨은 claude 를 띄워 실 세션/주간 한도를
                # 긁어온다(사용자 화면 무간섭, ~수초). 회신은 status 로 반영.
                self.send_cmd("refresh_usage")
                self.display_message("사용량 조회 중… (숨은 /usage, ~수초)", 4.0)
            elif c in ("token-account", "tokens-account"):
                # token-account <이름> — 활성 패널 Claude 계정 수동 지정(빈값=자동).
                self.send_cmd("set_claude_account", name=" ".join(args).strip())
            elif c in ("prompt-clear", "prompt-clear-mode"):
                # prompt-clear [on|off|toggle] — 활성 패널 프롬프트 단위 클리어 모드(#9).
                arg = args[0].lower() if args else "toggle"
                val = (arg == "on") if arg in ("on", "off") else None
                self.send_cmd("set_prompt_clear", value=val)
            elif c in ("version", "about"):
                # 클라/서버 버전(p4 CL)·업타임 팝업.
                self.open_version()
            elif c in ("token-saver", "claude-settings", "token-settings"):
                # 토큰 절감 설정 팝업 — 각 자동 개입 토글·잔량 임계·예산(설정 팝업).
                self.open_claude_saver()
            elif c in ("model", "model-config", "claude-model"):
                # 모델·컨텍스트 변경 팝업(요청). 상태줄 모델 배지 클릭으로도 열린다.
                self.open_model_config()
            elif c in ("auto-doc-clear", "auto-doc"):
                # auto-doc-clear [on|off|toggle] — Claude 가 idle 로 30초 지속되면
                # 자동으로 문서화→/clear 를 1회 수행(§10). 서버 전역 토글, opts 영속.
                arg = args[0].lower() if args else "toggle"
                val = (arg == "on") if arg in ("on", "off") else None
                self.send_cmd("set_auto_doc_clear", value=val)
            elif c in ("auto-compact", "auto-cmp"):
                # auto-compact [on|off|toggle] — Claude 가 idle 로 30초 지속되면
                # 자동으로 '/compact'+Enter 를 1회 주입(요청). 서버 전역 토글, opts 영속.
                arg = args[0].lower() if args else "toggle"
                val = (arg == "on") if arg in ("on", "off") else None
                self.send_cmd("set_auto_compact", value=val)
            elif c in ("claude-auto-mode", "auto-mode"):
                # claude-auto-mode [on|off|toggle] — Claude idle 시 권한모드를 자동
                # 으로 오토모드로 맞춤(§10). 서버 전역 토글, opts 영속.
                arg = args[0].lower() if args else "toggle"
                val = (arg == "on") if arg in ("on", "off") else None
                self.send_cmd("set_claude_auto_mode", value=val)
            elif c == "prompt-clear-message":
                # prompt-clear-message <문구> — ① 문서화 지시문 변경(opts 영속).
                self.send_cmd("set_prompt_clear_message", msg=" ".join(args).strip())
            elif c in ("prompt-clear-queue", "pc-queue"):
                # prompt-clear-queue [<명령> | -c|clear] — 빈값=현재 큐 목록 팝업(#4),
                # -c/clear=큐 비움, 그 외=명령을 큐에 추가(모드 자동 on, doc+/clear
                # 사이클마다 하나씩 투입).
                if not args:
                    q = self.status.prompt_clear_queue
                    lines = [f"{i+1}. {cmd}" for i, cmd in enumerate(q)] or \
                        ["(큐 비어 있음)"]
                    self.push_screen(InfoScreen(lines, title="프롬프트 클리어 큐"))
                elif args[0].lower() in ("-c", "clear", "--clear"):
                    self.send_cmd("pc_queue_clear")
                    self.display_message("큐 비움")
                else:
                    self.send_cmd("pc_queue_add", cmd=" ".join(args).strip())
            elif c in ("display-popup", "popup"):
                cmd = " ".join(a for a in args if not a.startswith("-"))
                if cmd:
                    try:
                        res = subprocess.run(_shell_argv(cmd),
                                             capture_output=True, timeout=30,
                                             **proc.no_window_kwargs())
                        text = (res.stdout + res.stderr).decode("utf-8", "ignore")
                    except (OSError, subprocess.SubprocessError) as e:
                        text = str(e)
                    self.push_screen(InfoScreen(
                        text.splitlines()[:60] or ["(출력 없음)"], title="popup"))
                else:
                    self.push_screen(InfoScreen(["display-popup <command>"],
                                                title="popup"))
            elif c in ("source-file", "source"):
                self.reload_config(args[0] if args else None)
            elif c in ("set", "set-option"):
                opts = [a for a in args if not a.startswith("-")]
                if len(opts) >= 2:
                    self.apply_option(opts[0], " ".join(opts[1:]))
            elif c in ("show-options", "show"):
                self.show_options()
            elif c == "set-hook":
                if "-u" in args and len(args) >= 2:
                    self.hooks.pop(args[args.index("-u") + 1], None)
                else:
                    opts = [a for a in args if not a.startswith("-")]
                    if len(opts) >= 2:
                        self.hooks[opts[0]] = " ".join(opts[1:])
            elif c in ("display-message", "display", "displaym"):
                self.display_message(" ".join(args) if args else "")
            elif c == "show-hooks":
                self.push_screen(InfoScreen(
                    [f"{k} → {v}" for k, v in self.hooks.items()], title="hooks"))
            elif c in ("bind-key", "bind", "bindkey"):
                # bind-key <key> <command...> — prefix 후 <key> 에 명령 바인딩(런타임).
                # 키는 tmux 표기(C-x)도 받아 textual(ctrl+x)로 정규화. 한 글자는 그대로.
                # 첫 인자만 키, 나머지는 명령 원문(플래그 -h 등 보존)으로 그대로 쓴다.
                if len(args) >= 2:
                    key = _tmux_key_to_textual(args[0])
                    self.bindings[key] = " ".join(args[1:])
                    self.display_message(f"bound {key}")
            elif c in ("unbind-key", "unbind", "unbindkey"):
                # unbind-key <key> | -a (전체 해제). 없는 키는 조용히 무시.
                if "-a" in args:
                    n = len(self.bindings)
                    self.bindings.clear()
                    self.display_message(f"unbound all ({n})")
                else:
                    pos = [a for a in args if not a.startswith("-")]
                    if pos:
                        key = _tmux_key_to_textual(pos[0])
                        if self.bindings.pop(key, None) is not None:
                            self.display_message(f"unbound {key}")
                        else:
                            self.display_message(f"no binding: {key}")
            elif c in ("list-keys", "lsk", "list-binds"):
                lines = [f"{k} → {v}" for k, v in sorted(self.bindings.items())]
                self.push_screen(InfoScreen(lines or ["(바인딩 없음)"],
                                            title="key bindings"))
            # 알 수 없는 명령은 조용히 무시

        def on_paste(self, event: events.Paste):
            # 외부 터미널의 붙여넣기(멀티라인 포함)를 활성 패널로 패스스루.
            # 내부 앱이 bracketed paste 를 켰으면 서버가 마커로 감싼다.
            # (이미지 붙여넣기는 내부 Claude Code 가 공유 OS 클립보드에서 읽음)
            if len(self.screen_stack) > 1:
                return  # 프롬프트/모달 입력은 그 스크린이 처리
            if self.writer and event.text:
                self.send_cmd("paste", text=event.text)
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
            # ESC: 명령 모드 진입(키를 명령으로 받음). 셸로는 전달하지 않음.
            if event.key == "escape":
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
            data = key_to_bytes(event)
            if data:
                self.send_input(data)
            event.prevent_default()
            event.stop()

        def _handle_prefix(self, event: events.Key):
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
                self.toggle_clock(self.layout.get("active"))
            elif k == "R":
                self.send_cmd("set_autoresume")
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

        # ---- ESC(명령) 모드 ----
        def _exit_esc(self):
            self.mode = "normal"
            self.status.cmd_mode = False
            if self.tabbar.bar_focus:
                self.tabbar.bar_focus = False
                self.tabbar.refresh()
            if self._hdr_focus is not None:    # 헤더 포커스 해제(#5)
                self._hdr_focus = None
                self._composite()
            if self._status_focus is not None:   # 상태바 버튼 포커스 해제(요청)
                self._status_focus = None
                self.status.focus_btn = None
            self.status.refresh()

        def _claude_header_panes(self):
            """헤더가 그려지는 Claude 패널 id 를 레이아웃 순서로 반환(#5 헤더 포커스)."""
            out = []
            if not self.claude_header_on:
                return out
            for p in self.layout.get("panes", []):
                if not p.get("claude_hdr"):   # 헤더 행이 실제 예약된 패널만(#1)
                    continue
                if p["id"] in self._claude_hidden_panes:
                    continue
                info = self.pane_claude.get(p["id"])
                if info and info.get("claude") and info.get("prompt"):
                    out.append(p["id"])
            return out

        def _focus_tabbar(self):
            self._hdr_focus = None
            self.tabbar.sel = self._active_tab_index()
            self.tabbar.bar_focus = True
            self._composite()
            self.tabbar.refresh()

        def _handle_hdr_focus(self, event: events.Key):
            """ESC 모드 패널 첫 행(프롬프트 헤더)·닫기 [x] 포커스 동선(#5·#31).
            헤더에서 ←→ 로 헤더 사이 이동, 마지막에서 → 면 닫기 [x] 로, ↑ 면 탭바,
            ↓ 면 패널로 복귀, Enter=히스토리. 닫기 [x] 포커스에서 Enter=탭 닫기."""
            k = event.key
            # 닫기 [x] 포커스 상태
            if self._hdr_focus == "close":
                if k == "enter":
                    self._hdr_focus = None
                    self.confirm_kill_tab()
                elif k == "left":          # 헤더로 복귀(없으면 패널로)
                    panes = self._claude_header_panes()
                    active = self.layout.get("active")
                    self._hdr_focus = (active if active in panes
                                       else (panes[-1] if panes else None))
                    self._composite()
                elif k == "up":
                    self._focus_tabbar()
                elif k == "down":
                    self._hdr_focus = None       # 패널로 복귀
                    self._composite()
                elif k == "escape":
                    self._hdr_focus = None
                    self._composite()
                    self._exit_esc()
                return
            panes = self._claude_header_panes()
            if not panes or self._hdr_focus not in panes:
                # 헤더가 없어도 닫기 [x] 는 접근 가능하게(현 상태가 close 가 아니면 해제)
                self._hdr_focus = None
                self._composite()
                return
            cur = panes.index(self._hdr_focus)
            if k == "up":
                self._focus_tabbar()             # 헤더 위 → 탭바
            elif k == "down":
                self._hdr_focus = None           # 헤더 아래 → 패널 복귀
                self._composite()
            elif k == "left":
                self._hdr_focus = panes[(cur - 1) % len(panes)]
                self._composite()
            elif k == "right":
                # 마지막 헤더에서 오른쪽 → 닫기 [x], 그 외엔 다음 헤더
                self._hdr_focus = ("close" if cur == len(panes) - 1
                                   else panes[cur + 1])
                self._composite()
            elif k == "enter":
                pid = self._hdr_focus
                self._hdr_focus = None
                self._exit_esc()
                self.open_prompt_history(pid)
            elif k == "escape":
                self._hdr_focus = None
                self._composite()
                self._exit_esc()

        def _handle_esc_mode(self, event: events.Key):
            """ESC 명령 모드: 방향키=패널 이동, 위로 더 가면 상단 탭바 포커스.
            탭바 포커스에서는 ←→ 탭 선택, Enter 전환, +/x 추가/삭제, ↓/Esc 복귀."""
            k = event.key
            ch = event.character
            tb = self.tabbar
            if self._hdr_focus is not None:       # Claude 헤더 포커스 동선(#5)
                self._handle_hdr_focus(event)
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
                # 최상단 패널에서 ↑: 먼저 프롬프트 헤더(있으면)로, 다시 ↑ 면 탭바로
                # (#31 헤더·닫기버튼을 방향키 동선에 편입). 헤더가 없어도 우상단 닫기
                # [x] 는 항상 그려지므로 [x] 로 보낸다(#) — 거기서 다시 ↑ 면 탭바.
                panes = self._claude_header_panes()
                active = self.layout.get("active")
                if active in panes:
                    self._hdr_focus = active
                    self._composite()
                else:
                    self._hdr_focus = "close"   # 프롬프트 헤더 없어도 [x] 로 이동
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
            elif ch == "h":                       # Claude 헤더 포커스 진입(#5)
                panes = self._claude_header_panes()
                if panes:
                    active = self.layout.get("active")
                    self._hdr_focus = active if active in panes else panes[0]
                    self._composite()
                else:
                    self.display_message("Claude 헤더 없음")
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
                self.open_prompt("search", "search ↑ (이전 방향)")
                return  # 프롬프트 모드로 전환
            elif k == "n":
                self.send_cmd("search", direction="up")
            elif k == "N":
                self.send_cmd("search", direction="down")
            elif k in ("q", "escape", "enter"):
                self.send_scroll(aid, bottom=True)
                self.mode = "normal"

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
