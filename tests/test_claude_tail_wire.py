"""Claude 트랜스크립트 **꼬리**의 와이어 계약(§7 P5 ⓑ' — 사용자 결정 2026-07-28).

# 무엇을 정하는 테스트인가

원격 탭의 Claude 뷰를 채우려면 상류가 무언가를 보내야 한다. 고른 길은 **원문 꼬리**다 —
항목까지 만들어 보내면 파이썬에 표시용 파서를 새로 써야 하고, 그 파서가 러스트 것과
어긋나는 순간 **같은 대화가 탭에 따라 달라 보인다**(설계문서 §7 P5 의 비용 재측정).

원문을 보내는 것에 걸린 조건은 하나뿐이고 그것이 이 파일의 주제다: **얼마나 보내는가**.
반대 근거가 '사적'이 아니라 '양'이었으므로, 상한이 실제로 걸리는지가 계약이다.

`test_blocks_wire.py` 와 같은 구조다 — 계약이 깨져도 조용하기 때문에(기존 클라는 모르는
메시지를 그냥 무시한다) 테스트로 못박는다.

러너는 `async def` 만 수집하고 픽스처를 주지 않는다(tests/run.py) — 그래서 동기 검사도
async 로 적고 임시 파일은 손으로 만든다.
"""
import asyncio
import importlib
import os
import tempfile

import harness  # noqa: F401 (경로 설정)
from harness import running_server
from pytmuxlib import ipc, plugins
from pytmuxlib.protocol import PROTO_VERSION, read_msg, write_msg
from pytmuxlib.serverremote import (
    _REMOTE_CLAUDE_LINES_MAX,
    _relay_frame_ok,
    _sanitize_claude_tail,
)

# 플러그인 디렉토리 이름에 하이픈이 있어 평범한 import 로는 못 가져온다.
clienttail = importlib.import_module("pytmuxlib.plugins.claude-code.clienttail")


class _FakePane:
    """훅이 캐시를 매다는 자리만 있으면 된다."""
    id = 1


async def _attach(sock, srv, caps=None):
    reader, writer = await ipc.open_connection(sock)
    hello = {"t": "hello", "proto": PROTO_VERSION, "cols": 80, "rows": 24,
             "token": srv.auth_token}
    if caps is not None:
        hello["caps"] = caps
    await write_msg(writer, hello)
    return reader, writer


async def _drain(reader, seconds=1.0):
    out = []
    try:
        while True:
            msg = await asyncio.wait_for(read_msg(reader), seconds)
            if msg is None:
                break
            out.append(msg)
    except asyncio.TimeoutError:
        pass
    return out


def _write_jsonl(path, lines):
    with open(path, "w", encoding="utf-8") as fp:
        for line in lines:
            fp.write(line + "\n")


# ---- 꼬리 자르기 -----------------------------------------------------------

async def test_a_short_file_is_sent_whole():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"a":1}', '{"a":2}'])
        assert clienttail.read_tail(path) == '{"a":1}\n{"a":2}'


async def test_the_first_line_is_dropped_when_the_cut_lands_mid_line():
    """바이트로 자르면 첫 줄은 거의 항상 반 토막이다.

    그대로 보내면 클라는 그 줄을 조용히 버리거나(운이 좋으면) **틀린 항목으로
    파싱한다**(운이 나쁘면). 후자가 더 나쁘다 — 화면에 그럴듯한 거짓이 뜬다.
    """
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"n":"' + "x" * 200 + '"}', '{"n":"end"}'])
        tail = clienttail.read_tail(path, max_bytes=60)
        assert tail == '{"n":"end"}', f"반 토막 줄이 남았다: {tail!r}"


