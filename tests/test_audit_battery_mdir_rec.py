"""검수 새 배터리 2026-07-25 — mdir / rec / (federation 표시층) 적대적 입력.

배경: 검수 2026-07-17 §6 말미의 잔여 5줄 중 `MDIR-2/3`·`REC-1/2`·`F5/F6` 는 **이름만
있고 정의부가 없어**(전 문서 grep 0건) 다음 사이클에 복원 불가능한 부채가 됐다(§9.3).
옛 ID 를 억측으로 되살리는 대신 **세 영역을 새로 훑는** 것이 정직한 처리라고 판정했고,
이 파일이 그 배터리다. 발견은 여기 테스트로 남고(=증상+앵커+재현이 코드로 보존된다),
같은 실수를 반복하지 않는다.

이 배터리가 고정하는 계약:
  mdir · 목적지 심볼릭 링크는 **따라가지 않는다**(MDIR-1 의 rename 형제까지)
        · 파일→디렉토리 덮어쓰기는 **조용히 중첩하지 않는다**
        · 루트·자기하위·경로구분자 이름은 거부
  rec  · 캡처 파일명은 세션/탭 이름의 경로구분자·상대참조·제어문자를 **먹지 않는다**
        · 캡처 디렉터리 0700 · 파일 0600(같은 머신 타 사용자 열람 차단)
"""
import importlib
import os
import stat
import tempfile

import harness  # noqa: F401

mserver = importlib.import_module("pytmuxlib.plugins.mdir.server")
recmix = importlib.import_module("pytmuxlib.plugins.rec.servermixin")


def _op(**kw):
    """mdir_op_msg 호출 축약(서버·세션은 이 경로에서 안 쓰인다)."""
    return mserver.mdir_op_msg(None, None, kw)


def _symlink(target, link):
    """심링크를 만들되, **권한이 없으면 명시 SKIP** 한다.

    Windows 는 `SeCreateSymbolicLinkPrivilege`(개발자 모드 또는 관리자) 없이는
    `os.symlink` 가 `WinError 1314` 로 죽는다. 종전에는 그것이 **상시 FAIL 2건**으로
    남아 합본 게이트가 이 상자에서 영원히 빨간불이었다 — 그러면 진짜 회귀가 하나 더
    늘어도 눈에 안 띈다("원래 2건 실패"로 읽힌다). 이건 결함이 아니라 **환경 부적합**
    이므로 저장소 규약대로 SKIP 으로 적는다(루트 CLAUDE.md 「명시 SKIP」) — 요약의
    `N skipped` 와 사유별 리포트에 남아 **커버리지 갭이 보이는** 쪽이 정직하다.

    ⚠ 다른 OSError 는 삼키지 않는다. 이 배터리가 재는 계약(끊어진 링크를 따라가지
    않는다)은 링크를 실제로 만들 수 있을 때만 성립하고, 만들다 난 **다른** 실패는
    진짜 문제다.
    """
    try:
        os.symlink(target, link)
    except OSError as e:
        if os.name == "nt" and getattr(e, "winerror", None) == 1314:
            from run import skip
            skip("Windows: 심링크 생성 권한 없음(개발자 모드/관리자 필요)")
        raise


# ── mdir: 목적지 심볼릭 링크(MDIR-1 계열 전수) ─────────────────────────────

async def test_copy_does_not_follow_dangling_dst_symlink():
    """MDIR-1 회귀(고정 유지): 목적지가 **끊어진** 링크면 lexists 로 충돌 판정하고,
    덮어쓰기를 골라도 링크를 지운 뒤 그 자리에 쓴다(링크 대상에 쓰지 않는다)."""
    d = tempfile.mkdtemp()
    src = os.path.join(d, "notes.txt")
    with open(src, "w") as f:
        f.write("payload")
    dst = os.path.join(d, "dstdir")
    os.mkdir(dst)
    outside = os.path.join(d, "outside.txt")          # 링크 대상(존재하지 않음)
    _symlink(outside, os.path.join(dst, "notes.txt"))
    r = _op(op="copy", src=[src], dst=dst, overwrite="ask")
    assert r["conflicts"] == ["notes.txt"], "끊어진 링크를 충돌로 못 봤다"
    r = _op(op="copy", src=[src], dst=dst, overwrite="all")
    assert r["done"] == 1 and not r["failed"]
    assert not os.path.exists(outside), "링크를 따라가 dstdir 밖에 썼다"
    assert not os.path.islink(os.path.join(dst, "notes.txt")), "링크가 남았다"


