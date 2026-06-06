"""클라이언트 순수 유틸리티 — 화면 폭·토큰 포맷·셸 argv·키 정규화·상수.

client.py 의 거대 클로저(build_client_app)에서 분리한, 클로저 상태(config/sock_path)
를 캡처하지 않는 순수 함수·상수 모음(§10 LLM 친화 리팩토링). client.py 가 이름을
그대로 import 해 쓰므로 동작은 불변이다."""
from __future__ import annotations

from functools import lru_cache

from rich.style import Style
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


# 이모지(컬러 픽토그래프) 코드포인트 대략 범위. 팝업이 떠 본문을 어둡게 칠할 때,
# 이모지는 터미널이 셀 스타일을 무시하고 컬러로 그려 안 어두워지므로 placeholder 로
# 치환한다(#25). CJK/한글·기하도형(○◐ 등)·박스문자는 스타일대로 어두워지므로 제외.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF), (0x1FC00, 0x1FFFD),
    (0x2600, 0x27BF),   # 기타 기호·딩뱃(✽ ✓ ☀ 등 컬러로 그려질 수 있음)
    (0x2B00, 0x2BFF), (0x2300, 0x23FF),  # ⌛⏳⏰ 등
    (0x2190, 0x21FF),  # 화살표 일부(컬러 이모지 변형)
    (0xFE00, 0xFE0F),  # variation selectors
)


def _is_emoji(ch: str) -> bool:
    """ch 가 컬러 이모지로 렌더될 가능성이 높은 문자인지(어둡게 안 되는 대상, #25)."""
    if not ch:
        return False
    o = ord(ch[0])
    return any(a <= o <= b for a, b in _EMOJI_RANGES)


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


# 한영 오타 복원(명령 프롬프트): IME 가 켜진 채 영문 명령을 치면 두벌식 자모가
# 음절로 합성돼 들어온다(예: "kill"→"ㅏㅑㅣㅣ", "split"→"�holl"… 식). 합성 음절을
# 초/중/종성으로 분해하고 복합 자모(겹받침·이중모음)를 낱자로 풀어 _JAMO 로 QWERTY
# 영문으로 되돌린다. ASCII 등 비-한글은 그대로 둔다.
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"            # 초성 19
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"          # 중성 21
_JONG = "_ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"  # 종성 28(0=없음)
# 복합 자모 → 두벌식상 그것을 만드는 낱자 시퀀스(각 낱자는 _JAMO 로 영문 변환).
_COMPOUND_JAMO = {
    "ㅘ": "ㅗㅏ", "ㅙ": "ㅗㅐ", "ㅚ": "ㅗㅣ", "ㅝ": "ㅜㅓ", "ㅞ": "ㅜㅔ",
    "ㅟ": "ㅜㅣ", "ㅢ": "ㅡㅣ",
    "ㄳ": "ㄱㅅ", "ㄵ": "ㄴㅈ", "ㄶ": "ㄴㅎ", "ㄺ": "ㄹㄱ", "ㄻ": "ㄹㅁ",
    "ㄼ": "ㄹㅂ", "ㄽ": "ㄹㅅ", "ㄾ": "ㄹㅌ", "ㄿ": "ㄹㅍ", "ㅀ": "ㄹㅎ",
    "ㅄ": "ㅂㅅ",
}


def _jamo_to_q(j: str) -> str:
    if j in _COMPOUND_JAMO:
        return "".join(_JAMO.get(x, x) for x in _COMPOUND_JAMO[j])
    return _JAMO.get(j, j)


def has_hangul(s: str) -> bool:
    """문자열에 한글(자모/호환자모/완성형 음절)이 하나라도 있으면 True."""
    return any(0x1100 <= ord(c) <= 0x11FF or 0x3130 <= ord(c) <= 0x318F
               or 0xAC00 <= ord(c) <= 0xD7A3 for c in s)


