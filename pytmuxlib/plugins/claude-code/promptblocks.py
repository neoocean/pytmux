"""Claude 패널의 **턴 경계** — 프롬프트 마커 줄 하나부터 다음 마커 직전까지.

# 왜 셸 블록과 따로인가 (제보 §10-21ⓤ2 / pytmux-21)

블록 선택(pytmux-18)은 「명령 하나 + 그 출력」을 한 덩이로 골라 복사한다. 그 경계는
셸이 **OSC 133** 으로 알려 주는데(`plugins/blocks`), **Claude 는 OSC 를 안 보낸다** —
그래서 Claude 패널에서는 그 기능이 통째로 죽어 있었다.

대신 흔적이 화면 글에 남는다: 제출된 프롬프트는 열 0 의 `> `/`❯ ` 로 시작한다. 그
판정은 이미 **한 곳**에 있고(`claude.claude_prompt_marks` — 프롬프트 점프 `esc ctrl+↑/↓`
가 쓴다) 여기서는 그것을 **범위**로 바꿔 같은 블록 와이어에 실을 뿐이다. 클라는 블록의
출처를 모른다 — 고르기·강조·복사가 **한 벌**로 남는다는 것이 이 설계의 전부다.

# ⛔ 셸 패널에는 걸지 않는다

`> ` 로 시작하는 **인용·diff 를 프롬프트로 오인**하기 때문이다(정본 `claude_jump_prompt`
가 같은 이유로 `_claude` 를 먼저 본다). 진입점(`wire`)이 그 게이트를 지킨다.

# 왜 전체 스크롤백을 매 프레임 다시 안 훑나

블록 프레임은 flush 마다 「바뀌었나」를 묻는다(30Hz). 그런데 스크롤백은 패널당
`HISTORY`=1만 행이고, 그것을 `line_text` 로 통째로 문자열화하면 **실측 0.45초**다
(200열 × 1만 행 = 200만 칸). 첫 칸만 보는 축소는 같은 조건에서 **4.4ms** 이고, 거기에
아래 두 가지를 더해 평상시 비용을 거의 0 으로 만든다:

- **히스토리는 뒤로만 자란다** — 이미 밀려난 줄은 안 바뀐다. 그래서 마커 목록을 들고
  있다가 **새로 들어온 줄만** 본다. 화면 몫(수십 행)만 매번 다시 본다.
- deque 가 가득 차면 앞에서 밀려나 **행 번호가 통째로 왼쪽으로 민다.** 몇 줄이
  밀렸는지는 지난번 마지막 줄 객체를 뒤에서 찾아 센다(`_SEARCH_BACK` 안에서). 못
  찾으면 통째로 다시 센다 — 느려도 **틀리지는 않는다**.

# 좌표계는 셸 블록과 같다

행 번호는 `히스토리 + 화면 버퍼` 를 이어 붙인 목록의 인덱스다. 서버가 `screen` 메시지에
싣는 `top`(`model.Pane.render` 의 `start`)과 `plugins/blocks` 의 `_absolute_row` 가 같은
좌표를 쓴다 — 셋이 갈리면 **강조한 자리와 복사되는 자리가 어긋난다.**
"""
from pytmuxlib.model import line_text

from .claude import PROMPT_MARK_FIRST, claude_prompt_marks

#: 패널당 보관할 턴 수 상한. 셸 블록(`plugins/blocks.MAX_BLOCKS`)과 같은 값이고 같은
#: 이유다 — 상한 없는 목록은 이 저장소가 이미 클라 프리즈로 물린 부류다.
MAX_BLOCKS = 500

#: 프롬프트 글 상한(글자). 셸 블록의 명령 텍스트와 같은 규율이다. 이 문자열은 클라가
#: 요약 판에 그대로 그리므로 길이도 제어문자도 여기서 잘린다.
MAX_CMD_LEN = 1024

#: 스크롤백이 회전했을 때 「몇 줄이 밀렸나」를 되짚어 보는 최대 줄 수. 한 프레임에
#: 이보다 많이 밀렸으면(대량 출력 폭주) 전량 재훑기로 떨어진다 — 드물고, 그때도 답은
#: 맞다. 값이 크면 폭주 프레임이 비싸지고 작으면 전량 재훑기가 잦아진다.
_SEARCH_BACK = 4096

#: 패널에 붙는 캐시를 담는 속성명. 플러그인 네임스페이스를 지켜 코어 필드와 안 섞인다.
_ATTR = "_claude_prompt_blocks"


