"""클라이언트 위젯 — 패널 합성 뷰(MultiplexerView)·상단 탭바(TabBar)·하단
상태표시줄(StatusBar).

client.py 의 거대 클로저(build_client_app)에서 분리(§10 LLM 친화 리팩토링).
config/sock_path 미캡처 — 데이터는 status/layout 메시지로 받고 앱 상호작용은
self.app 으로 런타임에 한다. client.py(PytmuxApp)가 이름으로 import 해 compose 에
쓴다."""
from __future__ import annotations

import os
import socket
from datetime import datetime

from textual.widget import Widget
from textual import events
from textual.geometry import Region
from textual.strip import Strip
from textual.suggester import SuggestFromList
from rich.segment import Segment
from rich.style import Style

from . import clientnotices, i18n
from .clientutil import (_DATE_STRFTIME, _TIME_STRFTIME, REMOTE_PINK,
                         _char_cells, _deemoji_text, norm_sep, path_at,
                         remote_title_display, theme_color)
from .keymap import drag_copy_policy


def _backdrop_dim_active(app) -> bool:
    """반투명 모달(팝업)이 떠 본문을 어둡게 깔고 있는지. 상태표시줄·탭바처럼 _composite
    그리드 밖의 별도 위젯이 backdrop 딤 중 컬러 이모지를 placeholder 로 바꿔야 하는지
    판단한다 — Textual backdrop 은 셀 스타일색만 블렌딩해 컬러 이모지 글리프(⚠ 등)는
    안 어두워지기 때문(#25)."""
    stack = getattr(app, "screen_stack", None) if app is not None else None
    if not stack or len(stack) <= 1:
        return False
    return not getattr(stack[-1], "_no_backdrop_dim", False)


def _deemoji_strip_if_dim(app, strip: Strip) -> Strip:
    """backdrop 딤 중이면 strip 의 컬러 이모지 글리프를 폭 보존 placeholder(·)로 치환한
    새 Strip 을, 아니면 원본 그대로 돌려준다(#25). _deemoji_text 가 폭을 보존하므로
    클릭존·우측정렬 회계는 변하지 않는다. 상태표시줄·탭바가 공유한다."""
    if not _backdrop_dim_active(app):
        return strip
    segs = [Segment(_deemoji_text(seg.text), seg.style, seg.control)
            for seg in strip]
    return Strip(segs, strip.cell_length)


class SepInsensitiveSuggester(SuggestFromList):
    """ghost 자동완성에서 공백·언더바·하이픈을 동일 취급한다(norm_sep). §5.4 에서 client.py
    의 build_client_app 팩토리(거대 PytmuxApp 옆) 안 지역 클래스를 모듈로 빼낸 것.

    'rename_'·'rename ' 를 쳐도 'rename-tab' 를 제안 — 명령 검색이 구분자 선택에
    좌우되지 않게 한다. 후보·입력을 모두 norm_sep 로 통일해 prefix 비교."""
    def __init__(self, suggestions, *, case_sensitive=False):
        sugg = list(suggestions)
        super().__init__(sugg, case_sensitive=case_sensitive)
        # base 의 casefold 는 부모와 동일 규칙(case_sensitive=False → casefold).
        base = sugg if case_sensitive else [s.casefold() for s in sugg]
        self._sep_orig = sugg
        self._sep_norm = [norm_sep(s) for s in base]

    async def get_suggestion(self, value):
        # 부모 _get_suggestion 이 case_sensitive=False 면 value 를 이미 casefold 함.
        v = norm_sep(value)
        for orig, norm in zip(self._sep_orig, self._sep_norm):
            if norm.startswith(v):
                return orig
        return None


