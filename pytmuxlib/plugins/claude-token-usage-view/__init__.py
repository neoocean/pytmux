"""claude-token-usage-view 플러그인 — Claude 사용 한도 화면 + 다음 리셋 카운트다운.

명령 `usage-view [popup|tab|pane]`(별칭 token-viewer·usage-clock)로 연다. claude-code 가
숨은 /usage 스크랩으로 status 에 싣는 `usage_limits`(app.status.usage_limits)를 getattr
로 **부드럽게** 읽어, 코어 `usage_bar_lines` + `_CLOCK_FONT` 를 재사용해 한도 막대와
다음 리셋 카운트다운을 그린다. 새 네트워크·자격증명·의존성 없음(scripts/claude-token-
viewer 의 웹 API 경로는 쓰지 않는다). 설계 근거: docs/internal/USAGE_VIEW_DESIGN.md.

표시 3모드:
  * popup(기본) — token-log 의 '한도'(/usage) 탭을 연다(통합 2026-06-17; claude-code
                  없으면 중앙 모달 UsageScreen 폴백).
  * tab          — 풀스크린 화면(UsageScreen full). pytmux 서버 탭은 항상 fresh 셸이고
                   스크랩 데이터는 클라에 있어, '탭'은 서버 탭이 아니라 클라 풀스크린
                   화면이다(데이터 일관성·delete-to-disable; DESIGN §5.3).
  * pane         — 현재 패널 오버레이(client_overlay 훅, clock/calendar 패턴).

이 디렉토리를 통째로 지우면 usage-view 명령은 검색·자동완성·디스패치 어디에도 안 잡히고,
패널 오버레이도 client_overlay 훅이 빈 루프라 안 그려진다 — 코어는 무에러로 그대로
동작한다(delete-to-disable). claude-code 가 없어 usage_limits 가 없으면 화면/오버레이는
'데이터 없음' 안내만 보인다(플러그인끼리 하드 참조 금지, getattr 가드).

무게: 이 __init__ 은 textual/rich/datetime 을 최상단에서 import 하지 않는다(서버
프로세스도 plugins.load() 로 읽는다). 화면(screen)·오버레이(overlay)·시각 파서(reset)는
실제로 쓸 때 메서드 안에서 지연 import 한다."""
from __future__ import annotations

# 명령 메타데이터 — 코어 COMMANDS/COMPLETIONS/COMMAND_NOARG/PANE_SCOPED_CMDS 에 합쳐짐.
COMMANDS = [
    ("usage-view", "Claude 사용 한도 + 다음 리셋 카운트다운 화면 "
                   "(usage-view [popup|tab|pane], 기본 popup; 별칭 token-viewer)",
     "Claude"),
]
NOARG = {"usage-view", "token-viewer", "usage-clock"}
PANE_SCOPED = {"usage-view"}        # pane 모드 대상(활성) 패널을 프롬프트서 밝게 표시

_ALIASES = ("usage-view", "token-viewer", "usage-clock")
_MODES = ("popup", "tab", "pane")

# i18n(§6.1): 이 플러그인의 사용자 표면(명령 설명·UsageScreen·패널 오버레이)을 ko/en
# 으로. 영어 로케일에서 한국어로 새던 표면을 t() 경로로(uview.* 네임스페이스 — 코어
# claude-code 의 usage.* 와 충돌 회피). 플러그인 import 시 등록(delete-to-disable 일관).
from pytmuxlib import i18n  # noqa: E402

