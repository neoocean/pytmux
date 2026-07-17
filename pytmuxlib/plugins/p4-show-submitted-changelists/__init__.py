"""p4-show-submitted-changelists 플러그인 — 현재 퍼포스 서버의 submitted CL 목록.

명령 `p4changes [N]`(별칭 `submitted`·`p4-changes`)로 연다. 현재 활성 패널 cwd 의 퍼포스
설정(P4PORT/P4CLIENT/.p4config 등 **현재 설정된 그대로**)으로 `p4 changes -s submitted` 를
실행해, 최신 submitted changelists 목록을 **풀스크린 화면**(클라 ModalScreen)에 띄운다.
↑↓ 로 스크롤·하이라이트하고, Enter 로 하이라이트된 CL 의 `p4 describe` 상세를 팝업으로
본다. 팝업은 Esc 로 닫고, 목록 화면도 Esc(=탭 닫기)로 닫아 플러그인을 종료한다.

왜 '탭'이 서버 윈도우가 아니라 클라 풀스크린 화면인가: claude-token-usage-view 와 같은
이유 — p4 호출 결과는 클라가 그리고, 서버 탭은 항상 fresh 셸이라 데이터를 실을 수 없다.
풀스크린 ModalScreen 이 곧 사용자가 보는 '탭'이고, 닫으면(Esc) 기능이 사라진다.

이 디렉토리를 통째로 지우면 `p4changes` 명령은 검색·자동완성·디스패치 어디에도 안 잡히고
(서버의 request_p4_changes/_describe 회신도 사라짐), 코어는 무에러로 그대로 동작한다 —
코어가 이 플러그인을 직접 참조하지 않고 plugins 레지스트리를 통해서만 호출하기 때문이다
(delete-to-disable).

무게: 이 __init__ 은 textual 을 최상단에서 import 하지 않는다(서버 프로세스도
plugins.load() 로 이걸 읽는다). 화면(screen)·서버 로직(server)은 실제로 쓸 때 지연
import 한다. i18n 은 ipc 만 끌어와 가벼우므로 코어 플러그인처럼 최상단 등록한다."""
from __future__ import annotations

from pytmuxlib import i18n

# 명령 메타데이터 — 코어 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐짐.
COMMANDS = [
    ("p4changes", "현재 퍼포스 서버의 submitted changelists 목록을 띄운다 "
                  "(p4changes [N], 기본 50; ↑↓ 스크롤 · Enter 상세 · Esc 닫기; "
                  "별칭 submitted)", "Perforce"),
]
NOARG = {"p4changes", "submitted", "p4-changes"}

_ALIASES = ("p4changes", "submitted", "p4-changes")
_DEFAULT_COUNT = 50
_MAX_COUNT = 500


i18n.register({
    "ko": {
        "p4cl.title": "submitted changelists",
        "p4cl.title_port": "submitted changelists — {port}",
        "p4cl.nav": "↑↓ 이동 · Enter 상세 · Esc 닫기",
        "p4cl.empty": "(제출된 체인지리스트가 없습니다)",
        "p4cl.detail_nav": "↑↓ 스크롤 · PgUp/PgDn · Home/End · Esc 닫기",
        "p4cl.loading": "불러오는 중…",
        "p4cl.error": "오류: {err}",
        "p4cl.no_detail": "(내용 없음)",
    },
    "en": {
        "p4cl.title": "submitted changelists",
        "p4cl.title_port": "submitted changelists — {port}",
        "p4cl.nav": "↑↓ move · Enter details · Esc close",
        "p4cl.empty": "(no submitted changelists)",
        "p4cl.detail_nav": "↑↓ scroll · PgUp/PgDn · Home/End · Esc close",
        "p4cl.loading": "Loading…",
        "p4cl.error": "Error: {err}",
        "p4cl.no_detail": "(empty)",
    },
})


