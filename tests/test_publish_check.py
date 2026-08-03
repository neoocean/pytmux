"""게시 게이트(scripts/publish_check.py) — 드리프트 **방향 판정**과 CL 부정 게이트.

왜 테스트하나: 이 게이트가 조용히 뒤집히면(방향 오분류·필터 과잉) "✓ 미러 일치"가
거짓 안심이 된다 — 정확히 로드맵 §5.1 이 막으려던 사고를 게이트가 은폐하는 형태다.
p4/git 명령은 `run()` 을 갈아끼워 주입한다(실 depot 상태에 의존하면 머신마다 다른
거짓 실패).
"""
import importlib.util
import os

import harness

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "publish_check", os.path.join(os.path.dirname(HERE), "scripts",
                                  "publish_check.py"))
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

ROOT = pc.ROOT


def _fake_run(mapping):
    """cmd 앞부분 매칭 → (rc, stdout). 미지정 명령은 (0, "")."""
    def run(cmd, cwd=ROOT):
        key = " ".join(cmd)
        for prefix, val in mapping.items():
            if key.startswith(prefix):
                return val
        return 0, ""
    return run


def _gate(mapping, ignored=(), **kw):
    old_run, old_ign = pc.run, pc.git_ignored
    pc.run = _fake_run(mapping)
    pc.git_ignored = lambda paths: {p for p in paths if p in ignored}
    out = []
    try:
        rc = pc.check_mirror(out=out.append, **kw)
    finally:
        pc.run, pc.git_ignored = old_run, old_ign
    return rc, "\n".join(out)


_CLEAN = {
    "git rev-parse HEAD": (0, "abc123\n"),
    "git log --oneline origin/main..HEAD": (0, ""),
    "git ls-remote": (0, "abc123\trefs/heads/main\n"),
    "git merge-base": (0, ""),
    "p4 opened": (0, ""),
    "p4 diff -se": (0, ""),
    "git status --porcelain": (0, ""),
}


async def test_clean_tree_reports_in_sync():
    rc, text = _gate(dict(_CLEAN))
    assert rc == 0, text
    assert "미러 일치" in text, text


async def test_git_only_content_is_reported_as_p4_unsubmitted():
    """depot 과 다른데 git 은 clean → git 에만 있는 내용(실사고: .gitignore `.env` 규칙)."""
    rc, text = _gate({**_CLEAN,
                      "p4 diff -se": (0, f"{ROOT}/.gitignore\n")})
    assert rc == 1, text
    assert "p4 미제출" in text and ".gitignore" in text, text
    assert "git 미푸시" not in text, "방향을 뒤집어 보고하면 안 된다: " + text


async def test_p4_only_content_is_reported_as_git_unpushed():
    """depot 과 같은데 git HEAD 와 다름 → p4 만 게시된 상태(미러 stall 63471·63714)."""
    rc, text = _gate({**_CLEAN,
                      "git status --porcelain": (0, " M pytmuxlib/server.py\n")})
    assert rc == 1, text
    assert "git 미푸시" in text and "pytmuxlib/server.py" in text, text
    assert "p4 미제출" not in text, "방향을 뒤집어 보고하면 안 된다: " + text


async def test_wip_on_both_sides_is_not_drift():
    """열려 있거나 양쪽 다 다른 파일은 그냥 작업 중 — 드리프트로 보고하면 게이트가
    상시 빨개져 아무도 안 본다."""
    rc, text = _gate({**_CLEAN,
                      "p4 opened": (0, f"{ROOT}/tests/run.py#12 - edit …\n"),
                      "p4 diff -se": (0, f"{ROOT}/CLAUDE.md\n"),
                      "git status --porcelain": (0, " M CLAUDE.md\n M tests/run.py\n"
                                                    "?? tests/test_new.py\n")})
    assert rc == 0, text
    assert "작업 중 3개" in text, text
    # "미푸시 커밋 없음"(성공 문구)에 걸리지 않게 드리프트 **헤더**로 단언한다.
    assert "✗ p4 미제출" not in text and "✗ git 미푸시" not in text, text