async def test_rename_does_not_clobber_through_dangling_symlink():
    """**새로 발견(M-A)**: rename 의 충돌 검사가 `exists`(링크 추종)라, 목적 이름이
    끊어진 링크면 '없음'으로 보고 그 링크를 **말없이 대체**했다. 사용자는 "그 이름은
    비어 있다"고 믿는데 실제로는 있던 항목(링크)이 사라진다 — copy/move 가 lexists 로
    고쳐진 그 형제 결함이다(같은 클래스, 다른 호출부)."""
    d = tempfile.mkdtemp()
    src = os.path.join(d, "a.txt")
    with open(src, "w") as f:
        f.write("x")
    link = os.path.join(d, "b.txt")
    _symlink(os.path.join(d, "gone.txt"), link)       # 끊어진 링크
    r = _op(op="rename", src=[src], dst="b.txt", base=d)
    assert r["done"] == 0, "끊어진 링크 이름을 조용히 대체했다"
    assert r["failed"] and r["failed"][0][1] == "exists"
    assert os.path.islink(link), "링크가 지워졌다"
    assert os.path.exists(src), "원본이 옮겨졌다"


async def test_copy_file_over_existing_directory_is_refused():
    """**새로 발견(M-B)**: 파일 X 를 같은 이름의 **디렉토리** X 위로 덮어쓰면
    `copy2` 가 그 안으로 복사해 `X/X` 가 생기는데 결과는 `done=1`(성공)이었다 —
    사용자가 기대한 것은 대체이고, 조용한 중첩은 move 쪽이 이미 `dir_overwrite` 로
    거부하는 상황이다(두 경로가 같은 규칙을 써야 한다)."""
    d = tempfile.mkdtemp()
    src = os.path.join(d, "X")
    with open(src, "w") as f:
        f.write("file")
    dst = os.path.join(d, "dstdir")
    os.makedirs(os.path.join(dst, "X"))              # 같은 이름의 디렉토리
    r = _op(op="copy", src=[src], dst=dst, overwrite="all")
    assert r["done"] == 0, "파일→디렉토리 덮어쓰기가 성공으로 보고됐다"
    assert r["failed"] and r["failed"][0][1] == "dir_overwrite"
    assert not os.path.exists(os.path.join(dst, "X", "X")), "조용히 중첩 복사됐다"


async def test_root_and_self_nesting_and_bad_names_refused():
    """기존 방어(고정): 루트 삭제/이동 · 디렉토리를 자기 하위로 · 이름의 경로구분자."""
    d = tempfile.mkdtemp()
    sub = os.path.join(d, "sub")
    os.mkdir(sub)
    assert _op(op="delete", src=["/"])["failed"][0][1] == "root"
    assert _op(op="move", src=["/"], dst=d)["failed"][0][1] == "root"
    assert _op(op="copy", src=[d], dst=sub)["failed"][0][1] == "into_self"
    for bad in ("../evil", "a/b", "a\\b", "", ".", ".."):
        r = _op(op="mkdir", src=[], dst=bad, base=d)
        assert r["failed"] and r["failed"][0][1] == "bad_name", bad


# ── rec: 캡처 파일명·권한 ──────────────────────────────────────────────────

async def test_capture_filename_never_escapes_directory():
    """세션/탭 이름은 사용자가 자유롭게 바꾼다(rename-tab). 그 문자열이 파일명 조각에
    그대로 들어가면 `../` 로 캡처 디렉터리를 벗어나거나 제어문자가 파일명에 박힌다."""
    for hostile in ("../../etc/passwd", "a/b", "a\\b", "..", ".",
                    "tab\x00null", "sp ace", "탭\x1b[31m", "a" * 300):
        out = recmix._safe(hostile)
        # 탈출은 화이트리스트가 막는다(구분자·NUL·ESC 제거) — 아래 join 단언이 정본.
        assert "/" not in out and "\\" not in out, out
        assert "\x00" not in out and "\x1b" not in out, repr(out)
        # 점 런·양끝 점은 남기지 않는다: Windows 는 끝 점을 잘라 이름이 **충돌**하고
        # POSIX 는 선행 점이 숨김 파일이 된다(정상 이름 `my.tab` 은 그대로 통과).
        assert ".." not in out and not out.startswith(".") \
            and not out.endswith("."), repr(out)
        assert out, "빈 조각은 'x' 로 대체돼야 한다"
        # 조각을 합친 경로가 캡처 루트를 벗어나지 않는다. base 도 abspath 로 만든다 —
        # 리터럴 "/tmp/captures" 를 쓰면 Windows 에서 좌변만 드라이브·역슬래시로
        # 정규화돼(`D:\tmp\captures\…`) 우변(`/tmp/captures\`)과 형태가 어긋난다
        # (탈출을 잡은 것이 아니라 **표기가 달라** 실패한다).
        base = os.path.abspath(os.path.join(tempfile.gettempdir(), "captures"))
        assert os.path.abspath(os.path.join(base, out)).startswith(base + os.sep)
    assert recmix._safe("my.tab.name") == "my.tab.name", "정상 이름 불변"
    assert recmix._safe("..") == "x" and recmix._safe(".") == "x"