class MultiplexerView(Widget):
    can_focus = True

    def __init__(self):
        super().__init__(id="view")
        self._cells: list[list] = []
        # §10-21ⓧ2 마우스가 올라온 경로 범위 `(y, x0, x1, 전체경로)`. 밑줄을 그 자리에
        # 긋고, 클릭하면 전체 경로를 복사한다.
        self._span_hover = None
        self._dragging = None  # (split_id, orient, rect)
        self._hover_divider = None  # 마우스가 올라간 경계선 rect (x,y,w,h)
        self._sel = None       # 선택 영역 (x0,y0,x1,y1) 전역 좌표
        self._sel_start = None
        self._sel_rect = None  # 선택 시작 패널 content rect (px,py,pw,ph) — 드래그·
        #   추출을 이 패널 안으로 클램프(분할 경계 넘어 복사 오염 방지, §2.4)
        self._sel_pane_id = None  # 선택 시작 패널 id — 추출 시 그 패널의 soft-wrap
        #   정보(app.pane_wrap)를 찾아 자동 줄바꿈 줄을 한 줄로 잇는다
        self._mouse_fwd = None     # 패스스루 중인 패널 id(버튼 다운~업)
        self._mouse_fwd_btn = 0    # 그 시퀀스의 버튼(드래그/릴리스 인코딩용)
        self._sel_pending = None   # mouse-drag-copy: down 후 임계 미만 이동 (x,y) — 클릭↔드래그
        #   미결. move 오면 드래그=pytmux 선택, move 없이 up 이면 클릭=앱 전달.
        # 선택을 **절대 스크롤백 좌표**로도 들고 있는다 — (anchor_line, anchor_col,
        # focus_line, focus_col). 화면 좌표(_sel)만 쓰면 스크롤 순간 같은 칸이 다른
        # 텍스트를 가리켜 한 화면을 넘는 선택이 원리적으로 불가능하다(제보 2026-07-25:
        # 드래그 중 휠을 굴리면 선택이 풀렸다). 서버가 screen 메시지에 실어 주는
        # `top`(뷰포트 첫 행의 절대 인덱스)이 없으면(구 서버) None 으로 남고 종전
        # 화면-내 선택으로 폴백한다.
        self._sel_abs = None
        self._sel_ptr = None       # 드래그 중 마지막 포인터 (x,y) — 스크롤 후 focus 재계산
        self._autoscroll = None    # 경계 밖 드래그 자동 스크롤 타이머(Textual Timer)
        self._autoscroll_delta = 0  # 그 타이머가 매 tick 보낼 스크롤 델타(+위/-아래)
        # 패널 pick-up(헤더 드래그, #1): 패널의 위쪽 테두리/제목 행을 잡아 끌면 그 패널을
        # 든다. 다른 패널에 놓으면 swap, 탭바의 탭에 놓으면 그 탭으로 이동, [+]에 놓으면
        # 새 탭으로 분리(break). (구 Shift+드래그 swap 을 헤더 드래그로 이전 — Shift+드래그는
        # 이제 텍스트 선택. docs 메모리 pytmux-pane-dnd-mouse-design 2026-06-05 결정.)
        self._pickup = None        # 들고 있는 소스 패널 id(헤더 드래그 중)
        self._pickup_over = None   # 드래그 중 가리키는 대상 패널 id(놓으면 swap)
        self._pickup_moved = False  # 다운 후 다른 패널/탭바로 이동했나(클릭 vs 드래그 구분)

    def _clamp_sel(self, x, y):
        """좌표를 선택 시작 패널의 content rect 안으로 클램프(§2.4). rect 가 없으면
        (단일 패널·구버전) 원좌표 그대로 — 기존 전체화면 선택과 동일."""
        r = self._sel_rect
        if not r:
            return x, y
        px, py, pw, ph = r
        return (max(px, min(px + pw - 1, x)),
                max(py, min(py + ph - 1, y)))

    # ── 선택의 절대 좌표(스크롤을 넘는 선택) ────────────────────────────────
    def _pane_top(self, pid):
        """그 패널 뷰포트 첫 행의 절대 인덱스. 모르면 None(구 서버·앱 없음)."""
        if pid is None:
            return None
        try:
            return self.app.pane_top.get(pid)
        except Exception:
            return None

    def _to_abs(self, x, y):
        """화면 좌표 → 선택 시작 패널 기준 (절대행, 패널내 열). 불가하면 None."""
        r, pid = self._sel_rect, self._sel_pane_id
        top = self._pane_top(pid)
        if r is None or top is None:
            return None
        return (top + (y - r[1]), x - r[0])

    def _from_abs(self, line, col):
        """(절대행, 패널내 열) → 화면 좌표. 뷰포트 밖이면 행을 패널 경계로 클램프한다
        (보이는 부분만 강조하고 선택 자체는 유지 — 화면 밖 텍스트도 복사 대상이다)."""
        r, pid = self._sel_rect, self._sel_pane_id
        top = self._pane_top(pid)
        if r is None or top is None:
            return None
        px, py, pw, ph = r
        y = py + (line - top)
        return (max(px, min(px + pw - 1, px + col)),
                max(py, min(py + ph - 1, y)))

    def _sel_begin_abs(self, sx, sy, ex=None, ey=None):
        """드래그 시작 시 절대 앵커를 세운다(가능할 때만 — 없으면 화면-내 폴백)."""
        self._sel_ptr = (ex, ey) if ex is not None else (sx, sy)
        a = self._to_abs(sx, sy)
        b = self._to_abs(ex if ex is not None else sx,
                         ey if ey is not None else sy)
        self._sel_abs = (a + b) if (a is not None and b is not None) else None

    def _sel_clear(self):
        self._autoscroll_stop()      # 릴리스와 함께 자동 스크롤도 멈춘다
        self._sel_start = None
        self._sel = None
        self._sel_rect = None
        self._sel_pane_id = None
        self._sel_abs = None
        self._sel_ptr = None

    def sync_selection(self):
        """절대 앵커 → 화면 좌표(_sel) 재계산. **프레임마다** 호출한다(_composite).

        이게 이 기능의 핵심이다: 패널이 스크롤되면(휠·새 출력) `top` 이 바뀌므로 같은
        절대 앵커가 다른 화면 행으로 매핑된다 → 선택이 텍스트를 계속 따라간다. 드래그
        중이면(포인터가 눌린 채) focus 는 **포인터가 가리키는 현재 절대 행**으로 다시
        잡는다 — 그래서 버튼을 누른 채 휠을 굴리면 선택이 그만큼 늘어난다.
        """
        if self._sel_abs is None:
            return
        if self._sel_start is not None and self._sel_ptr is not None:
            cur = self._to_abs(*self._clamp_sel(*self._sel_ptr))
            if cur is not None:
                self._sel_abs = (self._sel_abs[0], self._sel_abs[1]) + cur
        a = self._from_abs(self._sel_abs[0], self._sel_abs[1])
        b = self._from_abs(self._sel_abs[2], self._sel_abs[3])
        if a is None or b is None:
            return
        self._sel = (a[0], a[1], b[0], b[1])

    def _sel_wrap_set(self):
        """선택 시작 패널의 soft-wrap 연속원 행 인덱스 집합(프레임 상대). app/패널
        정보가 없거나(테스트의 __new__ 주입·앱 컨텍스트 없음) 구버전 서버라 wrap 을
        못 받았으면 빈 집합 → _extract_selection 은 기존 줄 단위 개행으로 폴백한다.
        Textual 의 self.app 은 앱 컨텍스트가 없으면 예외를 던지므로 통째로 감싼다."""
        pid = getattr(self, "_sel_pane_id", None)
        if pid is None:
            return ()
        try:
            return self.app.pane_wrap.get(pid, ())
        except Exception:
            return ()

    def _sel_first_col(self):
        """선택 **첫 줄이 시작하는 패널 내 열**(없으면 0). 첫 줄만 드래그 시작 칸에서
        잘려 나와 다른 줄보다 짧으므로, copy-unwrap 의 접힘 폭 추정이 이 값을 되돌려
        받아야 어긋나지 않는다(매달림 들여쓰기가 깊은 표시에서 판정을 놓쳤다)."""
        if not self._sel:
            return 0
        x0, y0, x1, y1 = self._sel
        if (y0, x0) > (y1, x1):
            x0 = x1
        lx = self._sel_rect[0] if self._sel_rect else 0
        return max(0, x0 - lx)

    def _extract_selection(self):
        if not self._sel or not self._cells:
            return ""
        x0, y0, x1, y1 = self._sel
        if (y0, x0) > (y1, x1):
            x0, y0, x1, y1 = x1, y1, x0, y0
        # 여러 줄 선택의 중간 줄은 시작 패널의 가로 범위(left..right)로 한정한다 —
        # 안 그러면 중간 줄이 화면 끝까지 잡혀 인접 패널·테두리까지 복사된다(§2.4).
        # rect 없으면 전체 폭(0..행끝)으로 폴백해 단일 패널 동작 불변.
        if self._sel_rect:
            lx, py, lw, _ = self._sel_rect
            left, right = lx, lx + lw - 1
        else:
            lx, py, lw = 0, None, None
            left, right = 0, None
        # 자동 줄바꿈(soft-wrap) 연속원 행 집합(패널 content 행 인덱스, 프레임 상대).
        # 서버가 정확히 표시한 wrap 만 join 한다(휴리스틱 아님). 행 y 의 content 행은
        # y - py. 그 행이 wrap 이면 다음 행과 개행 없이 잇고, 꽉 찬 줄이라 trailing 도
        # 보존한다. 마지막 선택행은 wrap 여부와 무관하게 거기서 선택이 끝난다.
        wrap = self._sel_wrap_set()
        parts = []
        for y in range(y0, y1 + 1):
            if not (0 <= y < len(self._cells)):
                continue
            row = self._cells[y]
            row_right = right if right is not None else len(row) - 1
            sx = x0 if y == y0 else left
            ex = x1 if y == y1 else row_right
            text = "".join(row[x][0] for x in range(max(0, sx),
                                                     min(len(row), ex + 1)))
            wrapped = (py is not None and y < y1 and (y - py) in wrap)
            if wrapped:
                parts.append(text)          # 다음 행과 한 줄로 — 개행·rstrip 없음
            else:
                parts.append(text.rstrip())
                if y < y1:
                    parts.append("\n")
        return "".join(parts)

    def set_frame(self, cells):
        """합성된 전 화면 cells 를 받아 변경된 행만 다시 그리게 한다(B8).

        _composite 는 오버레이·테두리가 셀을 공유해 정합성 위험 때문에 **전 화면을
        그대로 재구성**한다(증분 합성 아님). 대신 여기서 직전 프레임과 **행 단위로
        정확 비교**해, 바뀐 행만 `refresh(Region(...))` 로 무효화한다 — textual 이
        깨끗한 행의 render_line 재호출을 건너뛴다. 1줄 델타(Claude 스피너·ssh)에도
        전 화면 W×H render_line 을 돌리던 클라 핫패스를 변경 행만으로 줄인다.

        정확성: 전 화면을 재구성한 뒤 (ch, Style) 동등성(Style 은 캐시 hash 비교)으로
        비교하므로 dirty 검출이 정확하다 — 스타일만 바뀐 행도 잡고, 시각적으로 동일한
        행(새 Style 인스턴스라도 ==)은 건너뛴다. 차원 변화·첫 프레임은 전체 refresh.
        """
        prev = self._cells
        self._cells = cells
        H = len(cells)
        # 첫 프레임이거나 행 수/열 수(리사이즈)가 바뀌면 안전하게 전체 무효화.
        if (not prev or len(prev) != H
                or (H and len(prev[0]) != len(cells[0]))):
            self.refresh()
            return
        dirty = [y for y in range(H) if cells[y] != prev[y]]
        if not dirty:
            return                      # 시각적 변화 없음 — 재렌더 불필요
        if len(dirty) * 2 >= H:         # 절반 이상 바뀌면 region 분할 이득이 적다
            self.refresh()
            return
        w = len(cells[0]) if H else 0
        for y in dirty:
            self.refresh(Region(0, y, w, 1))

    def render_line(self, y: int) -> Strip:
        if y >= len(self._cells):
            return Strip.blank(self.size.width)
        row = self._cells[y]
        # §10-21ⓧ2: 이 줄에 경로 밑줄이 있나. 배경을 칠하지 않는 이유는 그러면 선택
        # (드래그 복사)처럼 보이고 그 앱이 칠한 색을 우리가 덮기 때문이다 — 밑줄은
        # 글자를 안 건드리고 링크 관습과도 맞는다(네이티브 클라도 같은 모양).
        mark = self._span_hover
        under = range(mark[1], mark[2]) if (mark and mark[0] == y) else ()
        segs = []
        run = []
        run_st = None
        for cx, (ch, st) in enumerate(row):
            if ch == "":
                continue  # 와이드 문자의 연속 셀 → 앞 문자가 2칸을 차지함
            if cx in under:
                st = st + Style(underline=True) if st else Style(underline=True)
            if st is run_st:
                run.append(ch)
            else:
                if run:
                    segs.append(Segment("".join(run), run_st))
                run = [ch]
                run_st = st
        if run:
            segs.append(Segment("".join(run), run_st))
        return Strip(segs)

    # --- 마우스 ---
    # ── §10-21ⓧ2 패널 글의 경로 범위(hover 밑줄 · 클릭 = 전체 경로 복사) ──────
    #
    # 판정은 `clientutil.find_paths` 한 곳이고 **네이티브 클라와 한 벌**이다(픽스처
    # `client/scripts/gen_spans_fixture.py` 가 그 함수를 직접 불러 대조한다). 여기서
    # 하는 일은 그 규칙에 줄을 떠먹이고 자리를 **칸**으로 되돌리는 것뿐이다 —
    # 한 글자가 두 칸인 글이 있어 그 산수는 셀 격자를 아는 이쪽만 안다.
    def _span_at(self, x, y):
        """(x, y) 칸에 걸린 경로 범위 → `(y, x0, x1, 전체경로)`. 없으면 None.

        ⚠ **그 패널의 줄만** 본다. 캔버스 한 행에는 옆 패널의 글도 있어, 행 전체를
        넘기면 두 패널의 글자가 이어 붙어 없던 경로가 생긴다.

        전체 경로로 못 풀면 **존을 안 만든다** — 밑줄을 그어 놓고 눌러도 아무 일이
        없으면 그 밑줄이 거짓말이다."""
        if not (0 <= y < len(self._cells)):
            return None
        pane = self._pane_at(x, y)
        if not pane:
            return None
        px, py, pw, ph = pane["x"], pane["y"], pane["w"], pane["h"]
        if not (px <= x < px + pw and py <= y < py + ph):
            return None                     # 테두리는 글이 아니다
        row = self._cells[y]
        text, cell_of_char, hit = [], [], None
        for cx in range(px, min(px + pw, len(row))):
            ch = row[cx][0]
            if ch == "":                    # 넓은 글자의 뒤 칸 — 앞 글자가 답이다
                if cx == x and hit is None and cell_of_char:
                    hit = len(cell_of_char) - 1
                continue
            if cx == x:
                hit = len(cell_of_char)
            cell_of_char.append(cx)
            text.append(ch)
        if hit is None:
            return None
        found = path_at("".join(text), hit)
        if found is None:
            return None
        start, end, word = found
        full = self._resolve_path(word, pane.get("id"))
        if full is None:
            return None
        x0 = cell_of_char[start]
        x1 = cell_of_char[end] if end < len(cell_of_char) else px + pw
        return (y, x0, x1, full)

    def _resolve_path(self, word: str, pane_id=None):
        """상대 경로를 전체 경로로. 기준은 **그 패널의** 작업 디렉터리다.

        셸 통합이 없으면 cwd 를 모르고, 그때는 풀 수 없다 — 모르는 것을 아는 척하지
        않는다(네이티브 클라의 `resolve_path` 와 같은 판정).

        ⚠ 기준이 **활성 패널이 아니라 hover 한 패널**인 것이 핵심이다. 활성 패널의
        cwd 로 옆 패널의 글을 풀면 밑줄은 멀쩡히 그어지고 복사한 값만 틀린다 —
        조용한 오답이라 사용자가 의심할 단서가 없다."""
        if os.path.isabs(word):
            return word
        cwd = (self.app.pane_cwds or {}).get(pane_id)
        return os.path.join(cwd, word) if cwd else None

    def _pane_at(self, x, y):
        for p in self.app.layout.get("panes", []):
            bx, by, bw, bh = p.get("box") or (p["x"], p["y"], p["w"], p["h"])
            if bx <= x < bx + bw and by <= y < by + bh:
                return p
        return None

    def _divider_at(self, x, y):
        for d in self.app.layout.get("dividers", []):
            if d["x"] <= x < d["x"] + d["w"] and d["y"] <= y < d["y"] + d["h"]:
                return d
        return None

    def _pane_by_id(self, pid):
        for p in self.app.layout.get("panes", []):
            if p["id"] == pid:
                return p
        return None

    def _header_pane_at(self, x, y):
        """패널 pick-up(헤더 드래그, #1) 히트테스트: (x,y)가 어떤 패널의 **위쪽 가장자리
        행**(테두리 박스 상단 행, 또는 border-status 제목줄)에 있으면 그 패널을 돌려준다.

        호출부(on_mouse_down)에서 divider(리사이즈 경계)를 **먼저** 걸러내므로, 분할
        경계와 겹치는 패널 상단(예: 상하분할의 아래 패널 윗변=divider)은 여기 닿지 않고
        리사이즈가 우선한다 — 즉 divider 가 아닌 자기 상단 가장자리(바깥 프레임 쪽)만
        pick-up 으로 잡힌다. 제목 글자 유무와 무관하게 테두리 한 행 전체가 손잡이다."""
        border_status = self.app.layout.get("border_status")
        for p in self.app.layout.get("panes", []):
            box = p.get("box")
            if box:
                bx, by, bw, _bh = box
                if y == by and bx <= x < bx + bw:
                    return p
            elif border_status and y == p["y"] - 1 \
                    and p["x"] <= x < p["x"] + p["w"]:
                return p   # border-status 제목줄(내용 한 줄 위)
        return None

    # --- 내부 앱 마우스 패스스루(p4v-tui 등 마우스 1급 TUI) ---
    def _mouse_target(self, x, y):
        """패스스루 대상 패널을 반환. 내부 앱이 마우스 모드를 켰고, 좌표가 그
        패널의 **content 영역**(테두리 제외) 안이며, pytmux 가 normal 모드일
        때만. prefix/copy-mode/팝업이면 None → pytmux 가 가로챈다(tmux 와 동일)."""
        if self.app.mode != "normal":
            return None
        p = self._pane_at(x, y)
        if not p or not p.get("mouse"):
            return None
        if not (p["x"] <= x < p["x"] + p["w"]
                and p["y"] <= y < p["y"] + p["h"]):
            return None   # 테두리/타이틀바 위 → pytmux
        return p

    def _encode_mouse(self, p, x, y, kind, button):
        """마우스 이벤트를 내부 앱이 이해하는 바이트로 인코딩한다.
        kind: press/release/drag/move/wheelup/wheeldown. 좌표는 패널 content
        기준 1-based. 패널이 1006 을 켰으면 SGR, 아니면 레거시 X10 인코딩."""
        col = x - p["x"] + 1
        row = y - p["y"] + 1
        if col < 1 or row < 1 or col > p["w"] or row > p["h"]:
            return b""
        if kind == "wheelup":
            cb = 64
        elif kind == "wheeldown":
            cb = 65
        else:
            base = {1: 0, 2: 1, 3: 2}.get(button, 0)
            cb = (base + 32 if kind == "drag"
                  else 35 if kind == "move" else base)
        if p.get("mouse_sgr"):
            final = "m" if kind == "release" else "M"
            return f"\x1b[<{cb};{col};{row}{final}".encode()
        # 레거시 X10: 릴리스는 버튼 3, 좌표/버튼은 32 오프셋(223 캡).
        if kind == "release":
            cb = 3
        return b"\x1b[M" + bytes([32 + min(cb, 223), 32 + min(col, 223),
                                  32 + min(row, 223)])

    def on_mouse_down(self, event: events.MouseDown):
        self.app._log_mouse("down", event.x, event.y, event.button)
        if not self.app.mouse_enabled:
            return
        # `mouse-drag-copy` 는 값이 **셋**이다(on·off·shift — 뜻은
        # keymap.drag_copy_policy 한 곳). 이 함수의 두 갈래(Shift+드래그·평드래그)가
        # 그 값을 함께 보므로 여기서 한 번만 읽어 둔다.
        drag_pol = drag_copy_policy(getattr(self.app, "mouse_drag_copy", "on"))
        # §10-21ⓧ2: 밑줄이 그어진 자리는 **그 뜻이 먼저**다 — 그어 놓고 눌렀는데
        # 선택 드래그가 시작되면 그 밑줄이 거짓말이 된다. 왼쪽 버튼만이다(오른쪽은
        # 패널 메뉴이고, 그건 정본이 이미 하던 일이다).
        if event.button == 1:
            span = self._span_at(event.x, event.y)
            if span is not None:
                self.app.copy_text(span[3])
                self.app.display_message(
                    i18n.t("span.copied", path=span[3]))
                event.stop()
                return
        if self.app.mode == "scroll":  # copy-mode: 드래그로 선택
            # 선택 시작 패널을 기억해(§2.4) 이후 드래그/추출을 그 패널 안으로 묶는다.
            p = self._pane_at(event.x, event.y)
            self._sel_rect = (p["x"], p["y"], p["w"], p["h"]) if p else None
            self._sel_pane_id = p["id"] if p else None
            sx, sy = self._clamp_sel(event.x, event.y)
            self._sel_start = (sx, sy)
            self._sel = (sx, sy, sx, sy)
            self._sel_begin_abs(sx, sy)
            self.capture_mouse()
            self.app._composite()
            event.stop()
            return
        # Shift+좌드래그 = 내부 앱으로 마우스 이벤트 **전달**(passthrough) — 마우스 모드를
        # 켠 앱(예: 에디터 패널 스플리터)이 드래그를 직접 받아 조작되게 한다(사용자 요청
        # 2026-07-18). 평드래그는 종전대로 pytmux 드래그-복사(mouse-drag-copy)로 두고,
        # Shift 를 '앱에 넘김' 제스처로 쓴다 — pytmux 는 평드래그가 이미 복사라 Shift 가
        # 자연히 반대(앱 전달) 동작이 된다. Claude 등 마우스앱 위 평드래그 복사는 그대로
        # 보존된다. 넘길 대상이 없으면(마우스 모드 앱 아님·테두리) 아래 평드래그 경로로
        # 흘려 복사한다. down 에서 press 를 보내고 _mouse_fwd 를 세우면 이후 move/up 은
        # 기존 패스스루 경로가 drag(1002+)/release 를 전달한다. 좌표·버튼만 인코딩하고
        # Shift 비트는 안 실어(_encode_mouse) 앱엔 순수 드래그로 보인다. 구 Shift=텍스트
        # 선택은 폐지(평드래그 복사가 대체). divider/passthrough 보다 먼저 가로챈다.
        # ⚠ `mouse-drag-copy shift` 에서는 **이 갈래를 안 탄다** — 그 값은 평드래그와
        # Shift 의 역할을 맞바꾼 것이라, Shift 는 아래 평드래그(복사) 경로로 흘러야
        # 한다(사람의 결정 2026-08-31 · pytmux-422).
        if (getattr(event, "shift", False) and event.button == 1
                and drag_pol != "shift"
                and self.app.mode == "normal"):
            tp = self._mouse_target(event.x, event.y)
            if tp is not None:
                if not tp.get("active"):     # 비활성 패널이면 먼저 포커스 이동
                    self.app.send_cmd("select_pane_id", id=tp["id"])
                data = self._encode_mouse(tp, event.x, event.y, "press",
                                          event.button)
                if data:
                    self.app.send_mouse(tp["id"], data)
                    self._mouse_fwd = tp["id"]
                    self._mouse_fwd_btn = event.button
                    self.capture_mouse()
                event.stop()
                return
            # 마우스 모드 앱이 아니면 fall-through → 아래 평드래그(복사) 경로.
        # Ctrl+Click 은 무동작 — 컨텍스트 메뉴는 순수 우클릭(button 3)으로만 연다.
        # (단, 터미널이 Ctrl+Click 을 그냥 button 3 으로 합쳐 보내면 ctrl 플래그가
        #  안 와 구분 불가 — 그 경우 우클릭으로 취급됨. 터미널 의존 한계.)
        if event.ctrl and self.app.mode == "normal":
            event.stop()
            return
        # 우클릭: 마우스 모드(패스스루) 앱 위여도 pytmux 컨텍스트 메뉴를 우선한다.
        # 커서 아래 패널을 먼저 활성화한 뒤 그 패널을 대상으로 메뉴를 연다.
        if event.button == 3 and self.app.mode == "normal":
            p = self._pane_at(event.x, event.y)
            if p and p["id"] != self.app.layout.get("active"):
                self.app.send_cmd("select_pane_id", id=p["id"])
            self.app.open_menu(p["id"] if p else None)
            event.stop()
            return
        # 달력 ‹/› 화살표 클릭 → 이전/다음 달(아래 '패널 클릭=닫기' 보다 먼저 가로챈다
        # — 안 그러면 달력 패널 클릭이 곧 닫기라 화살표를 누를 수 없다). 존은 calendar
        # 플러그인이 client_overlay 훅으로 채운다(없으면 빈 dict → no-op, calendar_nav
        # 도 getattr 가드 — delete-to-disable).
        for pid, zs in getattr(self.app, "_calendar_nav_zones", {}).items():
            for (zx0, zx1, zy, delta) in zs:
                if zy == event.y and zx0 <= event.x < zx1:
                    fn = getattr(self.app, "calendar_nav", None)
                    fn and fn(pid, delta)
                    event.stop()
                    return
        # 시계/달력 오버레이가 켜진 패널을 클릭하면 닫는다([x] 버튼 폐지).
        op = self._pane_at(event.x, event.y)
        if op and self.app._close_overlay(op["id"]):
            event.stop()
            return
        # Claude 클릭존(권한모드 footer/원격제어)은 claude-code 플러그인이
        # client_render 훅으로 채운다(없으면 빈 dict → 아래 루프 no-op, 팝업도 getattr
        # 가드로 호출 안 됨 — delete-to-disable).
        # Claude busy footer 의 'esc to interrupt' 클릭 → 그 패널에 ESC 주입. perm 존
        # 과 겹칠 수 있어(폭 잘림 fallback 시 perm=줄 전체) **perm 보다 먼저** 가로챈다
        # (없으면 빈 dict).
        for pid, (zx0, zx1, zy) in getattr(self.app, "_interrupt_zone", {}).items():
            if zy == event.y and zx0 <= event.x < zx1:
                fn = getattr(self.app, "interrupt_pane", None)  # 플러그인 설치
                fn and fn(pid)
                event.stop()
                return
        # Claude 권한모드 footer 클릭 → 권한모드 선택 팝업(§10 item 2). 패스스루
        # 보다 먼저 가로채 마우스 모드 앱 위에서도 동작한다.
        for pid, (zx0, zx1, zy) in getattr(self.app, "_perm_zone", {}).items():
            if zy == event.y and zx0 <= event.x < zx1:
                fn = getattr(self.app, "open_perm_mode", None)  # 플러그인 설치
                fn and fn(pid)
                event.stop()
                return
        # Claude 'Remote Control active' 클릭 → 원격제어 정보 팝업(§10 item 3)
        for pid, (zx0, zx1, zy) in getattr(self.app, "_remote_zone", {}).items():
            if zy == event.y and zx0 <= event.x < zx1:
                fn = getattr(self.app, "open_remote_control", None)  # 플러그인 설치
                fn and fn(pid)
                event.stop()
                return
        # Claude 컨텍스트 footer 의 토큰 수치('… /clear to save 386.8k tokens') 클릭
        # → 토큰 사용량 팝업(pytmux-23). 상태줄 Σ 배지와 **같은 팝업**이다 — 같은 수를
        # 두 자리에서 눌러 서로 다른 판이 뜨면 그게 더 이상하다.
        for pid, (zx0, zx1, zy) in getattr(self.app, "_tokens_zone", {}).items():
            if zy == event.y and zx0 <= event.x < zx1:
                fn = getattr(self.app, "open_token_log", None)  # 플러그인 설치
                fn and fn()
                event.stop()
                return
        # 현재 탭 닫기 버튼([x]) 클릭(콘텐츠 오른쪽 위)
        z = self.app._tab_close_zone
        if z and z[2] == event.y and z[0] <= event.x < z[1]:
            self.app.confirm_kill_tab()
            event.stop()
            return
        d = self._divider_at(event.x, event.y)
        if d:
            self._dragging = d
            self._hover_divider = None   # 드래그 시작 → 호버 강조는 해제
            self.capture_mouse()
            event.stop()
            return
        # 패널 헤더(위쪽 테두리/제목 행) 드래그 = pick-up(#1). divider 검사 다음이라
        # 분할 경계(리사이즈)와 안 겹치는 자기 상단 가장자리만 잡힌다. 다운 시엔 들기만
        # 하고(클릭=포커스 보존을 위해 _pickup_moved 로 클릭/드래그 구분), 놓을 때
        # on_mouse_up 이 대상(다른 패널=swap·탭바 탭=이동·[+]=새 탭)을 처리한다.
        hp = self._header_pane_at(event.x, event.y)
        if hp is not None and self.app.mode == "normal":
            self._pickup = hp["id"]
            self._pickup_over = None
            self._pickup_moved = False
            self.capture_mouse()
            event.stop()
            return
        # 콘텐츠 좌클릭: mouse-drag-copy(기본 on)면 여기서 앱에 바로 넘기지 않고
        # **미결(pending)** 로 둔다 — 이후 move 가 오면 드래그로 보고 pytmux 패널-클램프
        # 선택(→OS 클립보드 자동복사, Shift·copy-mode 없이도), move 없이 up 이 오면
        # 클릭으로 보고 앱에 전달(마우스 앱 버튼/포커스 보존). 마우스 앱 위 드래그도
        # pytmux 선택에 양보한다 — 호스트 터미널이 Shift 선택을 가로채 pane 외곽선까지
        # 긁히던 불편 해소(사용자 요청 2026-07-11). `set mouse-drag-copy off` 로 아래
        # 앱 패스스루를 복원한다. Shift+드래그·copy-mode 는 위에서 이미 처리했다.
        # `shift` 값이면 그 자리에 **마우스를 켠 앱**이 있을 때만 양보한다 — 평드래그는
        # 그 앱 것이고(claude fullscreen 처럼 제 선택을 가진 앱이 마우스를 못 받던
        # 자리다) 복사는 Shift 로 간다. 앱이 마우스를 안 켰으면(보통 셸) 넘길 데가
        # 없으므로 종전대로 여기서 복사한다 — 안 그러면 그 패널에서 복사가 사라진다.
        if (drag_pol != "off" and event.button == 1
                and self.app.mode == "normal"
                and self._pane_at(event.x, event.y) is not None
                and not (drag_pol == "shift"
                         and not getattr(event, "shift", False)
                         and self._mouse_target(event.x, event.y) is not None)):
            self._sel_pending = (event.x, event.y)
            self.capture_mouse()
            event.stop()
            return
        # 내부 앱 마우스 패스스루(content 영역, 마우스 모드 on) — 여기로 오는 길은
        # 둘이다: `mouse-drag-copy off` · `shift` 의 평드래그.
        tp = self._mouse_target(event.x, event.y)
        if tp is not None:
            if not tp.get("active"):     # 비활성 패널 클릭 시에만 포커스 이동
                self.app.send_cmd("select_pane_id", id=tp["id"])
            data = self._encode_mouse(tp, event.x, event.y, "press",
                                      event.button)
            if data:
                self.app.send_mouse(tp["id"], data)
                self._mouse_fwd = tp["id"]
                self._mouse_fwd_btn = event.button
                self.capture_mouse()
            event.stop()
            return
        p = self._pane_at(event.x, event.y)
        if p:
            self.app.send_cmd("select_pane_id", id=p["id"])
        event.stop()

    def on_mouse_move(self, event: events.MouseMove):
        # 패널 pick-up(헤더 드래그) 중 — 드롭 대상 추적(시각 강조 갱신). capture_mouse
        # 라 탭바 위로 끌면 event.y 가 음수(뷰 위쪽)로 온다 — 그건 탭바 드롭 후보라
        # 패널 강조를 끈다(놓을 때 on_mouse_up 이 탭바를 hit-test). 다른 패널 위면 그
        # 패널을 swap 대상으로 강조한다. 소스 위/같은 자리면 강조 없음.
        if self._pickup is not None:
            if event.y < 0:                       # 탭바 영역(뷰 위) — 탭/[+] 드롭 후보
                over = None
                self._pickup_moved = True
            else:
                p = self._pane_at(event.x, event.y)
                over = p["id"] if (p and p["id"] != self._pickup) else None
                if over is not None:
                    self._pickup_moved = True
            if over != self._pickup_over:
                self._pickup_over = over
                self.app._composite()
            event.stop()
            return
        # mouse-drag-copy 미결 상태에서 **임계 이상** 이동이 오면 = 드래그로 확정 →
        # 선택 시작(§2.4). 시작 좌표는 down 시점의 (psx,psy)를 쓴다(첫 셀을 놓치지 않게).
        # 종전엔 1칸만 움직여도 확정이라, 창을 포그라운드로 올리려는 짧은 클릭에도 손이
        # 미세하게 밀리면 선택→클립보드가 덮어써졌다(제보 2026-07-28). 임계 미만 이동은
        # 미결 상태를 유지해 up 이 오면 클릭으로 처리한다(`mouse-drag-threshold`, 기본 3).
        if self._sel_pending is not None:
            psx, psy = self._sel_pending
            thr = max(1, int(getattr(self.app, "mouse_drag_threshold", 3) or 1))
            if max(abs(event.x - psx), abs(event.y - psy)) >= thr:
                p = self._pane_at(psx, psy)
                self._sel_rect = (p["x"], p["y"], p["w"], p["h"]) if p else None
                self._sel_pane_id = p["id"] if p else None
                sx, sy = self._clamp_sel(psx, psy)
                ex0, ey0 = self._clamp_sel(event.x, event.y)
                self._sel_start = (sx, sy)
                self._sel = (sx, sy, ex0, ey0)
                self._sel_begin_abs(sx, sy, ex0, ey0)
                self._autoscroll_set(self._edge_scroll_delta(event.y))
                self._sel_pending = None
                self.app._composite()
            event.stop()
            return
        if self._sel_start is not None:
            ex, ey = self._clamp_sel(event.x, event.y)   # 시작 패널 안으로(§2.4)
            self._sel = (self._sel_start[0], self._sel_start[1], ex, ey)
            self._sel_ptr = (event.x, event.y)
            # 패널 경계 밖이면 자동 스크롤(안으로 돌아오면 멈춘다). 선택 focus 는
            # _clamp_sel 로 이미 경계 행에 붙어 있어, 내용이 스크롤되면 그 경계 행의
            # 절대 행이 바뀌며 선택이 늘어난다(sync_selection).
            self._autoscroll_set(self._edge_scroll_delta(event.y))
            if self._sel_abs is not None:
                cur = self._to_abs(ex, ey)
                if cur is not None:
                    self._sel_abs = (self._sel_abs[0], self._sel_abs[1]) + cur
            self.app._composite()
            event.stop()
            return
        # 패스스루 드래그(버튼 다운 후 이동) — 1002+(드래그 추적) 앱에만 전달
        if self._mouse_fwd is not None:
            pd = self._pane_by_id(self._mouse_fwd)
            if pd and pd.get("mouse", 0) >= 2:
                data = self._encode_mouse(pd, event.x, event.y, "drag",
                                          self._mouse_fwd_btn)
                if data:
                    self.app.send_mouse(pd["id"], data)
            event.stop()
            return
        if not self._dragging:
            # 경계선(divider) 위 호버 → 배경 강조(리사이즈 가능 암시)(#27).
            # divider 는 테두리라 패스스루 content 영역과 분리됨 → 호버 우선.
            if self.app.mouse_enabled:
                dv = self._divider_at(event.x, event.y)
                new_hov = (dv["x"], dv["y"], dv["w"], dv["h"]) if dv else None
                if new_hov != self._hover_divider:
                    self._hover_divider = new_hov
                    self.app._composite()   # 변경 시에만 재합성(떨림 방지)
                if dv:
                    event.stop()
                    return
            # §10-21ⓧ2 경로 hover — 그냥 움직임일 때만 잡는다(경계 위는 위에서
            # 돌아갔고, 드래그 중이면 밑줄이 잡음이다). 패스스루는 **막지 않는다** —
            # 밑줄은 우리 것이고 모션은 그 앱의 것이라 겹치지 않는다.
            if self.app.mouse_enabled and self._sel is None:
                span = self._span_at(event.x, event.y)
                if span != self._span_hover:
                    old_mark, self._span_hover = self._span_hover, span
                    w = self.size.width
                    for mark in (old_mark, span):
                        if mark:
                            self.refresh(Region(0, mark[0], w, 1))
            # 버튼 없는 모션 — any-motion(1003) 앱에만 전달
            pd = self._mouse_target(event.x, event.y)
            if pd is not None and pd.get("mouse", 0) >= 3:
                data = self._encode_mouse(pd, event.x, event.y, "move", 0)
                if data:
                    self.app.send_mouse(pd["id"], data)
                    event.stop()
            return
        d = self._dragging
        sx, sy, sw, sh = d["rect"]
        if d["orient"] == "lr":
            avail = sw - 1
            ratio = (event.x - sx) / avail if avail > 0 else 0.5
        else:
            avail = sh - 1
            ratio = (event.y - sy) / avail if avail > 0 else 0.5
        self.app.send_cmd("resize", split_id=d["split_id"],
                          ratio=max(0.05, min(0.95, ratio)))
        event.stop()

    def on_leave(self, event=None):
        # 위젯 밖으로 나가면 경계선 호버 강조 해제(#27).
        if self._hover_divider is not None:
            self._hover_divider = None
            self.app._composite()

    def on_mouse_up(self, event: events.MouseUp):
        # 패널 pick-up(헤더 드래그) 완료 — 놓은 위치로 동작이 갈린다(#1):
        #   • 탭바의 다른 탭 위(event.y<0, _hit→tab)     → 그 탭으로 패널 이동
        #   • 탭바의 [+] 위(_hit→add)                    → 새 탭으로 분리(break)
        #   • 다른 패널 위                                → 두 패널 swap
        #   • 제자리/안 움직임(클릭)                      → 그 패널로 포커스만
        if self._pickup is not None:
            src = self._pickup
            self._pickup = None
            self._pickup_over = None
            moved = self._pickup_moved
            self._pickup_moved = False
            try:
                self.release_mouse()
            except Exception:
                pass
            if event.y < 0:                      # 탭바 위에 드롭
                kind, payload = self.app.tabbar._hit(event.x)
                cur = next((t["index"] for t in self.app.tabbar.tabs
                            if t.get("active")), None)
                if kind == "tab" and payload != cur:
                    self.app.send_cmd("select_pane_id", id=src)
                    self.app.send_cmd("move_pane_to_tab", id=src, to=payload)
                elif kind == "add":
                    self.app.send_cmd("select_pane_id", id=src)
                    self.app.send_cmd("break_pane")
                else:
                    self.app._composite()        # 같은 탭/빈 곳 — 강조만 해제
                event.stop()
                return
            p = self._pane_at(event.x, event.y)
            if moved and p and p["id"] != src:
                self.app.send_cmd("swap_pane_to", id=src, to_id=p["id"])
            else:
                # 안 움직였으면(클릭) 그 패널로 포커스만 — 헤더 클릭=선택 보존.
                self.app.send_cmd("select_pane_id", id=src)
                self.app._composite()
            event.stop()
            return
        # mouse-drag-copy: move 없이 up = 클릭. 마우스 앱 패널이면 press+release 를
        # 전달(버튼/링크 클릭·포커스 보존), 아니면 그 패널로 포커스만 옮긴다.
        if self._sel_pending is not None:
            psx, psy = self._sel_pending
            self._sel_pending = None
            try:
                self.release_mouse()
            except Exception:
                pass
            tp = self._mouse_target(psx, psy)
            if tp is not None:
                if not tp.get("active"):
                    self.app.send_cmd("select_pane_id", id=tp["id"])
                for kind in ("press", "release"):
                    data = self._encode_mouse(tp, psx, psy, kind, 1)
                    if data:
                        self.app.send_mouse(tp["id"], data)
            else:
                p = self._pane_at(psx, psy)
                if p and p["id"] != self.app.layout.get("active"):
                    self.app.send_cmd("select_pane_id", id=p["id"])
            event.stop()
            return
        if self._sel_start is not None:
            # 절대 좌표를 알고 있으면 **서버**에 추출을 요청한다 — 선택이 한 화면을
            # 넘었을 수 있고(드래그 중 스크롤), 클라는 현재 뷰포트 셀만 갖고 있어서
            # 화면 밖 줄을 스스로 만들 수 없다. 서버가 스크롤백에서 뽑아 `selection`
            # 으로 회신하면 클라가 OS 클립보드에 넣는다(client.py). 좌표를 모르면
            # (구 서버) 종전 화면-내 추출로 폴백해 동작이 그대로 유지된다.
            abs_sel, pid = self._sel_abs, self._sel_pane_id
            # 선택 패널의 내용 폭과 첫 줄 시작 열 — `copy-unwrap` 이 '앱이 접은 줄'을
            # 판정하는 근거다(clientutil.unwrap_copy_text). 서버 추출(copy_range) 회신
            # 에는 패널 정보가 없으므로 요청할 때 app 에 남겨 둔다.
            cols = self._sel_rect[2] if self._sel_rect else 0
            first_col = self._sel_first_col()
            text = None if abs_sel is not None else self._extract_selection()
            self._sel_clear()
            self.release_mouse()
            if abs_sel is not None:
                y0, x0, y1, x1 = abs_sel
                if (y0, x0) > (y1, x1):
                    y0, x0, y1, x1 = y1, x1, y0, x0
                self.app._copy_unwrap_geom = (cols, x0)
                self.app.send_cmd("copy_range", pane=pid,
                                  y0=y0, x0=x0, y1=y1, x1=x1)
            elif text:
                self.app.copy_selection_text(text, cols, first_col)
            self.app._composite()
            event.stop()
            return
        # 패스스루 버튼 릴리스
        if self._mouse_fwd is not None:
            pd = self._pane_by_id(self._mouse_fwd)
            if pd is not None:
                data = self._encode_mouse(pd, event.x, event.y, "release",
                                          self._mouse_fwd_btn)
                if data:
                    self.app.send_mouse(pd["id"], data)
            self._mouse_fwd = None
            self.release_mouse()
            event.stop()
            return
        if self._dragging:
            self._dragging = None
            self.release_mouse()
            event.stop()

    # 경계 밖 드래그 자동 스크롤: 포인터를 패널 위/아래로 끌면 그 방향으로 계속
    # 스크롤해 선택을 늘린다(휠 없이도 한 화면 초과 선택). **타이머가 필요한 이유**:
    # 마우스 이동 이벤트는 포인터가 움직일 때만 오므로, 경계 밖에 멈춰 있으면 move 가
    # 끊겨 스크롤도 멈춘다(에디터·tmux 도 같은 이유로 타이머를 쓴다).
    _AUTOSCROLL_SEC = 0.06     # tick 간격(≈16행/초 — 읽으면서 끌 수 있는 속도)
    _AUTOSCROLL_MAX = 3        # tick 당 최대 행수(경계에서 멀수록 빠르게)

    def _edge_scroll_delta(self, y):
        """포인터 y 로 자동 스크롤 델타를 낸다(안쪽=0). + = 위/과거, - = 아래/최신.

        **경계 행 자체를 포함한다**(제보 2026-07-25 2차: "화면 바깥으로 움직여도 스크롤
        안 됨"). 이유는 터미널의 구조적 제약이다 — 포인터가 **창 밖으로 나가면 터미널이
        모션 리포트를 아예 멈추고**, 창 안이라도 좌표가 마지막 행/열로 클램프되는 터미널이
        있다. 그래서 "경계를 넘어야 스크롤"로 만들면 사용자가 실제로 도달할 수 없는 조건이
        된다. tmux copy-mode·에디터도 **끝 줄에 닿으면** 스크롤한다.

        속도: 경계 행 = 1행/tick(읽으며 정밀하게), 밖으로 더 나갈수록 최대 3행/tick."""
        r = self._sel_rect
        if not r:
            return 0
        _px, py, _pw, ph = r
        bottom = py + ph - 1
        if y <= py:                     # 첫 행 **이상**(위 경계 포함)
            dist = py - y + 1           # 경계 행 = 1 → 1행/tick
        elif y >= bottom:               # 마지막 행 **이하**(아래 경계 포함)
            dist = -(y - bottom + 1)
        else:
            return 0
        mag = min(self._AUTOSCROLL_MAX, 1 + (abs(dist) - 1) // 2)
        return mag if dist > 0 else -mag

    def _autoscroll_set(self, delta):
        """델타가 0 이면 타이머를 멈추고, 아니면 (재)시작한다(멱등)."""
        if delta != self._autoscroll_delta:
            # mouse-debug 진단: "경계 밖인데 왜 안 스크롤되나" 를 로그로 가른다
            # (드래그 상태·판정 델타·대상 패널). `:set mouse-debug on`.
            self.app._log_mouse("autoscroll", *(self._sel_ptr or (-1, -1)),
                                note=f"delta={delta} pane={self._sel_pane_id} "
                                     f"rect={self._sel_rect}")
        self._autoscroll_delta = delta
        if not delta:
            self._autoscroll_stop()
            return
        if self._autoscroll is None:
            try:
                self._autoscroll = self.set_interval(self._AUTOSCROLL_SEC,
                                                    self._autoscroll_tick)
            except Exception:
                self._autoscroll = None      # 마운트 전(테스트 등) — tick 없이 진행

    def _autoscroll_stop(self):
        t, self._autoscroll = self._autoscroll, None
        self._autoscroll_delta = 0
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def _autoscroll_tick(self):
        """타이머 1회 — 드래그가 끝났으면 스스로 멈춘다(릴리스 누락에도 안전)."""
        if self._sel_start is None or not self._autoscroll_delta:
            self._autoscroll_stop()
            return
        self._scroll_during_drag(self._autoscroll_delta)

    def _scroll_during_drag(self, delta):
        """드래그 중 휠 — **선택을 유지한 채** 선택 시작 패널만 스크롤한다.

        이게 "한 화면보다 긴 텍스트 선택"의 조작 경로다(제보 2026-07-25). 앱(less/
        Claude 등)에 휠을 넘기지 않는 이유: 지금 이 제스처는 pytmux 의 선택이고, 앱이
        자기 화면을 스크롤하면 선택 좌표계(스크롤백 절대 인덱스)와 어긋난다. 선택
        focus 는 다음 프레임에서 `sync_selection` 이 포인터 기준으로 다시 잡으므로,
        누른 채 굴리면 선택이 그만큼 늘어난다. 절대 좌표를 모르는 구 서버에선
        스크롤해도 화면-내 선택만 유지된다(종전과 같은 한계)."""
        pid = self._sel_pane_id
        p = self._pane_by_id(pid) if pid is not None else None
        if p is None:
            p = self._pane_at(*(self._sel_ptr or (0, 0))) or self._active_pane()
        if p is not None:
            self.app.send_scroll(p["id"], delta=delta)
        return True

    def on_mouse_scroll_up(self, event):
        # 진단 로그는 어떤 가드보다 먼저 — "이벤트가 도달했는가"를 본다.
        self.app._log_mouse("scroll_up", event.x, event.y)
        if not self.app.mouse_enabled:
            return
        if self._sel_start is not None:      # 드래그 중 = 선택 확장(앱 전달 금지)
            self._scroll_during_drag(3)
            event.stop()
            return
        # 마우스 모드 앱(less/htop/Claude 등)은 휠을 직접 처리하도록 전달.
        tp = self._mouse_target(event.x, event.y)
        if tp is not None:
            data = self._encode_mouse(tp, event.x, event.y, "wheelup", 0)
            if data:
                self.app.send_mouse(tp["id"], data)
            event.stop()
            return
        p = self._pane_at(event.x, event.y) or self._active_pane()
        if p:
            self.app.send_scroll(p["id"], delta=3)
        event.stop()

    def on_mouse_scroll_down(self, event):
        self.app._log_mouse("scroll_down", event.x, event.y)
        if not self.app.mouse_enabled:
            return
        if self._sel_start is not None:      # 드래그 중 = 선택 확장(앱 전달 금지)
            self._scroll_during_drag(-3)
            event.stop()
            return
        tp = self._mouse_target(event.x, event.y)
        if tp is not None:
            data = self._encode_mouse(tp, event.x, event.y, "wheeldown", 0)
            if data:
                self.app.send_mouse(tp["id"], data)
            event.stop()
            return
        p = self._pane_at(event.x, event.y) or self._active_pane()
        if p:
            self.app.send_scroll(p["id"], delta=-3)
        event.stop()

    def _active_pane(self):
        aid = self.app.layout.get("active")
        for p in self.app.layout.get("panes", []):
            if p["id"] == aid:
                return p
        return None

def _visual_tab_order(tabs):
    """탭 리스트를 **화면 표시 순서**(비고정 먼저, 고정 나중 — 탭바 _entries 및
    상태줄이 그리는 순서)로 본 탭 index 목록. 고정 탭은 오른쪽 구역으로 밀려 그려지므로
    표시 번호도 그 순서를 따라야 "보이는 순서 = 번호" 가 맞는다. 서버가 로컬 탭을
    [비고정][고정]으로 정규화하면 이미 index 순=표시 순이지만, 원격(federation) 탭이
    고정 뒤에 덧붙거나(비고정인데 index 가 큼) 정규화 안 된 상태에선 어긋난다 — 그럴 때
    클라가 시각 순서로 재번호를 매겨 사용자가 보는 위치와 esc+숫자 이동을 일치시킨다."""
    return ([t["index"] for t in tabs if not t.get("pinned")]
            + [t["index"] for t in tabs if t.get("pinned")])


def _visual_tab_numbers(tabs):
    """탭 index → 1-based 표시 번호(시각 순서) dict. 로컬 정규화 상태에선 index+1 과
    동일하다(픽셀 불변). [[_visual_tab_order]] 참고."""
    return {idx: i + 1 for i, idx in enumerate(_visual_tab_order(tabs))}


class TabBar(Widget):
    """상단 탭 인터페이스. 각 탭과, 마지막 탭 바로 오른쪽의 [+] 새 탭 버튼을
    표시한다. (탭 닫기 [x] 는 콘텐츠 영역 오른쪽 위 모서리로 이동했다.)

    마우스 클릭과 ESC 모드 방향키(←→ 선택, Enter 전환)로 조작. 탭이 하나뿐이면
    기본 숨김이나, 설정 tab-bar always 면 항상 표시한다."""

    def __init__(self):
        super().__init__(id="tabbar")
        self.tabs = []          # [{index,name,active,bell,activity}]
        self.sel = 0            # ESC 모드 선택 인덱스(= tab.index)
        self.bar_focus = False  # ESC 모드 포커스가 탭바에 있는지
        self._scroll = 0        # 가로 스크롤(첫 표시 탭의 리스트 위치)
        self._zones = []        # [(x0, x1, kind, payload)] 클릭 히트테스트
        self._drag = None       # 드래그 중인 탭 index(재정렬)
        self._drag_over = None  # 드래그 중 현재 가리키는 드롭 대상 탭 index
        self._blink_idx = None  # 깜빡일 탭 index(ESC+없는 숫자 안내)
        self._blink_on = False  # 깜빡임 현재 위상(True=경고색 표시)
        self._blink_left = 0    # 남은 on/off 토글 횟수
        self._blink_timer = None
        # C2(PERFORMANCE_REVIEW 2026-06-07): _entries() 결과 프레임 캐시. render_line
        # 과 active_tab_xrange 가 같은 프레임에 _entries() 를 각각 부르므로(동일 기하
        # 2회 계산), (폭·sel·진입 스크롤·탭 시그니처)가 같으면 재사용한다. 스타일
        # (active/drag/blink/focus)은 기하와 무관해 키에 없다 — render_line 이 매
        # 프레임 스타일을 다시 입힌다.
        self._entries_sig = None    # 캐시 키(미스 판정)
        self._entries_cache = None  # 캐시된 entries
        self._entries_scroll = 0    # 캐시 시점의 안정화된 _scroll

    def set_tabs(self, tabs, active_idx):
        self.tabs = tabs
        if not self.bar_focus:
            self.sel = active_idx
        self.refresh()

    def scroll_by(self, delta):
        self._scroll = max(0, min(self._scroll + delta,
                                  max(0, len(self.tabs) - 1)))
        self.refresh()

    def blink_active(self, times: int = 3, period: float = 0.12):
        """현재 활성(하이라이트) 탭을 times 번 깜빡여 '여기서 더 이동 불가'를 시각적
        으로 알린다(ESC+없는 숫자). render_line 이 _blink_on 위상일 때 그 탭을 경고색
        으로 그린다. 활성 탭이 없으면 무시."""
        aidx = next((t["index"] for t in self.tabs if t.get("active")), None)
        if aidx is None:
            return
        self._blink_idx = aidx
        self._blink_on = True
        self._blink_left = max(1, times) * 2     # on/off 토글 횟수
        if self._blink_timer is not None:
            self._blink_timer.stop()
        self._blink_timer = self.set_interval(period, self._blink_step)
        self.refresh()

    def _blink_step(self):
        self._blink_left -= 1
        if self._blink_left <= 0:
            self._blink_on = False
            self._blink_idx = None
            if self._blink_timer is not None:
                self._blink_timer.stop()
                self._blink_timer = None
        else:
            self._blink_on = not self._blink_on
        self.refresh()

    # (탭 상태 글리프는 plugins.client_tab_glyph 훅이 기여한다 — 종전 CLAUDE_ICON/
    #  t.get("claude") 렌더는 claude-code 플러그인으로 이전. delete-to-disable.)

    # 탭바 왼쪽 여백 — 첫 탭을 한 칸 오른쪽에서 시작(사용자 요청). lead 엔트리로
    # 넣어 render_line/active_tab_xrange 가 같은 오프셋을 공유한다.
    LEAD = 1
    # 고정(핀) 탭 글리프(항목7) — 이모지 폭이 터미널마다 달라 ASCII 안전값. 고정
    # 구역 좌측 구분자.
    PIN_GLYPH = "*"
    PIN_SEP = " ‖ "

    def _remote_title_mode(self) -> str:
        """원격 탭 제목 표시 형식(§10-21ⓓ2). 앱이 아직 없으면 기본이다.

        앱에서 읽는 이유: 이 값은 설정 파일과 `set remote-title` 이 함께 쥐는 것이라
        위젯이 자기 사본을 들면 둘이 갈린다."""
        return getattr(self.app, "remote_title", "full")

    def _labels(self):
        out = []
        # 표시 번호는 **시각 순서**(비고정→고정)로 매긴다 — 고정 탭이 오른쪽으로 밀려도
        # "보이는 순서 = 번호" 가 맞게(사용자 요청 07-14). 로컬 정규화 상태에선 index+1
        # 과 같다. 이동(esc+숫자)은 index_for_number 가 같은 순서로 역매핑한다.
        vis = _visual_tab_numbers(self.tabs)
        for t in self.tabs:
            flag = "!" if t.get("bell") else ("#" if t.get("activity") else "")
            ic = self.app.plugins.client_tab_glyph(self.app, t)
            ic = (ic + " ") if ic else ""
            pin = (self.PIN_GLYPH + " ") if t.get("pinned") else ""  # 항목7 핀 글리프
            # 표시는 1부터(사용자 요청 #21). 내부 index 는 0-based 리스트 위치 그대로
            # 두고(select_window 등 좌표 계산 호환), **보여줄 때만** 시각 번호로 바꾼다.
            num = vis.get(t["index"], t["index"] + 1)
            # §10-21ⓓ2: 원격 탭 이름은 **그릴 때만** 접는다(값은 서버 계약이라 불변).
            name = remote_title_display(t["name"], bool(t.get("remote")),
                                        self._remote_title_mode())
            out.append(f" {pin}{ic}{num}:{name}{flag} ")
        return out

    def index_for_number(self, n):
        """표시 번호(1-based, 시각 순서) → 탭 index. 없으면 None. esc+숫자·alt+숫자
        이동이 _labels 의 표시 번호와 같은 순서를 따르게 한다(고정/원격 탭 재배치 대응)."""
        t = self.tab_for_number(n)
        return t["index"] if t else None

    def tab_for_number(self, n):
        """표시 번호(1-based, 시각 순서) → 탭 dict(index·wid 포함). 없으면 None.
        wid(Tab 의 안정 id, model.Tab 주석 참고)를 select_window 에 index 와 함께
        실으면, 클라가 번호를 계산한 시점과 서버가 커맨드를 처리하는 시점 사이
        다른 클라이언트의 탭 생성/삭제/이동으로 sess.tabs 가 재인덱싱돼도 서버가
        같은 탭을 다시 찾아낸다(간헐적 ESC+숫자 오탭 전환 레이스 대응)."""
        order = _visual_tab_order(self.tabs)
        if not (1 <= n <= len(order)):
            return None
        idx = order[n - 1]
        return next((t for t in self.tabs if t["index"] == idx), None)

    def _entries(self):
        """현재 상태(탭·스크롤·폭)에서 탭바에 그릴 항목을 (kind, payload, text)
        순서 리스트로 만든다(스타일 무관, 기하만). render_line(세그먼트·스타일)과
        active_tab_xrange(연결부 x 좌표)가 같은 기하를 공유해, 합성 시점이나
        직전 렌더 상태와 무관하게 일치한다(#23 — 예전엔 후자가 render_line 부산물인
        _zones 를 읽어 탭 전환 직후 stale 값으로 연결부가 어긋났다). 스크롤 보정은
        render_line 과 동일하게 여기서 수행(부수효과로 self._scroll 갱신).

        C2: 같은 프레임에 render_line·active_tab_xrange 가 둘 다 부르므로, (폭·sel·
        진입 스크롤·탭 기하 시그니처)가 직전과 같으면 캐시를 돌려준다. 히트 시엔
        labels/widths/스크롤 루프를 통째로 건너뛰고, 캐시 당시 안정화된 스크롤을
        복원해 후속 코드 일관성을 유지한다(스크롤 안정화는 멱등)."""
        w = self.size.width
        # ⚠ 시그니처에 **표시 형식**도 넣는다(§10-21ⓓ2) — 안 넣으면 형식만 바꿨을 때
        #   이름·폭이 그대로라 캐시가 히트하고, 방금 바꾼 설정이 안 먹은 것처럼 보인다.
        sig = (w, self.sel, self._scroll, self._remote_title_mode(),
               tuple((t["index"], t["name"], t.get("bell"),
                      t.get("activity"),
                      self.app.plugins.client_tab_glyph(self.app, t),
                      t.get("pinned")) for t in self.tabs))
        if sig == self._entries_sig:
            self._scroll = self._entries_scroll
            return self._entries_cache
        labels = self._labels()
        widths = [sum(_char_cells(c) for c in s) for s in labels]
        # 항목7: 고정(핀) 탭은 오른쪽 구역으로 분리한다. 서버가 tabs 를 *[비고정][고정]*
        # 으로 정규화하므로 비고정이 앞, 고정이 뒤다. 비고정만 가운데(스크롤) 구역에,
        # 고정은 우측 flush 로 항상 보이게 그린다. 핀이 없으면 종전과 픽셀 동일.
        n = len(self.tabs)
        # 항목7+§1.7-a: 고정 탭은 우측 구역, 비고정 탭은 가운데(스크롤) 구역.
        # 서버는 *로컬* 탭을 [비고정][고정]으로 정규화하지만, 원격(remote-attach)
        # 탭은 그 뒤에 그대로 덧붙어(serverremote._remote_tabs) 비고정 원격 탭이
        # 고정 로컬 탭보다 뒤 위치에 올 수 있다. 따라서 "첫 고정 위치 이후는 전부
        # 고정"이라는 가정(옛 first_pin 컷)은 깨져, 그 사각지대의 비고정 원격 탭이
        # 탭바에서 통째로 누락됐다(번호 이동은 index 기반이라 됐음). → 비고정/고정을
        # **위치 목록**으로 분리해 가운데 루프가 비고정 탭 전부를 그린다.
        pin_pos = [k for k in range(n) if self.tabs[k].get("pinned")]
        unpinned_pos = [k for k in range(n) if not self.tabs[k].get("pinned")]
        nu = len(unpinned_pos)
        # 고정 구역 폭(구분자 + 고정 라벨들). 핀 없으면 0.
        pin_block_w = 0
        if pin_pos:
            pin_block_w = (sum(_char_cells(c) for c in self.PIN_SEP)
                           + sum(widths[k] for k in pin_pos))
        idxs = [t["index"] for t in self.tabs]
        selpos = idxs.index(self.sel) if self.sel in idxs else 0
        # [+] 새 탭 버튼: 왼쪽 탭과 한 칸 더 띄운다(사용자 요청 — 앞 공백 2칸).
        # 왼쪽 여백(LEAD)·고정 구역 폭도 가운데 예산에서 뺀다.
        addtxt = "  [+]"
        mid_w = max(1, w - len(addtxt) - self.LEAD - pin_block_w)
        # 선택 탭이 보이도록 스크롤 보정(비고정 구역 한정 — 고정 탭은 늘 보임).
        # _scroll 은 왼쪽으로 밀려난 비고정 탭 개수(비고정 시퀀스 내 오프셋).
        sel_uidx = unpinned_pos.index(selpos) if selpos in unpinned_pos else None
        self._scroll = max(0, min(self._scroll, max(0, nu - 1)))
        if sel_uidx is not None and sel_uidx < self._scroll:
            self._scroll = sel_uidx
        while (sel_uidx is not None and self._scroll < sel_uidx and
               sum(widths[unpinned_pos[j]]
                   for j in range(self._scroll, sel_uidx + 1)) > mid_w - 2):
            self._scroll += 1
        # ⛔ **끝을 지나 밀리지 않는다**(pytmux-149). 위 루프는 스크롤을 *올리기만* 하고
        #   내리는 코드가 어디에도 없었다 — 그래서 좁은 폭에서 한 번 밀린 값이 폭이
        #   넓어져도 그대로 남는다. 기동 직후가 정확히 그 창이다: 진짜 터미널 폭이 오기
        #   전 프레임에서 mid_w 가 한 칸짜리라 _scroll 이 밀리고, 폭이 와도 안 돌아온다.
        #   증상은 **재부착하면 탭 1이 ◀ 뒤에 숨은 채 온다**(둘 다 들어가는 폭인데도).
        #   스크롤 가능한 것의 당연한 불변식을 여기서 세운다: 상한은 «[s..끝] 이 가운데
        #   구역에 들어가는 가장 작은 s». 다 들어가면 그 값이 0 이라 ◀ 자체가 사라진다.
        #   ⚠ 자리가 **이 루프 뒤**인 이유: 앞에 두면 위 루프가 다시 밀어 올린다(판정
        #   기준이 mid_w-2 라 한 칸 어긋난다). 뒤에 두어도 선택 탭은 계속 보인다 —
        #   s=max_scroll 이면 [max_scroll..끝] 이 통째로 들어가므로 그 안에 sel 도 있다.
        suffix = sum(widths[k] for k in unpinned_pos)
        max_scroll = 0
        while max_scroll < nu - 1 and suffix + (1 if max_scroll else 0) > mid_w:
            suffix -= widths[unpinned_pos[max_scroll]]   # ◀ 한 칸은 s>0 일 때만 든다
            max_scroll += 1
        self._scroll = min(self._scroll, max_scroll)
        entries, mid_used = [], 0
        if self.LEAD:                              # 왼쪽 여백(첫 탭 한 칸 오른쪽)
            entries.append(("lead", None, " " * self.LEAD))
        if self._scroll > 0:                       # 왼쪽에 더 있음
            entries.append(("scroll_left", None, "◀"))
            mid_used += 1
        i = self._scroll
        while i < nu:                               # 비고정 구역만 가운데에
            k = unpinned_pos[i]
            tw = widths[k]
            reserve = 1 if i < nu - 1 else 0         # 오른쪽 화살표 자리
            if mid_used + tw > mid_w - reserve and i > self._scroll:
                break
            entries.append(("tab", self.tabs[k]["index"], labels[k]))
            mid_used += tw
            i += 1
        if i < nu:                                  # 비고정 오른쪽에 더 있음
            entries.append(("scroll_right", None, "▶"))
        # [+] 새 탭 버튼(§10 #16): 앞 간격칸은 터미널 배경(녹색 아님)으로 분리해
        # 그려, 간격까지 녹색으로 칠해지지 않게 한다. 간격칸은 클릭 무시(lead 처럼).
        entries.append(("addgap", None, addtxt[:2]))   # 간격(터미널 배경)
        entries.append(("add", None, addtxt[2:]))      # "[+] "(녹색 버튼)
        # 고정 구역: 우측 flush 로 — 현재까지 폭 + 고정 블록폭을 w 에서 빼 그 만큼
        # 오른쪽 간격(rgap)을 채운 뒤 구분자·고정 탭을 그린다(항목7).
        if pin_pos:
            used = sum(sum(_char_cells(c) for c in txt) for _, _, txt in entries)
            rgap = w - used - pin_block_w
            if rgap > 0:
                entries.append(("rgap", None, " " * rgap))
            entries.append(("pinsep", None, self.PIN_SEP))
            for k in pin_pos:
                entries.append(("tab", self.tabs[k]["index"], labels[k]))
        self._entries_sig = sig            # C2: 진입 시그니처로 캐시(스크롤은 진입값)
        self._entries_scroll = self._scroll  # 안정화된 스크롤 보존(히트 시 복원)
        self._entries_cache = entries
        return entries

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        fg = theme_color(self, "foreground")
        # 비활성 탭·여백 배경은 터미널 기본 배경(bgcolor=None)을 따른다 — 패널
        # 내용 셀이 터미널 색을 보이는 것과 같은 메커니즘. 활성/선택/[+]/화살표
        # 배지는 자체 bgcolor 유지(의도된 강조).
        base = Style(color=fg, bgcolor=None)
        add_st = Style(color="black", bgcolor=theme_color(self, "success"),
                       bold=True)
        active_st = Style(color="white", bgcolor=theme_color(self, "primary"),
                          bold=True)
        sel_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                       bold=True)
        arrow_st = Style(color="black", bgcolor=theme_color(self, "accent"),
                         bold=True)
        # 비활성 탭의 Claude 작업 완료 알림(보면 해제). 배경을 바꾸면 너무 튄다는
        # 피드백(#31) → **배경은 그대로 두고 탭 이름 글자색만** 호박색(warning)+굵게로
        # 바꿔 알린다. 활성(primary 배경)·선택(accent 배경)과 자연히 구분된다.
        done_st = Style(color=theme_color(self, "warning"), bold=True)
        # §1.7-a 원격 탭(remote-attach 병합 ⇄) 분홍 구분: 활성=분홍 배경(로컬의
        # 파랑 자리), 비활성=분홍 글자(claude_done 패턴) — 로컬/원격이 한눈에.
        remote_active_st = Style(color="black", bgcolor=REMOTE_PINK, bold=True)
        remote_st = Style(color=REMOTE_PINK)
        # 드래그 재정렬 시각 피드백: 들고 있는 탭(소스)은 흐리게, 놓을 위치
        # (드롭 대상)은 밑줄+강조색으로 표시(놓으면 그 자리로 이동).
        dragging = self._drag is not None
        drop_st = Style(color="black", bgcolor=theme_color(self, "warning"),
                        bold=True, underline=True)
        # ESC+없는 숫자 안내용 깜빡임(현재 활성 탭을 경고색으로 번쩍).
        blink_st = Style(color="black", bgcolor=theme_color(self, "warning"),
                         bold=True)
        by_idx = {t["index"]: t for t in self.tabs}
        segs, zones = [], []
        x = 0
        # 항목7: 고정 구역 구분자(흐린 muted 글자), 우측 정렬 간격(터미널 배경).
        pinsep_st = Style(color=theme_color(self, "primary"), bold=True)
        for kind, payload, text in self._entries():
            if kind in ("lead", "addgap", "rgap"):  # 여백/간격칸(터미널 배경, 클릭 무시)
                st = base
            elif kind == "pinsep":                  # 고정 구역 구분자
                st = pinsep_st
            elif kind in ("scroll_left", "scroll_right"):
                st = arrow_st
            elif kind == "add":
                # ESC 모드에서 [+] 가 커서 대상으로 선택되면 강조(#26)
                st = sel_st if (self.bar_focus and self.sel == "+") else add_st
            else:                                  # tab
                t = by_idx.get(payload, {})
                if self._blink_on and payload == self._blink_idx:
                    st = blink_st  # ESC+없는 숫자 → 활성 탭 깜빡임(이동 불가 안내)
                elif dragging and payload == self._drag_over and payload != self._drag:
                    st = drop_st   # 드롭 대상(놓으면 여기로 이동)
                elif dragging and payload == self._drag:
                    st = base + Style(dim=True)  # 들고 있는 탭(소스) 흐리게
                elif self.bar_focus and payload == self.sel:
                    st = sel_st
                elif t.get("active"):
                    st = remote_active_st if t.get("remote") else active_st
                elif t.get("claude_done"):
                    st = done_st   # 비활성 탭 Claude 완료 알림(#22)
                elif t.get("remote"):
                    st = remote_st  # §1.7-a 비활성 원격 탭(분홍 글자)
                else:
                    st = base
            wdt = sum(_char_cells(c) for c in text)
            zones.append((x, x + wdt, kind, payload))
            segs.append(Segment(text, st))
            x += wdt
        pad = w - x
        if pad > 0:
            segs.append(Segment(" " * pad, base))
            x += pad
        self._zones = zones
        # backdrop 딤 중이면 탭 이름의 컬러 이모지도 placeholder 로 치환(#25, 상태표시줄과
        # 동일 — 탭바도 _composite 그리드 밖 위젯이라 Textual backdrop 이 이모지 글리프를
        # 못 어둡게 한다). 폭 보존이라 클릭존(_zones)은 그대로 유효.
        return _deemoji_strip_if_dim(self.app,
                                     Strip(segs).adjust_cell_length(w, base))

    def _hit(self, x):
        for x0, x1, kind, payload in self._zones:
            if x0 <= x < x1:
                return kind, payload
        return None, None

    def _tab_pinned(self, idx) -> bool:
        """§12 ②: 이 index 의 탭이 고정(핀) 구역인가."""
        return any(t["index"] == idx and t.get("pinned") for t in self.tabs)

    def _is_remote(self, idx) -> bool:
        """§1.7-a/c: 이 index 의 탭이 원격(remote-attach 병합) 탭인가."""
        return any(t["index"] == idx and t.get("remote") for t in self.tabs)

    def _tab_host(self, idx):
        """원격 탭이면 이름 '⇄host:name' 의 host, 아니면 None. host 는 첫 ':' 앞
        (rw 이름엔 ':' 가 들어갈 수 있으므로 1회만 분리)."""
        for t in self.tabs:
            if t["index"] == idx and t.get("remote"):
                nm = t.get("name", "")
                if nm.startswith("⇄"):
                    return nm[1:].split(":", 1)[0]
        return None

    def _viewing_host(self):
        """지금 보는(=활성) 탭이 원격이면 그 host, 아니면 None."""
        aidx = next((t["index"] for t in self.tabs if t.get("active")), None)
        return self._tab_host(aidx) if aidx is not None else None

    def _drag_merge_ok(self, src) -> bool:
        """src 탭을 끌어내려 지금 보는 탭의 패널에 합칠(join_pane) 수 있나.
        로컬: 로컬 탭을 로컬 화면에만(§1.7-c). 원격: **같은 호스트**의 원격 탭끼리
        (서버 remote_relay_join 이 index 변환해 업스트림으로 합친다)."""
        if not self._is_remote(src):
            return not self.app._viewing_remote()
        vh = self._viewing_host()
        return vh is not None and self._tab_host(src) == vh

    def active_tab_xrange(self):
        """현재 활성 탭의 화면 x 범위 (x0, x1). 콘텐츠 상단 테두리를 활성 탭과
        연결(노트북 탭 모양)하는 데 쓴다(#23). _zones(직전 렌더 부산물) 대신
        _entries() 로 현재 self.tabs+스크롤에서 직접 계산해, 탭 전환 직후
        render_line 재실행 전에 합성돼도 새 활성 탭을 정확히 가리킨다."""
        aidx = next((t["index"] for t in self.tabs if t.get("active")), None)
        if aidx is None:
            return None
        x = 0
        for kind, payload, text in self._entries():
            wdt = sum(_char_cells(c) for c in text)
            if kind == "tab" and payload == aidx:
                return (x, x + wdt)
            x += wdt
        return None

    def on_mouse_down(self, event):
        if not self.app.mouse_enabled:
            return
        kind, payload = self._hit(event.x)
        if kind == "add":
            self.app.send_cmd("new_window")
        elif kind == "scroll_left":
            self.scroll_by(-1)
        elif kind == "scroll_right":
            self.scroll_by(1)
        elif kind == "tab":
            # 탭 클릭=드래그 시작(놓을 때 같은 탭이면 선택, 다른 탭이면 재정렬)
            self._drag = payload
            self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event):
        # 드래그 중에만(capture_mouse 로 이동 이벤트가 여기로 옴) 시각 피드백 갱신.
        if self._drag is None:
            return
        # 탭바 아래(콘텐츠 영역)로 끌어내리면 패널 분할 드롭 모드(#19): 커서 아래 패널과
        # 분할 방향을 미리보기로 표시한다. 탭바는 1행이라 event.y>=1 이 콘텐츠 행이다.
        # §1.7-c: 탭→패널 합치기(join_pane) 미리보기는 로컬↔로컬 또는 **같은
        # 호스트의 원격 탭끼리**만 켠다(_drag_merge_ok). 로컬↔원격 등 섞기는 제외.
        if event.y >= 1 and self._drag_merge_ok(self._drag):
            drop = self.app._tabdrop_at(event.x, event.y - 1)
            if drop != self.app._drag_split:
                self.app._drag_split = drop
                self._drag_over = None
                self.app._composite()           # 분할 미리보기 갱신
            event.stop()
            return
        if self.app._drag_split is not None:    # 탭바로 되올라옴 → 미리보기 해제
            self.app._drag_split = None
            self.app._composite()
        kind, payload = self._hit(event.x)
        # §1.7-c: 원격 탭을 옮기거나(소스) 원격 탭 위치로 끼워넣는(대상) 재정렬은
        # 불가 — 원격 탭 순서는 업스트림 소유라 드롭 마커 자체를 켜지 않는다.
        over = payload if (kind == "tab" and payload != self._drag
                           and not self._is_remote(self._drag)
                           and not self._is_remote(payload)) else None
        if over != self._drag_over:
            self._drag_over = over
            self.refresh()
        event.stop()

    def on_mouse_up(self, event):
        if self._drag is None:
            return
        src = self._drag
        drop = self.app._drag_split
        self._drag = None
        self._drag_over = None
        self.app._drag_split = None
        self.refresh()
        try:
            self.release_mouse()
        except Exception:
            pass
        # 콘텐츠 위에 놓았으면(드롭 대상 패널 있음) 그 패널을 활성화하고, 끌어온 탭의
        # 패널을 그 패널에 분할로 합친다(#19 탭→패널). 아니면 기존 재정렬/전환.
        # §1.7-c: 탭→패널 합치기는 로컬↔로컬 또는 같은 호스트의 원격 탭끼리만
        # (_drag_merge_ok, 서버 가드와 대칭). 원격끼리면 select_pane_id·join_pane 이
        # 업스트림으로 릴레이돼 원격 서버가 합친다(src 는 서버가 index 변환).
        if event.y >= 1 and drop is not None and self._drag_merge_ok(src):
            pane_id, orient = drop
            self.app.send_cmd("select_pane_id", id=pane_id)
            self.app.send_cmd("join_pane", src=src, orient=orient)
            self.app._composite()
            event.stop()
            return
        kind, payload = self._hit(event.x)
        # §12 ②: 경계 너머 드롭 = 핀 토글. 끌어온(로컬) 탭을 구분자(‖) 위나 반대
        # 구역(고정↔비고정)의 탭 위에 놓으면, 같은 구역 재정렬은 서버가 클램프해
        # no-op 이던 자리를 이용해 그 탭의 고정 상태를 토글한다(드롭 강조로 의도 확인).
        if not self._is_remote(src):
            cross = (kind == "pinsep"
                     or (kind == "tab" and payload != src
                         and not self._is_remote(payload)
                         and self._tab_pinned(payload) != self._tab_pinned(src)))
            if cross:
                self.app.send_cmd("set_pinned", index=src,
                                  value=not self._tab_pinned(src))
                event.stop()
                return
        if (kind == "tab" and payload != src
                and not self._is_remote(src) and not self._is_remote(payload)):
            # index==위치(연속) 이므로 그대로 사용
            self.app.send_cmd("move_tab", index=src, to=payload)
        else:
            self.app.send_cmd("select_window", index=src)
        event.stop()

