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


def _is_fs_root(p: str) -> bool:
    r"""파일시스템 루트('/')·드라이브 루트('C:\\') 여부 — 삭제/이동 대상으로 거부."""
    q = p.rstrip("/\\")
    return q == "" or (len(q) == 2 and q[1] == ":")


def _inside(child: str, parent: str) -> bool:
    """child 가 parent 자신이거나 그 하위인지(디렉토리를 자기 안으로 복사/이동 방지)."""
    c = os.path.normcase(os.path.normpath(child))
    p = os.path.normcase(os.path.normpath(parent))
    return c == p or c.startswith(p + os.sep)


def mdir_op_msg(server, sess, msg: dict) -> dict:
    """mdir 파일 조작 요청(request_mdir_op) — 서버가 shutil/os 로 **직접** 수행한다
    (패널 셸 상태와 무관, 페더레이션이면 릴레이돼 원격 파일시스템에 적용).

    op: copy|move|delete|rename|mkdir. src=절대경로 목록, dst=대상(copy/move=디렉토리,
    rename=새 이름, mkdir=새 디렉토리 이름), base=현재 디렉토리(rename/mkdir 기준),
    overwrite=ask|all|skip.

    덮어쓰기 2단계 프로토콜: overwrite=ask 에서 충돌(대상에 같은 이름 존재)이 하나라도
    있으면 **아무것도 수행하지 않고** conflicts 를 회신한다 — 클라가 사용자에게
    [모두 덮어쓰기/건너뛰기/취소]를 물어 all|skip 으로 재요청한다. 절반만 수행하고
    묻는 것보다 결정론적이고, move 의 '이미 옮겨진 src 재요청' 문제가 없다.

    안전 규율: 파괴적 확인(삭제 확인 팝업)은 클라 몫이지만, 서버도 최소 방어를
    한다 — 루트/드라이브 루트 삭제·이동 거부, 디렉토리를 자기 하위로 복사/이동
    거부, rename/mkdir 이름의 경로 구분자 거부. 개별 실패는 failed 로 모아
    나머지를 계속 진행한다(일괄 작업 중 한 항목 때문에 전체 중단하지 않음)."""
    import shutil
    op = msg.get("op")
    srcs = [str(s) for s in (msg.get("src") or [])]
    dst = msg.get("dst")
    base = msg.get("base")
    overwrite = msg.get("overwrite") or "ask"
    done = 0
    failed: list[list[str]] = []

    def _name(p):
        return os.path.basename(p.rstrip("/\\")) or p

    if op in ("copy", "move"):
        if not dst:
            return {"t": "mdir_result", "op": op, "done": 0,
                    "failed": [["", "no_dst"]], "conflicts": []}
        dstdir = os.path.abspath(os.path.expanduser(str(dst)))
        if not os.path.isdir(dstdir):
            return {"t": "mdir_result", "op": op, "done": 0,
                    "failed": [[dstdir, "dst_not_dir"]], "conflicts": []}
        pairs = []
        conflicts = []
        for s in srcs:
            tgt = os.path.join(dstdir, _name(s))
            if not os.path.exists(s) and not os.path.islink(s):
                failed.append([_name(s), "no_src"])
                continue
            if op == "move" and _is_fs_root(s):
                failed.append([_name(s), "root"])
                continue
            if os.path.isdir(s) and _inside(dstdir, s):
                failed.append([_name(s), "into_self"])
                continue
            if os.path.normcase(os.path.normpath(tgt)) == \
                    os.path.normcase(os.path.normpath(s)):
                failed.append([_name(s), "same"])
                continue
            if os.path.exists(tgt):
                conflicts.append(_name(s))
            pairs.append((s, tgt))
        if conflicts and overwrite == "ask":
            # 수행 없이 되묻기(위 도크스트링의 2단계 프로토콜).
            return {"t": "mdir_result", "op": op, "done": 0, "failed": failed,
                    "conflicts": conflicts}
        for s, tgt in pairs:
            exists = os.path.exists(tgt)
            if exists and overwrite == "skip":
                continue
            try:
                if op == "copy":
                    if os.path.isdir(s) and not os.path.islink(s):
                        shutil.copytree(s, tgt, symlinks=True,
                                        dirs_exist_ok=exists)
                    else:
                        shutil.copy2(s, tgt, follow_symlinks=False)
                else:                     # move
                    if exists:
                        # 디렉토리가 끼면(어느 쪽이든) 덮어쓰기 이동은 병합 의미가
                        # 모호해 거부 — 원조도 물어보고 실패시키는 영역.
                        if os.path.isdir(s) or os.path.isdir(tgt):
                            failed.append([_name(s), "dir_overwrite"])
                            continue
                        os.replace(s, tgt)      # 파일→파일 원자 교체
                    else:
                        shutil.move(s, tgt)
                done += 1
            except OSError as ex:
                failed.append([_name(s), f"{type(ex).__name__}: {ex}"])
        return {"t": "mdir_result", "op": op, "done": done, "failed": failed,
                "conflicts": []}

    if op == "delete":
        for s in srcs:
            if _is_fs_root(s):
                failed.append([_name(s), "root"])
                continue
            try:
                if os.path.isdir(s) and not os.path.islink(s):
                    shutil.rmtree(s)
                else:
                    os.remove(s)
                done += 1
            except OSError as ex:
                failed.append([_name(s), f"{type(ex).__name__}: {ex}"])
        return {"t": "mdir_result", "op": op, "done": done, "failed": failed,
                "conflicts": []}

    if op in ("rename", "mkdir"):
        name = str(dst or "").strip()
        bad = (not name or name in (".", "..")
               or "/" in name or "\\" in name or "\x00" in name)
        if bad:
            return {"t": "mdir_result", "op": op, "done": 0,
                    "failed": [[name, "bad_name"]], "conflicts": []}
        try:
            if op == "mkdir":
                root = os.path.abspath(os.path.expanduser(str(base or "")))
                if not os.path.isdir(root):
                    return {"t": "mdir_result", "op": op, "done": 0,
                            "failed": [[name, "dst_not_dir"]], "conflicts": []}
                os.makedirs(os.path.join(root, name), exist_ok=False)
            else:
                src = srcs[0] if srcs else ""
                tgt = os.path.join(os.path.dirname(src.rstrip("/\\")), name)
                if os.path.exists(tgt):
                    return {"t": "mdir_result", "op": op, "done": 0,
                            "failed": [[name, "exists"]], "conflicts": []}
                os.rename(src, tgt)
            done = 1
        except FileExistsError:
            failed.append([name, "exists"])
        except OSError as ex:
            failed.append([name, f"{type(ex).__name__}: {ex}"])
        return {"t": "mdir_result", "op": op, "done": done, "failed": failed,
                "conflicts": []}

    return {"t": "mdir_result", "op": op or "?", "done": 0,
            "failed": [["", "bad_op"]], "conflicts": []}


