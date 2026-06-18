"""ime-indicator 플러그인 — 화면 우상단에 현재 IME(한/영) 상태 배지(코드네임 ime-indicator).

기능 전체가 이 디렉토리 안에 있다. 디렉토리를 통째로 지우면 `ime-indicator` 명령은
검색·자동완성·디스패치 어디에도 잡히지 않고 배지도 사라진다 — 코어는 패널로 보낼 확정
입력 관찰을 `plugins.client_key` 훅으로, 배지 그리기를 `client_render` 훅으로만 닿고,
상태(app.ime_show/ime_state)는 attach_client 가 설치하며 코어는 직접 읽지 않는다
(오직 이 플러그인의 훅이 getattr 로 읽음).

상태 원천 2계층(§10-B 2026-06-11, docs/internal/IME_INSTANT_STATE_SCENARIO.md):
① **OS 실측(macOS·Windows)** — 가능하면 이것이 권위값(_ime_os). 한/영 키로 모드만
   바꿔도 **입력 없이 즉시** 배지가 따라온다.
   - **Windows**: 포그라운드 창의 IME 변환모드를 `WM_IME_CONTROL` 로 인프로세스
     질의(IME_CMODE_NATIVE=한글), 0.05초 폴링(첫 client_tick 에서 지연 설치).
   - **macOS**: HIToolbox TIS 는 장수명 프로세스에서 freeze 되므로(asyncio 만 돌고
     CFRunLoop 미가동 → 변경 알림 미수신, 2026-06-17 확정) 인프로세스 폴링이 안 된다.
     대신 첫 client_tick 에서 **감시 헬퍼 자식 프로세스**(`oskbd.spawn_watcher`)를
     띄워, 그 헬퍼가 진짜 CFRunLoop 으로 입력소스 변경 알림을 받아 흘리는 줄을
     0.05초마다 비차단 드레인한다(client_unload 에서 거둠). 상세는 oskbd.py docstring.
② **확정 입력 휴리스틱(폴백)** — 조합(preedit) 문자열은 앱이 아니라 OS 가 하드웨어
   커서 위치에 오버레이한다(docs/internal/IME_PREEDIT_CURSOR_SCENARIO.md). 앱에는 확정된
   글자만 도착하므로, OS 질의가 불가한 환경(ssh 원격 클라·리눅스·질의 실패)에선
   확정 입력 문자의 스크립트로 추정한다: 한글이면 '한', ASCII 글자면 'EN',
   숫자·기호·공백·제어키 등 모드 중립 입력은 직전 상태 유지. 이 경로엔 한글
   모드에서 영문만 치면 'EN' 으로 보이는 휴리스틱 한계가 그대로 남는다.

무게: 이 __init__ 은 textual/rich 를 모듈 최상단에서 import 하지 않는다(서버 프로세스도
plugins.load() 로 같은 코드를 읽는다). 렌더 헬퍼/Style/테마는 client_render 에서 실제로
그릴 때 지연 import 한다. has_hangul 은 textual 비의존이라 최상단 import 해도 안전하다."""
from __future__ import annotations

import os

from pytmuxlib.clientutil import has_hangul

from . import oskbd


def _is_ssh_remote() -> bool:
    """클라이언트(Textual 앱)가 plain ssh 원격 세션에서 도는지(SSH_CONNECTION/SSH_TTY,
    clientwidgets._is_remote 와 동일 신호). 이 경우 로컬 OS IME 질의(oskbd)는 클라가 도는
    **원격 박스**의 키보드를 보게 돼, 사용자가 실제 타이핑하는 **로컬 머신**의 한/영과
    무관하다(원격 macOS 면 TIS 가 있어 EN 등으로 굳음). 그래서 OS 실측을 끄고 확정 입력
    휴리스틱으로 폴백한다(§9.1). pytmux 네이티브 remote-attach(클라=로컬)는 클라 env 에
    SSH_CONNECTION 이 없어 영향받지 않는다(로컬 OS 실측 그대로)."""
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("ime-indicator", "화면 우상단 IME(한/영) 상태 배지 표시 토글", "설정/기타"),
]
NOARG = {"ime-indicator", "ime"}


