"""qa/scenarios/ — 시나리오 등록소. **이 파일이 SSOT 다.**

여기 없는 시나리오는 안 돈다. 파일만 만들고 등록을 잊는 것이 형제 프로젝트에서도 가장
흔한 실수라, `tests/test_qa_layer.py` 가 **디렉터리와 등록소를 기계로 대조**한다 —
파일이 있는데 등록이 없으면(또는 그 반대면) 붉어진다.

시나리오 계약:

    NAME  = "T0-core-loop"     # 트래커가 `^T\\d` 로 티어 라벨을 뽑는다 — 티어로 시작할 것
    TIER  = "T0"
    TITLE = "…"
    def run(ctx) -> None       # ctx.step(…) 으로 스텝을 열고, ctx.fail(…) 로 결함을 낸다
"""
from __future__ import annotations

from . import t0_core_loop, t1_commands, t2_multi_client, t3_gui_window

#: 도는 순서 그대로. 티어가 낮은 것이 먼저다(T0 가 깨지면 위 티어의 결과는 뜻이 없다).
REGISTRY = (t0_core_loop, t1_commands, t2_multi_client, t3_gui_window)


def by_tier(tier: str | None):
    if not tier:
        return REGISTRY
    return tuple(s for s in REGISTRY if s.TIER == tier)


def by_name(name: str | None):
    if not name:
        return REGISTRY
    return tuple(s for s in REGISTRY if s.NAME == name)
