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
    ("mdir", "Mdir III 풍 파일 관리자 — 다열 리스트·태그·복사/이동/삭제·"
             "정렬/필터·뷰어·압축 보기·F10 트리·F4 패널 cd(별칭 m)", "탐색"),
]
NOARG = {"mdir", "m"}


# ---- 선언형 화면 스펙(Tier C · P6)의 어휘 ----
#
# 글자 키는 **스펙이 정한다**(P5). 정본의 Alt-C/Alt-M/… 를 같은 글자로 옮겼다 — 스펙의
# 어휘에 조합키가 없기도 하고, 표에 **없는** 글자는 판을 닫는 규약이라(그래야 닫을 길이
# 있다) 손 하나가 빠지면 "안 먹는다"가 아니라 "닫힌다"로 보이기 때문이다. 그래서 여기에
# 다 적는다 — 화면 안내(`hint`)도 이 표에서 나온다.
_SCREEN_KEYS = {
    "enter": "into",                       # 디렉터리면 들어가고 파일이면 본다
    ".": "up", "t": "tag", "u": "tagall", "h": "hidden",
    "c": "copy", "m": "move", "d": "delete", "r": "rename",
    "k": "mkdir", "v": "view", "p": "cd",
}
_SCREEN_HINT = ("(Enter 열기 · . 상위 · t 태그 · u 전체태그 · c 복사 · m 이동 · "
                "d 삭제 · r 이름 · k 새 디렉터리 · v 보기 · h 숨김 · p 패널 cd · Esc 닫기)")

# 서버가 코드로 돌려주는 실패 사유를 사람 말로. 코드 그대로 보이면 "same"/"into_self"
# 가 화면에 뜨는데, 그건 무엇을 잘못했는지 알려주지 않는다.
_REASONS = {
    "no_src": "원본이 없습니다", "root": "루트는 대상이 아닙니다",
    "into_self": "자기 안으로는 못 옮깁니다", "same": "같은 자리입니다",
    "dst_not_dir": "대상이 디렉터리가 아닙니다", "no_dst": "대상이 비었습니다",
    "dir_overwrite": "디렉터리는 덮어쓰지 않습니다", "exists": "이미 있습니다",
    "bad_name": "이름에 쓸 수 없는 글자가 있습니다", "bad_op": "모르는 조작입니다",
}
_VERBS = {"copy": "복사", "move": "이동", "delete": "삭제",
          "rename": "이름 변경", "mkdir": "새 디렉터리"}


def _offload(fn, *a):
    """순수 파일시스템 작업을 executor 로 — 단일 asyncio 루프를 막지 않는다.
    (`handle_server_request` 와 같은 규약: awaitable 을 돌려주면 서버가 기다린다.)"""
    import asyncio
    return asyncio.get_event_loop().run_in_executor(None, fn, *a)