class _ImeIndicatorPlugin:
    name = "ime-indicator"
    description = "IME 한/영 상태 배지(화면 우상단)"
    category = "입력"
    commands = COMMANDS
    noarg = NOARG
    completions = []            # 추가 옵션 템플릿 없음(명령 이름은 레지스트리가 자동 추가)
    command_options = {}
    pane_scoped = set()         # 화면 전역 배지라 패널 한정 아님

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """app 인스턴스에 배지 상태를 설치한다(코어는 이 attr 들을 직접 읽지 않는다 —
        오직 이 플러그인의 client_render/client_key/client_tick 훅이 getattr 로
        읽는다). 기본 ON. §10-B: OS 입력소스 질의(macOS TIS)가 가능하면 그 실측이
        초기·이후 상태의 권위값(_ime_os=True, 폴링 타이머는 첫 client_tick 에서
        지연 설치 — 이 시점엔 앱이 아직 안 돌아 set_interval 불가). 불가하면 'EN'
        에서 시작해 확정 입력 휴리스틱으로 추정한다."""
        app.ime_show = True
        # §9.1: plain ssh 원격이면 로컬 OS 질의는 원격 박스 키보드를 봐 틀리므로 끄고
        # 확정 입력 휴리스틱으로 폴백한다(_is_ssh_remote docstring 참조).
        sid = None if _is_ssh_remote() else oskbd.current_source_id()
        app._ime_os = sid is not None
        app._ime_os_timer = None
        # macOS 입력소스 변경 감시 헬퍼 자식 프로세스(첫 client_tick 에서 지연 기동)와
        # 그 stdout 누적 버퍼. macOS 는 장수명 프로세스에서 TIS 가 freeze 되므로
        # 인프로세스 폴링 대신 이 헬퍼가 흘리는 줄을 읽는다(oskbd 모듈 docstring).
        # Windows·폴백 경로에선 헬퍼가 없어(None) 종전 인프로세스 질의를 쓴다.
        app._ime_watch = None
        app._ime_buf = b""
        app.ime_state = ("한" if oskbd.is_korean(sid) else "EN") \
            if app._ime_os else "EN"
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
    def client_tick(self, app):
        """코어 1초 틱: OS 입력소스 질의가 가능한 클라(macOS 로컬)면 첫 틱에서
        0.05초 전용 폴링 타이머를 지연 설치하고(attach_client 시점엔 앱이 아직 안
        돌아 set_interval 불가), 틱 자체에서도 한 번 폴링한다(타이머 설치 실패
        환경에서도 1초 해상도는 보장). 50ms 폴링이라 한/영 전환이 체감 즉시(<50ms)
        배지에 반영되며, TIS 질의는 ~1µs/회(초당 ~27µs)라 비용은 사실상 무시 가능.
        상태 무변화 시 _poll 은 질의 1회만 하고 재합성하지 않는다. 재합성은 _poll 이
        상태 변화 시 직접 하므로 코어 일괄 재합성은 항상 불필요(False)."""
        if not getattr(app, "_ime_os", False):
            return False
        if getattr(app, "_ime_os_timer", None) is None:
            # macOS: 변경 감시 헬퍼 1회 기동(비 macOS·실패 시 None → 인프로세스 폴링).
            app._ime_watch = oskbd.spawn_watcher()
            si = getattr(app, "set_interval", None)
            app._ime_os_timer = (si(0.05, lambda: self._poll(app))
                                 if si else False)
        self._poll(app)
        return False

    def _poll(self, app):
        """입력소스 상태를 갱신한다. macOS(감시 헬퍼 가동 시)는 헬퍼 stdout 에서 최신
        줄을 비차단 드레인하고, 그 외(Windows·폴백)는 인프로세스 TIS/IMM 을 1회
        질의한다. 새 정보 없음/일시 실패는 직전 상태 유지(깜빡임 방지). _ime_os 가드:
        타이머 설치 후 실측을 끈 경우(테스트의 폴백 강제 등) 잔존 타이머가 상태를
        덮지 않게."""
        if not getattr(app, "_ime_os", False):
            return
        watch = getattr(app, "_ime_watch", None)
        if watch is not None:
            if watch.poll() is not None:       # 헬퍼 종료 → 직전 상태 유지
                return
            sid, app._ime_buf = oskbd.read_latest(
                watch, getattr(app, "_ime_buf", b""))
            if sid is None:                    # 새 변경 줄 없음
                return
        else:
            sid = oskbd.current_source_id()
            if sid is None:
                return
        new = "한" if oskbd.is_korean(sid) else "EN"
        if new != getattr(app, "ime_state", "EN"):
            app.ime_state = new
            if getattr(app, "ime_show", False):
                app._composite()

    def client_unload(self, app):
        """클라이언트 종료 시 감시 헬퍼 자식 프로세스를 정리한다(attach_client 의 짝).
        헬퍼는 stdin EOF 로도 자가 종료하지만, 정상 종료 경로에선 즉시 거둔다."""
        watch = getattr(app, "_ime_watch", None)
        if watch is None:
            return
        app._ime_watch = None
        try:
            watch.terminate()
        except Exception:
            pass

    def client_key(self, app, event):
        """normal 모드에서 패널로 보낼 확정 키 입력 1건을 관찰해 한/영 상태를 추정한다
        (**폴백 경로** — OS 실측(_ime_os)이 가능하면 그쪽이 권위값이라 여기선 아무
        것도 안 한다: 한글 모드에서 영문을 치는 순간 'EN' 으로 오판하던 휴리스틱
        한계가 실측에 역류하지 않게). 한글이면 '한', ASCII 글자(a-z/A-Z)면 'EN';
        숫자·기호·공백·제어키(문자 없음/비인쇄)는 한·영 공통이라 **모드 중립**으로
        두어 직전 상태를 유지한다(예: 한글 모드에서 숫자만 쳐도 '한' 이 깜빡여
        'EN' 으로 바뀌지 않는다). 상태가 바뀌고 배지가 켜져 있으면 재합성한다."""
        if getattr(app, "_ime_os", False):
            return
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
        """배지가 켜져 있으면 **커서가 있는 줄에서 활성 패널의 오른쪽 끝**에
        `[한]`/`[EN]` 을 그린다(2026-06-11 커서 줄로 변경 → 2026-06-16 활성 패널
        우측 끝으로 변경: 좌우 분할에서 활성 패널이 화면 왼쪽 절반인데 배지가 화면
        오른쪽(비활성 패널 위)에 떠 헷갈리던 문제 해결). 커서 좌표는 코어 _composite
        가 이 훅 **앞**에서 채우는 `_active_cursor_xy`(IME preedit 하드웨어 커서 동기화와
        같은 원천)를, 활성 패널 우측 경계는 `_active_pane_right`(같은 _composite 가
        채움)를 읽는다. 둘 다 없으면(활성 패널 미상) 종전처럼 커서 행 없으면 y=0,
        경계 없으면 화면 폭 W 로 폴백. 탭 닫기 [x] 와 같은 행이면 우측 4칸을 비운다
        ([x] 는 활성 패널 우상단이라 같은 우측 경계를 공유). '한'=success 색,
        'EN'=primary 색 배경의 검은 글자(테마 해석은 호출 시점)."""
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
        # 활성 패널 오른쪽 경계(exclusive). 미상이면 화면 폭 W(=종전 전체폭 동작).
        x_right = getattr(app, "_active_pane_right", None)
        # 탭 닫기 [x] 와 같은 행이면 우측 4칸 회피(이 훅 뒤에 그려져 배지를 덮는다).
        # [x] 행은 콘텐츠 우상단이라 테두리 유무에 따라 변한다(무테 0행·유테 1행·헤더
        # 행 등) — 전 프레임의 _tab_close_zone 행으로 판정(프레임 간 안정, 첫 프레임
        # 미상이면 0행 가정 = 종전 동작).
        tz = getattr(app, "_tab_close_zone", None)
        xrow = tz[2] if tz else 0
        span = draw_ime_indicator(cells, W, H, state, st, y=y,
                                  reserve_right=4 if y == xrow else 0,
                                  x_right=x_right)
        # 그린 칸 범위를 노출(테두리 강조 테스트의 [x] 동급 예외). 폭 부족 시 None.
        app._ime_zone = (span[0], span[1], y) if span else None


PLUGIN = _ImeIndicatorPlugin()
