#!/usr/bin/env python3
"""접속 수명 픽스처 — 정본 재접속 상태기계를 **실제로 돌려** 갈래마다 결과를 뽑는다(pytmux-585).

# 왜 돌려서 뽑나

상수(시도 횟수·간격·창)만 옮기면 **갈래**가 갈린다 — 어느 EOF 가 재시작이고 어느 것이
드롭인지, 실패하면 무슨 글로 끝나는지, 창 안 7번째 드롭이 시도를 하는지. 그 갈래는
`clientconn._reader_task` 의 if 사슬에 있다. 그래서 그 함수를 **그대로** 부른다:

- 앱 대신 작은 스텁(`_NetReconnectMixin` 을 상속)이 `exit`·`display_message`·`run_worker` 를
  적는다. 서버도 Textual 앱도 띄우지 않는다.
- `read_msg` 는 곧장 EOF(None), `ipc.open_connection` 은 시나리오대로 성공/실패하고 **부른
  횟수**를 센다. `_RECONNECT_DELAY` 는 0 으로(갈래만 재고 시간은 안 잰다).

Rust 쪽 `proto/tests/lifecycle_conformance.rs` 가 같은 시나리오를 `base::lifecycle` 에 먹여
대조한다.

    python3 scripts/gen_lifecycle_fixture.py [--pytmux ..]
"""

import argparse
import asyncio
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "lifecycle.json")

#: 화면 문구 키 — 정본 카탈로그의 ko/en 을 그대로 싣는다.
KEYS = [
    "msg.server_restarting", "msg.restart_done", "msg.restart_done_win_noclient",
    "msg.reconnected_resync", "msg.reconnecting", "msg.reconnecting_auto",
    "msg.reconnect_failed_net", "msg.server_terminated", "msg.server_lost",
    "msg.reconnect_failed", "msg.proto_mismatch", "msg.auth_failed",
]


def _config_default(root, key):
    """`client.py` 의 `config.get("<key>", <기본값>)` 기본값 — 앱을 안 세우고 소스에서 읽는다."""
    import ast
    src = open(os.path.join(root, "pytmuxlib", "client.py"), encoding="utf-8").read()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == key):
            return ast.literal_eval(node.args[1])
    raise SystemExit(f"client.py 에 config.get({key!r}, …) 이 없다 — 정본이 움직였다")


def _degraded_scenarios(cc, root):
    """정본 `_net_sample` 을 **실제로** 돌려 언제 `reconnect_now("auto")` 가 불리나를 뽑는다(pytmux-639).

    표본 `b`(임계 초과)·`g`(이하)의 차례마다 `fired` = 회복을 건 표본 번호(1부터) · `degraded` =
    표본마다 빨간 외곽선 상태."""
    class Net(cc._NetReconnectMixin):
        def __init__(self, local):
            self.net_rtt_threshold = _config_default(root, "net_rtt_threshold")
            self.net_bad_n = _config_default(root, "net_bad_n")
            self.net_good_n = _config_default(root, "net_good_n")
            self.net_recover_n = _config_default(root, "net_recover_n")
            self.net_auto_reconnect = _config_default(root, "net_auto_reconnect")
            self._net_local = local
            self._net_bad = self._net_good = 0
            self._net_degraded = False
            self._force_reconnecting = False
            self._net_rtt_hist = []
            self._rtt_save_n = 0
            self.fired = []
            self.i = 0

        def _save_rtt_hist(self):
            pass

        def _log_net_debug(self, _rtt):
            pass

        def _composite(self):
            pass

        def reconnect_now(self, reason):
            self.fired.append({"at": self.i, "reason": reason})

    def run(name, seq, local):
        n = Net(local)
        thr = n.net_rtt_threshold
        degraded = []
        for ch in seq:
            n.i += 1
            n._net_sample(thr * 2 if ch == "b" else thr / 4)
            degraded.append(n._net_degraded)
        return {"name": name, "local": local, "seq": seq, "fired": n.fired,
                "degraded": degraded}

    return {
        "recover_n": _config_default(root, "net_recover_n"),
        "threshold": _config_default(root, "net_rtt_threshold"),
        "scenarios": [
            run("remote_45_bad", "b" * 45, False),
            run("remote_good_resets", "b" * 19 + "g" + "b" * 20, False),
            run("local_45_bad", "b" * 45, True),
        ],
    }


