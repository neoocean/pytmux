"""claude-code 플러그인의 모달 화면 — 시작 규칙 편집(RulesEditScreen)·토큰 절감
설정(ClaudeSaverScreen). client.py 의 clientscreens 에서 이리로 이전.

textual 의존이 있어 이 모듈은 **실제로 팝업을 열 때** 지연 import 된다(플러그인 __init__
은 가벼움 — 서버 프로세스도 plugins.load() 로 읽기 때문)."""
from __future__ import annotations

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.strip import Strip
from textual.widgets import (DataTable, Input, Label, ListItem, ListView,
                             Static, TextArea)

from rich.text import Text
from rich.segment import Segment
from rich.style import Style

from pytmuxlib import i18n
from pytmuxlib.clientutil import theme_color
from . import SAVER_ROWS

# §6 ⑤ 플러그인 설정/로그 모달 문자열(token-saver·rules·model·perm·token-log). 정적 문자열은
# 키=원문 한국어(gettext 식 — 렌더가 t(원문) 로 단순 조회), 포맷 문자열만 pscreen.* semantic
# 키. SAVER_ROWS 라벨(__init__.py)도 여기서 en 보강(ko 자동 시드). 미등록은 원문 폴백.
i18n.register({
    "ko": {s: s for s in (
        # 공통/버튼/힌트
        "Enter 토글/순환 · ESC 닫기", "저장", "취소", "  ◀ 현재",
        # rules·model
        "Claude 시작 규칙 — Ctrl+S 저장 · Esc 취소", "기본", "모델", "컨텍스트",
        "모델·컨텍스트 변경 · ←→ 값 · Enter 적용 · Esc",
        # perm 모드
        "auto — 모든 동작 자동 수락, 안전검사 (⏵⏵ auto mode)",
        "accept — 편집·기본 FS 만 자동 수락 (⏵⏵ accept edits)",
        "default — 매번 확인 (일반 모드)",
        "plan — 플랜 모드 (계획만, 실행 안 함)",
        "bypass — 권한 우회, 확인 없음 ⚠️ (Bypass Permission Mode)",
        # token-log: 탭(넓은/좁은)·그룹·컬럼·차원·정렬
        "시간", "시", "일", "주", "월", "계정", "계", "패널", "패", "세", "정렬", "정",
        "시나리오", "비교", "한도", "경고", "경", "기간", "기", "보기", "조회", "토큰 사용량",
        "구간", "실측(세션 5h)", "추정Σ", "항목", "토큰", "비율",
        "세션", "타임스탬프", "토큰순", "시간순", "옵션",
        # token-log: 안내/대사
        "한도(/usage): [u] 눌러 조회", "(기록된 토큰 사용량이 없습니다)",
        "토큰 비교 · 실측Δ% vs 추정Σ", "/usage 조회 중… (~수초)",
        "r집계로 돌아가기 · u/usage 갱신 · Esc닫기",
        # [대사] 시간축 그래프(2026-06-20)
        "토큰 5h 사용률 추이 · 시간축",
        "r집계 표 · ←→/PgUp·PgDn/Home·End 스크롤 · u/usage · Esc닫기",
    )},
    "en": {
        "Enter 토글/순환 · ESC 닫기": "Enter toggle/cycle · ESC close",
        "저장": "Save", "취소": "Cancel", "  ◀ 현재": "  ◀ current",
        "Claude 시작 규칙 — Ctrl+S 저장 · Esc 취소":
            "Claude start rules — Ctrl+S save · Esc cancel",
        "기본": "Default", "모델": "Model", "컨텍스트": "Context",
        "모델·컨텍스트 변경 · ←→ 값 · Enter 적용 · Esc":
            "Model·context change · ←→ value · Enter apply · Esc",
        "auto — 모든 동작 자동 수락, 안전검사 (⏵⏵ auto mode)":
            "auto — auto-accept all, safety checks (⏵⏵ auto mode)",
        "accept — 편집·기본 FS 만 자동 수락 (⏵⏵ accept edits)":
            "accept — auto-accept edits·basic FS only (⏵⏵ accept edits)",
        "default — 매번 확인 (일반 모드)": "default — confirm each time (normal)",
        "plan — 플랜 모드 (계획만, 실행 안 함)": "plan — plan mode (plan only, no run)",
        "bypass — 권한 우회, 확인 없음 ⚠️ (Bypass Permission Mode)":
            "bypass — skip permissions, no confirm ⚠️ (Bypass Permission Mode)",
        "시간": "Time", "시": "T", "일": "Day", "주": "Week", "월": "Month",
        "계정": "Account", "계": "A", "패널": "Panel", "패": "P", "세": "S",
        "정렬": "Sort", "정": "S", "시나리오": "Scenario", "비교": "Recon",
        "한도": "Limit", "경고": "Warn", "경": "W",
        "기간": "Period", "기": "P",
        "보기": "View", "조회": "Query", "토큰 사용량": "Token usage",
        "구간": "Span", "실측(세션 5h)": "Measured (session 5h)", "추정Σ": "Est Σ",
        "항목": "Item", "토큰": "Tokens", "비율": "Ratio",
        "세션": "Session", "타임스탬프": "Timestamp",
        "토큰순": "by tokens", "시간순": "by time",
        "옵션": "Options",
        "한도(/usage): [u] 눌러 조회": "Limit (/usage): press [u] to query",
        "(기록된 토큰 사용량이 없습니다)": "(no recorded token usage)",
        "토큰 비교 · 실측Δ% vs 추정Σ": "Token reconcile · measured Δ% vs est Σ",
        "/usage 조회 중… (~수초)": "querying /usage… (~a few s)",
        "r집계로 돌아가기 · u/usage 갱신 · Esc닫기":
            "r back to totals · u refresh /usage · Esc close",
        "토큰 5h 사용률 추이 · 시간축":
            "Token 5h usage trend · time axis",
        "r집계 표 · ←→/PgUp·PgDn/Home·End 스크롤 · u/usage · Esc닫기":
            "r table · ←→/PgUp·PgDn/Home·End scroll · u /usage · Esc close",
    },
})
# token-saver 행 라벨(SAVER_ROWS, __init__.py). ko 자동 시드, en 보강.
i18n.register({
    "ko": {r[1]: r[1] for r in SAVER_ROWS},
    "en": {
        "토큰리밋 자동재개": "Token-limit auto-resume",
        "실측 세션한도 게이트(자동재개 보류 %)":
            "Measured session-limit gate (auto-resume hold %)",
        "실측 주간한도 게이트(%)": "Measured weekly-limit gate (%)",
        "실측 한도 압박(게이트의 80%) 시 plan 모드 유도":
            "Induce plan mode under limit pressure (80% of gate)",
        "컨텍스트 잔량 부족 시 자동 정리": "Auto-clean when context runs low",
        "  └ 정리 방식": "  └ clean method", "  └ 잔량 임계": "  └ remaining threshold",
        "  └ 정리 빈도 상한": "  └ clean frequency cap",
        "idle 지속 시 자동 문서화+/clear": "Auto document+/clear when idle persists",
        "idle 지속 시 자동 /compact": "Auto /compact when idle persists",
        "컨텍스트 하드스톱 시 즉시 자동 /compact":
            "Auto /compact immediately on context hardstop",
        "세션 종료 시 토큰 사용량 화면 자동 표시":
            "Auto-open token usage screen when session ends",
        "권한모드 자동 오토": "Auto-switch permission mode to auto",
        "프롬프트 단위 클리어(완료마다 doc+/clear)":
            "Per-prompt clear (doc+/clear each completion)",
        "장기 턴 경고(초)": "Long-turn warning (sec)",
        "반복 루프 경고(회)": "Repeat-loop warning (count)",
        "모델 과선택 힌트(Opus 반복+여유 시 가벼운 모델 제안)":
            "Model over-selection hint (suggest a lighter model when Opus repeats with headroom)",
    },
})
# 포맷 문자열(동적 인자 포함)은 semantic 키.
i18n.register({
    "ko": {
        "pscreen.perm_title": "권한모드 선택 (현재: {current})",
        "pscreen.tklog_title2": "토큰 사용량(추정) · {what}별",
        "pscreen.tklog_scope": "{order} · {sigma}",
        "pscreen.tklog_disp": " (표시 {n})",
        "pscreen.tklog_hint": "↑↓ 이동 · Enter/←→ 펼침·접힘 · p세션 o정렬 · l한도 r비교 u/usage · Esc닫기",
        # 계층 타임라인 트리 구역 구분선(2026-06-21).
        "pscreen.tree_earlier_weeks": "── 이번 달 이전 주 ──",
        "pscreen.tree_earlier_months": "── 이전 달 ──",
        "pscreen.weekdays": "월,화,수,목,금,토,일",
        "pscreen.hour_suffix": "시",
        "pscreen.recon_top": "실측(/usage Δ%)과 추정(스크랩 ~Σ)은 의미가 다른 두 출처 — 절대 일치가 아니라 추세 상관을 봅니다",
        "pscreen.recon_empty": "(비교할 실측 스냅샷 구간이 없습니다 — /usage 실측이 2회 이상 쌓이면 생깁니다)",
        "pscreen.recon_chart_top": "{rng} · 최신 {pct}% · 구간 {n}개 (막대=세션 5h% 실측, ←→ 스크롤)",
        "pscreen.win_session": "이번 5h창 ~Σ{tok}(리셋 {left} 후)",
        "pscreen.win_week": "이번 주 ~Σ{tok}(리셋 {left} 후)",
        # 한도 전용 서브뷰(상단 7줄 블록을 표 자리로 이동 — 작은 화면 정리).
        "pscreen.tklog_limit_title": "토큰 사용량 · 한도(/usage)",
        "pscreen.tklog_limit_col": "한도(/usage)",
        "pscreen.tklog_limit_hint": "l집계로 돌아가기 · u/usage 갱신 · Esc닫기",
        "pscreen.tklog_limit_empty": "한도(/usage) 미조회 — [u] 눌러 조회",
        "pscreen.next_reset": "다음 리셋: {label}",
        "pscreen.reset_session": "세션 5h",
        "pscreen.reset_week": "주 전체",
        # 경고(장기 턴/반복/포맷) 탭 — 상태줄 ⚠ 배지 클릭이 여는 통합 탭.
        "pscreen.tklog_warn_col": "Claude 경고",
        "pscreen.tklog_warn_title": "Claude 경고",
        "pscreen.tklog_warn_empty": "현재 표시할 Claude 경고가 없습니다(이미 해소됨).",
        "pscreen.tklog_warn_hint": "다른 탭으로 이동 · u/usage 갱신 · Esc닫기",
        # 상단 1줄 한도 요약(상세는 한도 탭).
        "pscreen.lim_5h": "5h {p}%",
        "pscreen.lim_wk": "주 {p}%",
        "pscreen.left_hm": "{h}시간{m}분",
        "pscreen.left_m": "{m}분",
        "pscreen.left_d": "{d}일{h}시간",
        # 5h%/1w% 칼럼 제목에 inline 으로 붙는 리셋 잔여시간(요청 2026-06-20,
        # 옛 footer 줄 대체). {left}=_fmt_left_short('87m'·'6d' 등 단일 단위).
        "pscreen.hdr_5h": "5h% ({left} 후)",
        "pscreen.hdr_1w": "1w% ({left} 후)",
        # [경고] 탭 본문(종류별 상황·할일) — 옛 한글 하드코딩을 i18n 으로(전수조사
        # 2026-06-19). 본문은 줄바꿈(\n)으로 묶고 _warn_info_text 가 split 한다.
        "claude.warn_fmt_title": "Claude 포맷 미인식",
        "claude.warn_fmt_body":
            "[상황]\n"
            "• pytmux 가 Claude Code 화면 형식을 인식하지 못합니다.\n"
            "• 토큰/사용량 추적과 자동화(자동 재개·자동 압축·한도 게이트)가 멈춥니다.\n"
            "• Claude Code 자체 동작(입력·출력)에는 영향이 없습니다.\n"
            "• 보통 Claude Code 버전 업데이트로 화면 구조가 바뀌면 발생합니다.\n"
            "\n"
            "[할일]\n"
            "• 화면이 다시 정상 인식되면 경고는 자동으로 사라집니다(잠시 대기).\n"
            "• 계속되면 pytmux 의 Claude 파서(claude.py)를 새 포맷에 맞춰 갱신해야 합니다.\n"
            "• REC 캡처가 켜져 있으면 captures/ 로그로 새 포맷을 분석할 수 있습니다.",
        "claude.warn_repeat_title": "Claude 반복 루프 의심",
        "claude.warn_repeat_body":
            "[상황]\n"
            "• 같은 출력이 여러 번 반복됐습니다 — 루프 의심(경고만, 자동 개입 없음).\n"
            "\n"
            "[할일]\n"
            "• 진행이 없으면 다른 지시를 주거나 /clear 후 다시 시도하세요.\n"
            "• 임계는 옵션 claude_repeat_alert 로 조정/끌 수 있습니다(0=끔).",
        "claude.warn_long_title": "Claude 장기 턴",
        "claude.warn_long_body":
            "[상황]\n"
            "• 현재 Claude 턴이 임계 시간을 넘겨 오래 진행 중입니다(경고만, 자동 개입 없음).\n"
            "\n"
            "[할일]\n"
            "• 정상적인 긴 작업일 수 있습니다. 멈춘 듯하면 패널에서 Esc 로 중단하세요.\n"
            "• 임계는 옵션 claude_long_turn_sec 로 조정/끌 수 있습니다(0=끔).",
    },
    "en": {
        "pscreen.perm_title": "Select permission mode (current: {current})",
        "pscreen.tklog_title2": "Token usage (est) · by {what}",
        "pscreen.tklog_scope": "{order} · {sigma}",
        "pscreen.tklog_disp": " (shown {n})",
        "pscreen.tklog_hint": "↑↓ move · Enter/←→ expand·collapse · p session o sort · l limit r recon u /usage · Esc close",
        "pscreen.tree_earlier_weeks": "── earlier weeks this month ──",
        "pscreen.tree_earlier_months": "── earlier months ──",
        "pscreen.weekdays": "Mo,Tu,We,Th,Fr,Sa,Su",
        "pscreen.hour_suffix": "h",
        "pscreen.recon_top": "Measured (/usage Δ%) and est (scrape ~Σ) are two different sources — look at trend correlation, not exact match",
        "pscreen.recon_empty": "(no measured snapshot spans to reconcile — appears once /usage measurements accumulate 2+)",
        "pscreen.recon_chart_top": "{rng} · latest {pct}% · {n} spans (bars=session 5h% measured, ←→ scroll)",
        "pscreen.win_session": "this 5h window ~Σ{tok} (resets in {left})",
        "pscreen.win_week": "this week ~Σ{tok} (resets in {left})",
        "pscreen.tklog_limit_title": "Token usage · Limit (/usage)",
        "pscreen.tklog_limit_col": "Limit (/usage)",
        "pscreen.tklog_limit_hint": "l back to totals · u refresh /usage · Esc close",
        "pscreen.tklog_limit_empty": "Limit (/usage) not queried — press [u]",
        "pscreen.next_reset": "Next reset: {label}",
        "pscreen.reset_session": "Session 5h",
        "pscreen.reset_week": "Week all",
        "pscreen.tklog_warn_col": "Claude warning",
        "pscreen.tklog_warn_title": "Claude warning",
        "pscreen.tklog_warn_empty": "No active Claude warning (already cleared).",
        "pscreen.tklog_warn_hint": "switch tab · u refresh /usage · Esc close",
        "pscreen.lim_5h": "5h {p}%",
        "pscreen.lim_wk": "wk {p}%",
        "pscreen.left_hm": "{h}h {m}m",
        "pscreen.left_m": "{m}m",
        "pscreen.left_d": "{d}d {h}h",
        "pscreen.hdr_5h": "5h% (in {left})",
        "pscreen.hdr_1w": "1w% (in {left})",
        "claude.warn_fmt_title": "Claude format unrecognized",
        "claude.warn_fmt_body":
            "[Situation]\n"
            "• pytmux cannot recognize the Claude Code screen format.\n"
            "• Token/usage tracking and automation (auto-resume·auto-compact·"
            "limit gate) stop.\n"
            "• Claude Code itself (input·output) is unaffected.\n"
            "• Usually happens when a Claude Code version update changes the layout.\n"
            "\n"
            "[To do]\n"
            "• The warning clears automatically once the screen is recognized "
            "again (wait a moment).\n"
            "• If it persists, pytmux's Claude parser (claude.py) must be updated "
            "for the new format.\n"
            "• If REC capture is on, the captures/ log can be used to analyze it.",
        "claude.warn_repeat_title": "Claude repeat-loop suspected",
        "claude.warn_repeat_body":
            "[Situation]\n"
            "• The same output repeated several times — loop suspected "
            "(warning only, no auto-intervention).\n"
            "\n"
            "[To do]\n"
            "• If there's no progress, give different instructions or retry "
            "after /clear.\n"
            "• Threshold is adjustable/disable-able via option claude_repeat_alert "
            "(0=off).",
        "claude.warn_long_title": "Claude long turn",
        "claude.warn_long_body":
            "[Situation]\n"
            "• The current Claude turn exceeded the threshold and is running long "
            "(warning only, no auto-intervention).\n"
            "\n"
            "[To do]\n"
            "• It may be a normal long task. If it seems stuck, press Esc in the "
            "panel to interrupt.\n"
            "• Threshold is adjustable/disable-able via option claude_long_turn_sec "
            "(0=off).",
    },
})


