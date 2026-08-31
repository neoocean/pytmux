"""T1 명령 표 전수 — 제어 라인의 명령을 **하나도 빼지 않고 실제로 보낸다**.

T0 이 만지는 명령은 둘(`split-window -h`·`new-window`)뿐이다. 그래서 그 전까지는
**명령 하나가 통째로 죽어도 QA 는 초록**이었다 — 그것을 메우는 것이 이 시나리오다.

⚠ **여기서 재는 것은 「돌려 봤는가」다.** 철자와 disposition 은 이미
`tests/test_control_command_golden.py` 와 `test_command_table_disposition_golden` 이
AST 로 고정한다(`pytmux/qa-system` §0-1). 그 둘과 겹치면 같은 판정이 두 벌이 된다 —
여기서 더하는 것은 **진짜 데몬이 그 명령을 실제로 받아 먹었는가**다.

## 판정 셋

⑴ 서버가 **거절하지 않았나** — `unknown: <명령>` 은 그 철자가 서버에서 사라졌다는 뜻이다.
⑵ on/off 토글은 **말한 대로 답하나** — `coalesce on` → `"on"`.
⑶ 트리를 바꾸는 명령은 **트리가 그만큼 바뀌나** — 탭/패널 수로 잰다.

⛔ **깊은 검증은 여기서 하지 않는다.** 이름 변경이 실제로 어디에 반영됐는지 같은 것은
화면(실 클라 캡처) 한 자리에서만 확인하고, 나머지는 「거절 없이 수행됐다」까지다 —
넓히는 층이 깊이까지 지려 하면 매 런 흔들린다(원칙 ⓓ).

⛔ **지난 명령을 여기서 세지 않는다.** 원장은 `Session.control` 이 적는다 — 세는 자리와
보내는 자리가 갈리면 「보냈는데 안 적힌」 명령이 미커버로 신고된다.
"""
from __future__ import annotations

import time

from .. import screens
from ..session import NotSupported

NAME = "T1-commands"
TIER = "T1"
TITLE = "명령 표 전수 — 제어 라인 · 커버리지 원장"

#: 이름 변경이 **실 화면까지** 갔는지 보는 표식. 짧은 ASCII 라 탭바에서 안 잘린다.
RENAME_MARK = "qaren"

#: 작업 보존 재시작이 수렴하기를 기다리는 상한(초). 다섯 철자를 차례로 지나므로 넉넉히
#: 잡되 무한은 아니다 — 안 돌아오는 것 자체가 S1 이라 **기다리다 끝나면 안 된다**.
RESTART_TIMEOUT = 15.0

#: on/off 토글 표 — `(명령, 인자, 기대 응답)`. 서버가 setter 결과를 그대로 말한다.
#: ⚠ `exit-empty` 는 **off 로 끝낸다** — on 인 채 마지막 탭이 닫히면 서버가 스스로
#:   내려가고, 그러면 남은 스텝 전부가 「no session」이 되어 원장이 통째로 거짓말한다.
ONOFF = (
    ("coalesce", "on", "on"),
    ("coalesce", "off", "off"),
    ("coalesce-repaints", "on", "on"),
    ("nest-attach", "on", "on"),
    ("nest-attach", "off", "off"),
    ("nest-auto-attach", "on", "on"),
    ("nest-auto-attach", "off", "off"),
    ("exit-empty", "off", "off"),
)


