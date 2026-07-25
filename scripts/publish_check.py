#!/usr/bin/env python3
"""게시 게이트 — p4↔git 미러 드리프트 + CL 오염(남의 파일) 점검.

이 저장소는 **p4 submit 과 git push 양쪽**에 게시한다(CLAUDE.md '게시'). 자동 미러가
없어 한쪽만 하면 다른 쪽이 영영 뒤처진다 — 실사고 3건(p4 63471·63714 캐치업, 그리고
이 스크립트를 만들며 발견한 `.gitignore` 의 `.env` 규칙이 git 에만 있던 것). 로드맵
§5.1 의 처방 (a)"체크리스트+`git ls-remote` 대조"를 사람이 기억하지 않아도 되게
**실행 가능한 게이트**로 만든 것이 이 파일이다.

    python3 scripts/publish_check.py            # 미러 드리프트 점검
    python3 scripts/publish_check.py --cl 66940 # 그 CL 이 내 파일만 담았는지(부정 게이트)

드리프트 판정 원리(값싼 명령 두 개로 양방향):
  · `p4 diff -se` = 열지 않았는데 depot 과 **내용이 다른** 파일.
  · `git status --porcelain` = git HEAD 와 다른 파일(ignore 된 것은 애초에 안 나온다).
  두 집합의 조합이 방향을 말해준다:
    depot≠workspace 인데 git 은 clean  → git 에만 있는 내용 = **p4 미제출**
    depot==workspace 인데 git 은 modified → p4 에만 있는 내용 = **git 미푸시**
    양쪽 다 다름 → 그냥 작업 중(WIP, 게시 전)이라 드리프트 아님
종료코드 = 0(깨끗)/1(드리프트 또는 오염). 게시 직전 훅으로 쓸 수 있다.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, cwd=ROOT):
    """(rc, stdout) — 실패해도 예외 대신 rc 로 돌려준다(도구 부재 진단용)."""
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, f"{e}"
    return p.returncode, p.stdout


def rel(path):
    """p4 의 절대 로컬 경로를 저장소 상대 경로로(비교 키를 git 쪽과 일치시킨다)."""
    path = path.strip()
    if not path:
        return ""
    try:
        r = os.path.relpath(os.path.realpath(path), os.path.realpath(ROOT))
    except ValueError:
        return ""
    return "" if r.startswith("..") else r.replace(os.sep, "/")


def git_ignored(paths):
    """gitignore 된 경로 집합. p4 전용 파일(docs/internal·captures·db)이 '미푸시'로
    오검출되는 것을 막는 필수 필터다."""
    if not paths:
        return set()
    p = subprocess.run(["git", "check-ignore", "--stdin"], cwd=ROOT,
                       input="\n".join(paths), capture_output=True, text=True)
    return {ln.strip() for ln in p.stdout.splitlines() if ln.strip()}


def check_cl(cl, out=print):
    """부정 게이트: CL 에 이 프로젝트 밖 파일(= 병렬 세션의 WIP)이 실렸는지.

    `p4 change -o | p4 change -i` 는 default CL 에 열린 **모든** 파일을 새 CL 스펙에
    싣기 때문에, 확인 없이 submit 하면 남의 작업을 대신 올린다(실제 재발 — CLAUDE.md).
    """
    rc, txt = run(["p4", "opened", "-c", str(cl)])
    if rc:
        out(f"✗ p4 opened -c {cl} 실패: {txt.strip()[:200]}")
        return 1
    files = [ln.split("#")[0] for ln in txt.splitlines() if ln.strip()]
    foreign = [f for f in files if "/pytmux/" not in f]
    out(f"CL {cl}: 파일 {len(files)}개")
    for f in foreign:
        out(f"  ✗ 남의 파일: {f}")
    if foreign:
        out(f"  → 되돌리기: p4 opened -c {cl} | sed 's/#.*//' | "
            f"grep -v \"/pytmux/\" | xargs p4 reopen -c default")
        return 1
    out("  ✓ 내 파일만" if files else "  (빈 CL)")
    return 0


def check_mirror(out=print, remote=True):
    problems = 0

    # ── git 쪽: 푸시 안 된 커밋 / 원격이 앞선 상태 ──────────────────────────
    rc, head = run(["git", "rev-parse", "HEAD"])
    if rc:
        out(f"✗ git 저장소가 아니다: {head.strip()[:120]}")
        return 1
    head = head.strip()
    rc, unpushed = run(["git", "log", "--oneline", "origin/main..HEAD"])
    unpushed = [ln for ln in unpushed.splitlines() if ln.strip()] if not rc else []
    if unpushed:
        problems += 1
        out(f"✗ git 미푸시 커밋 {len(unpushed)}개 (p4 만 게시된 상태일 수 있다):")
        for ln in unpushed[:10]:
            out(f"    {ln}")
        out("  → git push origin main")
    if remote:
        rc, ls = run(["git", "ls-remote", "origin", "refs/heads/main"])
        remote_sha = ls.split()[0] if (not rc and ls.split()) else ""
        if remote_sha:
            rc2, _ = run(["git", "merge-base", "--is-ancestor", remote_sha, head])
            if rc2 == 1:
                out(f"· 원격 main({remote_sha[:8]})이 로컬 HEAD 조상이 아니다 — "
                    "CI(bench 동기) 커밋이 앞서 있을 수 있다: git pull --rebase "
                    "--autostash 후 다시 확인")
        else:
            out("· 원격 조회 실패(오프라인?) — 커밋 대조는 생략")

    # ── 양방향 드리프트 ────────────────────────────────────────────────────
    rc, opened_txt = run(["p4", "opened", "./..."])
    if rc and "not opened" not in opened_txt:
        out(f"· p4 조회 실패 — 미러 대조 생략: {opened_txt.strip()[:120]}")
        return 1 if problems else 0
    opened = {rel(ln.split("#")[0]) for ln in opened_txt.splitlines()
              if ln.strip() and "/pytmux/" in ln}
    _, se_txt = run(["p4", "diff", "-se", "./..."])
    depot_diff = {r for r in (rel(ln) for ln in se_txt.splitlines()) if r}
    _, st_txt = run(["git", "status", "--porcelain"])
    git_dirty, git_untracked = set(), set()
    for ln in st_txt.splitlines():
        if not ln.strip():
            continue
        code, path = ln[:2], ln[3:].strip().strip('"')
        (git_untracked if code.strip() == "??" else git_dirty).add(path)

    ignored = git_ignored(sorted(depot_diff))
    # ① depot 과 다른데 git 은 clean → git 에만 있는 내용(p4 미제출)
    p4_missing = sorted(depot_diff - ignored - opened - git_dirty - git_untracked)
    # ② depot 과 같은데 git 은 modified → p4 에만 있는 내용(git 미푸시)
    git_missing = sorted(git_dirty - depot_diff - opened)
    if p4_missing:
        problems += 1
        out(f"✗ p4 미제출 {len(p4_missing)}개 — git 에는 있고 depot 에는 없는 내용:")
        for f in p4_missing[:20]:
            out(f"    {f}")
        out("  → p4 edit <파일> 후 번호 CL 로 submit(캐치업)")
    if git_missing:
        problems += 1
        out(f"✗ git 미푸시 {len(git_missing)}개 — depot 과 같은데 git HEAD 와 다른 내용:")
        for f in git_missing[:20]:
            out(f"    {f}")
        out("  → git add/commit + push(p4 만 게시된 상태)")
    # 양쪽 다 미게시(수정 중이거나 아직 아무 쪽에도 없는 신규 파일) = 드리프트 아님.
    wip = sorted(((git_dirty | git_untracked) & (depot_diff | opened))
                 | (git_untracked - depot_diff - opened))
    if wip:
        out(f"· 작업 중 {len(wip)}개(양쪽 미게시 — 드리프트 아님): "
            f"{', '.join(wip[:6])}{' …' if len(wip) > 6 else ''}")
    if not problems:
        out("✓ p4↔git 미러 일치(미푸시 커밋 없음, 양방향 내용 드리프트 없음)")
    return 1 if problems else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="p4↔git 게시 게이트")
    ap.add_argument("--cl", help="이 CL 이 내 파일만 담았는지 검사(부정 게이트)")
    ap.add_argument("--no-remote", action="store_true",
                    help="git ls-remote 대조 생략(오프라인)")
    a = ap.parse_args(argv)
    if a.cl:
        return check_cl(a.cl)
    return check_mirror(remote=not a.no_remote)


if __name__ == "__main__":
    sys.exit(main())