async def test_a_single_line_larger_than_the_cap_yields_nothing_not_garbage():
    """온전한 줄이 하나도 없으면 빈 문자열 — None(못 읽었다)과 구분한다."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"n":"' + "x" * 5000 + '"}'])
        assert clienttail.read_tail(path, max_bytes=100) == ""


async def test_the_line_cap_bounds_a_file_of_short_lines():
    """바이트 상한만 두면 짧은 줄이 이어질 때 수백 항목이 실린다."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"i":%d}' % i for i in range(500)])
        lines = clienttail.read_tail(path, max_lines=10).splitlines()
        assert len(lines) == 10
        assert lines[-1] == '{"i":499}', "꼬리가 아니라 머리를 잘랐다"


async def test_a_missing_file_is_none_not_empty():
    # ""(보낼 줄이 없다)와 None(파일이 없다)이 섞이면 호출부가 빈 프레임을 보낸다.
    with tempfile.TemporaryDirectory() as d:
        assert clienttail.read_tail(os.path.join(d, "nope.jsonl")) is None


# ---- 변경 판정 -------------------------------------------------------------

async def test_an_unchanged_file_is_not_sent_again():
    """매번 보내면 30Hz 루프가 대화 원문을 계속 실어 나른다."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"a":1}'])
        pane = _FakePane()
        assert clienttail.pane_tail(pane, path) == '{"a":1}'
        assert clienttail.pane_tail(pane, path) is None, "안 바뀐 파일을 또 보냈다"

        _write_jsonl(path, ['{"a":1}', '{"a":2}'])
        assert clienttail.pane_tail(pane, path) == '{"a":1}\n{"a":2}'


async def test_force_sends_even_when_nothing_changed():
    """방금 붙은 클라는 "바뀐 적이 없다"는 이유로 빈 화면을 봐서는 안 된다."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"a":1}'])
        pane = _FakePane()
        clienttail.pane_tail(pane, path)
        assert clienttail.pane_tail(pane, path, force=True) == '{"a":1}'


