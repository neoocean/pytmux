#!/usr/bin/env python3
"""claude 의 fullscreen canary 를 **주기로 찍는다** — pytmux-415 ⓒ.

## 왜 있나

이 이슈는 여섯 회차째 「추론」에 머물렀고, 그 이유가 한 줄로 적혀 있다:

> **재는 자를 안 세웠다.** 이 값들은 전부 사후에 읽은 것이다 — 주기로 찍는 것이
> 없으면 다음 트립도 또 사후에 발견한다.

사후에 읽으면 **가장 중요한 증거가 이미 지워져 있다**. `fullscreenBootPending` 은
pid 를 열쇠로 갖는 표인데 트립하는 순간 **비워진다**(정본 `jc` — `delete D[we]` 뒤
`K={…}`). 그래서 「세 boot 가 각각 무엇이었나」를 나중에는 못 가른다. 이 기록기는
그 표를 **비워지기 전에** 찍고, 그 pid 들이 누구였는지(부모 사슬)까지 함께 남긴다.

## ⛔ 무엇을 안 하나

- **아무것도 안 고친다.** `~/.claude.json` 을 읽기만 한다(열기도 읽기 전용).
- **아무 프로세스도 안 죽인다.** `ps` 로 보기만 한다 — 이름으로 무엇을 겨냥하는
  코드가 여기 없다(루트 CLAUDE.md 의 ⛔ 규율).
- 판정하지 않는다. 「누가 무장했나」는 이 로그를 **사람이** 읽고 정한다.

## 쓰는 법

    python3 scripts/fullscreen_watch.py                 # 20초마다 · 바뀔 때만 적는다
    python3 scripts/fullscreen_watch.py --interval 5 --out /tmp/fsw.jsonl
    python3 scripts/fullscreen_watch.py --once          # 지금 한 장만 찍고 끝낸다

바뀐 것이 없으면 **안 적는다**(하루 4320줄이 아니라 트립 줄 몇 개가 남게). 다만
`--heartbeat N` 마다 한 줄은 남겨 「돌고 있었다」가 증명되게 한다 — 그것이 없으면
로그가 조용한 것이 「트립이 없었다」인지 「기록기가 죽어 있었다」인지 못 가른다.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fullscreen = importlib.import_module("pytmuxlib.plugins.claude-code.fullscreen")

# 이 셋이 canary 회계의 전부다(정본 `jc` 의 w/next 세 칸).
FIELDS = ("fullscreenAutoDisabled", "fullscreenBootPending",
          "fullscreenBootStrikes")


def read_fields(path: str | None = None) -> dict:
    """설정 파일에서 canary 세 칸만 꺼낸다(읽기 전용 · best-effort).

    ⛔ 파일을 통째로 로그에 싣지 않는다 — 그 파일에는 사용자가 친 프롬프트 이력이
    들어 있다(실측 수백 KB). 우리가 알아야 할 것은 이 셋뿐이다."""
    p = path or fullscreen.config_path()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {"_unreadable": True}
    if not isinstance(doc, dict):
        return {"_unreadable": True}
    return {k: doc.get(k) for k in FIELDS}


def _ps_rows(runner=None) -> list:
    """`ps` 한 번. (pid, ppid, lstart, command) 목록. 못 부르면 빈 목록."""
    runner = runner or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=10).stdout)
    try:
        out = runner(["ps", "-Ao", "pid=,ppid=,lstart=,command="])
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        # lstart 는 공백 5칸짜리 고정 폭("Wed Sep  3 07:46:01 2026")이다.
        rest = parts[2].split(None, 5)
        when = " ".join(rest[:5]) if len(rest) >= 5 else ""
        cmd = rest[5] if len(rest) > 5 else ""
        rows.append({"pid": pid, "ppid": ppid, "started": when, "cmd": cmd})
    return rows


def attribute(pending, rows) -> list:
    """`fullscreenBootPending` 의 pid 들이 **누구였는지** 붙인다.

    ★ 여기가 이 기록기의 값이다. 트립하면 그 표는 비워지므로, 나중에 `~/.claude.json`
    을 읽어서는 「세 boot 가 각각 무엇이었나」를 영영 못 가른다. 살아 있는 동안
    부모 사슬을 함께 적어 두면 그 물음이 로그 한 줄로 답해진다 —
    「우리 그림자 프로브가 낳은 것인가, 사용자가 패널에서 띄운 것인가」.

    ⛔ pid 는 재사용된다. 그래서 **죽었는지 살았는지**도 함께 적고(`alive`), 산
    것만 부모 사슬을 따라간다."""
    if not isinstance(pending, dict):
        return []
    by_pid = {r["pid"]: r for r in rows}
    out = []
    for key, val in sorted(pending.items()):
        try:
            pid = int(key)
        except (TypeError, ValueError):
            continue
        me = by_pid.get(pid)
        item = {"pid": pid, "alive": me is not None}
        if isinstance(val, dict):
            item["startedAt"] = val.get("startedAt")
            item["version"] = val.get("version")
            item["died"] = val.get("died")
        if me:
            item["cmd"] = me["cmd"][:200]
            item["started"] = me["started"]
            chain, cur, seen = [], me["ppid"], {pid}
            while cur and cur not in seen and len(chain) < 6:
                seen.add(cur)
                par = by_pid.get(cur)
                if not par:
                    break
                chain.append({"pid": cur, "cmd": par["cmd"][:120]})
                cur = par["ppid"]
            item["parents"] = chain
        out.append(item)
    return out


def sample(path=None, rows=None, now=None) -> dict:
    """지금 한 장. 순수하게 만들려고 `rows`·`now` 를 주입받는다(시험용)."""
    fields = read_fields(path)
    rows = _ps_rows() if rows is None else rows
    return {
        "ts": (now if now is not None else time.time()),
        "installed": fullscreen.installed_version(),
        **fields,
        "pending_who": attribute(fields.get("fullscreenBootPending"), rows),
    }


def significant(prev, cur) -> bool:
    """적을 값이 있나 — canary 세 칸 중 **하나라도** 달라졌으면 적는다.

    ⛔ `pending_who` 는 판정에서 뺀다. 그 안의 `alive`·부모 사슬은 프로세스가
    뜨고 지는 것만으로 매 표본 달라져서, 그것을 세면 로그가 곧 heartbeat 가 된다
    (=이 함수가 하는 일이 없어진다)."""
    if prev is None:
        return True
    return any(prev.get(k) != cur.get(k) for k in FIELDS + ("_unreadable",))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--interval", type=float, default=20.0, help="초(기본 20)")
    ap.add_argument("--out", default=None,
                    help="JSONL 자리(기본: <스크래치>/fullscreen-watch.jsonl)")
    ap.add_argument("--config", default=None, help="claude 설정 파일 자리")
    ap.add_argument("--once", action="store_true", help="한 장만 찍고 끝낸다")
    ap.add_argument("--heartbeat", type=int, default=90,
                    help="이만큼의 표본마다 한 줄은 남긴다(0=끔)")
    a = ap.parse_args(argv)
    out = a.out or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "fullscreen-watch.jsonl")

    prev, n = None, 0
    while True:
        cur = sample(a.config)
        n += 1
        why = ("change" if significant(prev, cur)
               else ("heartbeat" if a.heartbeat and n % a.heartbeat == 0
                     else None))
        if why:
            rec = dict(cur, why=why, n=n)
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)
        prev = cur
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