def run(ctx) -> None:
    s = ctx.session

    with ctx.step("bootstrap"):
        # ⛔ 먼저 exit-empty 를 내린다 — 아래에서 탭을 지웠다 만드는 동안 서버가 스스로
        #   종료하면 남은 명령이 전부 「no session」이 된다(원장이 텅 빈다).
        _expect(ctx, s, "exit-empty off", "off", oracle="control/onoff", key="exit-empty")
        _tree(ctx, s, (1, 1), oracle="tree/initial", key="t1-initial",
              title="T1 첫 트리가 탭1·패널1 이 아니다")

    with ctx.step("panes"):
        _ok(ctx, s, "split-window -h")
        _ok(ctx, s, "splitw -v")
        _tree(ctx, s, (1, 3), oracle="tree/split", key="t1-split",
              title="split-window·splitw 를 둘 다 지났는데 패널이 셋이 아니다")

    with ctx.step("tabs"):
        for cmd in ("new-window", "neww", "new-tab", "newt"):
            _ok(ctx, s, cmd)
        _tree(ctx, s, (5, 7), oracle="tree/new_window", key="t1-new-window",
              title="새 탭 명령 넷을 지났는데 탭이 다섯이 아니다")
        for cmd in ("kill-window", "killw", "kill-tab", "killt"):
            _ok(ctx, s, cmd)
        _tree(ctx, s, (1, 3), oracle="tree/kill_window", key="t1-kill-window",
              title="탭 삭제 명령 넷을 지났는데 처음 탭으로 안 돌아온다")

    with ctx.step("select-and-move"):
        _ok(ctx, s, "new-window")                     # 고르고 옮길 상대를 만든다
        # ⛔ **제어 라인의 `-t` 는 탭이 아니라 세션 이름이다**(`handle_control` 이 그것으로
        #   세션을 고른다). tmux 손버릇대로 `select-window -t 1` 을 치면 「1」이라는 이름의
        #   세션을 찾다 못 찾고 **no session** 을 돌려준다 — 실측(2026-08-09, 이 시나리오의
        #   첫 런). 탭 번호는 맨 인자로 준다.
        for cmd in ("select-window", "selectw", "select-tab", "selectt"):
            _ok(ctx, s, f"{cmd} 1")
        for cmd in ("move-tab-left", "move-tab-right",
                    "move-tab-first", "move-tab-last"):
            _ok(ctx, s, cmd)
        _tree(ctx, s, (2, 4), oracle="tree/stable", key="t1-select-move",
              title="탭 선택·이동이 트리를 바꿔 버린다")

    with ctx.step("rename"):
        for cmd in ("rename-window", "renamew", "rename-tab"):
            _ok(ctx, s, f"{cmd} {cmd[:5]}x")
        _ok(ctx, s, f"renamet {RENAME_MARK}")         # 화면에서 확인할 마지막 이름

    with ctx.step("layout"):
        _ok(ctx, s, "layout-save qa1")
        _ok(ctx, s, "save-tab-layout qa2")
        _ok(ctx, s, "layout-load qa1")
        _ok(ctx, s, "load-tab-layout qa2")

    with ctx.step("toggles"):
        for cmd, arg, want in ONOFF:
            _expect(ctx, s, f"{cmd} {arg}", want, oracle="control/onoff", key=cmd)
        for line in ("single-border on", "single-border off", "pane-border off",
                     "win-mouse-motion off", "window-size latest",
                     "winsize smallest", "vt-parser pyte"):
            _ok(ctx, s, line)

    with ctx.step("send-keys"):
        # 활성 패널의 셸에 넣는다. ⚠ 실행시키지 않는다(Enter 를 안 붙인다) — 격리
        #   슬롯이어도 남의 셸에서 무엇이 도는지는 QA 가 정할 일이 아니다.
        _ok(ctx, s, "send-keys qa")
        _ok(ctx, s, "send Escape")

    with ctx.step("sessions"):
        _ok(ctx, s, "new-session -s qa-b")
        _ok(ctx, s, "new -s qa-c")
        _ok(ctx, s, "kill-session -t qa-c")
        _ok(ctx, s, "kills -t qa-b")

    with ctx.step("restart") as st:
        # ★ 작업 보존 재시작(제자리 re-exec) — 이 제품의 계약 중 가장 비싼 것이다.
        #   철자가 다섯인 것은 tmux 호환 별칭 + 「클라까지 새로 띄우는」 갈래 때문이고,
        #   ⛔ **다섯을 다 지난다** — 하나만 지나면 나머지 넷이 원장에서 미커버로 남고
        #   그건 「아무도 못 고치는 결함」이 아니라 그냥 안 지난 것이다.
        #   ⚠ 트리는 **직전 값과** 견준다. 절대 숫자로 박으면 위 스텝을 하나 고칠 때마다
        #     여기가 붉어진다(그런 관문은 곧 꺼진다).
        before = s.tree()
        for spelling in ("restart-server", "restart", "restart-all",
                         "full-restart", "restart-client-server"):
            reply = s.control(spelling)
            if reply == "unsupported":
                st.skip(f"이 상자에서는 작업 보존 재시작이 안 된다({spelling})")
                break
            if reply != "restarting":
                ctx.fail(oracle="control/restart", key=spelling, severity="S2",
                         title=f"재시작 명령이 재시작을 시작하지 못한다 — {spelling}",
                         expected="restarting", actual=reply)
                break
            after = _tree_after_restart(s, before)
            if after is None:
                ctx.fail(oracle="session/survives_restart", key=f"{spelling}-no-return",
                         severity="S1",
                         title="작업 보존 재시작 뒤 서버가 안 돌아온다",
                         expected=f"`{spelling}` 뒤 {RESTART_TIMEOUT:.0f}초 안에 "
                                  f"{before[0]} tabs, {before[1]} panes 로 응답한다",
                         actual="응답이 없거나 트리가 안 돌아왔다")
                break

    # ── 실 화면 ────────────────────────────────────────────────────────────────
    # 위 스텝들은 **서버가 자기 트리를 말한 것**이다. 여기서 한 번만 실 클라를 붙여
    # 「이름 변경이 화면까지 갔나」를 본다 — 명령이 서버 안에서만 먹고 화면에 안 닿는
    # 부류는 여기서만 잡힌다(`client/CLAUDE.md` 의 GUI 배선 누락과 같은 자리).
    with ctx.step("client-render") as st:
        try:
            # ⛔ 기다림의 조건은 **아래 판정과 같은 술어**다 — 고정 대기로 잡으면
            #    부하 회차에 아직 안 그린 화면을 「아무것도 안 그렸다」로 신고한다
            #    (pytmux-425·426·427 · `session.capture_client` 머리말).
            raw, alive, text = s.capture_client(
                until=lambda t: (screens.drawn(t) and RENAME_MARK in t)
                or screens.has_traceback(t))
        except NotSupported as e:
            st.skip(str(e))
        else:
            if not alive:
                ctx.fail(oracle="client/alive", key="t1-died", severity="S1",
                         title="실 PTY 아래 붙인 클라가 스스로 종료한다",
                         expected="캡처 시간 동안 클라 프로세스가 살아 있다",
                         actual=f"캡처 중 스스로 종료(즉시 종료/크래시 신호) · "
                                f"화면 {len(text)}자{ctx.evidence('t1', raw)}")
            elif not screens.drawn(text):
                # T0 `_judge_screen` 과 같은 규율(pytmux-149) — 「아무것도 못 그렸다」는
                # rename 이 화면까지 안 간 것과 **다른 결함**이라 지문을 가른다.
                ctx.fail(oracle="client/paints_anything", key="t1-blank",
                         severity="S2",
                         title="실 클라가 살아 있는데 화면에 아무것도 안 그렸다",
                         expected="테두리든 탭바든 클라가 그린 것이 화면에 있다",
                         actual=f"화면 {len(text)}자 · 그린 것으로 볼 만한 글자가 "
                                f"하나도 없다 — 클라는 상한까지 살아 있었는데 첫 "
                                f"프레임이 안 왔다{ctx.evidence('t1', raw)}")
            elif RENAME_MARK not in text:
                ctx.fail(oracle="client/renders_rename", key="t1-rename",
                         severity="S2",
                         title="rename-tab 이 실 클라 화면의 탭바에 안 나타난다",
                         expected=f"탭바에 `{RENAME_MARK}` 가 그려진다",
                         actual=f"화면 {len(text)}자에 없다 · "
                                f"탭바: {screens.tabbar(text) or '(못 찾음)'}"
                                f"{ctx.evidence('t1', raw)}")

    with ctx.step("kill-server"):
        # ⚠ 제어 라인의 `kill-server` 는 **실제로 서버를 내린다**(0.2초 지연 shutdown).
        #   그래서 이 스텝이 마지막이다 — 여기서 지나야 그 철자가 원장에 든다.
        _ok(ctx, s, "kill-server")
        s.stop()
        left = ctx.slot.residue()
        if left:
            ctx.fail(oracle="lifecycle/clean_exit", key="t1-residue", severity="S3",
                     title="kill-server 뒤에도 슬롯에 살아 있는 것이 남는다",
                     expected="슬롯에 응답하는 서버도, 살아 있는 pty-host 도 없다",
                     actual="; ".join(left))


