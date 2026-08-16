"""T2 다중 클라 동시 접속 — **우리 고유의 핵심**(pytmux/pytmux-146).

pytmux 는 단일 서버 · 다중 클라다. 형제 프로젝트(`space`·`space-truck-simulator`)의
T2(동시접속·경합)에 해당하는 자리가 여기서는 **제품의 정체 그 자체**다 — 두 클라가 같은
서버에 붙어 서로의 조작을 보는 것이 이 도구의 존재 이유다. 그런데 T0·T1 은 클라를
**한 번에 하나만** 띄운다(`capture_client` 는 한 프로세스를 상한까지 붙들고 있다가
돌려준다). 그래서 그 전까지 **브로드캐스트가 통째로 한 클라에만 가도 QA 는 초록**이었다.

⚠ **`tests/test_client_capacity.py` 와 층이 다르다.** 그쪽은 가짜 writer 로 `clients`
리스트를 채워 `_flush_to_client` 를 직접 부르는 **헤드리스 단언**이고(N-클라 용량·느린
소비자·선형 비용), 여기는 **진짜 클라 프로세스 N 개가 진짜 PTY 아래서 실제로 그린
화면**이다. 저쪽이 초록인 채로 여기가 붉을 수 있다 — 프레임을 보냈다는 것과 그 화면이
그려졌다는 것은 다른 사건이고, 이 저장소가 `client/CLAUDE.md` 에 적어 둔 「GUI 배선
누락은 라이브 스크린샷만이 잡는다」가 바로 그 간격이다.

## 재는 것 넷

⑴ **함께 뜬다** — N 개가 같은 시각에 붙어 각자 화면을 그린다(살아 있고 · 트레이스백 없고).
⑵ **브로드캐스트** — 제3자(제어 라인)가 트리를 바꾸면 **전원의** 화면이 따라온다.
⑶ **델타 정합** — 한 클라에 친 키의 결과가 **다른 클라의 화면에도** 그려진다. 첫 클라와
   마지막 클라 **양쪽에서** 친다 — 「먼저 붙은 클라만 입력이 먹는다」는 부류가 있다.
⑷ **생존** — 하나를 SIGKILL 해도 서버 세션과 **나머지 클라**가 살고, 그 뒤의 변경도 계속
   받는다. 그리고 전원이 사라져도 서버는 산다.

## ⛔ 함정 (고치기 전에 읽을 것)

- **단계마다 마커가 달라야 한다.** `Multi.text(i)` 는 그 자식이 **지금까지 낸 전부**라
  (현재 화면이 아니다) 같은 마커를 두 번 쓰면 앞 단계의 흔적이 뒤 단계를 통과시킨다.
- **치는 글자와 단언하는 글자가 달라야 한다** — `echo PYT""MUX_…` 로 친다. 같으면
  클라가 아직 raw 모드를 안 잡았을 때 tty 로컬 에코만으로 통과한다(pytmux-141 이
  실측으로 겪은 거짓 초록. `tests/test_ptyshot.py` 머리말에 그 계측이 있다).
- **이름으로 죽이지 않는다** — 클라를 늘리면 정리도 는다. `Multi` 는 자기가 판 pid 만
  겨냥한다(안전 규율 ⑵ · `tests/test_qa_layer.py` 가 AST 로 지킨다).
- **Windows 에서는 통째로 미검증이다** — `ptyshot` 이 POSIX 전용이라 여기서 클라를 못
  띄운다. ⛔ 그때 조용히 빠지지 않고 **스텝마다 사유를 단 SKIP** 을 낸다(원칙 ⓑ).
  그 구멍은 `pytmux/pytmux-152` 가 이미 이름 대서 적어 둔 것이다.
"""
from __future__ import annotations

import time

from .. import screens
from ..session import NotSupported

NAME = "T2-multi-client"
TIER = "T2"
TITLE = "다중 클라 동시 접속 — 브로드캐스트 · 델타 정합 · 생존"

#: 동시에 붙이는 클라 수. ⚠ **둘로는 부족하다** — 둘이면 「첫 클라 + 나머지 하나」와
#: 「전원」이 같은 모양이라, 팬아웃이 첫 하나에만 가는 결함과 마지막 하나를 빠뜨리는
#: 결함이 구별되지 않는다. 셋이면 갈린다(그리고 하나를 죽여도 생존자가 둘 남는다).
CLIENTS = 3

#: 스텝 이름. ⛔ 한 벌로 둔다 — Windows 에서 통째로 미검증을 낼 때 **같은 이름들**로
#: 회계해야 리포트가 「무엇을 못 쟀나」를 정확히 말한다.
STEPS = ("attach", "broadcast", "delta-mirror", "one-dies", "detach-all", "kill-server")

