"""클라이언트 순수 유틸리티 — 화면 폭·토큰 포맷·셸 argv·키 정규화·상수.

client.py 의 거대 클로저(build_client_app)에서 분리한, 클로저 상태(config/sock_path)
를 캡처하지 않는 순수 함수·상수 모음(§10 LLM 친화 리팩토링). client.py 가 이름을
그대로 import 해 쓰므로 동작은 불변이다."""
from __future__ import annotations

from wcwidth import wcwidth

from . import proc

def _shell_argv(cmd: str) -> list:
    """run-shell/if-shell/display-popup 의 셸 명령 argv. OS 별 셸로 분기.

    POSIX: /bin/sh -c <cmd>,  Windows: cmd /c <cmd> (COMSPEC 우선).
    셸 분기 로직은 server(pipe-pane)와 공유하기 위해 proc.shell_argv 에 둔다.
    """
    return proc.shell_argv(cmd)


def _char_cells(ch: str) -> int:
    """터미널에서 문자가 차지하는 칸 수(와이드=2, 그 외=1)."""
    return 2 if wcwidth(ch) == 2 else 1


def _fmt_tokens(total: int) -> str:
    """누적 토큰 수를 짧게 표기. 1234567→"1.2M", 45200→"45.2k", 800→"800".
    (서버측 tokens.fmt 과 동일 규칙 — 클라이언트 단독 표시용 경량 복제.)"""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M".replace(".0M", "M")
    if total >= 1_000:
        return f"{total / 1_000:.1f}k".replace(".0k", "k")
    return str(total)


# 상태줄 오른쪽 strftime 코드 분류 — 시각(시계) vs 날짜(달력) 클릭 존 분리용.
_TIME_STRFTIME = set("HIMSpRTrXkl")
_DATE_STRFTIME = set("YymdbBaAjeDFuwUWxgGCV")


# clock-mode 큰 시계용 3x5 블록 폰트(시:분:초)
_CLOCK_FONT = {
    "0": ["███", "█ █", "█ █", "█ █", "███"],
    "1": ["  █", "  █", "  █", "  █", "  █"],
    "2": ["███", "  █", "███", "█  ", "███"],
    "3": ["███", "  █", "███", "  █", "███"],
    "4": ["█ █", "█ █", "███", "  █", "  █"],
    "5": ["███", "█  ", "███", "  █", "███"],
    "6": ["███", "█  ", "███", "█ █", "███"],
    "7": ["███", "  █", "  █", "  █", "  █"],
    "8": ["███", "█ █", "███", "█ █", "███"],
    "9": ["███", "█ █", "███", "  █", "███"],
    ":": ["   ", " █ ", "   ", " █ ", "   "],
}


# 한글 두벌식 자모 → QWERTY 영문 키. IME 가 켜져 있어도 단축키가 동작하도록
# 키 매칭 시 자모를 물리 키(영문)로 되돌린다.
_JAMO = {
    "ㅂ": "q", "ㅈ": "w", "ㄷ": "e", "ㄱ": "r", "ㅅ": "t", "ㅛ": "y", "ㅕ": "u",
    "ㅑ": "i", "ㅐ": "o", "ㅔ": "p", "ㅁ": "a", "ㄴ": "s", "ㅇ": "d", "ㄹ": "f",
    "ㅎ": "g", "ㅗ": "h", "ㅓ": "j", "ㅏ": "k", "ㅣ": "l", "ㅋ": "z", "ㅌ": "x",
    "ㅊ": "c", "ㅍ": "v", "ㅠ": "b", "ㅜ": "n", "ㅡ": "m",
    # 시프트(쌍자음/이중모음) → 대문자 영문
    "ㅃ": "Q", "ㅉ": "W", "ㄸ": "E", "ㄲ": "R", "ㅆ": "T", "ㅒ": "O", "ㅖ": "P",
}


def _normalize_key(k: str) -> str:
    """Textual 키 문자열에서 한글 자모를 QWERTY 영문 키로 정규화."""
    if not k:
        return k
    for pfx in ("ctrl+", "shift+", "alt+", "meta+"):
        if k.startswith(pfx):
            base = k[len(pfx):]
            return pfx + _JAMO.get(base, base)
    return _JAMO.get(k, k)


# mouse-debug 진단에서 기록할 '내비게이션 키' 안전 목록. 원격 휠이 alt-scroll
# (DECSET 1007) 변환으로 ↑/↓ 화살표로 새는지(=휠 이벤트는 안 오고 화살표만 옴)를
# 가려내기 위함. **여기 없는 키(특히 문자)는 절대 로그에 남기지 않는다** — 패널에
# 친 텍스트/비밀번호가 진단 로그로 유출되지 않게 하기 위한 화이트리스트.
_KEY_DIAG = frozenset({
    "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
})