# ── 판정 도우미 ───────────────────────────────────────────────────────────────

def _ok(ctx, s, line: str) -> str:
    """명령 하나를 보내고 **거절당하지 않았는지**만 본다.

    ⛔ `unknown: <명령>` 은 그 철자가 서버에서 사라졌다는 뜻이다 — 인벤토리(소스)에는
    있는데 도는 서버가 모른다면, 표와 코드가 갈린 것이고 그건 S2 다.
    """
    reply = s.control(line)
    if reply.startswith("unknown:"):
        ctx.fail(oracle="control/known", key=line.split()[0], severity="S2",
                 title=f"서버가 제어 명령을 모른다 — {line.split()[0]}",
                 expected="인벤토리에 있는 철자는 도는 서버도 안다",
                 actual=reply)
    elif reply in ("no session", "bad-line", "empty"):
        ctx.fail(oracle="control/accepted", key=line.split()[0], severity="S2",
                 title=f"제어 명령이 수행되지 않는다 — {line.split()[0]}",
                 expected="서버가 명령을 받아 수행한다",
                 actual=reply)
    return reply


def _expect(ctx, s, line: str, want: str, *, oracle: str, key: str) -> None:
    reply = _ok(ctx, s, line)
    if reply != want and not reply.startswith("unknown:"):
        ctx.fail(oracle=oracle, key=key, severity="S2",
                 title=f"토글이 말한 대로 답하지 않는다 — {line.split()[0]}",
                 expected=f"`{line}` → {want}", actual=reply)