#: 클라가 화면을 그릴 때까지의 상한(초). 실측 약 1.0초라 넉넉하지만 무한은 아니다 —
#: 안 뜨는 것 자체가 결함이므로 **기다리다 끝나면 안 된다**.
DRAW_TIMEOUT = 25.0
#: 한 클라의 조작이 다른 클라 화면에 닿을 때까지의 상한(초). 실측 0.1초 안쪽이다.
SYNC_TIMEOUT = 15.0
#: SIGKILL 한 클라를 서버가 정리할 틈. 이 뒤에 남은 쪽이 계속 받는지를 잰다.
REAP_SETTLE = 1.0

#: 단계별 마커. ⛔ **겹치면 안 된다**(위 함정 첫 줄).
MARK_BROADCAST = "qat2b"          # 제3자 rename 이 전원 탭바에 뜨나
MARK_SURVIVOR = "qat2s"           # 하나가 죽은 뒤에도 나머지가 받나
#: 키 입력 델타. `(치는 글자, 화면에서 찾을 글자)` — 반드시 달라야 한다.
TYPED = ('echo PYT""MUX_T2_FIRST\n', "PYTMUX_T2_FIRST")
TYPED_LAST = ('echo PYT""MUX_T2_LAST\n', "PYTMUX_T2_LAST")

for _typed, _marker in (TYPED, TYPED_LAST):
    # ⛔ **불러올 때 못 박는다** — `assert` 로 두면 `python -O` 에서 사라지고, 그러면
    #    누가 따옴표를 지운 날 이 시나리오가 tty 에코만으로 통과한다(거짓 초록).
    if _marker in _typed:
        raise ValueError(f"치는 글자가 마커를 품는다 — 에코만으로 통과한다: {_typed!r}")


def run(ctx) -> None:
    s = ctx.session
    try:
        multi = s.clients(CLIENTS)
    except NotSupported as e:
        # ⛔ 조용히 빠지지 않는다 — 못 잰 것은 **스텝마다** 사유를 달아 미검증으로
        #   회계한다(원칙 ⓑ · rc 3). 통과와 같은 모양이 되면 이 티어는 없는 것과 같다.
        for name in STEPS:
            with ctx.step(name) as st:
                st.skip(f"{e} — 다중 클라를 못 띄운다(pytmux/pytmux-152)")
        return

    try:
        _attach(ctx, multi)
        _broadcast(ctx, s, multi)
        _delta_mirror(ctx, multi)
        _one_dies(ctx, s, multi)
    finally:
        multi.close()                       # 우리가 쥔 pid 로만 회수한다(규율 ⑵)

    _detach_all(ctx, s)
    _kill_server(ctx, s)


# ── 스텝 ──────────────────────────────────────────────────────────────────────

def _attach(ctx, multi) -> None:
    """N 개가 **같은 시각에** 붙어 각자 화면을 그리는가."""
    with ctx.step("attach"):
        # ⛔ 기다리는 조건은 **탭바**다(테두리가 아니다). 클라는 서버 프레임을 하나도
        #    못 받아도 자기 껍데기(테두리)는 그린다 — 실측(2026-08-09 뮤테이션): 첫
        #    클라에만 보내도 나머지 둘의 화면에 테두리가 있었다. 「떴다」를 「붙었다」로
        #    읽으면 이 스텝은 통째로 장식이 된다.
        drew = multi.wait(lambda ts: all(screens.tabbar(t) for t in ts), DRAW_TIMEOUT)
        _judge_screens(ctx, _snapshot(multi), key_prefix="attach")
        if not drew:
            # 위 판정이 이미 무엇이 비었는지 말한다. 여기서는 **상한을 넘겼다**는
            # 사실만 더한다 — 느린 것과 안 뜨는 것은 다른 결함이다.
            blank = [lbl for lbl, _alive, text in _snapshot(multi)
                     if not screens.tabbar(text)]
            if blank:
                ctx.fail(oracle="multi/attach_together", key="draw-timeout", severity="S2",
                         title=f"클라 {CLIENTS} 개를 동시에 붙이면 일부가 화면을 안 그린다",
                         expected=f"{DRAW_TIMEOUT:.0f}초 안에 전원이 그린다",
                         actual=f"안 그린 클라: {', '.join(blank)}")


def _broadcast(ctx, s, multi) -> None:
    """제3자가 트리를 바꾸면 **전원의** 화면이 따라오는가."""
    with ctx.step("broadcast"):
        s.control("new-window")
        s.control(f"rename-tab {MARK_BROADCAST}")
        multi.wait(lambda ts: all(MARK_BROADCAST in t for t in ts), SYNC_TIMEOUT)
        _judge_mirror(
            ctx, _labelled(multi), MARK_BROADCAST,
            oracle="multi/broadcast", key="rename-all",
            title="트리 변경이 붙어 있는 클라 전원에게 안 간다",
            expected=f"붙어 있는 클라 {CLIENTS} 개 전부의 탭바에 "
                     f"`{MARK_BROADCAST}` 가 그려진다")


