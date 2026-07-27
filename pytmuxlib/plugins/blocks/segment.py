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

# 왜 순수 함수인가

이 모듈은 화면도 서버도 모른다. 상태 기계 하나와 블록 목록뿐이라 테스트가 쉽고,
서버 쪽 배선(`__init__.py`)이 얇아진다.

# 상한이 있어야 한다

블록 목록은 **반드시 잘려야 한다**. 스크롤백이 `HISTORY` 행에서 회전하듯 블록도
상한을 넘으면 오래된 것부터 버린다. 상한 없는 목록은 이 저장소가 이미 클라 프리즈로
물린 적이 있는 부류다(HANDOFF F-G).
"""
from urllib.parse import unquote, urlparse

#: 패널당 보관할 블록 수 상한. 스크롤백(HISTORY=10000 행)과 같은 뜻의 상한이며,
#: 한 화면에 보이는 것보다 넉넉하되 무한하지 않게 잡았다.
MAX_BLOCKS = 500


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

    def _on_cwd(self, param):
        """`OSC 7 ; file://<host>/<path>`. 경로만 쓰고 호스트는 버린다."""
        path = _parse_file_url(param)
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
        """현재 블록의 명령 문자열. 셸이 `OSC 133;B` 뒤에 알려 주거나, 호출부가
        화면에서 뽑아 넣는다."""
        if not self.blocks:
            return False
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


def _parse_file_url(param):
    """`file://host/path` 에서 경로만. 형식이 아니면 None."""
    if not param:
        return None
    try:
        parsed = urlparse(param)
    except ValueError:
        return None
    if parsed.scheme != "file" or not parsed.path:
        return None
    return unquote(parsed.path)


def _parse_exit(rest):
    """`D` 뒤의 종료코드. 없거나 숫자가 아니면 None(= 모른다)."""
    first = rest.split(";")[0].strip() if rest else ""
    if not first:
        return None
    try:
        return int(first)
    except ValueError:
        return None