def hangul_to_qwerty(text: str) -> str:
    """한글(두벌식 IME 로 잘못 입력된 영문)을 QWERTY 영문으로 되돌린다.

    완성형 음절은 초/중/종성으로 분해, 낱자/복합자모는 _JAMO 로 변환. 비-한글은
    그대로. 예: "ㅏㅑㅣㅣ"→"kill", "ㄴ푤ㅑㅅ"류 합성도 분해해 복원."""
    out = []
    for ch in text:
        o = ord(ch)
        if 0xAC00 <= o <= 0xD7A3:                 # 완성형 음절 → 분해
            s = o - 0xAC00
            cho, jung, jong = s // 588, (s // 28) % 21, s % 28
            out.append(_jamo_to_q(_CHO[cho]))
            out.append(_jamo_to_q(_JUNG[jung]))
            if jong:
                out.append(_jamo_to_q(_JONG[jong]))
        elif ch in _JAMO or ch in _COMPOUND_JAMO:  # 낱자/복합자모(미합성)
            out.append(_jamo_to_q(ch))
        else:
            out.append(ch)
    return "".join(out)


# mouse-debug 진단에서 기록할 '내비게이션 키' 안전 목록. 원격 휠이 alt-scroll
# (DECSET 1007) 변환으로 ↑/↓ 화살표로 새는지(=휠 이벤트는 안 오고 화살표만 옴)를
# 가려내기 위함. **여기 없는 키(특히 문자)는 절대 로그에 남기지 않는다** — 패널에
# 친 텍스트/비밀번호가 진단 로그로 유출되지 않게 하기 위한 화이트리스트.
_KEY_DIAG = frozenset({
    "up", "down", "left", "right", "pageup", "pagedown", "home", "end",
})


# ---- build_client_app 클로저에서 분리한 순수 헬퍼/상수(§10) ----
# 스타일 해석·키 바이트 변환·명령/메뉴 테이블 등 config/sock_path 를 캡처하지
# 않는 부분. client.py 클로저가 이 이름들을 그대로 import 해 참조한다.

DEFAULT_STYLE = Style()
_style_cache: dict = {}

# p4v-tui 와 동일한 textual-dark 테마 색을 따른다(없으면 폴백).
_THEME_FALLBACK = {
    "primary": "#0178D4", "secondary": "#004578", "accent": "#FEA62B",
    "background": "#121212", "surface": "#1E1E1E", "panel": "#242F38",
    "foreground": "#E0E0E0", "success": "#4EBF71", "warning": "#FEA62B",
    "error": "#B93C5B",
    "primary-darken-2": "#0053AA",   # Claude 헤더 배경(진한 파랑)
}

def theme_color(widget, name: str) -> str:
    """현재 Textual 테마에서 색을 해석(없으면 textual-dark 폴백)."""
    try:
        v = widget.app.theme_variables.get(name)
        if v:
            return v
    except Exception:
        pass
    return _THEME_FALLBACK.get(name, "white")

@lru_cache(maxsize=8192)
def _darken_style(st: Style, ratio: float = 0.55) -> Style:
    """ANSI dim 대신 실제 전경/배경 색을 검정 쪽으로 ratio 만큼 블렌드한 어두운
    색을 계산해 적용하고 bold 를 해제한다(§10). ANSI dim 은 터미널 의존적이라
    bold 글자(많은 터미널이 bold 를 밝게 렌더 → dim 상쇄)·명시적 밝은색 글자를
    충분히 어둡게 못 하는데, 실색 블렌드는 터미널 무관하게 균일하게 어둡다.
    전경색이 기본(None)이면 밝은 기본 전경이 안 묻히게 어두운 회색으로 둔다.

    §10-A #4: 팝업 배경 dim 은 전 화면 셀(수천 개)에 이 함수를 부르는데, 대부분
    셀의 스타일이 같으므로 (st, ratio) 키로 lru_cache 한다 — 같은 스타일은 한 번만
    블렌드 계산하고 재사용해 dim 적용을 같은 프레임에 끝낼 만큼 가볍게 만든다
    (Style 은 해시 가능·불변, 순수 함수라 캐시 안전)."""
    from rich.color import Color

    def _dark(col, fallback):
        if col is None:
            return fallback
        try:
            t = col.get_truecolor()
        except Exception:
            return fallback
        return Color.from_rgb(t.red * (1 - ratio),
                              t.green * (1 - ratio),
                              t.blue * (1 - ratio))

    return Style(color=_dark(st.color, Color.parse("grey23")),
                 bgcolor=_dark(st.bgcolor, None), bold=False)

