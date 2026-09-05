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

from pytmuxlib import i18n

# 줄의 뜻 판정 — 정본 화면과 **같은 함수**다(UI 무의존이라 서버도 부른다).
from .rowtag import row_tag

# 늘어놓는 규칙 — 정본 화면과 **같은 함수**다(UI 무의존이라 서버도 부른다).
from .listing import arrange, counts, next_sort, parse_masks

# 화면 스펙(Tier C)이 **소켓 너머로 실어 보내는** 글. 여기 없으면 게이트가 못 본다 —
# 픽스처는 카탈로그에서 뽑히므로, 스펙에 직접 적은 한국어는 영어 표에도 안 들어가고
# 영어 사용자에게 그대로 한국어로 뜬다(ncd·p4changes 와 같은 규율).
#
# ⚠ **동사·사유를 인자로 넘기지 않는다.** `복사 {n}건` 을 `{verb} {n}건` + verb="복사"
#    로 지으면 영어 클라가 자기 포맷에 **한국어 조각**을 끼워 `복사 3 copied` 를 만든다
#    (`i18n.phrase` 주석의 그 함정). 그래서 동사는 포맷 **안**에 있고 판이 조작마다 있다.
i18n.register({
    "ko": {
        "mdir.title": "파일 관리자 — {path}",
        # 마스크가 걸려 있으면 **어디에** 걸렸는지 제목이 말한다(정본은 Path 줄의
        # `[*.txt]`). 판이 둘인 이유: 한 판에 `[{mask}]` 를 두면 마스크가 없을 때
        # `[]` 한 쌍이 늘 붙는다.
        "mdir.title_mask": "파일 관리자 — {path}  [{mask}]",
        "mdir.hint": ("(Enter 열기 · . 상위 · t 태그 · u 전체태그 · c 복사 · m 이동 · "
                      "d 삭제 · r 이름 · k 새 디렉터리 · v 보기 · h 숨김 · p 패널 cd · "
                      "F10 트리 · Esc 닫기)"),
        # 줄의 **칸**으로 나가는 표식(이름은 자료라 번역 대상이 아니다).
        "mdir.parent": "<상위>",
        "mdir.drive": "<드라이브>",
        "mdir.read_fail": "읽기 실패: {err}",
        "mdir.too_many": "항목이 너무 많아 일부만 보입니다",
        "mdir.mask_ask": "파일 마스크 (예: *.txt *.md · 빈 값이면 해제)",
        "mdir.empty": "빈 디렉터리입니다",
        # 뷰어
        "mdir.view_hint": "Esc 닫기",
        "mdir.view_scroll_hint": "↑↓ 스크롤",
        "mdir.cant_read": "못 읽습니다: {err}",
        "mdir.binary": "이진 파일이라 안 보입니다",
        "mdir.truncated": "앞부분만 보입니다(뒤는 잘렸습니다)",
        # 물음(되돌릴 수 없는 것 앞)
        "mdir.ask_mkdir": "새 디렉터리 이름",
        "mdir.no_targets": "대상이 없습니다",
        "mdir.ask_copy": "복사 — 대상 디렉터리 ({n}개)",
        "mdir.ask_move": "이동 — 대상 디렉터리 ({n}개)",
        "mdir.here": "지금 자리: {path}",
        "mdir.rename_one": "이름 변경은 하나씩만 됩니다",
        "mdir.ask_rename": "새 이름 — {name}",
        "mdir.ask_delete": "{n}개를 지웁니다 — 되돌릴 수 없습니다",
        "mdir.and_more": "{names} 외 {n}개",
        "mdir.no_input": "대상을 안 적었습니다",
        "mdir.ask_overwrite": "같은 이름이 {n}개 있습니다 — 덮어쓸까요?",
        # 결과 한 줄 — 조작마다 판이 둘(성공만 · 실패 섞임).
        "mdir.res_copy": "복사 {n}건",
        "mdir.res_copy_fail": "복사 {n}건 · 실패 {f}건",
        "mdir.res_move": "이동 {n}건",
        "mdir.res_move_fail": "이동 {n}건 · 실패 {f}건",
        "mdir.res_delete": "삭제 {n}건",
        "mdir.res_delete_fail": "삭제 {n}건 · 실패 {f}건",
        "mdir.res_rename": "이름 변경 {n}건",
        "mdir.res_rename_fail": "이름 변경 {n}건 · 실패 {f}건",
        "mdir.res_mkdir": "새 디렉터리 {n}건",
        "mdir.res_mkdir_fail": "새 디렉터리 {n}건 · 실패 {f}건",
        # 실패 사유 — 서버가 코드로 돌려주는 것을 사람 말로. 코드 그대로 보이면
        # "same"/"into_self" 가 화면에 뜨는데, 그건 무엇을 잘못했는지 알려주지 않는다.
        "mdir.why_no_src": "원본이 없습니다",
        "mdir.why_root": "루트는 대상이 아닙니다",
        "mdir.why_into_self": "자기 안으로는 못 옮깁니다",
        "mdir.why_same": "같은 자리입니다",
        "mdir.why_dst_not_dir": "대상이 디렉터리가 아닙니다",
        "mdir.why_no_dst": "대상이 비었습니다",
        "mdir.why_dir_overwrite": "디렉터리는 덮어쓰지 않습니다",
        "mdir.why_exists": "이미 있습니다",
        "mdir.why_bad_name": "이름에 쓸 수 없는 글자가 있습니다",
        "mdir.why_bad_op": "모르는 조작입니다",
    },
    "en": {
        "mdir.title": "File manager — {path}",
        "mdir.title_mask": "File manager — {path}  [{mask}]",
        "mdir.hint": ("(Enter open · . up · t tag · u tag all · c copy · m move · "
                      "d delete · r rename · k new directory · v view · h hidden · "
                      "p cd panel · F10 tree · Esc close)"),
        "mdir.parent": "<UP>",
        "mdir.drive": "<DRIVE>",
        "mdir.read_fail": "Read failed: {err}",
        "mdir.too_many": "Too many entries — showing only some",
        "mdir.mask_ask": "File mask (e.g. *.txt *.md · empty clears)",
        "mdir.empty": "Empty directory",
        "mdir.view_hint": "Esc close",
        "mdir.view_scroll_hint": "↑↓ scroll",
        "mdir.cant_read": "Cannot read: {err}",
        "mdir.binary": "Binary file — not shown",
        "mdir.truncated": "Only the beginning is shown (the rest is cut)",
        "mdir.ask_mkdir": "New directory name",
        "mdir.no_targets": "Nothing to work on",
        "mdir.ask_copy": "Copy — destination directory ({n})",
        "mdir.ask_move": "Move — destination directory ({n})",
        "mdir.here": "Now at: {path}",
        "mdir.rename_one": "Rename takes one at a time",
        "mdir.ask_rename": "New name — {name}",
        "mdir.ask_delete": "Deleting {n} — this cannot be undone",
        "mdir.and_more": "{names} and {n} more",
        "mdir.no_input": "No destination was given",
        "mdir.ask_overwrite": "{n} names already exist — overwrite?",
        "mdir.res_copy": "Copied {n}",
        "mdir.res_copy_fail": "Copied {n} · {f} failed",
        "mdir.res_move": "Moved {n}",
        "mdir.res_move_fail": "Moved {n} · {f} failed",
        "mdir.res_delete": "Deleted {n}",
        "mdir.res_delete_fail": "Deleted {n} · {f} failed",
        "mdir.res_rename": "Renamed {n}",
        "mdir.res_rename_fail": "Renamed {n} · {f} failed",
        "mdir.res_mkdir": "Created {n}",
        "mdir.res_mkdir_fail": "Created {n} · {f} failed",
        "mdir.why_no_src": "The source is gone",
        "mdir.why_root": "The root is not a target",
        "mdir.why_into_self": "Cannot move into itself",
        "mdir.why_same": "Same place",
        "mdir.why_dst_not_dir": "The destination is not a directory",
        "mdir.why_no_dst": "The destination is empty",
        "mdir.why_dir_overwrite": "Directories are not overwritten",
        "mdir.why_exists": "Already there",
        "mdir.why_bad_name": "The name has characters that cannot be used",
        "mdir.why_bad_op": "Unknown operation",
    },
})

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
    # ★ 정렬·마스크는 **정본과 같은 키**다(pytmux-12 C) — `Alt+N/E/S/T/O` 와 `Alt+F`.
    #   글자 키로 옮기지 않는 이유: 그러면 손버릇이 갈리고, 이미 `t`(태그)가 정렬의
    #   `t`(시각)와 부딪힌다. 스펙의 키 어휘가 `alt-` 를 알아 그럴 필요가 없다.
    "alt-n": "sort-n", "alt-e": "sort-e", "alt-s": "sort-s",
    "alt-t": "sort-t", "alt-o": "sort-o",
    "alt-f": "mask",
    # ★ **열 수**도 정본과 같은 손이다(pytmux-126) — 원조 Mdir 의 `Alt+1~6` 이 열을
    #   못박고 `Alt+0` 이 자동으로 되돌린다. 이 값은 스펙의 `columns` 로 나가고
    #   (설계 §4.3 `panel`), 0 이면 «클라가 자기 폭을 보고» 정한다.
    "alt-0": "cols-0", "alt-1": "cols-1", "alt-2": "cols-2",
    "alt-3": "cols-3", "alt-4": "cols-4", "alt-5": "cols-5",
    "alt-6": "cols-6",
    # ★ F-키도 정본과 같은 표다(pytmux-125·pytmux-236) — 정본 화면(`screen.py::on_key`)의
    #   F2/F3/F4/F5/F6/F7/F8/F10 을 그대로 옮겼다. 글자 짝은 대체가 아니라 별칭이라
    #   그대로 둔다(노트북 자판에서 F-키는 Fn 조합이라 한 손을 없애면 기능이 사라진다).
    "f2": "rename", "f3": "view", "f4": "cd", "f5": "copy", "f6": "move",
    "f7": "mkdir", "f8": "delete", "f10": "tree",
}