# 내장 뷰어가 한 번에 보내는 최대 바이트(원조 '보라(VV)' 대응 — 앞부분만).
VIEW_LIMIT = 256 * 1024
# 압축 내부 목록 상한(폭주 방어 — 목록 상한과 같은 이유).
ARC_MAX_ENTRIES = 5000


def mdir_view_msg(server, sess, path: str) -> dict:
    """내장 텍스트 뷰어(request_mdir_view) — 파일 앞부분(VIEW_LIMIT)을 UTF-8
    (errors=replace)로 회신한다. 첫 8KB 에 NUL 이 있으면 이진 파일로 보고 본문
    없이 binary=True 만 회신(쓰레기 출력 방지). 서버 파일이므로 페더레이션이면
    원격 내용이 온다."""
    p = os.path.abspath(os.path.expanduser(str(path)))
    try:
        # 정규파일만 연다. FIFO(작가 없는 named pipe)나 블록킹 디바이스노드를
        # open/read 하면 무한 대기하는데, 이 빌더가 executor 로 넘어가도 그 스레드가
        # 영영 매달려 스레드풀을 잠식하고(페더레이션 피어가 원격 트리거 가능),
        # 오프로드 이전이라면 단일 asyncio 루프 자체가 멎는다(§뷰어 wedge).
        st = os.stat(p)
        if not stat.S_ISREG(st.st_mode):
            return {"t": "mdir_view", "path": p, "size": 0, "truncated": False,
                    "binary": True, "text": "", "err": "not_regular_file"}
        size = st.st_size
        with open(p, "rb") as f:
            head = f.read(VIEW_LIMIT)
    except OSError as ex:
        return {"t": "mdir_view", "path": p, "size": 0, "truncated": False,
                "binary": False, "text": "", "err": f"{type(ex).__name__}: {ex}"}
    if b"\x00" in head[:8192]:
        return {"t": "mdir_view", "path": p, "size": size, "truncated": False,
                "binary": True, "text": "", "err": None}
    return {"t": "mdir_view", "path": p, "size": size,
            "truncated": size > len(head), "binary": False,
            "text": head.decode("utf-8", errors="replace"), "err": None}