def _delta_mirror(ctx, multi) -> None:
    """한 클라에 친 키의 **결과**가 다른 클라 화면에도 그려지는가.

    ⚠ 첫 클라와 **마지막** 클라 양쪽에서 친다 — 입력 경로가 먼저 붙은 클라에만 이어진
    부류는 첫 클라만 쳐 보면 안 잡힌다(그리고 그 부류가 다중 클라에서 가장 흔하다).
    """
    with ctx.step("delta-mirror"):
        for who, (typed, marker) in ((0, TYPED), (len(multi) - 1, TYPED_LAST)):
            multi.feed(who, typed.encode())
            # ⚠ 마커를 기본인자로 묶는다 — 루프 변수를 그대로 닫으면 다음 회차의 값을
            #   보는 부류(late binding)가 생기고, 그 버그는 **조용히 통과**로 나타난다.
            multi.wait(lambda ts, m=marker: all(m in t for t in ts), SYNC_TIMEOUT)
            _judge_mirror(
                ctx, _labelled(multi), marker,
                oracle="multi/delta_mirror", key=f"typed-from-{who}",
                title=f"클라 {who} 에 친 키의 결과가 다른 클라 화면에 안 그려진다",
                expected=f"클라 {who} 가 친 명령의 **출력**이 클라 {CLIENTS} 개 "
                         f"전부의 화면에 그려진다")


def _one_dies(ctx, s, multi) -> None:
    """하나가 죽어도 서버 세션과 **나머지 클라**가 사는가."""
    with ctx.step("one-dies"):
        before = s.tree()
        multi.kill(0)
        time.sleep(REAP_SETTLE)

        after = s.tree()
        if after != before:
            ctx.fail(oracle="multi/survives_one_death", key="tree-after-death",
                     severity="S1",
                     title="클라 하나가 죽으면 서버 트리가 함께 바뀐다",
                     expected=f"클라 하나가 죽어도 {before[0]} tabs, {before[1]} panes",
                     actual=f"{after[0]} tabs, {after[1]} panes")

        dead = [f"클라 {i}" for i in range(1, len(multi)) if not multi.alive(i)]
        if dead:
            ctx.fail(oracle="multi/survives_one_death", key="siblings-died", severity="S1",
                     title="클라 하나가 죽으면 남은 클라도 함께 죽는다",
                     expected=f"클라 0 을 SIGKILL 해도 나머지 {len(multi) - 1} 개는 산다",
                     actual=f"함께 죽은 것: {', '.join(dead)}")

        # ★ 「살아 있다」와 「계속 받는다」는 다른 사건이다 — 굳은 화면도 살아는 있다.
        s.control(f"rename-tab {MARK_SURVIVOR}")
        survivors = [(f"클라 {i}", i) for i in range(1, len(multi))]
        multi.wait(lambda ts: all(MARK_SURVIVOR in ts[i] for _lbl, i in survivors),
                   SYNC_TIMEOUT)
        _judge_mirror(
            ctx, [(lbl, multi.text(i)) for lbl, i in survivors], MARK_SURVIVOR,
            oracle="multi/survivor_keeps_updating", key="survivor-frozen",
            title="클라 하나가 죽으면 남은 클라 화면이 굳는다",
            expected=f"생존 클라 전부의 탭바에 `{MARK_SURVIVOR}` 가 그려진다")


def _detach_all(ctx, s) -> None:
    """전원이 사라져도 **서버 세션은 산다** — 이 제품의 첫째 계약의 다중 클라판.

    ⚠ 호출부가 이미 `multi.close()` 로 전원을 회수한 뒤다(그래서 이 스텝에는 클라가
    없다). T0 의 재부착이 「하나가 죽어도」를 잰다면 여기는 「**전부** 죽어도」다.
    """
    with ctx.step("detach-all"):
        tabs, panes = s.tree()
        if (tabs, panes) == (0, 0):
            ctx.fail(oracle="multi/session_outlives_all_clients", key="empty-after-detach",
                     severity="S1",
                     title="클라가 전부 사라지면 서버 세션도 사라진다",
                     expected="붙었던 클라가 전부 죽어도 세션과 트리가 남는다",
                     actual=f"{tabs} tabs, {panes} panes")


def _kill_server(ctx, s) -> None:
    with ctx.step("kill-server"):
        s.stop()
        left = ctx.slot.residue()
        if left:
            ctx.fail(oracle="lifecycle/clean_exit", key="t2-residue", severity="S3",
                     title="kill-server 뒤에도 슬롯에 살아 있는 것이 남는다",
                     expected="슬롯에 응답하는 서버도, 살아 있는 pty-host 도 없다",
                     actual="; ".join(left))


