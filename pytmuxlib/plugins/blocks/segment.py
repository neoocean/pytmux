"""블록 경계 판정 — 셸이 보내는 OSC 133 을 블록 목록으로 바꾼다.

# 무엇이 블록인가

명령 한 번의 실행이 블록 하나다: 사용자가 친 명령, 그 출력, 종료코드, 돈 디렉토리.
서버는 이제껏 **렌더된 행**만 보냈고 명령 경계를 몰랐다 — 그 경계를 셸이 알려 준다.

# OSC 133 (시맨틱 프롬프트)

iTerm2 가 만들고 kitty·WezTerm·VSCode 가 따르는 사실상 표준이다.

    OSC 133 ; A ST   프롬프트 시작
    OSC 133 ; B ST   명령 입력 시작 (= 프롬프트 끝)
    OSC 133 ; C ST   명령 실행 시작 (= 출력 시작)
    OSC 133 ; D ; <exit> ST   명령 끝 (종료코드)

여기에 `OSC 7 ; file://<host><path> ST` 로 cwd 가 온다.

# 명령 텍스트는 OSC 633 ; E 로 온다

133 에는 "무슨 명령을 쳤나"를 알려 주는 자리가 없다(A~D 는 경계뿐). 화면에서 프롬프트
뒤를 긁어 추측하는 방법도 있으나 프롬프트 모양·줄바꿈·색에 그대로 깨진다 —
**셸이 직접 알려 주는 쪽**이 정확하다. VSCode 셸 통합이 쓰는

    OSC 633 ; E ; <명령줄> ; <nonce> ST

를 그대로 따른다(자체 코드를 만들면 다른 터미널이 이해할 여지가 없고, 633 은 모르는
터미널이 조용히 무시한다). 명령줄 안의 `;`·개행·제어문자는 **셸이 `\\xHH` 로 escape**
하므로 필드를 `;` 로 갈라도 안전하다. 633 의 나머지 하위 종류(A~D)는 **일부러 안 본다**
— 우리 통합은 경계를 133 으로 보내고, 둘 다 보면 블록이 두 번 생긴다.

# 왜 순수 함수인가

이 모듈은 화면도 서버도 모른다. 상태 기계 하나와 블록 목록뿐이라 테스트가 쉽고,
서버 쪽 배선(`__init__.py`)이 얇아진다.

# 상한이 있어야 한다

블록 목록은 **반드시 잘려야 한다**. 스크롤백이 `HISTORY` 행에서 회전하듯 블록도
상한을 넘으면 오래된 것부터 버린다. 상한 없는 목록은 이 저장소가 이미 클라 프리즈로
물린 적이 있는 부류다(HANDOFF F-G).
"""
import re
from urllib.parse import unquote, urlparse

#: 패널당 보관할 블록 수 상한. 스크롤백(HISTORY=10000 행)과 같은 뜻의 상한이며,
#: 한 화면에 보이는 것보다 넉넉하되 무한하지 않게 잡았다.
MAX_BLOCKS = 500

#: 명령 텍스트 상한(글자). OSC 는 **패널 안에서 도는 아무 프로그램이나** 보낼 수 있다 —
#: 상한이 없으면 `printf '\033]633;E;<1MB>\033\\'` 한 줄로 서버 메모리를 500배(블록 상한)
#: 로 불릴 수 있다. 사람이 치는 명령은 이보다 훨씬 짧다.
MAX_CMD_LEN = 1024

#: cwd 상한(글자). `OSC 7` 도 명령 텍스트와 **같은 신뢰 등급**(패널 안 아무 프로그램)이고
#: 같은 길로 클라 화면에 그려지므로 같은 방어를 받는다. 블록 500개가 각자 4KB(파서의
#: OSC 상한) cwd 를 들면 프레임이 MB 급으로 커진다 — 정상 경로는 수십~수백 바이트다.
MAX_CWD_LEN = 1024

#: 종료코드 허용 범위. POSIX 는 0~255(+128+signal), Windows 는 32비트 코드
#: (`0xC0000005` 같은 값)까지 나온다. 그 밖(예: `D;99999999999999999999`)은 **모른다**로
#: 떨어뜨린다 — 와이어 정수를 i64 로 읽는 네이티브 클라에서 프레임 파싱이 통째로
#: 실패하기 때문이다(한 줄 OSC 로 블록 표시를 끄는 것과 같아진다).
_EXIT_MIN, _EXIT_MAX = -(2 ** 31), 2 ** 32 - 1


