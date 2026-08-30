"""세션 / 윈도우 / 패널(분할 트리) 모델."""
from __future__ import annotations

import asyncio  # noqa: F401  (타입 주석용)
import os
import re
import time
from collections import deque
from functools import lru_cache

from . import plugins, sshwrap, vtconst
from .protocol import HISTORY, MIN_H, MIN_W, conv_color, set_winsize

# 단말 질의(CPR/DSR/DA) 응답을 자식 stdin 으로 되돌릴지 — **Windows(ConPTY)에서는 끈다**
# (Pane._reply_to_child 주석 참조). 모듈 상수라 테스트가 monkeypatch 로 양쪽을 다 돈다.
REPLY_TO_CHILD = os.name != "nt"


@lru_cache(maxsize=8192)
def _style_key(fg, bg, bold, italics, underscore, reverse, strike):
    """Char 스타일 필드 → 직렬화 런 키(정렬된 튜플). render 핫루프가 셀마다
    dict 생성+sort 하던 것을 메모이즈한다(B3) — 대부분 셀이 같은 스타일이라 적중률
    높음. 키에서 `dict(key)` 로 스타일 dict 를 복원한다(클라 _darken_style lru_cache 선례)."""
    d = {}
    f = conv_color(fg)
    b = conv_color(bg)
    if f:
        d["f"] = f
    if b:
        d["b"] = b
    if bold:
        d["bo"] = 1
    if italics:
        d["it"] = 1
    if underscore:
        d["un"] = 1
    if reverse:
        d["rv"] = 1
    if strike:
        d["st"] = 1
    return tuple(sorted(d.items()))


@lru_cache(maxsize=8192)
def _key_to_style(key):
    """정렬 튜플 스타일 키 → 스타일 dict. _serialize_row 가 런마다 `dict(key)` 를 새로
    할당(빈 스타일도 `dict(())` 빈 dict)하던 것을 메모(P2). 대부분 셀이 같은 스타일이라
    적중률 높음. 반환 dict 는 **공유**되므로 호출측은 읽기전용으로만 다룬다 — match
    하이라이트는 `{**st,'rv':1}` 로 복사하고, serverio 는 `==` 비교·JSON 인코딩만 한다."""
    return dict(key)


def line_text(line, sx: int, ex: int) -> str:
    """화면 한 줄의 `[sx..ex]` 칸을 **표시되는 그대로** 문자열로 잇는다.

    핵심 규칙 = **와이드 문자(한글·CJK·이모지)의 두 번째 칸(stub)은 건너뛴다**. 폭 2
    글자는 첫 칸에 글자를, 다음 칸에 `data=""`(빈 문자열) stub 을 심는다(nativescreen
    `draw`). 종전 관용구 `line[x].data or " "` 는 그 빈 값을 **공백으로 접어** 글자마다
    공백을 하나씩 끼워 넣었다 — 제보 2026-07-29: 마우스로 긁어 복사하면
    `뜨면 알려` 가 `뜨 면  알 려` 로 붙여넣어졌고, 같은 이유로 한글 **검색어**도
    화면에 보이는데 안 잡혔다(검색 대상 문자열이 `뜨 면` 이었다).

    `None` 등 결측 data 만 공백으로 접는다(방어). 이 규칙은 나머지 세 추출 표면과
    같다 — 클라 화면-내 추출(`clientwidgets._extract_selection`: stub 은 `""` 라
    이어붙여도 무해)·`Screen.display`(stub skip)·`_serialize_row`(stub 미전송).
    """
    out = []
    for x in range(sx, ex + 1):
        d = line[x].data
        if d == "":
            continue                     # 와이드 글자의 stub — 글자는 앞 칸에 있다
        out.append(" " if d is None else d)
    return "".join(out)


# 배경 갭 메꿈(_fill_flanked_gaps) 대상 공백 런의 최대 길이. Claude Code 등이 탭
# 전개로 남기는 구멍은 탭스톱 간격(≤8)이라 넉넉하되, 패널 배치용 큰 기본-배경
# 여백(정당한 '빈 영역')까지 물들이지 않게 상한을 둔다.
_GAP_FILL_MAX = 16


def _fill_flanked_gaps(segs):
    """같은 **명시 배경**의 런 사이에 낀 짧은 기본-배경 공백 런을 그 배경으로 메꾼다.

    Claude Code 는 트랜스크립트 블록을 명시 배경(48;2;…)으로 칠하면서 **탭 전개
    공백만 기본 배경(SGR 49)** 으로 내보낸다(캡처 실측 2026-07-10:
    `…3.13)\\x1b[49m     \\x1b[48;2;70;70;70mHeadless…`). 기본-배경 셀은 클라이언트
    에서 터미널 기본색으로 패스스루되므로, 회색 블록 한가운데 검은(터미널 배경)
    직사각형 구멍들이 뚫려 보인다(실박스 스크린샷 보고). 실제 터미널에 native 로
    띄워도 같은 구멍이 생기는 앱 쪽 특성이지만, 표시 품질을 위해 양쪽이 **동일한**
    명시 배경으로 감싼 ≤ _GAP_FILL_MAX 칸의 순수 공백 런만 그 배경으로 채운다.

    보수 조건(오탐 시 native 와 달라지므로 좁게): ① 런 전체가 공백 ② 자체 배경·
    반전(rv) 없음(반전은 배경이 전경이 됨) ③ 좌우 이웃 런의 'b' 가 서로 같고 명시적.
    스타일 dict 는 _key_to_style 공유 객체라 복사해 덮는다."""
    for i in range(1, len(segs) - 1):
        text, st = segs[i]
        if len(text) > _GAP_FILL_MAX or "b" in st or "rv" in st:
            continue
        if text.strip(" "):
            continue                      # 순수 스페이스 런만 대상
        left_b = segs[i - 1][1].get("b")
        if left_b is None or segs[i + 1][1].get("b") != left_b:
            continue
        segs[i][1] = {**st, "b": left_b}
    return segs


# Claude Code 가 **자기 회색 배경 위에 자기 subtle 색**으로 그려 대비가 무너지는 표면 →
# 같은 테마의 본문색(`text`)으로 전경만 올린다. 키 = (전경, 배경) truecolor 쌍, 값 =
# 그 테마의 `text`. 값은 Claude Code 2.1.220 번들의 테마 토큰 실측:
#   dark  : subtle rgb(80,80,80)   / userMessageBackground rgb(55,55,55)
#                                    userMessageBackgroundHover rgb(70,70,70) → text 순백
#   light : subtle rgb(175,175,175)/ 220·232·240·252 계열 배경          → text 검정
# (ansi:* 로 정의된 저색상 테마 변형은 truecolor 가 아니라 여기 안 걸린다 — 그쪽은
#  팔레트 대비가 이미 확보돼 있어 손댈 이유도 없다.)
_CLAUDE_SUBTLE_ON_MSG_BG = {
    ("#505050", "#373737"): "#ffffff",
    ("#505050", "#464646"): "#ffffff",
    ("#afafaf", "#dcdcdc"): "#000000",
    ("#afafaf", "#e8e8e8"): "#000000",
    ("#afafaf", "#f0f0f0"): "#000000",
    ("#afafaf", "#fcfcfc"): "#000000",
}


def _boost_claude_prompt_bar(segs):
    """Claude Code 의 '지나간 프롬프트' 스티키 바를 읽히게 **전경만** 올린다.

    Claude Code 는 스크롤로 지나간 프롬프트를 창 맨 위에 `› <프롬프트>` 한 줄로 고정
    표시한다(클릭하면 그 프롬프트 위치로 점프). 이 바를 **subtle**(dark 테마 =
    rgb(80,80,80)) 전경 + **userMessageBackground**(rgb(55,55,55), 마우스가 올라가
    있으면 Hover rgb(70,70,70)) 배경으로 그리는데, 회색 위 회색이라 사실상 안 읽힌다
    (제보 + 실측: 이 두 색은 pytmux 가 손대지 않고 그대로 받은 앱 색이다). 같은
    프롬프트를 트랜스크립트 본문에서는 `text`(순백)로 그리므로, 이 바의 전경만 같은
    테마의 `text` 로 올려 본문과 같은 대비를 준다. 배경은 그대로 둬 '프롬프트 블록'
    이라는 정체성은 유지한다.

    보수 조건(오탐 시 native 와 달라지므로 좁게): ① (전경,배경)이 실측 테마 쌍과
    **정확히** 일치 ② 반전(rv) 없음(반전은 전경/배경이 뒤바뀐다) ③ 런에 보이는 글자가
    둘 이상 — 본문 user 메시지의 `›` 포인터도 subtle 이지만 그건 **혼자** 한 런이고
    (본문 글자는 별도 `text` 런) 이미 잘 읽히므로 여기서 걸러져 native 그대로 남는다.
    스타일 dict 는 _key_to_style 공유 객체라 복사해 덮는다."""
    for i in range(len(segs)):
        text, st = segs[i]
        if "rv" in st or len(text.strip()) < 2:
            continue
        boost = _CLAUDE_SUBTLE_ON_MSG_BG.get((st.get("f"), st.get("b")))
        if boost is None:
            continue
        segs[i][1] = {**st, "f": boost}
    return segs


# restart-all 스냅샷(_export_screen)이 색/속성을 보존하도록 pyte 셀 속성 → SGR
# 이스케이프로 환원한다. pyte fg/bg 는 "default"·기본색명·"bright<name>"·6자리 hex.
_SGR_BASE = {"black": 30, "red": 31, "green": 32, "brown": 33, "yellow": 33,
             "blue": 34, "magenta": 35, "cyan": 36, "white": 37}


def _sgr_color(c, is_bg: bool):
    """pyte 색값 → SGR 코드 리스트(기본색이면 []). is_bg 면 배경 오프셋(+10)."""
    if not c or c == "default":
        return []
    off = 10 if is_bg else 0
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return [48 if is_bg else 38, 2,
                int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)]
    # 보존된 원 pyte 오타(aixterm 105 'bfightmagenta')를 먼저 정규화한다 — 안 하면
    # 아래 'bright' 접두 규칙에 안 걸려 _SGR_BASE 조회가 실패하고 **그 색이 통째로
    # 사라진다**. 재시작 스냅샷은 조용해서(글자는 그대로) 안 드러난다.
    name, bright = vtconst.PYTE_COLOR_TYPOS.get(c, c), False
    if name.startswith("bright"):
        bright, name = True, name[6:]
    base = _SGR_BASE.get(name)
    if base is None:
        return []
    return [base + off + (60 if bright else 0)]


