#!/usr/bin/env python3
"""레거시 토큰 로그(`*.tokens.jsonl`)를 SQLite(`db/claude-tokens.db`)로 임포트.

배경: 토큰 사용량 저장을 JSONL → SQLite 로 이행했다(docs/TOKEN_USAGE_STORAGE_DESIGN.md,
2026-06-07). 서버는 새 DB 최초 사용 시 옛 JSONL 을 **자동 일회 임포트**하지만, 이
스크립트는 그 과정을 **수동/명시적으로** 돌리거나(서버 미기동 환경) 여러 로그를 한
DB 로 합칠 때 쓴다.

동작: 주어진 JSONL 들의 레코드를 DB 에 append(insert_many). DB 가 없으면 생성한다.
**멱등이 아니다** — 같은 JSONL 을 두 번 임포트하면 레코드가 중복된다. 그래서 기본은
빈(또는 신규) DB 를 전제하고, 비어있지 않으면 `--force` 없이는 거부한다.

사용 예:
    # 상태 디렉터리의 모든 tokens.jsonl 을 기본 DB 로(미리보기)
    python3 scripts/import_token_jsonl.py
    # 실제 임포트(경로 명시)
    python3 scripts/import_token_jsonl.py --apply \
        /tmp/pytmux-501/default.sock.tokens.jsonl
    # 대상 DB 명시
    python3 scripts/import_token_jsonl.py --apply --db db/claude-tokens.db <jsonl...>
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib import ipc, usagedb, usagelog  # noqa: E402

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(PROJECT_DIR, "db", "claude-tokens.db")


def _discover_logs():
    """상태 디렉터리에서 *.tokens.jsonl 을 찾는다(경로 미지정 시)."""
    sd = ipc.default_state_dir()
    return sorted(set(glob.glob(os.path.join(sd, "*.tokens.jsonl"))))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*",
                    help="임포트할 JSONL 경로(미지정 시 상태 디렉터리 검색)")
    ap.add_argument("--db", default=DEFAULT_DB,
                    help=f"대상 SQLite DB(기본 {DEFAULT_DB})")
    ap.add_argument("--apply", action="store_true",
                    help="실제 임포트(미지정 시 미리보기)")
    ap.add_argument("--force", action="store_true",
                    help="DB 가 비어있지 않아도 진행(중복 위험 — 직접 확인 시)")
    args = ap.parse_args(argv)

    paths = args.paths or _discover_logs()
    if not paths:
        print("임포트할 JSONL 을 찾지 못했습니다(경로를 지정하세요).")
        return 1

    total = 0
    per = []
    for p in paths:
        recs = usagelog.read(p)
        per.append((p, len(recs), sum(int(r.get("tokens", 0)) for r in recs)))
        total += len(recs)

    print(f"대상 DB: {args.db}")
    print(f"모드: {'적용' if args.apply else '미리보기'}")
    for p, n, tok in per:
        print(f"  {p} — 레코드 {n} · 토큰 {tok}")
    print(f"합계 레코드 {total}")

    if not args.apply:
        print("미리보기입니다. 실제로 넣으려면 --apply 를 붙이세요.")
        return 0

    conn = usagedb.connect(args.db)
    existing = usagedb.count(conn)
    if existing and not args.force:
        print(f"거부: DB 에 이미 {existing} 건이 있습니다(중복 우려). "
              "정말 추가하려면 --force.")
        conn.close()
        return 2
    imported = 0
    for p in paths:
        imported += usagedb.import_jsonl(conn, p)
    print(f"→ {imported} 건 임포트 완료(DB 총 {usagedb.count(conn)} 건).")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