def _reason(code) -> str:
    """서버가 코드로 돌려준 실패 사유를 사람 말로. 카탈로그에 없는 코드는 코드 그대로
    (조용히 빈 칸이 되면 "왜 안 됐나"가 사라진다)."""
    return i18n.t(f"mdir.why_{code}", default=str(code))


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


def _disk(path: str):
    """그 볼륨의 `(남은 바이트, 전체 바이트)`. 못 재면 `(0, 0)`.

    ⛔ **못 잰 것을 0 으로 뭉개지 않는다** — 부르는 쪽이 둘을 갈라야 하므로 전체가 0
    이면 "모른다"이고, 그때 집계줄의 `free` 는 0 이 아니라 **0(0%)** 으로 나가지만
    머리줄(`_volume`)은 아예 빈 줄이 된다(없는 것을 그리는 것보다 안 그리는 것이 낫다).
    """
    import shutil
    try:
        u = shutil.disk_usage(path)
        return int(u.free), int(u.total)
    except (OSError, ValueError):
        return 0, 0


def _volume(free: int, total: int) -> str:
    """머리줄 — 정본 Path 줄의 오른쪽(`Free 남은 것/전체`). 못 재면 빈 줄.

    ⚠ 글자(`Free`)는 집계줄과 같은 부류라 **번역하지 않는다**(원조 서식)."""
    return f"Free {_human(free)}/{_human(total)}" if total else ""


