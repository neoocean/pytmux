"""게시 게이트(scripts/publish_check.py) — 드리프트 **방향 판정**과 CL 부정 게이트.

왜 테스트하나: 이 게이트가 조용히 뒤집히면(방향 오분류·필터 과잉) "✓ 미러 일치"가
거짓 안심이 된다 — 정확히 로드맵 §5.1 이 막으려던 사고를 게이트가 은폐하는 형태다.
p4/git 명령은 `run()` 을 갈아끼워 주입한다(실 depot 상태에 의존하면 머신마다 다른
거짓 실패).
"""
import importlib.util
import os

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


async def test_ci_generated_benchmarks_are_allowed_to_be_git_only():
    """bench 워크플로가 만들어 커밋하는 결과물은 depot 에 없는 게 맞다(실측 1,500여 개).
    안 빼면 존재 대조가 상시 빨개져 아무도 안 본다."""
    rc, text = _existence({
        "p4 files": (0, _files(("CLAUDE.md", "edit"))),
        "git ls-files": (0, "CLAUDE.md\ndocs/benchmark/darwin-arm64/x.json\n"),
    })
    assert rc == 0, text


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