def make_style(d: dict) -> Style:
    if not d:
        return DEFAULT_STYLE
    key = tuple(sorted(d.items()))
    st = _style_cache.get(key)
    if st is None:
        try:
            st = Style(color=d.get("f"), bgcolor=d.get("b"),
                       bold=bool(d.get("bo")), italic=bool(d.get("it")),
                       underline=bool(d.get("un")), reverse=bool(d.get("rv")),
                       strike=bool(d.get("st")))
        except Exception:
            st = Style(reverse=bool(d.get("rv")), bold=bool(d.get("bo")))
        _style_cache[key] = st
    return st

SPECIAL = {
    "enter": b"\r", "tab": b"\t", "backspace": b"\x7f", "escape": b"\x1b",
    "space": b" ", "up": b"\x1b[A", "down": b"\x1b[B", "right": b"\x1b[C",
    "left": b"\x1b[D", "home": b"\x1b[H", "end": b"\x1b[F",
    "pageup": b"\x1b[5~", "pagedown": b"\x1b[6~", "delete": b"\x1b[3~",
    "insert": b"\x1b[2~", "f1": b"\x1bOP", "f2": b"\x1bOQ", "f3": b"\x1bOR",
    "f4": b"\x1bOS", "f5": b"\x1b[15~", "f6": b"\x1b[17~", "f7": b"\x1b[18~",
    "f8": b"\x1b[19~", "f9": b"\x1b[20~", "f10": b"\x1b[21~",
    "f11": b"\x1b[23~", "f12": b"\x1b[24~",
    "shift+tab": b"\x1b[Z",   # backtab(CSI Z) — Claude 권한 모드 순환 등
    "shift+enter": b"\n",     # LF — Claude 등 입력 줄바꿈(Enter=CR 제출과 구분)
    "shift+escape": b"\x1b",  # 앱으로 ESC 전달(ESC 단독은 esc 모드 진입)
    # 수정자 포함 커서 키(표준 xterm CSI 1;mod 시퀀스)를 패널 앱에 그대로 전달한다.
    # 예전엔 매핑이 없어 b"" 로 버려졌다. Shift+Home/End 로 줄 선택 → Del(\x1b[3~)로
    # 삭제 같은 동작이 가능해진다(지원 여부는 패널 앱에 달림 — Claude Code 등). #14
    "shift+home": b"\x1b[1;2H", "shift+end": b"\x1b[1;2F",
    "shift+left": b"\x1b[1;2D", "shift+right": b"\x1b[1;2C",
    "shift+up": b"\x1b[1;2A", "shift+down": b"\x1b[1;2B",
    "ctrl+home": b"\x1b[1;5H", "ctrl+end": b"\x1b[1;5F",
}

def key_to_bytes(event: events.Key) -> bytes:
    k = event.key
    if k in SPECIAL:
        return SPECIAL[k]
    if k.startswith("ctrl+"):
        c = k[5:]
        # ASCII 영문 ctrl 조합만 제어코드로(한글 등 비ASCII는 무시)
        if len(c) == 1 and "a" <= c.lower() <= "z":
            return bytes([ord(c.lower()) - 96])
        if event.character and event.character.isascii():
            return event.character.encode("utf-8", "replace")
        return b""
    if event.character is not None and event.character != "":
        return event.character.encode("utf-8", "replace")
    return b""

