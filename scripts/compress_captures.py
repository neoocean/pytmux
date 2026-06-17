#!/usr/bin/env python3
"""닫힌 캡처 로그(.log)를 gzip(.log.gz) 압축해 captures/ 용량을 줄인다(요청 2026-06-16).

raw VT 스트림은 반복 escape/재도색이 많아 ~20-73× 압축된다(실측). 여러 머신이 Perforce
로 captures/ 를 동기화하므로 코퍼스를 **버리지 않고** 압축 보관하는 게 목적이다.

동작(파일시스템만 — p4 작업은 호출자가 `p4 reconcile` 로 픽업):
  - captures/<머신>/*.log (sessions.log·이미 .gz 는 제외) 중 **최근 수정 안 된**(min-age
    초과) 파일만 gzip → 같은 이름 + .gz 로 만들고 원본 .log 를 지운다.
  - min-age 가드: 활성 캡처(현재 기록 중인 .log)를 건드리지 않게 한다(기본 900초).
  - 멱등: 이미 .gz 가 있으면 건너뛴다.

압축 후: `p4 reconcile captures/...` 가 .log 삭제 + .gz 추가를 연다 → 타입을
binary+F 로 두고 submit(captures 는 p4 전용 — git 미러 제외).

용법:
    python3 scripts/compress_captures.py --dry-run            # 미리보기
    python3 scripts/compress_captures.py                      # 실제 압축
    python3 scripts/compress_captures.py captures/woojinkim   # 특정 디렉토리만
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import subprocess
import sys
import time


def _open_capture_files(dirs: list[str]) -> set[str]:
    """현재 어떤 프로세스가 열고 있는 캡처 파일 절대경로 집합(lsof, best-effort).
    라이브 REC 서버가 잡고 있는 활성 .log 를 압축·삭제해 ghost inode 로 출력을 잃는
    것을 막는다. lsof 가 없거나 실패하면 빈 집합(→ mtime 가드에만 의존)."""
    out: set[str] = set()
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            r = subprocess.run(["lsof", "+D", d], capture_output=True,
                               text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return set()           # lsof 부재/오류 → mtime 가드로 폴백
        for ln in r.stdout.splitlines()[1:]:
            parts = ln.split()
            if parts and parts[-1].endswith(".log"):
                out.add(os.path.abspath(parts[-1]))
    return out


def eligible(path: str, min_age: float, now: float, open_files: set[str]) -> bool:
    name = os.path.basename(path)
    if not name.endswith(".log") or name == "sessions.log":
        return False
    if os.path.exists(path + ".gz"):
        return False                       # 이미 압축됨(멱등)
    if os.path.abspath(path) in open_files:
        return False                       # 라이브 서버가 열고 있음 → 절대 건드리지 않음
    try:
        if now - os.path.getmtime(path) < min_age:
            return False                   # 최근 수정 = 활성 캡처일 수 있음 → 보류
    except OSError:
        return False
    return True


def compress(path: str) -> tuple[int, int]:
    """path → path+'.gz' 압축, 원본 삭제. (원본크기, 압축크기) 반환."""
    raw = os.path.getsize(path)
    tmp = path + ".gz.tmp"
    with open(path, "rb") as fi, gzip.open(tmp, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=1024 * 1024)
    os.replace(tmp, path + ".gz")
    comp = os.path.getsize(path + ".gz")
    os.remove(path)
    return raw, comp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="캡처 디렉토리(기본: captures/*)")
    ap.add_argument("--min-age-sec", type=float, default=900.0,
                    help="이 초보다 최근 수정된 .log 는 건드리지 않음(활성 보호)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dirs = args.dirs
    if not dirs:
        base = "captures"
        dirs = [os.path.join(base, d) for d in sorted(os.listdir(base))
                if os.path.isdir(os.path.join(base, d))] if os.path.isdir(base) else []

    # Date.now 대용: 실제 벽시계(스크립트라 워크플로 제약과 무관).
    now = time.time()
    open_files = _open_capture_files(dirs)     # 라이브 서버가 잡은 파일 제외
    if open_files:
        print(f"# 열린 캡처 {len(open_files)}개 제외(라이브 서버)", file=sys.stderr)
    todo = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p) and eligible(p, args.min_age_sec, now, open_files):
                todo.append(p)

    raw_tot = comp_tot = 0
    for p in todo:
        if args.dry_run:
            raw_tot += os.path.getsize(p)
            print(f"[dry] would compress {p} ({os.path.getsize(p):,}B)")
            continue
        try:
            raw, comp = compress(p)
        except OSError as e:
            print(f"!! {p}: {e}", file=sys.stderr)
            continue
        raw_tot += raw
        comp_tot += comp
        print(f"  {p}  {raw:,} -> {comp:,}  ({raw / max(1, comp):.1f}x)")

    n = len(todo)
    if args.dry_run:
        print(f"\n[dry-run] {n} files, {raw_tot:,}B raw. "
              f"실제 실행하면 p4 reconcile 로 .log 삭제 + .gz 추가를 픽업하세요.")
    else:
        print(f"\n{n} files: {raw_tot:,}B -> {comp_tot:,}B "
              f"({raw_tot / max(1, comp_tot):.1f}x). 다음: "
              f"p4 reconcile captures/... ; .gz 를 binary+F 로 ; submit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
