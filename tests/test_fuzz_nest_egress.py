"""레드팀 Tier 2 — NEST DCS **부작용 퍼징**(결정론적·신규 의존성 0·매 CI 실행).

NEST_AUTOATTACH_VULN_2026-06-20 §9 의 두 보강방향을 결합한다:

  · 경계 목록 확장(#2): 기존 퍼징(fuzz_targets.py)은 VT 파서를 *DoS/크래시* 경계로만
    두드렸다. 여기선 같은 신뢰 불가 패널-출력 경계를 **권한 속성**(임의 호스트 아웃바운드
    금지)으로 재분류해 두드린다.
  · 상태적 시퀀스 퍼징(#3): 단일 `feed` 가 아니라 DEST→REQ **프로토콜 시퀀스**를 문법
    으로 대량 생성하고, read 경계 분할·노이즈 삽입으로 파서 carry 상태를 흔든다.

위협모델 #4 의 핵심: 공격자는 패널 *출력 바이트*만 제어하며 **서버 비밀인 provenance
토큰을 만들 수 없다**. 따라서 현실 공격자의 알파벳은 **위조 토큰뿐**이고, 그 공간에서
egress 0 이 핵심 불변식이다(test 1). valid 토큰은 *정상 래퍼*(신뢰 경계 안)를 뜻하며
ssh-host 직결은 의도된 NEST 기능(S2 allowlist 관할) — 적대 대상이 아니다. 단,
NEW-1 의 layer-2(비로컬 `tcp:`/경로 endpoint **직결** 금지)는 valid 토큰 하에서도
성립해야 하므로 별도로 퍼징한다(test 2).

오라클은 두 겹: ① 파서는 어떤 바이트에도 예외 없음(안정성) ② egress 1차 함수
(`ipc.open_connection`·`asyncio.create_subprocess_exec`) 가로채기로 금지된 아웃바운드
0건 실측. 고정 seed 라 실패가 재현된다.
"""
from __future__ import annotations

import asyncio
import os
import random

from harness import server_only, teardown
# Step 1 의 egress 가로채기·DCS 합성기 재사용(중복 제거).
from test_security_nest_redteam import (
    _EgressMonitor, _Pty, _dcs_dest, _dcs_req, _server_token)


_FORGED_TOKENS = ["deadbeef", "", "x", "  deadbeef", "00000000"]
_DESTS = [
    "tcp:evil.com:9999", "tcp:10.0.0.5:22", "tcp:[2001:db8::1]:22",
    "tcp:evil.com:0", "tcp:evil.com:99999999", "evil.example.com",
    "user@evil", "/tmp/attacker.sock", "/etc/shadow",
    "tcp:127.0.0.1:65000", "tcp:localhost:65001", "../../escape", "",
]
_NOISE = [b"", b"\x1b]0;title\x07", b"normal output\n", b"\x07\x08",
          b"\x1bP", b">|pytmux-", b"\x1b\\", b"\x00\xff"]


def _is_nonlocal_endpoint(ep: str) -> bool:
    """open_connection 직결 목표가 로컬(unix소켓·loopback)이 아닌가 — NEW-1 layer-2
    가 막아야 하는 직결 대상."""
    return not (ep.startswith("/") or ep.startswith(
        ("tcp:127.0.0.1:", "tcp:localhost:", "tcp:[::1]:")))


def _interleave(rng, dcs: bytes) -> bytes:
    return rng.choice(_NOISE) + dcs + rng.choice(_NOISE)


async def _feed_split(srv, pane, data: bytes, rng):
    """data 를 임의 경계로 쪼개 먹인다(carry 상태 흔들기 — read 분할 모사)."""
    if len(data) <= 1 or rng.random() < 0.3:
        srv._on_pane_data(pane, data)
        return
    cuts = sorted(rng.sample(range(1, len(data)), k=min(3, len(data) - 1)))
    prev = 0
    for c in cuts + [len(data)]:
        srv._on_pane_data(pane, data[prev:c])
        prev = c


async def test_fuzz_forged_panel_output_never_egress():
    """위협모델 #4: **위조 토큰** DEST→REQ 시퀀스를 문법으로 대량 생성(분할·노이즈·
    순서뒤섞기 포함)해 실 서버 배선에 먹여도, provenance(layer 1)가 `_ssh_dest` 를
    심지 못하게 막아 **어떤 종류의 아웃바운드도 0건**. seed=1337, 400 시퀀스."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    try:
        sessA = srvA.ensure_default_session(80, 24)
        pane = sessA.active_window.active_pane
        real, pane.pty = pane.pty, _Pty()
        rng = random.Random(1337)
        with _EgressMonitor() as mon:
            for i in range(400):
                token = rng.choice(_FORGED_TOKENS)
                dest = rng.choice(_DESTS)
                selfreport = dest if rng.random() < 0.5 else "u@" + rng.choice(
                    ["other", "evil2.com", dest])
                pane._ssh_dest = ""
                pane._nest_req_ts = 0.0
                seq = [_interleave(rng, _dcs_dest(dest, token=token)),
                       _interleave(rng, _dcs_req(selfreport))]
                if rng.random() < 0.2:
                    seq.reverse()
                for chunk in seq:
                    await _feed_split(srvA, pane, chunk, rng)
                for _ in range(5):
                    await asyncio.sleep(0)
                assert pane._ssh_dest == "", (
                    f"seq#{i} 위조 토큰이 _ssh_dest 를 심음(layer 1 회귀)", dest)
                assert mon.connects == [] and mon.ssh_hosts == [], (
                    f"seq#{i} 위조 출력이 아웃바운드 유발(NEW-1 회귀)",
                    token[:4], dest, mon.connects, mon.ssh_hosts)
        pane.pty = real
    finally:
        await teardown(srvA, taskA, sockA)


async def test_fuzz_endpoint_block_never_direct_connects_nonlocal():
    """NEW-1 layer-2(심층방어): provenance 가 우회됐다고 *가정*하고(=valid 토큰을 가진
    정상 래퍼가 사용자 유도로 `tcp:evil` 을 기록한 worst-case) `_nest_do_attach` 를
    생성된 dest 공간으로 직접 구동. **비로컬 `tcp:`/경로 endpoint 가 open_connection
    직결에 도달하는 일은 결코 없다**(ssh-host 분기는 S2 allowlist 관할이라 별개)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    try:
        sessA = srvA.ensure_default_session(80, 24)
        rng = random.Random(4242)
        with _EgressMonitor() as mon:
            for i in range(200):
                dest = rng.choice(_DESTS)
                if not dest:
                    continue
                await srvA._nest_do_attach(sessA, dest)
                nonlocal_connects = [e for e in mon.connects
                                     if _is_nonlocal_endpoint(e)]
                assert nonlocal_connects == [], (
                    f"iter#{i} 비로컬 endpoint 가 직결 도달(layer 2 회귀)",
                    dest, nonlocal_connects)
        # 로컬 loopback/unix 는 *의도적으로* 직결 허용 — 경로가 살아있음을 양성 확인
        # (오라클이 모든 걸 막아 거짓통과하지 않음을 보증).
        with _EgressMonitor() as mon2:
            await srvA._nest_do_attach(sessA, "tcp:127.0.0.1:65000")
            assert mon2.connects == ["tcp:127.0.0.1:65000"], mon2.connects
    finally:
        await teardown(srvA, taskA, sockA)
