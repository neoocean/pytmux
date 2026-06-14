"""REC(패널 출력 캡처) 플러그인 — 각 패널 raw PTY 출력을 무손실 기록한다.

코어(server/serverpty/serverio/serverpersist)는 캡처를 **이름으로 직접 부르지 않고**
레지스트리 훅으로만 닿는다. 이 디렉토리를 지우면:
- `server_mixin()` 이 빠져 캡처 메서드(`_capture_write`/`set_capture`/`capture_dir` 등)가
  Server 에서 사라지고,
- `server_pty_output` 훅이 no-op 라 코어 PTY 루프가 바이트를 그냥 흘려보내며(기록 안 함),
- `server_status` 가 빠져 status 에 `capture*` 키가 안 실리고(클라 배지·팝업도 데이터
  부재로 자동 비활성),
- `capture-output`/`capture-toggle` 명령이 검색·자동완성·디스패치 어디에도 안 나타난다.
코어는 **에러 없이** 그대로 동작한다(delete-to-disable). 참고: docs/REC_SCENARIO.md.

무게 규칙: 이 모듈은 `textual`/`rich` 를 import 하지 않는다(서버도 plugins.load() 로 같은
코드를 읽는다). 서버 믹스인은 `server_mixin()` 이 지연 import 한다.
"""
from __future__ import annotations

# ---- 명령 메타데이터(코어 COMMANDS/COMPLETIONS/COMMAND_OPTIONS 에 병합) ----
COMMANDS = [
    ("capture-output", "패널 출력 캡처 토글 [on|off] (기본 off, 영속)", "모니터"),
    ("capture-toggle", "패널 출력 캡처 토글(별칭) [on|off]", "모니터"),
]
NOARG = {"capture-output", "capture-toggle"}
# 옵션(선택지) 스키마 — 팔레트에서 on/off/토글을 키보드로 고른다.
_ONOFF = [("토글", ""), ("켜기", "on"), ("끄기", "off")]
COMMAND_OPTIONS = {
    "capture-output": [{"key": "state", "label": "캡처", "choices": _ONOFF}],
}


