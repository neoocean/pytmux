"""하네스가 **자기 임시물을 거두는가**(pytmux-435 ④).

`server_only` 은 서버마다 임시 토큰 DB 하나와 캡처 디렉터리 하나를 만든다 — 그것이
격리의 값이다. 그런데 종전 `teardown` 은 **환경변수만 풀고 파일은 뒀다.** 스위트가
서버를 수백 곳에서 띄우므로 한 바퀴가 그만큼을 상자에 남긴다. 이 상자 실측
(2026-09-02): `pytmux-db-*` **2393개** · `pytmux-cap-*` **2847개**. 아무도 세지 않았고
아무도 지우지 않았다.

⛔ **여기서 재는 것은 「지우는 코드가 있나」가 아니라 「남았나」다.** 435 ③ 이 같은 교훈을
   QA 층에서 말한다 — 정리 코드가 있다는 것만 재고 결과를 안 재면, 정리는 실패한 채로
   조용히 산다.
"""
import os
import tempfile
import time

import harness  # noqa: F401  (sys.path 주입)


async def test_teardown_takes_its_own_temp_files_with_it():
    """서버 한 바퀴가 끝나면 그 서버가 쓰던 임시 DB·캡처 디렉터리가 **없어야** 한다."""
    async with harness.running_server() as (_srv, _task, _sock):
        db = os.environ["PYTMUX_TOKENS_DB"]
        cap = os.environ["PYTMUX_CAPTURE_DIR"]
        assert os.path.isdir(cap), cap
        # DB 는 서버가 토큰을 적을 때 생긴다 — 있으면 있고 없으면 없다. 재는 것은
        # 「끝난 뒤에 남았나」이므로 여기서는 경로만 쥔다.
    assert not os.path.exists(cap), f"캡처 디렉터리가 남았다: {cap}"
    for suffix in ("", "-wal", "-shm"):
        assert not os.path.exists(db + suffix), f"임시 토큰 DB 가 남았다: {db + suffix}"


async def test_teardown_does_not_take_a_file_the_test_itself_named():
    """⛔ **접두사 밖으로 넓히지 않는다** — 캡처 override 자체를 재는 시험의 파일까지
    지우면 그 시험을 망친다. 남의 이름은 안 건드린다."""
    mine = tempfile.mkdtemp(prefix="somebody-elses-")
    try:
        harness._discard_temp(mine, "pytmux-cap-")
        assert os.path.isdir(mine), "우리 접두사가 아닌데 지웠다"
    finally:
        os.rmdir(mine)


async def test_the_sweep_reaps_only_what_is_ours_and_old():
    """묵은 것에만 수명이 있다 — **우리 접두사 · 나이 초과** 둘을 다 만족할 때만.

    ⚠ 나이 조건이 없으면 **병렬로 도는 다른 런**이 지금 쥔 파일을 지워 그 런을 망친다.
    그래서 세 부류를 한 자리에 놓고 하나만 사라지는지 본다."""
    tmp = tempfile.gettempdir()
    old_stamp = time.time() - 48 * 3600
    made = {}
    for key, name in (("ours_old", "pytmux-db-selftest-old.tokens.db"),
                      ("ours_new", "pytmux-db-selftest-new.tokens.db"),
                      ("theirs_old", "notpytmux-selftest-old.db")):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("x")
        if key.endswith("_old"):
            os.utime(p, (old_stamp, old_stamp))
        made[key] = p
    try:
        harness.sweep_stale_temp(hours=24.0)
        assert not os.path.exists(made["ours_old"]), "우리 것이고 묵었는데 안 거뒀다"
        assert os.path.exists(made["ours_new"]), "아직 젊은 것을 거뒀다 — 병렬 런을 망친다"
        assert os.path.exists(made["theirs_old"]), "우리 접두사가 아닌 것을 거뒀다"
    finally:
        for p in made.values():
            if os.path.exists(p):
                os.remove(p)