async def test_opened_files_are_recognized_from_depot_syntax():
    """`p4 opened` 는 **depot 경로**를 준다 — `p4 diff -se` 의 로컬 경로와 형식이 다르다.

    종전엔 둘 다 로컬 경로로만 환원해서 열린 파일 집합이 **통째로 비었다**(`rel()` 이
    ROOT 밖이라 ''). 그러면 `p4 edit` 해 두고 고치는 중인 파일이 매번 '✗ git 미푸시'
    로 잡혀, 제출 직전 게이트가 상시 적색이 된다 — 게이트가 적색을 늘 달고 있으면
    진짜 드리프트를 아무도 못 본다. 픽스처를 **실제 p4 출력 그대로** 쓴다."""
    rc, text = _gate({**_CLEAN,
                      "p4 opened": (0, "//woojinkim/scripts/pytmux/pytmuxlib/"
                                       "server.py#12 - edit default change (unicode)\n"),
                      "git status --porcelain": (0, " M pytmuxlib/server.py\n")})
    assert rc == 0, text
    assert "✗ git 미푸시" not in text, text
    assert "작업 중 1개" in text, text


async def test_p4_only_files_are_filtered_by_gitignore():
    """docs/internal·captures 는 p4 전용(gitignore) — '미제출'로 오검출되면 게이트가
    영구히 빨갛다."""
    rc, text = _gate({**_CLEAN,
                      "p4 diff -se": (0, f"{ROOT}/docs/internal/HANDOFF.md\n")},
                     ignored=("docs/internal/HANDOFF.md",))
    assert rc == 0, text
    assert "HANDOFF" not in text, text


async def test_unpushed_commits_are_flagged():
    rc, text = _gate({**_CLEAN,
                      "git log --oneline origin/main..HEAD": (0, "deadbee 수정 (p4 1)\n")})
    assert rc == 1 and "미푸시 커밋 1개" in text, text


# ── 존재 드리프트 ────────────────────────────────────────────────────────────
# 내용 대조(`p4 diff -se` × `git status`)가 **원리적으로** 못 보는 구멍이다: depot 에 아예
# 없는 파일은 `p4 diff -se` 에 안 나오고, 커밋까지 끝난 파일은 `git status` 에 안 나온다.
# 그래서 "git 에만 add 하고 p4 add 를 잊었다" 는 종전 게이트를 **초록불로 통과**했다.

_DEPOT = "//woojinkim/scripts/pytmux/"


def _files(*entries):
    """`p4 files` 출력 모사. entries = (경로, 액션)."""
    return "\n".join(f"{_DEPOT}{p}#3 - {a} change 1 (text)" for p, a in entries)


def _existence(mapping, ignored=()):
    old_run, old_ign = pc.run, pc.git_ignored
    pc.run = _fake_run(mapping)
    pc.git_ignored = lambda paths: {p for p in paths if p in ignored}
    out = []
    try:
        rc = pc.check_existence(out=out.append)
    finally:
        pc.run, pc.git_ignored = old_run, old_ign
    return rc, "\n".join(out)


async def test_file_committed_to_git_but_never_added_to_p4_is_caught():
    rc, text = _existence({
        "p4 files": (0, _files(("pytmuxlib/server.py", "edit"))),
        "git ls-files": (0, "pytmuxlib/server.py\npytmuxlib/brandnew.py\n"),
    })
    assert rc == 1, text
    assert "depot 에 없는 파일" in text and "brandnew.py" in text, text
    assert "p4 add" in text, "고치는 방법을 알려줘야 한다: " + text


async def test_file_submitted_to_p4_but_never_committed_to_git_is_caught():
    rc, text = _existence({
        "p4 files": (0, _files(("pytmuxlib/server.py", "edit"),
                               ("pytmuxlib/onlyp4.py", "add"))),
        "git ls-files": (0, "pytmuxlib/server.py\n"),
    })
    assert rc == 1, text
    assert "git 에 없는 파일" in text and "onlyp4.py" in text, text