@lru_cache(maxsize=8192)
def _cell_sgr(fg, bg, bold, italics, underscore, reverse, strike) -> str:
    """셀 속성 → SGR 이스케이프 문자열(기본 속성이면 빈 문자열). _export_screen 이
    런 경계마다 끼워 색/굵기/밑줄 등을 복원 feed 에 실어 보낸다. 캐시 적중률 높음."""
    codes = []
    if bold:
        codes.append(1)
    if italics:
        codes.append(3)
    if underscore:
        codes.append(4)
    if reverse:
        codes.append(7)
    if strike:
        codes.append(9)
    codes += _sgr_color(fg, False)
    codes += _sgr_color(bg, True)
    if not codes:
        return ""
    # 런 시작마다 reset(0) 후 속성을 다시 깔아 직전 런 잔여 속성이 안 새게 한다.
    return "\x1b[0;" + ";".join(str(c) for c in codes) + "m"


def _cell_sgr_for(ch) -> str:
    """pyte Char 셀에서 _cell_sgr 캐시 키를 뽑아 SGR 문자열을 얻는다."""
    return _cell_sgr(ch.fg, ch.bg, ch.bold, ch.italics, ch.underscore,
                     ch.reverse, ch.strikethrough)


# 대체 화면 버퍼(alternate screen) 전환 시퀀스. pyte 가 직접 지원하지 않아
# pytmux 가 직접 처리한다(vim/less/htop/Claude Code 등 풀스크린 TUI 용).
_ALT_RE = re.compile(rb"\x1b\[\?(1049|1047|47)(h|l)")

# 내부 앱의 마우스 트래킹 DECSET. 1000=press/release, 1002=+drag, 1003=any-motion,
# 1006=SGR 확장 좌표 인코딩. 클라이언트의 마우스 패스스루 판단에 쓰인다.
# DECSET private 모드는 파라미터를 ';' 로 묶어 한 시퀀스로 보낼 수 있다(예: 결합형
# 해제 `\x1b[?1000;1002;1003;1006l` — tcell/tview 등이 teardown 에 쓴다). 파라미터
# 하나만 매칭하던 옛 정규식은 이런 결합 해제를 놓쳐 마우스 트래킹이 켜진 채로 남았고,
# 앱 종료 후에도 클라가 any-motion 리포트를 셸로 흘려 프롬프트에 텍스트로 박혔다.
# → 전체 파라미터 목록을 잡은 뒤 관심 모드만 추린다.
_MOUSE_DECSET_RE = re.compile(rb"\x1b\[\?([0-9;]+)(h|l)")
_MOUSE_TRACK_MODES = frozenset((1000, 1002, 1003))
# 포커스 이벤트 리포트(DECSET 1004 · pytmux-421). 켜 둔 앱은 단말이 포커스를 얻고 잃을
# 때 `ESC[I`·`ESC[O` 를 받길 기대하고, 그것으로 깜빡임·폴링·자동 새로고침을 멈춘다.
# ★ **마우스와 같은 스캔에 얹는다.** 두 번 훑으면 뜨거운 경로 비용이 두 배이고, 무엇보다
#   carry(경계 이월)를 각자 들면 조각 경계에서 두 스캔의 판정이 **갈릴 수 있다** —
#   같은 바이트를 보고 한쪽만 해제를 놓치는 부류의 결함이 실제로 있었다(위 결합 DECSET).
_FOCUS_MODE = 1004
# DECSET 이 PTY read/FEED_SLICE(8KiB) 경계에 걸쳐 쪼개져도 놓치지 않게 보관하는 직전
# 데이터 꼬리 길이. 스캔이 stateless 라 `\x1b[?1000;1002;1003;1006l` 한 개가 두 조각으로
# 갈리면 **해제를 통째로 놓쳐** 트래킹이 켜진 채 남는다(= 앱 종료 후 셸 프롬프트에 SGR
# 리포트가 텍스트로 박히는 증상). 관심 시퀀스 최장(결합형 8모드 해제)보다 넉넉히 잡는다.
# carry 를 앞에 붙여 다시 스캔하므로 같은 시퀀스가 두 번 적용될 수 있으나, 모드 집합
# add/discard 는 멱등이고 순서도 그대로라 결과는 같다.
_MOUSE_CARRY = 64

# 풀스크린 클리어(erase-in-display all): CSI 2J / CSI 3J. alt-screen 에서 이 시퀀스
# 이전에 그려진 내용은 전부 지워지므로(스크롤백 없음) 그 앞 바이트는 화면에 안 보인다.
_FULL_CLEAR_RE = re.compile(rb"\x1b\[[23]J")


def coalesce_alt_repaints(buf: bytes, alt_active: bool) -> bytes:
    """alt-screen 풀스크린 리페인트 버스트에서 무효화된 중간 프레임 바이트를 버려
    pyte feed 부하를 줄인다(docs/internal/HANDOFF.md §10 대응 ②, "같은 프레임 다중 리페인트
    합치기"). Claude busy 스피너처럼 매 프레임 화면을 통째로 다시 그리는 출력이
    feed 속도보다 빠르게 쌓일 때, 마지막 한 프레임만 보이므로 그 앞을 드롭한다.

    안전 조건(하나라도 어긋나면 buf 를 **그대로 반환** — 손실 없음):
      - ``alt_active`` 여야 한다. main-screen 은 위로 밀린 줄이 스크롤백에 쌓이므로
        바이트를 버리면 스크롤백을 잃는다(절대 드롭 금지).
      - buf 안에 alt-screen 전환(``_ALT_RE``: ?1049/?1047/?47 h|l)이 없어야 한다.
        있으면 버퍼가 화면 경계를 가로질러 단순 드롭이 안전하지 않다.
      - 풀클리어(CSI 2J/3J)가 **2개 이상**이어야 한다 — 즉 이미 여러 프레임이 밀려
        '뒤처진' 상태일 때만 합친다(중간 프레임들은 각자 상태를 세팅·리셋하는 완결된
        리페인트라, 마지막 클리어 이후 프레임이 자기 상태를 다시 세운다).

    드롭 지점은 **마지막 풀클리어의 시작 위치 이전 전부**다. 클리어 자체와 그 뒤
    리페인트는 온전히 남으므로 "화면을 비우고 새 프레임을 그린다"는 결과가 보존된다.
    """
    if not alt_active:
        return buf
    if _ALT_RE.search(buf):
        return buf
    last = -1
    count = 0
    for m in _FULL_CLEAR_RE.finditer(buf):
        last = m.start()
        count += 1
    if count < 2 or last <= 0:
        return buf
    return buf[last:]


