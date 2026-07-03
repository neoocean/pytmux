"""접속/재시작 수명 믹스인 — client.py 에서 분리한 PytmuxApp 믹스인(§5.4 파일 분할, CODE_REVIEW 4-1).

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
# 평상 EOF(slow-client backpressure 드롭/네트워크 끊김) 자동 재접속의 무한 루프
# 가드 — 이 창(초) 안에서 이 횟수를 넘게 드롭되면(지속 대량 출력) 재접속을 포기하고
# 종료한다(매번 드롭→재접속을 영원히 반복하지 않게).
_DROP_RECONNECT_WINDOW = 20.0
_DROP_RECONNECT_MAX = 6
# 평상 EOF 재접속 재시도 횟수 — backpressure 드롭이면 서버가 살아 있어 보통 첫
# 시도에 붙고, 정말 죽었으면(kill-server) 소켓이 사라져 곧 실패해 종료로 떨어진다.
# 짧게(~0.5s) 둬서 서버 종료 시 클라 종료가 지연되지 않게 한다.
_RECONNECT_RETRIES_DROP = 25


class _NetReconnectMixin:
    """IPC 소켓 reader 태스크 + 재접속(재시작 재개·degraded 강제 회복) + RTT
    히스테리시스. 서버 PTY/세션은 안 건드리고 클라↔서버 소켓만 다룬다(§10)."""

    _RTT_WINDOW = 3600.0   # RTT 이력 보존/그래프 창(초) — 최근 60분
    _RTT_GRAPH_W = 48      # 그래프 가로 칸(시간 버킷 수)
    _RTT_GRAPH_H = 5       # 그래프 세로 행(각 행 = 1/8 정밀 세로 막대)
    _RTT_SAVE_EVERY = 60   # 이 표본 수마다 이력 파일 재기록(0.5초 핑 → ≈30초)

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
                #
                # Windows 예외: execv 가 진짜 exec 이 아니라 CreateProcess+종료라, 새
                # 클라가 **살아 있는 콘솔을 상속**한다. 이 핸드오프 뒤 Textual 입력
                # 경로(win32.EventMonitor→XTermParser)가 외부 터미널의 SGR 마우스
                # 리포트(\x1b[<..M)를 마우스로 못 알아보고 키 입력으로 재발행 → 활성
                # 패널에 마우스 좌표가 텍스트로 새고(사용자 보고 2026-07-01), 콘솔이
                # VT-마우스 상태로 고착돼 터미널 창을 새로 열어야 풀렸다. 서버는 host
                # 모드로 세션을 보존하므로 Windows 에서는 execv 대신 **제자리 재접속(ⓔ)**
                # 으로 처리한다 — 콘솔/마우스 상태를 안 건드려 누출이 없다. 트레이드오프:
                # 클라 코드(client*.py) 변경은 이 경로로 반영 안 됨(전체 재시작 필요).
                if self._reconnecting:
                    if self._relaunch_on_restart and self._use_execv_relaunch():
                        self._relaunch = True
                        self.exit()
                    else:
                        if self._relaunch_on_restart:  # Windows: relaunch→제자리 재접속
                            self._relaunch_on_restart = False
                            self._win_restart_note = True
                        await self._reconnect()
                    return
                # 평상(플래그 없는) EOF: 서버가 우리를 떨궜거나(slow-client backpressure
                # 드롭 — serverio._drop_slow_client 가 bye 없이 writer 를 close; **서버는
                # 살아 있다**) 서버가 종료됐다. 종전엔 무조건 self.exit() 라, 모바일 ssh
                # 대량 출력으로 backpressure 드롭이 날 때마다 멀쩡한 서버를 두고 클라가
                # 통째로 종료됐다(사용자 보고 2026-06-25). net_auto_reconnect 면 재접속을
                # 시도해 — 서버 생존이면(드롭) 화면을 복구하고, 정말 죽었으면(소켓 부재→
                # 재접속 실패) 종료한다. 무한 재접속 루프(지속 폭주)는 카운터로 막는다.
                if self.net_auto_reconnect and self._drop_reconnect_ok() \
                        and await self._reconnect_after_drop():
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
        if cellwidth.ambiguous_wide():    # 재접속 시에도 서버에 모호폭 모드 재통지
            hello["ambig"] = "wide"
        tok = ipc.read_token(self.sock_path)   # 연결 인증(F1)
        if tok:
            hello["token"] = tok
        if self.session_name:
            hello["session"] = self.session_name
        await write_msg(self.writer, hello)
        return True

    def _use_execv_relaunch(self) -> bool:
        """restart-all 시 클라를 os.execv 로 relaunch 할지 여부. Windows 에서는
        execv 가 진짜 exec 이 아니라 CreateProcess+종료라 새 클라가 **살아 있는
        콘솔을 상속**하고, 그 핸드오프 뒤 Textual 입력 경로가 외부 터미널의 SGR
        마우스 리포트를 키 입력으로 오파싱해 활성 패널에 좌표가 텍스트로 새는
        문제가 있다(2026-07-01 보고, 위 _read 핸들러 주석 참조). 그래서 Windows 는
        execv 를 쓰지 않고 제자리 재접속(ⓔ)으로 폴백한다(서버 host 모드가 세션 보존).
        테스트는 이 메서드를 패치해 OS 분기를 결정론적으로 검증한다."""
        return os.name != "nt"

    async def _reconnect(self):
        """서버가 re-exec 로 재기동되는 동안 같은 소켓으로 재접속한다(ⓔ).
        새 서버가 listen 을 다시 열 때까지 잠깐 재시도한 뒤 hello 로 재개."""
        self._reconnecting = False
        if not await self._connect_and_hello(_RECONNECT_RETRIES_RESTART):
            self.exit(message=i18n.t("msg.reconnect_failed"))
            return
        # Windows 전체 재시작(restart-all)을 execv 대신 제자리 재접속으로 처리한
        # 경우, 클라 코드가 갱신되지 않았음을 한 줄로 안내한다(위 끊김 핸들러 참조).
        if getattr(self, "_win_restart_note", False):
            self._win_restart_note = False
            self.display_message(i18n.t("msg.restart_done_win_noclient"))
        else:
            self.display_message(i18n.t("msg.restart_done"))
        self._start_reader()

    def _drop_reconnect_ok(self) -> bool:
        """평상 EOF(backpressure 드롭) 자동 재접속을 짧은 창 내 _DROP_RECONNECT_MAX
        회로 제한한다 — 지속 대량 출력으로 매번 드롭→재접속하는 무한 루프를 막아
        그 한도를 넘으면 종료(호출부 self.exit)로 떨어지게 한다."""
        now = time.monotonic()
        ts = [t for t in getattr(self, "_drop_ts", ())
              if now - t < _DROP_RECONNECT_WINDOW]
        ts.append(now)
        self._drop_ts = ts
        return len(ts) <= _DROP_RECONNECT_MAX

    async def _reconnect_after_drop(self) -> bool:
        """평상 EOF(slow-client backpressure 드롭/네트워크 끊김)에서 재접속을 시도한다.
        서버가 살아 있으면(드롭) 새 연결+hello 후 _start_reader 로 화면을 복구하고 True,
        실패(서버 종료·소켓 부재)면 False(호출부가 self.exit). _force_reconnect 와 같은
        경로지만 **실패 시 앱을 닫지 않고** 성공/실패를 돌려준다(드롭은 종료 가능성 포함)."""
        if not await self._connect_and_hello(_RECONNECT_RETRIES_DROP):
            return False
        # 새 채널이니 degraded/네트워크 표본 리셋(_force_reconnect 와 동일).
        self._net_ping_ts = None
        self._net_bad = self._net_good = 0
        if self._net_degraded:
            self._net_degraded = False
            self._composite()
        self.display_message(i18n.t("msg.reconnected_resync"))
        self._start_reader()       # 새 reader 태스크(새 세대) — 서버 _send_full 수신
        return True

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
        # 영속(서버/클라 재시작에도 60분 그래프 유지): 일정 개수마다 파일을 전체
        # 재기록한다. crash 시 최대 _RTT_SAVE_EVERY 표본만 손실(허용). 종료 시점은
        # on_unmount 에서 한 번 더 확정 저장한다.
        self._rtt_save_n += 1
        if self._rtt_save_n >= self._RTT_SAVE_EVERY:
            self._save_rtt_hist()
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

    def _rtt_hist_path(self):
        """60분 RTT 이력 영속 파일 경로(`<state>.rtthist.json`). 영속 off 거나
        sock_path 가 없으면(테스트 등) None — 호출부가 조용히 건너뛴다."""
        if not getattr(self, "net_rtt_persist", True):
            return None
        sock = getattr(self, "sock_path", None)
        if not sock:
            return None
        return ipc.state_base(sock) + ".rtthist.json"

    def _load_rtt_hist(self):
        """영속된 최근 60분 RTT 표본을 메모리 이력(_net_rtt_hist)으로 복원한다.
        파일은 벽시계(time.time) 타임스탬프로 저장돼 있어 재시작/리부트에도 의미가
        있다 — 현재 monotonic 기준으로 환산해 그래프 코드가 그대로 쓰게 한다(이력
        튜플은 monotonic_ts 규약). 창(_RTT_WINDOW) 밖 표본(서버 꺼져 있던 구간
        포함)은 버려, 데이터가 있는 구간만 남는다(빈 버킷=그래프 공백→건너뜀)."""
        path = self._rtt_hist_path()
        if not path:
            return
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        samples = data.get("samples") if isinstance(data, dict) else None
        if not samples:
            return
        wall_now = time.time()
        cutoff = wall_now - self._RTT_WINDOW
        offset = wall_now - time.monotonic()    # mono = wall - offset
        restored = []
        for item in samples:
            try:
                wts, rtt = float(item[0]), float(item[1])
            except (TypeError, ValueError, IndexError):
                continue
            if wts < cutoff or wts > wall_now:   # 창 밖/미래(시계 역행) 표본 제외
                continue
            restored.append((wts - offset, rtt))
        restored.sort()
        self._net_rtt_hist = restored

    def _save_rtt_hist(self):
        """현재 메모리 이력을 벽시계 타임스탬프로 파일에 저장(best-effort, 전체
        재기록). monotonic→벽시계 환산해 저장하므로 재시작 후 _load_rtt_hist 가
        창 안 표본만 복원한다. 임시파일+os.replace 로 부분기록을 피한다. 저장이
        핑/표시 흐름을 막지 않게 예외는 삼킨다(netdbg 와 같은 정책)."""
        path = self._rtt_hist_path()
        if not path:
            return
        self._rtt_save_n = 0
        hist = getattr(self, "_net_rtt_hist", None)
        if hist is None:
            return
        offset = time.time() - time.monotonic()
        try:
            import json
            samples = [[round(ts + offset, 3), round(rtt, 6)] for ts, rtt in hist]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"samples": samples}, f)
            os.replace(tmp, path)
        except OSError:
            pass

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
        스파이크가 묻히지 않게 한다(오른쪽 끝 = 지금, 왼쪽 = -60분).

        세로 스케일(vmax)은 관측 peak 에 **자동**으로 맞춘다 — 임계(보통 400ms)에
        고정하지 않으므로 ~1ms 같은 낮은 RTT 도 0 으로 뭉개지지 않고 변동이 보인다.
        임계가 스케일 안(peak 이상으로 느림)이면 그 높이에 점선(┄) 기준선을 따로
        긋고, 임계가 스케일 위(정상 — peak<임계)면 기준선은 화면 밖이라 생략한다.

        표본이 **없는** 칸(클라가 안 떠 측정이 없던 구간)은 공백이 아니라 바닥에
        '·' 마커로 그려, '측정 없음' 을 '측정값이 0 에 가까움' 과 구분한다(요청).
        표본이 하나도 없으면 None — 호출부가 그래프 줄을 통째로 생략한다."""
        hist = getattr(self, "_net_rtt_hist", None)
        if not hist:
            return None
        if width is None:
            width = self._rtt_graph_width()
        width = width or self._RTT_GRAPH_W
        height = height or self._RTT_GRAPH_H
        now = time.monotonic()
        span = self._RTT_WINDOW
        buckets = [None] * width        # 칸별 최대 RTT(초) | None(표본 없음)
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
        vmax = peak or thr or 1e-9      # peak 기준 자동 스케일(전부 0 일 때 가드)
        VB = self._RTT_VBLOCKS
        total8 = height * 8
        # 칸별 채움 높이(1/8 eighths). None=측정 없음. 측정값은 최소 1/8 로 띄워
        # '0 에 가까운 측정' 이 '측정 없음(공백)' 과 시각적으로 구분되게 한다.
        eighths = [None if v is None else max(1, min(total8,
                   int(round(v / vmax * total8)))) for v in buckets]
        # 임계 기준선 행(스케일 안일 때만; r=0=맨 위). thr_e=임계의 1/8 높이.
        thr_e = int(round(thr / vmax * total8))
        thr_row = (height - 1 - (thr_e - 1) // 8) if 0 < thr_e <= total8 else None
        out = [i18n.t("hoststatus.rtt_graph")]
        vmax_ms = int(round(vmax * 1000))
        thr_ms = int(round(thr * 1000))
        for r in range(height):                 # r=0 = 맨 위 행
            base = (height - 1 - r) * 8          # 이 행 아래에 깔린 eighths
            on_thr = (r == thr_row)
            cells = []
            for e in eighths:
                if e is None:                    # 측정 없음
                    cells.append("┄" if on_thr else
                                 ("·" if r == height - 1 else " "))
                    continue
                blk = e - base
                if blk > 0:
                    cells.append(VB[min(8, blk)])
                else:                            # 막대 미도달 — 임계선이면 점선
                    cells.append("┄" if on_thr else " ")
            if r == 0:
                axis = f"{vmax_ms:>4} ┤"
            elif on_thr:
                axis = f"{thr_ms:>4} ┄"          # 임계 기준선 라벨
            else:
                axis = "     ┤"
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
        # 측정 없는 칸('·')이 하나라도 있으면 그 마커 범례를 덧붙인다(꽉 차면 생략).
        if any(e is None for e in eighths):
            out.append(i18n.t("hoststatus.rtt_legend"))
        return out