class Block:
    """명령 한 번의 실행."""

    __slots__ = ("cmd", "state", "exit", "cwd", "start_row", "end_row")

    def __init__(self, cwd=None, start_row=0):
        self.cmd = ""
        #: "prompt"(입력 대기) → "running"(실행 중) → "done"(끝)
        self.state = "prompt"
        self.exit = None
        self.cwd = cwd
        #: 스크롤백 **절대** 행 번호. 뷰포트가 움직여도 안 변하는 좌표라야
        #: 스크롤한 뒤에도 블록이 제자리를 가리킨다.
        self.start_row = start_row
        self.end_row = None

    def to_wire(self):
        """클라에 보낼 형태. 값이 없는 필드는 빼서 프레임을 키우지 않는다."""
        out = {"cmd": self.cmd, "state": self.state, "start": self.start_row}
        if self.exit is not None:
            out["exit"] = self.exit
        if self.cwd:
            out["cwd"] = self.cwd
        if self.end_row is not None:
            out["end"] = self.end_row
        return out

    def __repr__(self):                                   # pragma: no cover - 진단용
        return f"<Block {self.state} {self.cmd!r} exit={self.exit}>"


class Segmenter:
    """OSC 133 열을 블록 목록으로 바꾸는 상태 기계.

    셸 통합이 없으면 아무 일도 일어나지 않는다 — 블록이 하나도 안 생기고, 그건
    **정상 동작**이다(우아한 저하). 그때 클라는 종전처럼 화면만 그린다.
    """

    def __init__(self, max_blocks=MAX_BLOCKS):
        self.blocks = []
        self.cwd = None
        self._max = max_blocks

    # ---- OSC 입력 ----------------------------------------------------------

    def on_osc(self, code, param, row=0):
        """OSC 하나를 반영한다. 목록이 바뀌었으면 True.

        `row` 는 지금 커서가 있는 **절대** 스크롤백 행이다. 호출부가 넘겨 준다.
        """
        if code == "7":
            return self._on_cwd(param)
        if code == "633":
            return self._on_vscode(param)
        if code != "133":
            return False
        kind, _, rest = param.partition(";")
        if kind == "A":
            return self._on_prompt_start(row)
        if kind == "B":
            return self._on_command_start()
        if kind == "C":
            return self._on_output_start(row)
        if kind == "D":
            return self._on_command_end(rest)
        # 모르는 하위 종류(예: P;k=...)는 조용히 무시한다 — 셸이 확장 필드를 보내도
        # 블록이 깨지지 않아야 한다.
        return False

    def _on_vscode(self, param):
        """OSC 633. 우리가 보는 것은 **명령 텍스트(E)뿐**이다.

        A~D 를 함께 보면 VSCode 통합과 우리 통합이 같이 걸린 셸에서 블록이 두 번
        생긴다. 경계는 133 한 곳에서만 판정한다.
        """
        kind, _, rest = param.partition(";")
        if kind != "E":
            return False
        # `E;<명령줄>;<nonce>` — nonce 는 우리 관심 밖이다. 명령줄 안의 `;` 는
        # escape 돼 오므로 첫 필드만 떼면 된다.
        raw = rest.split(";")[0]
        return self.set_command(_unescape(raw))

    def _on_cwd(self, param):
        """`OSC 7 ; file://<host>/<path>`. 경로만 쓰고 호스트는 버린다.

        **살균은 여기서**(퍼센트 디코드 **뒤**)다 — `file:///%1b]0;x%07` 처럼 unquote 가
        제어문자를 *만들어 내는* 것이 이 경로의 위험이다. cwd 는 cmd 와 똑같이 클라가
        화면에 그리고(블록 머리줄) 네이티브 클라는 Claude 뷰의 폴더 판정에도 쓰므로,
        cmd 와 같은 방어(제어문자 접기 + 길이 상한)를 받아야 한다.
        """
        path = _sanitize_path(_parse_file_url(param))
        if not path or path == self.cwd:
            return False
        self.cwd = path
        # 아직 명령이 안 시작된 블록이면 cwd 를 소급 반영한다 — 셸이 프롬프트 직전에
        # cwd 를 보내는 순서라 이게 자연스럽다.
        if self.blocks and self.blocks[-1].state == "prompt":
            self.blocks[-1].cwd = path
        return True

    def _on_prompt_start(self, row):
        # 직전 블록이 안 끝났으면 여기서 끝난 것으로 본다 — 셸이 D 를 못 보내고
        # 죽는 경우(Ctrl-C, 셸 재시작)가 있고, 그때 목록이 영원히 "실행 중"으로
        # 남으면 사용자가 보기에 멈춘 것과 같다.
        if self.blocks and self.blocks[-1].state != "done":
            self.blocks[-1].state = "done"
            self.blocks[-1].end_row = row
        self.blocks.append(Block(cwd=self.cwd, start_row=row))
        self._trim()
        return True

    def _on_command_start(self):
        # B 는 프롬프트가 끝나고 입력이 시작되는 지점이다. 상태는 그대로 두고
        # (아직 실행 전) 표식만 남긴다 — 현재는 별도 필드가 필요 없다.
        return False

    def _on_output_start(self, row):
        if not self.blocks:
            # A 없이 C 만 오는 셸/부분 통합. 블록을 여기서 시작한다.
            self.blocks.append(Block(cwd=self.cwd, start_row=row))
            self._trim()
        block = self.blocks[-1]
        block.state = "running"
        return True

    def _on_command_end(self, rest):
        if not self.blocks:
            return False
        block = self.blocks[-1]
        block.state = "done"
        block.exit = _parse_exit(rest)
        return True

    # ---- 명령 텍스트 -------------------------------------------------------

    def set_command(self, text):
        """현재 블록의 명령 문자열. 셸이 `OSC 633;E` 로 알려 주거나, 호출부가
        화면에서 뽑아 넣는다.

        **끝난 블록에는 안 쓴다** — 지나간 명령의 이름이 뒤늦은 한 줄로 바뀌면
        사용자가 보던 목록이 조용히 뒤틀린다. 열린 블록이 없으면 그냥 버린다
        (프롬프트 없이 명령 텍스트만 오는 셸은 애초에 블록을 못 만든다).
        """
        if not self.blocks or self.blocks[-1].state == "done":
            return False
        text = _sanitize_cmd(text)
        if self.blocks[-1].cmd == text:
            return False
        self.blocks[-1].cmd = text
        return True

    # ---- 스크롤백 회전 -----------------------------------------------------

    def drop_before(self, row):
        """절대 행 `row` 앞에서 끝난 블록을 버린다.

        스크롤백이 회전해 그 행이 사라지면 블록도 함께 사라져야 한다 — 안 그러면
        존재하지 않는 행을 가리키는 블록이 쌓인다.
        """
        before = len(self.blocks)
        self.blocks = [
            b for b in self.blocks
            if b.end_row is None or b.end_row >= row
        ]
        return len(self.blocks) != before

    def _trim(self):
        if len(self.blocks) > self._max:
            del self.blocks[: len(self.blocks) - self._max]

    def to_wire(self):
        return [b.to_wire() for b in self.blocks]