def _when(mtime: int) -> str:
    import time
    try:
        return time.strftime("%Y/%m/%d %H:%M", time.localtime(mtime))
    except (OSError, ValueError):
        return ""


def _result_note(res: dict):
    """조작 결과 한 줄 — `(글, 재료)`.

    **몇 건 됐고 무엇이 왜 안 됐는지**를 알려야 한다: 일괄 작업은 절반만 성공하는 것이
    정상이라(서버가 개별 실패를 모아 계속한다) 성공/실패 둘 중 하나로 뭉개면 사용자가
    무엇을 다시 해야 하는지 모른다.

    # 사유는 줄로, 수는 이 한 줄로 (2026-08-02p)

    종전에는 `실패 2건 — a(이미 있습니다), b(같은 자리입니다)` 처럼 **사유를 이 줄에
    이어 붙였다**. 그 모양은 번역이 안 된다 — 사유를 인자로 넘기면 영어 클라가 자기
    포맷에 한국어 조각을 끼우고(`i18n.phrase` 의 그 함정), 판을 조작×사유로 만들면
    쉰 개가 된다. 그래서 **사유는 그 항목의 칸으로** 내려가고(`_spec`) 이 줄은 수만
    말한다. 눈에 보이는 것은 오히려 늘었다 — 종전엔 앞의 넷만 보였다.

    다만 **아무것도 못 했고 사유가 하나뿐**이면 그 사유가 곧 결과다(대상 디렉터리가
    틀린 경우가 그렇다 — 그 실패는 어느 줄에도 안 붙는다). 그때는 사유 한 줄을 그대로
    돌려준다: 자리가 없는 고정 리터럴이라 클라가 `t()` 로 자기 로케일에서 읽는다."""
    op = str(res.get("op") or "")
    n = int(res.get("done") or 0)
    failed = res.get("failed") or []
    f = len(failed)
    if not n and len({r for _, r in failed}) == 1:
        return _reason(failed[0][1]), None
    # ⚠ 갈래마다 키를 **리터럴로** 적는다. `phrase(key)` 나 `phrase(a if x else b)` 로
    #   묶으면 실려 나가기는 해도 정적 스캔에는 안 잡혀, 게이트가 "번역 못 하는 글"을
    #   못 센다(2026-08-02o 가 servermixin 에서 이미 한 번 밟은 함정).
    if op == "copy":
        return i18n.phrase("mdir.res_copy_fail", n=n, f=f) if failed \
            else i18n.phrase("mdir.res_copy", n=n)
    if op == "move":
        return i18n.phrase("mdir.res_move_fail", n=n, f=f) if failed \
            else i18n.phrase("mdir.res_move", n=n)
    if op == "delete":
        return i18n.phrase("mdir.res_delete_fail", n=n, f=f) if failed \
            else i18n.phrase("mdir.res_delete", n=n)
    if op == "rename":
        return i18n.phrase("mdir.res_rename_fail", n=n, f=f) if failed \
            else i18n.phrase("mdir.res_rename", n=n)
    if op == "mkdir":
        return i18n.phrase("mdir.res_mkdir_fail", n=n, f=f) if failed \
            else i18n.phrase("mdir.res_mkdir", n=n)
    return i18n.t("mdir.why_bad_op"), None


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
    def plugin_screen_closed(self, server, sess, req, closed):
        """ncd 트리가 **디렉터리를 남기고 닫히면** 그 자리로 옮겨간 판을 다시 낸다.

        pytmux-207(`F10` → ncd 트리 → Enter → mdir 이동)의 서버 쪽 절반이다. 종전에는
        이 잇기가 **코어 안**에 있었다 — `servercmd` 가 `p.name == "mdir"` 로 이 객체를
        찾아 사설 `_spec` 을 직접 불렀다(검수 2026-09-05 S-8). 이제 코어는 「어떤 화면이
        무엇을 남기고 닫혔다」만 흘리고, 그 값에 관심을 갖는 것은 여기다 — 정본이
        `getattr(self.app, "request_nc_list", None)` 로 **이름으로만** 잇는 것과 같은 결.

        ⛔ 내 판이 안 열려 있으면 `None` — 남의 화면이 닫혔다고 mdir 이 튀어나오지
        않는다. ncd 를 지우면 이 훅은 영영 안 불리고(그 화면이 없으니), mdir 을 지우면
        훅 자체가 사라진다. 어느 쪽도 남은 쪽을 안 깨뜨린다.

        느린 일(디렉터리 읽기)은 **awaitable 로 돌려준다** — 부르는 쪽이 기다린다.
        상태(`mine`)를 executor 로 넘기는 것은 종전과 같다: 그 dict 는 **이 클라의
        것**이고, 넘기는 동안 이 클라의 코루틴은 여기서 멎어 있다.
        """
        if closed.get("id") != "ncd" or not closed.get("input"):
            return None
        state = req.get("state") or {}
        mine = state.get("mdir")
        if not mine:
            return None                  # 내 판이 안 열려 있다
        mine["path"] = closed["input"]
        import asyncio
        return asyncio.get_event_loop().run_in_executor(
            None, self._spec, mine, 0, "")

    def plugin_screen(self, server, sess, req):
        """네이티브 클라용 파일 관리자 화면.

        # 정본과 **같은 모양**이다(pytmux-126 · 사람 결정 2026-08-10)

        정본은 검정 바탕 **다열** 리스트에 상단 Path/Volume 줄과 하단 집계·정보줄을 두는
        Mdir III 재현이다. 여기도 이제 그 모양이다 — 설계 §4.3 의 일곱째 모양 `panel`
        이 **열 수와 머리·꼬리줄**을 나른다.

        ⛔ **그 셋이 §6 의 선을 넘은 것은 «열 수» 하나뿐이다.** 나머지 둘이 나르는 것은
        표현이 아니라 **자료**다(몇 개가 보이고, 몇 바이트이고, 볼륨에 얼마가 남았고,
        지금 무슨 정렬·마스크가 걸렸는지). 선을 넘는 값을 치른 것은 다열 하나이고,
        그 값과 되돌리는 길은 설계 문서 §4.3·§10 이 쥔다.

        ⚠ 열 수는 **제안**이다 — `0` 이면 클라가 자기 폭을 보고 정한다. 스펙이 못박을
        수 있는 이유는 정본의 `Alt+1~6` 이 그 손을 이미 갖고 있어서다(`alt-N` 키).
        압축 보기처럼 **위젯 고유의 것**은 여전히 안 담는다(담기 시작하면 스펙이
        화면마다 늘어난다 — 설계 §10 의 무한 확장 위험).

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
        if do.startswith("sort-"):
            # 같은 갈래를 다시 누르면 **내림차순**(정본 손버릇) — 전이 규칙은 한 벌이다.
            mine["sort"], mine["rev"] = next_sort(
                mine.get("sort") or "n", bool(mine.get("rev")), do[-1])
            return _offload(self._spec, mine, row, "")
        if do == "mask":
            # 되돌릴 수 없는 것이 아니라 **거르는 것**이지만, 값이 필요하니 물어본다.
            # 빈 대답은 **끄기**다(정본과 같다 — 거는 것과 푸는 것이 한 키다).
            return {"t": "plugin_screen", "id": "mdir", "kind": "prompt",
                    "title": i18n.t("mdir.mask_ask"),
                    "note": "", "rows": [], "text": "",
                    "keys": {"enter": "mask-apply"}, "selected": row}
        if do == "mask-apply":
            mine["mask"] = parse_masks(picked)
            return _offload(self._spec, mine, 0, "")
        if do.startswith("cols-"):
            # 열 수 — `0` 은 «자동»(클라가 폭을 보고 정한다). 정본 `Alt+0~6` 과 같은 손.
            mine["cols"] = int(do[len("cols-"):])
            return _offload(self._spec, mine, row, "")
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
        if do == "tree":
            # 디렉터리 트리(ncd) 팝업을 띄운다. 트리에서 선택하면 mdir 이 그 디렉터리를
            # 보인다(패널은 cd 명령으로 이동 — pytmux-207).
            ncd_state = req.get("state", {}).setdefault("ncd", {})
            ncd_state["path"] = mine.get("path", "")
            ncd_state["cwd"] = mine.get("path", "")
            # ncd 플러그인의 트리 스펙을 만든다
            ncd_plugin = next(
                (p for p in server.plugins.plugins if getattr(p, "name", "") == "ncd"),
                None
            )
            if ncd_plugin is None:
                return None
            # ncd 의 _open_tree 메서드로 스펙을 받는다 — 조회 전용이라 async 아님
            ncd_spec = ncd_plugin._open_tree(ncd_state)
            # 응답 둘: ncd 를 띄우고, ncd 응답이 오면 mdir 을 갱신하는 메시지 받기
            # (실제 갱신은 ncd 응답 처리 시 _on_ncd_into 로 진행)
            return ncd_spec
        if do in ("copy", "move", "delete", "rename", "mkdir"):
            return self._begin(mine, do, picked, row)
        if do == "apply":
            return _offload(self._apply, mine, picked, row)
        if do == "close":
            return {"t": "plugin_screen_close", "id": "mdir"}
        return None

    def _send_to_pane(self, server, sess, text):
        """활성 패널에 글자를 넣는다(정본 F4 와 같은 결과).

        ☠ **`pane.write` 가 아니다**(pytmux-173) — 까닭과 실측은
        `plugins/ncd/__init__.py` §`_send_to_pane` **한 곳**이 쥔다.
        """
        win = sess.active_window if sess else None
        pane = win.active_pane if win else None
        try:
            if pane is not None and pane.pty is not None:
                pane.pty.write(text.encode("utf-8", "replace"))
        except OSError:
            pass

    # ---- 화면 만들기(전부 순수 fs — executor 에서 돈다) ----
    def _spec(self, mine, sel, note):
        """지금 디렉터리의 **다열 판** 스펙(설계 §4.3 `panel`).

        `note` 는 글 하나이거나 `(글, 재료)` 짝이다 — 자리가 있는 글은 **재료까지**
        실어야 영어 클라가 자기 로케일로 다시 짓는다(로케일 ⓑ · `i18n.phrase`).

        # 네 줄이 각자 다른 것을 진다 (⛔ 겹쳐 적지 않는다)

        - `title` — **어디인가**. 마스크가 걸렸으면 그것까지(`mdir.title_mask`).
        - `head`  — **볼륨**(`Free 쓴 것/전체`). 정본 Path 줄의 오른쪽이 이 자리다.
          Path 자체는 제목이 이미 말하므로 여기 다시 안 적는다.
        - `foot`  — **집계**. 셈과 서식은 정본 화면과 같은 함수 한 벌이다
          (`listing.counts`) — 두 벌이면 같은 디렉터리가 두 클라에서 다른 수를 말한다.
        - `note`  — **실패했거나 빈 것**. 평상시엔 비어 있다(빈 목록과 실패는 다르다).
        """
        import os
        from .server import list_entries, _drive_roots
        path = mine.get("path") or os.path.abspath(os.sep)
        entries, err, over = list_entries(path)
        visible = [e for e in entries if mine.get("hidden") or not e["h"]]
        # 늘어놓는 규칙은 **정본 화면과 같은 함수**다(`listing.arrange` — pytmux-12 C).
        # 종전에는 여기서 이름순으로 한 번 정렬하고 끝이라, 정본의 `Alt+E/S/T/O` 정렬과
        # `Alt+F` 마스크가 네이티브 클라에는 통째로 없었다.
        dirs, files = arrange(visible, mine.get("sort") or "n",
                              bool(mine.get("rev")), mine.get("mask"))
        items = dirs + files
        tags = set(mine.get("tags") or [])
        # 직전 조작에서 **안 된 것들** — 사유를 그 줄의 칸에 붙인다(한 번만 보인다).
        fails = mine.pop("fails", None) or {}
        rows = []
        parent = os.path.dirname(path.rstrip("/\\"))
        if parent and parent != path:
            # `..` 는 **자리가 아니라 뜻**으로 나른다(부모 경로 그대로 — ncd 동형).
            rows.append({"key": parent, "label": "   ..",
                         "cols": [i18n.t("mdir.parent")],
                         # 줄의 **뜻**(색은 각 클라가 이 이름으로 푼다 — pytmux-12 A).
                         "tag": row_tag("up")})
        operable = []
        for e in items:
            full = os.path.join(path, e["n"])
            operable.append(full)
            cols = ["<DIR>" if e["d"] else _human(e["s"]), _when(e["m"])]
            if e["n"] in fails:
                # 사유는 **칸**으로 나간다 — 칸은 플러그인이 적은 말이라 클라가 자기
                # 로케일로 읽는다(`PluginRow::say_cols`). 이름은 자료라 안 건드린다.
                # 표식과 사유를 **다른 칸**으로 두는 이유: 이어 붙이면 그 칸은 더 이상
                # 카탈로그의 글이 아니라 클라의 `t()` 가 못 찾는다(영어 클라에 한국어).
                cols += ["✗", _reason(fails[e["n"]])]
            rows.append({
                "key": full,
                # 태그는 **줄 안에도** 보인다(글자). 색만으로 말하면 색을 못 보는
                # 화면에서 그 뜻이 통째로 사라진다.
                "label": ("✓ " if full in tags else "  ")
                         + e["n"] + ("/" if e["d"] else ""),
                "cols": cols,
                # 판정은 정본 화면과 **같은 함수**다(`rowtag`) — 두 벌이면 같은 파일이
                # 두 클라에서 다른 색으로 뜬다.
                "tag": row_tag("dir" if e["d"] else "file", e, full in tags),
            })
        for d in _drive_roots():
            rows.append({"key": d, "label": f"  [-{d[:1]}-]",
                         "cols": [i18n.t("mdir.drive")],
                         "tag": row_tag("drive")})
        # 조작 대상은 이 목록 안의 것뿐이다 — 다음 액션이 `input` 으로 받은 경로를
        # 여기 대고 검증한다(클라가 옛 목록의 줄을 되돌려줘도 엉뚱한 것을 안 지운다).
        mine["items"] = operable
        # 태그도 **이 디렉터리 것만** 남긴다. 안 그러면 화면에 안 보이는 것이 지워진다.
        mine["tags"] = [t for t in (mine.get("tags") or []) if t in set(operable)]
        note_text, note_spec = note if isinstance(note, tuple) else (note, None)
        if not note_text:
            if err:
                note_text, note_spec = i18n.phrase("mdir.read_fail", err=str(err))
            elif over:
                note_text = i18n.t("mdir.too_many")
            elif not operable:
                note_text = i18n.t("mdir.empty")
        # 볼륨은 **한 번만** 잰다(느린 마운트에서 두 번 물으면 그만큼 두 배다).
        # 이 자리는 이미 executor 안이라(`_offload`) 루프를 안 막는다.
        free, total = _disk(path)
        masks = mine.get("mask") or []
        # ⚠ `i18n.phrase` 를 **리터럴 키로** 갈래마다 부른다. 서버가 보내는 글의 게이트가
        #   `phrase("리터럴")` 을 소스에서 긁어 픽스처를 짓기 때문이다 — 키를 변수로
        #   넘기면 실려 나가기는 해도 정적 스캔에 안 잡혀 게이트가 못 센다.
        if masks:
            title, title_spec = i18n.phrase("mdir.title_mask", path=path,
                                            mask=" ".join(masks))
        else:
            title, title_spec = i18n.phrase("mdir.title", path=path)
        carried = {"title": title_spec}
        if note_spec:
            carried["note"] = note_spec
        return {
            "t": "plugin_screen", "id": "mdir", "kind": "panel",
            "title": title,
            "hint": i18n.t("mdir.hint"),
            "rows": rows, "text": "",
            # 열 수는 **제안**이다(정본 `Alt+0~6`) — 0 이면 클라가 자기 폭을 보고 정한다.
            "columns": int(mine.get("cols") or 0),
            # 머리줄 = 볼륨. 못 재는 자리(권한 없는 경로·이상한 마운트)면 **빈 줄**이고,
            # 그때 클라는 그 줄을 아예 안 그린다 — 0 을 적으면 그것이 거짓말이 된다.
            "head": _volume(free, total),
            # 꼬리줄 = 집계. 글자(`File`·`Dir`·`Byte`·`free`)는 Mdir III 원조의 서식이라
            # **번역 대상이 아니다**(색과 같은 부류 — `listing.counts` 주석).
            "foot": counts(dirs, files, mine.get("tags") or [],
                           free, total, mine.get("sort") or "n",
                           bool(mine.get("rev")), bool(mine.get("hidden")),
                           key=lambda e: os.path.join(path, e["n"])),
            "selected": max(0, min(int(sel), max(0, len(rows) - 1))),
            "keys": dict(_SCREEN_KEYS),
            "note": note_text,
            # 자리가 있는 글은 재료까지(로케일 ⓑ). 자리가 없는 글은 원문이 곧 키라
            # 클라가 `t()` 로 읽는다(로케일 ⓐ) — 그래서 여기 안 싣는다.
            "i18n": carried,
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
            return self._spec(mine, row,
                              i18n.phrase("mdir.cant_read", err=str(m["err"])))
        if m.get("binary"):
            return self._spec(mine, row, i18n.t("mdir.binary"))
        return {
            "t": "plugin_screen", "id": "mdir", "kind": "text",
            # 제목은 **파일 이름**이라 번역 대상이 아니다(자료).
            "title": os.path.basename(picked) or picked,
            "hint": i18n.t("mdir.view_hint"),
            # 스크롤될 때만 붙는 토막(pytmux-478 ⑵) — 짧은 파일에서는 안 뜬다.
            # ⚠ 종전 문구의 괄호를 함께 걷었다: 꼬리줄 하나가 통째로 괄호에 싸여 있던
            #    것은 이 판뿐이었고, 토막이 붙고 떨어지면 그 괄호가 말이 안 된다.
            "scroll_hint": i18n.t("mdir.view_scroll_hint"), "rows": [],
            "text": m.get("text") or "", "selected": 0, "keys": {},
            "note": i18n.t("mdir.truncated") if m.get("truncated") else "",
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
        """물음 화면. `title`·`note` 는 글 하나이거나 `(글, 재료)` 짝이다 — 되돌릴 수
        없는 것 앞의 문구라 **무엇이 사라지는지**가 그 클라의 말로 보여야 한다."""
        title_text, title_spec = title if isinstance(title, tuple) else (title, None)
        note_text, note_spec = note if isinstance(note, tuple) else (note, None)
        carried = {}
        if title_spec:
            carried["title"] = title_spec
        if note_spec:
            carried["note"] = note_spec
        return {"t": "plugin_screen", "id": "mdir", "kind": kind,
                "title": title_text, "hint": "", "rows": [], "text": "",
                "note": note_text, "selected": 0, "keys": {"enter": "apply"},
                "i18n": carried}

    def _begin(self, mine, op, picked, row):
        """물음을 세운다. 여기서는 **아무것도 안 한다** — 답이 `apply` 로 돌아온다."""
        import os
        if op == "mkdir":
            mine["ask"] = {"op": "mkdir"}
            return self._ask("prompt", i18n.t("mdir.ask_mkdir"))
        targets = self._targets(mine, picked)
        if not targets:
            return _offload(self._spec, mine, row, i18n.t("mdir.no_targets"))
        names = [os.path.basename(t.rstrip("/\\")) or t for t in targets]
        here = i18n.phrase("mdir.here", path=str(mine.get("path") or ""))
        if op == "copy":
            mine["ask"] = {"op": op, "src": targets}
            return self._ask("prompt",
                             i18n.phrase("mdir.ask_copy", n=len(targets)), here)
        if op == "move":
            mine["ask"] = {"op": op, "src": targets}
            return self._ask("prompt",
                             i18n.phrase("mdir.ask_move", n=len(targets)), here)
        if op == "rename":
            if len(targets) != 1:
                return _offload(self._spec, mine, row, i18n.t("mdir.rename_one"))
            mine["ask"] = {"op": "rename", "src": targets}
            return self._ask("prompt", i18n.phrase("mdir.ask_rename", name=names[0]))
        # 삭제 — 되돌릴 수 없다. **무엇이 사라지는지**를 물음에 함께 싣는다.
        mine["ask"] = {"op": "delete", "src": targets}
        # 이름은 자료라 그대로 나르고, "외 N개"만 재료를 싣는다.
        shown = ", ".join(names[:6])
        if len(names) > 6:
            shown = i18n.phrase("mdir.and_more", names=shown, n=len(names) - 6)
        return self._ask("confirm",
                         i18n.phrase("mdir.ask_delete", n=len(targets)), shown)

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
                return self._spec(mine, row, i18n.t("mdir.no_input"))
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
                i18n.phrase("mdir.ask_overwrite", n=len(res["conflicts"])), names)
        mine["tags"] = []
        # 안 된 것의 **사유는 그 줄의 칸으로** 내려간다(`_spec`) — 결과 줄은 수만 말한다.
        mine["fails"] = {str(n): str(r) for n, r in (res.get("failed") or [])}
        return self._spec(mine, row, _result_note(res))


PLUGIN = _MdirPlugin()
