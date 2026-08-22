"""시험 실행 전 프리플라이트(tests/workspace_guard.py) — pytmux/pytmux-227.

왜 시험하나: 이 가드가 조용히 못 잡으면(또는 반대로 정상 WIP 를 오탐하면) 그 자체로
"오라클이 있는데 아무도 안 불렀다"던 원래 사고의 재판이 된다. p4/git 명령은 주입한다
(실 depot 상태에 의존하면 머신마다 다른 거짓 실패/거짓 통과가 된다).
"""
import os
import tempfile

import workspace_guard

ROOT = "/repo"


class _FakePC:
    """`scripts/publish_check.py` 의 최소 대역 — `run`·`rel`·`rel_any`·`git_ignored`
    만 있으면 `find_suspects` 가 돈다(실제 모듈의 순수 함수는 그대로 재사용해도 되지만,
    `rel`/`rel_any` 는 실경로 정규화를 하므로 여기서는 이미 저장소-상대인 값을 그대로
    돌려주는 얇은 대역을 쓴다 — 입력을 그 형태로 맞춰 준다)."""

    def __init__(self, mapping, ignored=()):
        self._mapping = mapping
        self._ignored = set(ignored)

    def run(self, cmd, cwd=None):
        key = " ".join(cmd)
        for prefix, val in self._mapping.items():
            if key.startswith(prefix):
                return val
        return 0, ""

    @staticmethod
    def rel(path):
        return path.strip()

    @staticmethod
    def rel_any(path):
        """실제 `rel_any`/`rel_depot`처럼 `//…/pytmux/` 표식 뒤만 남긴다 — depot 경로
        (`p4 opened`)와 이미-상대 경로(`p4 diff -se`)를 같은 키로 맞춰야 opened 집합이
        depot_diff 집합과 실제로 교집합한다."""
        p = path.strip()
        marker = "/pytmux/"
        i = p.find(marker)
        return p[i + len(marker):] if i >= 0 else p

    def git_ignored(self, paths):
        return {p for p in paths if p in self._ignored}


def test_workspace_matching_old_git_head_but_differing_from_depot_is_flagged():
    """pytmux-227 의 정확한 신호: 안 열렸고, depot 과 다르고, git 은 clean."""
    pc = _FakePC({
        "p4 info": (0, "..."),
        "p4 opened ./...": (0, "...  - file(s) not opened on this client."),
        "p4 diff -se ./...": (0, "pytmuxlib/clientscreens.py\n"),
        "git status --porcelain": (0, ""),   # clean = git HEAD 와 바이트 동일
    })
    suspects = workspace_guard.find_suspects(ROOT, pc=pc)
    assert suspects == ["pytmuxlib/clientscreens.py"]


def test_legit_local_wip_edit_is_not_flagged():
    """사람이 지금 고치는 중(git 도 dirty)이면 이 가드가 아니라 정상 WIP 다."""
    pc = _FakePC({
        "p4 info": (0, "..."),
        "p4 opened ./...": (0, "...  - file(s) not opened on this client."),
        "p4 diff -se ./...": (0, "pytmuxlib/clientscreens.py\n"),
        "git status --porcelain": (0, " M pytmuxlib/clientscreens.py\n"),
    })
    assert workspace_guard.find_suspects(ROOT, pc=pc) is None


def test_opened_file_is_not_flagged():
    """`p4 edit` 로 이미 연 파일은 의도된 작업 중이다 — 드리프트가 아니다."""
    pc = _FakePC({
        "p4 info": (0, "..."),
        "p4 opened ./...": (0, "//depot/pytmux/pytmuxlib/clientscreens.py#5 - edit\n"),
        "p4 diff -se ./...": (0, "pytmuxlib/clientscreens.py\n"),
        "git status --porcelain": (0, ""),
    })
    assert workspace_guard.find_suspects(ROOT, pc=pc) is None


def test_gitignored_auto_mirror_drift_is_not_flagged():
    """docs/internal/qa/issues/*.md 류 — gitignore 라 'git HEAD 와 같다'는 판정 대상이
    아니다(정상적으로 depot 보다 앞서거나 뒤설 수 있다)."""
    path = "docs/internal/qa/issues/pytmux-268.md"
    pc = _FakePC({
        "p4 info": (0, "..."),
        "p4 opened ./...": (0, "...  - file(s) not opened on this client."),
        "p4 diff -se ./...": (0, f"{path}\n"),
        "git status --porcelain": (0, ""),
    }, ignored=(path,))
    assert workspace_guard.find_suspects(ROOT, pc=pc) is None


def test_clean_workspace_returns_none():
    pc = _FakePC({
        "p4 info": (0, "..."),
        "p4 opened ./...": (0, "...  - file(s) not opened on this client."),
        "p4 diff -se ./...": (0, ""),
        "git status --porcelain": (0, ""),
    })
    assert workspace_guard.find_suspects(ROOT, pc=pc) is None


def test_no_git_dir_short_circuits_without_loading_publish_check():
    """git 클론이 아닌 순수 p4 워크스페이스 — 잴 것이 없다(존재하지도 않는 `.git`).

    ⚠ `pc` 를 주입하지 않는다 — 주입은 "p4+git 동거를 이미 전제"하는 시험 경로라
    `.git` 실사조건을 건너뛴다(그래야 실 파일시스템에 없는 가짜 root 로도 로직을 시험할
    수 있다). 이 시험은 정확히 그 실사조건 자체를 잰다."""
    d = tempfile.mkdtemp(prefix="pytmux-test-wsguard-")
    assert workspace_guard.find_suspects(d) is None


def test_env_opt_out_short_circuits():
    old = os.environ.get("PYTMUX_SKIP_WORKSPACE_GUARD")
    os.environ["PYTMUX_SKIP_WORKSPACE_GUARD"] = "1"
    try:
        pc = _FakePC({"p4 diff -se ./...": (0, "pytmuxlib/clientscreens.py\n")})
        assert workspace_guard.find_suspects(ROOT, pc=pc) is None
    finally:
        if old is None:
            del os.environ["PYTMUX_SKIP_WORKSPACE_GUARD"]
        else:
            os.environ["PYTMUX_SKIP_WORKSPACE_GUARD"] = old


def test_pc_load_failure_degrades_to_none_not_a_crash():
    """가드 자신의 버그(또는 p4 부재)가 전체 스위트의 새 단일 장애점이 되면 안 된다."""
    class _Broken(_FakePC):
        def run(self, cmd, cwd=None):
            raise RuntimeError("p4 가 갑자기 죽었다고 치자")

    assert workspace_guard.find_suspects(ROOT, pc=_Broken({})) is None
