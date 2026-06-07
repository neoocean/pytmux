#!/usr/bin/env python3
"""토큰 로그(`*.tokens.jsonl`)의 **비신뢰 계정을 unknown 으로** 일괄 정리.

배경: 2026-06-07 이전 `claude_account` 는 화면에 흩어진 임의 이메일(git SSH URL
`git@github.com:...` → `gi…@github.com`, 산문/예시 이메일 등)을 계정으로 오검출했다.
검출은 엄격화로 고쳤지만(docs/TOKEN_SAVING_SCENARIO.md §2.1), **이미 적힌 과거
레코드**는 그대로 남는다. 이 스크립트가 그 과거 데이터를 정리한다.

신뢰 규칙(allowlist): `--keep <account>`(정확 일치, 반복) 또는 `--keep-domain
<domain>`(별칭이 `@domain` 으로 끝남, 반복)에 해당하는 계정만 남기고, **나머지는 모두
`unknown`** 으로 바꾼다(이미 unknown·빈 값은 unknown 유지). 잘못된 계정 표시보다
Unknown 이 옳다는 원칙(사용자 지시 2026-06-07).

안전장치: 기본 **드라이런**(미리보기만). 실제 쓰기는 `--apply`. 쓰기 전 `<path>.bak`
백업을 남기고, 임시파일+os.replace 로 원자적 교체(0600). 서버가 로그에 append 중이면
경쟁이 날 수 있으니 **서버 idle(또는 종료) 시 실행**을 권장한다.

사용 예:
    # 상태 디렉터리의 모든 토큰 로그를 미리보기(woojinkim.org 만 신뢰)
    python3 scripts/migrate_token_accounts.py --keep-domain woojinkim.org
    # 실제 적용(특정 계정만 신뢰)
    python3 scripts/migrate_token_accounts.py --apply --keep me@woojinkim.org
    # 경로 명시
    python3 scripts/migrate_token_accounts.py --apply --keep-domain woojinkim.org \
        /tmp/pytmux-501/default.sock.tokens.jsonl
    # SQLite DB 정정(저장이 SQLite 로 이행된 뒤 — UPDATE 한 방, 파일 재작성 없음)
    python3 scripts/migrate_token_accounts.py --db db/claude-tokens.db --apply \
        --keep-domain woojinkim.org

이제 토큰 저장은 SQLite(db/claude-tokens.db)가 표준이며(docs/TOKEN_USAGE_STORAGE_DESIGN.md),
`--db` 모드가 DB 의 계정을 직접 정정한다. JSONL 경로 모드는 레거시 로그·임포트 전
데이터 정리에 남겨둔다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib import ipc, usagelog  # noqa: E402


# 신뢰 규칙(is_trusted/remap_account)은 usagelog 로 단일화 — DB update_accounts 와 공유.
is_trusted = usagelog.is_trusted
remap_account = usagelog.remap_account


def migrate_lines(lines, keep_accounts, keep_domains):
    """JSONL 줄들을 받아 (새 줄 리스트, 통계 dict) 반환(순수 함수).

    통계: {"records", "changed", "bad", "before": {acct: n}, "after": {acct: n}}.
    파싱 불가/계정 키 없는 줄은 원형 보존(bad 카운트)."""
    out, before, after = [], {}, {}
    records = changed = bad = 0
    for raw in lines:
        s = raw.rstrip("\n")
        if not s.strip():
            out.append(raw)
            continue
        try:
            rec = json.loads(s)
        except (ValueError, TypeError):
            bad += 1
            out.append(raw)            # 손상 줄은 건드리지 않는다
            continue
        if not isinstance(rec, dict):
            bad += 1
            out.append(raw)
            continue
        records += 1
        old = rec.get("account") or usagelog.UNKNOWN
        new = remap_account(rec.get("account"), keep_accounts, keep_domains)
        before[old] = before.get(old, 0) + 1
        after[new] = after.get(new, 0) + 1
        if new != (rec.get("account") or usagelog.UNKNOWN):
            changed += 1
        rec["account"] = new
        out.append(json.dumps(rec, ensure_ascii=False) + "\n")
    return out, {"records": records, "changed": changed, "bad": bad,
                 "before": before, "after": after}


def _write_atomic(path, lines):
    """0600 임시파일에 쓰고 os.replace 로 원자 교체. 기존 파일은 <path>.bak 로 백업."""
    if os.path.exists(path):
        bak = path + ".bak"
        with open(path, "rb") as src, ipc.open_private(bak, "wb") as dst:
            dst.write(src.read())
    tmp = path + ".tmp"
    with ipc.open_private(tmp, "w") as f:
        f.writelines(lines)
    os.replace(tmp, path)


def _discover_logs():
    """상태 디렉터리에서 *.tokens.jsonl 을 찾는다(경로 미지정 시)."""
    sd = ipc.default_state_dir()
    return sorted(set(glob.glob(os.path.join(sd, "*.tokens.jsonl"))))


def _fmt_counts(d):
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items(),
                                                    key=lambda kv: -kv[1]))


def _migrate_db(db_path, keep_accounts, keep_domains, apply):
    """SQLite DB 의 비신뢰 계정을 unknown 으로 정정(UPDATE 한 방 — 파일 재작성 없음).
    드라이런은 변경될 행 수만 보고하고, --apply 시 실제 UPDATE 한다."""
    from pytmuxlib import usagedb  # 지연 임포트(JSONL 경로엔 불필요)
    if not os.path.exists(db_path):
        print(f"DB 없음: {db_path}")
        return 1
    conn = usagedb.connect(db_path)
    before = usagedb.account_counts(conn)
    untrusted = {a: n for a, n in before.items()
                 if a != usagelog.UNKNOWN
                 and not is_trusted(a, keep_accounts, keep_domains)}
    print(f"대상 DB: {db_path}")
    print(f"신뢰 계정={sorted(keep_accounts) or '(없음)'} · "
          f"신뢰 도메인={sorted(keep_domains) or '(없음)'} · "
          f"모드={'적용' if apply else '드라이런(미리보기)'}")
    print(f"  before: {_fmt_counts(before)}")
    print(f"  정정 대상(→unknown): {_fmt_counts(untrusted) or '(없음)'}")
    if not apply:
        print("\n드라이런입니다. 실제로 바꾸려면 --apply 를 붙이세요.")
        conn.close()
        return 0
    changed = usagedb.update_accounts(conn, keep_accounts, keep_domains)
    print(f"  after : {_fmt_counts(usagedb.account_counts(conn))}")
    print(f"  → {changed} 행 정정 완료.")
    conn.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help="토큰 로그 경로(미지정 시 상태 디렉터리 검색)")
    ap.add_argument("--keep", action="append", default=[], metavar="ACCOUNT",
                    help="신뢰할 계정(정확 일치, 반복 가능)")
    ap.add_argument("--keep-domain", action="append", default=[], metavar="DOMAIN",
                    help="신뢰할 도메인 — 별칭이 @DOMAIN 으로 끝나면 유지(반복 가능)")
    ap.add_argument("--apply", action="store_true",
                    help="실제 적용(미지정 시 드라이런)")
    ap.add_argument("--db", metavar="PATH",
                    help="JSONL 대신 SQLite DB(db/claude-tokens.db)의 계정을 정정한다")
    args = ap.parse_args(argv)

    keep_accounts = set(args.keep)
    keep_domains = set(args.keep_domain)
    if not keep_accounts and not keep_domains:
        ap.error("--keep 또는 --keep-domain 중 최소 하나는 필요합니다 "
                 "(신뢰 목록 없이 전부 unknown 으로 만들지 않도록).")

    if args.db:
        return _migrate_db(args.db, keep_accounts, keep_domains, args.apply)

    paths = args.paths or _discover_logs()
    if not paths:
        print("토큰 로그를 찾지 못했습니다(상태 디렉터리에 *.tokens.jsonl 없음).")
        return 1

    print(f"신뢰 계정={sorted(keep_accounts) or '(없음)'} · "
          f"신뢰 도메인={sorted(keep_domains) or '(없음)'} · "
          f"모드={'적용' if args.apply else '드라이런(미리보기)'}")
    total_changed = 0
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            print(f"  ! {path}: 읽기 실패 {e}")
            continue
        new_lines, st = migrate_lines(lines, keep_accounts, keep_domains)
        total_changed += st["changed"]
        print(f"\n{path}")
        print(f"  레코드 {st['records']} · 변경 {st['changed']} · 손상줄 {st['bad']}")
        print(f"  before: {_fmt_counts(st['before'])}")
        print(f"  after : {_fmt_counts(st['after'])}")
        if args.apply and st["changed"]:
            _write_atomic(path, new_lines)
            print(f"  → 적용 완료(백업 {os.path.basename(path)}.bak)")
        elif args.apply:
            print("  → 변경 없음(건너뜀)")
    if not args.apply:
        print("\n드라이런입니다. 실제로 바꾸려면 --apply 를 붙이세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
