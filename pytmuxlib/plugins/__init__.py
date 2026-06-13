"""선택적 플러그인 로더/레지스트리.

`pytmuxlib/plugins/` 하위의 각 **서브패키지**(디렉토리+__init__.py)를 불러와
`PLUGIN` 객체를 모은다. 플러그인은 명령(이름·설명·카테고리)·자동완성·무인자 표시·
명령 디스패치·메시지 처리(클라)·요청 처리(서버)를 기여한다.

핵심 계약: **디렉토리를 통째로 지우면 그 기능은 조용히 사라진다.** 코어(client/
server)는 플러그인을 직접 import 하지 않고 오직 이 레지스트리를 통해서만 호출하므로,
디렉토리가 없으면 명령 검색·자동완성·디스패치 어디에도 나타나지 않는다(에러 없이).

무게 주의: 이 모듈과 각 플러그인의 `__init__` 은 **textual 을 import 하지 않는다** —
서버 프로세스도 `load()` 로 같은 코드를 읽기 때문이다. 화면 등 무거운 의존은 플러그인
안에서 실제로 필요할 때 지연 import 한다."""
from __future__ import annotations

import importlib
import pkgutil


def _discover():
    """plugins/ 하위 서브패키지를 불러와 `PLUGIN` 객체 목록을 만든다. import 가
    깨진 플러그인은 조용히 건너뛴다(하나가 망가져도 앱 전체를 막지 않게)."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:
            continue
        plugin = getattr(mod, "PLUGIN", None)
        if plugin is not None:
            found.append(plugin)
    return found


class Registry:
    """불러온 플러그인들의 기여를 코어가 쓰기 좋은 형태로 모아 준다. 모든 플러그인
    멤버는 선택적(덕 타이핑) — 없으면 빈 값으로 취급한다."""

    def __init__(self, plugins):
        self.plugins = list(plugins)

    # ---- 명령 메타데이터(코어 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐짐) ----
    @property
    def commands(self):
        out = []
        for p in self.plugins:
            out.extend(getattr(p, "commands", None) or [])
        return out

    @property
    def noarg(self):
        out = set()
        for p in self.plugins:
            out |= set(getattr(p, "noarg", None) or ())
        return out

    @property
    def command_options(self):
        out = {}
        for p in self.plugins:
            out.update(getattr(p, "command_options", None) or {})
        return out

    @property
    def completions(self):
        out = []
        for p in self.plugins:
            out.extend(getattr(p, "completions", None) or [])
            # 명령 이름도 자동완성 후보로(코어가 COMMANDS 로부터 하던 것과 동일).
            out.extend(n for (n, *_rest) in (getattr(p, "commands", None) or []))
        return out

    @property
    def pane_scoped(self):
        """활성 패널에 적용되는 플러그인 명령 이름 집합(코어 PANE_SCOPED_CMDS 에 합쳐짐).
        명령 프롬프트에서 이 명령을 작성 중이면 대상(활성) 패널을 밝게 표시한다."""
        out = set()
        for p in self.plugins:
            out |= set(getattr(p, "pane_scoped", None) or ())
        return out

    @property
    def menu_items(self):
        """우클릭 컨텍스트 메뉴에 합쳐질 플러그인 항목 [(key, 라벨)] (§2.7). key 는
        **그 플러그인의 명령 이름** — 코어 _run_menu_action 이 자기 키가 아니면
        `_run_command(key)` 로 폴백 디스패치하므로 별도 배선이 필요 없다. 디렉토리를
        지우면 메뉴 항목과 명령 디스패치가 함께 사라진다(delete-to-disable)."""
        out = []
        for p in self.plugins:
            out.extend(getattr(p, "menu_items", None) or [])
        return out

    # ---- 클라이언트 훅 ----
    def attach_client(self, app):
        """앱 인스턴스마다 1회 — 플러그인이 인스턴스 글루(예: app.request_nc_list)를
        설치하게 한다."""
        for p in self.plugins:
            fn = getattr(p, "attach_client", None)
            if fn is not None:
                fn(app)

    def handle_command(self, app, c, args):
        """명령 프롬프트의 명령 `c` 를 처리한 플러그인이 있으면 True."""
        for p in self.plugins:
            fn = getattr(p, "handle_command", None)
            if fn is not None and fn(app, c, args):
                return True
        return False

    def handle_message(self, app, msg):
        """서버 메시지(t)를 처리한 플러그인이 있으면 True."""
        for p in self.plugins:
            fn = getattr(p, "handle_message", None)
            if fn is not None and fn(app, msg):
                return True
        return False

    # ---- 서버 훅 ----
    def server_mixins(self):
        """플러그인이 기여하는 **서버측 믹스인 클래스** 목록. `server.Server` 가 이들을
        자신의 동적 베이스로 합성한다(plugins/claude-code 의 ServerClaudeMixin 등).
        플러그인이 `server_mixin()`(지연 import 콜러블)을 노출하면 그 반환 클래스를
        모은다. 디렉토리를 지우면 목록이 비어 해당 서버 로직이 Server 에서 빠진다."""
        out = []
        for p in self.plugins:
            fn = getattr(p, "server_mixin", None)
            if fn is not None:
                cls = fn()
                if cls is not None:
                    out.append(cls)
        return out

    def server_init(self, server):
        """`Server.__init__` 마지막에 1회 — 플러그인이 서버측 런타임 상태를 설치한다
        (pane_init 의 서버 버전). claude-code 가 토큰 DB 연결 상태를 여기서
        설치한다(S5 토큰 모듈화 T2 — 코어 server.__init__ 에서 이전). 플러그인이 없으면
        no-op → 코어 server 에 그 상태가 안 생기고, 읽는 코드(서버 믹스인)도 함께 사라져
        깨지지 않는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "server_init", None)
            if fn is not None:
                fn(server)

    def server_opts_init(self, server, opts):
        """`Server.__init__` 에서 opts.json 로드 직후 1회 — 플러그인이 자기 소유 설정을
        opts dict 에서 읽어 server 속성으로 설치한다(S5 토큰 모듈화 T3). claude-code 가
        usage_gate_* 등을 plugin_opts 네임스페이스에서 읽는다(구 top-level 키 하위호환).
        플러그인이 없으면 no-op → 코어 server 엔 그 설정이 안 생기고, 읽는 코드(플러그인)도
        함께 사라진다(delete-to-disable). 코어는 키의 의미를 모른다."""
        for p in self.plugins:
            fn = getattr(p, "server_opts_init", None)
            if fn is not None:
                fn(server, opts)

    def server_opts_serialize(self, server) -> dict:
        """`_save_opts` 가 opts.json 직렬화 시 1회 — 플러그인 소유 설정을 한 dict 로 모아
        코어가 `plugin_opts` 키 밑에 불투명하게 저장한다(코어는 키 의미 모름). claude-code
        가 usage_gate_* 등을 돌려준다. 플러그인이 없으면 {} → opts.json 에 plugin_opts 가
        비어 그 설정이 통째로 사라진다(delete-to-disable)."""
        out = {}
        for p in self.plugins:
            fn = getattr(p, "server_opts_serialize", None)
            if fn is not None:
                out.update(fn(server) or {})
        return out

    def handle_server_request(self, server, sess, action, msg):
        """서버의 알 수 없는 action 을 플러그인에 넘긴다. 회신 dict(클라로 보낼
        메시지)를 반환한 첫 플러그인의 값을 쓰고, 없으면 None."""
        for p in self.plugins:
            fn = getattr(p, "handle_server_request", None)
            if fn is not None:
                resp = fn(server, sess, action, msg)
                if resp is not None:
                    return resp
        return None

    # ---- 서버 런타임 훅(코어가 믹스인 메서드를 이름으로 직접 부르지 않게) ----
    # 코어(serverio/server)는 Claude 서버 로직(스캔/상태/입력/사용량)에 **이 훅으로만**
    # 닿는다. 플러그인이 없으면 전부 기본값(False/None/no-op)이라 서버가 그대로 동작
    # 한다(delete-to-disable). 플러그인이 있으면 동적 합성된 ServerClaudeMixin 으로
    # 위임한다(server.<method>). Claude Pane/Tab 속성은 model.py 코어에 안전한
    # 기본값이 있어, 플러그인 부재 시 코어가 그 속성을 읽어도 깨지지 않는다.
    def server_scan(self, server, sess, win) -> bool:
        """30Hz flush 루프의 Claude 스캔(상태/사용량/자동개입). 변화 있으면 True."""
        changed = False
        for p in self.plugins:
            fn = getattr(p, "server_scan", None)
            if fn is not None and fn(server, sess, win):
                changed = True
        return changed

    def server_status(self, server, sess, win, msg, full):
        """status 메시지에 Claude 필드를 in-place 로 채운다. 플러그인이 없으면 no-op
        → status 에 Claude 키가 빠지고, 클라(역시 플러그인 부재)는 그 키를 안 본다."""
        for p in self.plugins:
            fn = getattr(p, "server_status", None)
            if fn is not None:
                fn(server, sess, win, msg, full)

    def server_pane_overview(self, server, pane, info):
        """트리/개요 패널 정보(info dict)에 Claude 상태/사용량/토큰을 덧붙인다(in-place)."""
        for p in self.plugins:
            fn = getattr(p, "server_pane_overview", None)
            if fn is not None:
                fn(server, pane, info)

    def server_input(self, server, pane, data):
        """패널 입력 1건의 Claude 부수효과(프롬프트 추적 + 자동개입 타이머 해제)."""
        for p in self.plugins:
            fn = getattr(p, "server_input", None)
            if fn is not None:
                fn(server, pane, data)

    def server_paste(self, server, pane, data):
        """붙여넣기 입력의 프롬프트 추적(Claude 헤더용)."""
        for p in self.plugins:
            fn = getattr(p, "server_paste", None)
            if fn is not None:
                fn(server, pane, data)

    def server_pending(self, server, pane):
        """무장된 자동 액션 카운트다운({kind, eta}) 또는 None(없음)."""
        for p in self.plugins:
            fn = getattr(p, "server_pending", None)
            if fn is not None:
                r = fn(server, pane)
                if r is not None:
                    return r
        return None

    async def server_usage_refresh(self, server):
        """그림자 /usage 자동 갱신 1회(플러그인이 있고 Claude 패널이 있을 때만)."""
        for p in self.plugins:
            fn = getattr(p, "server_usage_refresh", None)
            if fn is not None:
                await fn(server)

    def server_command(self, server, client, sess, action, msg):
        """Claude 명령 액션(set_claude_*/token/pc/refresh_usage 등)을 처리한다. 처리한
        플러그인이 있으면 코어가 따를 **후속 지시 문자열**을 반환한다:
          'handled'   — 플러그인이 다 처리, 코어는 추가 회신 없음(return).
          'send_full' — 코어가 요청 클라에 _send_full.
          'broadcast' — 코어가 _broadcast_session(sess) 후 요청 클라에 _send_full.
        처리한 플러그인이 없으면 None(코어가 handle_server_request 로 넘긴다)."""
        for p in self.plugins:
            fn = getattr(p, "server_command", None)
            if fn is not None:
                r = fn(server, client, sess, action, msg)
                if r is not None:
                    return r
        return None

    def server_pty_output(self, server, pane, data):
        """패널 PTY 출력 1조각(raw 바이트)을 플러그인에 넘긴다(REC 캡처 등). 코어
        serverpty 드레인 루프가 `if self.capture: self._capture_write` 로 직접 가로채던
        걸 대체한다 — 플러그인이 없으면 no-op 라 코어는 바이트를 그냥 흘려보낸다
        (기록 안 함, delete-to-disable). **주의: 30Hz 드레인의 모든 바이트마다 불리는
        핫패스다** — self.plugins 가 보통 0~1개라 순회 비용은 무시할 만하다."""
        for p in self.plugins:
            fn = getattr(p, "server_pty_output", None)
            if fn is not None:
                fn(server, pane, data)

    def server_shutdown(self, server):
        """서버 종료·재시작(re-exec) 경계의 플러그인 정리(REC 캡처 파일 닫기 등). 코어
        serverio.shutdown·serverpersist 재시작 경로가 `_close_all_capfiles` 를 직접
        부르던 걸 대체한다 — 플러그인이 없으면 no-op."""
        for p in self.plugins:
            fn = getattr(p, "server_shutdown", None)
            if fn is not None:
                fn(server)

    # ---- 클라이언트 런타임 훅(코어가 패널 오버레이 플러그인을 이름으로 직접 부르지
    # 않게) ----
    # 코어(client)는 패널 오버레이(시계/달력 등) 그리기·1초 틱·닫기에 **이 훅으로만**
    # 닿는다. 플러그인이 없으면 전부 기본값(no-op/False)이라 코어가 그대로 동작한다
    # (delete-to-disable). 플러그인이 있으면 각 plugin 의 동명 메서드로 위임한다.
    def client_overlay(self, app, cells, W, H, active):
        """패널 전체를 덮는 오버레이(시계/달력 등)를 cells 에 그린다(in-place). 플러그인이
        없으면 no-op → 오버레이 없이 일반 패널 출력만 보인다."""
        for p in self.plugins:
            fn = getattr(p, "client_overlay", None)
            if fn is not None:
                fn(app, cells, W, H, active)

    def client_tick(self, app):
        """1초 틱: 시간 갱신이 필요한 오버레이를 띄운 플러그인이 하나라도 있으면 True
        (코어가 재합성). 없으면 False(idle)."""
        changed = False
        for p in self.plugins:
            fn = getattr(p, "client_tick", None)
            if fn is not None and fn(app):
                changed = True
        return changed

    def client_close_overlay(self, app, pane_id):
        """해당 패널의 플러그인 오버레이를 닫는다(패널 클릭/Shift+ESC). 닫은 플러그인이
        하나라도 있으면 True(코어가 입력 소비), 없으면 False(코어 기본 동작)."""
        closed = False
        for p in self.plugins:
            fn = getattr(p, "client_close_overlay", None)
            if fn is not None and fn(app, pane_id):
                closed = True
        return closed

    def client_overlay_key(self, app, event):
        """활성 패널에 플러그인 오버레이가 떠 있을 때 키 1건을 가로채(소비) 오버레이를
        조작한다(달력 월 이동 등). 소비한 플러그인이 하나라도 있으면 True(코어가 키를
        패널로 보내지 않음), 없으면 False(코어 기본 입력 경로). 플러그인이 없으면
        False(no-op) → 코어 입력 경로는 그대로다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_overlay_key", None)
            if fn is not None and fn(app, event):
                return True
        return False

    def client_key(self, app, event):
        """normal 모드에서 패널로 보낼 **확정(committed) 키 입력** 1건을 플러그인이
        관찰한다 — 서버측 server_input 의 클라이언트 대응. ime-indicator 가 최근 입력
        문자의 스크립트(한글/ASCII)로 한/영 상태를 추정하는 데 쓴다. 플러그인이 없으면
        no-op(루프 본문이 안 돌아 event 를 안 건드림) → 코어 입력 경로는 그대로다."""
        for p in self.plugins:
            fn = getattr(p, "client_key", None)
            if fn is not None:
                fn(app, event)

    def client_render(self, app, cells, W, H):
        """패널 내용(content) 위에 플러그인이 콘텐츠-레이어 장식을 그린다(in-place).
        claude-code 는 이 훅으로 ① 프롬프트 스티키 헤더를 그리고 ② footer 클릭존
        (권한모드/원격제어)을 스캔해 app 의 zone dict 를 채운다. 플러그인이 없으면
        no-op → Claude 헤더·클릭존이 전혀 나타나지 않는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_render", None)
            if fn is not None:
                fn(app, cells, W, H)

    def client_status(self, app, msg):
        """서버 status 메시지의 플러그인-소유 필드를 클라가 흡수한다(in-place 상태 갱신).
        claude-code 는 이 훅으로 claude_rules 동기화, 패널별 Claude 상태
        (pane_claude) 갱신, /usage 자동 팝업 시퀀스를 처리한다. 플러그인이 없으면
        no-op → Claude 상태가 클라에 전혀 반영되지 않는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_status", None)
            if fn is not None:
                fn(app, msg)

    def client_statusbar_update(self, app, status, msg):
        """status 메시지의 Claude 필드(claude_usage/tokens/model/warn/budget·토큰절감
        설정 등)를 하단 상태줄 위젯(status)에 in-place 흡수한다. 플러그인이 없으면
        no-op → 상태줄 Claude 세그먼트가 비활성(claude_active=False) 그대로다."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar_update", None)
            if fn is not None:
                fn(app, status, msg)

    def client_statusbar_init(self, app, status):
        """하단 상태줄 위젯(status) 생성 직후 — 플러그인이 위젯에 Claude 상태 속성
        (claude_active/usage/tokens/model·토큰절감 설정·예산·카운트다운 등)을 안전한
        기본값으로 설치한다. 코어 StatusBar.__init__ 은 이 속성들을 더 이상 두지 않고,
        client_statusbar_update(흡수)·client_statusbar(렌더)가 읽고 쓴다. 플러그인이
        없으면 no-op → 속성이 안 생기지만 흡수/렌더 훅도 함께 사라져 안전하다
        (delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar_init", None)
            if fn is not None:
                fn(app, status)

    def client_statusbar(self, app, status, segs, w, w0=0):
        """하단 상태줄 좌측에 Claude 세그먼트(모델 배지·컨텍스트·토큰Σ·예산경고·카운트
        다운·폭주경고)를 append 하고 클릭존(_usage_zone/_model_zone)을 status 에 채운다.
        플러그인이 없으면 no-op → Claude 세그먼트가 전혀 안 그려지고 클릭존도 None(클릭
        no-op) — delete-to-disable.

        w0 = 들어오는 segs 의 누적 셀폭(P6). 각 플러그인이 자기 append 후의 새 누적
        폭을 반환하면 다음 플러그인·코어가 재순회 없이 이어 쓴다. 반환이 없으면(None)
        직전 w0 를 유지한다. 최종 누적 폭을 돌려준다(플러그인 부재면 w0 그대로)."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar", None)
            if fn is not None:
                r = fn(app, status, segs, w, w0)
                if r is not None:
                    w0 = r
        return w0

    # ---- Pane Claude 상태 소유 훅(S4) ----
    # 코어 model.py 의 Pane 은 Claude 거동 필드를 정의하지 않고, 생성·respawn·직렬화
    # 시 이 훅으로 플러그인에 위임한다. 플러그인이 없으면 전부 no-op/{} 이라 패널엔
    # Claude 필드가 안 생기고, 코어의 소수 읽기 지점은 getattr 기본값으로 동작한다
    # (delete-to-disable). claude-code 의 panestate 모듈이 구현한다.
    def pane_init(self, pane):
        """Pane 생성 시 — 플러그인이 패널에 Claude 거동 필드를 설치한다."""
        for p in self.plugins:
            fn = getattr(p, "pane_init", None)
            if fn is not None:
                fn(pane)

    def pane_closing(self, server, pane):
        """패널이 트리에서 제거되기 직전(servertree._remove_pane_from_tree) — 플러그인이
        패널-종료 부수효과를 처리한다. claude-code 가 닫히는 패널의 확정 토큰을 같은 계정
        생존 패널로 이관한다(#20, S5 토큰 모듈화 T4 에서 코어 servertree 에서 이전). 코어는
        토큰 누계 의미를 모른다. 플러그인이 없으면 no-op(토큰 기능 자체가 없다)."""
        for p in self.plugins:
            fn = getattr(p, "pane_closing", None)
            if fn is not None:
                fn(server, pane)

    def pane_reset(self, pane):
        """respawn(새 셸) 시 — 플러그인이 Claude 필드 부분집합을 리셋한다."""
        for p in self.plugins:
            fn = getattr(p, "pane_reset", None)
            if fn is not None:
                fn(pane)

    def pane_serialize(self, pane) -> dict:
        """재시작 직렬화 — 플러그인들의 Claude 보존 필드를 한 dict 로 합친다."""
        out = {}
        for p in self.plugins:
            fn = getattr(p, "pane_serialize", None)
            if fn is not None:
                out.update(fn(pane) or {})
        return out

    def pane_restore(self, pane, data):
        """재시작 복원 — 직렬화된 plugin_state dict 를 플러그인들이 흡수한다."""
        for p in self.plugins:
            fn = getattr(p, "pane_restore", None)
            if fn is not None:
                fn(pane, data)

    def client_status_tabs(self, app, tree):
        """통합 상태 팝업(_open_status_tabs)에 플러그인이 탭을 기여한다 — (제목, 줄들)
        또는 (제목, 줄들, 동작리스트) 튜플 목록을 반환한다. 동작리스트는 InfoTabsScreen
        에 그 탭 인덱스로 전달된다([(키,라벨,콜백),…]). rec 는 'REC' 탭(+[c]/[o] 동작)을,
        claude-code 는 (구) '토큰 사용량' 탭을 기여한다. 플러그인이 없으면 빈 목록 →
        팝업에 서버 탭만 남는다(delete-to-disable)."""
        tabs = []
        for p in self.plugins:
            fn = getattr(p, "client_status_tabs", None)
            if fn is not None:
                tabs.extend(fn(app, tree) or [])
        return tabs