class Pane:
    """잎 노드. 셸 PTY + pyte 화면 버퍼 + 스크롤백 뷰포트."""

    #: 타이틀 밖의 OSC 관찰자 ``fn(pane, code, param)``. 서버가 패널 생성 시 꽂는다.
    #: 안 꽂히면(테스트·클라·플러그인 부재) OSC 훅이 아무 일도 하지 않는다.
    osc_handler = None

    #: OSC 52 로 앱이 «클립보드에 이것을 넣어라» 한 base64 본문. 서버가 다음 flush 에
    #: 걷어(`take_clipboard`) 광고한 클라에게 넘기고 None 으로 돌린다. 여러 번 와도
    #: **마지막 것만** 남긴다 — 클립보드는 하나뿐이라 중간 값은 어차피 덮인다.
    #: ⛔ 코어가 base64 를 풀지 않는다. 푸는 자리는 OS 클립보드를 가진 **클라**다.
    _clipboard_pending = None

    def __init__(self, pid: int, fd: int, cols: int, rows: int,
                 vt_parser: str = "native", screen_impl: str | None = None):
        self.id = pid_counter()
        self.master_fd = fd
        self.child_pid = pid
        self.cols = cols
        self.rows = rows
        # 화면 백엔드는 자작 nativescreen 단일(M4b, 2026-07-18: pyte 완전 은퇴). vt_parser·
        # screen_impl 파라미터는 상위 opts 배선 호환을 위해 받되, 값과 무관하게 native
        # 파서(vtparse.VTTokenizer)+native 화면만 쓴다("pyte" 선택은 native 로 수렴).
        # 메인 화면(스크롤백 보관) + 대체 화면(풀스크린 TUI 용, 스크롤백 없음)
        self._main = self._make_main_screen(cols, rows)
        self._main.set_mode(vtconst.LNM)
        self._alt = None
        self.alt_active = False
        self.screen = self._main      # 현재 활성 화면(렌더 대상)
        # 자작 증분 토크나이저(vtparse.VTTokenizer)를 패널에 상주시켜 native 화면에 직접
        # 디스패치한다(콜론SGR·XTMODKEYS·kitty·alt·partial-CSI 를 1급 처리 — feed 전
        # 정규식 우회 불필요). alt 전환은 _on_alt_transition 콜백으로 라우팅한다.
        self._tok = self._make_tokenizer()
        # §1.7 중첩 능동 감지: XTVERSION 질의(ESC[>0q)가 read 경계에 걸쳐 쪼개져도
        # 놓치지 않게 직전 청크 꼬리(질의 길이-1 바이트)를 보관(serverpty 스캔용).
        self._nestq_carry = b""
        # 동기화 출력(DEC 2026) 지원 질의(ESC[?2026$p)가 read 경계에 걸쳐 쪼개져도
        # 놓치지 않게 직전 청크 꼬리를 보관(serverpty 광고 스캔용). 배칭 자체는
        # sync_output/_flush_loop 가 이미 담당한다(여긴 광고 질의용 carry 뿐).
        self._syncq_carry = b""
        # 원격 중첩 자동 승격(NESTED_ATTACH_SCENARIO): 가변 길이 NEST DCS(목적지
        # 기록/승격 요청)의 read 경계 보전 carry + ssh 래퍼가 기록한 이 패널의
        # 마지막 ssh 목적지(사용자가 친 문자열 그대로 — 자동 remote-attach 인자의
        # 유일한 출처, 패널 출력 self-report 는 쓰지 않는다) + 승격 요청 디바운스.
        # 전부 휘발성(재시작 비영속 — dest 는 다음 ssh 때 다시 기록된다).
        self._nestd_carry = b""
        self._ssh_dest = ""
        self._ssh_dest_ts = 0.0
        self._nest_req_ts = 0.0
        # 대량 출력 청크 드레인(server._feed_drain): PTY 에서 읽었으나 아직 pyte 에
        # 안 먹인 바이트와, 진행 중인 비동기 드레인 태스크(서버가 생성/취소 관리).
        self._feedbuf = b""
        self._feed_task = None
        # 버스트 감지(server._on_pane_data): 짧은 간격으로 연달아 도착하는 소형 청크의
        # 연속 횟수와 마지막 도착 시각(monotonic). Windows owned-ConPTY 처럼 read 가
        # FEED_SLICE 로 캡돼 모든 청크가 인라인 한계 이하여도, 고빈도 버스트를 감지해
        # 드레인 경로(pause 백프레셔·repaint coalesce·슬라이스 양보)로 돌리는 데 쓴다.
        self._burst_run = 0
        self._burst_ts = 0.0
        self.scroll = 0          # 0 = live(맨 아래), 양수 = 위로 N 행
        self.dirty = True
        # 행 단위 재직렬화 캐시(#8): 직전 render 의 행 직렬화 결과(라이브 뷰 한정).
        # 다음 render 는 pyte screen.dirty 가 표시한 행만 다시 만들고 나머지는 이
        # 캐시를 재사용한다. _row_cache_key=(cols,lines,id(screen))로 크기·alt 전환을
        # 감지해 무효화한다. render 가 패널당 flush당 1회라 클라 델타와 충돌 없음.
        self._row_cache = None
        self._row_cache_key = None
        self._last_wrap = []     # 직전 render 의 soft-wrap 연속원 행(프레임 상대 인덱스)
        # 직전 render 뷰포트의 **첫 행 절대 인덱스**(스크롤백 top 기준 — `_match_abs` 와
        # 같은 좌표계). 서버가 screen 메시지에 실어 보내면 클라가 화면 행 ↔ 절대 행을
        # 환산할 수 있고, 그래서 마우스 선택이 **스크롤을 넘어** 유지된다(선택을 화면
        # 좌표로 들고 있으면 스크롤 순간 다른 텍스트를 가리킨다).
        self._last_top = 0
        # ConPTY(Windows)는 conhost 가 자기 버퍼에서 줄을 미리 접어 하드 개행으로
        # 재방출하므로 DECAWM 오토랩이 발생하지 않아 wrapped 태그가 영원히 비었다
        # (실캡처 검증: 꽉 찬 줄 36개, 태그 0 — 멀티라인 명령 복사가 줄마다 개행,
        # 사용자 07-15). 이 플래그가 켜진 패널은 render 가 보수적 휴리스틱으로
        # wrap 을 보강한다(꽉 찬 줄 + 경계 양쪽이 박스문자 아님 → 연속원).
        self._prewrap_heuristic = os.name == "nt"
        self.rect = (0, 0, cols, rows)
        self.parent: Split | None = None
        self.title = "shell"
        # 토큰 리밋 자동 재개(토글). 메시지·예약 보류 등 나머지 자동재개 상태와
        # Claude 거동 필드 전반은 claude-code 플러그인이 pane_init 으로 설치한다(S4).
        self.autoresume = False
        self._activity = False   # 마지막 검사 이후 출력 있었음
        self._bell = False       # 마지막 검사 이후 BEL 수신
        # Claude 스캔 dirty 게이팅(B1): feed 마다 _feed_seq 증가(코어). _scan_claude
        # (플러그인)가 마지막 스캔 때 본 seq(_scan_seq, 플러그인 소유)와 같으면 화면
        # 텍스트가 그대로 → 스캔 생략. 비교 대상 _feed_seq 만 코어가 증가시킨다.
        self._feed_seq = 0
        # 탭 리네임을 Claude 세션에 `/rename` 으로 반영(servertree.rename_window) 시
        # busy 면 보류했다가 다음 busy→idle 경계에서 발동한다. 코어 servertree 가 직접
        # 쓰므로(플러그인은 발동만) 코어 Pane 에 남긴다.
        self._pending_rename = None
        # 토큰 영속 로깅(#7) 계정: 마지막 감지/지정한 계정과 manual(수동 지정) 여부.
        # 플러그인(servermixin _log_tokens)이 레코드에 쓰고 set-account 명령이 갱신하나,
        # 코어 servertree 리네임/이관 경로가 읽어 아직 코어 Pane 에 남긴다. (토큰 누계
        # _tok_state/_session_tokens 와 세션 id 는 claude-code 플러그인 pane_init 소유 —
        # 누계는 S5 토큰 모듈화 T4 에서 panestate 로 이전.)
        self._claude_account = None
        # footer 표시용 비별칭(전체) 계정 — 폭이 충분하면 전체 이메일을 보인다(요청
        # 2026-06-12). 로그·이벤트는 별칭(_claude_account)을 그대로 쓰고, 이 값은 상태
        # 메시지로만 클라에 전달된다. 별칭과 같은 스크랩 판정으로 함께 갱신된다.
        self._claude_account_full = None
        self._claude_account_manual = False
        # 프롬프트 단위 클리어 큐(#4): 사용자가 미리 쌓아 둔 명령들. respawn 시 코어가
        # 직접 비우므로(reinit) 코어 Pane 에 남긴다(모드/상태기계는 플러그인 소유).
        self.prompt_clear_queue = []
        self.search_query = ""   # 스크롤백 검색어
        self._match_abs = None   # 현재 매치된 절대 라인 인덱스
        self.bracketed = False   # 내부 앱이 bracketed paste 모드를 켰는지
        # 동기화 출력(DEC private 2026, BSU/ESU): 내부 앱이 한 프레임을 ?2026h…?2026l
        # 로 감싸 '원자적으로' 그려지길 기대하는 동안 True. 서버 flush 가 그 중간을
        # 보내지 않게 하는 게이트(update_sync_output 참조).
        self.sync_output = False
        self._sync_since = 0.0   # sync_output 가 켜진 시각(time.monotonic) — 타임아웃용
        # 내부 앱의 마우스 트래킹 모드(DECSET). 클라이언트가 이 패널로 마우스를
        # 패스스루할지/어떻게 인코딩할지 판단하는 데 쓴다(서버가 추적해 전달).
        self._mouse_modes = set()   # 켜진 {1000,1002,1003}
        self.mouse_track = 0        # 0=off 1=press/release 2=+drag 3=any-motion
        self.mouse_sgr = False      # 1006 SGR 확장 좌표 인코딩 사용 여부
        self._mouse_sent = (0, False)  # 클라이언트로 마지막 전달한 (track, sgr)
        self._mouse_carry = b""     # DECSET 이 read 경계에 걸릴 때의 직전 꼬리
        self._mouse_on_seen = False  # 직전 슬라이스에 트래킹 **켜기**가 있었는지
        # 트래킹을 켠 앱의 포그라운드 프로세스 그룹(서버가 tcgetpgrp 로 기록, 0=모름).
        # 그 그룹이 사라졌는데 트래킹이 남아 있으면 = 앱이 teardown 없이 죽은 stale
        # 상태다(serverio._mouse_tracking_stale).
        self._mouse_owner_pgid = 0
        # 앱이 포커스 리포트(1004)를 켰는가. 켜 둔 패널에만 `ESC[I`/`ESC[O` 를 쓴다 —
        # 안 켠 앱에 쓰면 그 두 바이트가 **글자로** 화면에 박힌다.
        self.focus_track = False
        # 이번 슬라이스에서 **켜졌다**. 서버가 그때 지금 상태를 한 번 알려 준다 —
        # 안 알리면 앱은 켠 직후부터 다음 전이까지 「모르는 채」이고 그 사이 그림이 틀린다.
        self._focus_armed = False
        # 서버가 이 패널에 마지막으로 알려 준 포커스(None=아직 안 알렸다).
        self._focus_sent = None
        self.pipe_proc = None    # pipe-pane 대상 프로세스
        # PTY 백엔드 핸들(pty_backend.PtyProcess). 서버가 spawn 직후 주입한다.
        # 렌더 전용(replay/진단) 패널은 None — master_fd/child_pid 만 -1 로 둔다.
        self.pty = None
        # Windows 세션유지 재시작 host 모드(옵션 C): host 프로세스가 소유한 원격 PTY 의
        # 식별자(서버 할당). None 이면 인프로세스 PTY(POSIX/비host). child_pid/master_fd 가
        # -1 이어도 이 id 로 재시작을 가로질러 패널을 재바인딩한다.
        self.host_pane_id = None
        # 플러그인 공용 패널 네임스페이스(S4). claude-code 가 pane_init 훅으로 Claude
        # 거동 필드(~40개: 상태/사용량/자동개입 타이머/권한모드/프롬프트 추적 등)를
        # 패널에 설치한다. 디렉토리 삭제 시 훅이 없어 그 필드가 안 생기고, 코어의 소수
        # 읽기 지점(serverpty 자동재개·servertree 리네임·
        # _log_tokens)은 getattr(…, 기본값)으로 안전하게 동작한다(delete-to-disable).
        self.plugin_state = {}
        plugins.get().pane_init(self)

    def reinit(self, pid: int, fd: int, cols: int, rows: int) -> None:
        """respawn: 새 PTY/셸로 화면 버퍼를 초기화한다."""
        self.master_fd = fd
        self.child_pid = pid
        self.pty = None          # 서버가 reinit 직후 새 PtyProcess 를 주입
        self.host_pane_id = None  # respawn: host 모드면 서버가 새 id 를 다시 설정
        self.cols, self.rows = cols, rows
        self._main = self._make_main_screen(cols, rows)
        self._main.set_mode(vtconst.LNM)
        self._alt = None
        self.alt_active = False
        self.screen = self._main
        # 새 셸용으로 토크나이저 재생성(증분 상태/디코더 초기화).
        self._tok = self._make_tokenizer()
        # NESTED_ATTACH: 새 셸이므로 NEST carry/ssh 목적지 기록도 무효(이전 셸의
        # 목적지로 자동 승격하지 않게 — 다음 ssh 가 다시 기록한다).
        self._nestd_carry = b""
        self._ssh_dest = ""
        self._ssh_dest_ts = 0.0
        self._feedbuf = b""
        self._feed_task = None
        self.scroll = 0
        self.dirty = True
        self._row_cache = None       # 행 재직렬화 캐시 리셋(#8; 새 화면 객체)
        self._row_cache_key = None
        # 계정 리셋 — 새 셸이므로 미지정에서 시작. (토큰 누계 _tok_state/_session_tokens
        # 리셋은 S5 T4 에서 plugins.pane_reset → panestate 로 이전.)
        self._claude_account = None
        self._claude_account_full = None
        self._claude_account_manual = False
        self.prompt_clear_queue = []  # 새 셸이므로 쌓인 명령 큐도 버린다(#4)
        # Claude 거동 필드(스캔버퍼·자동재개·자동정리·세션id·권한모드·done 디바운스·
        # 헤더 디바운스 등)의 새 셸 리셋은 claude-code 플러그인이 pane_reset 으로 한다(S4).
        plugins.get().pane_reset(self)
        self.search_query = ""
        self._match_abs = None
        self.bracketed = False
        self.sync_output = False
        self._sync_since = 0.0
        self.reset_mouse_modes()
        self._mouse_sent = (0, False)

    # 작업 보존 재시작(re-exec)용 직렬화 — docs/internal/RESTART_SCENARIO.md ⓑ/ⓓ.
    # setattr 로 그대로 복원 가능한 JSON 가능 스칼라/딕트 필드 목록. PTY 식별자
    # (child_pid·master_fd)와 크기·화면 스냅샷은 export_state 가 별도로 다룬다.
    # Claude 거동 필드(_claude·_claude_usage·_scanbuf·_resume_pending·resume_msg·
    # last_prompt·_claude_session_id·prompt_clear_mode·_rc_done·
    # pending_prompts·토큰 누계 _tok_state/_session_tokens)의 직렬화는 claude-code
    # 플러그인이 pane_serialize/pane_restore 훅으로 담당한다(S4/S5 — export_state 가
    # 'plugin_state' 키로 불투명하게 담는다). 여기 남는 건 코어가 직접 쓰는 계정/리네임/
    # 토글 필드뿐이다.
    _RESUME_FIELDS = (
        "title", "autoresume", "_claude_account", "_claude_account_full",
        "_claude_account_manual",
        "_pending_rename",   # 재시작 중 보류된 탭→세션 리네임도 idle 경계에서 발동
        "bracketed",
    )

    def _serialize_line(self, line, columns: int) -> str:
        """한 줄(pyte 버퍼 행)을 SGR(색/속성) 포함 문자열로. 마지막 비공백 셀까지만
        내보내고(뒤 공백 절약), 속성 없는 줄은 이스케이프 0(평문 그대로 — 회귀 없음).
        와이드 문자 연속 셀(data=="")은 건너뛴다(import feed 가 다시 만든다)."""
        last = -1
        for x in range(columns):
            d = line[x].data
            if d != "" and d != " ":
                last = x
        if last < 0:
            return ""
        # cur_sgr="" = 기본(reset) 상태 기준. 색→기본 전이는 명시적 reset 으로 닫는다.
        parts, cur_sgr = [], ""
        for x in range(last + 1):
            ch = line[x]
            if ch.data == "":
                continue
            sgr = _cell_sgr_for(ch)
            if sgr != cur_sgr:
                parts.append(sgr if sgr else "\x1b[0m")
                cur_sgr = sgr
            parts.append(ch.data)
        if cur_sgr:
            parts.append("\x1b[0m")
        return "".join(parts)

    def _export_screen(self) -> list[str]:
        """메인 화면(스크롤백+현재 버퍼) 전체를 SGR 포함 줄 목록으로(뒤 빈 줄 제거).
        스크롤-업 연속성·하위호환용. 정확 복원은 _export_history/_export_viewport 사용."""
        scr = self._main
        h = getattr(scr, "history", None)
        hist = list(h.top) if h is not None else []
        lines = hist + [scr.buffer[y] for y in range(scr.lines)]
        out = [self._serialize_line(line, scr.columns) for line in lines]
        while out and not out[-1]:
            out.pop()
        return out[-HISTORY:]

    def _export_history(self) -> list[str]:
        """스크롤백(화면 밖으로 밀린 줄)만 SGR 포함으로. 뒤 빈 줄 제거·HISTORY 캡."""
        scr = self._main
        h = getattr(scr, "history", None)
        hist = list(h.top) if h is not None else []
        out = [self._serialize_line(line, scr.columns) for line in hist]
        while out and not out[-1]:
            out.pop()
        return out[-HISTORY:]

    def _export_viewport(self) -> list[str]:
        """현재 화면(보이는 scr.lines 행)을 **빈 줄 트림 없이 그대로** SGR 포함으로.
        행 수·위치가 앱의 화면 모델과 정확히 일치해야 execv 후 부분 repaint(메인 화면
        TUI 의 SIGWINCH 갱신)가 어긋나지 않는다(restart-all 커서·줄 정합, B/D)."""
        scr = self._main
        return [self._serialize_line(scr.buffer[y], scr.columns)
                for y in range(scr.lines)]

    def export_state(self) -> dict:
        """재시작 시 보존할 패널 상태를 JSON 가능 dict 로 직렬화한다.

        PTY 식별자(child_pid·master_fd 번호)·크기·마우스 모드·프롬프트 큐·화면
        스냅샷을 포함한다. 새 서버 이미지가 import_state 로 같은 Pane 상태를 복원하고,
        master_fd 번호로 상속된 PTY 를 다시 채택한다. docs/internal/RESTART_SCENARIO.md ⓑ."""
        d = {
            "child_pid": self.child_pid,
            "master_fd": self.master_fd,
            # host 모드(옵션 C): 새 서버가 재시작 후 host 에 이 id 로 재바인딩한다.
            "host_pane_id": self.host_pane_id,
            "cols": self.cols,
            "rows": self.rows,
            "mouse_modes": sorted(self._mouse_modes),
            "mouse_sgr": self.mouse_sgr,
            # 트래킹 소유 프로세스 그룹도 넘긴다 — PTY/셸은 재시작을 가로질러 살아
            # 있으므로 pgid 도 그대로 유효하고, 안 넘기면 복원된 플래그의 stale 여부를
            # 새 이미지가 영영 판정 못 한다(앱이 다음 켜기를 낼 때까지).
            "mouse_owner_pgid": self._mouse_owner_pgid,
            "prompt_clear_queue": list(self.prompt_clear_queue),
            # Claude 보존 필드(프롬프트 대기큐·상태/세션 등)는 플러그인이
            # 직렬화한다(S4). 코어는 내용을 해석하지 않고 불투명 dict 로 담는다 —
            # 플러그인 부재 시 {} 라 복원도 no-op(delete-to-disable).
            "plugin_state": plugins.get().pane_serialize(self),
            # 스크롤백(연속성·하위호환) + 정확 뷰포트/커서(메인 화면 TUI 정합, B/C/D).
            "screen": self._export_screen(),          # 하위호환(구 이미지 읽기)
            "history": self._export_history(),         # 스크롤백만
            "viewport": self._export_viewport(),       # 현재 화면(트림 없음)
            "cursor": {"x": self._main.cursor.x, "y": self._main.cursor.y,
                       "hidden": bool(self._main.cursor.hidden)},
        }
        # 대체 화면(alt-screen) 보존(2026-06-19): export 가 _main 만 담으면, **alt 에
        # 그리는 풀스크린 TUI**(갓 띄운/idle Claude·vim·htop)는 재시작 후 _main(셸
        # `% claude`)만 복원돼 **빈 패널**이 된다(restart-all idle Claude 탭 빔 = 시나리오
        # B, RESTART_SCENARIO.md §주의①). 사용자가 실제로 보는 화면이 _alt 이므로 alt
        # 활성 패널은 _alt 의 뷰포트·커서도 담아 import 가 alt 를 그대로 되살린다.
        if self.alt_active and self._alt is not None:
            d["alt_active"] = True
            d["alt_viewport"] = [
                self._serialize_line(self._alt.buffer[y], self._alt.columns)
                for y in range(self._alt.lines)]
            ac = self._alt.cursor
            d["alt_cursor"] = {"x": ac.x, "y": ac.y, "hidden": bool(ac.hidden)}
        for f in self._RESUME_FIELDS:
            d[f] = getattr(self, f)
        return d

    def import_state(self, d: dict) -> None:
        """export_state 가 만든 dict 로 패널 상태를 복원한다(child_pid·master_fd 는
        생성자에서 이미 설정됐으므로 여기서 건드리지 않는다)."""
        for f in self._RESUME_FIELDS:
            if f in d:
                setattr(self, f, d[f])
        # host 모드(옵션 C) 재바인딩 식별자 — 생성자에서 못 받았으면 여기서 복원.
        if d.get("host_pane_id") is not None:
            self.host_pane_id = d["host_pane_id"]
        self._mouse_modes = set(d.get("mouse_modes", []))
        self.mouse_sgr = bool(d.get("mouse_sgr", False))
        self.mouse_track = (3 if 1003 in self._mouse_modes
                            else 2 if 1002 in self._mouse_modes
                            else 1 if 1000 in self._mouse_modes else 0)
        try:
            self._mouse_owner_pgid = int(d.get("mouse_owner_pgid", 0) or 0)
        except (TypeError, ValueError):
            self._mouse_owner_pgid = 0
        self.prompt_clear_queue = list(d.get("prompt_clear_queue", []))
        # Claude 보존 필드 복원은 플러그인이(S4). 구 이미지 하위호환: 예전 export 는
        # pending_prompts 를 최상위 키로 담았으므로, plugin_state 가 없고 그 키가
        # 있으면 함께 넘겨 준다.
        ps = d.get("plugin_state")
        if ps is None and "pending_prompts" in d:
            ps = {"pending_prompts": d.get("pending_prompts", [])}
        plugins.get().pane_restore(self, ps or {})
        view = d.get("viewport")
        if view is not None:
            # 정확 복원(B/C/D): 스크롤백 + **현재 화면(트림 없음)** 을 한 번에 피드하되
            # 끝에 개행을 붙이지 않아(마지막 줄이 한 칸 스크롤돼 커서가 밀리던 D 원인)
            # 마지막 scr.lines 줄이 화면을 정확히 채우게 한다. 이어서 커서를 앱이 두고
            # 간 좌표로 절대 이동(CUP)해, execv 후 메인 화면 TUI 의 부분 repaint 가
            # 어긋나지 않게 한다. 살아 있는 앱의 다음 출력/SIGWINCH repaint 가 이어 그린다.
            hist = d.get("history") or []
            payload = "\r\n".join(list(hist) + list(view))
            self.feed(payload.encode("utf-8", "ignore"))
            cur = d.get("cursor") or {}
            try:
                cy = int(cur.get("y", 0)) + 1
                cx = int(cur.get("x", 0)) + 1
            except (TypeError, ValueError):
                cy = cx = 1
            self.feed(f"\x1b[{cy};{cx}H".encode("ascii"))
            if cur.get("hidden"):
                self.feed(b"\x1b[?25l")   # 앱이 커서를 숨긴 상태였으면 복원
        elif d.get("screen"):
            # 하위호환(구 이미지가 쓴 스냅샷 — viewport/cursor 없음): 기존 평문 경로.
            self.feed(("\r\n".join(d["screen"]) + "\r\n").encode("utf-8", "ignore"))
        # 대체 화면 복원(export 의 alt_active): 메인 복원 뒤 alt-screen 에 진입(?1049h →
        # _enter_alt)해 alt 뷰포트·커서를 재생한다. 이렇게 하면 살아 있는 앱이 idle 라
        # SIGWINCH 에 repaint 하지 않아도(2026-06-19 시나리오 B) 사용자가 보던 풀스크린
        # 화면이 스냅샷에서 즉시 복원된다. 구 이미지(alt_* 키 없음)는 no-op(하위호환).
        if d.get("alt_active"):
            self.feed(b"\x1b[?1049h")
            alt_vp = d.get("alt_viewport") or []
            if alt_vp:
                self.feed("\r\n".join(alt_vp).encode("utf-8", "ignore"))
            ac = d.get("alt_cursor") or {}
            try:
                acy = int(ac.get("y", 0)) + 1
                acx = int(ac.get("x", 0)) + 1
            except (TypeError, ValueError):
                acy = acx = 1
            self.feed(f"\x1b[{acy};{acx}H".encode("ascii"))
            if ac.get("hidden"):
                self.feed(b"\x1b[?25l")

    def reset_mouse_modes(self) -> None:
        """마우스 트래킹 상태를 전부 끈다(새 셸 respawn·stale 회수 공용).
        `_mouse_owner_pgid` 도 지워 다음 앱이 자기 그룹을 새로 등록하게 한다."""
        self._mouse_modes = set()
        self.mouse_track = 0
        self.mouse_sgr = False
        self._mouse_carry = b""
        self._mouse_on_seen = False
        self._mouse_owner_pgid = 0
        # 포커스 리포트도 앱의 것이다 — 셸이 새로 뜨면 아무도 안 켠 상태로 돌아간다.
        self.focus_track = False
        self._focus_armed = False
        self._focus_sent = None

    def update_mouse_modes(self, data: bytes) -> bool:
        """피드 데이터에서 앱이 켠 DECSET 을 추적한다 — 마우스(1000/1002/1003/1006)와
        **포커스 리포트(1004)**.
        bracketed paste(2004) 추적과 같은 위치에서 호출. 상태가 바뀌면 True 를
        반환해 서버가 클라이언트에 레이아웃을 다시 보내게 한다.

        데이터는 PTY read/FEED_SLICE 단위로 잘려 오므로 시퀀스가 경계에 걸칠 수 있다
        → 직전 꼬리(_MOUSE_CARRY)를 앞에 붙여 스캔한다(NEST/SYNC 질의 스캔과 같은
        기법). 이 슬라이스에서 트래킹을 **켠** 시퀀스를 봤으면 `_mouse_on_seen` 을
        세워, 서버가 그 시점의 포그라운드 프로세스 그룹을 소유자로 기록하게 한다."""
        prev = self._mouse_carry
        self._mouse_carry = (data[-_MOUSE_CARRY:] if len(data) >= _MOUSE_CARRY
                             else (prev + data)[-_MOUSE_CARRY:])
        self._mouse_on_seen = False
        # 빠른 탈출: 이 조각에도, 이어붙일 꼬리에도 DECSET 이 될 씨앗이 없다.
        if b"\x1b[?" not in data and b"\x1b" not in prev:
            return False
        before = (self.mouse_track, self.mouse_sgr)
        for mo in _MOUSE_DECSET_RE.finditer(prev + data):
            on = mo.group(2) == b"h"
            # carry 안에서 **끝난** 시퀀스는 이전 호출에서 이미 본 것이다(멱등 재적용).
            # 그걸 '지금 켰다'로 세면 소유자 pgid 가 그 시퀀스를 낸 앱이 아니라 지금
            # 전경에 있는 프로세스(대개 앱이 죽은 뒤의 셸)로 잘못 기록돼, stale 판정이
            # 통째로 무력화된다(실측: 실 PTY 종단 테스트가 이 구멍으로 실패했다).
            fresh = mo.end() > len(prev)
            for tok in mo.group(1).split(b";"):
                if not tok.isdigit():
                    continue
                mode = int(tok)
                if mode == 1006:
                    self.mouse_sgr = on
                elif mode == _FOCUS_MODE:
                    if on and not self.focus_track:
                        self._focus_armed = True
                    self.focus_track = on
                elif mode in _MOUSE_TRACK_MODES:
                    if on:
                        self._mouse_modes.add(mode)
                        if fresh:
                            self._mouse_on_seen = True
                    else:
                        self._mouse_modes.discard(mode)
        self.mouse_track = (3 if 1003 in self._mouse_modes
                            else 2 if 1002 in self._mouse_modes
                            else 1 if 1000 in self._mouse_modes else 0)
        return (self.mouse_track, self.mouse_sgr) != before

    def update_sync_output(self, data: bytes) -> bool:
        """피드 데이터에서 동기화 출력 모드(DEC private 2026, BSU/ESU)를 추적한다.
        ?2026h=프레임 시작, ?2026l=프레임 끝. Claude Code 등 현대 TUI 는 한 프레임을
        이 안에 감싸 원자적으로 그려지길 기대하는데, 서버 flush 가 그 중간 상태를
        클라에 보내면 글자 겹침/반쪽 프레임이 보였다 다음 프레임에 낫는 '무작위
        깨짐'이 난다(tmux 는 2026 을 구현해 안 깨짐). _flush_loop 가 이 플래그를 보고
        프레임 도중엔 송신을 미룬다(SYNC_OUTPUT_MAX_DEFER 타임아웃 안전망 포함).
        bracketed/mouse 추적과 같은 자리(_ingest_slice)에서 원시 바이트로 호출한다.
        한 슬라이스에 h·l 이 함께 오면 **마지막 토글**(rfind)이 최종 상태다. 상태가
        바뀌면 True 반환(켜짐 전이 때 _sync_since 에 시각 기록)."""
        if b"\x1b[?2026" not in data:
            # 토글 없는 '프레임 중간' 슬라이스. 큰 프레임은 FEED_SLICE(8KB) 여러 조각에
            # 걸쳐 들어오는데(serverpty._feed_drain), 동기화 중이면 이건 아직 그 프레임을
            # 그리는 중이란 뜻이다. _sync_since 를 갱신해 디퍼 타임아웃이 '프레임 총
            # 소요'가 아니라 '마지막 바이트 이후 침묵'을 재게 한다 — 안 그러면 무거운
            # 스크롤(대형 프레임)이 SYNC_OUTPUT_MAX_DEFER 를 넘겨 _flush_loop 가 반쪽
            # 프레임을 송신(글자 겹침)한다. 먹통 앱은 바이트가 끊겨 갱신이 멈추므로
            # 타임아웃 안전망은 그대로 동작한다.
            if self.sync_output:
                self._sync_since = time.monotonic()
            return False
        on = data.rfind(b"\x1b[?2026h") > data.rfind(b"\x1b[?2026l")
        if on == self.sync_output:
            if on:   # 동기화 유지 슬라이스(추가 BSU/바이트) — 위와 같은 이유로 리셋
                self._sync_since = time.monotonic()
            return False
        self.sync_output = on
        if on:
            self._sync_since = time.monotonic()
        return True

    # 레이아웃 계산용
    def first_pane(self) -> "Pane":
        return self

    def _make_main_screen(self, cols: int, rows: int):
        """메인(스크롤백) 화면 = 자작 nativescreen.NativeScrollbackScreen."""
        from .nativescreen import NativeScrollbackScreen
        screen = NativeScrollbackScreen(cols, rows, history=HISTORY, ratio=0.5)
        screen.write_process_input = self._reply_to_child
        return screen

    def _make_alt_screen(self, cols: int, rows: int):
        """대체(alt) 화면 = 자작 nativescreen.NativeScreen(스크롤백 없음)."""
        from .nativescreen import NativeScreen
        screen = NativeScreen(cols, rows)
        screen.write_process_input = self._reply_to_child
        return screen

    def _reply_to_child(self, data: str) -> None:
        """단말 **질의 응답**(CPR/DSR/DA)을 이 패널의 pty stdin 으로 되돌려준다.

        nativescreen 의 `report_device_status`(DSR/CPR `ESC[6n`)·
        `report_device_attributes`(DA `ESC[c`)는 응답 문자열을 만들어
        `write_process_input` 으로 넘기는데, 그 훅이 스텁(pass)이라 **지금까지 모든
        단말 질의가 삼켜졌다** — pytmux 안의 앱은 커서 위치도, 단말 정체도 물어볼 수
        없었다(무응답 = 타임아웃 후 폴백). 그래서 CPR 로 단말 특성을 재는 앱은
        pytmux 안에서만 감지에 실패했다: 실제 사례가 blog-editor 의 East Asian
        Ambiguous 폭 감지(`·` 를 1칸으로 그리는지 2칸으로 그리는지 CPR 로 전진 칸수를
        잰다) — pytmux 격자는 그 답을 알고 있는데 물어볼 길이 없었다.

        응답 바이트는 **우리가 만든 고정 형식**(`ESC[y;xR`·`ESC[?6c`·`ESC[0n`)뿐이라
        패널 출력이 stdin 으로 임의 바이트를 밀어 넣는 통로가 되지 않는다(개행 없음 →
        셸 명령 주입 불가). 같은 진입점 성격의 선례가 이미 있다 — serverpty 가
        XTVERSION·DEC 2026 질의에 `pane.pty.write` 로 답한다. 이쪽은 **격자 상태**
        (커서 좌표)가 필요해 파서가 그 시퀀스를 처리하는 지점에서 답해야 한다.

        pty 가 없는 패널(재시작 복원·리플레이 전용)은 조용히 무시한다.

        ⚠️ **Windows(ConPTY)에서는 답하지 않는다**(REPLY_TO_CHILD, 제보 2026-07-27):
        ConPTY 패널에서 자식의 단말은 conhost/OpenConsole 자신이라 CPR/DA 를 **제 화면
        버퍼에서** 직접 답한다. 그래서 우리 파서까지 올라오는 `ESC[c` 는 자식의 질의가
        아니라 **번들 OpenConsole 이 부팅할 때 호스트에게 던지는 핸드셰이크 질의**다
        (conpty.py 모듈 docstring "호스트 패리티": OpenConsole 출력이 `\\x1b[c`·
        `\\x1b[?9001h` 로 시작). 여기에 답하면 응답은 이미 지나간 핸드셰이크 창 대신
        ConPTY **입력** 파이프에 타이핑으로 들어가고, 자식 화면에 `^[[?6c` 캐럿표기로
        에코돼 그 자리 글자를 덮어쓴다 — 실측 피해: Claude 관리설정 승인 메뉴의 첫 줄
        `❯ 1. Yes, I trust these settings` 앞부분이 뭉개져 자동 승인
        (claude.claude_managed_settings_yes 의 ❯ 셀렉터 게이트)이 영영 안 걸렸다."""
        if not REPLY_TO_CHILD:
            return
        pty = self.pty
        if pty is None or not data:
            return
        try:
            pty.write(data.encode("ascii", "ignore"))
        except (OSError, ValueError):
            pass          # 자식이 이미 죽었거나 fd 가 닫힘 — 응답은 버려도 무해

    def _make_tokenizer(self):
        """native VT 토크나이저 생성(현재 _main 을 가리키게)."""
        from .vtparse import VTTokenizer
        return VTTokenizer(self._main, alt_hook=self._on_alt_transition,
                           osc_hook=self._on_osc)

    def _on_osc(self, code: str, param: str) -> None:
        """타이틀 밖의 OSC 를 관찰자에게 넘긴다(셸 통합 = OSC 133/7).

        코어는 **해석하지 않는다** — 무엇을 할지는 플러그인이 정한다. 관찰자를 아무도
        안 꽂으면 조용히 버린다(플러그인 디렉토리를 지운 경우 = delete-to-disable).

        관찰자를 **여기서 찾지 않고 꽂아 두는** 이유: 이 경로는 feed 안이라 뜨겁고,
        `plugins.load()` 는 호출마다 디렉토리를 스캔한다. OSC 를 반복 방출하는 프로그램이
        그 스캔을 유발하면 그 자체가 자원 공격이 된다(보안검수 계열 N1/F2 와 같은 부류).
        서버가 패널을 만들 때 한 번 꽂는다.

        ⛔ **딱 하나 예외가 52(클립보드)다.** 그것은 플러그인 관심사가 아니라 단말이
        원래 하는 일이고(tmux 의 `set-clipboard`), 플러그인을 지웠다고 복사가 죽으면
        안 된다. 그래서 코어가 **모아만 두고**(해석·디코드는 안 한다) 훅에도 그대로
        넘긴다 — 보고 싶은 플러그인은 계속 볼 수 있다."""
        if code == "52":
            self._on_clipboard(param)
        handler = self.osc_handler
        if handler is not None:
            handler(self, code, param)

    def _on_clipboard(self, param: str) -> None:
        """OSC 52 본문(`<selection>;<base64>`)을 다음 flush 를 위해 세워 둔다.

        ⛔ **읽기 요청(`<sel>;?`)에는 답하지 않는다.** 그것은 앱이 「클립보드 내용을
        돌려달라」는 것인데, 답하면 패널 안의 아무 프로그램이나(`cat` 한 파일 포함)
        사용자의 클립보드를 훔쳐 갈 수 있다. xterm 의 `allowWindowOps`·tmux 가 기본으로
        막는 자리와 같다 — 우리도 **쓰기만** 받는다.

        선택 대상(`c`=클립보드 · `p`=primary · 여럿이면 `pc` 처럼 붙어 온다)은 지금
        구분하지 않는다. 우리가 닿는 것은 OS 클립보드 하나뿐이고, 없는 구분을 있는 척
        실어 보내면 클라가 그것으로 뭘 해야 하는지 몰라 조용히 버린다."""
        _sel, _, data = param.partition(";")
        if not data or data == "?":
            return
        self._clipboard_pending = data

    def take_clipboard(self):
        """세워 둔 OSC 52 본문을 **걷어** 돌려준다(없으면 None). 두 번 부르면 두 번째는
        None — 같은 복사가 클라 수만큼 반복되지 않게 서버가 한 번만 걷는다."""
        data, self._clipboard_pending = self._clipboard_pending, None
        return data

    def _on_alt_transition(self, enter: bool) -> None:
        """토크나이저가 ?1049/1047/47 h|l 을 감지했을 때의 alt 라우팅
        (_enter_alt/_leave_alt 로 위임)."""
        if enter:
            self._enter_alt()
        else:
            self._leave_alt()

    def feed(self, data: bytes) -> None:
        """증분 토크나이저가 우회 없이 화면에 직접 디스패치한다(alt 라우팅은
        _on_alt_transition 콜백, partial 시퀀스는 토크나이저 상태로 흡수 — feed 전
        정규식 우회 불필요). 스크롤백 뷰포트 고정(R6)은 feed 전체 단위로 적용한다 —
        alt 전환이 끼면 _enter/_leave_alt 가 scroll 을 0 으로 리셋하므로 메인 잔류
        시에만 보정하면 등가다."""
        main = self._main
        track = self.screen is main and self.scroll > 0
        before = len(main.history.top) if track else 0
        self._tok.feed(data)
        if track and self.screen is main:
            after = len(main.history.top)
            if after > before:
                self.scroll = min(self.scroll + (after - before), after)
        self.dirty = True
        self._feed_seq += 1   # B1: Claude 스캔 게이팅용 — 출력 있을 때만 재스캔

    def _enter_alt(self) -> None:
        if self.alt_active:
            return
        self._alt = self._make_alt_screen(self.cols, self.rows)
        self._alt.set_mode(vtconst.LNM)
        # 토크나이저를 alt 화면으로 재지정한다(FSM 상태는 보존).
        self._tok.set_screen(self._alt)
        self.screen = self._alt
        self.alt_active = True
        self.scroll = 0
        self._match_abs = None

    def _leave_alt(self) -> None:
        if not self.alt_active:
            return
        self._alt = None
        self.screen = self._main
        self._tok.set_screen(self._main)
        self.alt_active = False
        self.scroll = 0
        self._match_abs = None

    def _notify_winsize(self, cols: int, rows: int) -> None:
        # PTY 크기 통지는 백엔드 핸들을 통해(크로스플랫폼). 렌더 전용 패널(pty=None)은
        # 옛 fd 기반 set_winsize 로 폴백(fd=-1 이면 무해하게 실패).
        if self.pty is not None:
            try:
                self.pty.set_winsize(rows, cols)
            except OSError:
                pass
        elif isinstance(self.master_fd, int) and self.master_fd >= 0:
            # 렌더 전용 패널(pty=None)인데 유효한 master_fd 가 있으면 fd 기반 폴백.
            # fd=-1(pty 없음/죽음·테스트 스텁)이면 POSIX 에서 fcntl 이 OSError 가
            # 아니라 ValueError 를 던지므로(Python 3.13) 호출 자체를 건너뛴다.
            # Windows 는 set_winsize 가 fcntl/termios 를 지연 import 해 ImportError
            # 가능 — 폭만 못 알릴 뿐 무해하므로 삼킨다.
            try:
                set_winsize(self.master_fd, rows, cols)
            except (OSError, ImportError, ValueError):
                pass

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, cols)
        rows = max(1, rows)
        if cols == self.cols and rows == self.rows:
            return
        # 폭 축소(shrink) 시엔 ConPTY/Claude 에 좁은 폭을 **먼저** 통지해, pyte 가
        # 좁아지기 전에 SIGWINCH 로 좁은-폭 리페인트가 시작되게 한다(폭-불일치 윈도우
        # 최소화 — Windows ConPTY set_winsize 지연 대비). 넓힐 때는 pyte 를 먼저 키워도
        # wrap 이 안 생기므로 기존 순서(화면 먼저)를 유지한다. 어느 쪽이든 _BCEMixin 의
        # autowrap 가드가 남은 전환 윈도우의 cascade 를 truncate 로 흡수한다.
        shrink_w = cols < self.cols
        self.cols, self.rows = cols, rows
        if shrink_w:
            self._notify_winsize(cols, rows)
        self._main.resize(rows, cols)
        if self._alt is not None:
            self._alt.resize(rows, cols)
        if not shrink_w:
            self._notify_winsize(cols, rows)
        self.dirty = True

    def _history_len(self) -> int:
        h = getattr(self.screen, "history", None)
        return len(h.top) if h is not None else 0

    def scroll_by(self, delta: int) -> None:
        self.scroll = max(0, min(self.scroll + delta, self._history_len()))
        self.dirty = True

    def scroll_to(self, where: str) -> None:
        self.scroll = self._history_len() if where == "top" else 0
        self.dirty = True

    def extract_range(self, y0: int, x0: int, y1: int, x1: int) -> str:
        """**절대 행 인덱스** 범위의 텍스트를 뽑는다(스크롤백 + 현재 화면).

        마우스 선택이 한 화면을 넘을 수 있게 하려면 추출을 서버가 해야 한다 — 클라는
        현재 뷰포트 셀만 갖고 있어서 화면 밖 줄을 만들 수 없다. 좌표계는 `_last_top`
        (=render 가 클라에 실어 보낸 뷰포트 첫 행)과 같은 절대 인덱스이고, 범위는
        (y0,x0)..(y1,x1) 포함이다(뒤바뀌어 오면 정렬한다).

        규칙은 클라의 화면 내 추출(`MultiplexerView._extract_selection`)과 같게 맞춘다:
        중간 줄은 폭 전체, 각 줄은 rstrip, 단 **soft-wrap 연속원**(다음 줄과 이어진
        줄)은 rstrip·개행 없이 다음 줄에 붙인다. wrap 판정도 render 와 같은 신호를
        쓴다(줄의 `wrapped` 태그 + 마지막 칸이 아직 꽉 참 — 지워져서 더는 wrap 이
        아닌 stale 태그를 배제). 칸→문자 변환도 클라와 같은 `line_text` 규칙이다
        (와이드 글자 stub 은 건너뛴다 — 공백으로 접으면 한글마다 공백이 낀다).

        범위를 벗어난 인덱스는 조용히 클램프한다(스크롤백이 밀려 y0 가 사라졌을 수
        있다 — 선택 중 출력이 계속되면 정상적으로 일어나는 일이다).
        """
        screen = self.screen
        cols = screen.columns
        h = getattr(screen, "history", None)
        hist = list(h.top) if h is not None else []
        full = hist + [screen.buffer[y] for y in range(screen.lines)]
        if not full:
            return ""
        if (y0, x0) > (y1, x1):
            y0, x0, y1, x1 = y1, x1, y0, x0
        y0 = max(0, min(len(full) - 1, y0))
        y1 = max(0, min(len(full) - 1, y1))
        cl = cols - 1
        parts = []
        for y in range(y0, y1 + 1):
            line = full[y]
            sx = x0 if y == y0 else 0
            ex = x1 if y == y1 else cl
            sx = max(0, min(cl, sx))
            ex = max(0, min(cl, ex))
            text = line_text(line, sx, ex)
            wrapped = (y < y1 and getattr(line, "wrapped", False)
                       and line[cl].data != " ")
            if wrapped:
                parts.append(text)          # 다음 줄과 한 줄로(개행·rstrip 없음)
            else:
                parts.append(text.rstrip())
                if y < y1:
                    parts.append("\n")
        return "".join(parts)

    def _serialize_row(self, line, cols):
        """한 줄(line)을 [text, style] 런(run) 목록으로 직렬화한다(매치 하이라이트
        제외). render 의 빠른 경로/전체 경로가 공유한다(#8)."""
        segs = []
        cur_text = []
        cur_key = None
        for x in range(cols):
            ch = line[x]
            data = ch.data
            if data == "":
                # 와이드 문자(이모지·CJK)의 연속 셀: 보내지 않는다(클라이언트가
                # 문자 폭만큼 칸을 차지). 공백으로 바꾸면 한 칸씩 밀린다.
                continue
            if not data:
                data = " "
            key = _style_key(ch.fg, ch.bg, ch.bold, ch.italics,
                             ch.underscore, ch.reverse,
                             getattr(ch, "strikethrough", False))
            if key != cur_key:
                if cur_text:
                    segs.append(["".join(cur_text), _key_to_style(cur_key)])
                cur_text = [data]
                cur_key = key
            else:
                cur_text.append(data)
        if cur_text:
            segs.append(["".join(cur_text), _key_to_style(cur_key)])
        return _boost_claude_prompt_bar(_fill_flanked_gaps(segs))

    def render(self, with_cursor: bool):
        """현재 뷰포트를 [rows, cursor] 로 직렬화. rows = 행마다 [text, style] 런 목록.

        #8 행 단위 재직렬화: 라이브 뷰(scroll 0)·검색 비활성·캐시 유효(같은 화면
        객체·크기)면 pyte `screen.dirty` 가 표시한 행만 다시 만들고 나머지는 직전
        캐시를 재사용한다(alt 풀리페인트에서 1줄만 바뀌어도 24행 전부 재직렬화하던
        낭비 제거). 스크롤/검색/리사이즈/alt전환/콜드캐시는 전체 경로로 폴백한다."""
        screen = self.screen
        cols, lines = screen.columns, screen.lines
        h = getattr(screen, "history", None)
        if self.scroll == 0:
            # 라이브 뷰(P3): 스크롤백 deque 전체 복사+concat 을 하지 않는다 —
            # 뷰포트 = 화면 버퍼 행 그대로. start(절대 인덱스 기준)는 스크롤백 길이로,
            # len(h.top) 은 O(1) 이라 복사 없이 구한다(검색 매치 절대인덱스 보존).
            window = [screen.buffer[y] for y in range(lines)]
            start = len(h.top) if h is not None else 0
        else:
            hist = list(h.top) if h is not None else []  # 대체 화면은 스크롤백 없음
            full = hist + [screen.buffer[y] for y in range(lines)]
            total = len(full)
            end = total - self.scroll
            start = end - lines
            if start < 0:
                start, end = 0, lines
            window = full[start:end]

        # 자동 줄바꿈(soft-wrap) 연속원 행을 프레임 상대 인덱스로 모은다(복사 시 한 줄
        # 잇기, serverio 가 screen 메시지 "wrap" 으로 클라에 그대로 전달). draw 가
        # 태그한 wrapped 줄 중, **현재 마지막 칸이 비어 있지 않은**(=여전히 꽉 찬) 줄만
        # 내보내 — 줄 끝이 지워지거나 당겨져 더는 wrap 이 아닌 stale 태그를 싸게
        # 무효화한다(빈 칸 default Char.data == " "; 와이드문자 stub 은 "" 라 꽉 참).
        self._last_top = start      # 뷰포트 첫 행의 절대 인덱스(클라 선택 좌표 환산용)
        cl = cols - 1
        self._last_wrap = [i for i, ln in enumerate(window)
                           if getattr(ln, "wrapped", False) and ln[cl].data != " "]
        # ConPTY 보강(휴리스틱): conhost 가 미리 접은 줄엔 wrapped 태그가 없다(위
        # _prewrap_heuristic 주석). 마지막 칸까지 꽉 찬 줄이고, 접힌 경계 양쪽 글자가
        # 둘 다 공백/박스문자(U+2500–259F: Claude 구분선 ─·테두리 │ 등)가 아니면
        # 연속원으로 추가한다 — 정확 신호가 원천 불가한 환경의 근사라 '정확히 폭에
        # 맞는 하드 줄 + 다음 줄이 글자로 시작'이면 드물게 오결합할 수 있다(수용).
        if self._prewrap_heuristic:
            exact = set(self._last_wrap)
            for i in range(len(window) - 1):
                if i in exact:
                    continue
                ln = window[i]
                last = ln[cl].data
                if last == "":               # 와이드 글자 stub — 실 글자는 앞 칸
                    last = ln[cl - 1].data
                nxt = window[i + 1][0].data
                if (last and last != " " and nxt and nxt != " "
                        and not (0x2500 <= ord(last[0]) < 0x25A0)
                        and not (0x2500 <= ord(nxt[0]) < 0x25A0)):
                    self._last_wrap.append(i)
            if len(self._last_wrap) > len(exact):
                self._last_wrap.sort()

        cursor = None
        if with_cursor and self.scroll == 0 and not screen.cursor.hidden:
            cursor = [screen.cursor.x, screen.cursor.y]

        sdirty = getattr(screen, "dirty", None)
        live = (self.scroll == 0 and self._match_abs is None
                and not self.search_query and len(window) == lines)
        cache_key = (cols, lines, id(screen))
        # 빠른 경로: dirty 행만 재직렬화하고 나머지는 캐시 재사용.
        if (live and sdirty is not None and self._row_cache is not None
                and self._row_cache_key == cache_key):
            rows = list(self._row_cache)
            for ry in list(sdirty):
                if 0 <= ry < lines:
                    rows[ry] = self._serialize_row(window[ry], cols)
            sdirty.clear()
            self._row_cache = rows
            return rows, cursor

        # 전체 경로(스크롤/검색/alt전환/리사이즈/콜드캐시).
        rows = []
        for ry, line in enumerate(window):
            segs = self._serialize_row(line, cols)
            # 검색 매치 라인 전체 하이라이트
            if self._match_abs is not None and (start + ry) == self._match_abs:
                segs = [[t, {**st, "rv": 1}] for t, st in segs]
            rows.append(segs)
        # 뷰포트가 화면보다 짧으면(스크롤 초기) 빈 줄로 채움
        while len(rows) < lines:
            rows.append([[" " * cols, {}]])
        # 다음 빠른 경로용 캐시는 라이브 뷰일 때만 둔다(그 외엔 무효화).
        if live:
            self._row_cache = list(rows)
            self._row_cache_key = cache_key
        else:
            self._row_cache = None
            self._row_cache_key = None
        if sdirty is not None:
            sdirty.clear()
        return rows, cursor