class _RecPlugin:
    name = "rec"
    # REC 의 "깃헙 배포 기본 OFF"(SECURITY_REVIEW §F4)는 **플러그인 비활성**이 아니라
    # **capture 옵션 기본 False**로 구현한다(server_opts_init, CL 58771) — 새 설치는
    # 캡처를 안 하지만(=badge 없음, raw 기록 없음) `capture-output` 명령으로 켤 수 있게
    # 플러그인 자체는 활성으로 둔다(발견성). 따라서 default_enabled 은 True(기본) —
    # 플러그인 관리 팝업으로는 끌 수 있으나 시드 비활성 대상은 아니다. (서버 믹스인
    # 플러그인을 시드 비활성하면 부팅 시 server_init 이 안 돌아 상태 미설치 → 런타임
    # 재활성이 재시작 전까지 불완전해지는 문제도 피한다. PLUGIN_MANAGER_SCENARIO §5·§8.)
    description = "패널 출력 캡처(REC) — Claude 화면 문구 분석용 무손실 로그"
    category = "모니터"
    commands = COMMANDS
    noarg = NOARG
    command_options = COMMAND_OPTIONS

    # ---- 서버 측 ----
    def server_mixin(self):
        """서버측 캡처 로직 믹스인(server.Server 의 동적 베이스로 합성). 지연 import —
        디렉토리를 지우면 server_mixins() 가 비어 캡처 로직이 Server 에서 빠진다."""
        from .servermixin import ServerRecMixin
        return ServerRecMixin

    def server_init(self, server):
        """Server.__init__ 1회 훅 — 캡처 런타임 상태(열린 파일 핸들 맵)를 설치한다
        (코어 server.__init__ 에서 이전). 디렉토리 삭제 시 no-op 라 코어 server 엔 이
        상태가 안 생기고, 읽는 캡처 메서드도 함께 사라진다(delete-to-disable).
        (계약 테스트가 server=None 을 넘기는 경우를 대비해 방어한다.)"""
        if server is None:
            return
        server._capfiles = {}    # pane.id -> 열린 바이너리 파일
        server._cappaths = {}    # pane.id -> 그 핸들의 캡처 경로(파일명에 시각이 박혀 보관)

    def server_opts_init(self, server, opts):
        """opts.json → server.capture 설치(코어 __init__ 의 _opts.get('capture') 이전).
        plugin_opts 네임스페이스 우선, 없으면 구 top-level 'capture' 키 폴백(업그레이드
        무중단 — 기존 사용자의 ON 선택 보존). **기본 False**(깃헙 배포 OFF, F4)."""
        po = opts.get("plugin_opts")
        po = po if isinstance(po, dict) else {}
        raw = po["capture"] if "capture" in po else opts.get("capture", False)
        server.capture = bool(raw)

    def server_opts_serialize(self, server):
        """server.capture → opts.json plugin_opts.capture(코어 _save_opts 의 'capture'
        줄 이전). 코어는 plugin_opts 밑에 불투명하게 저장한다."""
        return {"capture": bool(getattr(server, "capture", False))}

    def server_status(self, server, sess, win, msg, full):
        """status 메시지에 capture/capture_path/capture_size 를 채운다(serverio
        _status_msg 에서 이전). 디렉토리 삭제 시 키가 빠지고 클라는 그 키를 안 본다.
        (_capture_info 는 합성된 믹스인 메서드 — 계약 테스트가 server=None 을 넘기는
        경우를 대비해 방어한다.)"""
        info = getattr(server, "_capture_info", None)
        if info is None:
            return
        ap = win.active_pane if win else None
        cap_path, cap_size = info(ap)
        msg["capture"] = bool(getattr(server, "capture", False))
        msg["capture_path"] = cap_path
        msg["capture_size"] = cap_size

    def server_command(self, server, client, sess, action, msg):
        """set_capture 액션 처리(serverio _handle_cmd 의 capture 분기 이전). 'send_full'
        반환 → 코어가 요청 클라에 _send_full(종전 동작과 동일)."""
        if action == "set_capture":
            server.set_capture(msg.get("value"))
            return "send_full"
        return None

    def server_pty_output(self, server, pane, data):
        """패널 PTY 출력 1조각 — 캡처 ON 이면 파일에 무손실 append(serverpty
        _ingest_slice 의 `if self.capture: self._capture_write` 이전). 이 훅은 30Hz
        드레인의 모든 바이트마다 불리는 핫패스다(REC_SCENARIO §10 ②)."""
        if getattr(server, "capture", False):
            server._capture_write(pane, data)

    def server_shutdown(self, server):
        """서버 종료·재시작(re-exec) 경계 — 열린 캡처 파일을 닫는다(serverio.shutdown·
        serverpersist 재시작 경로의 _close_all_capfiles 이전). 파일은 buffering=0(즉시
        flush)이라 누락돼도 데이터 손실은 아니나 핸들을 깔끔히 정리한다."""
        if hasattr(server, "_close_all_capfiles"):
            server._close_all_capfiles()

    # ---- 클라이언트 측(표시 전용) — clientside.py 지연 import ----
    def client_statusbar_init(self, app, status):
        """StatusBar 생성 직후 — REC 표시 상태(capture/_rec_zone/capture_path/size)를
        설치한다(코어 clientwidgets.__init__ 에서 이전)."""
        from .clientside import init_status_defaults
        init_status_defaults(status)

    def client_statusbar_update(self, app, status, msg):
        """status 메시지의 capture* 필드를 위젯에 흡수(코어 update_status 에서 이전)."""
        from .clientside import absorb
        absorb(status, msg)

    def client_statusbar_badges(self, app, status, segs, w, w0=None):
        """시스템 배지 영역(SYNC/AR 직후)에 ` REC ` 배지를 그리고 클릭존을 채운다(코어
        _render_main 에서 이전 — 종전과 같은 위치). w0=들어오는 누적 셀폭, 새 누적 폭 반환(P6)."""
        from .clientside import render_badge
        if w0 is None:
            return None
        return render_badge(status, segs, w0)

    def client_status_tabs(self, app, tree):
        """통합 상태 팝업에 '출력 캡처(REC)' 탭(+[c]/[o] 동작)을 기여(코어
        _open_status_tabs 의 하드코딩 REC 탭에서 이전). (제목,줄,동작) 3-튜플."""
        from .clientside import status_tab
        return [status_tab(app, tree)]

    def attach_client(self, app):
        """클라 앱에 REC 글루(show_capture_info)를 설치한다 — 코어 클릭/ESC nav 가
        getattr 로 호출한다(없으면 no-op). 코어 client.show_capture_info 에서 이전."""
        from .clientside import show_capture_info
        app.show_capture_info = lambda path=None, size=None: \
            show_capture_info(app, path, size)


PLUGIN = _RecPlugin()