class ClaudeSaverScreen(ModalScreen):
    """토큰 절감 설정 팝업(docs/internal/TOKEN_SAVING_SCENARIO.md, `token-saver` 명령).

    각 자동 개입을 ●/○ 로 토글하고, 정리 방식·잔량 임계·일/세션 예산을 Enter 로
    프리셋 순환한다. ESC 로 닫는다. MenuScreen 과 같은 위임 구조 — 현재값/동작은
    app._saver_display/_saver_action 이 처리하고(앱 상태 의존), 서버 status 회신마다
    refresh_labels 로 권위값을 다시 그린다(client.py 의 _saver_screen 훅)."""
    CSS = """
    ClaudeSaverScreen { align: center middle; background: $background 80%; }
    #saver { width: 64; height: auto; max-height: 90%;
             border: round $accent; background: $panel; }
    #saver_hint { color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        self._labels = {}
        items = []
        for key, label, _kind in SAVER_ROWS:
            lab = Label(self._fmt(key, label))
            self._labels[key] = (lab, label)
            items.append(ListItem(lab, id=f"s_{key}"))
        items.append(ListItem(Label(i18n.t("Enter 토글/순환 · ESC 닫기"),
                                    id="saver_hint"),
                              id="s__hint", disabled=True))
        yield ListView(*items, id="saver")

    def _fmt(self, key, label):
        return f"{i18n.t(label)}   {self.app._saver_display(key)}"

    def refresh_labels(self):
        for key, (lab, base) in getattr(self, "_labels", {}).items():
            lab.update(self._fmt(key, base))

    def on_mount(self):
        self.query_one(ListView).focus()
        self.app._saver_screen = self

    def on_unmount(self):
        if getattr(self.app, "_saver_screen", None) is self:
            self.app._saver_screen = None

    def on_list_view_selected(self, event):
        item_id = event.item.id or ""
        if not item_id.startswith("s_") or item_id == "s__hint":
            return
        key = item_id[2:]
        # 토글/순환은 팝업을 닫지 않고 동작만 보낸 뒤 라벨을 낙관적으로 갱신한다
        # (_saver_action 이 status 를 즉시 반영). 서버 broadcast 가 권위값으로 확정.
        self.app._saver_action(key)
        self.refresh_labels()

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class RulesEditScreen(ModalScreen):
    """Claude 시작 규칙 편집 팝업(#27). 멀티라인 에디터에 '항상 지킬 규칙'을 적고
    Ctrl+S 로 저장(dismiss=텍스트), Esc 로 취소(dismiss=None). 저장된 규칙은 새
    Claude 세션/clear 후 프롬프트에 자동 주입된다(빈값이면 주입 안 함)."""
    CSS = """
    RulesEditScreen { align: center middle; background: $background 80%; }
    #rulesbox { width: 90%; max-width: 100; height: auto; max-height: 90%;
                border: round $accent; background: $panel; padding: 0 1; }
    /* 헤더: 타이틀(1fr) + 우측 닫기 [x]. */
    #ruleshead { width: 100%; height: 1; }
    #rulestitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #rulesclose { width: 5; height: 1; content-align: center middle;
                  background: $error; color: $text; text-style: bold; }
    /* 타이틀과 에디터 사이 한 줄(여백). */
    #rulesspacer { width: 100%; height: 1; }
    #rulesedit { width: 100%; height: auto; min-height: 8; max-height: 70%; }
    /* 하단 저장/취소 버튼(에디터와 한 줄 띄움, 우측 정렬). */
    #rulesbtns { width: 100%; height: 1; margin-top: 1; align-horizontal: right; }
    #rulesbtns Label { width: auto; height: 1; padding: 0 2; margin-left: 2;
                       text-style: bold; }
    #rulessave { background: $success; color: $text; }
    #rulescancel { background: $panel-darken-2; color: $text; }
    """

    def __init__(self, text=""):
        super().__init__()
        self._text = text or ""

    def compose(self) -> ComposeResult:
        with Vertical(id="rulesbox"):
            with Horizontal(id="ruleshead"):
                yield Label(i18n.t("Claude 시작 규칙 — Ctrl+S 저장 · Esc 취소"),
                            id="rulestitle")
                # markup=False: "[x]" 가 마크업 태그로 사라지지 않게.
                yield Label("[x]", id="rulesclose", markup=False)  # 닫기 버튼
            yield Label("", id="rulesspacer")        # 타이틀↔에디터 한 줄 여백
            yield TextArea(self._text, id="rulesedit")
            with Horizontal(id="rulesbtns"):
                yield Label(i18n.t("저장"), id="rulessave")
                yield Label(i18n.t("취소"), id="rulescancel")

    def on_mount(self):
        ta = self.query_one(TextArea)
        ta.focus()

    def on_click(self, event: events.Click):
        # 닫기 [x]/취소 → 취소(None), 저장 → 텍스트 반환. 그 외(에디터 등) 유지.
        w = getattr(event, "widget", None)
        while w is not None:
            wid = getattr(w, "id", None)
            if wid in ("rulesclose", "rulescancel"):
                event.stop(); self.dismiss(None); return
            if wid == "rulessave":
                event.stop(); self.dismiss(self.query_one(TextArea).text); return
            w = w.parent

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key == "ctrl+s":
            event.stop()
            self.dismiss(self.query_one(TextArea).text)


class ModelCtxScreen(ModalScreen):
    """Claude 모델·컨텍스트 크기 변경 팝업(요청). 상태줄 모델 배지 클릭 / `model`
    명령 / esc 모드 상태바 포커스로 연다. ←→ 로 값 변경, Enter 로 적용(=활성 패널에
    '/model <이름> [컨텍스트]' 주입), Esc 취소. (clientscreens 에서 이리로 이전.)"""
    CSS = """
    ModelCtxScreen { align: center middle; }
    #mcmenu { width: 56; height: auto; max-height: 80%;
              border: round $accent; background: $panel; }
    """
    # 모델 후보(짧은 별칭 + 구체 버전). '/model <이름>' 인자로 그대로 주입한다.
    _MODELS = ["opus", "sonnet", "haiku",
               "opus-4.8", "sonnet-4.6", "haiku-4.5", "default"]
    # 컨텍스트 크기: 기본 / 1M(확장). 'default' 면 모델만, 아니면 뒤에 토큰으로 덧붙임.
    _CTX = [("기본", "default"), ("1M", "1m")]

    def __init__(self, current_model=None):
        super().__init__()
        models = [(m, m) for m in self._MODELS]
        mi = 0
        if current_model:
            cm = current_model.lower()
            for i, (_d, v) in enumerate(models):
                if cm.startswith(v.lower()) and v != "default":
                    mi = i
                    break
        self.rows = [{"label": "모델", "choices": models},
                     {"label": "컨텍스트", "choices": list(self._CTX)}]
        self.sel = [mi, 0]

    def compose(self) -> ComposeResult:
        self._labs = []
        items = []
        for i in range(len(self.rows)):
            lab = Label(self._row_text(i))
            self._labs.append(lab)
            items.append(ListItem(lab, id=f"mc_{i}"))
        yield ListView(*items, id="mcmenu")

    def on_mount(self):
        lv = self.query_one(ListView)
        lv.index = 0
        lv.focus()
        lv.border_title = i18n.t("모델·컨텍스트 변경 · ←→ 값 · Enter 적용 · Esc")
        self._update_sub()

    def _row_text(self, i):
        return format_option_row(self.rows[i], self.sel[i])

    def _result(self):
        model = self.rows[0]["choices"][self.sel[0]][1]
        ctx = self.rows[1]["choices"][self.sel[1]][1]
        return (model, ctx)

    def _update_sub(self):
        model, ctx = self._result()
        arg = model if ctx == "default" else f"{model} {ctx}"
        self.query_one(ListView).border_subtitle = ": /model " + arg

    def on_list_view_selected(self, event):
        self.dismiss(self._result())

    def on_key(self, event: events.Key):
        k = event.key
        if k == "escape":
            event.stop()
            self.dismiss(None)
        elif k == "enter":
            event.stop()
            self.dismiss(self._result())
        elif k in ("left", "right") and self.rows:
            event.stop()
            i = self.query_one(ListView).index or 0
            o = self.rows[i]
            self.sel[i] = (self.sel[i] + (1 if k == "right" else -1)) \
                % len(o["choices"])
            self._labs[i].update(self._row_text(i))
            self._update_sub()


class PermModeScreen(ModalScreen):
    """Claude 권한모드 선택 팝업(하단 footer 클릭, §10 item 2). 현재 모드를 표시
    하고 목표 모드를 고르면 그 키를 dismiss → 서버가 shift+tab 폐루프로 목표까지
    순환 주입한다. bypass(권한 우회)는 **가용할 때만** 목록 맨 아래에 노출한다
    (`bypass_available` — 서버가 idle footer 에서 bypass 모드를 관측해 시작 시
    `--dangerously-skip-permissions` 가 활성임을 안 경우). 가용하지 않은 세션에는
    숨겨 실수로 도달 불가 모드를 고르는 걸 막는다.

    배치(§10-A #2): 클릭한 footer('auto mode on …') 줄 **바로 위**에, 그리고
    **좌측 정렬**(footer 가 시작하는 패널 왼쪽 x 에 맞춤)로 띄운다. 그래서 팝업이
    클릭한 그 줄에 붙어 보인다(화면 중앙이 아님). anchor 가 없으면 화면 중앙.
    (clientscreens 에서 이리로 이전.)"""
    CSS = """
    PermModeScreen { align: left top; }
    #perm { width: 60; height: auto; max-height: 80%;
            border: round $accent; background: $panel; }
    #perm ListItem Label { width: 1fr; }
    """
    # 박스 폭(CSS #perm width 와 일치) — 좌측 정렬 오프셋/중앙 계산에 쓴다.
    _BOX_W = 60
    _MODES = [
        ("auto", "auto — 모든 동작 자동 수락, 안전검사 (⏵⏵ auto mode)"),
        ("accept", "accept — 편집·기본 FS 만 자동 수락 (⏵⏵ accept edits)"),
        ("default", "default — 매번 확인 (일반 모드)"),
        ("plan", "plan — 플랜 모드 (계획만, 실행 안 함)"),
    ]
    # 가용할 때만(또는 현재 모드일 때) 목록 끝에 덧붙는 위험 모드.
    _BYPASS = ("bypass", "bypass — 권한 우회, 확인 없음 ⚠️ (Bypass Permission Mode)")

    def __init__(self, current, anchor_y=None, anchor_x=None,
                 bypass_available=False):
        super().__init__()
        self._current = current
        # bypass 항목 노출 여부: 서버가 가용 판정했거나 현재가 이미 bypass 면 포함.
        show_bypass = bool(bypass_available) or current == "bypass"
        self._modes = list(self._MODES)
        if show_bypass:
            self._modes.append(self._BYPASS)
        # 클릭한 footer 행(화면 y). 아래에 공간이 있으면 그 아래, 없으면 위에 띄운다.
        # None 이면 화면 세로 중앙(기존 동작).
        self._anchor_y = anchor_y
        # 클릭한 footer 가 시작하는 화면 x(패널 왼쪽). 좌측 정렬 기준(#2).
        # None 이면 화면 가로 중앙.
        self._anchor_x = anchor_x

    def compose(self) -> ComposeResult:
        items = []
        for key, label in self._modes:
            mark = i18n.t("  ◀ 현재") if key == self._current else ""
            items.append(ListItem(Label(i18n.t(label) + mark, markup=False),
                                  id=f"M_{key}"))
        lv = ListView(*items, id="perm")
        lv.border_title = i18n.t("pscreen.perm_title",
                                 current=self._current or '?')
        yield lv

    def on_mount(self):
        lv = self.query_one(ListView)
        lv.focus()
        # 클릭 위치 기준 세로 배치: 아래 공간이 충분하면 클릭 행 바로 아래, 아니면 위.
        box_h = len(self._modes) + 2          # 테두리 포함 대략 높이
        sh = self.size.height
        if self._anchor_y is None:
            y = max(0, (sh - box_h) // 2)     # 앵커 없으면 중앙
        elif self._anchor_y - box_h >= 0:
            y = self._anchor_y - box_h        # 클릭한 줄 **바로 위**(우선, #29)
        else:
            y = self._anchor_y + 1            # 위 공간이 없을 때만 아래
        # 가로 배치(#2): footer 시작 x 에 좌측 정렬(align:left top 기준 offset).
        # 박스가 화면 오른쪽을 넘지 않게 클램프. 앵커 없으면 가로 중앙.
        sw = self.size.width
        if self._anchor_x is None:
            x = max(0, (sw - self._BOX_W) // 2)
        else:
            x = max(0, min(self._anchor_x, sw - self._BOX_W))
        lv.styles.offset = (x, y)

    def on_list_view_selected(self, event):
        self.dismiss(event.item.id[2:])   # "M_auto" → "auto"

    def on_click(self, event: events.Click):
        # 박스(#perm) 바깥(백드롭) 클릭 → 닫기(§10-A #3). InfoScreen 패턴 재사용.
        w = getattr(event, "widget", None)
        inside = False
        while w is not None:
            if getattr(w, "id", None) == "perm":
                inside = True
                break
            w = w.parent
        if not inside:
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


# TokenLogScreen 이 쓰는 심볼. usagelog 는 S5 T5 에서 플러그인 소속(상대 import).
from . import usagelog
from .claude import parse_reset_ts
from pytmuxlib.clientutil import (_char_cells, bar, bar_floating,
                                  bar_floating_segments,
                                  format_option_row, _CLOCK_FONT, _CLOCK_FONT_ROWS)
from pytmuxlib.clientscreens import usage_bar_lines


class _TkTabConnector(Widget):
    """토큰 팝업 메인 뷰 탭 줄과 본문 사이의 '노트북 탭' 연결선(사용자 요청 2026-06-18 —
    메인 탭바와 같은 탭 모양으로 통일). 한 줄짜리 가로 규칙(─, 박스 테두리색 accent)을
    그리되 **활성 뷰 탭 아래 구간만** 오렌지 ▀(accent, 라인·활성 탭과 동색)로 덮어, 활성 탭이 아래 본문으로
    열려 이어지는 노트북 탭처럼 보이게 한다 — 메인 클라이언트 TabBar 가 콘텐츠 상단에
    ▀ 를 덧칠해 노트북 탭을 만드는 것(client.py `_composite`)과 같은 기법을 위젯 한 줄로
    옮긴 것. 활성 탭 위치는 매 렌더에 그 Label 의 화면 region 으로 읽어, 폭 변화·탭 전환에
    자동으로 따라온다(레이아웃 후 region 이 권위). 위에 탭 Label 들, 아래에 보조옵션/본문이
    오므로 활성 탭은 그 탭에 속한 영역으로 정확히 열린다."""

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        rule_st = Style(color=theme_color(self, "accent"))
        if w <= 0:
            return Strip([], 0)
        # 활성 메인 탭 Label 의 가로 구간(이 위젯 기준 상대 x)을 화면 region 으로 구한다.
        bx0 = bx1 = -1
        scr = self.screen
        getter = getattr(scr, "_active_main_tab_widget", None)
        lbl = getter() if getter else None
        if lbl is not None and getattr(lbl, "display", True):
            try:
                bx0 = lbl.region.x - self.region.x
                bx1 = bx0 + lbl.region.width
            except Exception:
                bx0 = bx1 = -1
        bx0 = max(0, bx0)
        bx1 = min(w, bx1)
        if bx1 > bx0:
            # 다리 ▀ 도 라인·활성 탭과 같은 오렌지(accent) — 활성 탭이 라인으로
            # 이어지는 한 덩어리로 보이게(사용자 요청 2026-06-18). ─(얇은 중앙선)와
            # ▀(상단 반블록)는 글자가 달라 같은 색이어도 '활성 탭 아래만 채워진' 노트북
            # 모양으로 구분된다.
            br_st = Style(color=theme_color(self, "accent"))
            segs = []
            if bx0 > 0:
                segs.append(Segment("─" * bx0, rule_st))
            segs.append(Segment("▀" * (bx1 - bx0), br_st))
            if bx1 < w:
                segs.append(Segment("─" * (w - bx1), rule_st))
        else:
            segs = [Segment("─" * w, rule_st)]
        return Strip(segs).adjust_cell_length(w, rule_st)


# 모델 티어 → 막대 색(요청 2026-06-21 — 막대를 모델 구성비로 색 분할). 한 막대가
# 여러 모델로 쌓이므로 서로 잘 구분되는 색을 고른다. 미상(unknown)은 회색.
_MODEL_BAR_COLORS = {
    "haiku": "green",
    "sonnet": "cyan",
    "opus": "magenta",
    "fable": "yellow",
    "unknown": "#808080",
}

# 범례 표시 라벨(티어명 → 사람용). 미상은 '기타'.
_MODEL_LABELS = {
    "haiku": "Haiku", "sonnet": "Sonnet", "opus": "Opus",
    "fable": "Fable", "unknown": "?",
}

# [세션] 뷰에서 **현재 활성 세션** 행을 알리는 색(요청 2026-06-21) — 모델 팔레트
# (green/cyan/magenta/yellow)와 겹치지 않는 밝은 오렌지(테마 accent 와 같은 계열).
_ACTIVE_SESSION_COLOR = "orange1"


def _session_id_of_label(label):
    """'세션 27 (탭1:p1)' → 27. group_key 가 만든 라벨은 로케일 무관하게 항상
    '세션 N …' 로 시작한다(usagelog.group_key). '기타 N개' 등은 None."""
    parts = (label or "").split()
    if len(parts) >= 2 and parts[0] == "세션":
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


class _ReconChart(Widget):
    """[대사] 뷰의 **시간축 세로 막대 그래프**(사용자 요청 2026-06-20 — 표 대신 시간에
    따른 5h 사용률 증가를 한눈에). 각 막대=대사 구간 1개, 높이=그 구간 끝의 실측 세션
    5h%(0~100 고정). **색=그 구간 토큰의 모델 구성비**(요청 2026-06-21) — 한 막대가
    Haiku/Sonnet/Opus/Fable 색 띠로 바닥부터 쌓여 모델별 비중을 대략 가늠한다(모델
    분해가 없는 구간은 임계색 폴백: ≥80 빨강·≥50 노랑·그 외 초록, 리셋은 청록).
    좌우(←→/PgUp·PgDn/Home·End)로 더 이전 구간을 스크롤한다. 폭에 들어가는 만큼만
    그려 가로 스크롤=그릴 구간 창을 옮기는 것(_ReconChart.x_off, 순수 기하는
    usagelog.recon_chart)."""

    GUTTER = 4          # 좌측 y축 눈금자 폭(숫자 3 + 축선 1)
    STEP = 2            # 구간당 칸수(막대 1 + 간격 1)
    can_focus = True

    def __init__(self, **kw):
        super().__init__(**kw)
        self.intervals: list = []
        self.x_off = 0
        self._cap = 1        # 마지막 빌드의 capacity(페이지 스크롤 보폭)
        self._max_off = 0    # 마지막 빌드의 최대 오프셋(스크롤 클램프)

    def set_data(self, intervals):
        self.intervals = intervals or []
        self.refresh()

    def _plot_dims(self):
        w = self.size.width
        h = self.size.height
        plot_w = max(1, w - self.GUTTER)
        plot_h = max(1, h - 2)      # 하단 2줄=축선+라벨
        return plot_w, plot_h

    def _build(self):
        plot_w, plot_h = self._plot_dims()
        ch = usagelog.recon_chart(self.intervals, plot_w, plot_h,
                                  self.x_off, self.STEP)
        self.x_off = ch["x_off"]
        self._max_off = ch["max_off"]
        self._cap = max(1, plot_w // self.STEP)
        return ch

    def scroll_intervals(self, delta):
        """막대 창을 delta 구간만큼 옮긴다(음수=더 새, 양수=더 옛). Home/End 는
        delta=±10**6 로 끝까지."""
        self.x_off = max(0, min(self._max_off, self.x_off + delta))
        self.refresh()

    def on_mouse_scroll_down(self, event):   # 휠 아래=더 새 구간(오른쪽)
        event.stop()
        self.scroll_intervals(-1)

    def on_mouse_scroll_up(self, event):     # 휠 위=더 옛 구간(왼쪽)
        event.stop()
        self.scroll_intervals(1)

    @staticmethod
    def _bar_style(pct, reset):
        if reset:
            return Style(color="cyan")
        color = "red" if pct >= 80 else "yellow" if pct >= 50 else "green"
        return Style(color=color, bold=pct >= 50)

    @staticmethod
    def _cell_style(model, iv):
        """막대 한 칸 색: 모델 티어가 있으면 그 색(요청 2026-06-21), 없으면(모델
        미분해 구간) 기존 임계/리셋 색으로 폴백 — 하위호환."""
        if model:
            color = _MODEL_BAR_COLORS.get(model, _MODEL_BAR_COLORS["unknown"])
            return Style(color=color, bold=model in ("opus", "fable"))
        return _ReconChart._bar_style(iv.get("pct1", 0) or 0, iv.get("reset"))

    def render_line(self, y: int) -> Strip:
        w = self.size.width
        if w <= 0:
            return Strip([], 0)
        ch = self._build()
        plot_w, plot_h = self._plot_dims()
        grid = ch["grid"]
        col_iv = ch["col_iv"]
        cell_model = ch.get("cell_model") or []
        muted = Style(dim=True)
        axis_st = Style(color=theme_color(self, "accent"))
        segs = []
        axis_max = ch.get("axis_max", 100)
        if y < plot_h:
            # 좌측 눈금자: 위=axis_max·중앙=절반·바닥=0 에 숫자+축틱, 그 외 축선만.
            # axis_max 는 보이는 구간 최대치에 따라 100 또는 50(usagelog.recon_chart).
            mid = plot_h // 2
            if y == 0:
                lab = f"{axis_max:>3}"
            elif y == plot_h - 1:
                lab = "  0"
            elif y == mid:
                lab = f"{axis_max // 2:>3}"
            else:
                lab = "   "
            segs.append(Segment(lab, muted))
            segs.append(Segment("┤" if lab.strip() else "│", axis_st))
            # 막대 영역: 열마다 글리프+구간 색.
            row = grid[y] if y < len(grid) else " " * plot_w
            x = 0
            while x < plot_w:
                iv_i = col_iv[x] if x < len(col_iv) else None
                if iv_i is None:
                    # 빈/간격 열은 한 칸씩(인접 막대와 색이 달라 묶지 않음).
                    segs.append(Segment(row[x] if x < len(row) else " "))
                    x += 1
                    continue
                iv = self.intervals[iv_i]
                m = (cell_model[y][x] if y < len(cell_model)
                     and x < len(cell_model[y]) else None)
                st = self._cell_style(m, iv)
                segs.append(Segment(row[x] if x < len(row) else " ", st))
                x += 1
        elif y == plot_h:
            # x축선: 눈금자 모서리 └ + 막대 영역 ─, 라벨 위치엔 ┬.
            segs.append(Segment("   ", muted))
            segs.append(Segment("└", axis_st))
            line = ["─"] * plot_w
            for col, _txt in ch["labels"]:
                if 0 <= col < plot_w:
                    line[col] = "┬"
            segs.append(Segment("".join(line), axis_st))
        elif y == plot_h + 1:
            # 라벨줄: 막대 아래 시각(겹치지 않게 솎인 것).
            segs.append(Segment(" " * self.GUTTER, muted))
            buf = [" "] * plot_w
            for col, txt in ch["labels"]:
                for j, c in enumerate(txt):
                    if 0 <= col + j < plot_w:
                        buf[col + j] = c
            segs.append(Segment("".join(buf), muted))
        return Strip(segs).adjust_cell_length(w)


class TokenLogScreen(ModalScreen):
    """토큰 사용량 영속 로그 집계 팝업(#7, 2026-06-12 재설계).

    상단은 실측 한도(세션 5h·주간 막대+리셋)와 현재 창 추정 Σ, 아래 표는 **한 번에
    한 차원만** 보인다(ccusage 의 daily/blocks 뷰 참고 — 예전엔 계정 그룹행과 시간
    버킷행을 구분선으로 한 표에 섞어 읽기 어려웠다):
      · 기간 뷰(기본): [h]시간 [d]일 [w]주 [m]월 — 행=시간 버킷(일엔 요일 곁들임)
      · 계정 뷰: [c] — 행=계정별 전체 이력 합(행 선택=그 계정 필터+일별 드릴다운)
      · 세션 뷰: [p] — 행=Claude 세션별 합(대표 탭:패널 라벨)
    [a] 계정 필터 순환(기간/세션 뷰에 적용), [o] 기간 정렬(시간순↔토큰순), 방향키
    스크롤, 그 외/Esc 닫기. 전환은 전부 라운드트립 없음."""
    CSS = """
    TokenLogScreen { align: center middle; }
    /* 높이 고정(2026-06-07 사용자 요청): 내용(레코드 수)에 따라 박스가 줄거나
       출렁이지 않게 **고정 높이**로 둔다. 표(DataTable)는 1fr 로 남는 높이를 채운다. */
    #tklogbox { width: 96%; max-width: 86; height: 76%;
                border: round $accent; background: $panel; padding: 0 1; }
    #tkloghead { width: 100%; height: 1; }
    #tklogtitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #tklogclose { width: 5; height: 1; content-align: center middle;
                  background: $error; color: $text; text-style: bold; }
    /* 상단 2단 구조(§7.1·§7.2): ①뷰 탭 1줄(#tktabs) — 상호배타 뷰만 같은 모양의
       가로 탭으로 ②활성 탭의 보조옵션 1줄(#tksub) — 그 탭에서만 작동하는 옵션을
       아래로. 예전엔 기간/보기/조회 외곽선 그룹 3개에 탭과 필터가 섞여 위계가
       구분되지 않았다(사용자 보고). */
    #tktabs { width: 100%; height: 1; align-horizontal: left; }
    #tktabs Label { height: 1; padding: 0 1; margin: 0 1 0 0; }
    /* 메인 탭 줄과 본문 사이 노트북 연결선(_TkTabConnector) — 활성 탭이 본문으로 열려
       이어지게(메인 탭바와 같은 모양, 사용자 요청 2026-06-18). */
    #tkconn { width: 100%; height: 1; }
    /* 보조옵션 줄: 활성 탭(기간/세션)의 입도·정렬을 살짝 들여써 상위 탭과 구분. */
    #tksub { width: 100%; height: 1; align-horizontal: left; padding: 0 0 0 2; }
    #tksub Label { height: 1; padding: 0 1; margin: 0 1 0 0; }
    #tksublead { color: $text-muted; padding: 0 1 0 0; margin: 0; }
    /* 탭 색 언어(사용자 요청 2026-06-18): 활성 탭=accent(오렌지)+흰 글자 — 노트북
       연결선·박스 테두리(둘 다 $accent)와 같은 오렌지로 맞춰 활성 탭이 그 라인으로
       열려 이어지게 한다(처음엔 primary 파랑이었으나 라인색과 어긋나 통일). 액션
       버튼=success(초록)+검은 글자, 비활성=옅은 surface/muted. */
    .tkbtab { background: $surface; color: $text-muted; }       /* 뷰 탭·옵션(비활성) */
    .tkbtab-active { background: $accent; color: white; text-style: bold; } /* 활성(오렌지) */
    .tkbbtn { background: $success; color: black; text-style: bold; }  /* 액션 버튼(초록, 뷰 아님) */
    /* 스코프/한도(위)·키 안내(아래)는 옅게 1~몇 줄, 표가 남는 높이를 채운다. */
    #tktop { width: 100%; height: auto; color: $text-muted; }
    #tkhint { width: 100%; height: 1; color: $text-muted; }
    #tktable { width: 100%; height: 1fr; }
    /* [대사] 시간축 그래프: 표와 같은 본문 자리(남는 높이). 평소 숨김. */
    #tkchart { width: 100%; height: 1fr; }
    """
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")
    # 계층 타임라인 뷰(2026-06-21): 옛 시간/일/주/월 서브탭(_BUCKETS·_TAB_BUCKET)을
    # 제거하고 단일 트리 뷰로 통합 — 입도는 행을 펼치고(▶→▼) 접어(▼→▶) 고른다.
    # 탭 라벨(넓은 폭, 좁은 폭). 좁으면 한 글자로 줄여 모바일에서 탭 줄이 안 넘치게(P6).
    _TAB_LABELS = {
        "tab_period": ("기간", "기"),
        "tab_panel": ("세션", "세"),
        "tab_order": ("정렬", "정"), "tab_usage": ("/usage", "U"),
        "tab_saver": ("시나리오", "S"), "tab_recon": ("비교", "R"),
        "tab_limit": ("한도", "L"), "tab_warn": ("경고", "경"),
    }
    # [패널]/계정 그룹이 많을 때 상위 N + '기타'로 접어 길이 폭주를 막는다(설계 §4).
    _GROUP_TOP = 8

    def __init__(self, records, usage=None, total_all=None,
                 daily=None, reconcile=None, daily_pct=None, hourly_pct=None,
                 hourly_week_pct=None, active_session=None, initial_mode=None):
        super().__init__()
        self._records = records or []
        # 요청 2026-06-21: 현재 활성 패널의 claude 세션 id(없으면 None) — [세션] 뷰가
        # 이 세션 행을 하이라이트하고 막대를 다른 색으로 그린다.
        self._active_session = active_session
        # §10-D: 세션 5h 한도 최대%(권위 /usage). 스크랩 Σ 가 5h 소비를 과소집계하므로
        # 사용량 뷰가 '얼마나 썼나'를 이 권위값으로 보인다. daily_pct=일자별(레거시
        # 유지), hourly_pct=시각('YYYY-MM-DD HH:00')별 — 5h 비율은 일 단위가 아니라
        # **시간 단위 뷰**에 두는 게 의미상 맞다는 사용자 결정(2026-06-17)으로, 표의
        # '5h%' 열은 이제 hour 버킷에서 hourly_pct 로 보인다(None/구버전 서버 → 열 생략).
        self._daily_pct = daily_pct or {}
        self._hourly_pct = hourly_pct or {}
        # 1w%(주간 전체모델 한도) 시각별 — 5h% 옆 열(사용자 요청 2026-06-17). 5h% 와 같은
        # hour 버킷에서만 보이고, None/구버전 서버면 열 생략.
        self._hourly_week_pct = hourly_week_pct or {}
        # 시각별 5h% 를 '계단식 누적' 막대로 그리기 위한 구간 {hour: (start, end)} —
        # 각 시각의 막대가 직전 시각이 끝난 위치에서 시작하고, 5h 창이 리셋되면(누적%
        # 하락 또는 ≥5h 공백) 다시 0 부터 시작한다(요청 2026-06-17, _hourly_spans).
        self._hourly_span = self._hourly_spans(self._hourly_pct)
        # 전체 이력 일자별 합성 레코드(서버 daily_breakdown). day/week/month 버킷은
        # 이걸로 집계해 옛 버킷이 cap 에 잘리지 않게 한다(None=구버전 서버 → 폴백으로
        # 최근 N 건 _records 사용). hour 버킷만 raw _records 를 쓴다(_refresh 참고).
        self._full_recs = usagelog.daily_to_records(daily) if daily else None
        self._usage = usage          # M19 그림자 /usage 한도(dict|None)
        # S6 T2: 대사 구간(서버 usagedb.reconcile 결과). [대사] 뷰 전용 진단 데이터.
        self._reconcile = reconcile or []
        self._recon_mode = False     # True 면 표가 대사 구간을 보여준다([r] 토글)
        # True 면 표 자리에 /usage 한도 상세(막대·리셋·창Σ·신선도)를 보여준다([l]
        # 토글). 상단 빽빽한 7줄 블록을 이 전용 뷰로 옮겨 작은 화면을 정리(사용자
        # 요청 2026-06-14) — 기본 화면 상단은 1줄 한도 요약만 남긴다. recon 과 배타.
        # initial_mode=="limit" 이면 처음부터 한도 탭으로 연다 — usage-view 팝업이
        # 이 통합 팝업의 한도 탭을 열게 한다(통합, 사용자 결정 2026-06-17).
        self._limit_mode = (initial_mode == "limit")
        # initial_mode=="warn" 이면 경고 탭으로 연다 — 상태줄 ⚠ 경고 배지 클릭이 별도
        # InfoScreen 대신 이 통합 팝업의 경고 탭을 열게 한다(통합, 사용자 결정 2026-06-17).
        self._warn_mode = (initial_mode == "warn")
        # Phase B: 서버가 SQL 로 집계한 정확한 전체 이력 합(레코드 cap 무관). 받은
        # 레코드(_records)는 최근 N 건이라 그 Σ 는 과소표시될 수 있으므로, lifetime
        # 합은 이 값을 쓴다(None=구버전 서버 → 레코드 합으로 폴백).
        self._total_all = total_all
        # 계층 타임라인 뷰(2026-06-21)는 입도 개념(_bucket)이 없다 — 월→주→일→시각을
        # 한 트리로 보인다. 단, 세션 뷰와 토큰순(평탄) 폴백은 day 입도로 집계하므로
        # 내부 기본값만 "day" 로 둔다(사용자 전환 UI 없음). initial_mode=="hour"(상태줄
        # "N%/5h used" 클릭)는 트리에서 오늘 행이 이미 시각까지 펼쳐져 있어 별도 분기
        # 불요 — 기본 시간순 트리로 진입한다.
        self._bucket = "day"
        # 계층 뷰 펼침 상태: 기본 펼침(§3 — 오늘=시각)에서 사용자가 **토글한** 행 키
        # 집합. effective_open = default ^ (key in _tree_toggled) — 한 집합으로 기본
        # 열린 행 접기와 기본 닫힌 행 펼치기를 모두 표현한다. 정렬/뷰 전환 시 비운다.
        self._tree_toggled: set = set()
        # 마지막으로 그린 트리 노드 목록(표 행과 1:1, 커서 행→노드 매핑·토글용).
        self._tree_nodes: list = []
        # 표 차원(2026-06-12 재설계 — 한 번에 한 차원만): "time"=시간 버킷(기본),
        # "session"=세션별 합. 세션은 닫히고 재사용되는 패널 id 대신 안정적 세션
        # id 로 묶는다(설계 §8). 계정 차원은 제거 — 토큰 사용량은 계정과 무관하게
        # 현재 로컬 머신 기준으로만 본다(2026-06-19 결정).
        self._view = "time"
        # 세션 뷰 타임스탬프 열용 시작 시각(grows 와 동순) — _view_rows 가 채운다.
        self._sess_times = None
        # 버킷(시간축) 정렬: "time"=최근 위(기본), "tokens"=많이 쓴 순([o] 토글).
        self._order = "time"

    def compose(self) -> ComposeResult:
        with Vertical(id="tklogbox"):
            with Horizontal(id="tkloghead"):
                yield Label(i18n.t("토큰 사용량"), id="tklogtitle")
                # markup=False: "[x]" 가 마크업 태그로 사라지지 않게(배경색만
                # 남고 X 가 안 보이던 버그).
                yield Label("[x]", id="tklogclose", markup=False)  # 닫기 버튼
            # 상위 = 상호배타 **뷰 탭**(§7.1·§7.2): 같은 모양의 가로 탭 한 줄. 활성
            # 탭만 하이라이트. 끝의 /usage·시나리오는 뷰가 아니라 **액션**이라 다른
            # 색(.tkbbtn)으로 구분한다.
            # 1-4: 초기 compose 라벨도 i18n.t 로(_sync_tabs 의 resize 갱신은 이미 i18n.t
            # — 첫 페인트에서 en 사용자에게 한글이 보이던 누락 보완). 키는 등록돼 있다.
            with Horizontal(id="tktabs"):
                yield Label(i18n.t("기간"), id="tab_period", classes="tkbtab",
                            markup=False)
                yield Label(i18n.t("세션"), id="tab_panel", classes="tkbtab",
                            markup=False)
                yield Label(i18n.t("한도"), id="tab_limit", classes="tkbtab",
                            markup=False)
                yield Label(i18n.t("비교"), id="tab_recon", classes="tkbtab",
                            markup=False)
                yield Label(i18n.t("경고"), id="tab_warn", classes="tkbtab",
                            markup=False)
                yield Label("/usage", id="tab_usage", classes="tkbbtn",
                            markup=False)
                yield Label(i18n.t("시나리오"), id="tab_saver", classes="tkbbtn",
                            markup=False)
            # 노트북 연결선: 활성 메인 탭이 아래 본문으로 열려 이어지게(메인 탭바와
            # 같은 모양, 사용자 요청 2026-06-18). _sync_tabs 가 탭 전환 시 refresh.
            yield _TkTabConnector(id="tkconn")
            # 보조옵션 줄: **활성 탭에서만 작동하는 옵션**을 탭 하위로(§7.2). 기간/세션
            # 뷰에서 정렬만 남는다(시간/일/주/월 서브탭은 계층 트리로 대체, 2026-06-21).
            # 그 외(한도/대사/경고)엔 숨긴다(_sync_tabs 가 display 제어).
            with Horizontal(id="tksub"):
                yield Label("", id="tksublead", markup=False)
                yield Label(i18n.t("정렬"), id="tab_order", classes="tkbtab",
                            markup=False)
            yield Static("", id="tktop", markup=False)
            table = DataTable(id="tktable", zebra_stripes=True,
                              cursor_type="row")
            table.can_focus = True
            yield table
            # [대사] 뷰 전용 시간축 그래프(요청 2026-06-20) — 표와 같은 자리를 차지하되
            # 평소엔 숨기고 대사 모드에서만 보인다(_refresh 가 display 토글).
            chart = _ReconChart(id="tkchart")
            chart.display = False
            yield chart
            yield Static("", id="tkhint", markup=False)

    async def on_mount(self):
        # status 훅이 새 /usage 결과를 이 화면에 밀어넣게 등록(M19). 자동 조회는 안
        # 한다(매번 열 때 숨은 claude 기동은 과함) — 마지막 결과를 보여주고, 갱신은
        # [/usage] 버튼/`claude-usage` 명령으로 명시 트리거한다.
        self.app._token_log_screen = self
        await self._refresh()
        self.query_one(DataTable).focus()
        # 한도 탭의 카운트다운 시계를 매 초 갱신한다(usage-view 통합, 2026-06-17).
        # 한도 뷰가 아닐 땐 _tick_limit 가 no-op 이라 기간/계정/세션 표는 안 건드린다.
        self.set_interval(1.0, self._tick_limit)

    def _tick_limit(self):
        """한도 뷰가 켜져 있으면 표(막대+창Σ+시계)를 다시 그려 카운트다운을 1초마다
        센다. 작은 정적 뷰라 통째 재구성이 가볍고, 행 커서/스크롤 개념이 없어 부작용이
        없다. 다른 뷰(기간/계정/세션/대사)에선 아무것도 안 한다(표 흔들림 방지)."""
        if not (self._limit_mode and not self._recon_mode):
            return
        try:
            table = self.query_one(DataTable)
        except Exception:
            return
        table.clear(columns=True)
        self._refresh_limit(table)

    def on_unmount(self):
        if getattr(self.app, "_token_log_screen", None) is self:
            self.app._token_log_screen = None

    def update_usage(self, usage):
        """클라 status 훅이 새 /usage 결과를 전달하면 갱신·재그린다(M19)."""
        if usage and usage != self._usage:
            self._usage = usage
            self.run_worker(self._refresh())

    @staticmethod
    def _cellpad(s, cells):
        """문자열을 표시 셀폭 cells 로 우측 패딩(한글 2셀 고려) — 막대 그래프 정렬용."""
        used = sum(_char_cells(c) for c in s)
        return s + " " * max(0, cells - used)

    def _usage_lines(self):
        """M19 그림자 /usage 한도를 **막대 그래프**로 보여준다(요청 — Claude /usage 의
        세션/주간 사용률 바). 공유 포맷터(usage_bar_lines)를 쓰고, 데이터가 없으면
        안내 1줄([u] 로 조회)로 폴백한다."""
        try:
            w = self.app.size.width
        except Exception:
            w = 80
        age = getattr(getattr(self.app, "status", None), "usage_age_sec", None)
        # row_gap: [한도] 뷰는 막대 3개를 빈 줄로 띄워 보기 좋게(요청 2026-06-18).
        return usage_bar_lines(self._usage, w, age_sec=age, row_gap=True) \
            or [i18n.t("한도(/usage): [u] 눌러 조회")]

    @staticmethod
    def _fmt_left(sec: int) -> str:
        """남은 시간(초)을 짧은 사람표기로: 90분 미만 'N분', 하루 미만 'N시간M분',
        그 이상 'N일H시간'."""
        mins = max(0, int(sec) // 60)
        if mins < 90:
            return i18n.t("pscreen.left_m", m=mins)
        h, m = divmod(mins, 60)
        if h < 24:
            return i18n.t("pscreen.left_hm", h=h, m=m)
        d, h = divmod(h, 24)
        return i18n.t("pscreen.left_d", d=d, h=h)

    def _window_lines(self):
        """현재 5h/주간 **창 구간의 스크랩 추정 Σ + 리셋까지 남은 시간** 한 줄(없으면
        빈 목록). 실측 리셋 표기(parse_reset_ts)로 창 시작(리셋-5h/-7일)을 역산해
        받은 레코드를 합산한다 — 실측 %(점유)와 별개로 "이번 창에서 얼마나 썼나"를
        토큰 양으로 보여 준다(요청: 5시간 내·1주 내 사용량과 리셋 시점을 알기 쉽게).
        리셋 표기가 없거나 과거(stale 실측)면 그 축은 생략한다(지어내지 않음).

        주의: 합산 원천은 받은 최근 레코드(서버 cap N건)라 7일 창은 이력이 cap 을
        넘으면 과소일 수 있다 — 추정(~) 라벨로 일관 표기."""
        u = self._usage
        if not isinstance(u, dict):
            return []
        import time as _t
        now = _t.time()
        parts = []
        for key, span, name_key in (
                ("session", 5 * 3600, "pscreen.win_session"),
                ("week_all", 7 * 86400, "pscreen.win_week")):
            d = u.get(key)
            reset = d.get("reset") if isinstance(d, dict) else None
            ts = parse_reset_ts(reset) if reset else None
            if ts is None or ts <= now:
                continue
            tok = usagelog.window_sum(self._records, ts - span)
            parts.append(i18n.t(name_key, tok=usagelog._fmt_tokens(tok),
                                left=self._fmt_left(ts - now)))
        return [" · ".join(parts)] if parts else []

    @staticmethod
    def _fmt_left_short(sec: int) -> str:
        """리셋까지 남은 시간을 **단일 단위** 초컴팩트 표기로(컬럼 제목용, 요청
        2026-06-20): 90분 미만 'N m', 하루 미만 'N h', 그 이상 'N d'. 분/시/일을
        섞지 않아 'in 87m'·'in 6d' 처럼 칼럼 제목에 짧게 붙는다. 단위 글자는 두
        로케일 공통이라 i18n 불요."""
        mins = max(0, int(sec) // 60)
        if mins < 90:
            return f"{mins}m"
        h = mins // 60
        if h < 24:
            return f"{h}h"
        return f"{h // 24}d"

    def _reset_left(self, key: str):
        """`self._usage[key]`(session|week_all)의 실측 리셋 표기까지 **남은 시간**을
        _fmt_left_short 로 돌려준다. 표기가 없거나 과거(stale 실측)면 None — 호출부가
        제목에 잔여시간을 안 붙이고 맨 '5h%'/'1w%' 만 쓴다. 옛 footer(_boundary_left_line)
        대신 5h%/1w% 칼럼 제목에 inline 으로 보이는 데 쓴다(요청 2026-06-20)."""
        u = self._usage
        if not isinstance(u, dict):
            return None
        import time as _t
        d = u.get(key)
        reset = d.get("reset") if isinstance(d, dict) else None
        ts = parse_reset_ts(reset) if reset else None
        if ts is None:
            return None
        sec = ts - _t.time()
        if sec <= 0:
            return None
        return self._fmt_left_short(sec)

    def _active_tab(self):
        """현재 활성 **상위 뷰 탭** — limit/recon/warn 오버레이가 우선, 아니면 _view.
        §7.1: 단일 '활성 탭' 개념으로 통합해 한도/경고/대사 진입 시 기간 라디오가
        함께 강조되던 충돌을 없애고, 어느 탭이 활성인지 한 곳에서 판정한다."""
        if self._limit_mode:
            return "limit"
        if self._recon_mode:
            return "recon"
        if self._warn_mode:
            return "warn"
        return self._view   # "time" | "session"

    # 활성 상위 뷰 → 그 탭 Label id. 노트북 연결선(_TkTabConnector)이 활성 탭의
    # 화면 구간을 읽어 ▀ 다리를 그릴 때 쓴다. 액션(/usage·시나리오)은 활성 개념이
    # 없어 매핑에 없다(연결선은 항상 뷰 탭 아래에 걸린다).
    _ACTIVE_TAB_WIDGET = {
        "time": "tab_period", "session": "tab_panel",
        "limit": "tab_limit", "recon": "tab_recon", "warn": "tab_warn"}

    def _active_main_tab_widget(self):
        tid = self._ACTIVE_TAB_WIDGET.get(self._active_tab())
        if not tid:
            return None
        try:
            return self.query_one("#" + tid, Label)
        except Exception:
            return None

    def _sync_tabs(self):
        """상위 뷰 탭 라벨·활성 하이라이트(§7.1) + 활성 탭의 보조옵션 줄 표시(§7.2)."""
        try:
            narrow = self.app.size.width < 64
        except Exception:
            narrow = False
        for tid, (full, short) in self._TAB_LABELS.items():
            try:
                self.query_one("#" + tid, Label).update(
                    i18n.t(short) if narrow else i18n.t(full))
            except Exception:
                pass
        active = self._active_tab()
        # 상위 뷰 탭 하이라이트(액션 /usage·시나리오는 상태가 없어 강조 안 함).
        for tid, name in (("tab_period", "time"),
                          ("tab_panel", "session"), ("tab_limit", "limit"),
                          ("tab_recon", "recon"), ("tab_warn", "warn")):
            try:
                self.query_one("#" + tid, Label).set_class(active == name,
                                                           "tkbtab-active")
            except Exception:
                pass
        # 노트북 연결선 다시 그리기 — 활성 탭이 바뀌면 ▀ 다리도 새 탭 아래로 옮긴다.
        try:
            self.query_one("#tkconn").refresh()
        except Exception:
            pass
        # 보조옵션 줄: 기간/세션 뷰에서만 보인다(한도/대사/경고엔 옵션 없음 → 줄 자체를
        # 숨겨 빈 줄이 안 남게). 입도(시간/일/주/월)는 계층 트리로 대체돼 정렬만 남는다.
        show_sub = active in ("time", "session")
        try:
            self.query_one("#tksub").display = show_sub
        except Exception:
            pass
        if show_sub:
            try:
                self.query_one("#tab_order", Label).set_class(
                    self._order == "tokens", "tkbtab-active")
            except Exception:
                pass
            try:
                self.query_one("#tksublead", Label).update(i18n.t("옵션"))
            except Exception:
                pass

    def _metrics(self):
        """현재 폭 티어로 (라벨 셀폭, 막대 칸수)를 정한다(반응형). 막대 칸수는
        DataTable 셀 패딩+세로 스크롤바가 폭을 먹어도 % 가 안 잘리게 여유를 둔다."""
        try:
            w = self.app.size.width
        except Exception:
            w = 0
        if w >= 80:
            return 26, 12
        if w >= 60:
            return 20, 8
        if w >= 44:
            return 15, 4
        return 11, 0

    @staticmethod
    def _tok_aligned(tok, maxdigits):
        """토큰 약식 표기를 '전체(미약식) 자릿수' 기준으로 왼쪽 들여쓴다 — 값이 클수록
        더 왼쪽에서 시작해 한눈에 대소를 비교할 수 있다(사용자 요청). 예: 1.7M(7자리)은
        들여쓰기 0, 5.2k(4자리)은 (maxdigits-4)칸 들여써 작은 값이 오른쪽으로 밀린다.
        약식 문자열은 단위(M/k)로 자릿수를 가려 우측정렬만으로는 대소가 헷갈렸다."""
        s = usagelog._fmt_tokens(tok)
        digits = len(str(int(tok))) if tok else 1
        return " " * max(0, maxdigits - digits) + s

    @staticmethod
    def _tok_bar(tok, vmax, cells, models=None):
        """행 토큰을 표시 최댓값(vmax) 기준 **가로 막대**로 그린다(요청 2026-06-20) —
        토큰 숫자 옆에 둬 행 간 사용량을 한눈에 비교하게. 0/빈 값·폭 부족은 빈 셀.

        `models`({tier: tok})가 있으면 막대를 **모델 구성비로 색 분할**한다(요청
        2026-06-21) — 왼→오 순으로 Haiku/Sonnet/Opus/Fable 색 띠가 토큰 점유만큼
        이어져 한 기간의 모델 비중을 가늠한다. 분해가 없으면 종전대로 단일 톤(cyan)
        — 이 막대는 한도 경고가 아니라 행 사이 **상대 크기** 표시라 임계색(빨/노)을
        쓰지 않아 가장 큰 행이 '위험'으로 오인되지 않게 한다(5h%/1w% 열과 구분)."""
        if not tok or vmax <= 0 or cells <= 0:
            return Text("")
        s = bar(tok, vmax, cells)
        if not models:
            return Text(s, style="cyan", justify="left")
        seq = usagelog._model_cell_sequence(models, len(s))
        t = Text(justify="left")
        for ch, m in zip(s, seq):
            color = (_MODEL_BAR_COLORS.get(m, _MODEL_BAR_COLORS["unknown"])
                     if m else "cyan")
            t.append(ch, style=color)
        return t

    @staticmethod
    def _fmt_date_hdr(d):
        """hour 뷰 날짜 그룹 헤더 라벨: 'YYYY-MM-DD' → 'MM-DD (요일)'. 같은 날짜의
        시각 행들을 이 헤더 아래로 묶는다(요청 2026-06-19). 요일은 day 버킷과 같은
        i18n weekdays(월=0..일=6, datetime.weekday() 와 동순)로 표기."""
        from datetime import datetime
        short = d[5:] if len(d) >= 10 else d        # 'MM-DD'
        try:
            wd = datetime.strptime(d, "%Y-%m-%d").weekday()
            names = i18n.t("pscreen.weekdays").split(",")
            return f"{short} ({names[wd]})"
        except (ValueError, TypeError, IndexError):
            return short

    @staticmethod
    def _trunc(s, cells):
        """문자열을 셀폭 cells 로 자른다(한글 2셀 고려, 넘치면 … 말줄임)."""
        if cells <= 0:
            return ""
        used, out = 0, []
        for ch in s:
            cw = _char_cells(ch)
            if used + cw > cells:
                while out and used + 1 > cells:
                    used -= _char_cells(out[-1])
                    out.pop()
                out.append("…")
                return "".join(out)
            out.append(ch)
            used += cw
        return "".join(out)

    @staticmethod
    def _hourly_spans(hourly_pct):
        """시각별 **누적** 세션 5h%({hour: max_pct})를 '계단식' 막대 구간
        {hour: (start, end)} 로 변환한다(요청 2026-06-17). session_pct 는 5h 창 안에서
        단조 증가하는 누적 점유율이라, 각 시각의 막대를 직전 시각이 끝난 위치(start=직전
        누적%)에서 시작해 이번 누적%(end)까지 그리면 여러 시각에 흩어진 사용이 오른쪽으로
        쌓이는 계단이 된다. 5h 창이 리셋되면 누적%가 **하락**하거나 직전 표본과 **≥5h**
        벌어지므로, 그 시각은 start=0 으로 되돌려 막대가 다시 처음부터 시작한다.

        키는 'YYYY-MM-DD HH:00'(시간순=사전순). 입력 dict 와 무관하게 시간순으로 걷는다."""
        from datetime import datetime
        spans = {}
        prev_pct = None
        prev_dt = None
        for hk in sorted(hourly_pct):
            cur = hourly_pct[hk]
            try:
                cur_dt = datetime.strptime(hk, "%Y-%m-%d %H:00")
            except (ValueError, TypeError):
                cur_dt = None
            reset = (prev_pct is None or cur < prev_pct
                     or (prev_dt is not None and cur_dt is not None
                         and (cur_dt - prev_dt).total_seconds() >= 5 * 3600))
            start = 0 if reset else prev_pct
            spans[hk] = (start, cur)
            prev_pct = cur
            prev_dt = cur_dt
        return spans

    def _lim5h_cell(self, hour_key, cells=0):
        """hour 버킷의 **세션 5h 한도 누적%**(권위 /usage)를 **계단식 가로 막대**로(요청
        2026-06-17). 막대는 직전 시각이 끝난 위치(start)에서 시작해 이번 누적%(end)까지
        채우고(`bar_floating_segments`), 그 **앞쪽 [0, start) 은 같은 색의 연한 톤(dim)**
        으로 채워 이 시각의 누적이 어디서 이어졌는지 한눈에 보이게 한다(요청 2026-06-17).
        5h 창에 흩어진 사용이 시각을 따라 오른쪽으로 쌓이는 계단이 되고 창이 리셋되면 다시
        0 부터 시작한다(구간 계산은 `_hourly_spans`). 분모는 **항상 100%(5h 한도)** 라 시각
        간 절대 점유를 바로 비교할 수 있다. % 숫자(누적값)는 막대 **앞에** 둬 좁아 막대가
        잘려도 항상 보이게 한다. cells<=0(아주 좁은 폭)이면 % 만. 데이터 없으면 '·'.
        ≥80=빨강·≥50=노랑·굵게로 무거운 시각을 눈에 띄게(상태줄 한도 배지와 같은 임계).
        키는 hourly_pct 와 조인."""
        span = self._hourly_span.get(hour_key) if hour_key else None
        if span is None:
            return Text("·", justify="right", style="dim")
        start, pct = span
        color = "red" if pct >= 80 else "yellow" if pct >= 50 else "green"
        style = f"bold {color}" if pct >= 50 else color
        if cells <= 0:
            return Text(f"{pct:>3}%", justify="right", style=style)
        lead, fill = bar_floating_segments(start, pct, 100, cells)
        t = Text(f"{pct:>3}% ", style=style)
        if lead:                       # 선행 [0,start) — 같은 색 연한 톤으로 채움
            t.append("█" * lead, style=f"{color} dim")
        t.append(fill, style=style)
        return t

    def _lim_week_cell(self, hour_key):
        """hour 버킷의 **주간(전체모델) 한도 누적%**(권위 /usage) — 숫자 셀(요청
        2026-06-17, 5h% 옆 1w% 열). 5h% 와 같은 시각 키로 hourly_week_pct 를 조인한다.
        데이터 없으면 '·'. ≥80 빨강·≥50 노랑·굵게(상태줄 한도 배지와 같은 임계)."""
        pct = self._hourly_week_pct.get(hour_key) if hour_key else None
        if pct is None:
            return Text("·", justify="left", style="dim")
        color = "red" if pct >= 80 else "yellow" if pct >= 50 else "green"
        style = f"bold {color}" if pct >= 50 else color
        # 좌측 정렬: 헤더('1w% (in 6d)')가 좌측 정렬이라 데이터도 좌측에 둬 헤더
        # '1w%' 아래에 정렬되게 한다. 우측 정렬이면 넓어진 헤더 폭만큼 데이터가
        # 박스 우측 테두리로 밀려 작은 폭에서 잘렸다(사용자 요청 2026-06-20).
        # 숫자 자체는 `>3` 으로 자릿수 우측 정렬돼 열 간 정렬은 유지된다.
        return Text(f"{pct:>3}%", justify="left", style=style)

    def _refresh_recon(self, chart):
        """[대사] 뷰(S6 T2): 연속 실측 스냅샷 구간의 세션 5h%(실측)를 **시간축 세로
        막대 그래프**로 — 시간에 따른 사용률 증가/리셋(톱니)을 한눈에(요청 2026-06-20,
        예전 표 대신). 좌우 스크롤로 더 이전 구간을 본다. 그래프 위젯은 _ReconChart,
        순수 기하는 usagelog.recon_chart."""
        if chart is not None:
            chart.set_data(self._reconcile)
            chart.focus()
        self.query_one("#tklogtitle", Label).update(
            i18n.t("토큰 5h 사용률 추이 · 시간축"))
        self.query_one("#tktop", Static).update(self._recon_top_line())
        self.query_one("#tkhint", Static).update(
            i18n.t("r집계 표 · ←→/PgUp·PgDn/Home·End 스크롤 · u/usage · Esc닫기"))

    def _recon_top_line(self):
        """[대사] 그래프 상단: 보이는 구간 시간 범위 + 최신 5h% + 안내, 그리고 둘째
        줄에 **모델 색 범례**(요청 2026-06-21 — 막대가 모델 구성비로 색 분할되므로
        어느 색이 어느 모델인지). 데이터에 실제로 나타난 티어만 색 견본과 함께 보인다.
        데이터가 없으면 안내문(빈 그래프)."""
        ivs = self._reconcile
        if not ivs:
            return i18n.t("pscreen.recon_empty")
        import time as _t
        last = ivs[-1]
        rng = "{}→{}".format(
            _t.strftime("%m-%d %H:%M", _t.localtime(ivs[0].get("t0", 0))),
            _t.strftime("%m-%d %H:%M", _t.localtime(last.get("t1", 0))))
        # 기본 텍스트 색은 #tktop 의 CSS(color: $text-muted)에 맡긴다 — 여기서
        # theme_color 를 style 로 주면 'auto 60%'(Textual CSS 식)라 rich 가 못 읽는다.
        line = Text(i18n.t("pscreen.recon_chart_top", rng=rng,
                           pct=last.get("pct1", 0) or 0, n=len(ivs)))
        # 범례: 구간들에 실제로 등장한 모델 티어를 누적해 색 견본(█)과 라벨로.
        present: dict = {}
        for iv in ivs:
            for t, v in (iv.get("models") or {}).items():
                present[t] = present.get(t, 0) + (v or 0)
        leg = self._model_legend(present)
        if leg is not None:
            line.append("\n")
            line.append_text(leg)
        return line

    def _model_legend(self, present):
        """모델 색 범례 Text — present({tier: tok})에서 실제로 등장한(>0) 티어만
        색 견본(█)+라벨로(요청 2026-06-21, 막대 색↔모델 매핑 안내). 정의 순서 먼저,
        그 밖(unknown 등)은 뒤. 등장한 티어가 없으면 None."""
        present = {t: v for t, v in (present or {}).items() if v and v > 0}
        if not present:
            return None
        order = [t for t in usagelog._MODEL_TIER_ORDER if t in present]
        order += [t for t in present if t not in usagelog._MODEL_TIER_ORDER]
        # 라벨 색은 컨테이너(#tktop) CSS 에 맡기고(=text-muted), 견본 칸만 모델 색.
        leg = Text(justify="left")
        for t in order:
            color = _MODEL_BAR_COLORS.get(t, _MODEL_BAR_COLORS["unknown"])
            leg.append("█ ", style=color)
            leg.append(_MODEL_LABELS.get(t, t) + "  ")
        return leg

    def _limit_summary(self):
        """상단 1줄 한도 요약 접두('5h 17% · 주 14% · '). 상세(막대·리셋·계정·창Σ)는
        [한도] 뷰로 옮겼고(작은 화면 정리, 2026-06-14), 기본 화면엔 이 요약만 둔다.
        usage 실측이 없으면 빈 문자열(요약 생략 → scope 만 보인다)."""
        u = self._usage
        if not isinstance(u, dict):
            return ""
        parts = []
        for key, fmt in (("session", "pscreen.lim_5h"),
                         ("week_all", "pscreen.lim_wk")):
            d = u.get(key)
            if isinstance(d, dict) and d.get("pct") is not None:
                parts.append(i18n.t(fmt, p=d["pct"]))
        return (" · ".join(parts) + " · ") if parts else ""

    def _limit_clock_lines(self):
        """가장 이른 /usage 리셋까지 남은 시간을 **큰 블록 글자(HH:MM:SS)** 줄 목록으로
        만든다(<24h). 24h 이상이거나 리셋 파싱 불가면 한 줄 텍스트로 폴백, usage 없으면
        []. 별도 usage-view 팝업(UsageScreen)의 카운트다운을 이 통합 한도 탭으로 옮긴
        것(통합, 사용자 결정 2026-06-17) — 코어 _CLOCK_FONT 를 그대로 재사용한다."""
        u = self._usage
        if not isinstance(u, dict):
            return []
        import time as _t
        now = _t.time()
        best = None                       # (label, ts) — 가장 이른 리셋
        for key, name_key in (("session", "pscreen.reset_session"),
                              ("week_all", "pscreen.reset_week")):
            d = u.get(key)
            reset = d.get("reset") if isinstance(d, dict) else None
            ts = parse_reset_ts(reset) if reset else None
            if ts is None or ts <= now:
                continue
            if best is None or ts < best[1]:
                best = (i18n.t(name_key), ts)
        if best is None:
            return []
        left = int(best[1] - now)
        label = i18n.t("pscreen.next_reset", label=best[0])
        if left >= 86400:                 # 하루 이상이면 블록 시계 대신 텍스트
            return ["", label + " · " + self._fmt_left(left)]
        h, rem = divmod(left, 3600)
        m, s = divmod(rem, 60)
        text = f"{h:02d}:{m:02d}:{s:02d}"
        rows = [""] * _CLOCK_FONT_ROWS
        for i, ch in enumerate(text):
            glyph = _CLOCK_FONT.get(ch, ["   "] * _CLOCK_FONT_ROWS)
            for r in range(_CLOCK_FONT_ROWS):
                if i:
                    rows[r] += " "
                rows[r] += glyph[r]
        # 앞 빈 줄로 막대와 시계를 띄우고, 라벨 + 3줄 블록 글자.
        return ["", label] + rows

    def _refresh_limit(self, table):
        """[한도] 뷰: /usage 한도 상세(세션/주 막대·% 사용·리셋·신선도)와 현재
        5h/주 창 추정 Σ, 그리고 다음 리셋까지의 **카운트다운 시계**를 표 자리에 한 열로
        보여준다 — 예전엔 이 7줄이 표 위 #tktop 에 항상 깔려 작은 화면을 덮었고(사용자
        요청 2026-06-14: 한도 전용 서브뷰로 분리), 카운트다운은 별도 usage-view 팝업에만
        있었다(통합, 사용자 결정 2026-06-17). 막대/창 합은 기존 공유 포맷터
        (_usage_lines·_window_lines)를, 시계는 _limit_clock_lines 를 재사용한다."""
        table.add_column(i18n.t("pscreen.tklog_limit_col"), key="limit")
        if not isinstance(self._usage, dict):
            lines = [i18n.t("pscreen.tklog_limit_empty")]
        else:
            lines = self._usage_lines() + self._window_lines()
            lines += self._limit_clock_lines()
        for ln in lines:
            table.add_row(ln)
        self.query_one("#tklogtitle", Label).update(
            i18n.t("pscreen.tklog_limit_title"))
        self.query_one("#tktop", Static).update("")
        self.query_one("#tkhint", Static).update(
            i18n.t("pscreen.tklog_limit_hint"))

    @staticmethod
    def _warn_info_text(kind, warn=""):
        """경고 **종류(kind)** → (제목, 줄목록). kind 는 서버가 보낸 구조적 신호
        (status.claude_warn_kind: "fmt_unknown"|"repeat"|"long_turn") — 옛 한글
        부분문자열 판별을 대체해 en 로케일에서도 정확히 분류한다(i18n 전수조사
        2026-06-19). 제목·상황·할일 본문은 i18n(ko/en)에서 가져온다. 첫 줄엔 현재
        경고 배지 문자열(warn)을 그대로 둔다(있으면). kind 미상(구버전 서버)이면 남은
        한글 문자열로 한 번 폴백 판별하고, 그래도 모르면 장기 턴 안내."""
        if kind is None and warn:       # 구버전 서버 호환(한글 문자열만 옴) 폴백
            if "포맷" in warn or "미인식" in warn:
                kind = "fmt_unknown"
            elif "반복" in warn or "루프" in warn:
                kind = "repeat"
        key = {"fmt_unknown": "fmt", "repeat": "repeat"}.get(kind, "long")
        title = i18n.t(f"claude.warn_{key}_title")
        body = i18n.t(f"claude.warn_{key}_body").split("\n")
        return title, ([warn, ""] + body if warn else body)

    @staticmethod
    def _warn_badge(kind, warn, n=None):
        """경고 탭 첫 줄에 보일 **로케일 배지 문자열**. 상태줄(clientstatus)과 같은
        규칙: 반복/포맷-미인식은 i18n(ko/en), 장기 턴은 언어중립('⚠ M:SS') 서버 문자열
        유지. kind 미상(구버전 서버)이면 서버 문자열 폴백. en 모드에서 첫 줄이 한글
        서버 문자열로 새던 것 수정(i18n 전수조사 2026-06-19)."""
        if kind == "repeat":
            return i18n.t("claude.warn_repeat_badge", n=n or 0)
        if kind == "fmt_unknown":
            return i18n.t("claude.warn_fmt_badge")
        return warn

    def _refresh_warn(self, table):
        """[경고] 뷰: 상태줄 ⚠ Claude 경고(장기 턴/반복 루프/포맷 미인식)의 상황·할일
        안내를 표 자리에 보여준다 — 예전엔 별도 InfoScreen 팝업이었던 것을 이 통합 팝업의
        탭으로 옮겼다(사용자 결정 2026-06-17, 상태줄 ⚠ 배지 클릭 → 이 탭). 경고 내용은
        클라 status(claude_warn)에서 라이브로 읽어, 경고가 해소되면 '경고 없음' 안내로
        바뀐다(닫지 않고 탭에 머물러도 다음 합성에서 갱신)."""
        table.add_column(i18n.t("pscreen.tklog_warn_col"), key="warn")
        status = getattr(self.app, "status", None)
        warn = getattr(status, "claude_warn", None)
        kind = getattr(status, "claude_warn_kind", None)
        n = getattr(status, "claude_warn_n", None)
        if not warn:
            title = i18n.t("pscreen.tklog_warn_title")
            lines = [i18n.t("pscreen.tklog_warn_empty")]
        else:
            # 첫 줄 배지도 로케일화(en 에서 한글 서버 문자열 누출 방지).
            title, lines = self._warn_info_text(kind, self._warn_badge(kind, warn, n))
        for ln in lines:
            table.add_row(ln)
        self.query_one("#tklogtitle", Label).update(title)
        self.query_one("#tktop", Static).update("")
        self.query_one("#tkhint", Static).update(
            i18n.t("pscreen.tklog_warn_hint"))

    # 기간 뷰 제목/표 헤더용 버킷 단어(i18n 원문 키).
    _BUCKET_WORD = {"hour": "시간", "day": "일", "week": "주", "month": "월"}

    def _view_rows(self):
        """현재 뷰의 표 행 [(라벨, 토큰, 점유%)]·막대 기준(vmax)·표시합(win)·행 헤더
        라벨을 계산한다(표시 전용 — 데이터는 usagelog 순수 집계).

        · time: 시간 버킷 행(일 버킷엔 요일). day/week/month 는 전체 이력 일자 합성
          레코드(_full_recs)로 집계해 옛 버킷이 cap 에 안 잘리고, hour 는 일자
          합성으로 못 만들어 raw _records(최근 N 건이면 충분). 정렬 적용.
        · session: 세션별 합(대표 탭:패널 라벨, 상위 N+기타 접기).
        계정 차원은 제거 — 토큰 사용량은 머신-로컬 기준(2026-06-19)."""
        src = (self._records if self._bucket == "hour"
               else (self._full_recs if self._full_recs is not None
                     else self._records))
        hour_suffix = i18n.t("pscreen.hour_suffix")
        if self._view == "session":
            v = usagelog.agg_view(src, self._bucket, None, "session",
                                  self._order, top=self._GROUP_TOP,
                                  hour_suffix=hour_suffix)
            # 세션 시작 시각(별도 타임스탬프 열용) — _refresh 가 읽는다.
            self._sess_times = v.get("gtimes")
            return (v["groups"], v["gmax"], v["total"], i18n.t("세션"), None,
                    v.get("gmodels"))
        self._sess_times = None
        weekdays = i18n.t("pscreen.weekdays").split(",")
        v = usagelog.agg_view(src, self._bucket, None, "account",
                              self._order, weekdays=weekdays,
                              hour_suffix=hour_suffix)
        # 5번째: 원시 버킷 키(brows 와 동순) — hour 버킷 5h% 열 조인용(§10-D).
        # 6번째: 행별 모델 티어 분해(막대 색 분할용, 요청 2026-06-21).
        return (v["buckets"], v["bmax"], v["total"], i18n.t("기간"),
                v.get("bkeys"), v.get("bmodels"))

    async def _refresh(self):
        self._sync_tabs()
        table = self.query_one(DataTable)
        table.clear(columns=True)
        # [대사] 모드는 표 대신 시간축 그래프(_ReconChart)를 보인다 — 본문 자리를
        # 서로 토글한다(요청 2026-06-20). 다른 모드에선 그래프를 숨겨 표가 차지.
        try:
            chart = self.query_one("#tkchart", _ReconChart)
            chart.display = self._recon_mode
            table.display = not self._recon_mode
        except Exception:
            chart = None
        if self._recon_mode:
            self._refresh_recon(chart)
            return
        # 대사 그래프에서 빠져나오면 포커스를 다시 표로(숨은 그래프에 포커스가 남아
        # 키가 화면까지 안 오던 문제 방지).
        try:
            if not table.has_focus:
                table.focus()
        except Exception:
            pass
        if self._limit_mode:
            self._refresh_limit(table)
            return
        if self._warn_mode:
            self._refresh_warn(table)
            return
        # 기간(time) 뷰 + 시간순 = 계층 타임라인 트리(2026-06-21). 토큰순은 입도가 섞이면
        # 의미가 없어 평탄한 일(day) 목록으로 폴백(아래 _view_rows 경로, SC-8). 세션 뷰도
        # 종전 평탄 경로.
        if self._view == "time" and self._order == "time":
            self._refresh_tree(table)
            return
        label_w, bar_cells = self._metrics()
        rows, vmax, win, rowhdr, bkeys, rmodels = self._view_rows()
        # §10-D: hour 버킷이면 시각별 세션 5h 한도 최대%(권위 /usage)를 별도 열로 보인다.
        # 스크랩 Σ(토큰 열)는 5h 소비를 과소반영하므로 '그 시각 5h 창이 얼마나 찼나'의
        # 진짜 신호다. 5h 비율은 일 단위가 아니라 시간 단위 뷰에 둔다(사용자 결정
        # 2026-06-17) — day/week/month 뷰엔 이 열을 보이지 않는다.
        show5h = (self._bucket == "hour" and self._view != "session"
                  and bool(self._hourly_pct) and bkeys is not None)
        # 1w%(주간 전체모델 한도) 열 — 5h% 옆(사용자 요청 2026-06-17). 5h% 와 같은 hour
        # 버킷 조건 + 주간 데이터가 있을 때만.
        show1w = show5h and bool(self._hourly_week_pct)
        # 5h% 막대 칸수: 8칸으로 캡(0~100% 표현 충분)해 표 가로폭이 박스를 넘지 않게.
        lim_cells = min(bar_cells, 8)
        # 토큰 열: 약식(1.7M·5.2k)은 단위가 자릿수를 가려 우측정렬만으론 대소가
        # 헷갈린다. 표시되는 모든 값의 '전체 자릿수' 최댓값을 기준으로 작은 값을
        # 더 들여써(큰 값일수록 왼쪽에서 시작) 한눈에 비교되게 한다(사용자 요청).
        toks = [t for _, t, _ in rows]
        maxdig = max((len(str(int(t))) for t in toks if t), default=1)
        tok_w = min(11, max(6, max((len(self._tok_aligned(t, maxdig))
                                    for t in toks), default=6)))
        # 라벨 열 폭: _metrics 의 티어 값은 **상한**일 뿐 — 실제 라벨 내용에 맞춰 줄여
        # 라벨↔토큰 사이의 빈 간격을 없앤다(요청 2026-06-18, 'Period 06-18 21h' 처럼
        # 짧은 기간 라벨에서 간격이 컸다). 헤더·가장 긴 라벨이 들어갈 만큼(+1 여백)만
        # 쓰되 티어 폭을 넘지 않고(account 긴 이메일은 종전대로 티어 상한), 행이 있을
        # 때만 적용한다(win==0 빈 안내문은 길어서 티어 폭을 유지해야 안 잘린다).
        # hour+시간순 뷰는 같은 날짜의 시각 행을 **날짜 헤더 아래로 묶는다**(요청
        # 2026-06-19). 토큰순 정렬은 날짜가 섞여 묶음이 무의미하므로 평평한
        # 'MM-DD HHh' 라벨을 그대로 둔다. 묶을 때 시각 행 라벨에서 날짜를 떼고
        # 'HHh' 만 들여쓰며, 날짜는 헤더 행이 인다(5h%/1w% 열은 헤더 행에선 빈다).
        group_dates = (self._bucket == "hour" and self._view != "session"
                       and self._order == "time" and bkeys is not None
                       and bool(rows))
        # 세션 뷰는 시작 시각을 별도 '타임스탬프' 열로 분리한다(세션 | 타임스탬프 |
        # 토큰, 사용자 요청 2026-06-20). 그 외 뷰엔 이 열이 없다.
        show_ts = self._view == "session"
        disp = []   # ("hdr", date_label) | ("row", label, tok, bk, tstr)
        prev_date = None
        for i, (label, tok, pct) in enumerate(rows):
            bk = bkeys[i] if (bkeys is not None and i < len(bkeys)) else None
            tstr = (self._sess_times[i] if (self._sess_times
                    and i < len(self._sess_times)) else "")
            mdl = (rmodels[i] if (rmodels and i < len(rmodels)) else None)
            # 활성 세션 행(세션 뷰 한정): 라벨의 세션 id 가 현재 활성 세션과 같으면.
            active = (self._view == "session"
                      and self._active_session is not None
                      and _session_id_of_label(label) == self._active_session)
            if group_dates and bk:
                d = bk[:10]            # 'YYYY-MM-DD'
                if d != prev_date:
                    prev_date = d
                    disp.append(("hdr", self._fmt_date_hdr(d)))
                label = ("  " + label.split(" ", 1)[1]) if " " in label else label
            disp.append(("row", label, tok, bk, tstr, mdl, active))
        # 타임스탬프 열 폭: 헤더('타임스탬프')와 값('06-20 16:03'·'06-20') 중 넓은 쪽.
        ts_hdr = i18n.t("타임스탬프")
        if show_ts:
            tvals = [t for t in (self._sess_times or []) if t]
            ts_w = max(sum(_char_cells(c) for c in ts_hdr),
                       max((len(t) for t in tvals), default=0)) + 1
        else:
            ts_w = 0
        if rows:
            labels = [str(rowhdr)] + [str(it[1]) for it in disp]
            need = max(sum(_char_cells(c) for c in s) for s in labels) + 1
            if self._view == "session":
                # 세션 뷰는 라벨+타임스탬프+토큰 세 열(5h%/1w% 없음)이라 가로 여유가
                # 크다 → 라벨('세션 N (탭T:pP)')이 안 잘리도록 티어 상한 대신 박스
                # 가용 폭(앱폭·max-width 86 - 타임스탬프열 - 토큰열 - 패딩)까지 넓힌다.
                try:
                    box = min(int(self.app.size.width * 0.96), 86) - 2
                except Exception:
                    box = label_w + ts_w + tok_w
                avail = max(label_w, box - ts_w - tok_w - 2)   # -2: 셀 패딩 여유
                label_w = max(3, min(need, avail))
            else:
                label_w = min(label_w, max(3, need))
        # 토큰 막대 열(요청 2026-06-20): 각 행 토큰을 표시 최댓값(vmax) 기준 **가로
        # 막대**로 그려 행 간 사용량을 즉시 비교한다. hour 뷰의 5h%/1w% 막대 열이 있을
        # 땐(show5h) 가로 여유가 없고 그 열이 이미 시각 비교를 주므로 생략한다. 폭은
        # 박스 본문 잔여 가로폭(테두리·패딩·라벨·타임스탬프·토큰·셀 패딩을 뺀 나머지)
        # 에서 잡고 상한(14칸)을 둔다 — 너무 좁으면(<3칸) 의미가 없어 생략.
        show_bar = (not show5h) and bool(rows) and (vmax or 0) > 0
        bar_w = 0
        if show_bar:
            try:
                box = min(int(self.app.size.width * 0.96), 86)
            except Exception:
                box = label_w + tok_w + 18
            ncols = 3 if show_ts else 2          # 라벨[+ts]+토큰+막대
            # 박스 본문 = box - 테두리(2) - 좌우 패딩(2); 각 열 셀 좌우 패딩 ~2칸.
            avail = (box - 4) - label_w - tok_w - (ts_w if show_ts else 0)
            bar_w = max(0, min(14, avail - (ncols + 1) * 2))
            if bar_w < 3:
                show_bar = False
        # 컬럼: 행 차원(기간/계정/세션) | 토큰(자릿수 정렬, 좌측) | [막대] | [5h%] [1w%].
        # (옛 비율 막대 열은 제거됐고 — 2026-06-17 — 이 가로 비교 막대로 대체.)
        table.add_column(rowhdr, key="label", width=label_w)
        if show_ts:               # 세션 | 타임스탬프 | 토큰 (타임스탬프는 라벨과 토큰 사이)
            table.add_column(Text(ts_hdr, justify="left"), key="ts", width=ts_w)
        table.add_column(Text(i18n.t("토큰"), justify="left"), key="tok",
                         width=tok_w)
        if show_bar:              # 토큰 막대(헤더 없는 시각 비교 열)
            table.add_column(Text("", justify="left"), key="bar", width=bar_w)
        if show5h:
            # 칼럼 제목에 리셋까지 남은 시간을 inline 으로 붙인다(요청 2026-06-20,
            # 별도 footer 줄 제거). 예: '5h% (in 87m)'·'1w% (in 6d)'. 리셋 표기가
            # 없으면(stale/없음) 맨 '5h%'/'1w%'. 폭은 막대/숫자 기본폭과 제목 길이 중
            # 큰 쪽으로 늘려 제목이 안 잘리게 한다.
            l5 = self._reset_left("session")
            hdr5 = i18n.t("pscreen.hdr_5h", left=l5) if l5 else "5h%"
            # 막대를 담을 폭(% 3칸 + '%' + 공백 + 막대 cells). 막대 없으면 % 만(5칸).
            w5 = (lim_cells + 6) if lim_cells else 5
            table.add_column(Text(hdr5, justify="left"), key="lim5h",
                             width=max(w5, len(hdr5) + 1))
            if show1w:                 # 주간 한도% — 숫자 열(3칸+% = 5)
                lw = self._reset_left("week_all")
                hdrw = i18n.t("pscreen.hdr_1w", left=lw) if lw else "1w%"
                table.add_column(Text(hdrw, justify="left"), key="limw",
                                 width=max(5, len(hdrw) + 1))

        if win == 0:               # 선택 뷰/계정 집계 합이 0 (소스 무관)
            empty = [i18n.t("(기록된 토큰 사용량이 없습니다)")]
            if show_ts:
                empty.append("")
            empty.append("")
            if show5h:
                empty.append("")
                if show1w:
                    empty.append("")
            table.add_row(*empty)
        else:
            for it in disp:
                if it[0] == "hdr":
                    # 날짜 그룹 헤더 행(비-데이터): 날짜는 굵게, 나머지 열은 빈다.
                    # (헤더 행은 hour+기간 뷰 전용 → show_ts 와 공존하지 않는다.)
                    cells = [Text(str(it[1]), style="bold"), Text("")]
                    if show_bar:           # 헤더 행엔 막대 없음(빈 칸)
                        cells.append(Text(""))
                    if show5h:
                        cells.append(Text(""))
                        if show1w:
                            cells.append(Text(""))
                    table.add_row(*cells)
                    continue
                _, label, tok, bk, tstr, mdl, active = it
                # 활성 세션 행은 라벨·타임스탬프·토큰을 굵은 오렌지로 강조하고(위치
                # 하이라이트), 막대는 모델색 대신 단색 오렌지로 그려 한눈에 구분(요청
                # 2026-06-21). DataTable 의 커서/줄무늬와 충돌 없이 전경색만으로 강조.
                act_st = (_ACTIVE_SESSION_COLOR + " bold") if active else None
                lbl = self._trunc(label, label_w)
                cells = [Text(lbl, style=act_st) if active else lbl]
                if show_ts:
                    cells.append(Text(tstr, justify="left", style=act_st or ""))
                cells.append(Text(self._tok_aligned(tok, maxdig),
                                  justify="left", style=act_st or ""))
                if show_bar:
                    if active:
                        cells.append(Text(bar(tok, vmax, bar_w),
                                          style=act_st, justify="left"))
                    else:
                        cells.append(self._tok_bar(tok, vmax, bar_w, mdl))
                if show5h:
                    cells.append(self._lim5h_cell(bk, lim_cells))
                    if show1w:
                        cells.append(self._lim_week_cell(bk))
                table.add_row(*cells)

        # 제목: 뷰 차원(시간/일/주/월/세션)별. 스코프/한도(위)·키 안내(아래) 분리.
        order_l = i18n.t("토큰순") if self._order == "tokens" else i18n.t("시간순")
        # (추정): 집계 원천(스크랩 누계)은 활동량 추정 — 실측 한도는 상단 막대(S6 T3).
        what = (i18n.t("세션") if self._view == "session"
                else i18n.t(self._BUCKET_WORD[self._bucket]))
        self.query_one("#tklogtitle", Label).update(
            i18n.t("pscreen.tklog_title2", what=what))
        # Σ: 정확한 전체 이력 합(서버 SQL 집계, Phase B). 계정과 무관한 머신-로컬
        # 전체합(total_all)을 쓰고, 없으면(구버전 서버) 표시 레코드 합으로 폴백.
        # 레코드가 cap 돼 표시 합과 다르면 그 표시 합을 병기.
        life = self._total_all
        if life is None:
            life = win
        sigma = f"~Σ{usagelog._fmt_tokens(life)}"   # ~ = 추정 라벨(S6 T3)
        if life != win:
            sigma += i18n.t("pscreen.tklog_disp", n=usagelog._fmt_tokens(win))
        # 스코프는 1줄로 컴팩트(묶음/버킷은 제목에 이미 있음). 표 높이를 아낀다.
        scope = i18n.t("pscreen.tklog_scope", order=order_l, sigma=sigma)
        # 상단은 1줄(한도 요약 접두 + 스코프)만 — /usage 막대·창Σ·신선도 상세는
        # [한도] 뷰로 옮겼다(작은 화면 정리, 2026-06-14). usage 없으면 접두는 빈 문자열.
        # 막대 색 분할(요청 2026-06-21) 시 둘째 줄에 모델 색 범례를 곁들인다 — 어느
        # 색이 어느 모델인지(표에 등장한 티어만).
        top = Text(self._limit_summary() + scope)   # 색은 #tktop CSS(text-muted)
        present: dict = {}
        if show_bar and rmodels:
            for d in rmodels:
                for t, v in (d or {}).items():
                    present[t] = present.get(t, 0) + (v or 0)
        leg = self._model_legend(present)
        if leg is not None:
            top.append("\n")
            top.append_text(leg)
        self.query_one("#tktop", Static).update(top)
        self.query_one("#tkhint", Static).update(i18n.t("pscreen.tklog_hint"))
        # (옛 footer '5h/1주 경계까지 남은 시간' 한 줄은 제거 — 잔여시간을 5h%/1w%
        # 칼럼 제목에 inline 으로 옮겼다, 요청 2026-06-20.)

    # ── 계층 타임라인 트리(2026-06-21) ──────────────────────────────────────
    # 기간(time) 뷰 + 시간순일 때 표는 월→주→일→시각을 한 트리로 보인다(옛 시간/일/
    # 주/월 서브탭 대체). 기본 펼침 깊이는 시간적 거리로 자동 결정(§3): 오늘=시각까지,
    # 이번 주 지난 날=일, 이번 달 지난 주=주, 이전 달=월. 어떤 행이든 펼치고(▶→▼)
    # 접어(▼→▶) 더 깊은 입도를 본다.

    def _tree_open(self, key, default):
        """행의 effective 펼침 여부 = 기본값 ^ (사용자 토글). 한 집합(_tree_toggled)
        으로 기본 열린 행 접기·기본 닫힌 행 펼치기를 모두 표현한다."""
        return default ^ (key in self._tree_toggled)

    def _build_tree_rows(self):
        """계층 트리 노드 목록을 만든다(표 행과 1:1). 각 노드 dict:
          kind: 'month'|'week'|'day'|'hour'|'divider'
          key: 토글 키(펼침 가능 행만, leaf/divider 는 None)
          label·tokens·models·level(들여쓰기)·expandable·expanded·bk(시각 5h% 조인키)
        반환: (nodes, total) — total=표시 합(중복 없는 일자 전체 합).

        멤버십은 day 인덱스에서 각 날짜의 (월, ISO주)를 파생해 세 구역으로 가른다:
          ① 이번 주의 날 → 최상위 일 행(오늘은 시각까지 기본 펼침),
          ② 이번 달의 지난 주 → 최상위 주 행(펼치면 일·시각),
          ③ 이전 달 → 최상위 월 행(펼치면 주·일·시각).
        각 날짜는 정확히 한 구역에만 들어가 토큰이 중복 집계되지 않는다(가산성 유지).
        시각 입도는 raw _records(최근 N)로만 만들 수 있어 옛 날을 펼치면 시각이 비어
        있을 수 있다(일자 합성 레코드엔 시간 정보가 없다 — 설계 한계)."""
        from datetime import date, datetime
        src = self._full_recs if self._full_recs is not None else self._records
        weekdays = i18n.t("pscreen.weekdays").split(",")
        hour_suffix = i18n.t("pscreen.hour_suffix")
        day_idx = usagelog.agg_index(src, "day", weekdays=weekdays,
                                     hour_suffix=hour_suffix)
        hour_idx = usagelog.agg_index(self._records, "hour",
                                      hour_suffix=hour_suffix)
        try:
            today = date.today()
        except Exception:
            today = None
        today_key = today.strftime("%Y-%m-%d") if today else ""
        this_week = today.strftime("%G-W%V") if today else ""
        this_month = today.strftime("%Y-%m") if today else ""

        def wk_of(d):
            try:
                return datetime.strptime(d, "%Y-%m-%d").strftime("%G-W%V")
            except ValueError:
                return d

        seg_week_days = []      # 이번 주: 일 키 목록
        seg_month = {}          # 이번 달 지난 주: week_key -> [day keys]
        seg_past = {}           # 이전 달: month_key -> {week_key -> [day keys]}
        for d in day_idx:
            wk, mk = wk_of(d), d[:7]
            if wk == this_week:
                seg_week_days.append(d)
            elif mk == this_month:
                seg_month.setdefault(wk, []).append(d)
            else:
                seg_past.setdefault(mk, {}).setdefault(wk, []).append(d)

        nodes = []

        def _hours_of(day_key):
            return sorted((h for h in hour_idx if h[:10] == day_key),
                          reverse=True)

        def emit_hours(day_key, level):
            for hk in _hours_of(day_key):
                e = hour_idx[hk]
                nodes.append({"kind": "hour", "key": None,
                              "label": hk[11:13] + hour_suffix,
                              "tokens": e["tokens"], "models": e["models"],
                              "level": level, "expandable": False,
                              "expanded": False, "bk": hk})

        def emit_day(day_key, level, default_open):
            e = day_idx[day_key]
            key = "day:" + day_key
            has_hours = bool(_hours_of(day_key))
            opened = self._tree_open(key, default_open) if has_hours else False
            nodes.append({"kind": "day", "key": key if has_hours else None,
                          "label": e["label"], "tokens": e["tokens"],
                          "models": e["models"], "level": level,
                          "expandable": has_hours, "expanded": opened,
                          "bk": None})
            if opened:
                emit_hours(day_key, level + 1)

        def emit_week(week_key, days, level, parent_mk):
            key = "week:%s:%s" % (parent_mk, week_key)
            tok = sum(day_idx[d]["tokens"] for d in days)
            models = usagelog._merge_tiers([day_idx[d]["models"] for d in days])
            opened = self._tree_open(key, False)
            nodes.append({"kind": "week", "key": key,
                          "label": "W" + week_key.split("-W", 1)[-1],
                          "tokens": tok, "models": models, "level": level,
                          "expandable": True, "expanded": opened, "bk": None})
            if opened:
                for d in sorted(days, reverse=True):
                    emit_day(d, level + 1, False)

        def emit_month(month_key, weeks_map, level):
            key = "month:" + month_key
            all_days = [d for ds in weeks_map.values() for d in ds]
            tok = sum(day_idx[d]["tokens"] for d in all_days)
            models = usagelog._merge_tiers(
                [day_idx[d]["models"] for d in all_days])
            opened = self._tree_open(key, False)
            nodes.append({"kind": "month", "key": key, "label": month_key,
                          "tokens": tok, "models": models, "level": level,
                          "expandable": True, "expanded": opened, "bk": None})
            if opened:
                for wk in sorted(weeks_map, reverse=True):
                    emit_week(wk, weeks_map[wk], level + 1, month_key)

        def divider(text):
            nodes.append({"kind": "divider", "key": None, "label": text,
                          "tokens": 0, "models": {}, "level": 0,
                          "expandable": False, "expanded": False, "bk": None})

        # ① 이번 주의 날들(최근 위, 오늘은 시각까지 기본 펼침).
        for d in sorted(seg_week_days, reverse=True):
            emit_day(d, 0, default_open=(d == today_key))
        # ② 이번 달의 지난 주(주 행, 기본 접힘).
        if seg_month:
            if seg_week_days:
                divider(i18n.t("pscreen.tree_earlier_weeks"))
            for wk in sorted(seg_month, reverse=True):
                emit_week(wk, seg_month[wk], 0, this_month)
        # ③ 이전 달(월 행, 기본 접힘).
        if seg_past:
            if seg_week_days or seg_month:
                divider(i18n.t("pscreen.tree_earlier_months"))
            for mk in sorted(seg_past, reverse=True):
                emit_month(mk, seg_past[mk], 0)

        total = sum(e["tokens"] for e in day_idx.values())
        return nodes, total

    @staticmethod
    def _tree_label(node):
        """노드 표시 라벨: 들여쓰기(레벨×2칸) + 인디케이터(▼/▶/공백) + 라벨."""
        ind = ("▼" if node["expanded"]
               else ("▶" if node["expandable"] else " "))
        return ("  " * node["level"]) + ind + " " + node["label"]

    def _refresh_tree(self, table):
        """계층 타임라인 트리를 표에 그린다(_refresh 의 time+시간순 경로)."""
        nodes, win = self._build_tree_rows()
        self._tree_nodes = nodes
        label_w_cap, bar_cells = self._metrics()
        data = [n for n in nodes if n["kind"] != "divider"]
        # 시각 행 전용 5h%/1w% 열 — 데이터가 있을 때만(없으면 열 생략). 시각 외 행은 빈칸.
        show5h = bool(self._hourly_pct)
        show1w = show5h and bool(self._hourly_week_pct)
        lim_cells = min(bar_cells, 8)
        toks = [n["tokens"] for n in data]
        vmax = max(toks, default=0)            # 입도 혼재 단일 vmax(§7.2 — 절대 비교)
        maxdig = max((len(str(int(t))) for t in toks if t), default=1)
        tok_w = min(11, max(6, max((len(self._tok_aligned(t, maxdig))
                                    for t in toks), default=6)))
        # 라벨 열 폭: 들여쓰기·인디케이터 포함 렌더 라벨 + 헤더 중 넓은 쪽(+1), 트리는
        # 들여쓰기 여유가 필요해 티어 상한 대신 32셀까지 허용(박스 max-width 86 안).
        rowhdr = i18n.t("기간")
        if nodes:
            labels = [rowhdr] + [self._tree_label(n) for n in nodes]
            need = max(sum(_char_cells(c) for c in s) for s in labels) + 1
            label_w = max(8, min(need, 32))
        else:
            label_w = label_w_cap
        # 5h%/1w% 열 폭(헤더에 리셋 잔여시간 inline).
        l5 = self._reset_left("session")
        hdr5 = i18n.t("pscreen.hdr_5h", left=l5) if l5 else "5h%"
        w5 = (lim_cells + 6) if lim_cells else 5
        w5 = max(w5, len(hdr5) + 1)
        lw = self._reset_left("week_all")
        hdrw = i18n.t("pscreen.hdr_1w", left=lw) if lw else "1w%"
        ww = max(5, len(hdrw) + 1)
        # 토큰 막대 열: 모든 행(시각 비교). 박스 잔여 가로폭에서 잡고 14칸 상한.
        show_bar = bool(data) and vmax > 0
        bar_w = 0
        if show_bar:
            try:
                box = min(int(self.app.size.width * 0.96), 86)
            except Exception:
                box = label_w + tok_w + 18
            used = label_w + tok_w + (w5 if show5h else 0) + (ww if show1w else 0)
            ncols = 2 + (1 if show5h else 0) + (1 if show1w else 0)
            avail = (box - 4) - used
            bar_w = max(0, min(14, avail - (ncols + 1) * 2))
            if bar_w < 3:
                show_bar = False
        # 컬럼.
        table.add_column(rowhdr, key="label", width=label_w)
        table.add_column(Text(i18n.t("토큰"), justify="left"), key="tok",
                         width=tok_w)
        if show_bar:
            table.add_column(Text("", justify="left"), key="bar", width=bar_w)
        if show5h:
            table.add_column(Text(hdr5, justify="left"), key="lim5h", width=w5)
            if show1w:
                table.add_column(Text(hdrw, justify="left"), key="limw",
                                 width=ww)
        if not data:
            empty = [i18n.t("(기록된 토큰 사용량이 없습니다)"), ""]
            if show_bar:
                empty.append("")
            if show5h:
                empty.append("")
                if show1w:
                    empty.append("")
            table.add_row(*empty)
        else:
            for n in nodes:
                if n["kind"] == "divider":
                    cells = [Text(self._trunc(n["label"], label_w),
                                  style="dim"), Text("")]
                    if show_bar:
                        cells.append(Text(""))
                    if show5h:
                        cells.append(Text(""))
                        if show1w:
                            cells.append(Text(""))
                    table.add_row(*cells)
                    continue
                is_hour = n["kind"] == "hour"
                # 월·주 행은 굵게(계층 상위 강조), 일·시각은 기본.
                lstyle = "bold" if n["kind"] in ("month", "week") else None
                lbl = self._trunc(self._tree_label(n), label_w)
                cells = [Text(lbl, style=lstyle) if lstyle else lbl,
                         Text(self._tok_aligned(n["tokens"], maxdig),
                              justify="left")]
                if show_bar:
                    cells.append(self._tok_bar(n["tokens"], vmax, bar_w,
                                               n["models"]))
                if show5h:
                    cells.append(self._lim5h_cell(n["bk"], lim_cells)
                                 if is_hour else Text(""))
                    if show1w:
                        cells.append(self._lim_week_cell(n["bk"])
                                     if is_hour else Text(""))
                table.add_row(*cells)

        # 제목·스코프·범례·힌트(평탄 경로와 동형).
        order_l = i18n.t("시간순")
        self.query_one("#tklogtitle", Label).update(
            i18n.t("pscreen.tklog_title2", what=i18n.t("기간")))
        life = self._total_all
        if life is None:
            life = win
        sigma = f"~Σ{usagelog._fmt_tokens(life)}"
        if life != win:
            sigma += i18n.t("pscreen.tklog_disp", n=usagelog._fmt_tokens(win))
        scope = i18n.t("pscreen.tklog_scope", order=order_l, sigma=sigma)
        top = Text(self._limit_summary() + scope)
        present: dict = {}
        if show_bar:
            for n in data:
                for t, v in (n["models"] or {}).items():
                    present[t] = present.get(t, 0) + (v or 0)
        leg = self._model_legend(present)
        if leg is not None:
            top.append("\n")
            top.append_text(leg)
        self.query_one("#tktop", Static).update(top)
        self.query_one("#tkhint", Static).update(i18n.t("pscreen.tklog_hint"))

    def _tree_toggle_at(self, row, mode="toggle"):
        """표 커서 행(row)에 대응하는 트리 노드를 펼치/접는다(키·클릭 공통).
        mode='toggle'|'expand'|'collapse'. 펼침 불가 행(leaf/divider)·범위 밖이면 무동작.
        토글 후 _refresh 로 다시 그리고 커서를 같은 행에 유지한다. 동작했으면 True."""
        nodes = self._tree_nodes
        if not (0 <= row < len(nodes)):
            return False
        n = nodes[row]
        if not n["expandable"] or not n["key"]:
            return False
        cur_open = n["expanded"]
        if mode == "expand" and cur_open:
            return False
        if mode == "collapse" and not cur_open:
            return False
        # 기본값 대비 토글: key 가 토글집합에 있으면 빼고 없으면 넣어 effective 를 뒤집는다.
        if n["key"] in self._tree_toggled:
            self._tree_toggled.discard(n["key"])
        else:
            self._tree_toggled.add(n["key"])
        return True

    async def _tree_apply(self, row):
        """토글 반영(다시 그리기) 후 커서를 row 에 복원."""
        await self._refresh()
        try:
            table = self.query_one(DataTable)
            n = table.row_count
            if n:
                table.move_cursor(row=max(0, min(n - 1, row)))
        except Exception:
            pass

    def _exit_body_modes(self):
        """표를 대체하는 뷰(대사/한도/경고)에서 빠져나온다 — 기간/계정/세션/정렬 동작이
        어느 모드에서 눌려도 곧바로 먹게 한다. 하나라도 켜져 있었으면 True."""
        was = self._limit_mode or self._recon_mode or self._warn_mode
        self._limit_mode = False
        self._recon_mode = False
        self._warn_mode = False
        return was

    def on_click(self, event: events.Click):
        # 마우스 클릭: 닫기 [x]·서브탭(버킷)·동작 버튼을 위젯 id 로 분기한다. 박스
        # (#tklogbox) 바깥(백드롭)을 클릭/터치하면 팝업을 닫는다(InfoScreen·토큰
        # 팝업 공통 동선 — §10 #13).
        w = getattr(event, "widget", None)
        wid = None
        inside_box = False
        while w is not None:
            this = getattr(w, "id", None)
            if this:
                if wid is None:
                    wid = this          # 가장 안쪽의 의미 있는 id(분기용)
                if this == "tklogbox":
                    inside_box = True
            w = w.parent
        if wid == "tklogclose":
            event.stop()
            self.dismiss(None)
        elif wid == "tab_period":
            event.stop()
            # 기간(계층 타임라인) 뷰로 전환 — 정렬은 하위 보조옵션 줄에서 고른다(§7.2).
            # 한도/대사/경고 등 어느 탭에서든 클릭하면 기간 뷰로 복귀(§7.1).
            self._exit_body_modes()
            self._view = "time"
            self.run_worker(self._refresh())
        elif wid == "tab_panel":
            event.stop()
            # 세션 뷰 토글(행=세션별 합). 뷰 전환 시 트리 펼침 상태 초기화(§3 재진입).
            was = self._exit_body_modes()
            self._view = "session" if was or self._view != "session" else "time"
            self._tree_toggled.clear()
            self.run_worker(self._refresh())
        elif wid == "tab_order":
            event.stop()
            # 정렬 토글: 시간순(계층 트리) ↔ 토큰순(평탄 일 목록). 전환 시 트리 펼침
            # 상태 초기화 — 토큰순은 평탄이라 무의미하고, 시간순 복귀 시 §3 기본으로(T9).
            self._exit_body_modes()
            self._order = "tokens" if self._order == "time" else "time"
            self._tree_toggled.clear()
            self.run_worker(self._refresh())
        elif wid == "tab_limit":
            event.stop()
            # 한도 상세 뷰 토글(표 자리에 /usage 막대·창Σ). recon/경고 와 배타.
            self._limit_mode = not self._limit_mode
            if self._limit_mode:
                self._recon_mode = self._warn_mode = False
            self.run_worker(self._refresh())
        elif wid == "tab_usage":
            event.stop()
            self.app.send_cmd("refresh_usage")
            self.query_one("#tklogtitle", Label).update(i18n.t("/usage 조회 중… (~수초)"))
        elif wid == "tab_saver":
            event.stop()
            self.app.push_screen(ClaudeSaverScreen())
        elif wid == "tab_recon":
            event.stop()
            # S6 T2: 집계 ↔ 대사(실측Δ% vs 추정Σ) 뷰 토글. 한도/경고 뷰와 배타.
            self._recon_mode = not self._recon_mode
            if self._recon_mode:
                self._limit_mode = self._warn_mode = False
            self.run_worker(self._refresh())
        elif wid == "tab_warn":
            event.stop()
            # 경고(장기 턴/반복/포맷) 안내 뷰 토글. 한도/대사 뷰와 배타. 상태줄 ⚠
            # 배지 클릭이 여는 통합 탭(2026-06-17) — 탭 버튼 클릭으로도 토글된다.
            self._warn_mode = not self._warn_mode
            if self._warn_mode:
                self._limit_mode = self._recon_mode = False
            self.run_worker(self._refresh())
        elif not inside_box:
            # 박스 바깥(백드롭) 클릭/터치 → 팝업 닫기.
            event.stop()
            self.dismiss(None)

    async def on_key(self, event: events.Key):
        k = event.key
        # [대사] 그래프 모드: 방향키·Page·Home·End 로 시간축을 좌우 스크롤한다(요청
        # 2026-06-20 — 더 이전 구간 보기). 왼쪽/위=더 옛, 오른쪽/아래=더 새.
        if self._recon_mode and k in (
                "left", "right", "up", "down", "pageup", "pagedown",
                "home", "end"):
            event.stop()
            try:
                chart = self.query_one("#tkchart", _ReconChart)
            except Exception:
                return
            if k in ("left", "up"):
                chart.scroll_intervals(1)
            elif k in ("right", "down"):
                chart.scroll_intervals(-1)
            elif k == "pageup":
                chart.scroll_intervals(chart._cap)
            elif k == "pagedown":
                chart.scroll_intervals(-chart._cap)
            elif k == "home":
                chart.scroll_intervals(10 ** 6)   # 가장 옛(클램프)
            elif k == "end":
                chart.scroll_intervals(-10 ** 6)  # 가장 새(클램프)
            return
        # 계층 트리(기간 뷰 + 시간순): Enter/space 로 펼침·접힘 토글, →/← 로 펼침/접힘.
        # up/down 은 DataTable 행 커서에 위임. (마우스 클릭은 RowSelected →
        # on_data_table_row_selected 도 토글한다.) Enter 를 여기서 가로채지 않으면 화면
        # on_key 의 '그 외 키=닫기' 폴백에 걸려 팝업이 닫힌다.
        tree_active = (self._view == "time" and self._order == "time"
                       and not (self._limit_mode or self._recon_mode
                                or self._warn_mode))
        if tree_active and k in ("left", "right", "space", "enter"):
            event.stop()
            try:
                row = self.query_one(DataTable).cursor_coordinate.row
            except Exception:
                return
            mode = ("expand" if k == "right"
                    else "collapse" if k == "left" else "toggle")
            if self._tree_toggle_at(row, mode):
                await self._tree_apply(row)
            return
        # h/d/w/m: 옛 입도 서브탭 단축키 — 계층 트리로 대체돼 더는 입도를 바꾸지 않는다.
        # 흔한 글자라 팝업이 닫히지 않게 소비만 하고 무동작으로 둔다(예약).
        if k in ("h", "d", "w", "m"):
            event.stop()
            return
        if k == "p":
            event.stop()
            # 세션 뷰 토글(행=세션별 합). 뷰 전환 시 트리 펼침 상태 초기화(§3 재진입).
            was = self._exit_body_modes()
            self._view = "session" if was or self._view != "session" else "time"
            self._tree_toggled.clear()
            await self._refresh()
            return
        if k == "l":
            event.stop()
            # 한도 상세 뷰 토글(표 자리에 /usage 막대·창Σ·계정·신선도). recon 과 배타.
            self._limit_mode = not self._limit_mode
            if self._limit_mode:
                self._recon_mode = False
            await self._refresh()
            return
        if k == "o":
            event.stop()
            # 정렬 토글: 시간순(계층 트리) ↔ 토큰순(평탄 일 목록). 전환 시 트리 펼침
            # 상태 초기화(토큰순 평탄·시간순 복귀 시 §3 기본, T9).
            self._exit_body_modes()
            self._order = "tokens" if self._order == "time" else "time"
            self._tree_toggled.clear()
            await self._refresh()
            return
        if k == "s":
            event.stop()
            # M18-C: 사용량 통계에서 바로 과사용 완화 시나리오 on/off 로(§9.4).
            self.app.push_screen(ClaudeSaverScreen())
            return
        if k == "r":
            event.stop()
            # S6 T2: 집계 ↔ 대사(실측Δ% vs 추정Σ) 뷰 토글. 한도 뷰와 배타.
            self._recon_mode = not self._recon_mode
            if self._recon_mode:
                self._limit_mode = False
            await self._refresh()
            return
        if k == "u":
            event.stop()
            # M19: 그림자 /usage 갱신 요청. 결과는 status 로 와 다음 열람부터 반영.
            self.app.send_cmd("refresh_usage")
            self.query_one("#tklogtitle", Label).update(i18n.t("/usage 조회 중… (~수초)"))
            return
        if k in ("home", "end", "pageup", "pagedown"):
            # 표의 **하이라이트 행 커서**를 직접 옮긴다(사용자 요청 2026-06-18). 기본
            # DataTable 바인딩은 이 키들로 행 커서를 따라 움직여 주지 않아(스크롤만/무동작)
            # 하이라이트가 안 따라온다는 보고 — 명시적으로 move_cursor 로 행을 옮긴다.
            # up/down 은 DataTable 기본 동작이 정상이라 그대로 위임(아래 _NAV_KEYS).
            event.stop()
            try:
                table = self.query_one(DataTable)
            except Exception:
                return
            n = table.row_count
            if n:
                cur = table.cursor_coordinate.row
                if k == "home":
                    target = 0
                elif k == "end":
                    target = n - 1
                else:
                    # 한 페이지 = 보이는 데이터 행 수(헤더 1줄 제외). 최소 1.
                    page = max(1, table.size.height - 1)
                    target = cur + (page if k == "pagedown" else -page)
                target = max(0, min(n - 1, target))
                table.move_cursor(row=target)
            return
        if k in self._NAV_KEYS:
            # 스크롤/커서(up/down)는 포커스된 DataTable 이 자체 처리 — 가로채지 않는다.
            return
        if k == "escape":
            event.stop()
            self.dismiss(None)
            return
        # 그 외 키는 닫는다(기존 동작).
        event.stop()
        self.dismiss(None)

    async def on_data_table_row_selected(self, event):
        """행 선택(Enter·마우스 클릭) → 계층 트리에선 그 행을 펼치/접는다. 그 외
        뷰(세션/토큰순/한도/대사/경고)에선 행 선택이 아무 동작도 하지 않는다(종전)."""
        if not (self._view == "time" and self._order == "time"):
            return
        if self._limit_mode or self._recon_mode or self._warn_mode:
            return
        row = getattr(event, "cursor_row", None)
        if row is None:
            return
        if self._tree_toggle_at(row, "toggle"):
            await self._tree_apply(row)
