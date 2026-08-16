"""T3 실 GUI 창 — **두 번째 사각지대**(`pytmux/pytmux-147`).

"실브라우저"에 해당하는 것이 우리에겐 둘인데(`pytmux/qa-system` §3) T0~T2 가 잡은 것은
**첫째(실 PTY · 실 Textual 클라)** 뿐이었다. 둘째 — **실 GUI 창**(Rust `pytmux-gui`) —
은 아무 자동 검사도 안 받았고, `client/CLAUDE.md` 가 그 사실을 스스로 적어 뒀다:
*"GUI 에는 아직 큐 오라클 하네스가 없다 — GUI 쪽 배선 누락은 **라이브 스크린샷만이**
잡는다"*. 이 시나리오가 그 「라이브 스크린샷」을 사람 손에서 떼어 낸다.

## ⛔ 픽셀에서 글자를 읽지 않는다

OCR 은 없다(의존성 0 · 폰트가 바뀌는 날 전건이 붉어진다). 그래서 판정 재료를 **글자를
안 읽고도 뜻이 서는 것**으로만 골랐다 — 그 술어들은 `qa/frames.py` 에 있다:

| 스텝 | 무엇을 잰다 | 오라클 |
| --- | --- | --- |
| `first-attach` | 갓 뜬 세션에 **처음** 붙은 창의 프레임 | `gui/frame_dumped` · `gui/no_alarm_banner` · `gui/draws_something` |
| `reattach` | 두 번째 부착 — **같은 세션이면 같은 그림이어야 한다** | 위 셋 + `gui/attach_is_stable` |
| `layout-mirror` | 제3자(제어 라인)가 탭·패널을 늘리면 **창이 따라오나** | 위 셋 + `gui/tree_reaches_window` |
| `key-wiring` | 창에 넣은 키가 **화면에 닿나**(팔레트 오버레이) | 위 셋 + `gui/keys_reach_window` |
| `mouse` | — | **명시 SKIP**(아래 §마우스) |
| `kill-server` | 슬롯에 남는 것이 없다 | `lifecycle/clean_exit` |

## ⚠ 문턱은 전부 실측에서 왔다 (2026-08-09 · 이 상자 · 1280x800)

⛔ **눈대중으로 고르지 않는다** — 문턱 하나가 어긋나면 이 층은 늑대소년이 되거나(원칙 ⓓ)
   공허해진다. 상수마다 그 옆에 잰 값을 적어 뒀고, 고칠 때는 **다시 재서** 고친다.

## 마우스 — 조용히 덮지 않는다

키는 제품 안으로 넣는다(`--frame-keys`). **클릭·휠·드래그는 넣을 길이 없다** —
`client/scripts/{drag_mouse_on_window,hover_on_window,wheel_on_window}.ps1` 여덟 개가
전부 PowerShell 이라 이 상자에서 안 돌고, 맥·리눅스 대응물이 없다. ⛔ 그 구멍을 통과로
접지 않고 **사유 붙은 SKIP**(rc 3 = 미검증)으로 회계한다. 헤드리스로 메울 수 있는 것은
`client/crates/gui` 의 렌더 오라클이 이미 메우고 있다(히트테스트는 레이아웃을 요구해
헤드리스로 못 세우므로 소스 오라클이 그 자리다).
"""
from __future__ import annotations

import os

from ..env import ROOT
from ..frames import NotAFrame, read_png
from ..session import NotSupported

NAME = "T3-gui-window"
TIER = "T3"
TITLE = "실 GUI 창 — 프레임 오라클(부착·트리 반영·키 배선)"

#: 창 머리줄 아래 **탭바가 사는 띠**(픽셀 줄). 머리줄 높이는 `titlebar::band_height`
#: 가 배율 1 에서 30px 이고 탭 알약이 그 바로 아래에 앉는다(실측: 탭 글자가 y≈46~62).
#: ⚠ 이 값이 어긋나도 **없는 결함을 만들지는 않는다** — 여기서 재는 것은 「탭이 늘면
#: 이 띠가 바뀐다」라 띠가 빗나가면 판정이 약해질 뿐이다(그때는 다시 재서 고친다).
TAB_BAND = (34, 74)

#: 아래쪽 알림 띠 — GUI 가 오류 배너를 그리는 자리.
NOTICE_BAND_H = 80

#: 경보색 픽셀이 이만큼 넘으면 **배너가 떴다**고 본다.
#: 실측: 건강한 프레임 0 · 「Disconnected: Frame too large …」 배너가 뜬 프레임 1226~1259.
#: ⛔ 문턱을 더 낮추면 빨간 글자 한두 자(셸 출력)가 걸린다 — 그건 제품의 오류가 아니다.
ALARM_PIXELS = 300

