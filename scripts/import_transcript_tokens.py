#!/usr/bin/env python3
"""Claude Code 트랜스크립트(`~/.claude/projects/*.jsonl`)의 토큰 사용량을 usagedb 의
usage_xc(권위 회계, v7)로 백필 — §10-D 과소집계 복원.

배경(docs/internal/TOKEN_UNDERCOUNT_TRANSCRIPT_SOLUTION.md): pytmux 의 스크랩 누계는
푸터 ↑/↓(비캐시 input+output 근사)라 cache_read/creation 을 빠뜨려 실제의 0.4% 만
집계한다. 트랜스크립트엔 4항목이 모두 있으므로, 그 권위 출처를 읽어 usage_xc 로
적재하면 **가능한 전체 기간**의 정확 토큰 이력이 복원된다.

멱등(usage_xc PK=xkey, INSERT OR IGNORE) — 같은 파일을 여러 번 임포트해도 중복되지
않는다. 각 파일을 끝까지 읽은 뒤 테일 커서를 EOF 로 세팅해, 라이브 증분 적재(P4)가
백필 구간을 다시 읽지 않게 한다.

`scripts/import_token_jsonl.py`(레거시 스크랩 `*.tokens.jsonl` → usage 테이블)와는
**대상이 다르다**(이쪽은 Claude 트랜스크립트 → usage_xc).

사용 예:
    # 전체 프로젝트 미리보기(기본 dry-run)
    python3 scripts/import_transcript_tokens.py --all
    # 실제 적재
    python3 scripts/import_transcript_tokens.py --all --apply
    # 특정 프로젝트만 / DB 명시
    python3 scripts/import_transcript_tokens.py --project ~/p4/.../pytmux --apply
    python3 scripts/import_transcript_tokens.py --all --apply --db /path/claude-tokens.db
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import importlib  # noqa: E402

# claude-code 는 하이픈 디렉터리라 import 문법으론 못 부른다 — run.py 와 동일하게
# importlib 로 로드(플러그인 물리 이전, S5 T5).
transcript = importlib.import_module("pytmuxlib.plugins.claude-code.transcript")
usagedb = importlib.import_module("pytmuxlib.plugins.claude-code.usagedb")


def _resolve_db_path() -> str:
    """서버와 동일 규칙으로 기본 DB 경로 해석(PYTMUX_TOKENS_DB > PYTMUX_HOME/db > 플러그인 db/)."""
    override = os.environ.get("PYTMUX_TOKENS_DB")
    if override:
        return override
    from pytmuxlib import ipc
    home = ipc.pytmux_home()
    plugin_db = os.path.join(
        os.path.dirname(transcript.__file__), "db", "claude-tokens.db")
    if home:
        return os.path.join(home, "db", "claude-tokens.db")
    return plugin_db


def discover(args) -> list[str]:
    if args.files:
        return [os.path.abspath(os.path.expanduser(f)) for f in args.files]
    root = transcript.projects_dir()
    if args.all:
        return sorted(glob.glob(os.path.join(root, "*", "*.jsonl")))
    target = args.project or os.getcwd()
    d = transcript.project_dir_for(target, root)
    return sorted(glob.glob(os.path.join(d, "*.jsonl")))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="JSONL 직접 지정(생략 시 자동 탐색)")
    ap.add_argument("--all", action="store_true", help="모든 프로젝트")
    ap.add_argument("--project", help="프로젝트 경로(기본 cwd)")
    ap.add_argument("--db", help="대상 DB(기본: 서버 규칙 해석)")
    ap.add_argument("--apply", action="store_true",
                    help="실제 적재(생략 시 dry-run 미리보기)")
    args = ap.parse_args()

    files = discover(args)
    if not files:
        sys.stderr.write("[warn] 대상 트랜스크립트 없음 (--all 또는 --project 확인)\n")
        return 1
    db_path = args.db or _resolve_db_path()

    conn = usagedb.connect(db_path if args.apply else ":memory:")
    before = usagedb.xc_count(conn)
    new_rows = 0
    parsed = 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as fh:
                recs = [rec for _k, rec in transcript.iter_records(fh)]
        except OSError:
            continue
        parsed += len(recs)
        n = usagedb.insert_xc_many(conn, recs)
        new_rows += n
        if args.apply:
            try:
                usagedb.set_xc_cursor(conn, fp, os.path.getsize(fp),
                                      os.path.getmtime(fp))
            except OSError:
                pass

    tot = usagedb.xc_totals(conn)
    after = usagedb.xc_count(conn)
    mode = "APPLY" if args.apply else "DRY-RUN(메모리)"
    print("=" * 60)
    print(f"트랜스크립트 토큰 백필 ({mode})")
    print("=" * 60)
    print(f"DB        : {db_path}")
    print(f"파일      : {len(files)}개")
    print(f"파싱 usage: {parsed:,}건")
    print(f"신규 적재 : {new_rows:,}행 (멱등 — 기존 {before:,} → {after:,})")
    print("-" * 60)
    print(f"  input        : {tot['input']:>16,}")
    print(f"  output       : {tot['output']:>16,}")
    print(f"  cache_create : {tot['cache_create']:>16,}")
    print(f"  cache_read   : {tot['cache_read']:>16,}")
    print(f"  full(4항목)  : {tot['full']:>16,}")
    if tot["footer"]:
        print(f"  footer 근사  : {tot['footer']:>16,}  (full/{tot['ratio']:.0f}x)")
    print("=" * 60)
    if not args.apply:
        print("(미리보기 — 실제 적재는 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