def _first_char(line):
    """줄의 **첫 칸** 글자. 못 읽으면 빈 문자열.

    와이드 글자의 stub(`data == ""`)도 빈 문자열이라 자연히 후보에서 빠진다 — 마커는
    폭 1 글자다.
    """
    try:
        return line[0].data or ""
    except (AttributeError, KeyError, IndexError, TypeError):
        return ""


def _mark_text(line, columns):
    """이 줄이 **제출된 프롬프트** 줄이면 그 글자, 아니면 None.

    판정은 `claude_prompt_marks` 한 곳이 한다. 첫 칸을 먼저 보는 것은 그 정규식이 열 0 에
    못 박혀 있다는 사실을 쓰는 **축소**일 뿐이다(모듈 머리말 참조).
    """
    if _first_char(line) not in PROMPT_MARK_FIRST:
        return None
    text = line_text(line, 0, max(columns - 1, 0)).rstrip()
    return text if claude_prompt_marks([text]) else None


def _scan(lines, columns, base=0):
    """줄 묶음에서 `(절대 행, 글자)` 목록. `base` 는 첫 줄의 절대 행 번호다."""
    out = []
    for offset, line in enumerate(lines):
        text = _mark_text(line, columns)
        if text is not None:
            out.append((base + offset, text))
    return out


class _HistoryMarks:
    """스크롤백 몫의 마커 목록 — **새로 들어온 줄만** 보태는 캐시.

    화면 몫은 여기 없다: 화면 버퍼는 제자리에서 고쳐 쓰이므로(같은 줄 객체의 칸이
    바뀐다) 캐시할 수 없고, 대신 수십 행뿐이라 매번 다시 봐도 싸다.
    """

    __slots__ = ("marks", "seen", "tail", "columns")

    def __init__(self):
        self.marks = []       # [(절대 행, 글자)] — 히스토리 몫만
        self.seen = 0         # 이미 셈한 히스토리 줄 수
        self.tail = None      # 그때의 마지막 줄 객체(회전량을 재는 앵커)
        self.columns = None   # 그때의 화면 폭(바뀌면 글자가 달라진다)

    def reset(self):
        self.marks = []
        self.seen = 0
        self.tail = None

    def update(self, top, columns):
        """스크롤백 deque `top` 을 반영하고 히스토리 몫 마커를 돌려준다."""
        lines = None
        hlen = len(top)
        if columns != self.columns:
            # 리사이즈: 같은 줄이라도 잘리는 자리가 달라져 글자가 바뀐다.
            self.columns = columns
            self.reset()
        if hlen < self.seen:
            self.reset()                      # clear_history·reset — 줄어드는 길은 그것뿐
        if hlen == self.seen and (self.tail is None or
                                  (hlen and top[-1] is self.tail)):
            return self.marks                 # 새로 들어온 줄이 없다
        dropped = 0
        if self.tail is not None:
            lines = list(top)
            dropped = self._dropped(lines)
            if dropped is None:               # 앵커를 못 찾았다 → 통째로 다시
                self.reset()
                dropped = 0
        if dropped:
            # 앞에서 `dropped` 줄이 사라졌다 — 남은 마커를 왼쪽으로 밀고, 밀려난 것은 버린다.
            self.marks = [(row - dropped, text)
                          for row, text in self.marks if row >= dropped]
        first_new = self.seen - dropped
        if lines is None:
            lines = list(top)
        self.marks.extend(_scan(lines[first_new:], columns, base=first_new))
        del self.marks[:max(0, len(self.marks) - MAX_BLOCKS)]
        self.seen = hlen
        self.tail = lines[-1] if lines else None
        return self.marks

    def _dropped(self, lines):
        """앞에서 밀려난 줄 수. 앵커를 못 찾으면 None(= 전량 재훑기)."""
        want = self.tail
        stop = max(-1, len(lines) - 1 - _SEARCH_BACK)
        for i in range(len(lines) - 1, stop, -1):
            if lines[i] is want:
                return max(0, (self.seen - 1) - i)
        return None


def _fold_ctrl(text):
    """C0/DEL/C1 을 공백으로 접는다 — 클라가 이 글자를 그대로 그린다.

    지우지 않고 공백으로 바꾸는 것은 `plugins/blocks.segment._sanitize_cmd` 와 같은
    규율이다(글자가 서로 붙지 않게).
    """
    return "".join(
        " " if (ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f) else c
        for c in text
    )