_TAR_EXTS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")


def mdir_arc_msg(server, sess, path: str) -> dict:
    """압축파일 내부 목록(request_mdir_arc) — zip/jar 는 zipfile, tar 계열은
    tarfile(표준 라이브러리, 읽기전용). 내부 경로를 그대로 실어 클라가 '디렉토리
    재현'(원조 Alt-\\`)처럼 계층 탐색한다. rar/7z/lzh 등 표준 라이브러리 밖 형식은
    arc_unsupported 코드로 거절(클라가 번역)."""
    p = os.path.abspath(os.path.expanduser(str(path)))
    low = p.lower()
    entries: list[dict] = []
    # 지원 형식 판정을 stat 보다 먼저 — 미지원 확장자는 파일이 없어도(존재 여부와
    # 무관하게) arc_unsupported 로 거절한다(클라가 번역).
    is_zip = low.endswith((".zip", ".jar"))
    is_tar = low.endswith(_TAR_EXTS)
    if not (is_zip or is_tar):
        return {"t": "mdir_arc", "path": p, "entries": [],
                "err": "arc_unsupported"}
    try:
        # 뷰어와 같은 이유로 정규파일만(FIFO/디바이스노드에 zipfile/tarfile.open 이
        # 매달리는 것 차단 — mdir_view_msg 주석 참조). open 직전에만 stat 한다.
        if not stat.S_ISREG(os.stat(p).st_mode):
            return {"t": "mdir_arc", "path": p, "entries": [],
                    "err": "not_regular_file"}
        if is_zip:
            import zipfile
            with zipfile.ZipFile(p) as z:
                for i in z.infolist()[:ARC_MAX_ENTRIES]:
                    entries.append({"n": i.filename, "d": i.is_dir(),
                                    "s": int(i.file_size)})
        else:
            import tarfile
            with tarfile.open(p) as tf:
                for m in tf:
                    entries.append({"n": m.name, "d": m.isdir(),
                                    "s": int(m.size)})
                    if len(entries) >= ARC_MAX_ENTRIES:
                        break
    except OSError as ex:
        return {"t": "mdir_arc", "path": p, "entries": [],
                "err": f"{type(ex).__name__}: {ex}"}
    except Exception as ex:          # BadZipFile/TarError 등 형식 오류
        return {"t": "mdir_arc", "path": p, "entries": [],
                "err": f"{type(ex).__name__}: {ex}"}
    return {"t": "mdir_arc", "path": p, "entries": entries, "err": None}


def mdir_list_msg(server, sess, path: str | None = None) -> dict:
    """mdir 목록 요청 응답.

    - `path` 가 비면(팝업 초기 진입) 활성 패널 cwd(코어 범용 헬퍼
      `server._resolve_start_cwd`)에서 시작한다 — 추정 불가면 홈.
    - `path` 가 있으면(탐색: 진입/상위/드라이브 전환) 그 절대경로를 나열한다.
    - 디렉토리가 아니면 entries=[] + err — 클라는 화면을 유지한 채 오류만 표시.
    - free/total = 그 경로가 속한 볼륨의 디스크 용량(하단 집계줄).
    - nt = **셸을 소유한 서버**의 OS — F4(패널 cd)의 방언(cmd `cd /d` vs POSIX)은
      이걸로 정한다(ncd 와 동일: 클라 os.name 을 쓰면 페더레이션에서 오방언)."""
    return mdir_list_fs(mdir_list_resolve_base(server, sess, path))


def mdir_list_resolve_base(server, sess, path: str | None) -> str:
    """목록 시작 경로를 정한다. `path` 가 비면 활성 패널 cwd(세션 상태 읽기)에서
    시작한다 — 이 세션 접근은 **반드시 asyncio 루프 스레드**에서 해야 한다(executor
    스레드에서 sess 를 만지면 레이스). 순수 경로 계산이라 여기서 즉시 끝난다."""
    if path:
        return os.path.abspath(os.path.expanduser(str(path)))
    cwd = server._resolve_start_cwd(sess, "current")
    return os.path.abspath(cwd) if cwd else os.path.expanduser("~")


def mdir_list_fs(base: str) -> dict:
    """이미 해석된 `base` 디렉토리를 나열한다(순수 파일시스템 — executor 안전).
    대형·네트워크 디렉토리의 scandir/stat/disk_usage 블로킹이 여기 갇힌다."""
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