def load():
    """plugins/ 를 스캔해 Registry 를 만든다(프로세스당 1회 호출이면 충분)."""
    return Registry(_discover())


_REGISTRY = None


def get():
    """프로세스 공용 캐시된 Registry. 코어 model.py 의 Pane 이 생성·respawn·직렬화 시
    pane_init/pane_reset/pane_serialize 훅을 부를 때 매번 재발견하지 않게 한 번만
    로드한다(server/client 는 자체 self.plugins 로 load() 를 쓰지만, Pane 은 그
    인스턴스에 접근할 수 없어 이 싱글톤을 쓴다).

    **load() 가 아니라 _discover() 를 직접 캐시**한다 — 테스트가 `plugins.load` 를
    바꿔치기(클라측 delete-to-disable 시뮬)해도, 서버 Pane 의 Claude 필드 설치는
    import 시점에 고정된 서버 믹스인과 **일관**되게 항상 실제 플러그인을 반영해야
    하기 때문이다(불일치 시 믹스인 스캔이 없는 필드를 읽어 깨진다). 디렉토리를 진짜로
    지우면 _discover() 가 claude-code 를 못 찾아 pane_init 이 no-op 이 된다."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry(_discover())
    return _REGISTRY


def reset():
    """캐시된 싱글톤을 비운다(테스트 전용 — 플러그인 셋 변화 시뮬 후 강제 재발견)."""
    global _REGISTRY
    _REGISTRY = None