class StatusBar(Widget):
    def __init__(self, bg=None, fg=None,
                 left=" ", right=" #{pane_title}#h %H:%M %Y-%m-%d "):
        super().__init__(id="status")
        self.session = ""
        self.windows = []
        self.zoomed = False
        self.sync = False
        self.pane_title = ""
        self.autoresume = False
        self.prompt_clear = False  # 프롬프트 단위 클리어 모드(활성 패널, #9)
        self.prompt_clear_queue = []  # 프롬프트 단위 클리어 큐(활성 패널, #4)
        # REC 표시 상태(capture/_rec_zone/capture_path/capture_size)는 rec 플러그인의
        # client_statusbar_init 훅이 설치한다(코어 미소유 — delete-to-disable). 코어는
        # 흡수/배지/클릭에서 getattr 로만 읽는다(없으면 미캡처로 동작).
        self.prefix_off = False  # 중첩: outer prefix 해제 표시
        self.cmd_mode = False  # ESC 명령 모드 표시
        # pytmux-467(449 ⑷): prefix 를 누른 뒤 **다음 키를 기다리는 중** 표시.
        # 종전에는 그 상태가 화면에 아무 자국도 안 남아, 잘못 눌렀는지 아닌지를 사람이
        # 알 길이 없었다 — GUI 는 `[prefix]` 칩으로 이미 냈고 이 줄이 그 갈림을 없앤다.
        self.prefix_mode = False
        self.message = None    # display-message 임시 메시지
        # §10-8 알림 등급(ok/info/warn/error) — 메시지 줄의 색·기호를 정한다.
        self.message_sev = clientnotices.DEFAULT_SEVERITY
        # §10-8 미확인 알림 배지: 수·그 중 최고 등급(색). total=이력 보유 건수
        # (0 이면 배지 자체를 안 그린다 — 빈 배지는 소음).
        self.notices_n = 0
        self.notices_sev = None
        self.notices_total = 0
        self.hide_tabs = False  # 상단 탭바가 보이면 하단 탭 목록 생략
        # Claude 상태 속성(claude_active/usage/tokens/model·토큰절감 설정·예산·카운트
        # 다운 등 ~26개)은 코어가 더 이상 두지 않는다 — claude-code 플러그인이
        # client_statusbar_init 훅으로 이 위젯에 안전 기본값을 설치하고(client.py 생성
        # 직후 호출), client_statusbar_update(흡수)·client_statusbar(렌더)가 읽고 쓴다.
        # 플러그인 부재 시 속성이 안 생기지만 흡수/렌더 훅도 함께 사라지고 _render_main
        # 은 이 속성을 읽지 않아 안전하다(delete-to-disable, Phase 2c 마무리).
        self.bg = bg
        self.fg = fg
        self.left_fmt = left
        self.right_fmt = right
        # 다중 줄 상태표시줄: lines = 상태줄 줄 수(0~5, 기본 1). 맨 아래 줄(bottom)이
        # 기존의 풍부한 상태(REC/사용량/시계 등), 그 위 줄들은 extra[i] 의 포맷
        # 문자열을 _expand 로 펼쳐 표시(tmux status-format[i] 와 동일하게 index 1
        # 이 바닥 바로 위). 0 이면 상태줄 숨김.
        self.lines = 1
        self.extra = {}          # {line_index(>=1): fmt 문자열}
        self._clock_zone = None  # (x0, x1) 시각(시계) 클릭 영역
        self._date_zone = None   # (x0, x1) 날짜(달력) 클릭 영역
        self._usage_zone = None  # (x0, x1) 토큰 사용량 클릭 영역(token-log 팝업)
        # _rec_zone(REC 클릭 영역)은 rec 플러그인 client_statusbar 가 설치(코어 미소유).
        self._model_zone = None  # (x0, x1) 모델 배지 클릭 영역(모델·컨텍스트 팝업, 요청)
        self._warn_zone = None   # (x0, x1) Claude 경고 배지 클릭 영역(상황·할일 팝업, 요청)
        self._ar_zone = None     # (x0, x1) AR(자동재개) 배지 클릭 영역(켜고끄기 팝업, 요청)
        self._host_zone = None   # (x0, x1) 서버이름(host) 클릭 영역(서버 탭, §10-A #12)
        self._session_zone = None  # (x0, x1) 세션 이름(#S) 클릭 영역 → 제자리 리네임
        # 세션 이름 제자리 편집(§10-21ⓛ 제보): `#S` 가 펼쳐진 자리를 클릭하면 **판을
        # 띄우지 않고** 그 자리가 입력칸이 된다. 모달이 아니라 값을 맡길 스크린이
        # 없으므로 버퍼·커서를 이 위젯이 직접 든다(편집 중이면 str, 아니면 None).
        # 키는 clientio.on_key 가 모달 검사 직후에 여기로 넘긴다(_handle_session_edit_key).
        self.session_edit = None
        self.session_edit_cur = 0
        self._notices_zone = None  # (x0, x1) 알림 이력 배지 클릭 영역(§10-8)
        self.focus_btn = None    # ESC 모드 하단 포커스 키 강조(model/usage/rec/host/clock/date)
        # 클라이언트가 SSH 원격 세션에서 도는지(attach 한 머신 기준, 시작 시 1회).
        self._is_remote = bool(os.environ.get("SSH_CONNECTION")
                               or os.environ.get("SSH_TTY"))

    def _expand(self, fmt):
        """#S/#h/#H/#{pane_title} 토큰과 strftime(%) 코드를 치환."""
        try:
            s = datetime.now().strftime(fmt)
        except ValueError:
            s = fmt
        host = socket.gethostname()
        tpane = (self.pane_title + " · ") if (self.pane_title
                 and self.pane_title != "shell") else ""
        aw = next((w for w in self.windows if w.get("active")), None)
        return (s.replace("#S", self.session)
                 .replace("#h", host.split(".")[0])
                 .replace("#H", host)
                 .replace("#I", str(aw["index"] + 1) if aw else "")
                 .replace("#W", aw["name"] if aw else "")
                 .replace("#{pane_title}", tpane))

    def _expand_parts(self, fmt):
        """포맷을 (kind, text) 런 목록으로 펼친다.
        kind ∈ {'host','time','date','session','plain'}. 호스트(원격 강조)·시각(시계
        클릭)·날짜(달력 클릭)·세션 이름(제자리 리네임) 구간을 분리하기 위해 토큰/‌strftime
        코드 단위로 쪼갠 뒤 인접 동종을 병합한다. 좌/우 포맷이 커스텀돼도 동작한다."""
        host = socket.gethostname()
        aw = next((w for w in self.windows if w.get("active")), None)
        tpane = (self.pane_title + " · ") if (self.pane_title
                 and self.pane_title != "shell") else ""
        runs = []
        i, n = 0, len(fmt)
        while i < n:
            c = fmt[i]
            if c == "#":
                if fmt.startswith("#{pane_title}", i):
                    runs.append(("plain", tpane)); i += len("#{pane_title}"); continue
                two = fmt[i:i + 2]
                if two == "#h":
                    runs.append(("host", host.split(".")[0])); i += 2; continue
                if two == "#H":
                    runs.append(("host", host)); i += 2; continue
                if two == "#S":
                    # 고유 kind — _merge_runs 가 인접 plain 과 안 합치므로 세션 이름
                    # 구간이 그대로 남고, 그 폭이 곧 클릭존(=편집칸)이 된다.
                    runs.append(("session", self.session)); i += 2; continue
                if two == "#I":
                    runs.append(("plain", str(aw["index"] + 1) if aw else "")); i += 2; continue
                if two == "#W":
                    runs.append(("plain", aw["name"] if aw else "")); i += 2; continue
                runs.append(("plain", c)); i += 1; continue
            if c == "%" and i + 1 < n:
                code = fmt[i + 1]
                if code == "%":
                    runs.append(("plain", "%")); i += 2; continue
                try:
                    val = datetime.now().strftime("%" + code)
                except ValueError:
                    val = "%" + code
                kind = ("time" if code in _TIME_STRFTIME
                        else "date" if code in _DATE_STRFTIME else "plain")
                runs.append((kind, val)); i += 2; continue
            runs.append(("plain", c)); i += 1
        return self._merge_runs(runs)

    @staticmethod
    def _merge_runs(runs):
        # ① 같은 종류 strftime 코드 사이의 구분자(:,-,/,. )만 있는 plain 런을
        #    양옆과 같은 kind 로 흡수(%H:%M·%Y-%m-%d 를 한 구간으로 묶음).
        absorbed = []
        for idx, (kind, text) in enumerate(runs):
            if (kind == "plain" and text and all(ch in ":-/. " for ch in text)
                    and absorbed and absorbed[-1][0] in ("time", "date")
                    and idx + 1 < len(runs)
                    and runs[idx + 1][0] == absorbed[-1][0]):
                kind = absorbed[-1][0]
            absorbed.append((kind, text))
        # ② 인접 동일 kind 병합.
        merged = []
        for kind, text in absorbed:
            if merged and merged[-1][0] == kind:
                merged[-1] = (kind, merged[-1][1] + text)
            else:
                merged.append([kind, text])
        return [(k, t) for k, t in merged if t]

    # ---- 세션 이름 제자리 편집(§10-21ⓛ) ----
    # 상태줄의 `#S` 자리에서 바로 글자를 고친다. 이 클라에 **선례가 없다** —
    # cmd_mode 는 배지일 뿐이고 PromptScreen 은 판을 띄운다(제보가 하지 말라는 것).
    # 그래서 값(버퍼·커서)은 이 위젯이 들고, 키 라우팅은 clientio.on_key 가,
    # 커밋(rename_session 전송)은 앱이 한다 — 위젯은 소켓을 모른다.

    def session_editing(self) -> bool:
        return self.session_edit is not None

    def begin_session_edit(self) -> bool:
        """편집 시작. 현재 이름을 버퍼에 싣고 커서를 끝에 둔다.
        `#S` 가 안 펼쳐졌거나(=클릭할 자리가 없다) 이름이 비었으면 False."""
        if self._session_zone is None or not self.session:
            return False
        self.session_edit = self.session
        self.session_edit_cur = len(self.session_edit)
        self.refresh()
        return True

    def end_session_edit(self):
        """편집 종료(커밋·취소 공통). 편집 중이던 버퍼를 돌려준다(아니면 None)."""
        buf = self.session_edit
        self.session_edit = None
        self.session_edit_cur = 0
        self.refresh()
        return buf

    def session_edit_insert(self, text):
        if self.session_edit is None or not text:
            return
        c = self.session_edit_cur
        self.session_edit = self.session_edit[:c] + text + self.session_edit[c:]
        self.session_edit_cur = c + len(text)
        self.refresh()

    def session_edit_erase(self, forward=False):
        if self.session_edit is None:
            return
        c, buf = self.session_edit_cur, self.session_edit
        if forward:
            if c >= len(buf):
                return
            self.session_edit = buf[:c] + buf[c + 1:]
        else:
            if c <= 0:
                return
            self.session_edit = buf[:c - 1] + buf[c:]
            self.session_edit_cur = c - 1
        self.refresh()

    def session_edit_move(self, key):
        if self.session_edit is None:
            return
        n = len(self.session_edit)
        self.session_edit_cur = {
            "left": max(0, self.session_edit_cur - 1),
            "right": min(n, self.session_edit_cur + 1),
            "home": 0, "end": n,
        }.get(key, self.session_edit_cur)
        self.refresh()

    def session_edit_cursor_at(self, x):
        """편집칸 안의 절대 x 를 커서 위치(문자 index)로 옮긴다 — 이미 편집 중일 때
        같은 자리를 다시 클릭하면 커서만 그리로 간다. 폭은 셀 기준으로 재므로
        와이드 문자(한글 등)에서도 어긋나지 않는다."""
        z = self._session_zone
        if self.session_edit is None or z is None:
            return
        off = max(0, x - z[0])
        acc = 0
        for i, ch in enumerate(self.session_edit):
            w = _char_cells(ch)
            if off < acc + w:
                self.session_edit_cur = i
                self.refresh()
                return
            acc += w
        self.session_edit_cur = len(self.session_edit)
        self.refresh()

    def _session_segs(self, base, tc):
        """세션 이름 런의 (세그먼트 목록, 셀폭).

        편집 중이면 강조 배경의 **입력칸**으로 그리고 커서 자리를 반전시킨다 —
        판을 안 띄우므로 "지금 여기를 고치는 중"을 보여 줄 곳이 이 자리뿐이다.
        커서가 끝에 있으면 한 칸을 덧대 커서를 보이게 한다(그만큼 칸이 넓어진다)."""
        if self.session_edit is None:
            text = self.session
            return ([Segment(text, base)] if text else []), sum(
                _char_cells(c) for c in text)
        buf = self.session_edit
        cur = max(0, min(self.session_edit_cur, len(buf)))
        est = Style(color="black", bgcolor=tc("accent"), bold=True)
        cst = Style(color="black", bgcolor=tc("accent"), bold=True, reverse=True)
        segs, cells = [], 0
        for text, st in ((buf[:cur], est), (buf[cur:cur + 1] or " ", cst),
                         (buf[cur + 1:], est)):
            if text:
                segs.append(Segment(text, st))
                cells += sum(_char_cells(c) for c in text)
        return segs, cells

    def update_status(self, msg):
        self.session = msg.get("session", "")
        self.windows = msg.get("windows", [])
        self.zoomed = msg.get("zoomed", False)
        self.sync = msg.get("sync", False)
        self.pane_title = msg.get("pane_title", "")
        self.autoresume = msg.get("autoresume", False)
        self.prompt_clear = msg.get("prompt_clear", False)
        self.prompt_clear_queue = msg.get("prompt_clear_queue", [])
        # Claude 필드(claude_active/usage/tokens/model/warn/budget 등)와 REC capture*
        # 필드는 각 플러그인의 client_statusbar_update 훅이 이 위젯에 흡수한다(claude-code·
        # rec). 플러그인이 없으면 no-op → 속성이 __init__ 기본값(또는 미설치) 그대로라
        # _render_main 의 해당 세그먼트가 비활성(delete-to-disable). self.app 은 마운트 후.
        self.app.plugins.client_statusbar_update(self.app, self, msg)
        self.refresh()

    def render_line(self, y: int) -> Strip:
        # 다중 줄: 맨 아래 줄이 주 상태(아래 _render_main), 그 위는 extra 포맷.
        # backdrop 딤 중이면 컬러 이모지(⚠)를 placeholder 로 치환(#25, 모듈
        # _deemoji_strip_if_dim — 탭바와 공유).
        h = max(1, self.lines)
        base = Style(color=self.fg or theme_color(self, "foreground"),
                     bgcolor=self.bg)
        if y != h - 1:
            # bottom 위의 보조 줄. tmux 처럼 index 1 = 바닥 바로 위.
            idx = (h - 1) - y
            fmt = self.extra.get(idx, "")
            txt = self._expand(fmt) if fmt else ""
            return _deemoji_strip_if_dim(self.app, Strip([Segment(txt, base)])
                                         .adjust_cell_length(self.size.width, base))
        return _deemoji_strip_if_dim(self.app, self._render_main(base))

    def _notices_badge(self, tc):
        """§10-8 알림 이력 배지 `(텍스트, 스타일)` — 이력이 비면 `("", None)`.

        표기는 ASCII 폭 1 문자 `≡`(설계 §9-1: 이모지는 폰트·폭 문제를 오래 겪어
        기본 ASCII 권고) + 미확인 수. 색은 **미확인 중 최고 등급**이라, 알림이
        사라진 뒤에도 "방금 실패가 있었다"가 색으로 남는다. 전부 확인했으면 평시
        색(수는 0 이라 기호만)."""
        if not self.notices_total:
            return "", None
        n = self.notices_n
        txt = f" ≡{n} " if n else " ≡ "
        if self.focus_btn == "notices":       # ESC 모드 포커스 강조(다른 배지와 동일)
            return txt, Style(color="black", bgcolor=tc("accent"), bold=True)
        if n and self.notices_sev is not None:
            return txt, Style(color=clientnotices.fg(self.notices_sev),
                              bgcolor=tc(clientnotices.theme_name(
                                  self.notices_sev)), bold=True)
        return txt, Style(color=tc("foreground"), bgcolor=self.bg)

    def _render_main(self, base) -> Strip:
        w = self.size.width
        # 색상은 p4v-tui 와 동일한 textual-dark 테마를 따른다(설정으로 덮어쓰기 가능).
        tc = lambda n: theme_color(self, n)  # noqa: E731
        # 배경은 명시 설정(self.bg)이 없으면 터미널 기본(None)을 따른다 —
        # REC/SYNC/AR 등 개별 배지는 자체 bgcolor 유지(의도된 강조).
        if self.message is not None:
            # 세션 이름 클릭존은 무효화한다 — 메시지가 덮은 자리를 눌러 리네임 편집이
            # 열리면 안 된다. 편집 **중**이던 상태는 건드리지 않는다(메시지는 잠깐
            # 떴다 사라지고, 그때 편집칸이 그대로 돌아온다).
            self._session_zone = None
            # §10-8: 등급이 배경색과 기호를 정한다(멀리서 색만 보고 성공/실패 판단 +
            # 색맹·모노크롬 대비 기호). ESC 모드 하단 포커스가 메시지에 와 있으면
            # (focus_btn=='msg') 강조 + ⏎ 닫기 힌트를 붙인다(요청).
            sev = self.message_sev
            if self.focus_btn == "msg":
                ms = Style(color="black", bgcolor=tc("accent"), bold=True)
                txt = i18n.t("ui.notice_close", message=self.message)
            else:
                ms = Style(color=clientnotices.fg(sev),
                           bgcolor=tc(clientnotices.theme_name(sev)), bold=True)
                txt = f" {clientnotices.symbol(sev)} {self.message} "
            # 메시지가 줄 전체를 덮어도 **이력 배지는 오른쪽 끝에 겹쳐** 남긴다 —
            # 메시지를 보는 중에도 이력으로 갈 수 있어야 한다(설계 §5.1).
            bt, bs = self._notices_badge(tc)
            if bt:
                bw = sum(_char_cells(c) for c in bt)
                body = Strip([Segment(txt, ms)]).adjust_cell_length(
                    max(0, w - bw), ms)
                self._notices_zone = (max(0, w - bw), w)
                return Strip(list(body) + [Segment(bt, bs)]) \
                    .adjust_cell_length(w, ms)
            self._notices_zone = None
            return Strip([Segment(txt, ms)]).adjust_cell_length(w, ms)
        active = Style(color="white", bgcolor=tc("primary"), bold=True)
        # P6: 세그먼트 누적 셀폭을 증분으로 추적한다(예전엔 rx0·used 에서 segs 전체를
        # 문자 단위로 두 번 재순회). _char_cells 는 메모(C1)지만 전수 재순회 자체를 없앤다.
        def _cw(t):
            return sum(_char_cells(c) for c in t)
        acc = 0
        # 왼쪽도 오른쪽처럼 (kind, text) 런으로 펼친다 — 통짜 문자열이면 `#S` 가
        # 어디부터 어디까지인지 몰라 클릭존을 못 만든다. 렌더 결과 문자열은 종전
        # (_expand)과 같고, 세션 런만 자기 폭을 _session_zone 에 남긴다. host/시각/
        # 날짜는 왼쪽에서 **강조하지 않는다**(종전 그대로 base) — 이 CL 의 관심사는
        # 세션 이름 한 자리다.
        segs = []
        self._session_zone = None
        for kind, text in self._expand_parts(self.left_fmt):
            if kind == "session":
                ssegs, scells = self._session_segs(base, tc)
                if scells:
                    self._session_zone = (acc, acc + scells)
                segs.extend(ssegs)
                acc += scells
                continue
            segs.append(Segment(text, base))
            acc += _cw(text)
        if self.cmd_mode:
            # i18n 키 경유(과거 하드코딩 한글 리터럴이라 en 로케일에서도 한글 누출 —
            # 카탈로그값만 검사하는 test_en_catalog_has_no_hangul_leak 가 못 잡았다).
            _t = i18n.t("ui.cmd_mode_badge")
            segs.append(Segment(_t, Style(color="black", bgcolor=tc("accent"),
                                          bold=True)))
            acc += _cw(_t)
        if self.prefix_mode:
            # 색이 esc 배지(accent=호박)와 **달라야** 한다 — 둘 다 「지금 모달이다」인데
            # 한 색이면 배지가 어느 모드인지 못 말한다. GUI 의 그 칩도 파랑 계열이라
            # (`theme::INVERT_BG` #7aa2f7) 두 클라가 같은 뜻에 같은 계열을 쓴다.
            _p = i18n.t("ui.prefix_mode_badge")
            segs.append(Segment(_p, Style(color="white", bgcolor=tc("primary"),
                                          bold=True)))
            acc += _cw(_p)
        if self.zoomed:
            segs.append(Segment("Z ", Style(color="black", bgcolor=tc("warning"),
                                             bold=True)))
            acc += 2
        if self.sync:
            segs.append(Segment("SYNC ", Style(color="white", bgcolor=tc("error"),
                                                bold=True)))
            acc += 5
        self._ar_zone = None
        if self.autoresume:
            segs.append(Segment(" AR ", Style(color="black", bgcolor=tc("accent"),
                                              bold=True)))
            # AR 배지 클릭존(요청): 클릭/터치 시 자동 재개 켜고 끄기 팝업을 연다.
            self._ar_zone = (acc, acc + 4)
            acc += 4
        # 시스템 배지 영역(SYNC/AR 직후) 플러그인 배지: REC ` REC ` 배지·_rec_zone 을
        # rec 플러그인의 client_statusbar_badges 훅이 여기서 그린다 — 좌하단 정보 클러스터
        # (client_statusbar, 아래)보다 **앞**이라 종전과 같은 위치(시스템 배지 옆)를
        # 유지한다. 플러그인 부재면 acc 그대로(배지·클릭존 없음, delete-to-disable).
        acc = self.app.plugins.client_statusbar_badges(self.app, self, segs, w, acc)
        self._usage_zone = None
        self._model_zone = None   # 모델 배지 클릭존(모델·컨텍스트 변경 팝업, 요청)
        self._warn_zone = None    # Claude 경고 배지 클릭존(상황·할일 팝업, 요청)
        # Claude 좌하단 세그먼트(모델 배지·컨텍스트·토큰Σ·예산경고·카운트다운·폭주경고)는
        # claude-code 플러그인의 client_statusbar 훅이 그리고 위 두 클릭존을 채운다(Phase
        # 2c). 플러그인이 없으면 no-op → Claude 세그먼트 미표시·클릭존 None(클릭 no-op).
        # P6: 누적 폭 acc 를 훅에 w0 로 넘기고, 훅이 자기 세그먼트를 그린 뒤의 새 누적
        # 폭을 돌려받는다 — 플러그인이 ux0/left 를 segs 전수합산으로 다시 구하지 않고,
        # 코어도 추가분을 재순회하지 않는다. 플러그인 부재면 acc 가 그대로 돌아온다.
        acc = self.app.plugins.client_statusbar(self.app, self, segs, w, acc)
        if self.prefix_off:
            segs.append(Segment("NEST ", Style(color="white",
                                               bgcolor=tc("secondary"), bold=True)))
            acc += 5
        win_vis = _visual_tab_numbers(self.windows)   # 탭바와 동일 시각 번호(07-14)
        for win in ([] if self.hide_tabs else self.windows):
            flag = "!" if win.get("bell") else ("#" if win.get("activity") else "")
            num = win_vis.get(win["index"], win["index"] + 1)
            label = f"{num}:{win['name']}{flag} "   # 표시 1-based(#21), 시각 순서
            acc += _cw(label)
            if win["active"]:
                # §1.7-a: 원격 탭은 활성도 분홍 배경(탭바와 동일 구분).
                st = (Style(color="black", bgcolor=REMOTE_PINK, bold=True)
                      if win.get("remote") else active)
            elif win.get("bell"):
                st = Style(color="white", bgcolor=tc("error"), bold=True)
            elif win.get("activity"):
                st = Style(color="black", bgcolor=tc("warning"))
            elif win.get("remote"):
                st = Style(color=REMOTE_PINK, bgcolor=self.bg)  # §1.7-a 분홍 글자
            else:
                st = base
            segs.append(Segment(label, st))
        # 오른쪽은 host/시각/날짜를 별도 런으로 쪼개 그린다 — 원격이면 host 를
        # `ssh:` 접두사+붉은색으로, 시각/날짜는 각각 시계/달력 클릭 존으로.
        right_parts = self._expand_parts(self.right_fmt)
        host_style = Style(color=tc("error"), bgcolor=self.bg, bold=True)
        # ESC 모드 포커스(host/clock/date)면 그 run 을 강조색으로(요청). focus_btn
        # 키 clock 은 strftime run kind 'time' 에 대응한다.
        _fk = {"host": "host", "clock": "time", "date": "date"}.get(self.focus_btn)
        focus_hi = Style(color="black", bgcolor=tc("warning"), bold=True)
        # (kind, [Segment…], cells) — 한 런이 여러 세그먼트일 수 있다(편집 중인
        # 세션 이름은 커서 반전 때문에 앞/커서/뒤 셋으로 쪼개진다). 폭은 여기서 잰
        # 값을 그대로 패딩과 클릭존에 쓴다.
        built = []
        right_w = 0
        # §10-8 알림 이력 배지는 우측 배지열의 **맨 왼쪽**에 붙인다(host/시각/날짜
        # 앞). 아래 런 루프가 같은 방식으로 x 를 누적해 클릭존을 계산한다.
        _nb_txt, _nb_style = self._notices_badge(tc)
        if _nb_txt:
            _nb_cells = sum(_char_cells(c) for c in _nb_txt)
            built.append(("notices", [Segment(_nb_txt, _nb_style)], _nb_cells))
            right_w += _nb_cells
        for kind, text in right_parts:
            if kind == "session":
                # `#S` 를 status-right 에 둔 설정에서도 같은 자리를 누를 수 있다.
                ssegs, scells = self._session_segs(base, tc)
                built.append((kind, ssegs, scells))
                right_w += scells
                continue
            st = base
            if kind == "host" and self._is_remote:
                text = "ssh:" + text
                st = host_style
            if kind == _fk:
                st = focus_hi
            cells = sum(_char_cells(c) for c in text)
            built.append((kind, [Segment(text, st)], cells))
            right_w += cells
        used = acc   # P6: 증분 누적값(전수 재합산 제거)
        pad = max(0, w - used - right_w)
        if pad:
            segs.append(Segment(" " * pad, base))
        # 각 런 세그먼트를 붙이며 누적 x 로 시각(시계)/날짜(달력)/서버이름 클릭 존 계산.
        self._clock_zone = None
        self._date_zone = None
        self._host_zone = None
        self._notices_zone = None
        x = used + pad
        for kind, bsegs, cells in built:
            segs.extend(bsegs)
            if cells and kind == "session":
                # 왼쪽에 이미 `#S` 가 있으면 그쪽이 편집 자리다(먼저 그린 쪽 우선).
                if self._session_zone is None:
                    self._session_zone = (x, x + cells)
            elif cells and kind == "notices":
                self._notices_zone = (x, x + cells)   # §10-8 이력 팝업 클릭존
            elif cells and kind == "time":
                self._clock_zone = (x, x + cells)
            elif cells and kind == "date":
                self._date_zone = (x, x + cells)
            elif cells and kind == "host":
                self._host_zone = (x, x + cells)   # 서버이름 클릭 → 서버 탭(#12)
            x += cells
        # 편집 중인데 `#S` 자리가 사라졌으면(포맷이 바뀌었다·세션이 없어졌다) 편집을
        # 끝낸다 — 안 그러면 보이지도 않는 입력칸이 키를 계속 삼킨다(패널에 아무것도
        # 안 찍히는 먹통이 된다).
        if self.session_edit is not None and self._session_zone is None:
            self.session_edit = None
            self.session_edit_cur = 0
        # 폭 맞추기(자르기)
        return Strip(segs).adjust_cell_length(w, base)

    def on_mouse_down(self, event: events.MouseDown):
        if not self.app.mouse_enabled:
            return
        # 클릭 존(REC/시계/날짜/사용량)은 주 상태가 그려지는 맨 아래 줄에만 있다.
        if event.y != self.size.height - 1:
            return
        # §10-8: 알림 이력 배지는 메시지 줄에도 **겹쳐** 그려지므로, 아래 "메시지 줄
        # 아무 데나 = 닫기" 보다 **먼저** 판정한다(겹친 배지 영역만 이력으로).
        nz = self._notices_zone
        if nz and nz[0] <= event.x < nz[1]:
            fn = getattr(self.app, "open_notice_history", None)
            if fn:
                fn()
                event.stop()
                return
        # 수동 닫기 가능한 메시지(remote-attach 핸드셰이크 실패 등)는 메시지가 줄
        # 전체를 덮으므로 아무 데나 클릭/터치하면 즉시 닫는다(요청).
        if self.message is not None and getattr(self.app, "_msg_dismissable", False):
            self.app._dismiss_message()
            event.stop()
            return
        rz = getattr(self, "_rec_zone", None)   # rec 플러그인 부재 시 None(no-op)
        if rz and rz[0] <= event.x < rz[1]:
            fn = getattr(self.app, "show_capture_info", None)
            if fn:
                fn(getattr(self, "capture_path", None),
                   getattr(self, "capture_size", 0))
            event.stop()
            return
        sz = self._session_zone
        if sz and sz[0] <= event.x < sz[1]:
            # 세션 이름 클릭 → **그 자리**가 입력칸이 된다(§10-21ⓛ 제보: 판을 띄우지
            # 않는다). 이미 편집 중이면 커서만 누른 자리로 옮긴다.
            if self.session_editing():
                self.session_edit_cursor_at(event.x)
            else:
                fn = getattr(self.app, "begin_session_rename", None)
                fn and fn()
            event.stop()
            return
        z = self._clock_zone
        if z and z[0] <= event.x < z[1]:
            # 시각 클릭 → clock-mode 토글(clock 플러그인 설치; 없으면 no-op).
            fn = getattr(self.app, "toggle_clock", None)
            fn and fn(self.app.layout.get("active"))
            event.stop()
            return
        dz = self._date_zone
        if dz and dz[0] <= event.x < dz[1]:
            # 날짜 클릭 → calendar-mode 토글(calendar 플러그인 설치; 없으면 no-op).
            fn = getattr(self.app, "toggle_calendar", None)
            fn and fn(self.app.layout.get("active"))
            event.stop()
            return
        mz = self._model_zone
        if mz and mz[0] <= event.x < mz[1]:
            # 모델 배지 클릭 → 모델·컨텍스트 변경 팝업(claude-code 플러그인 설치).
            fn = getattr(self.app, "open_model_config", None)
            fn and fn()
            event.stop()
            return
        wz = self._warn_zone
        if wz and wz[0] <= event.x < wz[1]:
            # Claude 경고 배지(⚠) 클릭 → 상황 설명 + 할일 팝업(claude-code 플러그인
            # 설치). 포맷 미인식·장기 턴·반복 루프 종류별 안내를 띄운다.
            fn = getattr(self.app, "open_claude_warn_info", None)
            if fn:
                fn()
                event.stop()
                return
        az = self._ar_zone
        if az and az[0] <= event.x < az[1]:
            # AR 배지 클릭 → 자동 재개(autoresume) 설명 + 켜고 끄기 팝업(코어).
            fn = getattr(self.app, "open_autoresume_info", None)
            fn and fn()
            event.stop()
            return
        uz = self._usage_zone
        if uz and uz[0] <= event.x < uz[1]:
            # 토큰 사용량("N%/5h used") 클릭 → 영속 통계 팝업(모든 세션 합계 포함, pytmux
            # 재시작 후에도 유지). claude-code 플러그인 설치. 이 세그먼트는 5h% 가 핵심이라
            # 계층 타임라인 뷰로 연다 — 오늘 행이 시각까지 기본 펼쳐져 시각별 5h% 막대가
            # 바로 보인다(2026-06-21 계층 트리; 옛 hour 버킷 대체).
            fn = getattr(self.app, "open_token_log", None)
            fn and fn("hour")
            event.stop()
            return
        hz = self._host_zone
        if hz and hz[0] <= event.x < hz[1]:
            self.app.show_status_tabs(initial=2)  # 서버이름 클릭 → 서버 탭(#12)
            event.stop()