MENU_ITEMS = [
    ("split_lr", "패널 분할 │ (좌우)"),
    ("split_tb", "패널 분할 ─ (상하)"),
    ("zoom", "패널 줌 토글 ⛶"),
    ("kill_pane", "패널 삭제 ✕"),
    ("sync", "입력 동기화 토글"),
    ("autoresume", "토큰리밋 자동재개 토글"),
    ("prompt_clear", "프롬프트 단위 클리어 토글(완료마다 문서화+/clear)"),
    ("new_window", "새 탭"),
    ("rename_window", "탭 이름 변경"),
    ("kill_window", "탭 삭제"),
    ("choose_tree", "탭 선택기(트리)"),
    ("next_window", "다음 탭"),
    ("prev_window", "이전 탭"),
    ("layout_save", "레이아웃 저장(현재 탭)"),
    ("layout_load_over", "레이아웃 불러오기(현재 탭 덮어쓰기)"),
    ("layout_load_new", "레이아웃 불러오기(새 탭)"),
    ("command", "명령 입력"),
    ("detach", "detach (앱 종료, 셸 유지)"),
    ("kill_server", "서버 종료 (모든 탭/셸 종료)"),
]
# 토글 메뉴 항목(현재 on/off 표시·선택해도 메뉴 안 닫음). 상태는 status 에서 읽음.
MENU_TOGGLES = {"zoom", "sync", "autoresume", "prompt_clear"}

# 토큰 절감 설정 팝업(`token-saver` 명령, docs/TOKEN_SAVING_SCENARIO.md)의 행 목록.
# (key, 라벨, 종류). 종류 "toggle"=●/○ 토글, "cycle"=프리셋 값 순환(Enter 로 다음
# 값). 현재값·동작은 client.py 의 _saver_display/_saver_action 이 처리한다(앱 상태
# 의존이라 화면이 직접 안 들고, MenuScreen 이 _run_menu_action 에 위임하는 것과 동일).
SAVER_ROWS = [
    ("autoresume", "토큰리밋 자동재개", "toggle"),
    ("resume_gate", "예산 초과 시 자동재개 보류", "toggle"),
    ("budget_plan", "예산 압박(≥80%) 시 plan 모드 유도", "toggle"),
    ("ctx_autoclear", "컨텍스트 잔량 부족 시 자동 정리", "toggle"),
    ("ctx_action", "  └ 정리 방식", "cycle"),
    ("ctx_threshold", "  └ 잔량 임계", "cycle"),
    ("ctx_min_interval", "  └ 정리 빈도 상한", "cycle"),
    ("auto_doc_clear", "idle 지속 시 자동 문서화+/clear", "toggle"),
    ("claude_auto_mode", "권한모드 자동 오토", "toggle"),
    ("prompt_clear", "프롬프트 단위 클리어(완료마다 doc+/clear)", "toggle"),
    ("budget_day", "일 토큰 예산", "cycle"),
    ("budget_session", "세션 토큰 예산", "cycle"),
    ("budget_5h", "5시간 한도(근접도 표시 분모)", "cycle"),
    ("budget_account", "계정 합계 예산(멀티세션)", "cycle"),
]
# cycle 행의 프리셋 값(Enter 마다 다음으로 순환). 예산 0=무제한(끔).
SAVER_CYCLES = {
    "ctx_action": ["compact", "doc-clear"],
    "ctx_threshold": [10, 15, 20, 25, 30],
    "ctx_min_interval": [0, 60, 120, 300, 600],
    "budget_day": [0, 100_000, 200_000, 500_000, 1_000_000],
    "budget_session": [0, 50_000, 100_000, 200_000, 500_000],
    "budget_5h": [0, 100_000, 200_000, 350_000, 500_000, 1_000_000],
    "budget_account": [0, 200_000, 500_000, 1_000_000, 2_000_000, 5_000_000],
}

