"""실행 전 프리플라이트 — 워크스페이스 바이트가 depot 과 몰래 어긋나 있지 않은가
(pytmux/pytmux-227).

git 미러가 p4 워크스페이스와 같은 자리에 있는 개발 상자에서, 워크스페이스 바이트가
git 미러 판으로 조용히 되감긴 사고가 실제로 있었다(84파일, 2026-08-16 실측). 그 사고의
핵심은 `p4 sync -n`이 "up-to-date"라 답했다는 것 — have 리비전만 보고 바이트는 안 본다.
그 트리 위에서 시험이 닷새간 초록으로 돌았다 — depot 이 아닌 코드를 재는 줄도 모르고.

`scripts/publish_check.py`가 같은 드리프트를 `p4 diff -se`(열지 않았는데 depot 과 내용이
다른 파일 — have/head 리비전과 무관하게 바이트로 잰다)로 재지만, pre-push 훅과
`check_all.py`에서만 불린다. **시험 단독 실행**(`python3 tests/run.py [모듈]` — 트래커
유입 경로 `tracker_tests.py --ingest`가 실제로 쓰는 길)에서는 한 번도 안 불렸다. 이
모듈은 그 구멍을 메운다: 시험을 돌리기 **전에** 재고, 어긋나 있으면 시험을 아예 안
돌린다.

⚠ **이 모듈 자신은 무거운 것을 물지 않는다**(os·shutil·importlib.util 뿐) — `run.py`
는 `pytmuxlib` 을 물기 **전에** 이 모듈을 불러야 하고, 여기서 매달리면 스위트가 첫
출력도 없이 멈춘다.
"""
import os

#: 직전 [`find_suspects`] 가 **못 잰** 이유(실제로 쟀으면 `None`).
#:
#: ☠ 조용한 SKIP 은 「다 봤다」로 읽힌다(검수 2026-09-05 C-7). 종전에는 `p4 info` 가
#: 실패하면(Docker 가 내려간 날처럼) 그냥 `None` 을 돌려 `run.py` 가 아무 말 없이 지나
#: 갔다 — 그날 depot 드리프트 가드는 **한 줄도 안 남기고 빠졌고**, 스위트는 종전과
#: 똑같이 초록이었다. 이제 이유를 여기 남기고 부르는 쪽이 한 줄 찍는다.
LAST_SKIP = None


def _skip(why):
    """못 쟀다 — 이유를 남기고 `None`."""
    global LAST_SKIP
    LAST_SKIP = why
    return None


def _load_publish_check(root):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "publish_check_wsguard", os.path.join(root, "scripts", "publish_check.py"))
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    return pc


def find_suspects(root, pc=None):
    """`root`(저장소 루트)가 p4+git 동거 워크스페이스인데 git-추적 파일 중 일부가
    **git HEAD 와는 바이트가 같으면서 depot 과는 다르면** 그 경로 목록을 돌려준다.
    그 조합은 정직한 설명이 하나뿐이다 — p4 관점에서 한 번도 열린 적 없는 파일이
    depot 과 달라졌다는 것은, 바이트가 p4 를 거치지 않고 **밖에서**(예: git checkout·
    미러 되감김) 바뀌었다는 뜻이다.

    `pc`: 시험 주입용(`scripts/publish_check.py`를 이미 로드해 `run`을 갈아끼운 모듈).
    생략하면 실제로 로드하고, 그 전에 `p4` 이진 유무를 먼저 본다.

    문제 없음(또는 잴 수 없음)이면 `None`. 그 둘은 다르므로, **못 잰 것**이면 그 이유를
    모듈 전역 [`LAST_SKIP`] 에 남긴다(잰 경우 `None` 으로 지운다) — 부르는 쪽이 한 줄
    찍게(검수 2026-09-05 C-7). **어떤 예외도 삼키고 `None`을 돌려준다** —
    이 결함은 이 특정 배치(p4 워크스페이스에 git 미러가 같은 자리)에서만 성립하고,
    p4 이진이 없는 순수 git 클론(대부분의 CI)에서는 애초에 잴 것이 없다. 그리고 이
    가드 자신의 버그가 전체 스위트의 새 단일 장애점이 되면 안 된다 — 이 저장소가
    이미 아는 원칙("거짓 붉음은 거짓 초록의 다른 얼굴")과 같은 결이다.
    """
    global LAST_SKIP
    LAST_SKIP = None
    if os.environ.get("PYTMUX_SKIP_WORKSPACE_GUARD"):
        return _skip("PYTMUX_SKIP_WORKSPACE_GUARD 로 껐다")
    try:
        if pc is None:
            # 실경로 사전조건 — 시험이 `pc` 를 직접 주입할 때는 "p4+git 동거냐"를
            # 이미 전제하고 부르므로 건너뛴다(실 파일시스템에 없는 가짜 root 로도 시험할 수 있게).
            if not os.path.isdir(os.path.join(root, ".git")):
                return _skip("git 클론이 아니다 — 잴 것이 없다")
            import shutil
            if not shutil.which("p4"):
                return _skip("p4 이진이 없다 — 잴 것이 없다")
            pc = _load_publish_check(root)

        rc, _ = pc.run(["p4", "info"])
        if rc:
            # 오프라인/미로그인. ⛔ 「p4 가 없는 것과 같은 취급」이되 **조용하지는
            # 않다** — 이 상자에서 p4 는 Docker 위에 있고, 그것이 내려간 날 이 가드가
            # 한 줄도 안 남기고 빠졌다(검수 2026-09-05 C-7).
            return _skip("p4 서버에 못 닿았다(오프라인·미로그인·Docker 다운)")

        rc, opened_txt = pc.run(["p4", "opened", "./..."])
        if rc and "not opened" not in opened_txt:
            return _skip("`p4 opened` 가 실패했다")
        opened = {r for r in (pc.rel_any(ln.split("#")[0])
                              for ln in opened_txt.splitlines()
                              if ln.strip() and "/pytmux/" in ln.replace("\\", "/"))
                  if r}

        _, se_txt = pc.run(["p4", "diff", "-se", "./..."])
        depot_diff = {r for r in (pc.rel(ln) for ln in se_txt.splitlines()) if r}
        if not depot_diff:
            return None

        ignored = pc.git_ignored(sorted(depot_diff))
        # git 추적 밖(gitignore 된 자동 미러·캡처 로그 등)은 애초에 "git HEAD 와 같다"는
        # 판정 대상이 아니다 — 정상적으로 depot 보다 앞서거나 뒤설 수 있다.
        tracked_diff = sorted(depot_diff - ignored - opened)
        if not tracked_diff:
            return None

        _, st_txt = pc.run(["git", "status", "--porcelain"])
        git_dirty = set()
        for ln in st_txt.splitlines():
            if ln.strip():
                git_dirty.add(ln[3:].strip().strip('"'))
        # git 도 dirty 한 파일은 사람이 지금 고치는 중인 WIP 다 — 이 가드가 잡을 것은
        # "git 은 clean(=HEAD 와 바이트 동일)한데 depot 과는 다르다"는 조용한 어긋남뿐.
        suspects = [f for f in tracked_diff if f not in git_dirty]
        return suspects or None
    except Exception as exc:
        return _skip(f"가드가 예외로 멈췄다: {exc!r}")
