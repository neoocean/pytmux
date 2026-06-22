#!/usr/bin/env python3
"""PoC: Claude Code 트랜스크립트(`~/.claude/projects/<proj>/*.jsonl`) 토큰 회계 파서.

목적(§10-D 과소집계 진단): pytmux 의 토큰 누계는 busy 푸터의 `↑/↓ N tokens` 를
스크랩해 만든다(plugins/claude-code/tokens.py). 그 화살표 값은 본질적으로
**비캐시 input + output** 근사라, API usage 의 네 항목 중 `cache_read_input_tokens`
와 `cache_creation_input_tokens` 를 통째로 빠뜨린다. 에이전트형 코딩 세션에선 캐시
read 가 토큰 볼륨의 대다수라, 다른 도구(ccusage·`/usage`·API usage)가 트랜스크립트
JSONL 에서 네 항목을 모두 합산하는 것 대비 pytmux 누계가 체계적으로 적게 나온다.

이 PoC 는 그 권위 출처(트랜스크립트)를 직접 읽어 네 항목을 모두 합산하고, pytmux
스크랩이 보는 근사(input+output)와 나란히 찍어 **과소집계 배율을 정량화**한다.

집계 단위/중복 제거:
  - assistant 메시지의 `message.usage` 만 회계 대상(다른 type 은 토큰 usage 없음).
  - 같은 (message.id, requestId) 쌍은 1회만 셈(ccusage 와 동일) — 스트리밍 중간/최종
    재기록·재개(resume) 세션이 히스토리를 복사해 같은 메시지가 여러 파일/줄에 중복
    등장하므로, 전역 dedup 으로 중복 합산을 막는다.

사용 예:
    # 현재 디렉터리(cwd)에 해당하는 프로젝트 트랜스크립트 회계
    python3 scripts/poc/token_jsonl_audit.py
    # 특정 프로젝트 경로 지정
    python3 scripts/poc/token_jsonl_audit.py --project ~/p4/playground/scripts/pytmux
    # 전체 프로젝트 합산 + 세션별 표
    python3 scripts/poc/token_jsonl_audit.py --all --per-session
    # 날짜 필터 + JSON 출력(스크립트 연동용)
    python3 scripts/poc/token_jsonl_audit.py --all --since 2026-06-01 --json
    # 임의 JSONL 직접 지정
    python3 scripts/poc/token_jsonl_audit.py path/to/session.jsonl ...

순수 stdlib 전용(pytmux 의존 없음) — 어느 머신에서나 단독 실행 가능.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def encode_project_dir(path: str) -> str:
    """절대 경로를 Claude Code 가 쓰는 프로젝트 디렉터리명으로 인코딩.

    Claude 는 cwd 의 경로 구분자('/')를 '-' 로 치환해 디렉터리명을 만든다
    (예: /Users/x/p4/proj → -Users-x-p4-proj). 점('.')도 같은 규칙으로 치환된다."""
    ap = os.path.abspath(os.path.expanduser(path))
    return ap.replace("/", "-").replace(".", "-")


def discover_files(args) -> list[str]:
    """회계 대상 JSONL 파일 목록을 결정한다(positional > --all > --project > cwd)."""
    if args.files:
        return [os.path.abspath(os.path.expanduser(f)) for f in args.files]
    if args.all:
        return sorted(glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")))
    target = args.project or os.getcwd()
    enc = encode_project_dir(target)
    d = os.path.join(PROJECTS_DIR, enc)
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")))
    if not files:
        sys.stderr.write(
            f"[warn] 프로젝트 트랜스크립트 없음: {d}\n"
            f"       --all 로 전체를 보거나 --project 로 경로를 지정하세요.\n")
    return files


def _u_int(u: dict, key: str) -> int:
    v = u.get(key)
    return int(v) if isinstance(v, (int, float)) else 0


def parse_files(files, since: str | None, include_sidechain: bool):
    """파일들을 훑어 assistant usage 를 (message.id, requestId) 로 dedup 하며 합산.

    반환: (totals, per_session, meta) — totals/per_session 은 토큰 항목 dict,
    meta 는 dedup·sidechain·파싱 통계."""
    seen: set[tuple] = set()
    sessions: dict[str, dict] = {}
    totals = _new_bucket()
    meta = {"files": len(files), "lines": 0, "assistant": 0, "with_usage": 0,
            "dup_skipped": 0, "sidechain": 0, "parse_errors": 0, "models": {}}

    for fp in files:
        try:
            fh = open(fp, encoding="utf-8")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                meta["lines"] += 1
                try:
                    o = json.loads(line)
                except (ValueError, TypeError):
                    meta["parse_errors"] += 1
                    continue
                if o.get("type") != "assistant":
                    continue
                meta["assistant"] += 1
                msg = o.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                ts = o.get("timestamp") or ""
                if since and ts[:10] < since:
                    continue
                is_side = bool(o.get("isSidechain"))
                if is_side:
                    meta["sidechain"] += 1
                    if not include_sidechain:
                        continue
                # ccusage 식 dedup 키: 메시지 id + requestId.
                key = (msg.get("id"), o.get("requestId"))
                if key != (None, None):
                    if key in seen:
                        meta["dup_skipped"] += 1
                        continue
                    seen.add(key)
                meta["with_usage"] += 1

                inp = _u_int(usage, "input_tokens")
                out = _u_int(usage, "output_tokens")
                cc = _u_int(usage, "cache_creation_input_tokens")
                cr = _u_int(usage, "cache_read_input_tokens")
                model = msg.get("model") or "unknown"
                meta["models"][model] = meta["models"].get(model, 0) + 1

                sid = o.get("sessionId") or os.path.basename(fp)
                b = sessions.get(sid)
                if b is None:
                    b = sessions[sid] = _new_bucket()
                    b["models"] = set()
                    b["first_ts"] = ts
                for tgt in (b, totals):
                    tgt["input"] += inp
                    tgt["output"] += out
                    tgt["cache_create"] += cc
                    tgt["cache_read"] += cr
                    tgt["turns"] += 1
                b["models"].add(model)
                b["last_ts"] = ts
    return totals, sessions, meta


def _new_bucket() -> dict:
    return {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "turns": 0}


def _derive(b: dict) -> dict:
    """버킷에서 파생값 계산: footer 근사(=input+output), 실제 합(4항목), 배율."""
    footer = b["input"] + b["output"]             # pytmux ↑/↓ 스크랩이 보는 근사
    full = footer + b["cache_create"] + b["cache_read"]   # 트랜스크립트 실제 합
    ratio = (full / footer) if footer else 0.0
    miss = full - footer                          # 스크랩이 빠뜨리는 양(≈캐시)
    return {"footer": footer, "full": full, "ratio": ratio, "missed": miss}


def _fmt(n: int) -> str:
    return f"{n:,}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="JSONL 파일 직접 지정(생략 시 자동 탐색)")
    ap.add_argument("--all", action="store_true", help="모든 프로젝트 트랜스크립트")
    ap.add_argument("--project", help="프로젝트 경로(기본: 현재 cwd)")
    ap.add_argument("--since", help="YYYY-MM-DD 이후 메시지만(timestamp 기준)")
    ap.add_argument("--per-session", action="store_true", help="세션별 표 출력")
    ap.add_argument("--no-sidechain", action="store_true",
                    help="서브에이전트(isSidechain) 메시지 제외")
    ap.add_argument("--json", action="store_true", help="JSON 출력(스크립트 연동)")
    args = ap.parse_args()

    files = discover_files(args)
    if not files:
        return 1
    totals, sessions, meta = parse_files(
        files, args.since, include_sidechain=not args.no_sidechain)
    td = _derive(totals)

    if args.json:
        out = {
            "totals": {**totals, **td},
            "meta": meta,
            "sessions": {
                sid: {k: v for k, v in b.items() if k != "models"}
                | {"models": sorted(b.get("models", [])), **_derive(b)}
                for sid, b in sessions.items()},
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    if args.per_session:
        rows = sorted(sessions.items(),
                      key=lambda kv: _derive(kv[1])["full"], reverse=True)
        print(f"{'session':12} {'turns':>5} {'input':>12} {'output':>10} "
              f"{'cache_cr':>13} {'cache_rd':>14} {'full':>15} {'x':>6}")
        print("-" * 96)
        for sid, b in rows:
            d = _derive(b)
            print(f"{sid[:12]:12} {b['turns']:>5} {_fmt(b['input']):>12} "
                  f"{_fmt(b['output']):>10} {_fmt(b['cache_create']):>13} "
                  f"{_fmt(b['cache_read']):>14} {_fmt(d['full']):>15} "
                  f"{d['ratio']:>5.1f}")
        print()

    print("=" * 60)
    print("토큰 회계 요약 (Claude Code 트랜스크립트 권위 출처)")
    print("=" * 60)
    print(f"파일 {meta['files']}개 · 라인 {_fmt(meta['lines'])} · "
          f"assistant {_fmt(meta['assistant'])} · usage {_fmt(meta['with_usage'])}")
    print(f"중복 제거 {_fmt(meta['dup_skipped'])}건 · "
          f"sidechain {_fmt(meta['sidechain'])}건"
          f"{' (제외됨)' if args.no_sidechain else ' (포함)'}"
          f" · 파싱오류 {meta['parse_errors']}건")
    if meta["models"]:
        ms = ", ".join(f"{m}×{c}" for m, c in sorted(
            meta["models"].items(), key=lambda kv: -kv[1]))
        print(f"모델: {ms}")
    print("-" * 60)
    print(f"  input_tokens          : {_fmt(totals['input']):>16}")
    print(f"  output_tokens         : {_fmt(totals['output']):>16}")
    print(f"  cache_creation_tokens : {_fmt(totals['cache_create']):>16}")
    print(f"  cache_read_tokens     : {_fmt(totals['cache_read']):>16}")
    print("-" * 60)
    print(f"  ① footer 근사(in+out) : {_fmt(td['footer']):>16}  ← pytmux 스크랩이 보는 양")
    print(f"  ② 트랜스크립트 실제   : {_fmt(td['full']):>16}  ← 4항목 합(타 도구 기준)")
    print(f"  ②-① 누락(≈캐시)       : {_fmt(td['missed']):>16}")
    if td["footer"]:
        pct = 100.0 * td["footer"] / td["full"]
        print(f"  과소집계 배율 ②/①     : {td['ratio']:>15.1f}x  "
              f"(스크랩은 실제의 {pct:.1f}%만 포착)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
