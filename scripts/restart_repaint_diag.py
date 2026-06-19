#!/usr/bin/env python3
"""재시작 복원 진단 — `<state>.restartdbg.jsonl` 을 읽어 SIGWINCH **직전**(restored)과
**직후**(post_repaint) 프레임을 패널별로 짝지어 비교한다(RESTART_SCENARIO.md §주의①).

서버(serverpersist._write_restore_manifest)가 재시작 복원 때 두 phase 를 찍는다:
- restored: 스냅샷을 새 pyte 에 복원한 직후(SIGWINCH 전).
- post_repaint: 0.6초 뒤(살아 있는 앱의 SIGWINCH repaint 가 가라앉은 뒤).

판정:
- 어떤 패널의 nonblank_rows 가 restored 18 → post 1 처럼 **줄면** = 그 앱의 부분 repaint 가
  복원된 화면을 지운 것(**clobber, 시나리오 A**). 재시작 후 빈 패널 증상의 직접 증거 —
  top/bottom 스니펫이 사라진 내용을 보여 준다. 고칠 지점 = 스냅샷↔앱 모델 충실도/SIGWINCH.
- restored 부터 이미 nonblank_rows≈0 = 스냅샷 자체가 빔(**시나리오 B**). 고칠 지점 = export
  (_export_viewport/cursor) — 앱 프레임이 애초에 안 담겼다.
- 변화 없음 = 그 패널은 정상 복원(보통 busy 패널은 새 출력으로 덮여 정상).

사용법:
    python scripts/restart_repaint_diag.py [<state>.restartdbg.jsonl]
인자 생략 시 기본 상태 디렉토리에서 자동 탐색한다.
"""
from __future__ import annotations

import glob
import json
import os
import sys


def _find_default() -> str | None:
    """기본 상태 디렉토리에서 가장 최근 *.restartdbg.jsonl 을 찾는다."""
    cands = []
    for base in (os.environ.get("PYTMUX_HOME"),
                 os.path.expanduser("~/.pytmux/state"),
                 os.environ.get("XDG_RUNTIME_DIR"), "/tmp"):
        if not base:
            continue
        cands += glob.glob(os.path.join(base, "**", "*.restartdbg.jsonl"),
                           recursive=True)
        cands += glob.glob(os.path.join(base, "*.restartdbg.jsonl"))
    cands = [c for c in set(cands) if os.path.isfile(c)]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def _last_two_phases(lines: list[dict]):
    """가장 최근 restored 와 그 뒤 post_repaint 한 쌍을 돌려준다(없으면 None)."""
    restored = post = None
    for rec in lines:
        if rec.get("phase") == "restored":
            restored, post = rec, None        # 새 복원 사이클 시작
        elif rec.get("phase") == "post_repaint":
            post = rec
    return restored, post


def main(argv: list[str]) -> int:
    path = argv[0] if argv else _find_default()
    if not path or not os.path.isfile(path):
        print("restartdbg.jsonl 을 못 찾음 — 경로를 인자로 주거나 재시작을 한 번 하세요.",
              file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        recs = [json.loads(ln) for ln in f if ln.strip()]
    restored, post = _last_two_phases(recs)
    if restored is None:
        print("restored phase 가 없음.", file=sys.stderr)
        return 2
    print(f"# {path}")
    print(f"# restored ts={restored.get('ts'):.3f}"
          + (f"  post_repaint ts={post.get('ts'):.3f}" if post else
             "  post_repaint=(없음 — 0.6초 내 종료?)"))
    post_by_id = {p["pane"]: p for p in (post.get("panes", []) if post else [])}
    print(f"{'pane':>5} {'alt':>3} {'cur(x,y)':>10} "
          f"{'nb_restored':>11} {'nb_post':>8} {'verdict':>9}  bottom-snippet")
    clobbered = []
    for pr in restored.get("panes", []):
        pid = pr["pane"]
        pp = post_by_id.get(pid)
        nb_r = pr["nonblank_rows"]
        nb_p = pp["nonblank_rows"] if pp else None
        cur = pr.get("cursor") or {}
        curs = f"({cur.get('x')},{cur.get('y')})"
        if nb_p is None:
            verdict = "?"
        elif nb_r >= 3 and nb_p <= 1:
            verdict = "CLOBBER"           # 시나리오 A — 내용 있었는데 비워짐
            clobbered.append(pid)
        elif nb_r <= 1:
            verdict = "EMPTY-SNAP"        # 시나리오 B — 스냅샷부터 빔
        elif nb_p < nb_r:
            verdict = "shrunk"            # 부분 손실
        else:
            verdict = "ok"
        snippet = (pp or pr).get("bottom", "")
        print(f"{pid:>5} {str(pr['alt']):>3} {curs:>10} "
              f"{nb_r:>11} {str(nb_p):>8} {verdict:>9}  {snippet}")
    print()
    if clobbered:
        print(f"⇒ CLOBBER 패널 {clobbered}: 복원 스냅샷에 내용이 있었으나 SIGWINCH "
              f"repaint 가 지움(시나리오 A). top/bottom 으로 잃은 내용 확인, 다음 단계="
              f"그 패널 REC 캡처의 재시작 직후 바이트로 앱이 emit 한 clear/커서이동 분석.")
    else:
        print("⇒ CLOBBER 없음 — 빈 패널이 있었다면 EMPTY-SNAP(스냅샷 자체가 빔, 시나리오 B) "
              "행을 보라(고칠 지점이 export 로 바뀜).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
