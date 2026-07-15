"""mdir 서버 측 로직 — 파일/디렉토리 목록·드라이브·디스크 용량(순수 파일시스템).

textual 무관. ncd/server.py 와 같은 위상: 클라(스크린)가 요청(request_mdir_*)을
보내면 serverio 가 handle_server_request 훅으로 이 모듈의 *_msg 빌더를 부른다.
원격 페더레이션에선 플러그인 relay_actions 화이트리스트로 업스트림에 릴레이돼
**원격 머신의 파일시스템**이 보인다(로컬 fs 오응답 방지 — ncd 와 동일 규율).

숨김/실행/읽기전용 판정은 서버(파일 소유자) OS 기준으로 여기서 끝내고 클라에는
불리언 플래그만 보낸다 — 클라 OS 로 다시 판정하면 페더레이션에서 어긋난다."""
from __future__ import annotations

import os
import stat

# 한 디렉토리 표시 상한(원조 Mdir 은 2,000개 제한). 초과분은 자르고 over=True 로
# 알린다 — 초대형/네트워크 디렉토리에서 응답 메시지가 폭주하지 않게(서버는 단일
# 스레드 asyncio 루프라 한 응답이 크면 모든 패널이 같이 멈춘다).
MAX_ENTRIES = 4000


def _is_hidden(name: str, st) -> bool:
    """숨김 판정 — POSIX 는 `.` 접두, Windows 는 FILE_ATTRIBUTE_HIDDEN(2)."""
    if name.startswith("."):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 2)          # FILE_ATTRIBUTE_HIDDEN(stat 모듈 상수는 win 전용)


def list_entries(path: str, limit: int = MAX_ENTRIES):
    """`path` 직계 항목(파일+디렉토리)을 mdir 목록용 dict 로 나열한다.

    반환 (entries, err, over). 각 entry:
      n=이름, d=디렉토리, s=크기(디렉토리는 0), m=mtime(초), h=숨김,
      ro=읽기전용(소유자 쓰기비트 기준 — os.access 는 항목당 syscall 이라 비쌈),
      x=실행파일(소유자 실행비트, 파일만 — 확장자 색과 별개로 초록 표시용)
    숨김 필터링은 **클라**가 한다(토글이 왕복 없이 즉시 반영되게 전부 싣는다).
    개별 항목의 stat 실패(권한·깨진 심링크)는 건너뛰고, 디렉토리 자체를 못 읽으면
    빈 목록 + err 문자열로 graceful — 화면이 죽지 않고 오류를 표시하게 한다."""
    entries: list[dict] = []
    err = None
    over = False
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    d = e.is_dir(follow_symlinks=True)
                except OSError:
                    continue
                mode = st.st_mode
                entries.append({
                    "n": e.name,
                    "d": bool(d),
                    "s": 0 if d else int(st.st_size),
                    "m": int(st.st_mtime),
                    "h": _is_hidden(e.name, st),
                    "ro": not bool(mode & stat.S_IWUSR),
                    "x": bool(mode & stat.S_IXUSR) and not d
                         and stat.S_ISREG(mode),
                })
                if len(entries) >= limit:
                    over = True
                    break
    except OSError as ex:
        return [], f"{type(ex).__name__}: {ex}", False
    return entries, err, over


def _drive_roots() -> list[str]:
    r"""Windows: 사용 가능한 드라이브 루트(["C:\\","D:\\",...]) 정렬 목록. 그 외 [].
    mdir 목록 맨 끝의 `[-C-]` 드라이브 항목(커서로 골라 Enter=드라이브 전환)용.
    (ncd/server.py 와 동일 로직 — 플러그인끼리 import 하지 않는 규율이라 사본.)"""
    if os.name != "nt":
        return []
    try:
        return sorted(os.listdrives())      # Python 3.12+
    except (AttributeError, OSError):
        pass
    out = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        d = f"{c}:\\"
        if os.path.exists(d):
            out.append(d)
    return out


def mdir_list_msg(server, sess, path: str | None = None) -> dict:
    """mdir 목록 요청 응답.

    - `path` 가 비면(팝업 초기 진입) 활성 패널 cwd(코어 범용 헬퍼
      `server._resolve_start_cwd`)에서 시작한다 — 추정 불가면 홈.
    - `path` 가 있으면(탐색: 진입/상위/드라이브 전환) 그 절대경로를 나열한다.
    - 디렉토리가 아니면 entries=[] + err — 클라는 화면을 유지한 채 오류만 표시.
    - free/total = 그 경로가 속한 볼륨의 디스크 용량(하단 집계줄).
    - nt = **셸을 소유한 서버**의 OS — F4(패널 cd)의 방언(cmd `cd /d` vs POSIX)은
      이걸로 정한다(ncd 와 동일: 클라 os.name 을 쓰면 페더레이션에서 오방언)."""
    if path:
        base = os.path.abspath(os.path.expanduser(str(path)))
    else:
        cwd = server._resolve_start_cwd(sess, "current")
        base = os.path.abspath(cwd) if cwd else os.path.expanduser("~")
    if not os.path.isdir(base):
        return {"t": "mdir_list", "path": base, "entries": [], "drives": [],
                "free": 0, "total": 0, "nt": os.name == "nt", "over": False,
                "err": "not a directory"}
    entries, err, over = list_entries(base)
    try:
        import shutil
        du = shutil.disk_usage(base)
        free, total = int(du.free), int(du.total)
    except OSError:
        free = total = 0
    return {"t": "mdir_list", "path": base, "entries": entries,
            "drives": _drive_roots(), "free": free, "total": total,
            "nt": os.name == "nt", "over": over, "err": err}