# 명령 프롬프트(:)에서 쓸 수 있는 명령 목록 (이름, 설명) — ? 목록·자동완성용
# (이름, 설명, 카테고리). 카테고리는 ?/help 목록의 탭 그룹으로 쓰인다.
# 새 명령을 추가할 땐 카테고리도 함께 지정할 것(없으면 "기타"로 묶임).
COMMANDS = [
    ("split-window", "패널 분할 (-h 좌우 │ · -v/기본 상하 ─)", "패널"),
    ("kill-pane", "현재 패널 삭제", "패널"),
    ("resize-pane", "패널 크기 (-Z 줌 · -L/-R/-U/-D 분할선 이동)", "패널"),
    ("select-pane", "패널 이동 (-L/-R/-U/-D) 또는 제목 (-T)", "패널"),
    ("rename-pane", "패널 제목 변경", "패널"),
    ("swap-pane", "패널 위치 교환 (-U/-D)", "패널"),
    ("rotate-window", "윈도우 내 패널 회전", "패널"),
    ("break-pane", "패널을 새 탭으로 분리", "패널"),
    ("join-pane", "다른 탭의 패널을 현재로 합치기 (-h)", "패널"),
    ("respawn-pane", "패널 셸 재시작", "패널"),
    ("select-layout", "레이아웃 프리셋 (even-h/v, main-h/v, tiled)", "패널"),
    ("next-layout", "다음 레이아웃 프리셋", "패널"),
    ("synchronize-panes", "입력 동기화 토글 [on|off]", "패널"),
    ("new-tab", "새 탭 (새 윈도우 1개 생성, = new-window)", "탭"),
    ("kill-tab", "탭 삭제 (= kill-window)", "탭"),
    ("next-tab", "다음 탭", "탭"),
    ("previous-tab", "이전 탭", "탭"),
    ("last-tab", "직전 탭", "탭"),
    ("select-tab", "탭 선택 (-t N)", "탭"),
    ("move-tab", "탭 이동 (-t N)", "탭"),
    ("move-tab-left", "현재 탭을 왼쪽으로", "탭"),
    ("move-tab-right", "현재 탭을 오른쪽으로", "탭"),
    ("move-tab-first", "현재 탭을 맨 앞으로", "탭"),
    ("move-tab-last", "현재 탭을 맨 뒤로", "탭"),
    ("swap-tab", "탭 교환 (-t N)", "탭"),
    ("rename-tab", "탭 이름 변경", "탭"),
    ("automatic-rename", "탭 자동 이름 [on|off]", "탭"),
    ("choose-tree", "탭/패널 트리(전환·종료, 실행 앱·로컬/원격 표시. d/x=종료)", "탭"),
    ("capture-pane", "패널 내용을 버퍼로 캡처 (-S 전체)", "복사/버퍼"),
    ("pipe-pane", "패널 출력을 외부 명령으로 파이프", "복사/버퍼"),
    ("clear-history", "스크롤백 비우기", "복사/버퍼"),
    ("send-keys", "패널에 키 주입 (예: Enter, C-c)", "복사/버퍼"),
    ("send-escape", "활성 패널에 ESC 전달 (Shift+ESC 안 먹는 터미널용; ESC 모드서 ESC 한 번 더로도 가능)", "복사/버퍼"),
    ("paste-buffer", "페이스트 버퍼 붙여넣기 (N)", "복사/버퍼"),
    ("choose-buffer", "페이스트 버퍼 선택기", "복사/버퍼"),
    ("paste-clipboard", "OS 클립보드 붙여넣기", "복사/버퍼"),
    ("save-layout", "전체 레이아웃 저장(서버 영속)", "레이아웃"),
    ("restore-layout", "전체 레이아웃 복원(서버 영속)", "레이아웃"),
    ("layout-save", "현재 탭 레이아웃 저장 (이름)", "레이아웃"),
    ("layout-load", "레이아웃 불러오기 → 현재 탭 덮어쓰기 (이름)", "레이아웃"),
    ("layout-load-new", "레이아웃 불러오기 → 새 탭 (이름)", "레이아웃"),
    ("monitor-activity", "활동 모니터링 [on|off]", "모니터"),
    ("monitor-bell", "벨 모니터링 [on|off]", "모니터"),
    ("auto-resume", "토큰 리밋 자동 재개 [on|off]", "Claude"),
    ("auto-resume-message", "자동 재개 메시지 설정", "Claude"),
    ("capture-output", "패널 출력 캡처 토글 [on|off] (기본 on, 영속)", "모니터"),
    ("set", "옵션 설정 (prefix/mouse/status-*/mode-keys 등)", "설정/기타"),
    ("show-options", "현재 옵션 보기", "설정/기타"),
    ("set-hook", "이벤트 훅 설정 (<event> <cmd>)", "설정/기타"),
    ("show-hooks", "훅 목록 보기", "설정/기타"),
    ("source-file", "설정 파일 다시 불러오기", "설정/기타"),
    ("display-message", "상태줄에 메시지 표시", "설정/기타"),
    ("display-popup", "명령 실행 결과를 팝업으로", "설정/기타"),
    ("clock-mode", "현재 패널을 큰 시계로 덮기(토글, 우상단 [x]/명령으로 닫기)", "설정/기타"),
    ("calendar-mode", "현재 패널을 이번 달 달력으로 덮기(토글, 상태줄 날짜 클릭/우상단 [x])", "설정/기타"),
    ("open-clock", "현재 패널에 큰 시계 표시(이미 떠 있으면 유지)", "설정/기타"),
    ("close-clock", "현재 패널의 큰 시계 닫기", "설정/기타"),
    ("open-calendar", "현재 패널에 이번 달 달력 표시(이미 떠 있으면 유지)", "설정/기타"),
    ("close-calendar", "현재 패널의 달력 닫기", "설정/기타"),
    ("claude-header", "Claude 프롬프트 헤더 표시 on/off (claude-header on|off|toggle)", "Claude"),
    ("single-border", "패널이 하나뿐일 때 테두리 표시 on/off (single-border on|off|toggle)", "설정/기타"),
    ("coalesce-repaints", "대량 출력 시 alt-screen 풀스크린 리페인트 합치기 on/off — ssh 반응성(coalesce-repaints on|off|toggle)", "설정/기타"),
    ("prompt-history", "Claude 프롬프트 히스토리 팝업(헤더 클릭으로도 열림)", "Claude"),
    ("token-usage", "Claude 실행 중 탭/패널 + 토큰 사용량 트리(상태줄 사용량 클릭)", "Claude"),
    ("token-log", "토큰 사용량 영속 로그 집계 팝업(시간/일/월 × 계정 — h/d/m·a 전환)", "Claude"),
    ("token-account", "활성 패널 Claude 계정 수동 지정 (token-account <이름>, 빈값=자동)", "Claude"),
    ("prompt-clear", "프롬프트 단위 클리어 모드 토글(완료마다 문서화+/clear) [on|off]", "Claude"),
    ("prompt-clear-message", "프롬프트 단위 클리어의 문서화 지시문 변경", "Claude"),
    ("prompt-clear-queue", "프롬프트 단위 클리어 큐에 명령 쌓기(빈값=목록, -c=비움)", "Claude"),
    ("claude-rules", "Claude 시작 규칙 편집(저장 시 새 세션/clear 후 프롬프트에 자동 주입)", "Claude"),
    ("version", "클라/서버 버전(p4 CL)·업타임 팝업(별칭 about)", "설정/기타"),
    ("token-saver", "토큰 절감 설정 팝업 — 각 자동 개입 토글·잔량 임계·예산(별칭 claude-settings, token-settings)", "Claude"),
    ("auto-doc-clear", "Claude idle 30초 지속 시 자동 문서화+/clear on/off (auto-doc-clear on|off|toggle)", "Claude"),
    ("claude-auto-mode", "Claude idle 시 권한모드를 자동으로 오토모드로 전환 on/off (claude-auto-mode on|off|toggle)", "Claude"),
    ("run-shell", "셸 명령 실행", "설정/기타"),
    ("if-shell", "조건부 셸 실행", "설정/기타"),
    ("bind-key", "prefix 후 키에 명령 바인딩 (bind-key <key> <command>)", "설정/기타"),
    ("unbind-key", "키 바인딩 해제 (unbind-key <key> | -a)", "설정/기타"),
    ("list-keys", "현재 키 바인딩 목록 팝업", "설정/기타"),
    # help/commands/list-commands 도 자동완성 후보에 잡히게 등록(§10 #8). 입력하면
    # 전체 명령 목록(CommandListScreen)을 연다 — '?' 입력도 같은 목록을 즉시 연다.
    ("help", "전체 명령 목록 보기('?' 도 동일, 카테고리 탭)", "설정/기타"),
    ("commands", "전체 명령 목록 보기(help 별칭)", "설정/기타"),
    ("list-commands", "전체 명령 목록 보기(help 별칭)", "설정/기타"),
    ("detach-client", "detach (앱 종료, 셸 유지)", "설정/기타"),
    ("kill-server", "서버와 모든 탭/셸 종료", "설정/기타"),
    ("restart-server", "작업 보존 재시작 — 셸/PTY 를 살린 채 서버 코드만 교체(재접속)", "설정/기타"),
    ("restart-all", "전체 재시작 — 서버 세션유지 재시작 + 클라 재기동(별칭 full-restart). 서버·클라 코드 모두 갱신", "설정/기타"),
    ("restart-check", "restart-all 드라이런 — 실행 없이 안전성(re-exec·직렬화·fd·relaunch) 점검 팝업(별칭 restart-dry-run)", "설정/기타"),
    ("reconnect", "IPC 강제 재접속 — degraded(빨간 외곽선) 고착 회복(서버 보존)", "설정/기타"),
]

