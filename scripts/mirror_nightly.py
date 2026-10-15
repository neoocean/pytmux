#!/usr/bin/env python3
"""야간 미러 캐치업 — p4 에만 게시된 것을 git 에 담아 민다 (pytmux-196·197).

빚이 쌓이는 입구는 하나다: git 클론이 없는 p4 전용 워크스페이스(office·alienware)에서
제출한 CL. 그 상자에서는 아무도 git 에 안 민다. 그래서 git 클론이 있는 상자(playground)의
야간 감시가 **QA 보다 먼저** 이 스크립트를 돌린다(사용자 결정 2026-10-15 — 무인 push 허용):

  ① `p4 sync`   — 그 워크스페이스에 **열린 파일이 없을 때만**. 남의 작업 중인 파일에
                  resolve 를 떠넘기지 않는다(열려 있으면 건너뛰고 그 사실을 남긴다).
  ② `git fetch` — 로컬 HEAD 가 origin/main 보다 **뒤처지기만** 했으면(로컬 커밋 0)
                  `git reset --mixed origin/main` 으로 HEAD 만 옮긴다(작업 트리 무접촉 —
                  `publish_check` 의 처방 그대로).
  ③ `publish_check.catch_up(push=True)` — 감시·게이트와 **같은 측정**으로 p4 쪽 빚만 담아 민다.

QA 보다 먼저 도는 이유: QA 의 미러 감시(`qa/repo.py`)가 «갚은 뒤»를 재야 한다. 뒤에 돌면
감시는 매일 그날의 빚을 보고 196·197 을 다시 연다.

종료 코드: 0 깨끗/갚음 · 1 갚지 못한 빚(git→p4 방향 등) · 2 못 쟀다(기준선·오프라인).
⛔ 이 스크립트는 p4 쪽(열린 파일·depot)을 바꾸지 않는다 — sync 외에는 읽기만 한다.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import publish_check  # noqa: E402


def sh(argv):
    p = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def step_sync(out=print):
    rc, opened = sh(["p4", "opened", "./..."])
    if rc and "not opened" not in opened:
        out(f"· p4 조회 실패 — sync 건너뜀: {opened.strip()[:160]}")
        return
    files = [ln for ln in opened.splitlines() if ln.strip() and "not opened" not in ln]
    if files:
        out(f"· 이 워크스페이스에 열린 파일 {len(files)}개 — sync 를 건너뛴다"
            "(남의 작업에 resolve 를 떠넘기지 않는다)")
        return
    rc, txt = sh(["p4", "sync", "-q", "./..."])
    tail = txt.strip().splitlines()[-1:] if txt.strip() else []
    out(f"{'✓' if rc == 0 else '✗'} p4 sync" + (f" — {tail[0][:160]}" if tail else ""))


def step_fast_forward(out=print):
    rc, _ = sh(["git", "fetch", "--no-tags", "--quiet", "origin"])
    if rc:
        out("· git fetch 실패 — catch_up 이 기준선을 다시 재고 판정한다")
        return
    rc1, behind = sh(["git", "rev-list", "--count", "HEAD..origin/main"])
    rc2, ahead = sh(["git", "rev-list", "--count", "origin/main..HEAD"])
    if rc1 or rc2:
        return
    behind, ahead = int(behind.strip() or 0), int(ahead.strip() or 0)
    if behind and not ahead:
        rc, txt = sh(["git", "reset", "--mixed", "--quiet", "origin/main"])
        out(f"{'✓' if rc == 0 else '✗'} HEAD 를 origin/main 으로({behind}커밋 뒤 · 로컬 커밋 0)")
    elif behind and ahead:
        out(f"✗ 로컬 커밋 {ahead}개가 origin/main({behind}커밋 앞)과 갈라졌다 — 사람이 본다")


def main():
    print("[mirror] 야간 미러 캐치업 —", ROOT)
    step_sync()
    step_fast_forward()
    rc = publish_check.catch_up(push=True)
    print(f"[mirror] rc={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
