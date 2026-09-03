"""pytmux-382 §8-⑤ — `:debug-stats` 가 실제로 재는가(`pytmuxlib/clientdiag.py`).

이 이슈의 첫 산출물은 고침이 아니라 **재는 법**이다: 제보(*"수 일 띄워놓으면 점점
느려진다"*)의 재현이 며칠이라, 계측 한 줄이 없으면 다음 사람도 며칠을 다시 태운다.
그 한 줄은 **한 번 지어졌다가 사라졌다**(박제 CL 73983 이 p4d 에서 소실 · depot 에
한 번도 안 들어갔다). 다시 지었고, 여기서 그것이 재는지를 잰다.

되돌리면 실패해야 하는 오라클:
  · 다섯 축 중 하나라도 빠지면 → test_a_sample_carries_the_five_axes
  · 기본에서 `gc.collect()` 를 부르면 → test_it_does_not_collect_unless_asked
    (⛔ 진단이 제 손으로 수십 ms 멎게 하면 재려던 증상을 자기가 만든다)
  · 종류를 `__qualname__` 만으로 묶으면 → test_types_are_keyed_by_module_too
  · 기준선을 표에서 빼면 → test_the_table_carries_the_baseline
  · **명령이 그 팝업을 안 띄우면** → test_debug_stats_command_opens_the_popup
    (값 만드는 함수만 재는 시험은 「호출 제거」 뮤테이션에 공허 통과한다 — 이
    저장소가 그 함정을 두 번 밟았다)
"""
import gc
import sys

import harness  # noqa: F401  (러너 위생 · 러너가 이 모듈을 import 로 잡는다)
from pytmuxlib import clientdiag


class _App:
    """계측이 앱에서 읽는 것은 두 칸뿐이다 — 그 최소를 흉내낸다."""
    screen_stack = ["Screen"]


def test_a_sample_carries_the_five_axes():
    """2026-08-25·09-01 계측이 정한 축 그대로다 — 다음 사람이 「같은 기제인가」를
    견줄 수 있어야 이 명령이 값을 한다."""
    s = clientdiag.collect_stats(_App())
    for k in ("objects", "generations", "screen_depth", "timers", "top"):
        assert k in s, k
    assert s["objects"] > 0
    assert s["screen_depth"] == 1
    assert [g["gen"] for g in s["generations"]] == [0, 1, 2]
    assert s["top"] and all(isinstance(n, int) for _, n in s["top"])


def test_it_does_not_collect_unless_asked():
    """⛔ 전체 수거는 단일 스레드 앱을 수십 ms 멎게 한다(이 이슈 실측 18→84 ms).
    진단이 그것을 제 손으로 만들면 **재려던 증상을 자기가 만든다.**"""
    before = gc.get_stats()[2]["collections"]
    s = clientdiag.collect_stats(_App())
    assert s["collected_now"] is False
    assert s["gc_collect_ms"] is None
    assert gc.get_stats()[2]["collections"] == before, "기본이 수거를 불렀다"


def test_asking_for_a_collection_measures_it():
    """대조군 — `-c` 를 주면 실제로 부르고 그 시간을 낸다(안 그러면 위 시험은
    「늘 안 부른다」로도 통과한다)."""
    before = gc.get_stats()[2]["collections"]
    s = clientdiag.collect_stats(_App(), collect=True)
    assert s["collected_now"] is True
    assert isinstance(s["gc_collect_ms"], float) and s["gc_collect_ms"] >= 0
    assert gc.get_stats()[2]["collections"] > before


def test_types_are_keyed_by_module_too():
    """⛔ `__qualname__` 만으로 묶으면 서로 다른 모듈의 같은 이름이 한 열쇠에
    겹친다 — 2026-08-25 계측이 실제로 그 함정을 밟아 `ScrollBar` 가 새는 것처럼
    보였다."""
    class Dup:
        pass
    counts = clientdiag.type_counts([Dup(), Dup(), object()])
    keys = list(counts)
    assert any(k.endswith(".Dup") and "." in k[:-4] for k in keys), keys
    assert "builtins.object" in counts


def test_generations_report_frequency_not_just_size():
    """★ 2026-09-01 계측의 결론이 이 칸에 걸린다: *gen2 **빈도**는 안 변한다*.
    빈도를 안 내면 그 결론을 다음 상자에서 다시 잴 수 없다."""
    gens = clientdiag.gc_generations()
    assert len(gens) == 3
    for g in gens:
        assert "collections" in g and isinstance(g["collections"], int)


def test_rss_unit_is_not_off_by_1024():
    """⚠ `ru_maxrss` 는 macOS 가 **바이트**, Linux 가 **KiB** 다. 틀리면 표가
    1024배 어긋난 채 「메모리가 폭증했다」로 읽힌다."""
    n = clientdiag.rss_bytes()
    if n is None:
        return
    # 이 러너가 1 MB 미만이거나 100 GB 이상일 수는 없다 — 단위가 어긋나면 걸린다.
    assert 1_000_000 < n < 100_000_000_000, (n, sys.platform)


def test_the_table_carries_the_baseline():
    """숫자 하나만 보면 「많은 건가」를 알 수 없다 — 이 명령은 그것을 알려주려고
    있다. 기준선(2026-09-01 실측)을 표에 함께 싣는다."""
    lines = clientdiag.render(clientdiag.collect_stats(_App()))
    text = "\n".join(lines)
    assert "기준선" in text
    assert f"{clientdiag.BASELINE['objects_settled']:,}" in text
    assert "눕는다" in text, "「자라다 눕는다」가 이 이슈의 판정 기준이다"
    assert "산 객체 상위" in text


def test_the_table_says_when_it_did_not_collect():
    """안 잰 것을 잰 것처럼 보이면 안 된다."""
    lines = clientdiag.render(clientdiag.collect_stats(_App()))
    assert any("안 불렀다" in ln for ln in lines), lines


async def test_debug_stats_command_opens_the_popup():
    """★ **호출부를 겨눈다.** 값 만드는 함수만 재면 그 값을 붙이는 호출을 지워도
    통과한다(이 저장소 실측 2회 — 공허 통과). 뮤테이션에 「호출 제거」를 포함한다."""
    from test_client import _with_app, wait_mounted
    from pytmuxlib.clientscreens import InfoScreen

    async def body(app, pilot, srv):
        app._run_command("debug-stats")
        scr = await wait_mounted(pilot, InfoScreen)
        assert isinstance(app.screen, InfoScreen)
        text = "\n".join(str(x) for x in scr._lines)
        assert "산 객체" in text, text[:400]
    await _with_app(body)
