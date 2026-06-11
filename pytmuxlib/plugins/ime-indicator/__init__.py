"""ime-indicator 플러그인 — 화면 우상단에 현재 IME(한/영) 상태 배지(코드네임 ime-indicator).

기능 전체가 이 디렉토리 안에 있다. 디렉토리를 통째로 지우면 `ime-indicator` 명령은
검색·자동완성·디스패치 어디에도 잡히지 않고 배지도 사라진다 — 코어는 패널로 보낼 확정
입력 관찰을 `plugins.client_key` 훅으로, 배지 그리기를 `client_render` 훅으로만 닿고,
상태(app.ime_show/ime_state)는 attach_client 가 설치하며 코어는 직접 읽지 않는다
(오직 이 플러그인의 훅이 getattr 로 읽음).

한계(설계 — docs/IME_PREEDIT_CURSOR_SCENARIO.md): 호스트 터미널은 OS IME *언어*를 직접
질의할 수 없고, 조합(preedit) 문자열은 앱이 아니라 OS 가 하드웨어 커서 위치에 오버레이
한다 — 즉 앱에는 **확정된(committed) 글자만** 키 이벤트로 도착한다. 그래서 한/영은 패널로
보내는 확정 입력 문자의 스크립트로 **추정**한다: 한글(자모/완성형)이면 '한', ASCII 글자면
'EN', 숫자·기호·공백·제어키 등 **모드 중립** 입력은 직전 상태를 유지한다. 따라서 한글
모드에서 영문 ASCII 만 치면 'EN' 으로 보이는 휴리스틱 한계가 있다(조합 중 여부 자체는
앱이 관찰할 수 없으므로 '최근 확정 입력의 스크립트'가 최선의 신호다).

무게: 이 __init__ 은 textual/rich 를 모듈 최상단에서 import 하지 않는다(서버 프로세스도
plugins.load() 로 같은 코드를 읽는다). 렌더 헬퍼/Style/테마는 client_render 에서 실제로
그릴 때 지연 import 한다. has_hangul 은 textual 비의존이라 최상단 import 해도 안전하다."""
from __future__ import annotations

from pytmuxlib.clientutil import has_hangul

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("ime-indicator", "화면 우상단 IME(한/영) 상태 배지 표시 토글", "설정/기타"),
]
NOARG = {"ime-indicator", "ime"}


class _ImeIndicatorPlugin:
    name = "ime-indicator"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 추가 옵션 템플릿 없음(명령 이름은 레지스트리가 자동 추가)
    command_options = {}
    pane_scoped = set()         # 화면 전역 배지라 패널 한정 아님

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """app 인스턴스에 배지 상태를 설치한다(코어는 이 attr 들을 직접 읽지 않는다 —
        오직 이 플러그인의 client_render/client_key 훅이 getattr 로 읽는다). 기본 ON,
        초기 상태는 'EN'(한글을 한 번이라도 확정 입력하면 '한' 으로 전환)."""
        app.ime_show = True
        app.ime_state = "EN"
        # 배지가 첫 행에 차지한 칸 범위 (x0, x_end, y=0) 또는 None(미표시). 활성 패널
        # 테두리 강조 검사가 [x](_tab_close_zone)처럼 이 구간을 예외로 둔다 — 배지는
        # 의도된 상단 테두리 오버레이라 그 칸이 파랑이 아닌 게 정상이다.
        app._ime_zone = None

    def handle_command(self, app, c, args):
        if c in ("ime-indicator", "ime"):
            app.ime_show = not getattr(app, "ime_show", True)
            app._composite()
            fn = getattr(app, "display_message", None)
            if fn:
                fn("IME 인디케이터 " + ("ON" if app.ime_show else "OFF"))
            return True
        return False

    # ---- 클라이언트 런타임 훅 ----
    def client_key(self, app, event):
        """normal 모드에서 패널로 보낼 확정 키 입력 1건을 관찰해 한/영 상태를 추정한다.
        한글이면 '한', ASCII 글자(a-z/A-Z)면 'EN'; 숫자·기호·공백·제어키(문자 없음/비인쇄)는
        한·영 공통이라 **모드 중립**으로 두어 직전 상태를 유지한다(예: 한글 모드에서 숫자만
        쳐도 '한' 이 깜빡여 'EN' 으로 바뀌지 않는다). 상태가 바뀌고 배지가 켜져 있으면
        재합성해 배지를 갱신한다."""
        ch = getattr(event, "character", None)
        if not ch or not ch.isprintable():
            return
        if has_hangul(ch):
            new = "한"
        elif ch.isascii() and any(c.isalpha() for c in ch):
            new = "EN"
        else:
            return                       # 모드 중립 — 상태 유지
        if new != getattr(app, "ime_state", "EN"):
            app.ime_state = new
            if getattr(app, "ime_show", False):
                app._composite()

    def client_render(self, app, cells, W, H):
        """배지가 켜져 있으면 **커서가 있는 줄의 오른쪽 끝**에 `[한]`/`[EN]` 을 그린다
        (2026-06-11 사용자 요청 — 우상단 고정에서 변경: preedit 이 보이는 커서 줄과
        같은 높이라 시선 이동 없이 확인). 커서 좌표는 코어 _composite 가 이 훅 **앞**
        에서 채우는 `_active_cursor_xy`(IME preedit 하드웨어 커서 동기화와 같은 원천)
        를 읽고, 없으면(활성 패널 커서 미상) 종전처럼 첫 행(y=0) 폴백. y=0 일 때만
        탭 닫기 [x] 회피로 우측 4칸을 비운다(다른 행엔 [x] 가 없어 진짜 오른쪽 끝).
        '한'=success 색, 'EN'=primary 색 배경의 검은 글자(테마 해석은 호출 시점)."""
        if not getattr(app, "ime_show", False):
            app._ime_zone = None
            return
        from rich.style import Style
        from pytmuxlib.clientutil import theme_color
        from .render import draw_ime_indicator
        state = getattr(app, "ime_state", "EN")
        color = "success" if state == "한" else "primary"
        st = Style(color="black", bgcolor=theme_color(app, color), bold=True)
        cxy = getattr(app, "_active_cursor_xy", None)
        y = cxy[1] if cxy else 0
        # 탭 닫기 [x] 와 같은 행이면 우측 4칸 회피(이 훅 뒤에 그려져 배지를 덮는다).
        # [x] 행은 콘텐츠 우상단이라 테두리 유무에 따라 변한다(무테 0행·유테 1행·헤더
        # 행 등) — 전 프레임의 _tab_close_zone 행으로 판정(프레임 간 안정, 첫 프레임
        # 미상이면 0행 가정 = 종전 동작).
        tz = getattr(app, "_tab_close_zone", None)
        xrow = tz[2] if tz else 0
        span = draw_ime_indicator(cells, W, H, state, st, y=y,
                                  reserve_right=4 if y == xrow else 0)
        # 그린 칸 범위를 노출(테두리 강조 테스트의 [x] 동급 예외). 폭 부족 시 None.
        app._ime_zone = (span[0], span[1], y) if span else None


PLUGIN = _ImeIndicatorPlugin()
