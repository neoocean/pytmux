"""결정론적 시드 퍼징(Tier 2, 신규 의존성 0) — docs/internal/SECURITY_REVIEW.md §9.

신뢰불가 바이트를 직접 받는 경계 파서 4종(`fuzz_targets`)에 시드 코퍼스 + **결정론적**
의사난수 입력(고정 seed)을 대량으로 먹여, "어떤 바이트에도 ① 예외 없음 ② 출력 한계
(치수 clamp·OSC 상한)"를 단언한다. atheris 미설치 환경에서도 매 CI 마다 도는 baseline
회귀이며, 같은 타깃을 `tests/fuzz/*.py`(atheris)가 커버리지 가이드로 더 깊게 판다.

seed 고정이라 실패는 항상 재현된다(보고된 입력을 tests/fuzz/crashes/ 에 떨궈 회귀화).
"""
import asyncio
import random

import harness  # noqa: F401 (sys.path 주입)
import fuzz_targets as ft

SEED = 20260620


def _corpus(n: int) -> list[bytes]:
    """시드 코퍼스 + 결정론적 의사난수(랜덤 바이트 절반 + VT 토큰 변이 절반)."""
    rnd = random.Random(SEED)
    out = list(ft.seed_corpus())
    vt = [b"\x1b", b"[", b"]", b"\x07", b"P", b"\\", b";", b"0", b"m", b"H",
          b"r", b"A", b"d", b"?1049", b"38;2;", b"\r", b"\n", b"\xff",
          "🌟混".encode(), b"A" * 40]
    for _ in range(n):
        if rnd.random() < 0.5:
            out.append(bytes(rnd.randrange(256)
                             for _ in range(rnd.randrange(0, 96))))
        else:
            out.append(b"".join(rnd.choice(vt)
                                for _ in range(rnd.randrange(0, 48))))
    return out


async def test_fuzz_protocol_read_msg_never_raises():
    # 와이어 길이프리픽스 JSON 파서 — 어떤 바이트든 (JSON값|None), 예외 없음.
    for data in _corpus(1500):
        await ft.aread_protocol(data)


async def test_fuzz_ptyhost_read_frame_never_raises():
    # pty-host 멀티플렉싱 프레임 파서 — None|('json',..)|('data',..), 예외 없음.
    for data in _corpus(1500):
        await ft.aread_ptyhost(data)


async def test_fuzz_clamp_dim_always_bounded():
    # 클라 치수 가드 — 임의 입력에 결과는 늘 [MIN_W, MAX_W] 정수.
    for data in _corpus(1500):
        ft.check_clamp(data)


async def test_fuzz_pane_feed_never_raises_osc_bounded():
    # 패널 PTY 출력 파서(VTTokenizer) — 통짜·분할 feed 모두 예외 없음 + OSC 상한.
    # 과다 파라미터 CSI(ESC[38;2;H 류, R2) 같은 악성 시퀀스도 파서를 못 죽인다.
    for data in _corpus(600):           # Pane 생성이 무거워 횟수만 줄임
        ft.check_vtparse(data)


async def test_fuzz_known_crashers_regression():
    # 과거 퍼징이 잡은 크래시 입력(R2: 과다 파라미터 CSI)의 명시 회귀.
    for seq in (b"\x1b[38;2;H", b"\x1b[1;2A", b"\x1b[5;10;99H",
                b"\x1b[1;2;3r", b"\x1b[?1;2;3;4h"):
        ft.check_vtparse(seq)
    # 비동기 파서도 같은 바이트에 안 죽는다.
    for seq in (b"\xff\xff\xff\xff", b"\x00\x00\x00\x05bad!!", b""):
        await ft.aread_protocol(seq)
        await ft.aread_ptyhost(seq)