#: `/D:/…` · `/D:\…` 처럼 드라이브 문자가 슬래시 뒤에 오는 URL 경로.
_DRIVE_PREFIX = re.compile(r"^/[A-Za-z]:[/\\]")


def _parse_file_url(param):
    """`file://host/path` 에서 경로만. 형식이 아니면 None.

    **Windows 드라이브 경로는 앞의 `/` 를 뗀다.** `file:///D:/a/b` 의 URL 경로는
    규격상 `/D:/a/b` 인데, 그 `/` 가 남으면 cwd 가 `/D:/a/b` 가 되고 네이티브 클라의
    Claude 뷰가 폴더 이름을 `-D--a-b` 로 만든다(구분자·콜론이 전부 `-` 로 바뀌므로
    맨 앞 슬래시도 `-` 가 된다). 실제 Claude Code 가 쓰는 이름은 `D--a-b` 라
    **한 글자 차이로 못 찾고**, 증상은 오류가 아니라 "세션이 없다"다 — 조용해서 더
    나쁘다. (2026-07-27 alienware 박스에서 실제 `~/.claude/projects` 이름과 대조.)
    """
    if not param:
        return None
    try:
        parsed = urlparse(param)
    except ValueError:
        return None
    if parsed.scheme != "file" or not parsed.path:
        return None
    path = unquote(parsed.path)
    if _DRIVE_PREFIX.match(path):
        path = path[1:]
    return path


