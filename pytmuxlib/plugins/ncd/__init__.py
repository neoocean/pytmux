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

from pytmuxlib import i18n

# 화면 스펙(Tier C)이 **소켓 너머로 실어 보내는** 글. 여기 없으면 게이트가 못 본다 —
# 픽스처는 카탈로그에서 뽑히므로, 스펙에 직접 적은 한국어는 영어 표에도 안 들어가고
# 영어 사용자에게 그대로 한국어로 뜬다(2026-08-02o 실측).
i18n.register({
    "ko": {
        "ncd.title": "디렉터리 — {path}",
        # ⛔ 「Enter 들어가기」는 **평면 목록 시절의 글**이었다(pytmux-417). 지금 이
        # 화면은 트리이고 `Enter` 는 정본과 같이 **그 자리로 cd** 다 — 화면이 스스로
        # 틀린 기대를 만들고 있었다. 정본 화면의 힌트(`ncd/screen.py:_HINT`)와 같은
        # 뜻으로 맞춘다(항해 키 넷은 pytmux-417 ① 에서 실제로 먹게 됐다).
        "ncd.hint": ("↑↓·PgUp/PgDn·Home/End 이동 · →펼치기 ←접기 · "
                     "Enter cd · c 여기로 cd · Esc 닫기"),
        "ncd.empty": "하위 디렉터리가 없습니다",
        "ncd.too_many": "줄이 너무 많아 일부만 보입니다 — 접으면 줄어듭니다",
    },
    "en": {
        "ncd.title": "Directory — {path}",
        "ncd.hint": ("↑↓·PgUp/PgDn·Home/End move · → expand ← collapse · "
                     "Enter cd · c cd here · Esc close"),
        "ncd.empty": "No subdirectories",
        "ncd.too_many": "Too many rows — collapse to see fewer",
    },
})

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("ncd", "디렉토리 트리(Norton Change Directory 풍) — 루트→cwd 펼침·↑↓ 탐색·"
            "타이핑 찾기·Enter cd·⇧Enter/^O 새 패널(별칭 nc)", "탐색"),
]
NOARG = {"ncd", "nc"}


def _cd_command(path: str, nt: bool | None = None) -> str:
    r"""ncd 의 Enter(현재 패널 cd)로 보낼 명령 문자열. Windows(cmd.exe)에선
    `cd /d "<경로>"` 로 **드라이브까지 전환**하고, 그 외엔 `cd <shlex.quote(경로)>`.
    nt 은 **명령을 실행할 셸의 OS**(서버가 nc_list 로 알려줌). None 이면 클라 os.name
    으로 폴백(구버전 서버·테스트) — 단, 페더레이션에서 클라≠셸 OS 면 오방언이 될 수
    있어 서버발 nt 를 우선한다."""
    import os
    import shlex
    if nt is None:
        nt = os.name == "nt"
    if nt:
        # POSIX 분기의 shlex.quote 와 동일한 방어 규율(M4). 임베드 따옴표·제어문자를
        # 제거해 따옴표 탈출 후 명령 분리(`" & cmd`)를 원천 차단한다.
        #
        # **셸 방언 함정(CD-1, 보안검수 2026-07-17)**: 이 명령은 서버가 띄운 셸이
        # 소비하는데 그 셸은 `PYTMUX_SHELL or COMSPEC or cmd.exe`(serverpty)라 cmd 가
        # 아닐 수 있다. cmd 의 큰따옴표 안에선 `& | ^ $ ()`가 리터럴이지만
        # **PowerShell/pwsh 은 `$(...)`·백틱을 큰따옴표 안에서도 보간**한다 — 그리고
        # 이 문자들은 **Win32 파일명에 합법**이라 따옴표 필터를 그냥 통과한다. 즉 M4 는
        # "심층 방어"가 아니라 load-bearing 인데 겨눈 셸이 틀렸었다. `nt`(OS 유래)로는
        # 실제 셸을 알 수 없으므로, **어느 Windows 셸에서도 활성일 수 있는 메타문자를
        # 전부 제거**한다: `" $ \` (백틱) % ! & | < > ^ ( )`. 이 문자들은 정상 디렉토리
        # 경로엔 안 나타나므로(`( )`는 드물게 나타나지만 cd 대상으로는 희귀) 제거해도
        # 실사용 불변, 대신 cmd·PowerShell·bash.exe **어디서 실행돼도** 주입이 불가능하다.
        safe = path
        for ch in '"$`%!&|<>^()':
            safe = safe.replace(ch, "")
        safe = safe.replace("\r", "").replace("\n", "")
        return f'cd /d "{safe}"\n'
    return f"cd {shlex.quote(path)}\n"