i18n.register({
    "ko": {
        "cmd.usage-view": "Claude 사용 한도 + 다음 리셋 카운트다운 화면 "
                          "(usage-view [popup|tab|pane], 기본 popup; 별칭 token-viewer)",
        "uview.title": "Claude 사용 한도 (/usage)",
        "uview.btn_refresh": "↻ 갱신 [u]",
        "uview.btn_toggle": "⤢ 팝업/탭 [t]",
        "uview.btn_pane": "▭ 패널 보기 [a]",
        "uview.no_data": "한도 데이터 없음 — Claude 패널에서 /usage 실행 후 [u]로 갱신",
        "uview.reset_unparsable": "(리셋 시각을 파싱할 수 없음)",
        "uview.next_reset": "다음 리셋: {label}",
        "uview.refreshing": "사용량 갱신 중… (숨은 /usage, ~수초)",
        "uview.overlay_no_data": "한도 데이터 없음 — Claude 패널에서 /usage 실행 후 갱신",
        "uview.overlay_next_reset": "다음 리셋까지 ",
    },
    "en": {
        "cmd.usage-view": "Claude usage limit + next-reset countdown screen "
                          "(usage-view [popup|tab|pane], default popup; alias token-viewer)",
        "uview.title": "Claude usage limit (/usage)",
        "uview.btn_refresh": "↻ Refresh [u]",
        "uview.btn_toggle": "⤢ Popup/Tab [t]",
        "uview.btn_pane": "▭ Pane view [a]",
        "uview.no_data": "No limit data — run /usage in a Claude pane, then [u] to refresh",
        "uview.reset_unparsable": "(cannot parse reset time)",
        "uview.next_reset": "Next reset: {label}",
        "uview.refreshing": "Refreshing usage… (hidden /usage, ~a few s)",
        "uview.overlay_no_data": "No limit data — run /usage in a Claude pane to refresh",
        "uview.overlay_next_reset": "Until next reset ",
    },
})


class _UsageViewPlugin:
    name = "claude-token-usage-view"
    description = "Claude 사용 한도 화면 + 다음 리셋 카운트다운"
    category = "Claude"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = {}
    pane_scoped = PANE_SCOPED

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """인스턴스 글루를 설치한다(clock 의 toggle_clock/clock_panes 패턴):
          * app.usage_view_panes — pane 모드 오버레이가 켜진 패널 id 집합.
          * app.open_usage_view(mode) — popup/tab 화면을 띄우거나 pane 오버레이 토글."""
        app.usage_view_panes = set()

        def open_usage_view(mode="popup"):
            if mode not in _MODES:
                mode = "popup"
            if mode == "pane":
                pid = app.layout.get("active")
                if pid is None:
                    return
                if pid in app.usage_view_panes:
                    app.usage_view_panes.discard(pid)
                else:
                    app.usage_view_panes.add(pid)
                app._composite()
                return
            if mode == "popup":
                # 통합(2026-06-17, 사용자 결정): popup 은 별도 모달 대신 token-log 의
                # '한도'(/usage) 탭을 활성인 채로 연다 — 한도 막대·리셋 카운트다운을 그
                # 통합 팝업이 보인다. claude-code 가 있으면 그리로, 없으면(델리트-투-
                # 디세이블) 기존 UsageScreen 으로 폴백한다(하드 참조 금지·getattr 가드).
                fn = getattr(app, "open_token_log", None)
                if fn is not None:
                    fn("limit")
                    return
            from .screen import UsageScreen
            app.push_screen(UsageScreen(full=(mode == "tab")))

        app.open_usage_view = open_usage_view

    def handle_command(self, app, c, args):
        if c not in _ALIASES:
            return False
        mode = args[0].lower() if args else "popup"
        if mode not in _MODES:
            mode = "popup"
        # 최신화 시도 — claude-code 의 server_command 가 처리(없으면 무응답·무에러).
        # 팝업/탭은 1초 틱이, 오버레이는 다음 합성이 갱신값을 자동 반영한다.
        app.send_cmd("refresh_usage")
        app.open_usage_view(mode)
        return True

    # ---- 클라이언트 오버레이 훅(pane 모드) — clock/calendar 와 동일 계약 ----
    def client_overlay(self, app, cells, W, H, active):
        if not getattr(app, "usage_view_panes", None):
            return
        from rich.style import Style
        from pytmuxlib.clientutil import theme_color
        from .overlay import draw_usage_overlay
        text_st = Style(color=theme_color(app, "foreground"))
        digit_st = Style(color=theme_color(app, "success"), bold=True)
        draw_usage_overlay(
            cells, app.layout.get("panes", []), app.usage_view_panes, W, H,
            text_st, digit_st,
            getattr(app.status, "usage_limits", None),
            age_sec=getattr(app.status, "usage_age_sec", None))

    def client_tick(self, app):
        """1초마다 오버레이가 떠 있으면 True(코어가 재합성 → 카운트다운 갱신)."""
        return bool(getattr(app, "usage_view_panes", None))

    def client_close_overlay(self, app, pane_id):
        """해당 패널의 usage 오버레이를 닫는다(Shift+ESC/패널 클릭). 닫았으면 True."""
        cp = getattr(app, "usage_view_panes", None)
        if cp and pane_id in cp:
            cp.discard(pane_id)
            return True
        return False


PLUGIN = _UsageViewPlugin()