# ── 판정 ──────────────────────────────────────────────────────────────────────
#
# ⛔ **판정은 순수 함수로 둔다.** 실 클라를 띄우지 않고도 「오라클이 정말로 무는가」를
#    변이로 잴 수 있어야 하고(`tests/test_qa_layer.py`), 안 물면 이 티어는 초록을 파는
#    장식이다 — 받아들임 기준이 그것을 이름 대서 요구한다(pytmux-146).

def _snapshot(multi) -> list[tuple[str, bool, str]]:
    """`[(라벨, 살아있나, 화면글)]` — 지금 이 순간의 전원."""
    return [(f"클라 {i}", multi.alive(i), multi.text(i)) for i in range(len(multi))]


def _labelled(multi) -> list[tuple[str, str]]:
    """`[(라벨, 화면글)]` — 마커 판정에 쓰는 재료."""
    return [(f"클라 {i}", multi.text(i)) for i in range(len(multi))]


def _judge_screens(ctx, snapshot, *, key_prefix: str) -> None:
    """붙은 클라 **전원**의 화면을 본다.

    ⚠ 판정 재료를 좁게 고른다(T0 `_judge_screen` 과 같은 규율) — 화면 전체를 골든으로
    굳히면 시각·호스트명·셸 프롬프트가 들어와 매 런 붉어진다(위양성 = 원칙 ⓓ).
    여기서 보는 셋은 그 셋 다 아니다: 살아 있었나 · 트레이스백을 토했나 · 무언가 그렸나.

    ⛔ **첫 클라에서 멈추지 않는다** — 전원을 재고 결함도 클라마다 하나씩 낸다.
       「어느 하나가 깨졌다」로 접으면 지문이 하나라 두 번째 클라만 깨진 날 이슈가
       엉뚱한 것을 가리킨다.
    """
    for i, (label, alive, text) in enumerate(snapshot):
        if not alive:
            ctx.fail(oracle="client/alive", key=f"{key_prefix}-{i}-died", severity="S1",
                     title="동시에 붙인 클라가 스스로 종료한다",
                     expected=f"{label} 가 붙어 있는 동안 살아 있다",
                     actual="스스로 종료(즉시 종료/크래시 신호)")
            continue
        if "Traceback (most recent call last)" in text:
            ctx.fail(oracle="client/no_traceback", key=f"{key_prefix}-{i}-traceback",
                     severity="S1",
                     title="동시에 붙인 클라 화면에 파이썬 트레이스백이 그려진다",
                     expected=f"{label} 화면에 트레이스백이 없다",
                     actual=screens.around(text, "Traceback (most recent call last)"))
            continue
        if not screens.tabbar(text):
            ctx.fail(oracle="client/renders_tree", key=f"{key_prefix}-{i}-blank",
                     severity="S2",
                     title="동시에 붙인 클라 화면에 탭바가 안 그려진다",
                     expected=f"{label} 화면에 세션의 탭바가 있다",
                     actual=f"화면 {len(text)}자에 탭바가 없다 · "
                            f"테두리는 {'있다' if screens.drawn(text) else '없다'}"
                            f"(테두리만 있으면 껍데기만 뜨고 세션은 못 받은 것이다)")


def _judge_mirror(ctx, labelled, marker: str, *, oracle: str, key: str,
                  title: str, expected: str, severity: str = "S1") -> None:
    """마커를 **못 받은 클라**를 전부 이름 대서 신고한다.

    ★ 이 함수가 T2 의 오라클이다. 브로드캐스트가 한 쪽에만 가면 여기서 문다 —
    받아들임 기준의 뮤테이션(`tests/test_qa_layer.py`)이 겨누는 자리가 정확히 여기다.
    ⛔ **「하나라도 받았으면 통과」로 접지 마라** — 그 판정은 클라가 하나일 때와 같은
       모양이고, 그러면 이 티어 전체가 뜻을 잃는다.
    """
    missing = [label for label, text in labelled if marker not in text]
    if not missing:
        return
    got = [label for label, text in labelled if marker in text]
    ctx.fail(oracle=oracle, key=key, severity=severity, title=title,
             expected=expected,
             actual=f"`{marker}` 를 못 받은 클라 {len(missing)}/{len(labelled)}: "
                    f"{', '.join(missing)}"
                    + (f" · 받은 클라: {', '.join(got)}" if got else " (전원 못 받음)")
                    + "\n탭바: "
                    + " | ".join(f"{lbl}={screens.tabbar(t) or '(못 찾음)'}"
                                 for lbl, t in labelled))