def _human(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return str(n)


def _when(mtime: int) -> str:
    import time
    try:
        return time.strftime("%Y/%m/%d %H:%M", time.localtime(mtime))
    except (OSError, ValueError):
        return ""


def _result_note(res: dict) -> str:
    """조작 결과 한 줄. **몇 건 됐고 무엇이 왜 안 됐는지**를 한 줄에 담는다 — 일괄
    작업은 절반만 성공하는 것이 정상이라(서버가 개별 실패를 모아 계속한다) 성공/실패
    둘 중 하나로 뭉개면 사용자가 무엇을 다시 해야 하는지 모른다."""
    verb = _VERBS.get(res.get("op"), str(res.get("op") or "?"))
    parts = [f"{verb} {res.get('done') or 0}건"]
    failed = res.get("failed") or []
    if failed:
        shown = ", ".join(f"{n}({_REASONS.get(r, r)})" for n, r in failed[:4])
        parts.append(f"실패 {len(failed)}건 — {shown}")
        if len(failed) > 4:
            parts.append(f"외 {len(failed) - 4}건")
    return " · ".join(parts)


def _cd_command(path: str, nt: bool | None = None) -> str:
    r"""F4(현재 패널 cd 후 닫기)로 보낼 명령 문자열. Windows(cmd.exe)에선
    `cd /d "<경로>"` 로 드라이브까지 전환하고, 그 외엔 `cd <shlex.quote(경로)>`.
    nt 은 **명령을 실행할 셸의 OS**(서버가 mdir_list 로 알려줌). None 이면 클라
    os.name 폴백. 임베드 따옴표·개행 제거로 명령 분리 주입 차단 — ncd 와 동일
    규율의 사본(플러그인끼리 import 하지 않는다).

    **셸 방언 함정(CD-1, 2026-07-17)**: 서버 셸이 cmd 아닌 PowerShell 이면 큰따옴표 안
    `$(...)`·백틱이 보간돼 주입된다(이 문자들은 Win32 파일명에 합법). `nt`(OS 유래)로는
    실제 셸을 모르므로 어느 Windows 셸에서도 활성일 수 있는 메타문자를 전부 제거한다.
    ncd/__init__._cd_command 와 동일 필터(사본)."""
    import os
    import shlex
    if nt is None:
        nt = os.name == "nt"
    if nt:
        safe = path
        for ch in '"$`%!&|<>^()':
            safe = safe.replace(ch, "")
        safe = safe.replace("\r", "").replace("\n", "")
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
        # 파일시스템 I/O 는 executor 로 넘긴다(coroutine 반환 → serverio 가 await).
        # 대형 트리 복사/삭제·대형 압축 목록(전체 압축해제)·느린 네트워크 fs 가
        # 단일 asyncio 루프를 막아 모든 패널/클라/페더레이션을 얼리던 것 해소.
        # 빌더는 순수 fs 라 서버 상태를 만지지 않아 스레드 오프로드가 안전하다.
        import asyncio

        def _offload(fn, *a):
            return asyncio.get_event_loop().run_in_executor(None, fn, *a)

        if action == "request_mdir_list":
            # base 해석은 세션 상태(활성 패널 cwd)를 읽으므로 루프 스레드에서 먼저
            # 끝내고(레이스 방지), 순수 fs 나열만 executor 로 넘긴다.
            from .server import mdir_list_fs, mdir_list_resolve_base
            base = mdir_list_resolve_base(server, sess, msg.get("path"))
            return _offload(mdir_list_fs, base)
        if action == "request_mdir_op":
            from .server import mdir_op_msg
            return _offload(mdir_op_msg, server, sess, msg)
        if action == "request_mdir_view":
            from .server import mdir_view_msg
            return _offload(mdir_view_msg, server, sess, msg.get("path"))
        if action == "request_mdir_arc":
            from .server import mdir_arc_msg
            return _offload(mdir_arc_msg, server, sess, msg.get("path"))
        return None

    # ---- 선언형 화면 스펙(Tier C · P6 — **되돌릴 수 없는 조작이 있는 첫 시민**) ----
    def plugin_screen(self, server, sess, req):
        """네이티브 클라용 파일 관리자 화면.

        # 정본과 모양이 다르다(그리고 그래도 된다)

        정본은 검정 바탕 **다열** 리스트에 상단 Path/Volume 줄과 하단 집계·정보줄을 두는
        Mdir III 재현이다. 여기서는 한 줄에 한 항목인 **표**(이름·크기·시각)다 — 설계 §6
        이 그은 선 그대로: 스펙은 **내용과 선택**을 정하고 표현은 각 클라 관례를 따른다.
        열 수·정렬 토글·압축 보기·트리(F10)처럼 **위젯 고유의 것**은 안 담았다(담기
        시작하면 스펙이 화면마다 늘어난다 — 설계 §10 의 무한 확장 위험).

        # 되돌릴 수 없는 것은 이 클라의 화면이 묻는다

        삭제는 `confirm`(기본 '아니오'), 목적지·이름은 `prompt` 다(P5 의 판단). 물음
        문구는 스펙이 실어 보낸다 — 무엇을 지우는지 모른 채 누르는 화면이 되면 기본이
        '아니오'인 것만으로는 부족하다.

        # 태그와 커서는 **그 클라의 것**이다

        `req["state"]` 는 연결에 매달린 보관함이라(설계 P5) 두 사람이 같은 디렉터리를
        열어도 서로의 태그를 옮기지 않고, 연결이 끊기면 함께 사라진다.
        """
        state = req.get("state")
        if state is None:
            state = {}
        mine = state.setdefault("mdir", {})
        do = req.get("do")
        if do == "open":
            if req.get("name") not in ("mdir", "m"):
                return None
            from .server import mdir_list_resolve_base
            # base 해석은 **세션 상태**(활성 패널 cwd)를 읽으므로 루프 스레드에서 끝낸다
            # (executor 에서 sess 를 만지면 레이스 — `handle_server_request` 와 같은 규율).
            mine.clear()
            mine["path"] = mdir_list_resolve_base(server, sess, None)
            mine["tags"] = []
            return _offload(self._spec, mine, 0, "")
        if req.get("id") != "mdir":
            return None
        row = int(req.get("row") or 0)
        picked = str(req.get("input") or "")
        if do == "up":
            return _offload(self._up, mine)
        if do == "into":
            return _offload(self._into, mine, picked, row)
        if do == "tag":
            return _offload(self._tag, mine, picked, row)
        if do == "tagall":
            return _offload(self._tagall, mine, row)
        if do == "hidden":
            mine["hidden"] = not mine.get("hidden")
            return _offload(self._spec, mine, row, "")
        if do == "view":
            return _offload(self._view, mine, picked, row)
        if do == "cd":
            # 정본 F4 와 **같은 결과**: 그 자리에서 패널에 cd 를 친다. 셸 방언은 이
            # 서버의 OS 가 정한다(클라의 것을 쓰면 Windows 클라가 macOS 패널에
            # `cd /d` 를 흘린다 — 정본이 이미 밟은 함정).
            path = str(mine.get("path") or "")
            if path:
                self._send_to_pane(server, sess, _cd_command(path))
            return {"t": "plugin_screen_close", "id": "mdir"}
        if do in ("copy", "move", "delete", "rename", "mkdir"):
            return self._begin(mine, do, picked, row)
        if do == "apply":
            return _offload(self._apply, mine, picked, row)
        if do == "close":
            return {"t": "plugin_screen_close", "id": "mdir"}
        return None

    def _send_to_pane(self, server, sess, text):
        """활성 패널에 글자를 넣는다(정본 F4 와 같은 결과)."""
        win = sess.active_window if sess else None
        pane = win.active_pane if win else None
        if pane is not None:
            pane.write(text.encode("utf-8", "replace"))

    # ---- 화면 만들기(전부 순수 fs — executor 에서 돈다) ----
    def _spec(self, mine, sel, note):
        """지금 디렉터리의 표 스펙."""
        import os
        from .server import list_entries, _drive_roots
        path = mine.get("path") or os.path.abspath(os.sep)
        entries, err, over = list_entries(path)
        items = [e for e in entries if mine.get("hidden") or not e["h"]]
        # 디렉터리 먼저, 그 안에서 이름순 — 정본의 기본 정렬과 같다.
        items.sort(key=lambda e: (not e["d"], e["n"].lower()))
        tags = set(mine.get("tags") or [])
        rows = []
        parent = os.path.dirname(path.rstrip("/\\"))
        if parent and parent != path:
            # `..` 는 **자리가 아니라 뜻**으로 나른다(부모 경로 그대로 — ncd 동형).
            rows.append({"key": parent, "label": "   ..", "cols": ["<상위>"]})
        operable = []
        for e in items:
            full = os.path.join(path, e["n"])
            operable.append(full)
            rows.append({
                "key": full,
                # 태그는 **줄 안에** 보인다. 색·굵기는 클라마다 다르지만 글자는 같다.
                "label": ("✓ " if full in tags else "  ")
                         + e["n"] + ("/" if e["d"] else ""),
                "cols": ["<DIR>" if e["d"] else _human(e["s"]), _when(e["m"])],
            })
        for d in _drive_roots():
            rows.append({"key": d, "label": f"  [-{d[:1]}-]", "cols": ["<드라이브>"]})
        # 조작 대상은 이 목록 안의 것뿐이다 — 다음 액션이 `input` 으로 받은 경로를
        # 여기 대고 검증한다(클라가 옛 목록의 줄을 되돌려줘도 엉뚱한 것을 안 지운다).
        mine["items"] = operable
        # 태그도 **이 디렉터리 것만** 남긴다. 안 그러면 화면에 안 보이는 것이 지워진다.
        mine["tags"] = [t for t in (mine.get("tags") or []) if t in set(operable)]
        if not note:
            if err:
                note = f"읽기 실패: {err}"
            elif over:
                note = "항목이 너무 많아 일부만 보입니다"
            elif not operable:
                note = "빈 디렉터리입니다"
        return {
            "t": "plugin_screen", "id": "mdir", "kind": "table",
            "title": f"파일 관리자 — {path}",
            "hint": _SCREEN_HINT,
            "rows": rows, "text": "",
            "selected": max(0, min(int(sel), max(0, len(rows) - 1))),
            "keys": dict(_SCREEN_KEYS),
            "note": note,
        }

    def _up(self, mine):
        import os
        path = str(mine.get("path") or "")
        parent = os.path.dirname(path.rstrip("/\\"))
        if parent and parent != path:
            mine["path"] = parent
        return self._spec(mine, 0, "")

    def _into(self, mine, picked, row):
        """디렉터리면 들어가고 파일이면 본다.

        정본의 Enter 는 파일을 **실행**하지만(패널에 그 이름을 친다) 여기서는 보여만
        준다 — 목록에서 Enter 한 번에 무엇이 실행되는 화면은, 그 무엇을 스펙이 못
        보여주는 곳에서는 위험하다. 실행이 필요하면 `p`(패널 cd) 뒤에 치면 된다."""
        import os
        if not picked:
            return self._spec(mine, row, "")
        if os.path.isdir(picked):
            mine["path"] = os.path.abspath(picked)
            return self._spec(mine, 0, "")
        return self._view(mine, picked, row)

    def _tag(self, mine, picked, row):
        tags = list(mine.get("tags") or [])
        if picked in (mine.get("items") or []):
            if picked in tags:
                tags.remove(picked)
            else:
                tags.append(picked)
            mine["tags"] = tags
        # 정본과 같이 커서가 **한 줄 내려간다** — 연달아 찍는 것이 이 키의 쓰임이다.
        return self._spec(mine, row + 1, "")

    def _tagall(self, mine, row):
        items = list(mine.get("items") or [])
        mine["tags"] = [] if mine.get("tags") else items
        return self._spec(mine, row, "")

    def _view(self, mine, picked, row):
        import os
        from .server import mdir_view_msg
        # 이 빌더는 server/sess 를 안 읽는다(순수 fs) — 그래서 executor 로 나온다.
        m = mdir_view_msg(None, None, picked)
        if m.get("err"):
            return self._spec(mine, row, f"못 읽습니다: {m['err']}")
        if m.get("binary"):
            return self._spec(mine, row, "이진 파일이라 안 보입니다")
        return {
            "t": "plugin_screen", "id": "mdir", "kind": "text",
            "title": os.path.basename(picked) or picked,
            "hint": "(↑↓ 스크롤 · Esc 닫기)", "rows": [],
            "text": m.get("text") or "", "selected": 0, "keys": {},
            "note": "앞부분만 보입니다(뒤는 잘렸습니다)" if m.get("truncated") else "",
        }

    # ---- 되돌릴 수 없는 것: 묻고(begin) → 받고(apply) ----
    def _targets(self, mine, picked):
        """조작 대상 — 태그가 있으면 태그 전체, 없으면 커서 항목(정본 `_targets` 동형).
        `..`·드라이브는 대상이 아니다: `items` 에만 담지 않았으므로 여기서 걸러진다."""
        items = list(mine.get("items") or [])
        tags = [t for t in (mine.get("tags") or []) if t in items]
        if tags:
            return tags
        return [picked] if picked in items else []

    def _ask(self, kind, title, note=""):
        return {"t": "plugin_screen", "id": "mdir", "kind": kind,
                "title": title, "hint": "", "rows": [], "text": "",
                "note": note, "selected": 0, "keys": {"enter": "apply"}}

    def _begin(self, mine, op, picked, row):
        """물음을 세운다. 여기서는 **아무것도 안 한다** — 답이 `apply` 로 돌아온다."""
        import os
        if op == "mkdir":
            mine["ask"] = {"op": "mkdir"}
            return self._ask("prompt", "새 디렉터리 이름")
        targets = self._targets(mine, picked)
        if not targets:
            return _offload(self._spec, mine, row, "대상이 없습니다")
        names = [os.path.basename(t.rstrip("/\\")) or t for t in targets]
        if op in ("copy", "move"):
            mine["ask"] = {"op": op, "src": targets}
            return self._ask(
                "prompt", f"{_VERBS[op]} — 대상 디렉터리 ({len(targets)}개)",
                "지금 자리: " + str(mine.get("path") or ""))
        if op == "rename":
            if len(targets) != 1:
                return _offload(self._spec, mine, row, "이름 변경은 하나씩만 됩니다")
            mine["ask"] = {"op": "rename", "src": targets}
            return self._ask("prompt", f"새 이름 — {names[0]}")
        # 삭제 — 되돌릴 수 없다. **무엇이 사라지는지**를 물음에 함께 싣는다.
        mine["ask"] = {"op": "delete", "src": targets}
        shown = ", ".join(names[:6]) + (f" 외 {len(names) - 6}개"
                                        if len(names) > 6 else "")
        return self._ask("confirm",
                         f"{len(targets)}개를 지웁니다 — 되돌릴 수 없습니다", shown)

    def _apply(self, mine, answer, row):
        """물음의 답이 왔다. 취소는 여기까지 오지 않는다(클라가 아무것도 안 보낸다)."""
        from .server import mdir_op_msg
        ask = mine.pop("ask", None)
        if not ask:
            return self._spec(mine, row, "")
        op = ask["op"]
        answer = (answer or "").strip()
        if op == "delete":
            msg = {"op": "delete", "src": ask["src"]}
        elif op in ("copy", "move"):
            dst = ask.get("dst") or answer
            if not dst:
                return self._spec(mine, row, "대상을 안 적었습니다")
            msg = {"op": op, "src": ask["src"], "dst": dst,
                   "overwrite": ask.get("overwrite") or "ask"}
        elif op in ("rename", "mkdir"):
            if not answer:
                return self._spec(mine, row, "")
            msg = {"op": op, "src": ask.get("src") or [], "dst": answer,
                   "base": mine.get("path")}
        else:
            return self._spec(mine, row, "")
        # 이 빌더도 server/sess 를 안 읽는다(순수 fs).
        res = mdir_op_msg(None, None, msg)
        if res.get("conflicts"):
            # **2단계 프로토콜**: 아직 아무것도 안 했다. 겹치는 것을 덮어쓸지 되묻는다.
            # ⚠ 정본은 [모두 덮어쓰기 / 건너뛰기 / 취소] 셋을 물어보는데 여기는 둘이다 —
            #    '아니오'는 이 클라에서 **아무 일도 안 일어남**이라야 하고(P5 규약),
            #    '건너뛰기'를 거기에 얹으면 그 약속이 깨진다. 건너뛰기가 필요하면
            #    터미널 클라에서 한다(리포트에 적어 둔 빚).
            mine["ask"] = dict(ask, dst=msg.get("dst"), overwrite="all")
            names = ", ".join(res["conflicts"][:6])
            return self._ask(
                "confirm",
                f"같은 이름이 {len(res['conflicts'])}개 있습니다 — 덮어쓸까요?", names)
        mine["tags"] = []
        return self._spec(mine, row, _result_note(res))


PLUGIN = _MdirPlugin()