# 명령 프롬프트 자동완성 후보. 자주 쓰는 옵션 템플릿을 앞에 두어, 명령을 다 치면
# ghost 로 옵션(-h 등)까지 함께 제안된다(→ 로 수락). 뒤에 전체 명령 이름을 붙여
# 옵션이 없는 명령도 보완.
COMPLETIONS = [
    "split-window -h", "split-window -v",
    "resize-pane -Z",
    "resize-pane -L", "resize-pane -R", "resize-pane -U", "resize-pane -D",
    "select-pane -L", "select-pane -R", "select-pane -U", "select-pane -D",
    "swap-pane -U", "swap-pane -D",
    "join-pane -h",
    "select-layout tiled", "select-layout even-horizontal",
    "select-layout even-vertical", "select-layout main-vertical",
    "select-layout main-horizontal",
    "capture-pane -S",
    "monitor-activity on", "monitor-bell on", "automatic-rename on",
    "detach-client", "kill-server",
] + [c[0] for c in COMMANDS]

# 커맨드 팔레트(#3)에서 명령을 고르면 옵션(선택지)을 모달에서 정한 뒤 프롬프트를
# 거치지 않고 바로 실행한다. 각 항목은 {"key","label","choices":[(보임,값),...]}
# — 선택지(choice)뿐이라 모달이 키보드만으로 동작한다(자유 텍스트 인자가 필요한
# 명령은 schema 없이 기존처럼 프롬프트에 채운다). 값이 빈 문자열이면 토큰 생략.
_ONOFF = [("토글", ""), ("켜기", "on"), ("끄기", "off")]
COMMAND_OPTIONS = {
    "split-window": [{"key": "orient", "label": "방향", "choices": [
        ("좌우 분할 │ (-h)", "-h"), ("상하 분할 ─ (-v)", "-v")]}],
    "select-pane": [{"key": "dir", "label": "이동", "choices": [
        ("◀ 왼쪽", "-L"), ("▶ 오른쪽", "-R"),
        ("▲ 위", "-U"), ("▼ 아래", "-D")]}],
    "resize-pane": [{"key": "dir", "label": "동작", "choices": [
        ("줌 토글 ⛶", "-Z"), ("◀ 왼쪽", "-L"), ("▶ 오른쪽", "-R"),
        ("▲ 위", "-U"), ("▼ 아래", "-D")]}],
    "select-layout": [{"key": "preset", "label": "프리셋", "choices": [
        ("바둑판 tiled", "tiled"),
        ("가로 균등 even-horizontal", "even-horizontal"),
        ("세로 균등 even-vertical", "even-vertical"),
        ("메인 세로 main-vertical", "main-vertical"),
        ("메인 가로 main-horizontal", "main-horizontal")]}],
    "capture-pane": [{"key": "scope", "label": "범위", "choices": [
        ("보이는 영역", ""), ("스크롤백 전체 -S", "-S")]}],
    "synchronize-panes": [{"key": "state", "label": "동기화", "choices": _ONOFF}],
    "monitor-activity": [{"key": "state", "label": "활동", "choices": _ONOFF}],
    "monitor-bell": [{"key": "state", "label": "벨", "choices": _ONOFF}],
    "auto-resume": [{"key": "state", "label": "자동재개", "choices": _ONOFF}],
    "capture-output": [{"key": "state", "label": "캡처", "choices": _ONOFF}],
    "automatic-rename": [{"key": "state", "label": "자동이름", "choices": _ONOFF}],
    "prompt-clear": [{"key": "state", "label": "클리어모드", "choices": _ONOFF}],
    "auto-doc-clear": [{"key": "state", "label": "자동클리어", "choices": _ONOFF}],
    "claude-auto-mode": [{"key": "state", "label": "오토모드", "choices": _ONOFF}],
    "claude-header": [{"key": "state", "label": "헤더", "choices": _ONOFF}],
    "single-border": [{"key": "state", "label": "단일테두리", "choices": _ONOFF}],
    "coalesce-repaints": [{"key": "state", "label": "리페인트합치기", "choices": _ONOFF}],
}
# 인자 없이 바로 실행해도 되는(파괴적이지 않은) 명령 — 선택 즉시 실행한다(#3).
# kill-*/detach/respawn 등 파괴적 명령은 의도 확인을 위해 기존처럼 프롬프트에 채운다.
COMMAND_NOARG = {
    "next-tab", "previous-tab", "last-tab",
    "move-tab-left", "move-tab-right", "move-tab-first", "move-tab-last",
    "next-layout", "rotate-window", "new-tab", "choose-tree",
    "choose-buffer", "paste-clipboard", "save-layout", "restore-layout",
    "show-options", "show-hooks", "source-file", "clock-mode",
    "calendar-mode", "open-clock", "close-clock", "open-calendar",
    "close-calendar", "prompt-history", "token-usage", "token-log",
    "list-keys", "send-escape", "claude-rules", "token-saver", "version",
    "restart-check",
}
# 자유 텍스트 인자를 받는 명령 — 명령 프롬프트에서 명령을 다 치면 인자 자리에 밑줄
# (____)을 그려 "여기에 인자를 입력" 임을 알린다(사용자 요청). 선택지형(COMMAND_OPTIONS)
# 은 밑줄 대신 방향키로 고르는 토글 UI 를 쓰므로 여기 넣지 않는다. 무인자/파괴적
# 명령(detach-client·kill-* 등)도 제외해 잘못된 밑줄을 막는다.
COMMAND_FREETEXT = {
    "rename-pane", "rename-tab", "send-keys", "pipe-pane", "paste-buffer",
    "layout-save", "layout-load", "layout-load-new", "auto-resume-message",
    "set", "set-hook", "display-message", "display-popup", "run-shell",
    "if-shell", "bind-key", "unbind-key", "token-account",
    "prompt-clear-message", "prompt-clear-queue", "select-tab", "move-tab",
    "swap-tab", "resize-pane", "capture-pane", "join-pane",
}