#: 가장 흔한 색이 이 비율을 넘으면 **통짜 한 색** = 창은 떴는데 아무것도 안 그렸다.
#: 실측: 정상 세션 화면 0.93 · 팔레트가 열린 화면 0.53.
BLANK_SHARE = 0.995

#: 같은 세션에 두 번 붙었을 때 허용하는 차이. 실측: 연속 부착 3장이 **픽셀까지 동일**
#: (0.0000) · 첫 부착이 깨졌을 때 0.0504. 시계 한 칸이 약 0.0003 이라 그 사이에 둔다.
STABLE_MAX = 0.005

#: 탭이 하나 늘었을 때 **탭바 띠**가 바뀌어야 하는 최소 비율.
#: 실측: 탭 1 → 2 에서 0.0767 · 같은 상태 두 장은 0.0000.
#: ⛔ 화면 전체로 재면 같은 변화가 0.0061 이라 시계 한 칸과 20배밖에 안 벌어진다.
TREE_DIFF_MIN = 0.01

#: 팔레트를 연 프레임이 그 전 프레임과 달라야 하는 최소 비율. 실측 1.0000
#: (오버레이가 화면 전체를 어둡게 덮는다).
KEYS_DIFF_MIN = 0.05

#: 팔레트를 여는 손. `esc` 로 어느 오버레이든 걷고 `:` 로 연다. `wait` 는 키가 아니라
#: **배치의 끝**이라 거기서 서버 왕복을 기다린다(`gui/src/main.rs` `parse_frame_keys`).
PALETTE_KEYS = "esc,:,wait,s,p"

#: 프레임 한 장의 상한(초). 실측 약 10.6초(`FRAME_DUMP_DELAY` 4초 + 기동·부착).
SHOT_TIMEOUT = 90.0

#: 스텝 → **그 프레임이 어떤 순간의 것인가**. 제목에 붙는다.
#: ★ 같은 오라클이 여러 스텝에서 물면 트래커에 **같은 제목의 이슈가 여럿** 선다 — 그러면
#: 받은 사람이 무엇이 다른지 못 읽는다. 지문은 스텝별로 갈라 두되(고치는 자리가 다를 수
#: 있다) 제목이 그 차이를 말하게 한다. ⛔ 런마다 달라지는 값을 여기 넣지 마라(제목이 흔들린다).
WHEN = {
    "first-attach": "갓 뜬 세션에 처음 붙으면",
    "reattach": "같은 세션에 다시 붙으면",
    "layout-mirror": "트리가 바뀐 뒤에 붙으면",
    "key-wiring": "키를 넣으면",
}


