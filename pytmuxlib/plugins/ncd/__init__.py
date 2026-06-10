"""ncd 플러그인 — Norton Change Directory 풍 디렉토리 트리 모달(코드네임 nc).

기능 전체가 이 디렉토리 안에 있다:
  - `__init__.py` : 코어와의 계약(명령 메타·디스패치·메시지/요청 핸들러). 가벼움.
  - `screen.py`   : 모달 화면·트리 위젯(textual). 클라에서 실제로 열 때 지연 import.
  - `server.py`   : 디렉토리 나열·조상 사슬 등 서버 측 로직(textual 무관). 지연 import.

이 디렉토리를 지우면 `ncd`/`nc` 명령은 명령 검색·자동완성·디스패치 어디에도 잡히지
않고(서버의 request_nc_list 회신도 사라짐), 코어는 아무 변경 없이 그대로 동작한다 —
코어가 ncd 를 직접 참조하지 않고 plugins 레지스트리를 통해서만 호출하기 때문이다.

무게: 이 모듈은 textual/os/shlex 를 모듈 최상단에서 import 하지 않는다(서버 프로세스도
plugins.load() 로 이걸 읽는다). 필요한 곳에서 지연 import 한다."""
from __future__ import annotations

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("ncd", "디렉토리 트리(Norton Change Directory 풍) — 루트→cwd 펼침·↑↓ 탐색·"
            "타이핑 찾기·Enter cd·⇧Enter/^O 새 패널(별칭 nc)", "탐색"),
]
NOARG = {"ncd", "nc"}


def _cd_command(path: str, nt: bool | None = None) -> str:
    r"""ncd 의 Enter(현재 패널 cd)로 보낼 명령 문자열. Windows(cmd.exe)에선
    `cd /d "<경로>"` 로 **드라이브까지 전환**하고, 그 외엔 `cd <shlex.quote(경로)>`.
    nt 인자는 테스트용 오버라이드(기본=os.name)."""
    import os
    import shlex
    if nt is None:
        nt = os.name == "nt"
    if nt:
        return f'cd /d "{path}"\n'
    return f"cd {shlex.quote(path)}\n"


class _NcdPlugin:
    name = "ncd"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 추가 옵션 템플릿 없음(명령 이름은 레지스트리가 자동 추가)
    command_options = {}

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """_NcdView 가 self.app.request_nc_list(path) 를 부르므로 인스턴스에 설치한다.
        path=None → 활성 패널 cwd 루트(화면 열기), path=<dir> → 그 노드 자식(지연 펼치기).
        응답은 t==nc_list 로 와 handle_message 가 처리한다."""
        def request_nc_list(path=None):
            app._want_nc = True
            app.send_cmd("request_nc_list", path=path)
        app.request_nc_list = request_nc_list

    def handle_command(self, app, c, args):
        if c in ("ncd", "nc"):
            app.request_nc_list()
            return True
        return False

    def handle_message(self, app, msg):
        if msg.get("t") != "nc_list":
            return False
        self._on_nc_list(app, msg)
        return True

    def _on_nc_list(self, app, msg):
        """nc_list 수신. path 가 None 이면 초기 트리(루트→cwd chain) → ncd 화면을 연다
        (요청한 경우만). path 가 있으면 펼치기 응답 → 떠 있는 화면의 노드에 자식을 채운다."""
        from .screen import NcdScreen
        if msg.get("path") is None:
            if not getattr(app, "_want_nc", False):
                return            # 요청 안 했는데 온 응답은 무시(방어)
            app._want_nc = False
            app.push_screen(
                NcdScreen(msg.get("root"), chain=msg.get("chain"),
                          cwd=msg.get("cwd"), dirs=msg.get("dirs")),
                lambda res: self._done(app, res))
        else:
            scr = app.screen
            if isinstance(scr, NcdScreen):
                scr.fill_children(msg.get("path"), msg.get("dirs") or [])

    def _done(self, app, res):
        """ncd 화면 결과 처리. Enter→현재 패널 cd, Shift+Enter/Ctrl+O→새 패널 분할."""
        if not res:
            return            # Esc/취소
        action, path = res
        if action == "cd":
            app.send_input(_cd_command(path).encode())
        elif action == "newpane":
            app.send_cmd("split", orient="lr", path=path)

    # ---- 서버 측 ----
    def handle_server_request(self, server, sess, action, msg):
        if action != "request_nc_list":
            return None
        # ncd(Norton Change Directory 풍 디렉토리 트리): path 없으면 루트→cwd 사슬,
        # 있으면 해당 노드의 직계 하위를 회신(지연 펼치기). 부작용 없음.
        from .server import nc_list_msg
        return nc_list_msg(server, sess, msg.get("path"))


PLUGIN = _NcdPlugin()
