"""생성기 `client/scripts/gen_plugin_screens.py` — 픽스처가 **무관한 편집에 안 흔들리나**.

# 왜 있나 (pytmux-394)

이 픽스처(`crates/proto/tests/fixtures/plugin_screens.json`)의 판정 축은 둘뿐이다:
정본이 낼 수 있는 **화면 모양의 집합**(`kinds`)과 **못 푼 자리**(`unresolved`) —
픽스처를 읽는 `crates/proto/tests/plugin_screen_conformance.rs` 가 그 둘만
역직렬화한다. 나머지 `sites` 는 *"어느 파일이 이 모양을 내나"* 라는 **참고**다.

그 참고가 종전에 **줄 번호까지** 담았고, 그래서 플러그인 파일을 한 줄만 고쳐도
픽스처가 낡아 게이트 둘(`check_fixtures` · `test_surface_ledger`)이 울었다 —
화면 모양의 집합은 하나도 안 바뀐 채로다(실측 2026-08-24: depot HEAD 가 그 이유로
이미 붉었다). 상시 적색이 가르치는 것은 「이 게이트는 원래 붉다」이고, 그러면
**진짜 갈림(새 화면 모양)이 왔을 때 아무도 안 본다.**

⛔ 그래서 여기서 재는 것은 「지금 값이 무엇인가」가 아니라 **「무관한 줄 밀림이
픽스처를 못 움직이는가」** 라는 행동이다. 값을 단언하면 다음 사람이 값을 갱신하며
같은 함정을 도로 판다 — 행동을 단언하면 그 길이 막힌다.
"""

import os
import pathlib
import re
import shutil
import sys
import tempfile

import harness  # noqa: F401  (경로 설정)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DIR = os.path.join(ROOT, "client", "scripts")
if GEN_DIR not in sys.path:
    sys.path.insert(0, GEN_DIR)
import gen_plugin_screens  # noqa: E402

NL = chr(10)


def _plugin_tree(dst):
    """정본 플러그인만 떠 온 가짜 루트. 생성기는 `<루트>/pytmuxlib/plugins` 만 읽는다."""
    src = os.path.join(ROOT, "pytmuxlib", "plugins")
    shutil.copytree(src, os.path.join(dst, "pytmuxlib", "plugins"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    return pathlib.Path(dst)


async def test_an_unrelated_line_shift_does_not_move_the_fixture():
    """플러그인 파일에 빈 줄 하나를 끼워 **전부를 한 줄씩 밀어도** 픽스처는 그대로다."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _plugin_tree(tmp)
        before = gen_plugin_screens.collect(root)
        assert before["kinds"], "가짜 루트에서 아무 모양도 안 나왔다 — 오라클이 공허하다"

        # 스펙을 내는 파일 하나를 골라 맨 위에 빈 줄을 끼운다(= 그 파일의 줄이 전부 밀린다).
        # 자리에서 줄 번호를 떼고 고른다 — 안 그러면 «줄 번호가 돌아온» 변이에서
        # 이 시험이 파일을 못 찾아 죽고, 진짜 진단(픽스처가 움직였다)이 안 뜬다.
        rel = sorted({s for v in before["sites"].values() for s in v})[0]
        victim = root / re.sub(":[0-9]+$", "", rel)
        victim.write_text(NL + victim.read_text(encoding="utf-8"), encoding="utf-8")

        after = gen_plugin_screens.collect(root)

    assert after == before, (
        "무관한 줄 밀림이 픽스처를 움직였다 — 플러그인을 한 줄만 고쳐도 게이트 둘이"
        " 울고, 그러면 사람은 「이 게이트는 원래 붉다」를 배운다(pytmux-394)."
        + NL + f"  전: {before}" + NL + f"  후: {after}")


async def test_the_site_reference_names_a_file_not_a_line():
    """`sites` 는 파일까지만 적는다 — 줄 번호를 담는 순간 위 성질이 깨진다."""
    data = gen_plugin_screens.collect(pathlib.Path(ROOT))
    sites = sorted({s for v in data["sites"].values() for s in v})
    assert sites, "자리를 하나도 안 적었다 — 오라클이 공허하다"
    numbered = [s for s in sites if re.search(":[0-9]+$", s)]
    assert not numbered, (
        f"자리에 줄 번호가 다시 들어왔다: {numbered}"
        " — 참고는 판정에서 뺀다(줄 번호는 그때 `grep -n` 으로 찾는다)")


async def test_a_hole_we_could_not_resolve_still_names_its_line():
    """반대쪽 계약 — `unresolved` 는 줄 번호를 **그대로** 든다(참고가 아니라 진단이다)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        pdir = root / "pytmuxlib" / "plugins" / "probe"
        os.makedirs(pdir)
        # 스펙은 **3번째 줄**에서 열린다 — `kind` 가 글자도 매개변수도 아니라 못 푼다.
        src = NL.join([
            "KIND = compute()",
            "",
            "SPEC = {",
            '    "t": "plugin_screen",',
            '    "kind": KIND,',
            "}",
            "",
        ])
        (pdir / "__init__.py").write_text(src, encoding="utf-8")

        data = gen_plugin_screens.collect(root)

    assert data["unresolved"], "못 푼 자리를 조용히 넘겼다 — 초록으로 위장한 것이다"
    assert any(re.search(":3(?![0-9])", u) for u in data["unresolved"]), (
        f"진단이 줄 번호를 잃었다: {data['unresolved']}"
        " — 이쪽은 참고가 아니라 「여기를 못 풀었다」라 자리가 값이다")