def _crash_scenarios(root):
    """정본 `client.run_client` 를 **실제로** 돌려 크래시 뒤 무엇을 하나를 뽑는다(pytmux-639 G10).

    앱 대신 `run()` 이 예외를 내는(또는 그냥 돌아오는) 스텁, `_relaunch_self`·`sys.exit`·
    `time.monotonic`·표준 오류를 갈아 끼운다. 기록 파일은 임시 자리다."""
    import contextlib
    import io
    import tempfile
    import types
    from pytmuxlib import client as cl

    env = "PYTMUX_CLIENT_CRASH_N"
    out = {"env": env, "max": cl._CLIENT_CRASH_RELAUNCH_MAX, "window": cl._CRASH_LOOP_WINDOW,
           "cases": []}
    tmp = tempfile.mkdtemp(prefix="pgc-")
    sock = os.path.join(tmp, "default.sock")
    crash_log = cl.ipc.state_base(sock) + ".client.crash.log"

    class Boom:
        def __init__(self, crash):
            self.crash = crash
            self._relaunch = False

        def run(self):
            if self.crash:
                raise RuntimeError("boom")

    def case(prev, elapsed, crash=True):
        clock = iter([1000.0, 1000.0 + elapsed])
        relaunched = []
        saved = (cl.build_client_app, cl._relaunch_self, cl.time, cl.load_config,
                 os.environ.get(env))
        cl.build_client_app = lambda *_a, **_k: Boom(crash)
        cl._relaunch_self = lambda: relaunched.append(os.environ.get(env))
        cl.time = types.SimpleNamespace(monotonic=lambda: next(clock), strftime=time_strftime)
        cl.load_config = lambda *_a, **_k: {"ambiguous_width": "narrow"}
        if prev is None:
            os.environ.pop(env, None)
        else:
            os.environ[env] = prev
        err = io.StringIO()
        code = None
        try:
            with contextlib.redirect_stderr(err):
                try:
                    cl.run_client(sock)
                except SystemExit as e:
                    code = e.code
        finally:
            (cl.build_client_app, cl._relaunch_self, cl.time, cl.load_config, old) = saved
            after = os.environ.get(env)
            if old is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = old
        return {"prev": prev, "elapsed": elapsed, "crash": crash,
                "relaunch": relaunched[0] if relaunched else None, "exit": code,
                "env_after": after,
                "stderr": err.getvalue().replace(crash_log, "{path}")}

    import time as _time
    time_strftime = _time.strftime
    for prev in (None, "0", "4", "5", "x"):
        for elapsed in (1.0, out["window"] + 1.0):
            out["cases"].append(case(prev, elapsed))
    out["cases"].append(case("3", 1.0, crash=False))
    # 기록 머리줄의 모양(날짜·pid·판 자리를 지운 것) — `base::crash::log_header` 와 맞댄다.
    import re
    with open(crash_log, encoding="utf-8") as fp:
        head = fp.read().split("\n")[1]
    out["log_header"] = re.sub(
        r"^==== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d pid=\d+ ver=\S* ====$",
        "==== {stamp} pid={pid} ver={ver} ====", head)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default="..")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)
    sys.path.insert(0, root)
    os.environ.pop("NO_COLOR", None)
    from pytmuxlib import clientconn as cc, i18n

    class Stub(cc._NetReconnectMixin):
        def __init__(self, **kw):
            self.__dict__.update(dict(
                _conn_gen=1, _force_reconnecting=False, _reconnecting=False,
                _relaunch_on_restart=False, _relaunch=False, net_auto_reconnect=True,
                writer=None, reader=None, sock_path="/x", _net_degraded=False,
                _net_ping_ts=None, _net_bad=0, _net_good=0, session_name=None,
                _ending=False))
            self.__dict__.update(kw)
            self.log = []

        def exit(self, message=None, **_k):
            self.log.append({"exit": message, "relaunch": bool(self._relaunch)})

        def display_message(self, m, **_k):
            self.log.append({"msg": m})

        def run_worker(self, coro, exclusive=False):
            coro.close()

        def _composite(self):
            pass

        def _hello_msg(self):
            return {}

        def _dispatch(self, m):
            pass

    async def eof(_reader):
        return None

    async def wm(_w, _m):
        pass

    def scenario(name, *, steps, connect_ok=True, windows=False, **flags):
        """steps: 'eof'(리더 EOF) · 'force'(수동 재접속) 의 차례."""
        calls = {"n": 0}

        async def oc(_p, **_k):
            calls["n"] += 1
            if connect_ok:
                return object(), object()
            raise ConnectionError("거절")

        st = Stub(**flags)
        st._use_execv_relaunch = lambda: not windows
        saved = (cc.read_msg, cc.write_msg, cc.ipc.open_connection, cc._RECONNECT_DELAY)
        cc.read_msg, cc.write_msg, cc.ipc.open_connection, cc._RECONNECT_DELAY = eof, wm, oc, 0
        out = []
        try:
            for step in steps:
                before = calls["n"]
                st.log = []
                if step == "eof":
                    asyncio.run(st._reader_task(None, st._conn_gen))
                elif step == "force":
                    asyncio.run(st._force_reconnect("manual"))
                out.append({"step": step, "attempts": calls["n"] - before, "log": st.log})
        finally:
            cc.read_msg, cc.write_msg, cc.ipc.open_connection, cc._RECONNECT_DELAY = saved
        return {"name": name, "connect_ok": connect_ok, "windows": windows,
                "flags": {k: v for k, v in flags.items()}, "steps": out}

    scenarios = [
        scenario("drop_ok", steps=["eof"]),
        scenario("drop_fail", steps=["eof"], connect_ok=False),
        scenario("auto_off", steps=["eof"], net_auto_reconnect=False),
        scenario("drop_window", steps=["eof"] * 7),
        scenario("restart_ok", steps=["eof"], _reconnecting=True),
        scenario("restart_fail", steps=["eof"], _reconnecting=True, connect_ok=False),
        scenario("restart_relaunch_posix", steps=["eof"], _reconnecting=True,
                 _relaunch_on_restart=True),
        scenario("restart_relaunch_windows", steps=["eof"], _reconnecting=True,
                 _relaunch_on_restart=True, windows=True),
        scenario("force_ok", steps=["force"]),
        scenario("force_fail", steps=["force"], connect_ok=False),
        scenario("ending_guard", steps=["eof"], _ending=True),
    ]
    payload = {
        "_comment": "python3 scripts/gen_lifecycle_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientconn.py(_reader_task·_reconnect·_drop_reconnect_ok·"
                    "_reconnect_after_drop·_force_reconnect·_net_sample)와 client.py(run_client)를 "
                    "스텁 위에서 실제로 돌린 결과.",
        "degraded": _degraded_scenarios(cc, root),
        "crash": _crash_scenarios(root),
        "constants": {
            "delay": cc._RECONNECT_DELAY,
            "restart": cc._RECONNECT_RETRIES_RESTART,
            "force": cc._RECONNECT_RETRIES_FORCE,
            "drop": cc._RECONNECT_RETRIES_DROP,
            "drop_window": cc._DROP_RECONNECT_WINDOW,
            "drop_max": cc._DROP_RECONNECT_MAX,
        },
        "texts": {k: {"ko": i18n._CATALOG["ko"][k], "en": i18n._CATALOG["en"][k]}
                  for k in KEYS},
        "scenarios": scenarios,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 시나리오 {len(scenarios)}개")


if __name__ == "__main__":
    main()
