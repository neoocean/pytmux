"""서버가 **제 프로세스를 잰** 한 장 — `debug-stats` 판의 서버 절반(pytmux-382).

# 왜 있나

pytmux-382(「TUI 를 수 일 띄워 두면 느려진다」)의 조사가 실제로 막힌 자리는 클라가
아니라 **서버**였다. office1 의 서버가 코어를 먹는 이유를 가리려고 그 상자에 py-spy 를
깔아 라이브 프로세스를 떠야 했고, 그 조사 코멘트가 *"`:debug-stats` 의 서버 절반이
있었으면 위 전부가 한 줄이었다"* 고 적었다. 이 모듈이 그 절반이다.

# 규율

- ⛔ **UI 의존 없음 · 숫자만.** 서버가 문장을 짓지 않는다 — 문장은 클라가 제 로케일로
  짓는다(`clientdiag.render_server` · Rust `proto::diag::ServerStats::lines`). 서버가 지은
  글은 서버 로케일로 굳어 영어 사용자에게 한국어로 뜬다(pytmux-419 부류).
- ⛔ **못 잰 것은 `None` 이다.** 0 으로 적으면 「없다」로 읽히고, 그건 우리가 모르는
  사실이다(Windows 의 fd 수처럼 그 OS 에서 못 세는 값). `clientdiag` 와 같은 규율.
- ⛔ **`gc.collect()` 를 안 부른다.** 전체 수거는 서버를 수십 ms 멎게 하고, 그 멎음이
  모든 클라의 프레임을 세운다 — 진단이 재려던 증상을 자기가 만든다.
- **싸야 한다.** 이 표는 사람이 한 번 누를 때 한 번 모으는 것이지만, `gc.get_objects()`
  한 번은 산 객체 수에 비례한다(수십만 개면 수십 ms). 그래서 상위 타입 집계는 그 한
  번의 순회로 끝내고 다른 것은 O(1)·O(패널 수)만 한다.

# 재는 것

수명 · RSS · fd · 스레드 · asyncio 태스크 · 클라(마지막 수신 나이) · 세션/탭/패널 ·
스크롤백 행 합 · 원격 링크 · gc 세대 · 산 객체 상위 · `/usage` 프로브 마지막 회차
(`_usage_probe_last` · CL 75059 가 심은 그 값) · error.log 크기.
"""
from __future__ import annotations

import asyncio
import gc
import os
import sys
import threading
import time
from collections import Counter

#: 상위 타입 몇 개를 싣나 — `clientdiag.TOP_TYPES` 와 같은 수.
TOP_TYPES = 8


def rss_bytes() -> int | None:
    """상주 메모리(바이트). 못 알아내는 OS 에서는 `None`."""
    try:
        import resource
    except ImportError:
        return None                      # Windows
    try:
        n = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return None
    # ⚠ macOS 는 바이트 · Linux 는 KiB — `clientdiag.rss_bytes` 와 같은 갈래.
    return int(n) if sys.platform == "darwin" else int(n) * 1024


def open_fds() -> int | None:
    """열린 fd 수. 세는 길이 없는 OS 에서는 `None`(0 이 아니다)."""
    for d in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(d))
        except Exception:
            continue
    return None


def gc_generations() -> list:
    out = []
    try:
        for i, st in enumerate(gc.get_stats()):
            out.append({"gen": i,
                        "collections": int(st.get("collections", 0)),
                        "collected": int(st.get("collected", 0)),
                        "uncollectable": int(st.get("uncollectable", 0))})
    except Exception:
        pass
    return out


def live_tasks() -> int | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    try:
        return len(asyncio.all_tasks(loop))
    except Exception:
        return None


def _scrollback_rows(server) -> int | None:
    """전 패널 스크롤백 행의 합 — 메모리가 「자라는」 자리의 첫 후보.

    행 객체를 세지 않고 **길이만** 더한다(O(패널 수)). 한 패널이라도 모양을 모르면
    그 패널은 건너뛴다 — 부분합을 전체인 척 내지 않으려면 그때는 `None` 이 맞지만,
    스크린 종류가 갈리는 것은 정상(alt 화면)이라 **`_main` 만 본다**."""
    total = 0
    try:
        for sess in list(server.sessions.values()):
            for tab in list(getattr(sess, "tabs", ())):
                win = getattr(tab, "window", None)
                if win is None:
                    continue
                for pane in win.panes():
                    scr = getattr(pane, "_main", None)
                    hist = getattr(scr, "history", None)
                    if hist is None:
                        continue
                    total += len(getattr(hist, "top", ())) + len(getattr(hist, "bottom", ()))
    except Exception:
        return None
    return total


def _error_log_bytes(server) -> int | None:
    try:
        from . import ipc
        path = ipc.state_base(server.sock_path) + ".error.log"
        return os.path.getsize(path)
    except Exception:
        return None


def collect_stats(server) -> dict:
    """한 장. 전부 **숫자·이름**이다 — 라벨은 클라가 붙인다(모듈 머리말)."""
    now = time.time()
    mono = time.monotonic()
    objs = gc.get_objects()
    counts = Counter(type(o).__name__ for o in objs)
    clients = list(getattr(server, "clients", ()))
    idle = []
    for c in clients:
        seen = float(getattr(c, "last_seen", 0.0) or 0.0)
        idle.append(round(mono - seen, 1) if seen > 0 else None)
    sessions = list(getattr(server, "sessions", {}).values())
    windows = sum(len(getattr(s, "tabs", ())) for s in sessions)
    panes = 0
    for s in sessions:
        for tab in getattr(s, "tabs", ()):
            win = getattr(tab, "window", None)
            try:
                panes += len(win.panes()) if win is not None else 0
            except Exception:
                pass
    probe = getattr(server, "_usage_probe_last", None)
    if not isinstance(probe, dict) or not probe:
        probe = None
    else:
        probe = {k: probe.get(k) for k in ("boot", "panel", "total", "ok", "at")}
    boot = getattr(server, "_boot_time", None)
    return {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "uptime_s": round(now - float(boot), 1) if boot else None,
        "rss": rss_bytes(),
        "fds": open_fds(),
        "threads": threading.active_count(),
        "tasks": live_tasks(),
        "clients": len(clients),
        "client_idle_s": idle,
        "sessions": len(sessions),
        "windows": windows,
        "panes": panes,
        "scrollback_rows": _scrollback_rows(server),
        "remote_links": len(getattr(server, "_remotes", None) or {}),
        "remote_reconnecting": len(getattr(server, "_remote_reconn", None) or {}),
        "objects": len(objs),
        "gc": gc_generations(),
        "top": [[name, n] for name, n in counts.most_common(TOP_TYPES)],
        "usage_probe": probe,
        "error_log_bytes": _error_log_bytes(server),
    }
