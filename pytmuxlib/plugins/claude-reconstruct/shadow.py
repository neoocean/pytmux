"""claude-reconstruct — Claude 패널 출력의 **투명 재구성 에뮬레이터**(shadow).

설계 전환(2026-06-29 야간): 이전 두 시도(라이브 패널 오버레이·트랜스크립트 모달 뷰)를
버리고, **Claude 의 raw VT 바이트 스트림을 중간에서 가로채 별도 단말 에뮬레이터에
먹이고 그 깨끗한 격자를 패널 자리에 그대로 되돌린다.** 입력은 평소대로 Claude 실 PTY 로
가므로(우리는 표시만 가로챈다) 완전히 투명하다.

왜 이게 깨짐을 없애나(실측 확증, captures/*.claude-1 replay):
  Claude Code 는 모호폭(EAW='A': `· — → … ⏵`)을 **항상 1칸**으로 가정하고 증분 VT 를
  흘린다. 사용자 단말이 CJK(wide)면 pytmux 가 단말에 맞춰 pyte 격자를 모호폭=2 로 돌려,
  Claude 가 채운 줄이 우리 격자에서 1칸씩 밀려 **이후 모든 증분이 빗나가고 격자가
  겹쳐 굳는다**(boundary 비교: WIDE 격자는 우측에 잡문자·공백잠식·줄겹침; NARROW 격자는
  동일 바이트가 깨끗). → shadow 에뮬레이터를 **모호폭=narrow(1)** 로 고정해 Claude 의
  가정과 일치시키면 격자가 절대 발산하지 않는다. 클라 합성(`_composite`)은 행의
  텍스트 런을 좌→우로 **재배치**(서버 격자 좌표를 안 믿음)하므로, 이 깨끗한 narrow 행은
  wide 클라에서 렌더해도 **겹치지 않는다**(잔여: 모호폭 많은 줄이 우측에서 잘릴 수 있음 —
  앱(1칸)≠단말(2칸)의 근본 한계라 겹침<잘림으로 격하될 뿐, AMBIGUOUS_WIDTH 문서 §근본).

이 모듈은 코어 `model.Pane` 을 **그대로 재사용**한다 — 자작 VT 파서를 또 만들면 native
토크나이저·alt 라우팅·soft-wrap·콜론SGR 처리가 드리프트한다. shadow 패널은 PTY 가 없는
(pty=None·fd=-1) 렌더 전용 패널이라 resize/winsize 통지가 무해하다(model 주석 참조).
textual/rich 를 import 하지 않는다(서버 프로세스에서 돈다)."""
from __future__ import annotations

from pytmuxlib import cellwidth
from pytmuxlib.model import Pane

# shadow 패널 생성 중임을 알리는 모듈 플래그 — 다른 플러그인의 pane_init 가 shadow 에
# 대해서는 가벼운 경로를 타도록(우리 플러그인은 이 플래그를 보고 shadow 에 자기 필드를
# 안 심어 재진입을 막는다). 서버는 단일 스레드 asyncio 라 재진입 없음.
_CREATING = False


def _new_pane(cols: int, rows: int) -> Pane:
    """렌더 전용 shadow Pane 생성. Pane.__init__ 은 plugins.pane_init 를 부르므로
    생성 동안 _CREATING 를 세워 우리 pane_init 가 shadow 에 shadow 를 또 만들지
    않게 한다(다른 플러그인 pane_init 는 순수 속성설치라 무해)."""
    global _CREATING
    _CREATING = True
    try:
        return Pane(-1, -1, max(2, cols), max(2, rows))
    finally:
        _CREATING = False


def creating() -> bool:
    return _CREATING


class Shadow:
    """한 Claude 패널에 대응하는 모호폭=narrow 고정 단말 에뮬레이터.

    같은 raw 바이트를 실 패널과 **병렬로** 먹어, Claude 의 1칸 가정과 일치하는 깨끗한
    격자를 유지한다. `feed` 는 동기(이벤트 루프 양보 없음)라 모호폭 전역 토글이 원자적이다."""

    __slots__ = ("pane", "fed_bytes")

    def __init__(self, cols: int, rows: int):
        self.pane = _new_pane(cols, rows)
        self.fed_bytes = 0

    @property
    def cols(self) -> int:
        return self.pane.cols

    @property
    def rows(self) -> int:
        return self.pane.rows

    def resize(self, cols: int, rows: int) -> None:
        cols, rows = max(2, int(cols)), max(2, int(rows))
        if cols != self.pane.cols or rows != self.pane.rows:
            self.pane.resize(cols, rows)

    def feed(self, data: bytes) -> None:
        """raw 바이트를 **모호폭=narrow** 로 먹인다. 전역 `_AMBIG_WIDE`(wide 단말이면
        True)를 feed 동안만 False 로 내려 pyte 격자가 모호폭을 1칸으로 advance 하게 한다 —
        pyte 의 draw 가 매 문자 `pyte.screens.wcwidth`(= cellwidth._pyte_w, 호출 시점에
        `_AMBIG_WIDE` 를 읽음)를 부르므로 플래그만 내리면 충분하다. feed 는 동기라 토글
        창에 다른 코드가 끼지 않고, char_cells 캐시는 이 창에서 호출되지 않아 오염 없다."""
        if not data:
            return
        saved = cellwidth._AMBIG_WIDE
        if saved:
            cellwidth._AMBIG_WIDE = False
        try:
            self.pane.feed(data)
        finally:
            if saved:
                cellwidth._AMBIG_WIDE = saved
        self.fed_bytes += len(data)

    def render(self):
        """(rows, cursor) — rows = 행마다 [text, style] 런 목록(코어와 동일 포맷),
        cursor = [x, y] 또는 None. 행 직렬화는 셀 data 만 읽어 폭 모델과 무관하다
        (narrow feed 가 이미 셀을 제 위치에 앉혔다). 커서는 with_cursor=True 로 받는다."""
        return self.pane.render(True)

    @property
    def alt_active(self) -> bool:
        return bool(getattr(self.pane, "alt_active", False))
