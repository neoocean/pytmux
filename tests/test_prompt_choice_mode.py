"""pytmux-634 — `:` 줄 선택지 모드의 답지(`client/scripts/gen_prompt_choices.py`)가 정본
`PromptScreen` 과 같은가 — 그리고 정본이 그 모드에서 죽지 않는가.

GUI 팔레트는 이 답지(`prompt_choices.json`)에 대고 맞춘다. 그러니 답지가 정본 프롬프트의
실제 거동과 갈리면 두 클라가 같이 틀린다 — 여기서 답지의 행을 **실제 프롬프트에 쳐서** 잰다.

되돌리면 실패해야 하는 오라클:
  · `_refresh_hint` 의 낱 문자열 선택지 펴기를 빼면 → test_every_fixture_row_matches_the_live_prompt
    (`prompt-history-lines` 에서 ValueError — 고치기 전 정본 결함)
  · 답지 생성기의 게이트를 바꾸면(예: «자기 하나뿐» 제거) → 같은 시험(`inactive-dim` 이 갈린다)
"""
import importlib.util
import os

import harness  # noqa: F401  (러너 위생 · 경로 설정)
from harness import make_app, server_only, teardown, wait_until
from textual.widgets import Input

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "..", "client", "scripts", "gen_prompt_choices.py")


def _fixture():
    spec = importlib.util.spec_from_file_location("gen_prompt_choices", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build()["rows"]


async def test_every_fixture_row_matches_the_live_prompt():
    rows = _fixture()
    assert any(not r["choice_mode"] for r in rows), "대조군(선택지 모드에 못 드는 행)이 없다"
    srv, task, sock = await server_only()
    app = make_app(sock, {})
    try:
        async with app.run_test(size=(100, 30)) as pilot:
            assert await wait_until(pilot, lambda: getattr(app, "writer", None) is not None,
                                    timeout=8)
            await pilot.press("escape")
            await pilot.press("colon")
            assert await wait_until(
                pilot, lambda: app.screen_stack[-1].__class__.__name__ == "PromptScreen")
            scr = app.screen_stack[-1]
            inp = scr.query_one(Input)
            for row in rows:
                inp.value = row["name"]
                scr._refresh_cands()
                scr._refresh_hint()
                got = [list(c) for c in scr._choices]
                if row["choice_mode"]:
                    assert got == row["choices"], (row["name"], got, row["choices"])
                else:
                    assert got == [], (row["name"], "정본은 선택지 모드가 아닌데 답지가 그렇다", got)
            # Enter 가 내는 줄 — 강조를 한 칸 옮기고 실행한다(정본 `_run_choice`).
            row = next(r for r in rows if r["name"] == "split-window")
            inp.value = "split-window"
            scr._refresh_cands()
            scr._refresh_hint()
            base = scr._choice_sel
            await pilot.press("right")
            assert await wait_until(pilot, lambda: scr._choice_sel != base)
            captured = []
            scr.dismiss = lambda v=None: captured.append(v)
            scr.on_input_submitted(type("E", (), {"value": inp.value})())
            want = row["lines"][(base + 1) % len(row["lines"])]
            assert captured == [want], (captured, want)
    finally:
        await teardown(srv, task, sock)
