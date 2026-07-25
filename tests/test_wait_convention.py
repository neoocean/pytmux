"""대기·SKIP 규약 래칫(로드맵 test-infra ③⑦ / §10-4⑧).

문제의 성질: `pilot.pause(0.1)` 고정 대기는 **느린 러너에서만** 깨지므로 로컬에선
초록불이다. 그래서 규약("조건 대기는 `wait_until`/`wait_for`, 플랫폼 SKIP 은 명시
`skip()`")을 문서에만 두면 지켜지지 않고 부채가 자란다 — 실측으로 로드맵 작성 당시
510건이던 고정 pause 가 그 뒤 564건까지 **늘었다**(전면 이주는 공유트리 충돌 때문에
별도 프로젝트로 유보돼 있었다).

그래서 전면 이주 대신 **래칫**을 둔다: 총계는 늘어나면 실패하고, 모듈별 상한도 못
넘는다. 목록에 없는 **새 모듈은 상한 0** 이라 새 테스트는 처음부터 규약을 따라야 한다
(= "신규/수정 테스트부터 의무화"의 기계 집행). 이주로 숫자가 줄면 이 파일의 수를
**같은 CL 에서 낮춘다** — 그게 래칫이 내려가는 유일한 방법이다.

남아 있는 고정 대기가 전부 결함은 아니다. 두 부류는 **의도된 것**이라 이주 대상이 아니다:
  ① 부정 단언의 정착 대기("아직 안 나타났다"를 보려면 실제로 기다려야 한다 — 폴링으로
     바꾸면 조건이 처음부터 참이라 즉시 통과해 오라클이 공허해진다).
  ② 마운트/디바운스처럼 **시간 자체가 입력**인 대기(예: `push_screen` 직후 스택 깊이는
     이미 참이므로 폴링은 0회 대기가 된다 — 실측으로 InfoScreen 마운트 전에 진행해
     `NoMatches` 로 깨졌다).
"""
import os
import re

import harness

HERE = os.path.dirname(os.path.abspath(__file__))

_PAUSE = re.compile(r"pilot\.pause\(\s*[0-9]")          # 고정 pilot.pause(N)
_SLEEP = re.compile(r"asyncio\.sleep\(\s*[0-9]")        # 고정 asyncio.sleep(N)
_PLAT = re.compile(r"^\s*if\s+(?:ipc\.IS_WINDOWS|not\s+ipc\.IS_WINDOWS"
                   r"|os\.name\s*[!=]=\s*[\"']nt[\"']|sys\.platform.*)\s*:\s*$")

# 총계 래칫(2026-07-25 기준 실측). **늘리지 말고 줄여라** — 이주 CL 이 여기를 함께 낮춘다.
TOTALS = {"pause": 404, "sleep": 90, "silent_skip": 18}

# 모듈별 상한 [고정 pause, 고정 sleep, 조용한 플랫폼 return]. 목록에 없으면 전부 0.
CEILINGS = {
    "test_claude_resume_plugin": [8, 0, 0],
    "test_client": [214, 2, 0],
    "test_clientutil": [1, 1, 0],
    "test_compose_prompt": [46, 0, 0],
    "test_fuzz_nest_egress": [0, 1, 2],
    "test_mdir": [1, 0, 0],
    "test_model": [0, 2, 0],
    "test_nc": [20, 0, 0],
    "test_plugin_contract": [8, 0, 0],
    "test_plugin_ime_indicator": [7, 1, 5],
    "test_plugin_manager": [7, 0, 0],
    "test_plugin_name_sync": [8, 11, 0],
    "test_plugin_p4_changes": [7, 0, 0],
    "test_plugin_prompt_history": [4, 0, 0],
    "test_plugin_usage_view": [10, 0, 0],
    "test_proc": [0, 1, 0],
    "test_pty_backend": [0, 6, 0],
    "test_ptyhost": [0, 7, 0],
    "test_ptyhost_integration": [0, 6, 0],
    "test_ptyhost_lifecycle": [0, 1, 0],
    "test_ptyhost_orphan": [0, 9, 0],
    "test_ptyhost_reattach": [0, 1, 0],
    "test_ptyhost_restart": [0, 1, 0],
    "test_ptyhost_server": [0, 4, 0],
    "test_ptyhostclient": [0, 4, 0],
    "test_pytmux_home": [0, 0, 3],
    "test_redteam": [0, 0, 2],
    "test_remote": [1, 5, 0],
    "test_restart": [9, 8, 0],
    "test_robustness": [0, 8, 0],
    "test_security_nest_redteam": [0, 1, 2],
    "test_server": [0, 7, 3],
    "test_token_log_screen": [46, 0, 0],
    "test_token_saver": [0, 3, 0],
    "test_token_sync_p5": [7, 0, 0],
    "test_version": [0, 0, 1],
}