async def test_p4_only_files_that_are_gitignored_are_not_drift():
    """docs/internal·captures·db 는 p4 전용이다 — 여기서 울면 게이트가 영구히 빨갛다."""
    rc, text = _existence(
        {"p4 files": (0, _files(("docs/internal/HANDOFF.md", "edit"))),
         "git ls-files": (0, "")},
        ignored=("docs/internal/HANDOFF.md",))
    assert rc == 0, text
    assert "HANDOFF" not in text, text


async def test_moved_out_docs_are_no_longer_exempt_from_the_existence_check():
    """§10-17 이관의 한시 예외(`GIT_ONLY_PREFIXES`)는 **지웠다**.

    셋(`docs/benchmark/`·`docs/image/`·`docs/PENDING_UI_IMPROVEMENTS.md`)은
    `docs/internal/` 로 이사했고 미러에서도 `git rm --cached` 로 내렸다 — 그러니 이제
    저 경로가 git 에만 있다면 그건 **정상이 아니라 드리프트**다(이사한 자리로 누가 다시
    커밋했거나, 되돌아온 것). 예외를 되살리면 이 셋이 다시 죽는다."""
    for path in ("docs/benchmark/darwin-arm64/x.json",
                 "docs/image/02-split-lr.svg",
                 "docs/PENDING_UI_IMPROVEMENTS.md"):
        rc, text = _existence({
            "p4 files": (0, _files(("CLAUDE.md", "edit"))),
            "git ls-files": (0, f"CLAUDE.md\n{path}\n"),
        })
        assert rc == 1, f"{path} 가 예외로 새고 있다: {text}"
        assert path in text, text


async def test_deleted_revisions_do_not_count_as_present_in_depot():
    """`p4 files` 는 지워진 파일도 마지막 리비전으로 보여준다. 그대로 세면 옛날에 **옮긴**
    파일이 영원히 'git 에 없다'로 보고된다(실측: `docs/*.md` 40여 개가 move/delete 였다)."""
    rc, text = _existence({
        "p4 files": (0, _files(("CLAUDE.md", "edit"),
                               ("docs/HANDOFF.md", "move/delete"),
                               ("docs/OLD.md", "delete"))),
        "git ls-files": (0, "CLAUDE.md\n"),
    })
    assert rc == 0, text
    assert "HANDOFF" not in text and "OLD" not in text, text


async def test_mirror_gate_actually_runs_the_existence_check():
    """**호출부를 겨눈 오라클.** 위 다섯은 `check_existence` 를 직접 부르므로,
    `check_mirror` 에서 그 **호출 한 줄을 지워도 전부 통과한다**(실측 — 이 저장소가
    반복해서 물린 '공허 통과'). 게이트가 쓰는 경로는 `check_mirror` 하나뿐이니
    거기서도 울어야 한다."""
    rc, text = _gate({**_CLEAN,
                      "p4 files": (0, _files(("CLAUDE.md", "edit"))),
                      "git ls-files": (0, "CLAUDE.md\npytmuxlib/brandnew.py\n")})
    assert rc == 1, text
    assert "depot 에 없는 파일" in text and "brandnew.py" in text, text
    assert "미러 일치" not in text, "드리프트가 있는데 초록불을 냈다: " + text


async def test_cl_gate_rejects_foreign_files():
    """CL 스펙에 남의 파일이 실렸으면(병렬 세션 WIP) 0 이 아니어야 한다."""
    old = pc.run
    pc.run = _fake_run({"p4 opened -c 66940": (
        0, "//depot/playground/scripts/pytmux/tests/run.py#3 - edit change 66940\n"
           "//depot/playground/rx/other.py#1 - edit change 66940\n")})
    out = []
    try:
        rc = pc.check_cl("66940", out=out.append)
    finally:
        pc.run = old
    text = "\n".join(out)
    assert rc == 1, text
    assert "남의 파일" in text and "rx/other.py" in text, text
    assert "reopen -c default" in text, "되돌리는 방법을 알려줘야 한다: " + text


async def test_cl_gate_accepts_own_files_only():
    old = pc.run
    pc.run = _fake_run({"p4 opened -c 1": (
        0, "//depot/playground/scripts/pytmux/tests/run.py#3 - edit change 1\n")})
    out = []
    try:
        rc = pc.check_cl("1", out=out.append)
    finally:
        pc.run = old
    assert rc == 0 and "내 파일만" in "\n".join(out)


