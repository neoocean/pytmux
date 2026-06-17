#!/usr/bin/env python3
"""실 캡처 코퍼스로 Claude 스크래핑 휴리스틱 감사(F3/F4/F5 오탐 + fixture 보강).

REC 캡처 로그(captures/<machine>/*.log)를 pyte/native 파이프라인으로 **프레임 단위**
재생하며, 각 프레임 렌더 텍스트에 claude.py 의 휴리스틱을 돌려 무엇이 발화하는지
기록한다. 발화마다 (파일·프레임·렌더 스니펫)을 모아 오탐/미탐을 사람이 판정한다.

용법:
    python3 scripts/audit_scrape.py captures/playground.local --cols 100 --rows 30
    python3 scripts/audit_scrape.py captures/woojinkim captures/playground.local \
        --max-bytes 33554432 --out /tmp/audit.json

프레임 경계: 동기출력(DECSET 2026) `ESC[?2026l` 를 완성 프레임으로 본다(서버 flush
동치 — capture-replay-sync 교훈). 2026 이 없는 로그는 N바이트마다 스냅샷한다.
거대 로그는 --max-bytes 까지만 먹고 잘렸음을 기록한다(미탐 위험 명시)."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib.model import Pane                              # noqa: E402
from pytmuxlib.replay import render_pane_lines, read_capture  # noqa: E402

# claude.py 는 플러그인 디렉토리(하이픈 포함)라 일반 import 불가 → 경로로 로드.
import importlib.util                                          # noqa: E402

_CLAUDE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "pytmuxlib", "plugins", "claude-code", "claude.py")
_spec = importlib.util.spec_from_file_location("_claude_audit", _CLAUDE_PY)
claude = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(claude)

SYNC_END = b"\x1b[?2026l"
SNAP_EVERY = 8192   # 2026 없는 로그: 이 바이트마다 스냅샷


def frames(data: bytes):
    """바이트 스트림을 프레임 청크로 쪼갠다. 2026 종료를 경계로, 없으면 고정 크기."""
    if SYNC_END in data:
        parts = data.split(SYNC_END)
        acc = b""
        for i, p in enumerate(parts[:-1]):
            acc += p + SYNC_END
            yield acc
            acc = b""
        if parts[-1]:
            yield parts[-1]
    else:
        for i in range(0, len(data), SNAP_EVERY):
            yield data[i:i + SNAP_EVERY]


def is_claude_screen(text: str) -> bool:
    """렌더 텍스트가 Claude Code 화면으로 보이면 True(휴리스틱 발화의 맥락 판정)."""
    return claude.claude_state(text) is not None or claude.claude_welcome(text) \
        or "auto mode on" in text or "? for shortcuts" in text \
        or "Bypassing Permissions" in text


def snippet(lines, needle: str, ctx=2) -> str:
    """needle 을 포함하는 줄 ±ctx 를 합쳐 스니펫으로."""
    rstripped = [ln.rstrip() for ln in lines]
    for i, ln in enumerate(rstripped):
        if needle.lower() in ln.lower():
            lo, hi = max(0, i - ctx), min(len(rstripped), i + ctx + 1)
            return "\n".join(rstripped[lo:hi])
    # needle 못 찾으면 비어있지 않은 마지막 몇 줄
    ne = [ln for ln in rstripped if ln.strip()]
    return "\n".join(ne[-4:])


def _bounded_read(path: str, max_bytes: int) -> bytes:
    """경계 읽기(.gz 투명). 거대 로그는 max_bytes 까지만 — 압축 해제 스트림 기준."""
    import gzip as _gz
    op = _gz.open if path.endswith(".gz") else open
    with op(path, "rb") as f:
        return f.read(max_bytes + 1)


def audit_file(path: str, cols: int, rows: int, max_bytes: int):
    try:
        data = _bounded_read(path, max_bytes)
    except OSError as e:
        return {"file": path, "error": str(e)}
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]

    pane = Pane(-1, -1, cols, rows)
    hits = []
    seen_keys = set()
    nframes = 0
    claude_frames = 0
    for chunk in frames(data):
        if not chunk:
            continue
        try:
            pane.feed(chunk)
        except Exception as e:   # noqa: BLE001 (감사 도구 — 어떤 파서 예외도 기록만)
            hits.append({"kind": "feed_error", "err": repr(e)})
            continue
        nframes += 1
        lines = render_pane_lines(pane)
        text = "\n".join(lines)
        is_c = is_claude_screen(text)
        if is_c:
            claude_frames += 1

        # 각 휴리스틱 발화 기록(맥락=Claude 화면 여부 동반).
        st = claude.claude_state(text)
        api = claude.claude_api_error(text)
        use = claude.claude_usage(text)
        inl = claude.parse_inline_limit(text)
        usg = claude.parse_usage(text)

        for kind, val, needle in (
            ("api_error", api, "error"),
            ("usage", use, "tok"),
            ("inline_limit", inl, "limit"),
            ("usage_panel", usg, "used"),
            ("state_limit", (st == "limit"), "limit"),
        ):
            if not val:
                continue
            # 같은 (kind, 값, 스니펫 첫줄) 중복은 한 번만.
            key = (kind, json.dumps(val, ensure_ascii=False, default=str))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            hits.append({
                "kind": kind,
                "value": val if not isinstance(val, bool) else True,
                "is_claude": is_c,
                "state": st,
                "snippet": snippet(lines, needle),
            })
    return {
        "file": path,
        "bytes": len(data),
        "truncated": truncated,
        "frames": nframes,
        "claude_frames": claude_frames,
        "hits": hits,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+", help="캡처 디렉토리 또는 파일")
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    ap.add_argument("--out", default="/tmp/audit_scrape.json")
    args = ap.parse_args()

    paths = []
    for d in args.dirs:
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.endswith((".log", ".log.gz")):
                    paths.append(os.path.join(d, name))
        elif os.path.isfile(d):
            paths.append(d)

    results = []
    totals = {"files": 0, "truncated": 0, "claude_files": 0}
    kind_counts = {}
    for i, p in enumerate(paths):
        r = audit_file(p, args.cols, args.rows, args.max_bytes)
        results.append(r)
        totals["files"] += 1
        if r.get("truncated"):
            totals["truncated"] += 1
        if r.get("claude_frames"):
            totals["claude_files"] += 1
        for h in r.get("hits", []):
            kind_counts[h["kind"]] = kind_counts.get(h["kind"], 0) + 1
        if (i + 1) % 25 == 0:
            print(f"  ... {i + 1}/{len(paths)}", file=sys.stderr)

    with open(args.out, "w") as f:
        json.dump({"totals": totals, "kind_counts": kind_counts,
                   "results": results}, f, ensure_ascii=False, indent=1)
    print(f"files={totals['files']} claude_files={totals['claude_files']} "
          f"truncated={totals['truncated']}")
    print("kind_counts:", json.dumps(kind_counts, ensure_ascii=False))
    print("→", args.out)


if __name__ == "__main__":
    main()