class _NcdPlugin:
    name = "ncd"
    description = "디렉토리 트리 이동 모달(Norton Change Directory 풍)"
    category = "탐색"
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

        # 트리에 안 열린 디렉토리까지 재귀 검색(speed search 가 보이는 트리에서 못
        # 찾을 때 호출). 응답 t==nc_found → handle_message 가 화면에 적용(펼침·선택).
        def request_nc_find(query, root=None):
            app.send_cmd("request_nc_find", query=query, root=root)
        app.request_nc_find = request_nc_find

    def handle_command(self, app, c, args):
        if c in ("ncd", "nc"):
            app.request_nc_list()
            return True
        return False

    def handle_message(self, app, msg):
        t = msg.get("t")
        if t == "nc_list":
            self._on_nc_list(app, msg)
            return True
        if t == "nc_found":
            from .screen import NcdScreen
            scr = app.screen
            if isinstance(scr, NcdScreen):
                scr.apply_found(msg.get("query", ""), msg.get("target"),
                                msg.get("chain") or [])
            return True
        return False

    def _on_nc_list(self, app, msg):
        """nc_list 수신. path 가 None 이면 초기 트리(루트→cwd chain) → ncd 화면을 연다
        (요청한 경우만). path 가 있으면 펼치기 응답 → 떠 있는 화면의 노드에 자식을 채운다."""
        from .screen import NcdScreen
        if msg.get("path") is None:
            if not getattr(app, "_want_nc", False):
                return            # 요청 안 했는데 온 응답은 무시(방어)
            app._want_nc = False
            # 서버(패널 셸의 소유자)가 알려준 셸 방언. 부재(구버전 서버)면 None →
            # _cd_command 가 클라 os.name 로 폴백(하위호환).
            app._nc_nt = msg.get("nt")
            # 일회성 결과 콜백(app._nc_open_cb): 다른 소비자(mdir 의 F10 트리 등)가
            # request_nc_list 전에 심어 두면 기본 동작(_done: 패널 cd/분할) 대신 그
            # 콜백이 ("cd"|"newpane", path) 를 받는다. ncd 는 심은 쪽을 모른다
            # (역방향 결합 없음) — 콜백 부재 시 종전과 동일.
            cb = getattr(app, "_nc_open_cb", None)
            app._nc_open_cb = None
            app.push_screen(
                NcdScreen(msg.get("root"), chain=msg.get("chain"),
                          cwd=msg.get("cwd"), dirs=msg.get("dirs"),
                          # 경로 해석도 셸 OS 기준 — cd 방언(nt)과 같은 출처를 쓴다.
                          nt=msg.get("nt")),
                cb if cb is not None else (lambda res: self._done(app, res)))
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
            app.send_input(_cd_command(path, nt=getattr(app, "_nc_nt", None)).encode())
        elif action == "newpane":
            app.send_cmd("split", orient="lr", path=path)

    # ---- 서버 측 ----
    def handle_server_request(self, server, sess, action, msg):
        # ncd(Norton Change Directory 풍 디렉토리 트리). 부작용 없음(읽기 전용).
        #
        # **파일시스템 조회는 executor 로 넘긴다**(coroutine 반환 → serverio 가 await).
        # 종전엔 dict 를 곧바로 반환해 단일 asyncio 루프에서 그대로 돌았다 — 재귀 검색
        # (`nc_find`)은 최대 20000 디렉토리 BFS 라 실측 1.44s/회 이고, 스피드서치는
        # **키스트로크마다** 요청을 보내 'documents' 타이핑 한 번에 누적 ~11초 서버
        # 전면 정지였다. 게다가 request_nc_list/find 는 `_REMOTE_RELAY_ACTIONS` 라
        # 하류 사용자의 타이핑이 **상류 서버**의 전 패널·전 클라·전 링크를 얼렸다
        # (신뢰경계를 넘는 DoS). 보안검수 2026-07-17 LOOP-1. mdir 이 이미 쓰던
        # 탈출구(serverio.py 가 awaitable 을 await)를 ncd 도 채택한다.
        import asyncio

        def _offload(fn, *a):
            return asyncio.get_event_loop().run_in_executor(None, fn, *a)

        if action == "request_nc_list":
            # path 없으면 루트→cwd 사슬, 있으면 해당 노드의 직계 하위(지연 펼치기).
            # cwd 추정은 **세션 상태를 읽으므로 루프에서** 먼저 끝내고(레이스 방지),
            # 순수 fs 나열만 넘긴다 — mdir 과 동일한 분할.
            from .server import nc_list_fs, nc_list_resolve_cwd
            path = msg.get("path")
            cwd = None if path else nc_list_resolve_cwd(server, sess)
            return _offload(nc_list_fs, cwd, path)
        if action == "request_nc_find":
            # 트리에 안 열린 디렉토리까지 재귀 검색 → 최적 매치 + 조상 사슬.
            # server/sess 를 안 읽는 순수 fs 라 통째로 넘긴다.
            from .server import nc_find_msg
            return _offload(nc_find_msg, server, sess, msg.get("query", ""),
                            msg.get("root"))
        return None

    # ---- 선언형 화면 스펙(Tier C · 설계 P5 — **상태 있는 첫 시민**) ----
    def plugin_screen(self, server, sess, req):
        """네이티브 클라용 디렉터리 화면.

        # 정본과 **모양이 다르다**(그리고 그래도 된다)

        정본은 루트→cwd 를 펼친 **트리**를 보이고 `Enter` 로 그 자리에 cd 한다. 여기서는
        한 디렉터리씩 보이는 **평면 목록**이다(`..` + 하위 디렉터리). 설계 §6 이 그은 선이
        이것이다 — 스펙은 **내용과 선택**을 정하고 표현은 각 클라 관례를 따른다. 결과(어느
        디렉터리로 cd 하나)는 같고, 위젯 고유의 펼침 동작까지 스펙에 담으려 하면 스펙이
        화면마다 늘어난다.

        # 상태는 **그 클라의 것**이다

        지금 보고 있는 디렉터리는 사람마다 다르다. `req["state"]` 는 그 클라의 연결에
        매달린 보관함이라(설계 P5) 두 클라가 같은 화면을 열어도 서로의 자리를 안 옮기고,
        연결이 끊기면 함께 사라진다.
        """
        import asyncio
        import os

        def _offload(fn, *a):
            return asyncio.get_event_loop().run_in_executor(None, fn, *a)

        state = req.get("state") or {}
        mine = state.setdefault("ncd", {})
        do = req.get("do")
        if do == "open":
            if req.get("name") not in ("ncd", "nc"):
                return None
            from .server import nc_list_resolve_cwd
            # cwd 추정은 **세션 상태를 읽으므로 루프에서**(fs 나열만 오프로드 — LOOP-1).
            mine["path"] = nc_list_resolve_cwd(server, sess) or os.path.abspath(os.sep)
            # 셸이 지금 서 있는 자리 — 목록에서 **강조**할 줄을 고르는 데 쓴다
            # (정본은 노랑 + 표식으로 가리킨다. pytmux-11 A).
            mine["cwd"] = mine["path"]
            return _offload(self._open_tree, mine)
        if req.get("id") != "ncd":
            return None
        if do == "into":
            # 트리에서 `Enter` 는 **그 자리로 cd** 다(정본과 같다) — 평면 목록 시절의
            # "들어가기"가 아니다. 이름은 계약이라 그대로 두고 뜻만 정본에 맞춘다.
            # ★ 네이티브 클라에서는 이 경로가 mdir 갱신에 필요하므로 응답에 실어 보낸다.
            target = str(req.get("input") or "")
            if target:
                mine["cwd"] = target
                cmd = ("cd /d " if os.name == "nt" else "cd ") + _quote(target)
                self._send_to_pane(server, sess, cmd + "\r")
            return {"t": "plugin_screen_close", "id": "ncd", "input": target}
        if do == "expand":
            return _offload(self._expand, mine, str(req.get("input") or ""))
        if do == "collapse":
            return _offload(self._collapse, mine, str(req.get("input") or ""))
        if do == "cd":
            # 정본과 **같은 결과**: 그 자리에서 패널에 cd 를 친다. 셸 방언은 이 서버의
            # OS 가 정한다(클라의 것을 쓰면 Windows 클라가 macOS 패널에 `cd /d` 를 흘린다).
            path = str(req.get("input") or mine.get("path") or "")
            if path:
                cmd = ("cd /d " if os.name == "nt" else "cd ") + _quote(path)
                self._send_to_pane(server, sess, cmd + "\r")
            return {"t": "plugin_screen_close", "id": "ncd"}
        if do == "close":
            return {"t": "plugin_screen_close", "id": "ncd"}
        return None

    def _send_to_pane(self, server, sess, text):
        """활성 패널에 글자를 넣는다(정본 ncd 의 Enter 와 같은 결과).

        ☠ **`pane.write` 가 아니다 — `Pane` 에 그런 메서드는 없다**(pytmux-173).
        종전 이 한 줄은 불릴 때마다 `AttributeError` 로 죽었고, 서버는 데몬이라
        stderr 이 `/dev/null` 이라 그 트레이스백은 `<state_base>.error.log` 에만
        남았다. `plugin_screen` 이 예외로 죽으면 `plugin_screen_close` 도 안 나가서
        **화면도 안 닫히고 cd 도 안 들어가는** 「아무 일도 안 남」이 된다 — 사용자
        제보가 본 것이 정확히 그것이다(라이브 로그 실측 2026-08-23 17:45:08).

        글자를 넣는 길은 `pane.pty.write` 하나다. 가드는 정본
        (`plugins/claude-resume/__init__.py` §`_resume`)과 같은 모양이다 —
        `pane.pty` 는 `None` 일 수 있다(`model.py` 의 `self.pty = None` ·
        `reinit` 직후).

        ⛔ **가짜를 `pane` 에 달아 재지 마라** — 그것이 이 결함을 살려 둔 길이다
        (`tests/test_ncd_tree.py` §`_Pane` · 전수 오라클은
        `tests/test_pane_write_typo.py`).
        """
        win = sess.active_window if sess else None
        pane = win.active_pane if win else None
        try:
            if pane is not None and pane.pty is not None:
                pane.pty.write(text.encode("utf-8", "replace"))
        except OSError:
            pass

    # ---- 트리(pytmux-11 B) --------------------------------------------------
    #
    # 종전 이 화면은 **한 디렉터리씩 보이는 평면 목록**이었고, 그것은 설계 §6("스펙은
    # 내용과 선택을 정하고 표현은 각 클라 관례를 따른다")에 기대 의도한 선이었다.
    # 제보가 그 선을 옮겼다: *"정본은 트리 구조를 직접 내비게이팅하는데 GUI 는 조회와
    # 이동만 된다 — 완전히 같게."*
    #
    # 그래서 스펙이 **깊이와 펼침 상태**를 나른다. 트리의 모양(무엇이 펼쳐져 있나)은
    # 보는 사람마다 다르므로 `req["state"]` — 그 클라의 연결에 매달린 보관함 — 에 산다.

    @staticmethod
    def _norm(path):
        # ⚠ **`os.path` 가 아니라 `server._pathmod`** — 이 서버가 곧 그 경로들의 OS 라
        # 실제 Windows 에서는 둘이 같지만, 시험은 다른 OS 에서 Windows 판정을 검증하려고
        # `server._pathmod` 만 갈아 끼운다(같은 관례를 `_list_dirs`·`_drive_roots` 도 쓴다).
        from .server import _pathmod as pm
        return pm.normcase(pm.normpath(path)) if path else ""

    def _kids(self, mine, path):
        """`path` 의 하위 디렉터리(한 번 읽으면 그 클라 보관함에 남는다).

        지연 로드가 규칙이다 — 뿌리에서 전부 훑으면 네트워크 드라이브 하나에 화면이
        멎는다(정본이 같은 이유로 한 단계씩 읽는다).

        # 상한은 **층마다** 있다 (실측)

        형제가 수만인 디렉터리가 실제로 있다(임시 디렉터리 하나에서 89142). 전체 줄
        수로만 자르면 앞쪽 형제가 상한을 다 먹어 **정작 내가 서 있는 자리가 잘린다** —
        그건 자른 것이 아니라 화면을 못 쓰게 만든 것이다. 그래서 층마다 자르고,
        **사슬 위의 줄은 반드시 남긴다**(잘라도 길은 보인다).
        """
        from .server import _list_dirs
        cache = mine.setdefault("kids", {})
        key = self._norm(path)
        if key not in cache:
            kids = _list_dirs(path)
            if len(kids) > self._MAX_KIDS:
                keep = set(mine.get("open") or ())
                head = kids[:self._MAX_KIDS]
                # 사슬(펼쳐 둔 것) 위의 형제는 잘려도 되살린다 — 길이 끊기면 안 된다.
                on_path = [k for k in kids[self._MAX_KIDS:] if self._norm(k) in keep]
                kids = head + on_path
                mine["cut"] = True
            cache[key] = kids
        return cache[key]

    @staticmethod
    def _parent(path):
        """`path` 의 부모(뿌리면 자기 자신).

        ⚠ **구분자를 깎고 dirname 하지 말 것**: `C:\` 를 깎으면 `C:` 가 되고, 그것은
        Windows 에서 **드라이브의 현재 디렉터리**라는 전혀 다른 곳이다. 그렇게 하면
        트리의 뿌리가 엉뚱한 자리를 가리켜 사슬이 통째로 끊긴다(실측: 8단 사슬이
        한 단만 펼쳐졌다)."""
        from .server import _pathmod as pm
        parent = pm.dirname(path) or path
        return parent

    def _chain(self, path):
        """뿌리 → `path` 의 조상 사슬(그 경로 자신 포함)."""
        from .server import _pathmod as pm
        out, cur = [], pm.abspath(path)
        while True:
            out.append(cur)
            parent = self._parent(cur)
            if self._norm(parent) == self._norm(cur):
                break
            cur = parent
        out.reverse()
        return out

    def _open_tree(self, mine):
        """열 때의 트리 — 뿌리부터 셸이 서 있는 자리까지 **펼쳐 둔다**(정본과 같다).

        그래야 창이 뜨자마자 지금 어디에 있는지가 보이고, 위아래 형제로 바로 옮길 수 있다.

        # Windows — 뿌리가 여럿이다 (pytmux-160·pytmux-238)

        드라이브 나열 로직(`server._drive_roots`)은 있었는데 이 화면은 그것을 안 탔다 —
        `_chain` 이 셸이 서 있는 드라이브 하나만 뿌리로 삼아 `C:\` 만 보이고 `D:\` 로
        옮겨갈 항목이 트리에 없었다. 여기서는 합성 최상위(빈 문자열 키 · 화면에 줄로
        안 뜬다)를 두고 그 자식으로 드라이브 목록을 실어 드라이브들을 형제로 만든다
        (서버측 `_build_chain` 과 같은 모양이지만, 사슬 위 디렉터리의 형제 상한
        (`_kids`/`_MAX_KIDS`)을 그대로 타야 하므로 여기서 따로 짠다 — `_build_chain` 은
        그 상한을 모른다).
        """
        from .server import _drive_roots
        cwd = mine.get("cwd") or mine.get("path")
        chain = self._chain(cwd)
        drives = _drive_roots()
        if drives:
            canon = next((d for d in drives if self._norm(d) == self._norm(chain[0])), None)
            # 지금 서 있는 드라이브가 목록에서 빠졌으면(subst·매핑 경합) 보강한다 —
            # 안 그러면 그 드라이브로 가는 형제 줄이 아예 없어 사슬이 끊긴다.
            tops = list(drives) if canon is not None else drives + [chain[0]]
            if canon is not None:
                chain = [canon] + chain[1:]      # 사슬 머리 = 드라이브 정본 표기
            mine["root"] = ""
            mine["drives"] = [self._norm(d) for d in tops]
            mine.setdefault("kids", {})[""] = sorted(tops, key=self._norm)
        else:
            mine["root"] = chain[0]
            mine["drives"] = []
        # 사슬 위의 노드는 전부 펼친다(마지막 = cwd 도 — 그 안이 보여야 한다).
        mine["open"] = [self._norm(p) for p in chain]
        for p in chain:
            self._kids(mine, p)
        return self._tree_spec(mine)

    #: 한 프레임에 실을 줄 상한. 트리는 펼친 만큼 커지고, 형제가 수만인 디렉터리가
    #: 실제로 있다(실측: 임시 디렉터리 하나에서 89142 줄). 그걸 그대로 소켓에 실으면
    #: 화면이 멎는다 — mdir 이 같은 이유로 `_MAX_ENTRIES` 를 둔다.
    #:
    #: ⚠ **말없이 자르지 않는다**: 잘렸으면 `note` 로 알리고 무엇을 하면 되는지(접기)를
    #:    함께 말한다. 조용한 절단은 "다 보인다"로 읽힌다.
    _MAX_ROWS = 4000
    #: 한 디렉터리에서 보일 하위 수 상한(위 `_kids` 문서 참조).
    _MAX_KIDS = 500

    def _rows(self, mine, path, depth, out):
        """펼쳐진 것만 따라 내려가며 줄을 만든다(정본 `_flatten` 과 같은 차례)."""
        from .server import _pathmod as pm
        if len(out) >= self._MAX_ROWS:
            return
        opened = set(mine.get("open") or [])
        cache = mine.get("kids") or {}
        cwd = self._norm(mine.get("cwd"))
        me = self._norm(path)
        drives = set(mine.get("drives") or ())
        if me in drives and me not in cache:
            # ⛔ **드라이브는 펼치기 전엔 안 읽는다** — 연결 안 된 네트워크·광학 드라이브
            #   하나가 있으면 잎 판정 하나 때문에 화면이 멎는다(정본도 펼칠 때 읽는다).
            #   그래도 **열 수 있는 줄**로는 보여야 한다 — 안 읽었다고 잎으로 그리면
            #   거짓말이다(pytmux-239).
            kids = []
            expandable = True
        else:
            kids = self._kids(mine, path)
            expandable = bool(kids)
        out.append({
            "key": path,
            # 이름은 **자료 그대로**다 — 들여쓰기를 글자로 섞으면 타이핑 찾기·복사가
            # 그 공백을 물고 간다(그래서 깊이를 따로 싣는다).
            "label": pm.basename(path.rstrip("/\\")) or path,
            "cols": [],
            "depth": depth,
            # 접힘과 **잎**은 다르다 — 빈 디렉터리에 눌러도 안 열리는 화살표를 안 붙인다.
            "expand": ("open" if me in opened else "shut") if expandable else "",
            # 뜻이 없는 줄은 이름을 안 단다 — "dir" 은 mdir 시그니처 붉은색과 묶여
            # 있어(rowtag.py TAGS) ncd 자기 팔레트에 없는 색을 강제로 입힌다.
            "tag": "cwd" if me == cwd else "",
        })
        if me in opened:
            if not kids:
                kids = self._kids(mine, path)
            for kid in kids:
                self._rows(mine, kid, depth + 1, out)

    def _tree_spec(self, mine):
        """지금 트리의 스펙(순수 fs — executor 에서 돈다)."""
        from .server import _pathmod as pm
        root = mine.get("root")
        if root is None:
            root = pm.abspath(pm.sep)
        rows = []
        if mine.get("drives"):
            # 합성 최상위(빈 문자열)는 화면에 줄로 안 뜬다 — 그 자식(드라이브들)이
            # 곧바로 깊이 0 의 형제로 보인다.
            for kid in self._kids(mine, root):
                self._rows(mine, kid, 0, rows)
        else:
            self._rows(mine, root, 0, rows)
        over = len(rows) >= self._MAX_ROWS or bool(mine.get("cut"))
        # 커서는 **셸이 서 있는 줄**에 둔다 — 열자마자 지금 자리가 골라져 있어야
        # `Enter` 한 번이 뜻을 갖는다.
        cwd = self._norm(mine.get("cwd"))
        sel = next((i for i, r in enumerate(rows) if self._norm(r["key"]) == cwd), 0)
        title, title_spec = i18n.phrase("ncd.title", path=mine.get("cwd") or root)
        return {
            "t": "plugin_screen", "id": "ncd", "kind": "list",
            "title": title,
            "hint": i18n.t("ncd.hint"),
            "rows": rows,
            "selected": sel,
            # 키 → 액션. 글자뿐 아니라 **이름 있는 키**도 실을 수 있다(pytmux-11 B) —
            # 트리는 `←→` 로 접고 펴는 것이 손버릇이고 그 둘은 글자가 아니다.
            "keys": {"enter": "into", "c": "cd",
                     "right": "expand", "left": "collapse"},
            "note": (i18n.t("ncd.too_many") if over
                     else "" if rows else i18n.t("ncd.empty")),
            "i18n": {"title": title_spec},
        }

    def _select(self, spec, path):
        """스펙의 커서를 `path` 줄에 둔다(그 줄이 없으면 그대로).

        ⛔ **이 함수가 있어야 하는 이유**(pytmux-417 ②): `_tree_spec` 은 상태 함수가
        아니라 **매번 다시 계산**하고, 커서 기본값이 언제나 「셸이 서 있는 줄(cwd)」이다.
        그리고 클라는 새 스펙이 올 때마다 그 값을 그대로 적용한다(「커서의 주인은
        스펙」). 그래서 커서를 옮긴 응답은 **스스로 그 사실을 실어야** 한다 — 안 실으면
        `D:` 드라이브를 펴도 화면과 커서가 셸이 선 드라이브의 cwd 로 되돌아가고, 사용자에겐
        「다른 드라이브 트리가 안 열린다」로 보인다.
        `_collapse` 는 이것을 손으로 하고 있었고 `_expand` 는 안 하고 있었다."""
        key = self._norm(path)
        for i, r in enumerate(spec["rows"]):
            if self._norm(r["key"]) == key:
                spec["selected"] = i
                break
        return spec

    def _expand(self, mine, path):
        """그 줄을 편다. 이미 펴져 있으면 그대로(첫 자식으로 내려가는 것은 `↓` 의 일).

        편 줄에 **커서를 남긴다** — 편 다음에 커서가 딴 데 있으면 그 조작은 아무것도 안
        한 것처럼 보인다(위 `_select`)."""
        if path:
            opened = set(mine.get("open") or [])
            if self._kids(mine, path):
                opened.add(self._norm(path))
                mine["open"] = sorted(opened)
        spec = self._tree_spec(mine)
        return self._select(spec, path) if path else spec

    def _collapse(self, mine, path):
        """접는다. **이미 접혀 있으면 부모로 올라간다** — 정본 `←` 의 두 뜻이다.

        한 키에 두 뜻을 두는 것이 손버릇이다(접힌 잎에서 `←` 를 눌렀는데 아무 일도 안
        나면 사람은 그 키가 죽은 줄 안다)."""
        import os
        if not path:
            return self._tree_spec(mine)
        opened = set(mine.get("open") or [])
        me = self._norm(path)
        if me in opened:
            opened.discard(me)
            mine["open"] = sorted(opened)
            # 접은 줄에 커서를 남긴다 — 접고 나서 커서가 cwd 로 튀면 `←→` 를 번갈아
            # 누를 때 자리가 왔다 갔다 한다(`_expand` 와 같은 결).
            return self._select(self._tree_spec(mine), path)
        return self._select(self._tree_spec(mine), self._parent(path))


def _quote(path: str) -> str:
    """셸에 넣을 경로 — 공백·따옴표가 있는 경로가 그대로 깨지지 않게."""
    if not path or any(c in path for c in ' "\'$`'):
        return '"' + path.replace('"', '\\"') + '"'
    return path


PLUGIN = _NcdPlugin()
