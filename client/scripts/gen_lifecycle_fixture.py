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
                    "_reconnect_after_drop·_force_reconnect)를 스텁 위에서 실제로 돌린 결과.",
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