def run(ctx) -> None:
    s = ctx.session
    #: ★ 프레임은 **런 산출물 자리**에 남긴다 — 슬롯은 런이 끝나면 지워지므로(`slot.wipe`)
    #: 거기에 두면 결함 본문이 가리키는 그림이 판정 직후 사라진다. `qa/out/` 은 p4·git
    #: 양쪽에서 제외라 저장소를 안 부풀린다(`pytmux/qa-system` §0-5).
    shots = os.path.join(ctx.run_dir, "frames")
    os.makedirs(shots, exist_ok=True)

    # ── ① 첫 부착 ─────────────────────────────────────────────────────────────
    # ⛔ **이 한 장을 「예열」이라며 버리지 않는다.** 사람이 손으로 확인할 때는 첫 회차가
    #    깨지는 것을 알고 한 장 버리고 시작하는 관행이 있었는데, 그 관행이 곧 「사용자가
    #    처음 보는 화면을 아무도 안 본다」는 뜻이다. 사용자는 예열을 안 한다.
    with ctx.step("first-attach") as st:
        first = _judged(ctx, st, s, shots, "01-first.png")

    # ── ② 두 번째 부착 ────────────────────────────────────────────────────────
    # 같은 세션에 그대로 다시 붙은 것이라 **같은 그림**이 나와야 한다. 여기가 ①의 대조군
    # 이다 — 둘이 다르면 어느 한쪽이 세션을 잘못 그린 것이고, 그것은 부착 경로의 결함이다.
    with ctx.step("reattach") as st:
        warm = _judged(ctx, st, s, shots, "02-warm.png")
        if warm and first:
            got = warm.diff_ratio(first)
            if got > STABLE_MAX:
                ctx.fail(
                    oracle="gui/attach_is_stable", key="cold-vs-warm", severity="S2",
                    title="같은 세션에 두 번 붙었는데 창의 그림이 다르다",
                    expected=f"두 부착의 프레임 차이가 {STABLE_MAX:.1%} 미만",
                    actual=f"바뀐 픽셀 {got:.2%} — 사이에 아무 조작도 없었다 "
                           f"(프레임: {_rel(first.path)} · {_rel(warm.path)})")
        elif warm:
            # ⛔ 대조군이 없는데 조용히 넘어가지 않는다 — 넘어가면 리포트에서 이 스텝이
            #    「OK」로 보이고, 그때 안 잰 것은 아무 데도 안 적힌다(원칙 ⓑ).
            st.skip("첫 부착 프레임이 판정을 못 지나 대조군이 없다 — 부착 안정성은 미검증")

    # ── ③ 트리 변경이 창에 닿나 ────────────────────────────────────────────────
    # 제3자(제어 라인)가 트리를 바꾼다. 창은 그것을 **서버 프레임으로** 받아 그려야 한다 —
    # `client/CLAUDE.md` 가 말한 "GUI 쪽 배선 누락"이 정확히 여기서만 잡히는 부류다.
    with ctx.step("layout-mirror") as st:
        s.control("new-window")
        s.control("split-window -h")
        tabs, panes = s.tree()
        if (tabs, panes) != (2, 3):
            ctx.fail(oracle="tree/new_window", key="t3-tree", severity="S2",
                     title="제어 라인으로 늘린 트리가 서버에서부터 안 맞는다",
                     expected="2 tabs, 3 panes", actual=f"{tabs} tabs, {panes} panes")
        grown = _judged(ctx, st, s, shots, "03-tree.png")
        # ⚠ 대조군은 **판정을 지난 마지막 프레임**이다 — 직전 한 장만 보면 그것이 깨진 런에서
        #   뒤따르는 오라클이 통째로 미검증이 된다(하나 깨지면 나머지가 안 도는 도미노).
        before = warm or first
        if grown and before:
            band = grown.diff_ratio(before, *TAB_BAND)
            if band < TREE_DIFF_MIN:
                ctx.fail(
                    oracle="gui/tree_reaches_window", key="tabbar-unchanged",
                    severity="S1",
                    title="탭이 늘어도 실 GUI 창의 탭바가 그대로다",
                    expected=f"탭바 띠(y {TAB_BAND[0]}~{TAB_BAND[1]})가 "
                             f"{TREE_DIFF_MIN:.1%} 이상 바뀐다",
                    actual=f"바뀐 픽셀 {band:.2%} — 서버는 {tabs} tabs/{panes} panes 라고 "
                           f"답했는데 창은 따라오지 않았다 (프레임: {_rel(grown.path)})")
        elif grown:
            st.skip("앞선 부착 프레임이 하나도 판정을 못 지나 대조군이 없다 — "
                    "트리 반영은 미검증")

    # ── ④ 키가 창에 닿나 ──────────────────────────────────────────────────────
    with ctx.step("key-wiring") as st:
        keyed = _judged(ctx, st, s, shots, "04-palette.png", keys=PALETTE_KEYS)
        base = grown or warm or first
        if keyed and base:
            moved = keyed.diff_ratio(base)
            if moved < KEYS_DIFF_MIN:
                ctx.fail(
                    oracle="gui/keys_reach_window", key="palette-not-open",
                    severity="S1",
                    title="창에 넣은 키가 화면에 닿지 않는다(팔레트가 안 열린다)",
                    expected=f"`{PALETTE_KEYS}` 뒤 화면이 {KEYS_DIFF_MIN:.0%} 이상 바뀐다",
                    actual=f"바뀐 픽셀 {moved:.2%} — 견준 것은 {_rel(base.path)} "
                           f"(프레임: {_rel(keyed.path)})")
        elif keyed:
            st.skip("앞선 부착 프레임이 하나도 판정을 못 지나 대조군이 없다 — "
                    "키 배선은 미검증")

    # ── ⑤ 마우스 — 조용히 덮지 않는다 ──────────────────────────────────────────
    with ctx.step("mouse") as st:
        st.skip("창에 마우스를 넣는 하네스가 이 상자에 없다 — client/scripts 의 "
                "drag_mouse_on_window·hover_on_window·wheel_on_window 는 PowerShell "
                "(Windows 전용)이고 --frame-keys 에는 마우스가 없다")

    with ctx.step("kill-server"):
        s.stop()
        left = ctx.slot.residue()
        if left:
            ctx.fail(oracle="lifecycle/clean_exit", key="residue-t3", severity="S3",
                     title="GUI 를 붙였다 뗀 뒤 슬롯에 살아 있는 것이 남는다",
                     expected="슬롯에 응답하는 서버도, 살아 있는 pty-host 도 없다",
                     actual="; ".join(left))