# ── 진짜 git 을 거치는 필터 ───────────────────────────────────────────────────
# 위 존재-대조 테스트들은 `git_ignored` 를 **통째로 스텁**한다(`_existence(..., ignored=)`).
# 그래서 그 함수 자체가 무엇을 돌려주든 전부 초록이었고, 실제로 **Windows 에서 필터가
# 아무것도 못 거르는 상태로 넉 달을 갔다**(2026-08-03 실측 · 오탐 6813건 · 그 소음에
# 진짜 드리프트 두 건이 묻혔다). 그러니 하나는 실 git 을 거쳐야 한다.


def _git_repo_with_gitignore(tmp, rules):
    import subprocess
    subprocess.run(["git", "init", "-q", tmp], capture_output=True)
    with open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as f:
        f.write("\n".join(rules) + "\n")


async def test_git_ignored_returns_paths_verbatim_so_set_subtraction_works():
    """반환값은 **넣은 문자열 그대로**여야 한다 — 호출부가 집합 뺄셈을 하기 때문이다.

    종전 구현은 `text=True` + 개행 join 이었다. Windows 에서 `text=True` 는 stdin 에
    유니버설 개행을 적용해 `\\n` 을 `\\r\\n` 으로 쓰고, git 은 그 `\\r` 을 경로의 일부로
    읽는다 → 특수문자가 낀 경로라 결과를 `"…\\r"` 로 **인용·이스케이프**해서 돌려준다.
    그러면 `set(raw) - git_ignored(raw)` 가 **한 건도 못 뺀다**. 즉 무시 목록이 맞는지가
    아니라 **문자열이 왕복하는지**가 오라클이다(비ASCII 경로도 같은 인용에 걸린다)."""
    import shutil
    import subprocess
    import tempfile
    if not shutil.which("git"):
        from run import skip
        skip("git 이 PATH 에 없다")

    tmp = tempfile.mkdtemp(prefix="pubchk-")
    try:
        _git_repo_with_gitignore(tmp, ["captures/", "/MEMORY.md", "docs/internal/"])
        if subprocess.run(["git", "-C", tmp, "rev-parse"],
                          capture_output=True).returncode:
            from run import skip
            skip("임시 git 저장소를 만들지 못했다")

        ignored = ["captures/a.log.gz", "MEMORY.md",
                   "docs/internal/한글 문서.md"]   # 비ASCII+공백 = 인용 유발
        kept = ["pytmuxlib/server.py", "docs/CONTRIBUTING.md"]
        with harness.patched(pc, ROOT=tmp):
            got = pc.git_ignored(ignored + kept)

        assert got == set(ignored), (
            "넣은 문자열 그대로 돌아오지 않았다 — 호출부의 집합 뺄셈이 무효가 된다.\n"
            f"  기대: {sorted(ignored)}\n  실제: {sorted(got)}")
        assert not (got & set(kept)), f"안 무시되는 파일을 걸렀다: {sorted(got & set(kept))}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def test_existence_check_uses_the_real_filter_not_a_stub():
    """**호출부를 겨눈 오라클.** 위 테스트는 `git_ignored` 를 직접 부르므로,
    `check_existence` 에서 그 **호출을 지워도** 통과한다(이 저장소가 반복해 물린
    '공허 통과' — 뮤테이션에 '호출 제거'를 포함할 것)."""
    calls = []

    def spy(paths):
        calls.append(list(paths))
        return set(paths)

    old_run, old_ign = pc.run, pc.git_ignored
    pc.run, pc.git_ignored = _fake_run({
        "p4 files": (0, _files(("captures/a.log.gz", "add"))),
        "git ls-files": (0, ""),
    }), spy
    out = []
    try:
        rc = pc.check_existence(out=out.append)
    finally:
        pc.run, pc.git_ignored = old_run, old_ign
    assert calls, "check_existence 가 gitignore 필터를 아예 안 불렀다"
    assert calls[0] == ["captures/a.log.gz"], calls
    assert rc == 0, "\n".join(out)