class Split:
    """내부 노드. 방향(lr/tb)과 비율로 두 자식을 분할."""

    def __init__(self, orient: str, a, b, ratio: float = 0.5):
        self.id = split_counter()
        self.orient = orient   # 'lr' = 좌우, 'tb' = 상하
        self.a = a
        self.b = b
        self.ratio = ratio
        self.rect = (0, 0, 0, 0)
        self.parent: Split | None = None

    def first_pane(self) -> Pane:
        return self.a.first_pane()


_pid_seq = [0]
_split_seq = [0]
_win_seq = [0]


def pid_counter() -> int:
    _pid_seq[0] += 1
    return _pid_seq[0]


def split_counter() -> int:
    _split_seq[0] += 1
    return _split_seq[0]


def window_counter() -> int:
    """탭(Tab)에 부여할 안정 window id(단조 증가). 위치값 index 와 달리 재할당되지
    않아 원격 detached_windows 키잉 등 '이 탭'을 계속 가리켜야 할 때 쓴다(M-1)."""
    _win_seq[0] += 1
    return _win_seq[0]


class Window:
    """탭에 종속된 단일 윈도우: 패널 집합(분할 트리)을 보유하는 렌더 영역.

    상위 컨테이너는 :class:`Tab` 이며(탭 1개 = 윈도우 1개), 탭이 이름/인덱스 등
    전환 단위 정보를 갖는다. 윈도우는 패널 트리와 줌/동기화/모니터 상태를 갖는다.
    """

    def __init__(self, root: Pane):
        self.root = root
        self._active = root    # 활성 패널(프로퍼티로 last-pane 추적)
        self._last = None      # 직전 활성 패널(prefix ;)
        self.zoomed = False    # 활성 패널 전체화면(prefix z)
        self.layout_idx = 0    # 레이아웃 프리셋 순환 인덱스
        self.sync = False      # 입력 동기화(synchronize-panes)
        self.border_status = False  # 패널 제목 경계선 표시(pane-border-status)
        self.auto_rename = True  # 활성 패널 명령으로 탭 이름 자동 갱신
        # 활동/벨 모니터 플래그(monitor_*/has_*)는 상위 Tab 이 보유한다.
        self._panes_cache = None  # panes() 캐시(§4.6 — 트리 변경 시 invalidate_panes)

    @property
    def active_pane(self):
        return self._active

    @active_pane.setter
    def active_pane(self, pane):
        if pane is not self._active:
            self._last = self._active
        self._active = pane

    def toggle_last_pane(self):
        if self._last is not None and self._last in self.panes():
            self.active_pane = self._last

    def invalidate_panes(self):
        """트리 구조 변경(분할/종료/이동/swap/rotate/프리셋) 직후 호출 — 다음
        panes() 가 재-DFS 하도록 캐시를 버린다(§4.6). 리프 집합 **또는 순서**가
        바뀔 수 있는 모든 트리 수술 뒤에 둬야 한다(swap/rotate 는 집합은 같아도
        순서가 바뀌고, swap_pane 등이 panes() 순서로 이웃을 계산하므로 중요)."""
        self._panes_cache = None

    def panes(self):
        # §4.6: 리프 패널 리스트를 캐시한다(트리는 split/kill/break/join/swap/rotate/
        # preset 때만 바뀌고 그때 invalidate_panes 로 무효화). flush 루프가 프레임마다
        # 여러 번(활성 창 + 전 창) 호출하던 트리 DFS 를 트리 변경 시로 줄인다. 반환
        # 리스트는 read-only 로 다뤄야 한다(호출부는 순회/index 만 — append/remove 금지).
        if self._panes_cache is None:
            out = []
            stack = [self.root]
            while stack:
                n = stack.pop()
                if isinstance(n, Pane):
                    out.append(n)
                else:
                    stack.append(n.a)
                    stack.append(n.b)
            self._panes_cache = out
        return self._panes_cache

    def pane_by_id(self, pid: int):
        for p in self.panes():
            if p.id == pid:
                return p
        return None

    # --- 레이아웃 ---
    def compute_layout(self, x, y, w, h):
        panes, divs = [], []
        if self.zoomed and isinstance(self.active_pane, Pane):
            # 줌: 활성 패널만 전체 영역을 차지하고 분할선/타 패널은 숨김
            self.active_pane.rect = (x, y, w, h)
            panes.append(self.active_pane)
            return panes, divs
        self._layout(self.root, x, y, w, h, panes, divs)
        return panes, divs

    def _layout(self, node, x, y, w, h, panes, divs):
        node.rect = (x, y, w, h)
        if isinstance(node, Pane):
            panes.append(node)
            return
        # 자식은 경계 셀을 공유(겹침)한다. 각 패널이 자기 테두리 박스를 그리므로
        # 경계 열/행을 양쪽 패널 테두리가 같은 셀로 공유한다(한 변당 최소 MIN).
        if node.orient == "lr":
            if w >= MIN_W * 2:
                bx = max(MIN_W, min(w - MIN_W, round((w - 1) * node.ratio)))
            else:
                bx = max(1, min(w - 1, (w - 1) // 2))
            divs.append({"split_id": node.id, "orient": "lr",
                         "x": x + bx, "y": y, "w": 1, "h": h,
                         "rect": [x, y, w, h]})
            self._layout(node.a, x, y, bx + 1, h, panes, divs)        # [x, x+bx]
            self._layout(node.b, x + bx, y, w - bx, h, panes, divs)   # [x+bx, x+w-1]
        else:
            if h >= MIN_H * 2:
                by = max(MIN_H, min(h - MIN_H, round((h - 1) * node.ratio)))
            else:
                by = max(1, min(h - 1, (h - 1) // 2))
            divs.append({"split_id": node.id, "orient": "tb",
                         "x": x, "y": y + by, "w": w, "h": 1,
                         "rect": [x, y, w, h]})
            self._layout(node.a, x, y, w, by + 1, panes, divs)        # [y, y+by]
            self._layout(node.b, x, y + by, w, h - by, panes, divs)   # [y+by, y+h-1]

    def split_by_id(self, sid: int):
        stack = [self.root]
        while stack:
            n = stack.pop()
            if isinstance(n, Split):
                if n.id == sid:
                    return n
                stack += [n.a, n.b]
        return None

    # --- 레이아웃 프리셋(select-layout) ---
    @staticmethod
    def _chain(nodes, orient):
        """노드들을 동일 비율의 orient 분할 사슬로 묶는다."""
        node = nodes[-1]
        for i in range(len(nodes) - 2, -1, -1):
            count = len(nodes) - i  # 이 서브트리의 잎/노드 수
            node = Split(orient, nodes[i], node, 1.0 / count)
        return node

    def _fix_parents(self, node, parent):
        node.parent = parent
        if isinstance(node, Split):
            self._fix_parents(node.a, node)
            self._fix_parents(node.b, node)

    def apply_preset(self, preset: str):
        leaves = self.panes()
        if not leaves:
            return
        self.zoomed = False
        self.invalidate_panes()  # 아래에서 self.root 재구성 → panes() 순서 변동
        if preset in ("even-horizontal", "even-h"):
            self.root = self._chain(leaves, "lr")
        elif preset in ("even-vertical", "even-v"):
            self.root = self._chain(leaves, "tb")
        elif preset == "main-vertical":
            main, rest = leaves[0], leaves[1:]
            self.root = (Split("lr", main, self._chain(rest, "tb"), 0.5)
                         if rest else main)
        elif preset == "main-horizontal":
            main, rest = leaves[0], leaves[1:]
            self.root = (Split("tb", main, self._chain(rest, "lr"), 0.5)
                         if rest else main)
        elif preset == "tiled":
            n = len(leaves)
            cols = int(n ** 0.5)
            if cols * cols < n:
                cols += 1
            rows = [leaves[i:i + cols] for i in range(0, n, cols)]
            row_nodes = [self._chain(r, "lr") for r in rows]
            self.root = self._chain(row_nodes, "tb")
        else:
            return
        self._fix_parents(self.root, None)


class Tab:
    """최상위 전환 단위. 정확히 하나의 :class:`Window` 를 종속으로 가진다.

    이름/인덱스(상태표시줄 탭)와 출력 활동/벨 표시를 보유한다. 새 탭을 만들면
    새 윈도우(단일 패널)가 생기고 이를 패널로 분할한다.
    """

    def __init__(self, index: int, name: str, window: "Window"):
        self.index = index
        # 안정 window id(단조 증가, 생성 시 1회 부여·이후 불변). `index` 는 _reindex 가
        # 탭 kill/move 마다 위치로 재할당하는 **위치값**이라 안정 식별자로 못 쓴다
        # (serverpersist 복원 주석도 명시). 원격 페더레이션의 단일-탭 분리
        # (serverremote.detached_windows)가 상류 탭 close/reorder 로도 안 어긋나게
        # 이 wid 로 키잉한다(코드검수 2026-07-10 M-1). 프로세스 수명 내 재사용 없음.
        self.wid = window_counter()
        self.name = name
        self.window = window
        self.has_activity = False
        self.has_bell = False
        self.has_claude_done = False   # 비활성 탭 Claude 작업 완료(busy→idle) 알림
        self.monitor_activity = False
        self.monitor_bell = True
        self.monitor_claude = True
        # 탭 고정(핀, 항목7): True 면 탭바 오른쪽 구역(항상 보임)에 모이고 실수 닫기에
        # 확인 한 단계가 붙는다. Session.tabs 불변식 "비고정 먼저, 고정 나중"을
        # servertree._normalize_pins 가 강제한다. 영속(재시작·세션유지) 양 경로 직렬화.
        self.pinned = False


class Session:
    def __init__(self, name: str, root: Pane):
        self.name = name
        self.created_at = time.time()
        self.tabs = [Tab(0, "win", Window(root))]
        self.active_index = 0
        self.last_index = 0    # 직전 활성 탭(prefix l)
        # M14 카운트다운 디바운스: 직전에 status 로 보낸 무장 자동액션 (kind, eta초).
        # flush 루프가 ETA 변동 때만 status 를 재전송하도록 비교에 쓴다(휘발성).
        self._pending_key = None
        # 라이브 PTY 팝업(display-popup): 트리에 속하지 않는 떠 있는 PTY 패널 1개.
        # None 이면 닫힌 상태. 열리면 {"pane", "title", "want_w", "want_h"} 를 담고,
        # 표시 geometry 는 매 레이아웃 계산 때 세션 크기에 맞춰 중앙 정렬로 산출한다.
        self.popup = None

    @classmethod
    def restored(cls, name: str, tabs: list, active_index: int = 0,
                 last_index: int = 0) -> "Session":
        """직렬화 복원용 생성자(__init__ 우회). 복원 경로(restore_layout·
        restore_resume_state)는 tabs 를 따로 만들어 넘기므로 __init__ 의 시그니처
        (root 1개)와 안 맞아 `__new__` 로 만든다 — 그때 __init__ 이 세팅하는 **휘발성
        속성을 빠짐없이 채워** 복원 세션이 새 세션과 동일한 속성 집합을 갖게 한다.
        과거 popup 누락이 모든 attach 를 깨뜨렸다(§10): _popup_layout 의 sess.popup
        에서 AttributeError → _send_full 실패 → 화면 일부만 그려진 채 끊김/브릭. 앞으로
        Session 에 휘발성 속성을 추가하면 **여기도 함께** 갱신할 것."""
        self = cls.__new__(cls)
        self.name = name
        self.created_at = time.time()
        self.tabs = tabs
        self.active_index = active_index
        self.last_index = last_index
        self._pending_key = None   # M14 카운트다운 디바운스(휘발성)
        self.popup = None
        return self

    @property
    def active_tab(self) -> Tab | None:
        if not self.tabs:
            return None
        self.active_index = max(0, min(self.active_index, len(self.tabs) - 1))
        return self.tabs[self.active_index]

    @property
    def active_window(self) -> Window | None:
        t = self.active_tab
        return t.window if t else None


class ClientConn:
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.session: Session | None = None
        self.cols = 80
        self.rows = 24
        # H-1: 이 클라 writer 로의 다중-프레임 송신(_send_full·flush write_frames)을
        # 직렬화해, 두 송신의 await(drain) 사이로 다른 송신 프레임이 끼어들어 stale
        # full 스냅샷이 newer delta 를 덮는 프레임 순서 역전을 막는다.
        self.write_lock = asyncio.Lock()
        # §1.7 페더레이션: 이 클라가 보는 원격 링크 이름(None=로컬). 설정 중엔 화면/
        # 레이아웃이 업스트림에서 전달되고 입력/스크롤/리사이즈가 릴레이된다(serverremote).
        self.remote_view: str | None = None
        # 클라가 hello 로 광고한 능력 집합(§10-11 P4). 광고한 것만 보낸다 — 이걸 안
        # 보내는 기존 클라에는 새 메시지가 한 바이트도 가지 않는다.
        self.caps: set = set()
        # B2 행 단위 델타: 이 클라에 마지막으로 보낸 패널별 rows 스냅샷
        # {pane_id -> rows}. 다음 프레임에 바뀐 행만 screen-delta 로 보낸다(클라마다
        # 자기 상태 기준이라 다중 클라·신규 attach 도 정합 — seq/resync 불필요).
        self._sent_rows: dict[int, list] = {}
        # 死-클라 회수(_liveness_loop)용. last_seen = 이 클라에서 마지막 메시지를
        # 받은 monotonic 시각(매 수신마다 갱신; 0=아직 없음). ever_pinged = ping 을
        # 한 번이라도 보낸 적 있나(=ping 켜진 클라) — ping 끈 클라는 무응답이어도
        # 회수 대상에서 제외해 오탐을 막는다.
        self.last_seen: float = 0.0
        self.ever_pinged: bool = False
        # 플러그인 **화면 상태**(설계 Tier C · P5). `{플러그인 화면 id -> dict}`.
        #
        # 왜 클라마다인가: `ncd` 의 지금 디렉터리, `mdir` 의 커서·태그 같은 것은 그 사람이
        # 보고 있는 판의 상태다. 서버 전역에 두면 두 클라가 같은 화면을 열었을 때 서로의
        # 커서를 옮긴다. 수명도 여기 매달아야 맞다 — **연결이 끊기면 함께 사라진다**
        # (서버가 떠난 클라의 판 상태를 영영 들고 있을 이유가 없다).
        # P3 이 같은 자리에 `"overlays"` 를 얹는다: `{플러그인 이름 -> {패널 id}}`.
        # 시계가 어느 패널에 떠 있는지도 "그 사람이 보고 있는 판"의 것이라 수명이 같다.
        self.plugin_state: dict = {}
        # 마지막으로 이 클라에 셀 기여(`plugin_cells`)를 보낸 monotonic 시각.
        # 0 = "다음 프레임에 무조건 보낸다"(오버레이를 켜거나 끈 직후 · 첫 프레임).
        self._cells_at: float = 0.0
        # 직전에 보낸 셀 기여 프레임의 내용 — **같으면 다시 안 보낸다**. 시계는 1초에
        # 한 번만 달라지는데 레이아웃이 바뀔 때마다 다시 만들므로, 이 비교가 없으면
        # 같은 그림이 30Hz 로 흘러간다.
        self._cells_last: tuple = ()
        # 직전에 만든 오버레이 기여(런·딤). **클릭존만 움직인 프레임**에도 프레임 하나는
        # 온전해야 해서 들고 있는다 — 클릭존은 패널 내용에서 나와 1초 주기를 못 기다리는데
        # (그 사이 누른 자리가 낡는다) 런까지 매 틱 다시 만들면 시계를 30Hz 로 다시
        # 그리게 된다. 그래서 런은 종전 주기로 만들고 여기서 꺼내 쓴다.
        self._cells_runs: list = []
        self._cells_dim: list = []
        # 그 런·딤을 만들 때의 **기하**(패널 사각형·활성 패널·격자 크기 —
        # `_cells_shape_key`). 지금 기하가 이것과 다르면 1초 주기를 안 기다리고 다시
        # 만든다: 런의 자리가 여기서 나오므로, 안 그러면 창을 키운 뒤 최대 1초 동안
        # 새 격자 위에 **옛 폭으로 잰 배지**가 얹힌다(pytmux-164).
        self._cells_shape: tuple | None = None
        # window-size=latest 용: 이 클라에서 마지막 **사용자 조작**(키 입력·붙여넣기·
        # 마우스·스크롤·리사이즈)이 온 monotonic 시각(0=아직 없음). _session_size 가
        # latest 모드에서 이 값이 가장 큰(가장 최근 조작된) 클라의 크기를 세션 공유
        # 크기로 쓴다. last_seen(ping 포함 아무 메시지나) 과 달리 사용자 조작만 센다 —
        # keepalive ping 만 오는 유휴 클라가 '최근 조작'으로 오인돼 크기를 뺏지 않게.
        self.last_active: float = 0.0
        # 이 클라(창·단말)가 지금 포커스를 갖고 있나(pytmux-421). 클라가 `focus` 프레임으로
        # 알린다. **기본이 True** 인 이유: 안 알리는 구클라를 「영원히 포커스 없음」으로
        # 두면, 그 클라만 붙어 있는 패널의 앱이 계속 blur 상태로 굴러 그림이 틀린다 —
        # 모르면 종전과 같이 구는 편이 안전하다.
        self.has_focus: bool = True

