"""픽스처 생성기 `gen_plugin_client_cmds.py` 가 **빌드 시각에 상자를 안 만지나**.

# 왜 이 자리를 재나 (pytmux-194)

2026-08-09 에 「커밋 전 한 명령」(`scripts/check_all.py`)이 **첫 스텝에서 47분 동안
0바이트**로 매달렸다. 원인은 이 생성기였다:

1. 플러그인에 화면 스펙을 물으면 `mdir`·`ncd` 가 **진짜 파일시스템을 훑었다**. 스텁이
   `MagicMock` 이라 경로가 엉뚱한 곳으로 떨어지고, 그 자들은 없는 경로를 만나면 위로
   거슬러 올라가며 훑는다 — 실측으로 `os.scandir` 90회가 `/`·`/Users/<계정>`·
   `/Applications` 로 나갔다. 그중 하나(Arq 백업 목적지)에서 `open()` 이 매달렸다.
2. 그 훑기가 **비-데몬 executor 스레드**에 있어, 일이 끝나도(=JSON 을 다 찍었어도)
   인터프리터 종료가 그 스레드를 영원히 join 했다.

# 무엇을 재나 — 그리고 왜 「돌려 보니 안 매달린다」로 안 재나

⛔ **"생성기가 시한 안에 끝나나"는 오라클로 못 쓴다.** 그날의 매달림은 그 상자에 그때
   붙어 있던 마운트가 만든 것이라, 고치기 **전** 코드도 오늘 이 맥에서는 1.2초에 끝난다
   (실측 2026-08-17). 초록이 고쳤다는 뜻이 아니라 **오늘 운이 좋았다**는 뜻이다.

그래서 원인 둘을 직접 잰다 — 저장소 밖을 훑는 호출 **0건**, 살아남은 비-데몬 스레드
**0개**. 둘 다 고치기 전 코드에서는 붉다(90건 · 2개).
"""

import os
import sys
import threading

import harness  # noqa: F401  (경로 설정)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DIR = os.path.join(ROOT, "client", "scripts")
if GEN_DIR not in sys.path:
    sys.path.insert(0, GEN_DIR)
import gen_plugin_client_cmds as gen  # noqa: E402


def _collect_watching_the_filesystem():
    """`collect()` 를 돌리며 **디렉터리를 훑는 호출**을 전부 적어 돌려준다.

    `os.scandir`·`os.listdir` 둘을 본다 — 그 둘이 이 저장소의 나열 경로다
    (`pathlib.Path.iterdir` 도 `os.scandir` 로 내려간다).
    """
    seen = []
    real_scandir, real_listdir = os.scandir, os.listdir

    def scandir(path="."):
        seen.append(os.fspath(path))
        return real_scandir(path)

    def listdir(path="."):
        seen.append(os.fspath(path) if path is not None else ".")
        return real_listdir(path)

    os.scandir, os.listdir = scandir, listdir
    try:
        data = gen.collect(__import__("pathlib").Path(ROOT))
    finally:
        os.scandir, os.listdir = real_scandir, real_listdir
    return data, seen


async def test_the_generator_never_walks_outside_the_repo():
    """빌드 시각의 픽스처 생성기가 **이 상자의 홈**을 훑으면 안 된다.

    훑는 순간 픽스처의 운명이 그 상자의 마운트 사정에 걸린다 — 그리고 마운트가 매달리는
    것은 이 저장소에 이미 기록된 부류다.
    """
    _data, seen = _collect_watching_the_filesystem()
    root = os.path.realpath(ROOT)
    outside = sorted({p for p in seen
                      if not os.path.realpath(p).startswith(root + os.sep)
                      and os.path.realpath(p) != root})
    assert not outside, (
        f"저장소 밖을 {len(outside)}곳 훑었다 — 빌드 시각의 생성기가 상자를 만진다: "
        + ", ".join(outside[:8]))


async def test_the_generator_leaves_no_thread_that_blocks_shutdown():
    """일이 끝난 뒤 **비-데몬 스레드가 남으면 안 된다**.

    남으면 인터프리터 종료가 그것을 join 한다 — 그 스레드가 매달리는 날 프로세스는
    출력을 다 내고도 좀비가 되고, 그 위의 게이트 둘이 함께 선다.
    """
    before = {t.ident for t in threading.enumerate()}
    gen.collect(__import__("pathlib").Path(ROOT))
    left = [t for t in threading.enumerate()
            if t.ident not in before and not t.daemon and t.is_alive()]
    assert not left, ("종료를 막는 스레드가 남았다: "
                      + ", ".join(sorted(t.name for t in left)))


async def test_offload_is_stubbed_but_the_answer_is_unchanged():
    """오프로드를 안 시켜도 **픽스처 내용이 같다** — 값을 안 읽는 자리이기 때문이다.

    이것이 위 두 시험의 전제다: 일을 안 시키는 것이 답을 위조하는 것이 아님을 잰다.
    `no_filesystem_offload` 밖에서는 진짜 루프가 돌아오는지도 함께 본다(패치를 안
    되돌리면 이 프로세스의 나머지 시험이 조용히 다른 세상에서 돈다).
    """
    import asyncio
    import pathlib

    real = asyncio.get_event_loop
    with gen.no_filesystem_offload():
        assert asyncio.get_event_loop is not real
        loop = asyncio.get_event_loop()
        fired = []
        awaitable = loop.run_in_executor(None, lambda: fired.append(1))
        assert hasattr(awaitable, "__await__"), "생성기가 '낸다'로 셀 수 있어야 한다"
        assert not fired, "오프로드된 일이 실제로 돌았다 — 상자를 만진다"
    assert asyncio.get_event_loop is real, "패치를 안 되돌렸다"

    data = gen.collect(pathlib.Path(ROOT))
    fixture = os.path.join(ROOT, "client", "crates", "proto", "tests",
                           "fixtures", "plugin_client_cmds.json")
    if os.path.isfile(fixture):
        import json
        with open(fixture, encoding="utf-8") as fh:
            assert json.load(fh) == data, (
                "생성기 결과가 픽스처와 다르다 — 오프로드를 안 시킨 것이 답을 바꿨거나"
                " 픽스처가 낡았다(`check_fixtures.py --write`)")