def _count(path):
    """(고정 pause, 고정 sleep, 조용한 플랫폼 return)."""
    lines = open(path, encoding="utf-8").read().splitlines()
    p = sum(len(_PAUSE.findall(ln)) for ln in lines)
    s = sum(len(_SLEEP.findall(ln)) for ln in lines)
    sil = sum(1 for i, ln in enumerate(lines)
              if _PLAT.match(ln) and i + 1 < len(lines)
              and lines[i + 1].strip() == "return")
    return p, s, sil


_SELF = os.path.basename(__file__)


def _scan():
    """모듈별 카운트. **자기 자신은 제외** — 위 정규식 리터럴(`pilot.pause(0`)이 스스로
    매칭돼(실측) 게이트가 자기 소스를 부채로 세는 자기참조를 막는다."""
    out = {}
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("test_") and fn.endswith(".py") and fn != _SELF:
            out[fn[:-3]] = _count(os.path.join(HERE, fn))
    return out


async def test_fixed_wait_totals_do_not_grow():
    counts = _scan()
    tot = {
        "pause": sum(c[0] for c in counts.values()),
        "sleep": sum(c[1] for c in counts.values()),
        "silent_skip": sum(c[2] for c in counts.values()),
    }
    over = {k: (tot[k], TOTALS[k]) for k in TOTALS if tot[k] > TOTALS[k]}
    assert not over, (
        f"고정 대기/조용한 SKIP 이 늘었다 {over} (현재, 상한). 조건 대기는 "
        "harness.wait_until(클라)·wait_for(서버), 플랫폼 SKIP 은 `from run import skip`. "
        "의도된 정착 대기(부정 단언·마운트)면 이 파일의 상한을 올리고 **사유를 커밋에** 남길 것.")
    under = {k: (tot[k], TOTALS[k]) for k in TOTALS if tot[k] < TOTALS[k]}
    assert not under, (
        f"이주로 수치가 줄었다 {under} (현재, 상한) — 래칫을 **같은 CL 에서** 낮춰라. "
        "안 낮추면 다음 사람이 그만큼 다시 늘릴 수 있어 래칫이 헐거워진다.")


async def test_per_module_ceilings_hold_and_new_modules_start_clean():
    counts = _scan()
    bad = []
    for mod, (p, s, sil) in sorted(counts.items()):
        cap = CEILINGS.get(mod, [0, 0, 0])
        if [p, s, sil] > cap and (p > cap[0] or s > cap[1] or sil > cap[2]):
            bad.append(f"{mod}: {[p, s, sil]} > 상한 {cap}")
    assert not bad, ("모듈 상한 초과(새 모듈 상한은 0 — 신규 테스트는 처음부터 폴링 규약):\n  "
                     + "\n  ".join(bad))


async def test_helpers_exist_and_share_one_core():
    """네 헬퍼가 같은 `_poll` 을 쓰는지 — 갈라지면 스톨 감지가 한쪽에서만 사라진다."""
    import inspect
    for name in ("wait_until", "wait_until_settled", "wait_for", "wait_for_settled"):
        fn = getattr(harness, name, None)
        assert fn is not None and inspect.iscoroutinefunction(fn), name
        assert "_poll(" in inspect.getsource(fn), f"{name} 이 _poll 을 쓰지 않는다"
    src = inspect.getsource(harness._poll)
    assert "settle" in src and "return False, repr(snap)" in src, \
        "스톨 조기 반환이 코어에서 사라졌다"