def _prompt_body(text):
    """마커 줄에서 **사용자가 친 글**만. 마커와 뒤따르는 공백을 뗀다."""
    return _fold_ctrl(text[1:].strip())[:MAX_CMD_LEN]


def marks(pane):
    """이 패널의 프롬프트 마커 `(절대 행, 글자)` 목록. Claude 패널이 아니면 [].

    ⛔ 셸 패널을 거르는 것은 **여기 한 곳**이다(모듈 머리말 ⛔ 항목).
    """
    if not getattr(pane, "_claude", None):
        return []
    screen = getattr(pane, "screen", None)
    if screen is None:
        return []
    history = getattr(screen, "history", None)
    if history is None:
        return []                       # 대체 화면(alt) — 스크롤백이 없다
    columns = screen.columns
    state = getattr(pane, _ATTR, None)
    if state is None:
        state = _HistoryMarks()
        setattr(pane, _ATTR, state)
    hist = state.update(history.top, columns)
    base = len(history.top)
    live = _scan((screen.buffer[y] for y in range(screen.lines)), columns, base=base)
    out = hist + live
    return out[-MAX_BLOCKS:]


def _build_wire(pane):
    """이 패널의 턴을 블록 와이어 형태로. 턴이 없으면 None(= 보낼 것 없음).

    # `end` 를 안 싣는 이유

    클라는 끝을 셋에서 고른다: `end` → **다음 블록의 시작** → 라이브 하단
    (`proto::blocks::row_span`). 마커 목록에서 다음 블록의 시작이 곧 이 턴의 끝 다음
    줄이라, `end` 를 실으면 같은 값을 두 번 적는 셈이다. 마지막 턴은 아직 자라는
    중이므로 끝이 없는 것이 맞다.

    # 상태를 `turn` 으로 두는 이유

    턴에는 종료코드가 없다. `done` + `exit` 없음을 클라는 **"끝났는데 종료코드를
    모른다"**(`Tone::Unknown` · 노랑)로 읽는데, 그건 초록(성공)도 빨강(실패)도 아닌
    자리라 거짓말은 아니지만 **턴은 애초에 성패를 갖지 않는다** — 요약 판에 노란 `??`
    가 줄줄이 뜨면 사용자는 뭔가 잘못됐다고 읽는다. 그래서 `turn` 을 보낸다.
    이 값을 모르는 **옛 클라**는 「아직 진행 중」으로 우아하게 떨어진다
    (`BlockState::parse` 의 기본값 — 모르는 상태를 '끝났다'로 넘겨짚지 않는다).
    """
    found = marks(pane)
    if not found:
        return None
    return [{"cmd": _prompt_body(text), "state": "turn", "start": row}
            for row, text in found]


def wire(pane):
    """이 패널의 턴 목록(와이어). **한 프레임 안에서는 한 번만 짓는다.**

    flush 한 번이 이 값을 **세 번** 묻는다(`blocks_dirty` → `clear_blocks_dirty` →
    `blocks_wire`). 셋이 각자 지으면 턴 수백 개짜리 dict 목록을 프레임마다 세 벌 만드는
    셈이라, 출력 판이 바뀔 때(`Pane._feed_seq`)만 다시 짓는다. 기하가 바뀌면 잘리는
    자리가 달라지므로 그것도 토큰에 넣는다.
    """
    screen = getattr(pane, "screen", None)
    history = getattr(screen, "history", None)
    token = (getattr(pane, "_feed_seq", 0), id(screen),
             getattr(screen, "columns", 0), getattr(screen, "lines", 0),
             len(history.top) if history is not None else 0)
    cached = getattr(pane, _ATTR + "_wire", None)
    if cached is not None and cached[0] == token:
        return cached[1]
    payload = _build_wire(pane)
    setattr(pane, _ATTR + "_wire", (token, payload))
    return payload


def dirty(pane):
    """마지막으로 보낸 뒤 턴 목록이 바뀌었나.

    ★ **여기가 매 프레임 도는 자리**다(flush 는 dirty 한 패널마다 이것을 묻는다).
    Claude 패널이 아니면 첫 줄에서 끝나고, 맞으면 증분 스캔(모듈 머리말)이 답한다.
    """
    return wire(pane) != getattr(pane, _ATTR + "_sent", None)


def clear_dirty(pane):
    """방금 보낸 목록을 기억한다 — 같은 목록을 매 프레임 다시 보내지 않게."""
    setattr(pane, _ATTR + "_sent", wire(pane))
