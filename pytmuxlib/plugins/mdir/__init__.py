"""mdir 플러그인 — Mdir III(엠디르) 풍 파일 관리자 모달.

1990년대 한국에서 NCD 보다 널리 쓰인 도스 파일 관리자 Mdir III 의 재현: 검정
바탕 1-패널 다열 파일 리스트(2-pane 노턴류와 다름), 확장자별 색, 리스트 끝의
드라이브 항목, 상단 Path/Volume 줄, 하단 집계줄 + 커서파일·시계 정보줄. 탐색·
태그·파일 조작(복사/이동/삭제 등)을 팝업 안에서 수행한다(`:mdir`, 별칭 `m`).

기능 전체가 이 디렉토리 안에 있다(ncd 와 같은 3분할):
  - `__init__.py` : 코어와의 계약(명령 메타·디스패치·메시지/요청 핸들러). 가벼움.
  - `screen.py`   : 모달 화면·리스트 위젯(textual). 클라에서 실제로 열 때 지연 import.
  - `server.py`   : 파일시스템 나열/조작(textual 무관). 지연 import.

이 디렉토리를 지우면 `mdir`/`m` 명령은 명령 검색·자동완성·디스패치 어디에도 잡히지
않고(서버의 request_mdir_* 회신·페더레이션 릴레이도 사라짐), 코어는 아무 변경 없이
그대로 동작한다(delete-to-disable).

무게: 이 모듈은 textual/os/shlex 를 모듈 최상단에서 import 하지 않는다(서버
프로세스도 plugins.load() 로 이걸 읽는다). 필요한 곳에서 지연 import 한다."""
from __future__ import annotations

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("mdir", "Mdir III 풍 파일 관리자 — 다열 리스트·탐색·빨리찾기·"
             "F4 패널 cd·⇧Enter 새 패널(별칭 m)", "탐색"),
]
NOARG = {"mdir", "m"}


def _cd_command(path: str, nt: bool | None = None) -> str:
    r"""F4(현재 패널 cd 후 닫기)로 보낼 명령 문자열. Windows(cmd.exe)에선
    `cd /d "<경로>"` 로 드라이브까지 전환하고, 그 외엔 `cd <shlex.quote(경로)>`.
    nt 은 **명령을 실행할 셸의 OS**(서버가 mdir_list 로 알려줌). None 이면 클라
    os.name 폴백. 임베드 따옴표·개행 제거로 명령 분리 주입 차단 — ncd 와 동일
    규율의 사본(플러그인끼리 import 하지 않는다)."""
    import os
    import shlex
    if nt is None:
        nt = os.name == "nt"
    if nt:
        safe = path.replace('"', "").replace("\r", "").replace("\n", "")
        return f'cd /d "{safe}"\n'
    return f"cd {shlex.quote(path)}\n"


class _MdirPlugin:
    name = "mdir"
    description = "Mdir III 풍 파일 관리자 모달(다열 리스트·파일 조작)"
    category = "탐색"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = {}
    # 원격 보기(federation) 중 업스트림으로 릴레이할 액션 — 원격 패널이면 원격
    # 머신의 파일시스템을 보고 조작해야 한다(코어 화이트리스트와 합집합).
    relay_actions = {"request_mdir_list", "request_mdir_op",
                     "request_mdir_view", "request_mdir_arc"}

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """스크린이 self.app.request_mdir_list(path) 로 탐색하므로 인스턴스에 설치.
        path=None → 활성 패널 cwd(팝업 열기·초기 진입), path=<dir> → 그 디렉토리
        나열(진입/상위/드라이브 전환). 응답은 t==mdir_list 로 와 handle_message 가
        처리한다(화면이 떠 있으면 갱신, 아니면 열기)."""
        def request_mdir_list(path=None):
            if path is None:
                app._want_mdir = True
            app.send_cmd("request_mdir_list", path=path)
        app.request_mdir_list = request_mdir_list

        # 파일 조작(copy/move/delete/rename/mkdir). 응답 t==mdir_result — 충돌이면
        # 화면이 [덮어쓰기/건너뛰기/취소]를 물어 overwrite=all|skip 으로 재요청한다.
        def request_mdir_op(**kw):
            app.send_cmd("request_mdir_op", **kw)
        app.request_mdir_op = request_mdir_op

        # 내장 뷰어(파일 앞부분) / 압축파일 내부 목록.
        def request_mdir_view(path):
            app.send_cmd("request_mdir_view", path=path)
        app.request_mdir_view = request_mdir_view

        def request_mdir_arc(path):
            app.send_cmd("request_mdir_arc", path=path)
        app.request_mdir_arc = request_mdir_arc

    def handle_command(self, app, c, args):
        if c in ("mdir", "m"):
            app.request_mdir_list()
            return True
        return False

    def handle_message(self, app, msg):
        t = msg.get("t")
        if t == "mdir_list":
            self._on_list(app, msg)
            return True
        if t == "mdir_result":
            # 조작 결과는 떠 있는 mdir 화면으로(확인 팝업이 위에 겹쳐 있어도 —
            # app.screen 이 아니라 스택 전체에서 찾는다).
            scr = self._find_screen(app)
            if scr is not None:
                scr.apply_result(msg)
            return True
        if t == "mdir_view":
            scr = self._find_screen(app)
            if scr is not None:
                from .screen import MdirViewer
                app.push_screen(MdirViewer(msg))
            return True
        if t == "mdir_arc":
            scr = self._find_screen(app)
            if scr is not None:
                scr.apply_arc(msg)
            return True
        return False

    @staticmethod
    def _find_screen(app):
        from .screen import MdirScreen
        for s in reversed(app.screen_stack):
            if isinstance(s, MdirScreen):
                return s
        return None

    def _on_list(self, app, msg):
        """mdir_list 수신. MdirScreen 이 떠 있으면 그 화면의 목록 갱신(탐색),
        없으면 요청한 경우에 한해 화면을 연다(초기 진입)."""
        from .screen import MdirScreen
        # 셸 방언(cd /d vs cd)은 서버발 nt 가 권위 — 매 응답마다 갱신(ncd 동형).
        app._mdir_nt = msg.get("nt")
        scr = app.screen
        if isinstance(scr, MdirScreen):
            scr.apply_list(msg)
            return
        if not getattr(app, "_want_mdir", False):
            return                # 요청 안 했는데 온 응답은 무시(방어)
        app._want_mdir = False
        app.push_screen(MdirScreen(msg), lambda res: self._done(app, res))

    def _done(self, app, res):
        """mdir 화면 결과 처리. F4→현재 패널 cd, ⇧Enter/^O→새 패널 분할."""
        if not res:
            return                # Esc/취소
        action, path = res
        if action == "cd":
            app.send_input(_cd_command(path, nt=getattr(app, "_mdir_nt", None))
                           .encode())
        elif action == "newpane":
            app.send_cmd("split", orient="lr", path=path)

    # ---- 서버 측 ----
    def handle_server_request(self, server, sess, action, msg):
        if action == "request_mdir_list":
            from .server import mdir_list_msg
            return mdir_list_msg(server, sess, msg.get("path"))
        if action == "request_mdir_op":
            from .server import mdir_op_msg
            return mdir_op_msg(server, sess, msg)
        if action == "request_mdir_view":
            from .server import mdir_view_msg
            return mdir_view_msg(server, sess, msg.get("path"))
        if action == "request_mdir_arc":
            from .server import mdir_arc_msg
            return mdir_arc_msg(server, sess, msg.get("path"))
        return None


PLUGIN = _MdirPlugin()