async def test_capture_dir_and_file_permissions_are_private():
    """캡처 raw 에는 화면에 에코된 비밀·토큰이 남을 수 있다 → 디렉터리 0700·파일 0600.
    (F4 규율. 권한이 풀리면 같은 머신의 다른 로컬 사용자가 읽는다.)"""
    if os.name == "nt":
        from run import skip
        skip("POSIX 권한 전용")
    d = tempfile.mkdtemp()
    os.chmod(d, 0o755)
    from pytmuxlib import ipc
    p = os.path.join(d, "f.log")
    with ipc.open_private(p, "a") as f:
        f.write("x")
    assert stat.S_IMODE(os.stat(p).st_mode) & 0o077 == 0, "캡처 파일이 열려 있다"


# ── federation: 신뢰불가 상류가 실어 보낸 목록(F-G) ────────────────────────

async def test_mdir_entries_from_hostile_upstream_are_capped_and_shaped():
    """**새로 발견(F-G)**: 원격 보기에서 mdir/ncd 목록은 상류가 준 그대로 클라에 온다
    (릴레이 액션). 로컬 서버는 MAX_ENTRIES 로 자르지만 **상류는 그 약속에 묶이지
    않는다** — 종전엔 ①무제한 길이(정렬·렌더로 UI 프리즈) ②모양 무검증(`e["n"]`
    부재/비-dict → 정렬에서 예외 → Textual fatal 로 클라 사망)이었다. F-A(상류
    windows[] wedge)·CLI-1(스위처 크래시)과 같은 클래스."""
    mscreen = importlib.import_module("pytmuxlib.plugins.mdir.screen")
    hostile = ([{"n": "f%d" % i} for i in range(50_000)]
               + ["문자열", 42, None, {}, {"n": ""}, {"n": 7},
                  {"n": "ok", "s": "많이", "m": {"a": 1}}])
    out = mscreen._sane_entries(hostile)
    assert len(out) <= mscreen._MAX_ENTRIES, "상한이 없다(%d)" % len(out)
    assert all(isinstance(e, dict) and isinstance(e["n"], str) and e["n"]
               for e in out), "이름 없는/잡값 항목이 통과했다"
    # 수치 필드도 정렬이 터지지 않게 정규화된다(_sort_key 가 s/m 으로 비교한다).
    shaped = mscreen._sane_entries([{"n": "ok", "s": "많이", "m": None}])
    assert isinstance(shaped[0]["s"], (int, float))
    # 비-리스트(상류가 dict/문자열을 실어도)는 빈 목록으로 접힌다.
    assert mscreen._sane_entries({"n": "x"}) == []
    assert mscreen._sane_entries(None) == []


async def test_ncd_children_from_hostile_upstream_are_capped():
    """ncd 트리도 같은 처방(상한 + 문자열만)."""
    nscreen = importlib.import_module("pytmuxlib.plugins.ncd.screen")
    view = nscreen._NcdView.__new__(nscreen._NcdView)
    view._children = {}
    view._pending = None
    view._expanded = set()
    # 생성자를 우회하므로 생성자가 정하는 것도 여기서 정해야 한다: `_nt` 는 **경로의 OS**
    # (서버가 nc_list 로 알려 주는 값)이고 `_children` 키가 그걸로 정규화된다(p4 67613).
    view._nt = False
    view._rebuild_rows = lambda keep_path=None: None
    view.fill_children("/p", ["a"] * 50_000 + [7, None, {}, ""])
    got = view._children["/p"]
    assert len(got) <= nscreen._MAX_DIRS and all(isinstance(d, str) and d
                                                 for d in got)
