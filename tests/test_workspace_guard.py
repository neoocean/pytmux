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


def test_a_guard_that_could_not_measure_says_so():
    """☠ 검수 2026-09-05 C-7 — **못 잰 것을 조용히 지나지 않는다.**

    종전에는 `p4 info` 실패(Docker 가 내려간 날)·p4 부재·예외가 전부 그냥 `None` 이었고,
    `run.py` 는 아무 말 없이 지나갔다 — 그날 depot 드리프트 가드는 한 줄도 안 남기고
    빠졌는데 스위트는 종전과 똑같이 초록이었다. 「가드가 봤고 괜찮더라」와 「가드가
    아예 안 돌았다」가 화면에서 같아 보이면 그것은 가드가 아니다."""
    # ⓐ 실제로 쟀고 깨끗하다 — 그때는 남길 이유가 없다.
    clean = _FakePC({
        "p4 info": (0, "..."),
        "p4 opened ./...": (0, "...  - file(s) not opened on this client."),
        "p4 diff -se ./...": (0, ""),
        "git status --porcelain": (0, ""),
    })
    assert workspace_guard.find_suspects(ROOT, pc=clean) is None
    assert workspace_guard.LAST_SKIP is None, workspace_guard.LAST_SKIP

    # ⓑ p4 서버에 못 닿았다(오프라인·Docker 다운).
    offline = _FakePC({"p4 info": (1, "Connect to server failed")})
    assert workspace_guard.find_suspects(ROOT, pc=offline) is None
    assert workspace_guard.LAST_SKIP and "p4 서버" in workspace_guard.LAST_SKIP, \
        workspace_guard.LAST_SKIP

    # ⓒ 가드 자신이 터졌다 — 그것도 「못 쟀다」다.
    class _Broken(_FakePC):
        def run(self, cmd, cwd=None):
            raise RuntimeError("p4 가 갑자기 죽었다고 치자")

    assert workspace_guard.find_suspects(ROOT, pc=_Broken({})) is None
    assert workspace_guard.LAST_SKIP and "예외" in workspace_guard.LAST_SKIP, \
        workspace_guard.LAST_SKIP

    # ⓓ 그리고 **부르는 쪽이 그 줄을 찍는가**(호출부 오라클 — 전역만 재면 그 줄을
    #    지워도 통과한다).
    import ast
    import os as _os
    src = open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "run.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    reads = [n for n in ast.walk(tree)
             if isinstance(n, ast.Attribute) and n.attr == "LAST_SKIP"
             and isinstance(n.value, ast.Name) and n.value.id == "workspace_guard"]
    assert reads, "run.py 가 「못 쟀다」를 안 읽는다 — 이유가 어디에도 안 남는다"


def test_pc_load_failure_degrades_to_none_not_a_crash():
    """가드 자신의 버그(또는 p4 부재)가 전체 스위트의 새 단일 장애점이 되면 안 된다."""
    class _Broken(_FakePC):
        def run(self, cmd, cwd=None):
            raise RuntimeError("p4 가 갑자기 죽었다고 치자")

    assert workspace_guard.find_suspects(ROOT, pc=_Broken({})) is None