def _rel(path: str) -> str:
    """결함 본문에 싣는 프레임 경로. ⛔ **절대경로를 싣지 않는다** — 런마다 달라지는 값이
    본문에 들어가면 트래커의 같은 이슈가 매번 다른 본문으로 갱신된다."""
    return os.path.relpath(path, ROOT)


def _judged(ctx, st, s, shots_dir, name, keys=None):
    """프레임 한 장을 뜨고 **모든 프레임에 공통인 셋**을 판정한다.

    ⛔ **첫 판정에서 걸리면 거기서 멈춘다**(T0 `_judge_screen` 과 같은 규율) — 깨진 한 장에
       오라클 셋을 다 물리면 결함 하나가 이슈 셋이 되고, 그러면 트래커에서 무엇이 원인인지
       아무도 못 읽는다. 순서는 «말해 주는 것이 많은 쪽»부터다.

    반환은 판정을 통과한 `Frame`(또는 `None`) — 뒤따르는 비교 오라클이 그것을 받는다.
    """
    path = os.path.join(shots_dir, name)
    when = WHEN.get(st.name, "")
    try:
        shot = s.gui_frame(path, keys=keys, timeout=SHOT_TIMEOUT)
    except NotSupported as e:
        st.skip(str(e))
        return None

    if not shot.ok:
        ctx.fail(oracle="gui/frame_dumped", key=f"{st.name}-no-frame", severity="S1",
                 title=f"{when} 실 GUI 창이 프레임을 못 뜬다",
                 expected="pytmux-gui 가 창을 띄우고 PNG 한 장을 남기고 정상 종료한다",
                 actual=f"{shot.why()} · 이진 {_rel(shot.binary)}")
        return None
    try:
        frame = read_png(path)
    except NotAFrame as e:
        # ⛔ 못 읽은 것을 「아무것도 안 그렸다」로 접지 않는다 — 그건 디코더의 결함이지
        #    제품의 결함이 아니다. 그래서 오라클 이름도 `qa/` 로 시작한다.
        ctx.fail(oracle="qa/frame_unreadable", key=f"{st.name}-unreadable", severity="S2",
                 title=f"{when} QA 가 GUI 프레임을 못 읽는다",
                 expected="pytmux-gui 가 낸 PNG 를 qa/frames.py 가 읽는다",
                 actual=f"{e} · 이진이 말한 크기: {shot.said}")
        return None

    if shot.said and shot.said != (frame.width, frame.height):
        ctx.fail(oracle="gui/frame_dumped", key=f"{st.name}-size-mismatch", severity="S3",
                 title="GUI 가 말한 프레임 크기와 실제 PNG 크기가 다르다",
                 expected=f"{shot.said[0]}x{shot.said[1]}",
                 actual=f"{frame.width}x{frame.height} ({_rel(path)})")
        return None

    # ★ 이 하나가 이 시나리오의 첫 수확이다 — GUI 가 사용자에게 「무언가 잘못됐다」고
    #   말하는 유일한 그림 신호를, 이제 사람 대신 오라클이 본다. T0 이 실 클라 화면에서
    #   트레이스백을 찾는 자리와 같다(거기는 글자, 여기는 색).
    alarm = frame.alarm(frame.height - NOTICE_BAND_H, frame.height)
    if alarm >= ALARM_PIXELS:
        ctx.fail(oracle="gui/no_alarm_banner", key=f"{st.name}-alarm", severity="S1",
                 title=f"{when} 실 GUI 창이 오류 배너를 띄운 채로 그려진다",
                 expected=f"아래 알림 띠에 경보색 픽셀이 {ALARM_PIXELS} 미만",
                 actual=f"경보색 픽셀 {alarm}개 — 이 창을 띄운 사용자가 화면 아래에서 "
                        f"오류 배너를 본다는 뜻이다 (프레임: {_rel(path)})")
        return None

    share = frame.dominant_share()
    if share > BLANK_SHARE:
        ctx.fail(oracle="gui/draws_something", key=f"{st.name}-blank", severity="S1",
                 title=f"{when} 실 GUI 창이 통짜 한 색으로 그려진다",
                 expected=f"가장 흔한 색이 화면의 {BLANK_SHARE:.1%} 미만",
                 actual=f"한 색이 {share:.2%} 를 덮는다 — 창은 떴는데 아무것도 "
                        f"안 그렸다 (프레임: {_rel(path)})")
        return None
    return frame