async def test_a_failed_read_does_not_advance_the_marker():
    """읽기에 실패했는데 표식만 갱신하면 다음 변경 때까지 조용히 멈춘다."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.jsonl")
        _write_jsonl(path, ['{"a":1}'])
        pane = _FakePane()

        def boom(*a, **kw):
            raise OSError("읽기 실패")

        assert clienttail.pane_tail(pane, path, open_fn=boom) is None
        # 표식이 안 남았으므로 다음 호출은 정상적으로 보낸다.
        assert clienttail.pane_tail(pane, path) == '{"a":1}'


async def test_the_frame_counter_keeps_the_server_off_the_filesystem():
    """매 프레임 stat 하면 패널마다 초당 30번 파일시스템을 두드린다."""
    pane = _FakePane()
    assert clienttail.due(pane) is True          # 첫 호출은 항상 차례다
    skipped = sum(0 if clienttail.due(pane) else 1
                  for _ in range(clienttail.TAIL_FRAMES))
    assert skipped == clienttail.TAIL_FRAMES, "카운터가 안 걸렸다"
    assert clienttail.due(pane) is True, "카운터가 안 풀렸다"
    assert clienttail.due(pane, force=True) is True, "force 가 카운터에 막혔다"


# ---- 능력 광고 -------------------------------------------------------------

async def test_the_capability_is_advertised_upstream_so_remote_tabs_get_it():
    """다운스트림 서버는 업스트림에게 그냥 클라 하나다 — 광고 안 하면 안 온다."""
    assert "claude" in plugins.load().upstream_caps()
    assert plugins.Registry([]).upstream_caps() == [], \
        "플러그인을 지웠는데 능력이 남았다(delete-to-disable 이 깨졌다)"


async def test_a_client_without_caps_never_receives_the_transcript():
    """계약: 광고 안 한 클라(= 파이썬 Textual 클라)는 한 바이트도 안 받는다.

    그 클라는 자기 기계의 트랜스크립트를 직접 읽는 화면을 이미 갖고 있다. 대화 원문은
    프레임 중에 가장 크므로 흘리면 그만큼 그냥 낭비다.
    """
    async with running_server() as (srv, task, sock):
        srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv)          # 광고 없음
        msgs = await _drain(reader, 1.5)
        kinds = {m.get("t") for m in msgs}
        assert "claude" not in kinds, \
            f"광고하지 않은 클라에 claude 가 갔다: {sorted(kinds)}"
        writer.close()


async def test_an_advertising_client_gets_a_well_formed_frame_or_none_at_all():
    """광고한 클라의 계약: 프레임이 오면 **모양이 정해져 있고**, 없으면 조용하다.

    "안 온다"로 단언하지 않는 이유가 있다. 경로 해석은 `find_transcript` 의 폴백을
    타는데, 그 폴백은 **패널의 cwd 로 프로젝트 폴더를 찾는다** — claude 를 띄운 적 없는
    셸 패널이라도 그 디렉토리에서 예전에 돌린 대화가 있으면 그것이 잡힌다(토큰 회계가
    쓰던 규칙 그대로다). 그래서 이 단언은 **테스트를 돌리는 기계에 따라 갈린다**:
    pytmux 저장소에서 돌리면 실제로 프레임이 온다(2026-07-28 에 그렇게 드러났다).

    로컬 네이티브 클라도 같은 규칙으로 같은 파일을 고르므로 두 경로가 어긋나지는 않는다.
    """
    async with running_server() as (srv, task, sock):
        srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["claude"])
        msgs = await _drain(reader, 1.5)
        assert any(m.get("t") == "layout" for m in msgs), "핸드셰이크가 깨졌다"
        for m in [m for m in msgs if m.get("t") == "claude"]:
            assert isinstance(m.get("pane"), int), f"pane 이 없다: {m!r}"
            assert isinstance(m.get("tail"), str), f"tail 이 문자열이 아니다: {m!r}"
            assert m["tail"], "빈 꼬리를 프레임으로 보냈다"
            assert len(m["tail"]) <= clienttail.MAX_TAIL_BYTES, "상한을 넘겼다"
            assert len(m["tail"].splitlines()) <= clienttail.MAX_TAIL_LINES
        writer.close()


# ---- 신뢰경계(페더레이션) ---------------------------------------------------

async def test_a_relayed_frame_must_carry_a_pane_and_a_string_tail():
    """클라가 무가드로 소비하는 키를 경계에서 본다(_relay_frame_ok 계약)."""
    assert _relay_frame_ok("claude", {"pane": 1, "tail": "{}"})
    assert not _relay_frame_ok("claude", {"tail": "{}"}), "pane 없이 통과했다"
    assert not _relay_frame_ok("claude", {"pane": 1}), "tail 없이 통과했다"
    assert not _relay_frame_ok("claude", {"pane": 1, "tail": ["{}"]}), \
        "문자열이 아닌 tail 이 통과했다"


async def test_the_relay_bounds_what_a_compromised_upstream_can_send():
    """상류를 믿고 그대로 흘리면 큰 프레임 하나로 다운스트림 클라를 밀어낼 수 있다."""
    n = _REMOTE_CLAUDE_LINES_MAX * 3
    huge = "\n".join('{"i":%d}' % i for i in range(n))
    out = _sanitize_claude_tail(huge)
    lines = out.splitlines()
    assert len(lines) == _REMOTE_CLAUDE_LINES_MAX
    assert lines[-1] == '{"i":%d}' % (n - 1), "꼬리가 아니라 머리를 남겼다"


async def test_the_relay_does_not_mangle_the_json():
    """블록과 갈리는 지점 — 이건 클라가 **파싱하는** 데이터라 제어문자를 접으면 안 된다.

    블록의 `cmd`/`cwd` 는 클라가 그대로 그리는 텍스트라 접어야 하지만, 여기서 같은 짓을
    하면 멀쩡한 JSON 이 깨진다(문자열 안의 개행은 이미 이스케이프돼 있다).
    """
    line = '{"text":"a\\u0007b\\nc","tab":"x\\ty"}'
    assert _sanitize_claude_tail(line) == line
