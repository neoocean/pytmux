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

from . import clientclip, clientrender, i18n, ipc, plugins, proc, version
from .clientutil import (  # noqa: F401  (클로저에서 이름으로 사용)
    COMMAND_NOARG, COMMAND_OPTIONS, COMMANDS, COMPLETIONS, DEFAULT_STYLE,
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

# IPC 소켓 재접속 재시도 파라미터 — 흩어져 있던 매직 상수를 한곳에 모았다(M4 #30).
_RECONNECT_DELAY = 0.02            # 재시도 간격(초)
_RECONNECT_RETRIES_RESTART = 300  # 서버 re-exec 재기동 대기(~6s)
_RECONNECT_RETRIES_FORCE = 150    # degraded 강제 재접속(~3s, 서버는 살아 있음)


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
                    self.display_message(
                        i18n.t("msg.paste_image_path", path=path))
                    return
                # 폴백: 내부 앱이 공유 클립보드에서 직접 읽도록 Alt+V.
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


class _NetReconnectMixin:
    """IPC 소켓 reader 태스크 + 재접속(재시작 재개·degraded 강제 회복) + RTT
    히스테리시스. 서버 PTY/세션은 안 건드리고 클라↔서버 소켓만 다룬다(§10)."""

    _RTT_WINDOW = 3600.0   # RTT 이력 보존/그래프 창(초) — 최근 60분
    _RTT_GRAPH_W = 48      # 그래프 가로 칸(시간 버킷 수)
    _RTT_GRAPH_H = 5       # 그래프 세로 행(각 행 = 1/8 정밀 세로 막대)

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
                # 강제 재접속(§10 degraded 자동/수동 회복)이 막 옛 소켓을 close 해
                # 이 reader 를 깨운 경우: _force_reconnect 가 새 연결+hello 후
                # _start_reader 로 _conn_gen 을 올린다. 하지만 그 close 는 gen 증가
                # **이전**에 reader 를 깨우므로 위 gen 가드를 빠져나간다 — 여기서
                # _force_reconnecting 를 보고 조용히 종료해야 한다(앱을 닫지 않음).
                # 누락 시: pong 미지원 옛 서버에 새 클라가 붙으면 degraded 자동회복이
                # 떠 self.exit() 로 클라가 ~10초 만에 메시지 없이 종료됐다.
                if self._force_reconnecting:
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
            self.exit(message=i18n.t("msg.reconnect_failed"))
            return
        self.display_message(i18n.t("msg.restart_done"))
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
                self.display_message(i18n.t("msg.reconnect_failed_net"))
                return
            # 네트워크 상태 리셋: 새 채널이니 degraded 해제하고 표본 카운터 비움.
            self._net_ping_ts = None
            self._net_bad = self._net_good = 0
            if self._net_degraded:
                self._net_degraded = False
                self._composite()
            self.display_message(i18n.t("msg.reconnected_resync"))
            self._start_reader()   # 새 reader 태스크(새 세대) — 서버 _send_full 수신
        finally:
            self._force_reconnecting = False

    def reconnect_now(self, reason="manual"):
        """강제 재접속을 워커로 시작한다(명령/워치독에서 호출). 이미 진행 중이면
        _force_reconnect 가 즉시 반환한다."""
        self.run_worker(self._force_reconnect(reason), exclusive=False)

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
        # 60분 RTT 그래프용 이력에 표본을 남기고 창 밖(앞쪽)을 잘라낸다(서버 팝업).
        now_h = time.monotonic()
        self._net_rtt_hist.append((now_h, rtt))
        cutoff = now_h - self._RTT_WINDOW
        h = self._net_rtt_hist
        drop = 0
        while drop < len(h) and h[drop][0] < cutoff:
            drop += 1
        if drop:
            del h[:drop]
        if rtt > self.net_rtt_threshold:
            self._net_bad += 1
            self._net_good = 0
        else:
            self._net_good += 1
            self._net_bad = 0
        self._log_net_debug(rtt)
        # 로컬 연결은 네트워크 개념이 없다(§10-F): RTT 스파이크 = 이벤트루프/
        # 스케줄링 지터일 뿐이라 degraded(빨강)·강제 재접속을 띄우지 않는다.
        # 표본 카운터·RTT 로그는 위에서 이미 갱신했으니 진단은 계속 가능.
        if self._net_local:
            return
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

    def _net_debug_on(self) -> bool:
        """RTT 진단 로그 on/off. env PYTMUX_NET_DEBUG(기본 off) — 토큰 진단
        (§10-D)과 같은 결의 라이브 판정용 스위치."""
        return bool(os.environ.get("PYTMUX_NET_DEBUG"))

    def _log_net_debug(self, rtt):
        """env-gated(PYTMUX_NET_DEBUG) 네트워크 응답성 진단을 `<state>.netdbg.jsonl`
        에 한 줄(한 RTT 표본)씩 append. §10-F(로컬인데 degraded 빨강 깜빡임) 라이브
        판정용 — 매 표본의 rtt·임계·연속 카운터·degraded·local 여부를 남겨 로컬
        루프백/이벤트루프 지터가 실제로 임계를 넘는지 며칠치 실측한다. 기본 OFF 라
        평시 무영향, best-effort — 로깅이 핑/표시 흐름을 절대 막지 않는다(서버
        _log_token_debug 와 동일 정책)."""
        if not self._net_debug_on():
            return
        try:
            import json
            rec = {
                "ts": round(time.time(), 3),
                "rtt_ms": round(rtt * 1000, 1),
                "thr_ms": round(self.net_rtt_threshold * 1000, 1),
                "bad": self._net_bad, "good": self._net_good,
                "degraded": self._net_degraded, "local": self._net_local,
            }
            path = ipc.state_base(self.sock_path) + ".netdbg.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass


class _RestartVersionMixin:
    """버전/업타임 팝업 + 작업 보존 재시작(드라이런 게이트·서버/전체 재시작) +
    서버 정보 탭. 모두 서버에 요청을 보내고 회신을 팝업으로 그리는 표시 경로다."""

    def open_version(self):
        # version 명령: 서버에 버전/업타임을 요청하고(t==version 회신), 클라 자신의
        # 버전/업타임과 합쳐 팝업(InfoScreen)을 띄운다.
        self._want_version = True
        self.send_cmd("request_version")

    def open_restart_check(self):
        # restart-check: 작업 보존 전체 재시작이 안전한지 드라이런 점검(실행 안 함).
        # 서버 점검(re-exec/직렬화/fd)을 요청하고, 회신에 클라 측 점검을 합쳐 팝업.
        self._want_restart_check = True
        self.send_cmd("request_restart_check")

    def open_autoresume_info(self):
        """하단 'AR' 배지 클릭/터치 → 자동 재개(autoresume) 설명 + 켜고 끄기 팝업(요청).
        현재 상태를 보여 주고 [a] 로 토글한다(set_autoresume — 활성 패널 기준). 원격제어
        팝업과 같은 hide_key/hide_cb 패턴(토글 후 닫힘; 다시 열어 새 상태 확인)."""
        on = bool(getattr(self.status, "autoresume", False))
        state = i18n.t("ar.state_on") if on else i18n.t("ar.state_off")
        lines = [
            i18n.t("ar.line1", state=state),
            "",
            i18n.t("ar.line_b1"),
            i18n.t("ar.line_b2"),
            i18n.t("ar.line_b3"),
            "",
            i18n.t("ar.toggle_line",
                   act=i18n.t("ar.off") if on else i18n.t("ar.on")),
        ]
        self.push_screen(InfoScreen(
            lines, title=i18n.t("ar.title"),
            hide_key="a", hide_cb=lambda: self.send_cmd("set_autoresume")))

    def begin_restart(self, kind):
        # restart-server/restart-all 공통 진입: 실행 전 드라이런을 먼저 돌린다.
        # 회신(restart_check)에서 _pending_restart 를 보고 안전하면 곧장 실행,
        # FAIL 이면 재확인 팝업으로 진행 여부를 다시 묻는다(do_restart).
        self._pending_restart = kind
        self.display_message(i18n.t("msg.restart_dryrun"))
        self.send_cmd("request_restart_check")

    def do_restart(self, kind):
        # 실제 재시작 수행. restart-all 은 클라 자신도 relaunch 한다.
        if kind == "all":
            self._relaunch_on_restart = True
            self.display_message(
                "pytmux: 전체 재시작 — 서버 재시작 + 클라 재기동…")
        else:
            self.display_message(i18n.t("msg.server_restart"))
        self.send_cmd("restart_server")

    def _gate_restart_on_check(self, m):
        """대기 중인 재시작(_pending_restart)을 드라이런 결과로 게이트한다.
        안전하면 곧장 실행, FAIL 이면 재시작 여부를 재확인 팝업으로 묻는다."""
        kind = self._pending_restart
        self._pending_restart = None
        safe, checks = _restart_check_eval(
            m, _client_relaunch_ok(), kind)
        if safe:
            self.do_restart(kind)
            return
        label = (i18n.t("restart.label_all") if kind == "all"
                 else i18n.t("restart.label_server"))
        fails = [lbl for ok, lbl in checks if not ok]
        msg = "\n".join(
            [i18n.t("restart.fail_header", label=label), ""]
            + [i18n.t("restart.fail_item", lbl=lbl) for lbl in fails]
            + ["", i18n.t("restart.confirm_q")])
        self.confirm_popup(
            msg, action=lambda: self.do_restart(kind),
            title=i18n.t("dialog.restart_confirm_title"),
            yes_label=i18n.t("dialog.restart_yes"), danger=True)

    def _show_restart_check_popup(self, m):
        """서버 restart_check 결과 + 클라 측 점검을 PASS/WARN/FAIL 로 팝업."""
        cli_ok = _client_relaunch_ok()
        safe, checks = _restart_check_eval(m, cli_ok)
        lines = [
            (i18n.t("restartcheck.safe") if safe
             else i18n.t("restartcheck.unsafe")),
            "",
        ]
        for ok, label in checks:
            lines.append(i18n.t("restartcheck.item",
                                res=("PASS" if ok else "FAIL"), label=label))
        if m.get("serialize_err"):
            lines.append(i18n.t("restartcheck.serialize_err",
                                err=m["serialize_err"]))
        run_v, disk_v = m.get("running_version"), m.get("disk_version")
        lines += [
            "",
            i18n.t("restartcheck.srv_ver", run=run_v, disk=disk_v)
            + (i18n.t("restartcheck.updated") if run_v != disk_v
               else i18n.t("restartcheck.same")),
            i18n.t("restartcheck.cli_ver", run=self._code_version,
                   disk=version.code_version()),
            "",
            i18n.t("restartcheck.note"),
        ]
        self.push_screen(InfoScreen(lines, title=i18n.t("restartcheck.title")))

    def _show_version_popup(self, msg):
        """서버 version 회신(version·uptime·pid) + 클라 자신의 값으로 팝업 구성.
        버전은 `p4:` 접두사를 떼고 체인지리스트 번호만 보인다. 업타임은 이 팝업이 떠
        있는 동안에도 매 초 증가하도록 tick_cb 로 줄을 재생성한다(InfoScreen)."""
        def _cl(v):                       # "p4:58794" → "58794" (그 외 형식은 그대로)
            v = str(v)
            return v[3:] if v.startswith("p4:") else v
        pid = msg.get("pid", "?")
        srv_ver = _cl(msg.get("version", "?"))
        srv_uptime0 = msg.get("uptime", 0)
        srv_recv = time.time()            # 회신 수신 시각(서버 업타임 외삽 기준)

        def make_lines():
            cli_up = version.fmt_uptime(time.time() - self._boot_time)
            srv_up = version.fmt_uptime(srv_uptime0 + (time.time() - srv_recv))
            return [
                i18n.t("version.header"),
                "",
                i18n.t("version.client", ver=_cl(self._code_version), up=cli_up),
                i18n.t("version.server", ver=srv_ver, up=srv_up),
                "",
                i18n.t("version.pid", pid=pid),
            ]
        self.push_screen(InfoScreen(make_lines(), title="version",
                                    center=True, tick_cb=make_lines))

    def _server_info_lines(self):
        """서버 정보 탭(§10-A #12) 줄 — 호스트·로컬/원격·소켓 경로·RTT·응답성.
        상태줄 서버이름(host) 클릭 시 통합 상태 팝업의 '서버' 탭으로 보인다."""
        host = socket.gethostname()
        remote = bool(getattr(self.status, "_is_remote", False))
        lines = [
            i18n.t("hoststatus.host", host=host),
            i18n.t("hoststatus.conn", kind=(i18n.t("hoststatus.conn_remote")
                                            if remote
                                            else i18n.t("hoststatus.conn_local"))),
            i18n.t("hoststatus.sock", sock=self.sock_path),
        ]
        rtt = getattr(self, "_net_last_rtt", None)
        if rtt is not None:
            thr = getattr(self, "net_rtt_threshold", 0.4)
            lines.append(i18n.t("hoststatus.rtt", rtt=f"{rtt * 1000:.0f}",
                                thr=f"{thr * 1000:.0f}"))
        graph = self._rtt_graph_lines()
        if graph:
            lines.append("")
            lines.extend(graph)
        degraded = bool(getattr(self, "_net_degraded", False))
        lines.append(i18n.t("hoststatus.resp",
                            state=(i18n.t("hoststatus.resp_degraded") if degraded
                                   else i18n.t("hoststatus.resp_ok"))))
        lines.append("")
        lines.append(i18n.t("hoststatus.degraded_hint"))
        return lines

    # 세로 막대 그래프용 블록(아래→위로 차오름). bar() 의 가로 _BAR_BLOCKS 와 별개.
    _RTT_VBLOCKS = " ▁▂▃▄▅▆▇█"

    def _rtt_graph_width(self):
        """그래프 가로 칸 수를 팝업(InfoTabsScreen) 폭에 맞춘다. 박스는 화면 92%·
        최대 100칸이고, 그 안쪽에서 테두리·패딩(4)·축 프리픽스(6)·스크롤바 여백(2)을
        빼야 좁은 화면에서 그래프 줄이 접히지 않는다(요청). 화면 폭을 모르면 기본값."""
        try:
            screen_w = int(self.size.width)
        except Exception:
            screen_w = 0
        if screen_w <= 0:
            return self._RTT_GRAPH_W
        box_w = min(100, int(screen_w * 0.92))
        avail = box_w - 4 - 6 - 2
        return max(12, min(self._RTT_GRAPH_W, avail))

    def _rtt_graph_lines(self, width=None, height=None):
        """최근 60분 RTT 표본을 세로 막대 그래프(width 칸 × height 행) 텍스트 줄로
        그린다. 각 칸은 _RTT_WINDOW/width 초 버킷이고 버킷 안 **최대** RTT 를 써
        스파이크가 묻히지 않게 한다(오른쪽 끝 = 지금, 왼쪽 = -60분). 스케일 최댓값은
        관측 peak 와 임계(threshold) 중 큰 값이라 임계선이 항상 화면 안에 든다.
        표본이 없으면 None — 호출부가 그래프 줄을 통째로 생략한다."""
        hist = getattr(self, "_net_rtt_hist", None)
        if not hist:
            return None
        if width is None:
            width = self._rtt_graph_width()
        width = width or self._RTT_GRAPH_W
        height = height or self._RTT_GRAPH_H
        now = time.monotonic()
        span = self._RTT_WINDOW
        buckets = [None] * width        # 칸별 최대 RTT(초) | None(표본 없음=공백)
        raw = []                        # 창 안 원시 표본(통계용 — 버킷 최대는 평균을 부풀림)
        for ts, rtt in hist:
            age = now - ts
            if age < 0 or age > span:
                continue
            raw.append(rtt)
            col = width - 1 - int(age / span * width)   # age 0 → 오른쪽 끝
            col = max(0, min(width - 1, col))
            cur = buckets[col]
            buckets[col] = rtt if cur is None else max(cur, rtt)
        if not raw:
            return None
        thr = getattr(self, "net_rtt_threshold", 0.4)
        peak = max(raw)
        vmax = max(peak, thr)
        VB = self._RTT_VBLOCKS
        # 칸별 채움 높이(1/8 eighths). None 칸은 공백으로 남긴다.
        eighths = [None if v is None else int(round(v / vmax * height * 8))
                   for v in buckets]
        out = [i18n.t("hoststatus.rtt_graph")]
        vmax_ms = int(round(vmax * 1000))
        for r in range(height):                 # r=0 = 맨 위 행
            base = (height - 1 - r) * 8          # 이 행 아래에 깔린 eighths
            cells = []
            for e in eighths:
                if e is None:
                    cells.append(" ")
                else:
                    cells.append(VB[max(0, min(8, e - base))])
            axis = (f"{vmax_ms:>4} ┤" if r == 0 else "     ┤")
            out.append(axis + "".join(cells))
        out.append("   0 ┴" + "─" * width)   # x축
        # 시간축 라벨(왼쪽 -60분 · 오른쪽 지금)
        left = i18n.t("hoststatus.rtt_axis_start")
        right = i18n.t("hoststatus.rtt_axis_now")
        lw = sum(_char_cells(c) for c in left)
        rw = sum(_char_cells(c) for c in right)
        pad = max(1, width - lw - rw)
        out.append("      " + left + " " * pad + right)
        peak_ms = int(round(peak * 1000))
        avg_ms = int(round(sum(raw) / len(raw) * 1000))
        out.append(i18n.t("hoststatus.rtt_stats",
                          peak=peak_ms, avg=avg_ms, n=len(raw)))
        return out


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




class _CommandMixin:
    # §5.4 명령 실행 클러스터(프롬프트 수명·셸 우회·명령 디스패치) — 모듈 레벨 분리, PytmuxApp 은 MRO 로 상속
    def open_prompt(self, purpose, placeholder="", initial="", action=None,
                    suggest=None):
        # 한 줄 입력을 Input 을 담은 바닥 모달(PromptScreen)로 받는다.
        # 모달은 별도 스크린이라 포커스가 안정적이다(메인 뷰/AUTO_FOCUS 와 무관).
        # suggest: rename 등에서 현재 이름을 **ghost(제안)** 로 띄운다 — Tab/→ 로
        #   채워 편집·덧붙이고, 그냥 타이핑하면 덮어쓴다(initial 로 미리 채우면
        #   타이핑이 덧붙던 문제, 요청). 빈 입력일 땐 placeholder 로도 흐리게 보인다.
        suggester = None
        if purpose == "command":
            suggester = SepInsensitiveSuggester(
                COMPLETIONS + self.plugins.completions,
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

    def _compose_track_input(self, pid, data: bytes):
        r"""normal 패스스루로 활성 패널에 보낸 키 입력을 패널별로 누적해 **현재
        프롬프트에 남아 있는 텍스트**(`_prompt_buf[pid]`)를 추정한다(서버
        prompt-history track_input 의 클라 판 — 그쪽은 서버라 클라가 못 읽음).
        CSI/ESC 시퀀스(화살표 등)는 건너뛰고, backspace 는 한 글자 제거, Enter(\r)
        는 제출 경계라 비우고, \n(Shift+Enter)은 줄바꿈 누적, 인쇄 가능 문자는 추가.
        '프롬프트 인계' 컴포즈(open_compose)가 이 값을 시드로 쓰고 그만큼 백스페이스로
        프롬프트를 비운다. Claude 안에서 커서를 옮겨 편집하면 어긋날 수 있는 근사치."""
        if not data:
            return
        buf = getattr(self, "_prompt_buf", None)
        if buf is None:
            buf = self._prompt_buf = {}
        s = buf.get(pid, "")
        text = data.decode("utf-8", "ignore")
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":                 # ESC/CSI: 제어 시퀀스 건너뜀
                i += 1
                if i < n and text[i] == "[":
                    i += 1
                    while i < n and not (0x40 <= ord(text[i]) <= 0x7e):
                        i += 1
                    i += 1
                else:
                    i += 1
                continue
            if ch == "\r":                   # Enter: 제출 → 프롬프트 비워짐
                s = ""
            elif ch == "\n":                 # Shift+Enter/Ctrl+J: 줄바꿈 누적
                s += "\n"
            elif ord(ch) in (8, 127):        # backspace
                s = s[:-1]
            elif ord(ch) >= 32:
                s += ch
            i += 1
        buf[pid] = s[-4000:]

    def open_compose(self, initial=None):
        """블록 선택 편집이 되는 멀티라인 작성창(ComposePromptScreen)을 연다.

        Claude Code 등 자식 프롬프트 입력기는 Shift+방향키 범위 선택 편집을
        지원하지 않고, pytmux 는 자식의 논리 버퍼·커서 인덱스를 알 수 없어 그 위에
        선택을 투명하게 얹을 수 없다(타당성 검토 A 안 비권장). 대신 pytmux 가 버퍼를
        소유하는 별도 작성창에서 작성→완료 시 활성 패널에 **bracketed paste 로 투입**
        한다(권고안 B). ESC 모드에서 Insert 로 호출(옵트인, 필요할 때 매번).

        '프롬프트 인계'(사용자 선택 2026-06-19): 활성 패널 프롬프트에 입력 중이던
        텍스트(`_prompt_buf` 추적치)가 있으면 **그 텍스트를 시드로 채우고 프롬프트는
        백스페이스로 비운다**(중복 투입 방지 — 내용이 작성창으로 '이동'). 없으면
        직전에 저장 안 하고 닫은 **초안**(`_compose_draft`)을 시드로 쓴다. 작성창을
        Esc 로 닫아도 그 내용은 `_compose_draft` 에 남아 다음에 다시 시드된다."""
        active = self.layout.get("active")
        # 시드 우선순위: ① 이 패널에 방금 친(미제출) 프롬프트 텍스트 → 인계(비움)
        #                ② 없으면 저장된 초안(취소해도 보존)
        buf = getattr(self, "_prompt_buf", None) or {}
        typed = buf.get(active, "")
        seed = initial if initial is not None else (
            typed if typed else getattr(self, "_compose_draft", ""))

        def done(result):
            if not result:        # 방어(정상 경로는 (text, injected) 튜플)
                return
            text, injected = result
            self._compose_draft = text        # 초안 보존(취소해도 다음 시드)
            if injected and text:
                # 빈(인계로 비워진) 프롬프트에 통째 투입. 서버가 pane.bracketed 면
                # \x1b[200~…201~ 로 감싸 멀티라인이 줄마다 제출되지 않는다. 끝에
                # Enter 를 붙이지 않아 자동 제출 없음(사용자가 직접 Enter).
                self.send_cmd("paste", text=text)
                buf2 = getattr(self, "_prompt_buf", None)
                if isinstance(buf2, dict):
                    buf2[active] = text   # 이제 프롬프트에 이 텍스트가 있음
            elif injected and not text:
                self.display_message(i18n.t("compose.empty"))
        # 프롬프트 인계: 현재 프롬프트의 추적분(typed)을 백스페이스로 비운다(커서가
        # 끝에 있을 때 정확 — 사용자 동의한 근사치). 비운 뒤 추적값도 초기화한다.
        if initial is None and typed:
            self.send_input(b"\x7f" * len(typed))
            if isinstance(buf, dict):
                buf[active] = ""
        # 작성창 입력 줄을 활성 패널 프롬프트 줄(하드웨어 커서 행)보다 한 칸 아래에
        # 맞춘다(_active_cursor_xy). 좌우는 활성 패널 테두리 안쪽(box 있으면 bx+1..
        # bw-2, 없으면 x..w)에 맞춘다. 미상이면 각각 바닥 도킹/전체 폭.
        xy = getattr(self, "_active_cursor_xy", None)
        prompt_row = xy[1] if xy else None
        pane_x = pane_w = None
        for p in self.layout.get("panes", []):
            if p["id"] == active:
                box = p.get("box")
                if box:
                    bx, _by, bw, _bh = box
                    pane_x, pane_w = bx + 1, max(1, bw - 2)
                else:
                    pane_x, pane_w = p["x"], p["w"]
                break
        self.push_screen(
            ComposePromptScreen(seed, prompt_row, pane_x, pane_w), done)

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
        if c in ("plugins", "plugin-manager"):
            # 플러그인 관리 팝업(PLUGIN_MANAGER_SCENARIO) — 설치된 플러그인 on/off.
            self.push_screen(PluginManagerScreen())
            return
        if c in ("help", "commands", "?", "list-commands"):
            # 명령 목록 선택기(#3): 옵션 스키마가 있으면 옵션 모달에서 값을 정해
            # 프롬프트 없이 바로 실행, 인자 없는 안전한 명령은 선택 즉시 실행,
            # 그 외(자유 텍스트 인자)는 기존처럼 명령 프롬프트에 채워 Enter 로 실행.
            all_commands = COMMANDS + self.plugins.commands
            all_options = {**COMMAND_OPTIONS, **self.plugins.command_options}
            all_noarg = COMMAND_NOARG | self.plugins.noarg

            def _picked(name):
                if not name:
                    return
                opts = all_options.get(name)
                if opts:
                    desc = next((d for n, d, *_ in all_commands
                                 if n == name), "")

                    def _run(line):
                        if line:
                            self._run_command(line)
                    self.push_screen(
                        CommandOptionsScreen(name, desc, opts), _run)
                elif name in all_noarg:
                    self._run_command(name)
                else:
                    self.open_prompt("command", "", initial=name + " ")
            self.push_screen(CommandListScreen(all_commands), _picked)
            return
        # 코어 명령 디스패치 전에 플러그인에 기회를 준다(ncd 등). 플러그인 명령은
        # 코어와 이름이 겹치지 않으므로 우선순위 충돌은 없다. 디렉토리를 지우면
        # 여기서 아무도 처리하지 않아 명령은 조용히 무시된다.
        if self.plugins.handle_command(self, c, args):
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
            # 원격 탭이면 kill_window(서버가 §1.7-c 거부) 대신 그 링크를 분리한다
            # ([x]/esc x 와 동일 라우팅, confirm_kill_tab 참조).
            rhost = self._active_remote_host()
            if rhost is not None:
                self.send_cmd("remote_detach", host=rhost)
            else:
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
            idx = self._tab_target_index(args)     # 양수 1-based·음수 끝에서(§2.8)
            if idx is not None:
                self.send_cmd("move_window", index=idx)
        elif c in ("swap-tab", "swapt", "swap-window", "swapw"):
            idx = self._tab_target_index(args)     # 양수 1-based·음수 끝에서(§2.8)
            if idx is not None:
                self.send_cmd("swap_window", index=idx)
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
            idx = self._tab_target_index(args)     # 양수 1-based·음수 끝에서(§2.8)
            if idx is not None:
                self.send_cmd("select_window", index=idx)
        elif c in ("rename-tab", "renamet", "rename-window", "renamew"):
            # 인자(이름)가 있으면 즉시 변경. 인자 없이 입력하면 **아무 동작 없이
            # 취소**한다(예전 rename 프롬프트 인터페이스를 열지 않음 — 사용자 요청).
            # 이름 입력 ghost 프롬프트는 prefix+, 키로만 연다(_handle_prefix).
            name = " ".join(a for a in args if not a.startswith("-"))
            if name:
                self.send_cmd("rename_window", name=name)
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
                                  cells=(_first_int(args) or 3))
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
            # -s/-t <번호>: display-panes(prefix q) 오버레이의 0-based 패널
            # 번호로 임의의 두 패널을 교환(마우스 헤더 드래그와 같은
            # swap_pane_to 경로). -t 만 주면 활성 패널과, -s -t 둘 다면 그 두
            # 패널을 맞바꾼다. -s/-t 가 없으면 기존 인접 순환 swap(-U=이전·기본
            # 다음). §2.3: 마우스 전용이던 임의 swap 을 명령/키 경로로 대칭화.
            if "-s" in args or "-t" in args:
                a = self._pane_id_by_index(_opt_value(args, "-s"))
                b = self._pane_id_by_index(_opt_value(args, "-t"))
                act = self.layout.get("active")
                a = a if a is not None else act
                b = b if b is not None else act
                if a is not None and b is not None and a != b:
                    self.send_cmd("swap_pane_to", id=a, to_id=b)
                # 유효하지 않은 번호(범위밖·비숫자)면 조용히 무시 — 인접
                # swap 으로 떨어지지 않는다(엉뚱한 패널 교환 방지).
            else:
                self.send_cmd("swap_pane", forward=("-U" not in args))
        elif c in ("break-pane", "breakp"):
            self.send_cmd("break_pane")
        elif c in ("join-pane", "joinp"):
            self.send_cmd("join_pane", orient=("lr" if "-h" in args else "tb"))
        elif c in ("respawn-pane", "respawnp"):
            self.send_cmd("respawn_pane")
        elif c in ("capture-output", "capture-toggle"):
            val = None
            if "on" in args:
                val = True
            elif "off" in args:
                val = False
            self.send_cmd("set_capture", value=val)
            state = (i18n.t("word.toggle") if val is None
                     else ("ON" if val else "OFF"))
            self.display_message(i18n.t("msg.capture_toggle", state=state))
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
        elif c in ("inactive-dim", "dim-inactive"):
            # §2.9 비활성 패널 dim 세션 토글(클라-로컬 표현; 영속 기본값은 config
            # inactive_dim). 인자 on/off, 없으면 반전. 즉시 재합성해 반영.
            if "on" in args:
                self.inactive_dim = True
            elif "off" in args:
                self.inactive_dim = False
            else:
                self.inactive_dim = not self.inactive_dim
            self._composite()
            self.display_message(i18n.t(
                "msg.inactive_dim",
                state=("ON" if self.inactive_dim else "OFF")))
        elif c in ("inactive-dim-ratio", "dim-inactive-ratio"):
            # §2.9 비활성 패널 dim 세기(0~0.8) 런타임 조정. 인자 숫자 = 그 값으로
            # 설정(범위 클램프), 인자 없으면 현재 값 표시. 영속 기본값은 config
            # inactive_dim_ratio. 즉시 재합성해 반영(클라-로컬 표현).
            arg = args[0] if args else None
            if arg is not None:
                try:
                    self.inactive_dim_ratio = max(0.0, min(0.8, float(arg)))
                except (TypeError, ValueError):
                    self.display_message(i18n.t("msg.inactive_dim_ratio_bad"))
                    return
                self._composite()
            self.display_message(i18n.t(
                "msg.inactive_dim_ratio",
                ratio=f"{self.inactive_dim_ratio:.2f}"))
        elif c in ("settings", "config", "preferences", "prefs", "옵션"):
            # 통합 설정 화면(SETTINGS 레지스트리). 흩어진 설정을 한 곳에서 보고/바꾼다.
            # 링크 행(Claude·플러그인 전용 화면)을 고르면 그 명령을 돌려받아 디스패치한다.
            def _after_settings(line):
                if line:
                    self._run_command(line)
            # '키' 탭에 현재 prefix·사용자 바인딩을 보여주려 컨텍스트를 넘긴다(읽기 전용).
            self.push_screen(SettingsScreen(
                prefix_key=self.prefix_key,
                user_bindings=dict(self.bindings),
                root_bindings=dict(self.root_bindings)), _after_settings)
        elif c in ("detach-client", "detach"):
            if "-a" in args:
                self.send_cmd("detach_others")
            else:
                self.exit(message="detached")
        elif c == "kill-server":
            self.confirm_kill_server()
        elif c in ("remote-attach", "remote_attach"):
            # §1.7 페더레이션: 원격 pytmux 서버 탭을 이 pytmux 탭바에 병합.
            # 성공하면 ⇄host:이름 탭이 나타난다(선택=진입). ssh -T 가 전송.
            # host 는 shlex 토큰이 아니라 **원시 잔여 문자열** — 도메인 계정
            # (NATGAMES\user@host)의 백슬래시를 shlex(posix)가 삼키지 않게.
            rest = line.split(None, 1)
            host = rest[1].strip() if len(rest) > 1 else ""
            if host:
                self.send_cmd("remote_attach", host=host)
            else:
                self.display_message(i18n.t("msg.remote_attach_usage"))
        elif c in ("remote-new-tab", "remote_new_tab", "remote-new-window"):
            # §1.7 페더레이션: 원격 pytmux 에 **새 터미널**을 만들어 이 pytmux 의
            # 새 탭으로 붙인다(remote-attach 가 기존 원격 탭을 병합·열람만 하는 것과
            # 달리 원격에 새 셸을 띄운다). 아직 attach 안 됐으면 먼저 attach 한다.
            # host 는 remote-attach 와 같이 원시 잔여 문자열(백슬래시 보존).
            rest = line.split(None, 1)
            host = rest[1].strip() if len(rest) > 1 else ""
            if host:
                self.send_cmd("remote_new_window", host=host)
            else:
                self.display_message(i18n.t("msg.remote_newtab_usage"))
        elif c in ("remote-detach", "remote_detach"):
            rest = line.split(None, 1)
            host = rest[1].strip() if len(rest) > 1 else ""
            self.send_cmd("remote_detach",
                          **({"host": host} if host else {}))
        elif c in ("restart-server", "restart"):
            # 작업 보존 재시작: 셸/PTY 를 살린 채 서버 코드만 교체(re-exec).
            # 화면이 잠깐 끊겼다 재접속된다(docs/internal/RESTART_SCENARIO.md).
            # 실행 전 드라이런으로 안전성을 먼저 점검한다.
            self.begin_restart("server")
        elif c in ("restart-check", "restart-dry-run", "restart-all-check"):
            # restart-all 드라이런: 실제 재시작 없이 안전성만 점검해 팝업으로 보고.
            self.open_restart_check()
        elif c in ("restart-all", "full-restart", "restart-client-server"):
            # 전체 재시작: 서버는 work-preserving re-exec(셸/세션 보존), 동시에
            # 클라이언트도 자신을 relaunch(새 클라 코드로 재attach). 서버/클라
            # 코드를 모두 갱신하면서 작업은 보존한다(docs/internal/RESTART_SCENARIO.md).
            # 실행 전 드라이런으로 안전성을 먼저 점검한다.
            self.begin_restart("all")
        elif c in ("reconnect", "resync"):
            # IPC 강제 재접속(§10): degraded(빨간 외곽선) 고착 시 정체된 소켓을
            # 버리고 새로 세워 회복한다. 서버 PTY/세션·실행 중 Claude 는 보존.
            self.reconnect_now("manual")
        elif c in ("redraw", "refresh", "refresh-client"):
            # 화면 전체 강제 재그리기(§2.12, tmux refresh-client/Ctrl-L 해당): 여러
            # 상황(alt-screen 앱이 repaint 안 함·합성 스냅샷 stale·터미널 깨짐·원격
            # 잔상)에서 화면이 정상 재그리기 안 될 때 회복한다. 서버가 ① 각 패널 PTY 에
            # SIGWINCH 를 유발해 alt-screen 앱(vim/claude/htop)이 전체 repaint 하게 하고
            # ② 이 클라에 layout+screen 전체 프레임을 다시 보낸다(stale 스냅샷 교체).
            # 클라도 자기 합성을 즉시 다시 돌려 순수 클라측 잔상도 지운다. 원격 탭을
            # 보는 중이면 서버가 업스트림으로 릴레이해 원격 화면이 재그려진다.
            self.send_cmd("request_redraw")
            self._composite()
            self.refresh()
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
            idx = _first_int(args)
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
        # clock-mode/calendar-mode/open-clock/close-clock/open-calendar/
        # close-calendar(별칭 clock·calendar·cal·open-cal·close-cal)은 clock·
        # calendar 플러그인이 handle_command(위 폴백)로 처리한다.
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
        elif c == "nest-auto-attach":
            # nest-auto-attach [on|off|toggle] — 원격 중첩 자동 승격(NESTED_
            # ATTACH ㉢). 서버 내부 동작이라 클라 렌더 변화 없음. 서버가
            # opts.json 영속, 인자 없으면 toggle(서버가 반전).
            arg = args[0].lower() if args else "toggle"
            val = (arg == "on") if arg in ("on", "off") else None
            self.send_cmd("set_nest_auto_attach", value=val)
        elif c == "vt-parser":
            # vt-parser [pyte|native] — VT 파서 백엔드 선택(docs/VT_PARSER_TRADEOFF §8).
            # **재시작 시 발효**: 기존 패널은 즉시 안 바뀌고, 다음 작업 보존 재시작
            # (restart-server)·respawn 에서 새 백엔드를 채택한다. 서버가 opts.json 영속.
            val = (args[0].lower() if args else "")
            if val not in ("pyte", "native"):
                self.display_message(i18n.t("msg.vt_parser_usage"))
            else:
                self.send_cmd("set_vt_parser", value=val)
                self.display_message(i18n.t("msg.vt_parser_set", val=val))
        elif c in ("lang", "language"):
            # lang ko|en — UI 로케일 전환(§6 i18n). 클라이언트-로컬: 즉시 set_locale
            # +영속 후 전체 재합성으로 상태줄·헤더·메뉴를 새 언어로 다시 그린다(언어
            # 전환 자체가 즉시 보이는 피드백). 인자가 없거나 미지원이면 사용법 팝업.
            arg = args[0].lower() if args else ""
            if arg in i18n.available():
                self.lang = arg
                i18n.set_locale(arg)
                i18n.save_persisted(self.sock_path, arg)
                self._composite()
            else:
                self.push_screen(InfoScreen([i18n.t("lang.usage")],
                                            title="lang"))
        elif c in ("version", "about"):
            # 클라/서버 버전(p4 CL)·업타임 팝업.
            self.open_version()
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
                    text.splitlines()[:60] or [i18n.t("msg.display_no_output")],
                    title="popup"))
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
            # bind-key -n <key> <command...> — root table(§2.5, prefix 없이 바로).
            # 키는 tmux 표기(C-x)도 받아 textual(ctrl+x)로 정규화. 한 글자는 그대로.
            # 첫 인자만 키, 나머지는 명령 원문(플래그 -h 등 보존)으로 그대로 쓴다.
            root = bool(args) and args[0] == "-n"
            rest = args[1:] if root else args
            if len(rest) >= 2:
                key, warn = normalize_binding_key(rest[0])
                table = self.root_bindings if root else self.bindings
                table[key] = " ".join(rest[1:])
                self.display_message(
                    warn if warn else
                    (f"bound (root) {key}" if root else f"bound {key}"))
        elif c in ("unbind-key", "unbind", "unbindkey"):
            # unbind-key <key> | -n <key>(root) | -a (양 테이블 전체 해제).
            # 없는 키는 조용히 무시.
            if "-a" in args:
                n = len(self.bindings) + len(self.root_bindings)
                self.bindings.clear()
                self.root_bindings.clear()
                self.display_message(f"unbound all ({n})")
            else:
                root = args[:1] == ["-n"]
                pos = [a for a in args if not a.startswith("-")]
                if pos:
                    key = _tmux_key_to_textual(pos[0])
                    table = self.root_bindings if root else self.bindings
                    if table.pop(key, None) is not None:
                        self.display_message(f"unbound {key}")
                    else:
                        self.display_message(f"no binding: {key}")
        elif c in ("list-keys", "lsk", "list-binds", "mouse-help", "mouse"):
            # §2.2 발견성: 구현된 마우스 제스처(헤더 드래그 pick-up→swap/탭이동,
            # 탭 드래그 재정렬·분할, Shift+드래그 선택 등)는 명령이 아니라 ?목록·
            # 메뉴 어디에도 안 떠 사장돼 있었다. list-keys 가 사용자 바인딩과 함께
            # 1급 마우스 제스처를 먼저 보여 노출한다(동작 변경 없음, 표시만 추가).
            tr = i18n.t
            lines = [tr("keys.mouse_header", default="마우스 제스처")]
            lines += ["  " + tr(k, default=d) for k, d in (
                ("keys.g_click", "휠 — 스크롤백 스크롤 · 클릭 — 패널 포커스"),
                ("keys.g_rclick", "우클릭 — 패널 메뉴(분할·줌·회전·삭제…)"),
                ("keys.g_divider", "경계선 드래그 — 패널 크기 조절"),
                ("keys.g_header", "패널 헤더(위 테두리) 드래그 — 패널을 들어 "
                 "다른 패널과 swap · 탭으로 이동 · [+]에 놓아 새 탭"),
                ("keys.g_shift", "Shift+드래그 — 텍스트 선택(클립보드 복사)"),
                ("keys.g_tab", "탭 드래그 — 탭 재정렬 · 패널 위로 끌어 분할"),
            )]
            binds = [f"prefix {k} → {v}"
                     for k, v in sorted(self.bindings.items())]
            binds += [f"(root) {k} → {v}"
                      for k, v in sorted(self.root_bindings.items())]
            lines += ["", tr("keys.user_header", default="사용자 키 바인딩")]
            lines += binds or ["  " + tr("keys.none", default="(없음)")]
            self.push_screen(InfoScreen(
                lines, title=tr("keys.title", default="키 · 마우스")))
        # 알 수 없는 명령은 조용히 무시


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
        # Claude footer 클릭존(§10 item 2/3) 재계산은 claude-code 플러그인의
        # client_render 훅(아래)이 매 합성마다 비우고 다시 채운다.
        for p in self.layout.get("panes", []):
            if p["id"] == active:
                _abox = p.get("box")
                self._active_pane_right = (_abox[0] + _abox[2]) if _abox \
                    else (p["x"] + p["w"])
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
            # 비활성 패널 dim(§2.9, 요청): 한 탭에 패널이 둘 이상일 때 비활성 패널을
            # 활성 대비 한 톤 옅게 그려 외곽선 없이도 활성 패널을 구분한다. config 로
            # on/off·세기(0~0.8) 조정, 런타임 `inactive-dim` 명령으로 세션 토글(영속=config).
            self.inactive_dim = bool(config.get("inactive_dim", True))
            try:
                self.inactive_dim_ratio = max(0.0, min(0.8,
                    float(config.get("inactive_dim_ratio", 0.18))))
            except (TypeError, ValueError):
                self.inactive_dim_ratio = 0.18
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
                self.confirm_popup(
                    i18n.t("dialog.detach_remote_msg", host=rhost),
                    action=lambda: self.send_cmd("remote_detach", host=rhost),
                    title=i18n.t("dialog.detach_remote_title"))
                return
            # 이 탭을 닫으면 pytmux 가 끝나는가 = 탭이 하나뿐인가(#16).
            last = len(self.tabbar.tabs) <= 1
            if last:
                msg = i18n.t("dialog.kill_pytmux_msg")
                title = i18n.t("dialog.kill_pytmux_title")
            else:
                msg = i18n.t("dialog.kill_tab_msg")
                title = i18n.t("dialog.kill_tab_title")
            self.confirm_popup(
                msg, action=lambda: self.send_cmd("kill_window"),
                title=title, yes_label=None, danger=last)

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
                           "auto_rename", "border_status",
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
                asyncio.create_task(write_msg(self.writer, {
                    "t": "input", "pane": self.layout.get("active"),
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
            if key == "mode-keys":
                return self.mode_keys
            if key == "alt-scroll":
                return "on" if self.disable_alt_scroll else "off"
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
                    "nest-auto-attach": "nest_auto_attach",
                    "vt-parser": "vt_parser",
                    "automatic-rename": "auto_rename",
                    "pane-border-status": "border_status",
                    "monitor-activity": "monitor_activity",
                    "monitor-bell": "monitor_bell"}
            if key in _srv:
                v = self.server_opts.get(_srv[key])
                if v is None:
                    return None
                if key == "vt-parser":
                    return v
                return "on" if v else "off"
            return None   # 링크 등


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