class _P4ChangesPlugin:
    name = "p4-show-submitted-changelists"
    description = "Perforce submitted CL 목록 + p4 describe 상세"
    category = "Perforce"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = {}

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """인스턴스 글루를 설치한다(ncd 의 request_nc_list 패턴):
          * app.request_p4_changes(count) — submitted 목록 요청(응답은 t==p4_changes).
          * app.request_p4_describe(change) — CL 상세 요청 + 팝업 즉시 푸시(응답 t==p4_describe
            가 오면 팝업을 채운다). _want_p4_changes 가드로 요청 안 한 응답엔 화면을 안 연다."""
        def request_p4_changes(count=_DEFAULT_COUNT):
            app._want_p4_changes = True
            app.send_cmd("request_p4_changes", count=count)
        app.request_p4_changes = request_p4_changes

        def request_p4_describe(change):
            from .screen import DescribeScreen
            app.send_cmd("request_p4_describe", change=str(change))
            app.push_screen(DescribeScreen(str(change)))
        app.request_p4_describe = request_p4_describe

    def handle_command(self, app, c, args):
        if c not in _ALIASES:
            return False
        count = _DEFAULT_COUNT
        if args and str(args[0]).isdigit():
            count = max(1, min(_MAX_COUNT, int(args[0])))
        app.request_p4_changes(count)
        return True

    def handle_message(self, app, msg):
        t = msg.get("t")
        if t == "p4_changes":
            self._on_changes(app, msg)
            return True
        if t == "p4_describe":
            self._on_describe(app, msg)
            return True
        return False

    def _on_changes(self, app, msg):
        """submitted 목록 수신 → 요청한 경우에만 풀스크린 화면을 연다(방어 가드)."""
        if not getattr(app, "_want_p4_changes", False):
            return
        app._want_p4_changes = False
        from .screen import ChangesScreen
        app.push_screen(ChangesScreen(msg.get("rows"),
                                      info=msg.get("info"),
                                      err=msg.get("err")))

    def _on_describe(self, app, msg):
        """CL 상세 수신 → 떠 있는 DescribeScreen(같은 change)을 채운다."""
        from .screen import DescribeScreen
        scr = app.screen
        if isinstance(scr, DescribeScreen) and scr._change == str(msg.get("change")):
            scr.fill(msg.get("text"), msg.get("err"))

    # ---- 서버 측 ----
    def handle_server_request(self, server, sess, action, msg):
        # p4 서브프로세스는 executor 로 넘긴다(coroutine 반환 → serverio 가 await).
        # 종전엔 dict 를 곧바로 반환해 단일 asyncio 루프에서 그대로 돌았다 — `describe`
        # 는 타임아웃이 20초라 느린/불통 P4PORT 하나에 **서버 전체가 최대 20초 정지**
        # 했고(list 는 8초×2), 공격자 없이 평범한 네트워크 지연만으로 발동했다
        # (보안검수 2026-07-17 LOOP-2). cwd 추정은 세션 상태를 읽으므로 루프에서
        # 먼저 끝내고 나머지만 넘긴다 — mdir·ncd 와 동일한 분할.
        import asyncio

        def _offload(fn, *a):
            return asyncio.get_event_loop().run_in_executor(None, fn, *a)

        if action in ("request_p4_changes", "request_p4_describe"):
            from .server import _cwd
            cwd = _cwd(server, sess)          # 세션 읽기 → 루프에서
            if action == "request_p4_changes":
                from .server import list_changes_msg
                count = msg.get("count", _DEFAULT_COUNT)
                try:
                    count = max(1, min(_MAX_COUNT, int(count)))
                except (TypeError, ValueError):
                    count = _DEFAULT_COUNT
                return _offload(list_changes_msg, server, sess, count, cwd)
            from .server import describe_msg
            return _offload(describe_msg, server, sess,
                            str(msg.get("change", "")), cwd)
        return None


PLUGIN = _P4ChangesPlugin()
