"""리플레이/렌더 골든 스냅샷 테스트.

크래프팅한 원시 바이트(또는 record 로 캡처한 출력)를 replay() 로 재생해, 합성된
텍스트 프레임이 기대값과 일치하는지 확인한다. 렌더 파이프라인(커서 이동·덮어쓰기·
와이드 문자·열 정렬) 회귀를 화면 없이 못박는다.
"""
import os
import tempfile

import harness  # noqa: F401 (경로 설정)
import pytmux
from pytmuxlib.replay import replay, run_record


async def test_plain_lines():
    lines = [ln.rstrip() for ln in replay(b"hello\r\nworld\r\n", 20, 5)]
    assert lines[0] == "hello" and lines[1] == "world"


async def test_cursor_addressing():
    # ESC[2J 클리어, ESC[3;5H (행3,열5, 1-index) 로 이동 후 X
    lines = replay(b"\x1b[2J\x1b[3;5HX", 20, 5)
    assert lines[2][4] == "X", repr(lines[2])
    assert lines[0].strip() == "" and lines[1].strip() == ""


async def test_carriage_return_overwrite():
    # AAAA 쓰고 CR 로 줄 처음 복귀 후 BB → "BBAA"
    lines = [ln.rstrip() for ln in replay(b"AAAA\rBB", 10, 2)]
    assert lines[0] == "BBAA", repr(lines[0])


async def test_column_alignment():
    # 'a' 쓰고 ESC[20G(20열, 1-index)로 이동 후 'b' → a=col0, b=col19
    lines = replay(b"a\x1b[20Gb", 40, 2)
    assert lines[0][0] == "a" and lines[0][19] == "b", repr(lines[0][:21])


async def test_wide_char_alignment():
    # 와이드 문자(한글) 뒤 글자가 밀리지 않음 + 줄 시각 폭이 cols 와 동일
    lines = replay("가나X\r\n".encode(), 20, 3)
    assert lines[0].startswith("가나X"), repr(lines[0])
    # 시각 폭(와이드=2) 합이 20
    from wcwidth import wcswidth
    assert wcswidth(lines[0]) == 20, wcswidth(lines[0])


async def test_alt_screen_in_replay():
    # 대체 화면 진입 중 그린 내용만 보이고, 이탈 후엔 메인 복원
    data = b"MAIN\r\n\x1b[?1049h\x1b[2J\x1b[HALT\x1b[?1049l"
    lines = [ln.rstrip() for ln in replay(data, 20, 4)]
    assert any("MAIN" in ln for ln in lines) and not any("ALT" in ln for ln in lines)


async def test_record_then_replay_roundtrip():
    # record 로 실제 프로그램 출력을 캡처하고 replay 로 재생 → 일치
    if os.name == "nt":
        return  # run_record 는 PTY(pty.fork) 의존이라 Windows 미지원(rc=2 가드, §7-b4)
    path = tempfile.mktemp(suffix=".raw")
    rc = run_record(path, 40, 6, ["sh", "-c", "printf 'AB\\nCD\\n'"], echo=False)
    assert rc == 0
    lines = [ln.rstrip() for ln in pytmux.replay(open(path, "rb").read(), 40, 6)]
    joined = "\n".join(lines)
    assert "AB" in joined and "CD" in joined, repr(joined)


async def test_read_capture_transparent_gzip():
    """용량 절감으로 닫힌 로그를 .log.gz 로 보관한다(요청 2026-06-16) → read_capture
    가 .gz 를 투명 해제해 replay/감사 도구가 .log 와 동일하게 재생한다."""
    import gzip
    from pytmuxlib.replay import read_capture, replay
    data = b"\x1b[2Jhello\r\nworld\r\n"
    # 일반 .log
    p_raw = tempfile.mktemp(suffix=".log")
    with open(p_raw, "wb") as f:
        f.write(data)
    # 압축 .log.gz
    p_gz = tempfile.mktemp(suffix=".log.gz")
    with gzip.open(p_gz, "wb") as f:
        f.write(data)
    assert read_capture(p_raw) == data
    assert read_capture(p_gz) == data           # 투명 해제
    lr = [ln.rstrip() for ln in replay(read_capture(p_raw), 20, 4)]
    lg = [ln.rstrip() for ln in replay(read_capture(p_gz), 20, 4)]
    assert lr == lg and lg[0] == "hello" and lg[1] == "world", (lr, lg)
