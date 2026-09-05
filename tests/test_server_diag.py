"""pytmux-382 — `debug-stats` 의 **서버 절반**(`pytmuxlib/serverdiag.py` · `debug_stats` 명령).

이슈의 조사 코멘트가 셋 다 같은 자리에서 막혔다: *"`:debug-stats` 의 서버 절반이 없다 —
그것이 있었으면 위 전부가 한 줄이었다."* 서버가 코어를 먹는 이유를 가리려고 py-spy 를 그
상자에 깔아야 했고, 프로브가 예산을 얼마나 태우는지는 로그 어디에도 없었다. 이 시험은 그
절반이 **실제로 재고, 실제로 회신되는가**를 잰다.

되돌리면 실패해야 하는 오라클:
  · 표에서 축이 빠지면 → test_a_server_sample_carries_the_axes
  · 못 잰 값을 0 으로 적으면 → test_unknowns_are_none_not_zero
  · 서버가 문장을 지으면(라벨 문자열이 실리면) → test_the_server_ships_numbers_not_sentences
  · **명령이 표에 없거나 회신을 안 보내면** → test_the_command_answers_the_asking_client_only
    (값 만드는 함수만 재는 시험은 「호출 제거」 뮤테이션에 공허 통과한다)
  · 정본 클라가 그 회신을 판에 안 붙이면 → test_the_canon_client_appends_the_server_half
  · `gc.collect()` 를 부르면 → test_it_never_collects (서버가 멎으면 전 클라 프레임이 선다)
"""
import asyncio
import gc

import harness  # noqa: F401
from harness import running_server, patched
from pytmuxlib import serverdiag


class _Client:
    """회신을 받아 두는 인형 클라 — `_send_to` 가 이것에 쓴다."""
    def __init__(self):
        self.plugin_state = {}
        self.last_seen = 0.0
        self.sent = []


async def test_a_server_sample_carries_the_axes():
    async with running_server() as (srv, task, sock):
        srv.ensure_default_session(80, 24)
        s = serverdiag.collect_stats(srv)
    for k in ("pid", "python", "uptime_s", "rss", "fds", "threads", "tasks", "clients",
              "client_idle_s", "sessions", "windows", "panes", "scrollback_rows",
              "remote_links", "remote_reconnecting", "objects", "gc", "top",
              "usage_probe", "error_log_bytes"):
        assert k in s, k
    assert s["sessions"] >= 1 and s["windows"] >= 1 and s["panes"] >= 1, s
    assert s["objects"] > 0 and s["top"], s
    assert s["uptime_s"] is not None and s["uptime_s"] >= 0, s
    assert [g["gen"] for g in s["gc"]] == [0, 1, 2], s["gc"]


def test_unknowns_are_none_not_zero():
    """⛔ 0 은 「없다」로 읽힌다 — Windows 의 fd 수처럼 못 세는 값은 `None` 이어야 한다."""
    class _Srv:
        sessions = {}
        clients = []
        sock_path = "/nonexistent/x.sock"
    s = serverdiag.collect_stats(_Srv())
    assert s["uptime_s"] is None, s["uptime_s"]           # `_boot_time` 이 없다
    assert s["usage_probe"] is None                         # 한 번도 안 돌았다
    assert s["error_log_bytes"] is None                     # 파일이 없다
    assert s["clients"] == 0 and s["client_idle_s"] == []


def test_the_server_ships_numbers_not_sentences():
    """서버는 라벨을 짓지 않는다 — 값이 문장이면 서버 로케일로 굳는다(pytmux-419 부류)."""
    class _Srv:
        sessions = {}
        clients = []
        sock_path = "/nonexistent/x.sock"
    s = serverdiag.collect_stats(_Srv())
    for k, v in s.items():
        if k in ("python",):
            continue
        if isinstance(v, str):
            assert False, (k, v)
    # 상위 타입은 `[이름, 수]` 쌍 — 이름은 자료(타입 이름)라 그대로다.
    assert all(isinstance(name, str) and isinstance(n, int) for name, n in s["top"])


def test_it_never_collects():
    calls = []
    real = gc.collect
    with patched(gc, collect=lambda *a, **k: calls.append(1) or real(*a, **k)):
        class _Srv:
            sessions = {}
            clients = []
            sock_path = "/nonexistent/x.sock"
        serverdiag.collect_stats(_Srv())
    assert calls == [], "진단이 제 손으로 전체 수거를 불렀다"


async def test_the_command_answers_the_asking_client_only():
    """★ 호출부 오라클 — 표에 `debug_stats` 가 HANDLED 로 있고, 그 핸들러가 **요청 클라에게만**
    `{"t":"debug_stats","stats":…}` 를 보낸다."""
    from pytmuxlib.servercmd import _CMD_TABLE, HANDLED
    handler, disp = _CMD_TABLE["debug_stats"]
    assert disp == HANDLED, disp
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        asking, other = _Client(), _Client()
        sent = []

        async def _send_to(c, obj):
            sent.append((c, obj))
            return True
        srv._send_to = _send_to
        await handler(srv, asking, sess, {"t": "cmd", "action": "debug_stats"})
        assert len(sent) == 1, sent
        c, obj = sent[0]
        assert c is asking and c is not other
        assert obj["t"] == "debug_stats" and isinstance(obj["stats"], dict), obj
        assert obj["stats"]["pid"] > 0 and obj["stats"]["sessions"] >= 1, obj["stats"]


def test_the_canon_client_appends_the_server_half():
    """정본 `debug-stats` 판이 회신을 받으면 **서버 절반**이 같은 판 아래에 붙는다 —
    `clientdiag.render_server` 가 줄을 짓고, 클라가 그 줄을 판에 싣는다."""
    from pytmuxlib import clientdiag
    stats = {"pid": 7, "python": "3.13.0", "uptime_s": 3725.0, "rss": 150 * 1048576,
             "fds": 77, "threads": 4, "tasks": 6, "clients": 1, "client_idle_s": [0.3],
             "sessions": 1, "windows": 2, "panes": 3, "scrollback_rows": 1234,
             "remote_links": 0, "remote_reconnecting": 0, "objects": 100000,
             "gc": [{"gen": 0, "collections": 10, "collected": 5, "uncollectable": 0}],
             "top": [["Strip", 500], ["dict", 400]],
             "usage_probe": {"boot": 14.0, "panel": 10.1, "total": 37.0, "ok": True,
                             "at": 1000.0},
             "error_log_bytes": 349 * 1024}
    lines = clientdiag.render_server(stats, now=1130.0)
    joined = "\n".join(lines)
    assert "pid 7" in joined and "1h 02m" in joined, joined
    assert "boot 14.0s" in joined and "total 37.0s" in joined, joined
    assert "2m 10s" in joined, joined            # 프로브 회차의 나이
    assert "Strip" in joined and "500" in joined, joined
    # 못 잰 값은 `?` 다(0 이 아니다).
    unknown = clientdiag.render_server({"pid": 1}, now=None)
    assert any("fd ?" in ln for ln in unknown), unknown
