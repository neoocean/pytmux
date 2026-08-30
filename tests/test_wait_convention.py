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
  ② 디바운스처럼 **시간 자체가 입력**인 대기.

**②에서 '마운트 대기'는 2026-07-27j 에 빠졌다.** 못 옮기는 것이 아니라 **폴링 조건이
틀렸던 것**이다: `push_screen` 직후 `screen_stack > 1` 은 이미 참이라 0회 대기가 되고
곧이어 `query_one` 이 `NoMatches` 로 깨졌는데, 화면이 아니라 **자식이 생겼는가**를 보면
정확히 그 대기다(`harness.wait_mounted`). 뮤테이션으로 실증했다 — 그 대기를 지우면
`No nodes match 'TextArea' on ComposePromptScreen()` 로 깨지고, 새 조건으로 바꾸면
통과한다. 즉 남은 고정 pause 의 **가장 큰 덩어리가 이주 가능**하다(착수분 = 아래
`test_plugin_manager` 7→2 · `test_plugin_p4_changes` 7→1).
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
# 2026-07-30c 이주 6차(260→249): test_client 의 화면·자식 마운트 대기 11건.
# 2026-08-24: sleep 85→84. 코드를 지운 것이 아니라 **세는 법을 고쳤다** — 산문에 백틱으로
# 인용된 `await asyncio.sleep(0)` 두 줄이 부채로 잡혀 있었다(`_code` 문서). 래칫은 실측을
# 따라 같은 CL 에서 내린다(안 내리면 다음 사람이 그만큼 다시 늘릴 수 있다).
TOTALS = {"pause": 249, "sleep": 84, "silent_skip": 17}

# 모듈별 상한 [고정 pause, 고정 sleep, 조용한 플랫폼 return]. 목록에 없으면 전부 0.
CEILINGS = {
    "test_claude_resume_plugin": [8, 0, 0],
    # 2026-07-28 이주(214->182): 화면 마운트 대기 32건을 wait_mounted 로.
    # 2026-07-28 이주 5차(182->171): 단순 단언 대기를 wait_until 로.
    # 2026-07-30c 이주 6차(171->160): 화면 마운트(pause→isinstance 단언) 7건을
    # wait_mounted(screen=…)·wait_until 로, 자식 위젯 대기(pause→query_one) 4건을
    # wait_mounted(child=…) 로. 남은 160 은 "다음 입력 전 정착"·부정 단언 정착 부류다
    # (조건이 **행동 전에 이미 참**이라 폴링으로 옮기면 공허해진다 — 그 판별이 이주의
    # 실제 비용이고, 기계적 치환으로 옮길 수 있는 몫은 이 회차에서 거의 소진됐다).
    "test_client": [160, 2, 0],
    "test_clientutil": [1, 1, 0],
    # 2026-07-27g 이주(46→16): 남은 16은 앱 마운트·부정 단언 정착 대기
    "test_compose_prompt": [16, 0, 0],
    # 고정 pause 1건은 **의도된 것**: "릴리스 후에는 스크롤이 더 안 온다" 는 부정 단언이라
    # 시간 자체가 오라클이다(폴링으로 바꾸면 조건이 처음부터 참이라 공허해진다).
    "test_drag_select_scroll": [1, 0, 0],
    "test_fuzz_nest_egress": [0, 1, 2],
    "test_mdir": [1, 0, 0],
    "test_model": [0, 2, 0],
    "test_nc": [2, 0, 0],       # 2026-07-27 이주(20→2): 남은 둘은 app 마운트 대기
    "test_plugin_contract": [6, 0, 0],
    "test_plugin_ime_indicator": [4, 1, 5],
    # 2026-07-27j 이주(7→2): 남은 둘은 app 마운트·부정 단언 정착 대기
    "test_plugin_manager": [2, 0, 0],
    "test_plugin_name_sync": [6, 11, 0],
    "test_plugin_p4_changes": [1, 0, 0],   # 2026-07-27j 이주(7→1)
    "test_plugin_prompt_history": [4, 0, 0],
    "test_plugin_usage_view": [7, 0, 0],
    "test_proc": [0, 1, 0],
    "test_pty_backend": [0, 1, 0],
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
    "test_restart": [7, 8, 0],
    "test_robustness": [0, 8, 0],
    "test_security_nest_redteam": [0, 1, 2],
    "test_server": [0, 7, 3],
    # 2026-07-27f 이주(46→15): 남은 15는 run_test 직후 앱 마운트 대기
    "test_token_log_screen": [15, 0, 0],
    "test_token_saver": [0, 3, 0],
    "test_token_sync_p5": [7, 0, 0],
    # 2026-08-04(§10-21ⓔ3): 조용한 return 하나를 명시 skip 으로 옮겼다 — 이 상자에서
    # 늘 PASS 로 세어지던 자리다(POSIX 전용 드라이런).
    "test_version": [0, 0, 0],
}


# 산문에 인용된 코드를 세지 않으려고 지우는 것들 — 백틱 안(`await asyncio.sleep(0)`)과
# 줄 주석(`# … pilot.pause(1) …`).
_QUOTED = re.compile(r"`[^`]*`")
_COMMENT = re.compile(r"#.*$")


def _code(line):
    """그 줄에서 **코드만** 남긴다(백틱 인용·줄 주석 제거).

    # 왜 필요한가 (2026-08-24)

    이 게이트는 **자기 소스**를 세지 않으려고 `_SELF` 를 제외해 뒀다(위 `_scan` 문서 —
    정규식 리터럴이 스스로 매칭됐다). 같은 함정이 **다른 파일의 산문**에도 있다: 이
    저장소의 문서는 코드를 백틱으로 인용하는데(`` `await asyncio.sleep(0)` ``), 그것이
    «고정 대기» 로 세어졌다. 실측으로 `test_search_all` 의 docstring 한 줄이 그렇게
    부채로 잡혀 **모듈 상한과 총계를 동시에 넘겼다** — 코드는 한 줄도 안 늘었는데 게이트가
    붉었고, 그 붉은 줄은 다음 사람에게 「원래 그런 것」으로 읽힌다.

    ⛔ 삼중 인용 문자열 전체를 파싱하지는 않는다 — 그것까지 하려면 이 파일이 파서가 된다.
    이 저장소의 산문은 코드를 **거의 항상 백틱으로** 인용하므로 그 둘로 충분하다(안 잡히는
    잔여는 상한 표가 흡수한다).
    """
    return _COMMENT.sub("", _QUOTED.sub("", line))


def _count(path):
    """(고정 pause, 고정 sleep, 조용한 플랫폼 return)."""
    lines = open(path, encoding="utf-8").read().splitlines()
    # ⚠ **고정 대기만** 코드로 걸러 센다. 조용한 return 판정에는 원문을 쓴다 — 주석을
    #   지우면 `return  # 사유` 가 «맨 return» 이 되어 없던 부채가 생긴다(실측: 이 한
    #   줄을 안 갈라 놓았을 때 모듈 일곱이 한꺼번에 상한을 넘었다).
    code = [_code(ln) for ln in lines]
    p = sum(len(_PAUSE.findall(ln)) for ln in code)
    s = sum(len(_SLEEP.findall(ln)) for ln in code)
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