def _unescape(text):
    """셸이 `\\xHH`·`\\\\` 로 escape 한 명령줄을 되돌린다(VSCode 633 규약).

    한 번에 왼쪽에서 오른쪽으로 훑는다 — `\\\\x3b`(백슬래시 + 글자 x3b)를 `;` 로
    잘못 푸는 것을 막으려면 `\\\\` 를 먼저 소비해야 한다. escape 가 아닌 백슬래시는
    **그대로 둔다**: escape 를 안 하는 셸이 보낸 `C:\\path` 가 사라지면 안 된다.
    """
    if "\\" not in text:
        return text
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1:i + 2]
        if nxt == "\\":
            out.append("\\")
            i += 2
            continue
        hexpart = text[i + 2:i + 4]
        if nxt in ("x", "X") and len(hexpart) == 2:
            try:
                out.append(chr(int(hexpart, 16)))
            except ValueError:
                out.append(ch)
                i += 1
                continue
            i += 4
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _sanitize_cmd(text):
    """블록에 실을 명령 텍스트를 안전한 모양으로 만든다.

    ① **제어문자를 공백으로 접는다**: 이 문자열은 클라가 화면에 그대로 그린다. `\\x1b`
    가 살아 있으면 패널 안의 아무 프로그램이나 OSC 한 줄로 **사용자 단말에 이스케이프를
    주입**할 수 있다(원격 유래 문자열에 `_strip_ctrl` 을 쓰는 것과 같은 이유). 지우지
    않고 **공백으로** 바꾸는 것은 여러 줄 명령이 `echo a`+`echo b` → `echo aecho b` 로
    붙어 버리지 않게 하기 위해서다.
    ② **길이를 자른다**(`MAX_CMD_LEN`).
    """
    if not isinstance(text, str):
        return ""
    cleaned = _fold_ctrl(text)
    return cleaned[:MAX_CMD_LEN]


def _sanitize_path(text):
    """블록에 실을 cwd. `_sanitize_cmd` 와 같은 규율(제어문자 접기 + 길이 상한)."""
    if not isinstance(text, str) or not text:
        return None
    return _fold_ctrl(text)[:MAX_CWD_LEN] or None


def _fold_ctrl(text):
    """C0/DEL/C1 을 공백으로 접는다. 지우지 않는 이유는 `_sanitize_cmd` ① 참고."""
    return "".join(
        " " if (ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f) else c
        for c in text
    )


def _parse_exit(rest):
    """`D` 뒤의 종료코드. 없거나 숫자가 아니거나 **범위 밖이면** None(= 모른다)."""
    first = rest.split(";")[0].strip() if rest else ""
    if not first:
        return None
    try:
        code = int(first)
    except ValueError:
        return None
    return code if _EXIT_MIN <= code <= _EXIT_MAX else None


def row_span(wire, index, live_bottom):
    """와이어 블록 목록에서 `index` 번째가 차지하는 **절대 행 범위**(양끝 포함).

    ⛔ **이 판정은 한 자리여야 한다.** 강조를 그리는 곳과 복사할 범위를 정하는 곳이
    각자 세면 화면에 밝은 것과 클립보드에 담기는 것이 조용히 어긋난다 — 네이티브
    클라도 같은 이유로 한 함수를 쓴다(`proto::blocks::row_span`, 이것과 같은 규칙).

    끝을 어디서 얻나는 세 갈래다:

    - 블록이 `end` 를 들고 있으면 그것(`OSC 133;D` 가 왔다).
    - 없으면 **다음 블록의 시작 한 줄 앞**. `D` 만 오고 `A` 가 아직 안 온 블록이
      그 모양이다.
    - 그것도 없으면(마지막 블록) **지금 살아 있는 마지막 줄**. 아직 자라는 중이라
      물어볼 데가 없고, 물어볼 수 있는 것은 "지금까지 어디까지 찼나"뿐이다.

    ⚠ 한 줄짜리 블록(프롬프트에서 그냥 Enter)에서 **범위가 뒤집히면 안 된다** —
    뒤집힌 범위는 엉뚱한 데를 복사한다. 그래서 끝은 시작보다 앞설 수 없다.
    """
    try:
        block = wire[index]
    except (IndexError, TypeError, KeyError):
        return None
    start = _wire_row(block.get("start"))
    if start is None:
        return None
    end = _wire_row(block.get("end"))
    if end is None:
        nxt = wire[index + 1] if index + 1 < len(wire) else None
        nxt_start = _wire_row(nxt.get("start")) if isinstance(nxt, dict) else None
        end = (nxt_start - 1) if nxt_start is not None else live_bottom
    return (start, max(start, end))


def _wire_row(value):
    """와이어 정수 → 행 번호. 정수가 아니면 None, 음수는 0 으로 접는다.

    상류가 신뢰할 수 없는 값을 실을 수 있다(원격 링크 너머의 서버는 이 버전이 아닐 수
    있고, 블록의 글자는 애초에 **패널 안 아무 프로그램**이 보낸 OSC 다). 여기서 접지
    않으면 `range()` 가 TypeError 로 클라를 죽인다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, value)