def _tree(ctx, s, want, *, oracle: str, key: str, title: str) -> None:
    tabs, panes = s.tree()
    if (tabs, panes) != tuple(want):
        ctx.fail(oracle=oracle, key=key, severity="S2", title=title,
                 expected=f"{want[0]} tabs, {want[1]} panes",
                 actual=f"{tabs} tabs, {panes} panes")


def _tree_after_restart(s, want, timeout: float = None, step: float = 0.2):
    """재시작이 **수렴**하기를 기다린 뒤 트리를 돌려준다(못 돌아오면 `None`).

    ⛔ **`probe` 한 번으로 판정하지 않는다.** 재시작은 0.1초 뒤에 execv 하므로 명령이
    돌아온 직후의 소켓은 **아직 옛 서버**다 — 그 순간을 재면 늘 초록이고, 그건 위양성의
    거울상(거짓 초록)이다. 같은 함정을 `lifecycle/clean_exit` 가 반대 방향으로 이미
    한 번 밟았다(`pytmux/qa-system` §7): 수렴을 안 기다리고 한 번만 쟀다.

    그래서 「옛 트리와 같은 답을 **안정적으로** 준다」까지 기다린다 — 새 이미지가 resume
    상태를 채택해야 나오는 답이라, 이 폴링이 곧 작업 보존의 판정이다.
    """
    end = time.time() + (RESTART_TIMEOUT if timeout is None else timeout)
    time.sleep(0.4)                              # execv 예약(0.1초)이 지나기를 기다린다
    while time.time() < end:
        try:
            if s.tree() == tuple(want):
                return tuple(want)
        except Exception:                        # noqa: BLE001 — 교체 중엔 못 붙는다
            pass
        time.sleep(step)
    return None
