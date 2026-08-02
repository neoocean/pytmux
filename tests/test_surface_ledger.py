"""표면 대장 — **정본이 움직이면 정본의 스위트가 먼저 운다**(트리 통합 계획 §6.2).

# 왜 정본 쪽에서도 재나

`client/scripts/gen_*.py` 열여덟은 정본을 직접 import 해 픽스처를 뽑고, Rust 적합성
테스트가 그것을 읽는다. 그런데 **정본 쪽 테스트는 그 픽스처를 안 읽었다** — 그래서
`clientutil.SETTINGS` 에 줄을 하나 더해도 이 스위트는 초록이고, 어긋남은 다음에 누군가
생성기를 돌릴 때까지 잠들어 있었다. 실제로 그렇게 다섯 개가 벌어졌다(계획 §4.8:
`touch-scroll` · `copy-unwrap` · `keys.g_drag` · `attach-remote via` · `select-window wid`).
그중 `wid` 는 정본이 고친 **레이스 결함**이 클라 둘에만 남아 있었다는 뜻이다.

트리를 합친 뒤의 규율은 "정본이 바뀌면 **세 소비자가 같이 깨진다**"이다. 이 파일이 그
셋 중 첫째(정본 자신)다.

# 왜 여기서 다시 구현하지 않나

판정은 `client/scripts/check_fixtures.py` 한 벌이 한다(생성기를 전부 돌려 작업본과 대조).
여기서 표를 또 비교하면 규칙이 두 곳이 되고, 그 둘은 반드시 갈린다.

⚠ 이 테스트가 실패하면 **내 변경이 표면을 늘렸거나 바꾼 것**이다. 고치는 순서:
`python3 client/scripts/check_fixtures.py --write` → `cd client && cargo test` 로 새로
우는 적합성 테스트를 본다 → 그 표면을 두 뷰에 이관한다(둘 다, 같은 CL 에서).
"""

import os
import subprocess
import sys

import harness  # noqa: F401  (경로 설정)
from run import skip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")


async def test_the_surface_ledger_is_not_stale():
    gate = os.path.join(CLIENT, "scripts", "check_fixtures.py")
    if not os.path.isfile(gate):
        # `client/` 는 지워도 되는 트리다(Rust 클라 없이 정본만 쓰는 판) — 그때 이
        # 검사는 잴 것이 없다. 조용한 return 대신 SKIP 으로 남긴다.
        skip("client/ 가 없다 — Rust 클라 트리 없이 정본만 있는 판")
    proc = subprocess.run(
        [sys.executable, gate],
        # encoding 명시 필수: 게이트는 자기 스트림을 UTF-8 로 고정해 한글로 판정문을
        # 쓴다. 안 주면 Windows 에서 `text=True` 는 로케일(cp949/cp1252)로 읽어
        # `OK: 픽스처 21개…` 가 `OK: í”½ìŠ¤ì²˜ 21ê°œ…` 로 오고, **양성 확인이
        # 깨져** 멀쩡한 게이트가 실패로 잡힌다(실측 2026-08-01).
        cwd=CLIENT, capture_output=True, text=True,
        encoding="utf-8", errors="backslashreplace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, (
        "표면 대장이 정본과 어긋났다 — 정본이 앞서 나갔는데 클라 픽스처가 안 따라왔다.\n"
        "  고치는 순서: client/scripts/check_fixtures.py --write → cargo test →\n"
        "  새로 우는 적합성 테스트가 가리키는 표면을 **두 뷰에** 이관.\n" + out
    )
    # 양성 확인 — "0개를 재고 통과" 를 통과로 읽지 않는다.
    assert "OK: 픽스처" in out, f"게이트가 무엇을 쟀는지 안 적었다: {out!r}"


async def test_the_ledger_gate_names_its_generators():
    """게이트가 **생성기를 하나도 못 찾는** 상태를 통과로 접지 않는지.

    경로가 어긋나면(트리를 옮기거나 스크립트를 옮기면) `gen_*.py` 목록이 비고, 그때
    "대조할 것이 없다 = 통과" 로 접히면 이 게이트 전체가 조용히 죽는다. 계획 §4.7 이
    기록한 그 부류의 함정이다(이동이 픽스처 생성기의 기본 경로를 깨뜨렸다).
    """
    gate = os.path.join(CLIENT, "scripts", "check_fixtures.py")
    if not os.path.isfile(gate):
        skip("client/ 가 없다")
    scripts = os.path.join(CLIENT, "scripts")
    gens = [f for f in os.listdir(scripts) if f.startswith("gen_") and f.endswith(".py")]
    assert len(gens) >= 10, f"생성기가 {len(gens)}개뿐이다 — 경로가 어긋났을 수 있다"
