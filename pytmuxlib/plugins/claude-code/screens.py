"""claude-code 플러그인의 모달 화면 — 시작 규칙 편집(RulesEditScreen)·토큰 절감
설정(ClaudeSaverScreen). client.py 의 clientscreens 에서 이리로 이전.

textual 의존이 있어 이 모듈은 **실제로 팝업을 열 때** 지연 import 된다(플러그인 __init__
은 가벼움 — 서버 프로세스도 plugins.load() 로 읽기 때문)."""
from __future__ import annotations

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (DataTable, Input, Label, ListItem, ListView,
                             Static, TextArea)

from rich.text import Text

from pytmuxlib import i18n
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
        "시나리오", "대사", "한도", "기간", "보기", "조회", "토큰 사용량",
        "구간", "실측(세션 5h)", "추정Σ", "항목", "토큰", "비율",
        "세션", "토큰순", "시간순", "옵션",
        # token-log: 안내/대사
        "한도(/usage): [u] 눌러 조회", "(기록된 토큰 사용량이 없습니다)",
        "토큰 대사 · 실측Δ% vs 추정Σ", "/usage 조회 중… (~수초)",
        "r집계로 돌아가기 · u/usage 갱신 · Esc닫기",
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
        "정렬": "Sort", "정": "S", "시나리오": "Scenario", "대사": "Recon",
        "한도": "Limit",
        "기간": "Period", "보기": "View", "조회": "Query", "토큰 사용량": "Token usage",
        "구간": "Span", "실측(세션 5h)": "Measured (session 5h)", "추정Σ": "Est Σ",
        "항목": "Item", "토큰": "Tokens", "비율": "Ratio",
        "세션": "Session", "토큰순": "by tokens", "시간순": "by time",
        "옵션": "Options",
        "한도(/usage): [u] 눌러 조회": "Limit (/usage): press [u] to query",
        "(기록된 토큰 사용량이 없습니다)": "(no recorded token usage)",
        "토큰 대사 · 실측Δ% vs 추정Σ": "Token reconcile · measured Δ% vs est Σ",
        "/usage 조회 중… (~수초)": "querying /usage… (~a few s)",
        "r집계로 돌아가기 · u/usage 갱신 · Esc닫기":
            "r back to totals · u refresh /usage · Esc close",
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
        "pscreen.tklog_scope": "계정 {acct} · {order} · {sigma}",
        "pscreen.tklog_disp": " (표시 {n})",
        "pscreen.tklog_hint": "h시 d일 w주 m월 · c계정 p세션 · l한도 a필터 o정렬 · u/usage s설정 r대사 · Esc닫기",
        "pscreen.weekdays": "월,화,수,목,금,토,일",
        "pscreen.recon_top": "실측(/usage Δ%)과 추정(스크랩 ~Σ)은 의미가 다른 두 출처 — 절대 일치가 아니라 추세 상관을 봅니다",
        "pscreen.recon_empty": "(대사할 실측 스냅샷 구간이 없습니다 — /usage 실측이 2회 이상 쌓이면 생깁니다)",
        "pscreen.acct_all": "전체",
        "pscreen.fold_tag": "미식별 포함",
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
    },
    "en": {
        "pscreen.perm_title": "Select permission mode (current: {current})",
        "pscreen.tklog_title2": "Token usage (est) · by {what}",
        "pscreen.tklog_scope": "Account {acct} · {order} · {sigma}",
        "pscreen.tklog_disp": " (shown {n})",
        "pscreen.tklog_hint": "h hour d day w week m month · c account p session · l limit a filter o sort · u /usage s settings r recon · Esc close",
        "pscreen.weekdays": "Mo,Tu,We,Th,Fr,Sa,Su",
        "pscreen.recon_top": "Measured (/usage Δ%) and est (scrape ~Σ) are two different sources — look at trend correlation, not exact match",
        "pscreen.recon_empty": "(no measured snapshot spans to reconcile — appears once /usage measurements accumulate 2+)",
        "pscreen.acct_all": "All",
        "pscreen.fold_tag": "incl. unidentified",
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
                                  format_option_row, _CLOCK_FONT)
from pytmuxlib.clientscreens import usage_bar_lines


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
    /* 보조옵션 줄: 활성 탭(기간/세션)의 입도·정렬을 살짝 들여써 상위 탭과 구분. */
    #tksub { width: 100%; height: 1; align-horizontal: left; padding: 0 0 0 2; }
    #tksub Label { height: 1; padding: 0 1; margin: 0 1 0 0; }
    #tksub_period { width: auto; height: 1; }
    #tksublead { color: $text-muted; padding: 0 1 0 0; margin: 0; }
    .tkbtab { background: $surface; color: $text-muted; }       /* 뷰 탭·옵션(비활성) */
    .tkbtab-active { background: $accent; color: $text; text-style: bold; } /* 활성 */
    .tkbbtn { background: $primary-darken-2; color: $text; }     /* 액션 버튼(뷰 아님) */
    /* 스코프/한도(위)·키 안내(아래)는 옅게 1~몇 줄, 표가 남는 높이를 채운다. */
    #tktop { width: 100%; height: auto; color: $text-muted; }
    #tkhint { width: 100%; height: 1; color: $text-muted; }
    #tktable { width: 100%; height: 1fr; }
    """
    _NAV_KEYS = ("up", "down", "pageup", "pagedown", "home", "end")
    _BUCKETS = {"h": "hour", "d": "day", "w": "week", "m": "month"}
    # 서브탭 위젯 id → 버킷.
    _TAB_BUCKET = {"tab_hour": "hour", "tab_day": "day",
                   "tab_week": "week", "tab_month": "month"}
    # 탭 라벨(넓은 폭, 좁은 폭). 좁으면 한 글자로 줄여 모바일에서 탭 줄이 안 넘치게(P6).
    _TAB_LABELS = {
        "tab_period": ("기간", "기"),
        "tab_hour": ("시간", "시"), "tab_day": ("일", "일"),
        "tab_week": ("주", "주"), "tab_month": ("월", "월"),
        "tab_acct": ("계정", "계"), "tab_panel": ("세션", "세"),
        "tab_order": ("정렬", "정"), "tab_usage": ("/usage", "U"),
        "tab_saver": ("시나리오", "S"), "tab_recon": ("대사", "R"),
        "tab_limit": ("한도", "L"), "tab_warn": ("경고", "경"),
    }
    # [패널]/계정 그룹이 많을 때 상위 N + '기타'로 접어 길이 폭주를 막는다(설계 §4).
    _GROUP_TOP = 8

    def __init__(self, records, usage=None, total_all=None, accounts_total=None,
                 daily=None, reconcile=None, daily_pct=None, hourly_pct=None,
                 initial_mode=None):
        super().__init__()
        self._records = records or []
        # §10-D: 세션 5h 한도 최대%(권위 /usage). 스크랩 Σ 가 5h 소비를 과소집계하므로
        # 사용량 뷰가 '얼마나 썼나'를 이 권위값으로 보인다. daily_pct=일자별(레거시
        # 유지), hourly_pct=시각('YYYY-MM-DD HH:00')별 — 5h 비율은 일 단위가 아니라
        # **시간 단위 뷰**에 두는 게 의미상 맞다는 사용자 결정(2026-06-17)으로, 표의
        # '5h%' 열은 이제 hour 버킷에서 hourly_pct 로 보인다(None/구버전 서버 → 열 생략).
        self._daily_pct = daily_pct or {}
        self._hourly_pct = hourly_pct or {}
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
        self._accounts_total = accounts_total or {}
        # §5.5 단일 식별 계정 귀속(표시층): 이력 전체에서 식별(이메일) 계정이 정확히
        # 하나면 미식별(unknown) 레코드·합계를 그 계정에 귀속해 보여 준다 — 패널
        # 화면엔 계정 라벨이 거의 안 떠 레코드 대부분이 unknown 으로 적재되는데,
        # 'unknown 86%' 는 정보가 아니라 소음이었고 계정 필터를 걸면 데이터가 있는
        # 날이 통째로 사라져 보였다(사용자 보고). reconcile·서버 단일계정 합산과
        # 동일 가정. 식별 계정이 둘 이상이면 귀속이 모호하므로 접지 않는다.
        cand = set(self._accounts_total)
        for r in (self._full_recs if self._full_recs is not None
                  else self._records):
            cand.add(r.get("account") or usagelog.UNKNOWN)
        self._fold_acct = usagelog.fold_target(cand)
        if self._fold_acct:
            self._records = usagelog.fold_unknown(self._records,
                                                  self._fold_acct)
            if self._full_recs is not None:
                self._full_recs = usagelog.fold_unknown(self._full_recs,
                                                        self._fold_acct)
            if usagelog.UNKNOWN in self._accounts_total:
                at = dict(self._accounts_total)
                at[self._fold_acct] = (at.get(self._fold_acct, 0)
                                       + at.pop(usagelog.UNKNOWN))
                self._accounts_total = at
        self._bucket = "day"
        # 표 차원(2026-06-12 재설계 — 한 번에 한 차원만): "time"=시간 버킷(기본),
        # "account"=계정별 전체 합, "session"=세션별 합. 세션은 닫히고 재사용되는
        # 패널 id 대신 안정적 세션 id 로 묶는다(설계 §8, 사용자 결정).
        self._view = "time"
        self._acct_rows = []   # 계정 뷰 행 인덱스 → 계정(행 선택 드릴다운용)
        # 버킷(시간축) 정렬: "time"=최근 위(기본), "tokens"=많이 쓴 순([o] 토글).
        self._order = "time"
        # 계정 필터 순환 목록: None(전체) + 등장 계정들(정렬). 전체 이력 기준이라
        # accounts_total(서버 전이력 계정합) 키 ∪ 받은 레코드 계정으로 모은다 —
        # 옛 계정도 필터에서 고를 수 있게(과거엔 capped _records 에 든 계정만 보였다).
        seen = set(self._accounts_total)
        for r in (self._full_recs or self._records):
            seen.add(r.get("account") or usagelog.UNKNOWN)
        seen.discard(None)
        self._accounts = [None] + sorted(seen)
        self._ai = 0

    @property
    def _account(self):
        return self._accounts[self._ai]

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
            with Horizontal(id="tktabs"):
                yield Label("기간", id="tab_period", classes="tkbtab",
                            markup=False)
                yield Label("계정", id="tab_acct", classes="tkbtab",
                            markup=False)
                yield Label("세션", id="tab_panel", classes="tkbtab",
                            markup=False)
                yield Label("한도", id="tab_limit", classes="tkbtab",
                            markup=False)
                yield Label("대사", id="tab_recon", classes="tkbtab",
                            markup=False)
                yield Label("경고", id="tab_warn", classes="tkbtab",
                            markup=False)
                yield Label("/usage", id="tab_usage", classes="tkbbtn",
                            markup=False)
                yield Label("시나리오", id="tab_saver", classes="tkbbtn",
                            markup=False)
            # 보조옵션 줄: **활성 탭에서만 작동하는 옵션**을 탭 하위로(§7.2). 기간 뷰=
            # 입도(시간/일/주/월)+정렬, 세션 뷰=정렬. 그 외(계정/한도/대사/경고)엔
            # 숨긴다(_sync_tabs 가 display 제어). 계정 필터는 'a' 키로 순환(기간/세션).
            with Horizontal(id="tksub"):
                yield Label("", id="tksublead", markup=False)
                with Horizontal(id="tksub_period"):
                    yield Label("시간", id="tab_hour", classes="tkbtab",
                                markup=False)
                    yield Label("일", id="tab_day", classes="tkbtab",
                                markup=False)
                    yield Label("주", id="tab_week", classes="tkbtab",
                                markup=False)
                    yield Label("월", id="tab_month", classes="tkbtab",
                                markup=False)
                yield Label("정렬", id="tab_order", classes="tkbtab",
                            markup=False)
            yield Static("", id="tktop", markup=False)
            table = DataTable(id="tktable", zebra_stripes=True,
                              cursor_type="row")
            table.can_focus = True
            yield table
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
        return usage_bar_lines(self._usage, w, age_sec=age) \
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
            acct = self._account          # 현재 계정 필터를 그대로 따른다
            tok = usagelog.window_sum(self._records, ts - span, account=acct)
            parts.append(i18n.t(name_key, tok=usagelog._fmt_tokens(tok),
                                left=self._fmt_left(ts - now)))
        return [" · ".join(parts)] if parts else []

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
        return self._view   # "time" | "account" | "session"

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
        for tid, name in (("tab_period", "time"), ("tab_acct", "account"),
                          ("tab_panel", "session"), ("tab_limit", "limit"),
                          ("tab_recon", "recon"), ("tab_warn", "warn")):
            try:
                self.query_one("#" + tid, Label).set_class(active == name,
                                                           "tkbtab-active")
            except Exception:
                pass
        # 보조옵션 줄: 기간/세션 뷰에서만 보인다(계정/한도/대사/경고엔 옵션 없음 →
        # 줄 자체를 숨겨 빈 줄이 안 남게). 기간 뷰=입도+정렬, 세션 뷰=정렬.
        show_sub = active in ("time", "session")
        try:
            self.query_one("#tksub").display = show_sub
        except Exception:
            pass
        if show_sub:
            try:
                self.query_one("#tksub_period").display = (active == "time")
            except Exception:
                pass
            for tid, bucket in self._TAB_BUCKET.items():
                try:
                    self.query_one("#" + tid, Label).set_class(
                        active == "time" and bucket == self._bucket,
                        "tkbtab-active")
                except Exception:
                    pass
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

    def _barcell(self, tok, vmax, pct, cells):
        """% + 막대 셀 문자열. % 를 **앞에** 둔다 — 스크롤바/패딩이 폭을 먹어 셀
        오른쪽이 잘려도 핵심 숫자(%)는 항상 보이고 막대 꼬리만 잘린다. cells<=0 이면 % 만."""
        p = f"{pct:>3}%"
        if cells <= 0:
            return p
        return f"{p} {bar(tok, vmax, cells)}"

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

    def _refresh_recon(self, table):
        """[대사] 뷰(S6 T2): 연속 실측 스냅샷 구간마다 실측 Δ%(세션 5h)와 그 구간의
        스크랩 추정 Σ를 나란히 — 두 출처의 추세 상관을 눈으로 확인하는 진단 표.
        절대 일치는 기대하지 않는다(의미가 다른 두 수치 — 시나리오 §0-1)."""
        rows = usagelog.recon_view(self._reconcile)
        table.add_column(i18n.t("구간"), key="span", width=25)
        table.add_column(i18n.t("실측(세션 5h)"), key="meas", width=16)
        table.add_column(i18n.t("추정Σ"), key="est", width=8)
        table.add_column(i18n.t("계정"), key="note", width=16)
        if not rows:
            table.add_row(i18n.t("pscreen.recon_empty"), "", "", "")
        else:
            for span, meas, est, note in rows:
                table.add_row(span, meas, est, self._trunc(note, 16))
        self.query_one("#tklogtitle", Label).update(
            i18n.t("토큰 대사 · 실측Δ% vs 추정Σ"))
        self.query_one("#tktop", Static).update(i18n.t("pscreen.recon_top"))
        self.query_one("#tkhint", Static).update(
            i18n.t("r집계로 돌아가기 · u/usage 갱신 · Esc닫기"))

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
        rows = ["", "", "", "", ""]
        for i, ch in enumerate(text):
            glyph = _CLOCK_FONT.get(ch, ["   "] * 5)
            for r in range(5):
                if i:
                    rows[r] += " "
                rows[r] += glyph[r]
        # 앞 빈 줄로 막대와 시계를 띄우고, 라벨 + 5줄 블록 글자.
        return ["", label] + rows

    def _refresh_limit(self, table):
        """[한도] 뷰: /usage 한도 상세(세션/주 막대·% 사용·리셋·계정·신선도)와 현재
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
            if self._fold_acct:
                lines.append(i18n.t("pscreen.fold_tag"))
            lines += self._limit_clock_lines()
        for ln in lines:
            table.add_row(ln)
        self.query_one("#tklogtitle", Label).update(
            i18n.t("pscreen.tklog_limit_title"))
        self.query_one("#tktop", Static).update("")
        self.query_one("#tkhint", Static).update(
            i18n.t("pscreen.tklog_limit_hint"))

    @staticmethod
    def _warn_info_text(warn):
        """상태줄 Claude 경고 문자열 → (제목, 줄목록). 경고 종류(포맷 미인식/반복 루프/
        장기 턴)를 문구로 판별해 상황·할일 안내를 만든다 — 옛 _open_warn_info 의
        InfoScreen 내용을 이 통합 '경고' 탭이 그대로 재사용하도록 추출(2026-06-17)."""
        lines = [warn, ""]
        if "포맷 미인식" in warn:
            title = "Claude 포맷 미인식"
            lines += [
                "[상황]",
                "• pytmux 가 Claude Code 화면 형식을 인식하지 못합니다.",
                "• 토큰/사용량 추적과 자동화(자동 재개·자동 압축·한도 게이트)가 멈춥니다.",
                "• Claude Code 자체 동작(입력·출력)에는 영향이 없습니다.",
                "• 보통 Claude Code 버전 업데이트로 화면 구조가 바뀌면 발생합니다.",
                "",
                "[할일]",
                "• 화면이 다시 정상 인식되면 경고는 자동으로 사라집니다(잠시 대기).",
                "• 계속되면 pytmux 의 Claude 파서(claude.py)를 새 포맷에 맞춰 갱신해야 합니다.",
                "• REC 캡처가 켜져 있으면 captures/ 로그로 새 포맷을 분석할 수 있습니다.",
            ]
        elif "반복" in warn or "루프" in warn:
            title = "Claude 반복 루프 의심"
            lines += [
                "[상황]",
                "• 같은 출력이 여러 번 반복됐습니다 — 루프 의심(경고만, 자동 개입 없음).",
                "",
                "[할일]",
                "• 진행이 없으면 다른 지시를 주거나 /clear 후 다시 시도하세요.",
                "• 임계는 옵션 claude_repeat_alert 로 조정/끌 수 있습니다(0=끔).",
            ]
        else:   # 장기 턴(⚠ M:SS) 등
            title = "Claude 장기 턴"
            lines += [
                "[상황]",
                "• 현재 Claude 턴이 임계 시간을 넘겨 오래 진행 중입니다(경고만, 자동 개입 없음).",
                "",
                "[할일]",
                "• 정상적인 긴 작업일 수 있습니다. 멈춘 듯하면 패널에서 Esc 로 중단하세요.",
                "• 임계는 옵션 claude_long_turn_sec 로 조정/끌 수 있습니다(0=끔).",
            ]
        return title, lines

    def _refresh_warn(self, table):
        """[경고] 뷰: 상태줄 ⚠ Claude 경고(장기 턴/반복 루프/포맷 미인식)의 상황·할일
        안내를 표 자리에 보여준다 — 예전엔 별도 InfoScreen 팝업이었던 것을 이 통합 팝업의
        탭으로 옮겼다(사용자 결정 2026-06-17, 상태줄 ⚠ 배지 클릭 → 이 탭). 경고 내용은
        클라 status(claude_warn)에서 라이브로 읽어, 경고가 해소되면 '경고 없음' 안내로
        바뀐다(닫지 않고 탭에 머물러도 다음 합성에서 갱신)."""
        table.add_column(i18n.t("pscreen.tklog_warn_col"), key="warn")
        warn = getattr(getattr(self.app, "status", None), "claude_warn", None)
        if not warn:
            title = i18n.t("pscreen.tklog_warn_title")
            lines = [i18n.t("pscreen.tklog_warn_empty")]
        else:
            title, lines = self._warn_info_text(warn)
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
          합성으로 못 만들어 raw _records(최근 N 건이면 충분). 계정 필터·정렬 적용.
        · account: 계정별 **전체 이력** 합(서버 SQL accounts_total — cap 무관·귀속
          반영). 없으면(구버전 서버) 레코드 집계 폴백. 필터 무시(전 계정 비교 뷰).
        · session: 세션별 합(대표 탭:패널 라벨, 상위 N+기타 접기). 계정 필터 적용."""
        src = (self._records if self._bucket == "hour"
               else (self._full_recs if self._full_recs is not None
                     else self._records))
        if self._view == "account":
            if self._accounts_total:
                pairs = sorted(self._accounts_total.items(),
                               key=lambda kv: -kv[1])
            else:
                agg = usagelog.aggregate(
                    self._full_recs if self._full_recs is not None
                    else self._records)
                pairs = sorted(agg["groups"].items(), key=lambda kv: -kv[1])
            total = sum(t for _, t in pairs)
            rows = [(a, t, round(t / total * 100) if total else 0)
                    for a, t in pairs]
            self._acct_rows = [a for a, _ in pairs]
            vmax = max((t for _, t, _ in rows), default=0)
            return rows, vmax, total, i18n.t("계정"), None
        self._acct_rows = []
        if self._view == "session":
            v = usagelog.agg_view(src, self._bucket, self._account, "session",
                                  self._order, top=self._GROUP_TOP)
            return v["groups"], v["gmax"], v["total"], i18n.t("세션"), None
        weekdays = i18n.t("pscreen.weekdays").split(",")
        v = usagelog.agg_view(src, self._bucket, self._account, "account",
                              self._order, weekdays=weekdays)
        # 5번째: 원시 버킷 키(brows 와 동순) — hour 버킷 5h% 열 조인용(§10-D).
        return v["buckets"], v["bmax"], v["total"], i18n.t("기간"), v.get("bkeys")

    async def _refresh(self):
        self._sync_tabs()
        table = self.query_one(DataTable)
        table.clear(columns=True)
        if self._recon_mode:
            self._refresh_recon(table)
            return
        if self._limit_mode:
            self._refresh_limit(table)
            return
        if self._warn_mode:
            self._refresh_warn(table)
            return
        label_w, bar_cells = self._metrics()
        rows, vmax, win, rowhdr, bkeys = self._view_rows()
        # §10-D: hour 버킷이면 시각별 세션 5h 한도 최대%(권위 /usage)를 별도 열로 보인다.
        # 스크랩 Σ(토큰 열)는 5h 소비를 과소반영하므로 '그 시각 5h 창이 얼마나 찼나'의
        # 진짜 신호다. 5h 비율은 일 단위가 아니라 시간 단위 뷰에 둔다(사용자 결정
        # 2026-06-17) — day/week/month 뷰엔 이 열을 보이지 않는다.
        show5h = (self._bucket == "hour" and self._view != "account"
                  and self._view != "session" and bool(self._hourly_pct)
                  and bkeys is not None)
        # 5h% 막대 칸수: 토큰 비율 막대(bar_cells)와 같은 폭 티어를 쓰되 8칸으로 캡해
        # (0~100% 표현엔 충분) 표 가로폭이 박스를 넘지 않게 한다. 0이면 % 만 표시.
        lim_cells = min(bar_cells, 8)
        # 토큰 열: 약식(1.7M·5.2k)은 단위가 자릿수를 가려 우측정렬만으론 대소가
        # 헷갈린다. 표시되는 모든 값의 '전체 자릿수' 최댓값을 기준으로 작은 값을
        # 더 들여써(큰 값일수록 왼쪽에서 시작) 한눈에 비교되게 한다(사용자 요청).
        toks = [t for _, t, _ in rows]
        maxdig = max((len(str(int(t))) for t in toks if t), default=1)
        tok_w = min(11, max(6, max((len(self._tok_aligned(t, maxdig))
                                    for t in toks), default=6)))
        # 컬럼: 행 차원(기간/계정/세션) | 토큰(자릿수 정렬, 좌측) | [5h%] | 비율(막대+%)
        table.add_column(rowhdr, key="label", width=label_w)
        table.add_column(Text(i18n.t("토큰"), justify="left"), key="tok",
                         width=tok_w)
        if show5h:
            # 막대를 담을 폭(% 3칸 + '%' + 공백 + 막대 cells). 막대 없으면 % 만(5칸).
            table.add_column(Text("5h%", justify="left"), key="lim5h",
                             width=(lim_cells + 6) if lim_cells else 5)
        table.add_column(i18n.t("비율"), key="bar", width=bar_cells + 5)

        if win == 0:               # 선택 뷰/계정 집계 합이 0 (소스 무관)
            empty = [i18n.t("(기록된 토큰 사용량이 없습니다)"), ""]
            if show5h:
                empty.append("")
            empty.append("")
            table.add_row(*empty)
        else:
            for i, (label, tok, pct) in enumerate(rows):
                cells = [self._trunc(label, label_w),
                         Text(self._tok_aligned(tok, maxdig), justify="left")]
                if show5h:
                    cells.append(self._lim5h_cell(
                        bkeys[i] if i < len(bkeys) else None, lim_cells))
                cells.append(self._barcell(tok, vmax, pct, bar_cells))
                table.add_row(*cells)

        # 제목: 뷰 차원(시간/일/주/월/계정/세션)별. 스코프/한도(위)·키 안내(아래) 분리.
        order_l = i18n.t("토큰순") if self._order == "tokens" else i18n.t("시간순")
        # (추정): 집계 원천(스크랩 누계)은 활동량 추정 — 실측 한도는 상단 막대(S6 T3).
        what = (i18n.t("계정") if self._view == "account"
                else i18n.t("세션") if self._view == "session"
                else i18n.t(self._BUCKET_WORD[self._bucket]))
        self.query_one("#tklogtitle", Label).update(
            i18n.t("pscreen.tklog_title2", what=what))
        acct = self._account if self._account is not None \
            else i18n.t("pscreen.acct_all")
        # Σ: 정확한 전체 이력 합(서버 SQL 집계, Phase B). 현재 계정 필터에 맞춰
        # total_all(전체) / accounts_total[acct](계정별)을 쓰고, 없으면(구버전 서버)
        # 표시 레코드 합으로 폴백. 레코드가 cap 돼 표시 합과 다르면 그 표시 합을 병기.
        if self._account is None:
            life = self._total_all
        else:
            life = self._accounts_total.get(self._account)
        if life is None:
            life = win
        sigma = f"~Σ{usagelog._fmt_tokens(life)}"   # ~ = 추정 라벨(S6 T3)
        if life != win:
            sigma += i18n.t("pscreen.tklog_disp", n=usagelog._fmt_tokens(win))
        # 스코프는 1줄로 컴팩트(묶음/버킷은 제목에 이미 있음). 표 높이를 아낀다.
        scope = i18n.t("pscreen.tklog_scope", acct=acct, order=order_l,
                       sigma=sigma)
        if self._fold_acct:
            # §5.5 귀속 표시: 미식별분이 단일 식별 계정에 접혀 있음을 명시(침묵 변형
            # 금지 — 합계가 DB 의 unknown 분해와 달라 보이는 이유를 화면이 설명).
            scope += " · " + i18n.t("pscreen.fold_tag")
        # 상단은 1줄(한도 요약 접두 + 스코프)만 — /usage 막대·창Σ·계정·신선도 상세는
        # [한도] 뷰로 옮겼다(작은 화면 정리, 2026-06-14). usage 없으면 접두는 빈 문자열.
        self.query_one("#tktop", Static).update(self._limit_summary() + scope)
        self.query_one("#tkhint", Static).update(i18n.t("pscreen.tklog_hint"))

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
            # 기간(시간 버킷) 뷰로 전환 — 입도/정렬은 하위 보조옵션 줄에서 고른다(§7.2).
            # 한도/대사/경고 등 어느 탭에서든 클릭하면 기간 뷰로 복귀(§7.1).
            self._exit_body_modes()
            self._view = "time"
            self.run_worker(self._refresh())
        elif wid in self._TAB_BUCKET:
            event.stop()
            # 기간 탭: 기간 뷰로 전환하며 버킷 선택(계정/세션/대사/한도 뷰에서도 한 번에).
            self._exit_body_modes()
            self._view = "time"
            self._bucket = self._TAB_BUCKET[wid]
            self.run_worker(self._refresh())
        elif wid == "tab_acct":
            event.stop()
            # 계정 뷰 토글(행=계정별 전체 합). 행 선택=그 계정 필터+일별 드릴다운.
            was = self._exit_body_modes()
            self._view = "account" if was or self._view != "account" else "time"
            self.run_worker(self._refresh())
        elif wid == "tab_panel":
            event.stop()
            # 세션 뷰 토글(행=세션별 합).
            was = self._exit_body_modes()
            self._view = "session" if was or self._view != "session" else "time"
            self.run_worker(self._refresh())
        elif wid == "tab_order":
            event.stop()
            # 버킷 정렬 토글: 시간순 ↔ 토큰순.
            self._exit_body_modes()
            self._order = "tokens" if self._order == "time" else "time"
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

    def on_data_table_row_selected(self, event):
        """계정 뷰에서 행 선택(Enter/클릭) → 그 계정을 필터로 걸고 일별 추이로
        드릴다운(기간 뷰 전환). 다른 뷰에선 무동작(표 커서만)."""
        if self._view != "account":
            return
        idx = getattr(event, "cursor_row", None)
        if idx is None or not (0 <= idx < len(self._acct_rows)):
            return
        acct = self._acct_rows[idx]
        if acct in self._accounts:
            self._ai = self._accounts.index(acct)
            self._view = "time"
            self._bucket = "day"
            self.run_worker(self._refresh())

    async def on_key(self, event: events.Key):
        k = event.key
        if k in self._BUCKETS:
            event.stop()
            # 기간 뷰로 전환하며 버킷 선택(계정/세션/대사/한도 뷰에서도 한 번에).
            self._exit_body_modes()
            self._view = "time"
            self._bucket = self._BUCKETS[k]
            await self._refresh()
            return
        if k == "a" and len(self._accounts) > 1:
            event.stop()
            self._ai = (self._ai + 1) % len(self._accounts)
            await self._refresh()
            return
        if k == "c":
            event.stop()
            # 계정 뷰 토글(행=계정별 전체 합). 대사/한도 뷰에서 누르면 그리로 진입.
            was = self._exit_body_modes()
            self._view = "account" if was or self._view != "account" else "time"
            await self._refresh()
            return
        if k == "p":
            event.stop()
            # 세션 뷰 토글(행=세션별 합).
            was = self._exit_body_modes()
            self._view = "session" if was or self._view != "session" else "time"
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
        if k == "enter" and self._view == "account" \
                and not self._limit_mode and not self._recon_mode:
            # 계정 뷰 행 선택(드릴다운)은 DataTable 의 RowSelected 가 처리 — 닫지
            # 않고 표에 넘긴다.
            return
        if k == "o":
            event.stop()
            # 버킷 정렬 토글: 시간순 ↔ 토큰순.
            self._exit_body_modes()
            self._order = "tokens" if self._order == "time" else "time"
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
        if k in self._NAV_KEYS:
            # 스크롤/커서는 포커스된 DataTable 이 자체 처리 — 가로채지 않는다.
            return
        if k == "escape":
            event.stop()
            self.dismiss(None)
            return
        # 그 외 키는 닫는다(기존 동작).
        event.stop()
        self.dismiss(None)



class AccountAliasScreen(ModalScreen):
    """§10-E #2b 좌하단 Claude 계정 — 감지된 계정 목록 + 사용자 별칭 편집 + 표시모드
    선택 화면. 서버 request_account_list 회신으로 채운다(open_account_aliases).
      · 목록: 감지된 계정(이메일) → 현재 별칭
      · Enter(또는 행 클릭): 그 계정의 별칭 편집(하단 입력칸; 빈값 제출=별칭 삭제)
      · m: 표시모드 순환(별칭/메일 전체/표시 안 함) — footer 표기에 즉시 반영
      · Esc: 편집 중이면 편집 취소, 아니면 닫기. [x]/바깥 클릭=닫기.
    설정은 서버 opts.json(plugin_opts)에 영속(set_account_alias·set_account_display)."""
    CSS = """
    AccountAliasScreen { align: center middle; }
    #aabox { width: 80%; max-width: 76; height: auto; max-height: 80%;
             border: round $accent; background: $panel; padding: 0 1; }
    #aahead { width: 100%; height: 1; }
    #aatitle { width: 1fr; height: 1; color: $accent; text-style: bold; }
    #aaclose { width: 5; height: 1; content-align: center middle;
               background: $error; color: $text; text-style: bold; }
    #aamode { width: 100%; height: 1; color: $text-muted; }
    #aalist { width: 100%; height: auto; max-height: 16; margin: 0 0 0 0; }
    #aainput { width: 100%; display: none; }
    #aainput.editing { display: block; }
    #aahint { width: 100%; height: 1; color: $text-muted; }
    """
    _MODES = ("alias", "full", "hidden")

    def __init__(self, accounts, aliases, display):
        super().__init__()
        self._accounts = list(accounts or [])
        self._aliases = dict(aliases or {})
        self._display = display if display in self._MODES else "alias"
        self._editing = None        # 편집 중인 계정 이메일(없으면 None)

    def compose(self) -> ComposeResult:
        with Vertical(id="aabox"):
            with Horizontal(id="aahead"):
                yield Static(i18n.t("acct.title"), id="aatitle")
                yield Label("[x]", id="aaclose", markup=False)
            yield Static("", id="aamode", markup=False)
            yield ListView(id="aalist")
            yield Input(id="aainput", placeholder=i18n.t("acct.input_ph"))
            yield Static(i18n.t("acct.edit_hint"), id="aahint", markup=False)

    def on_mount(self):
        # 리스트는 mount 때 한 번만 채우고 이후엔 행 라벨을 제자리 갱신한다(ListView.clear
        # 가 비동기라 clear+append 재구성 시 id 충돌/중복이 났다 — §10-E #2b 함정).
        self._labels = []           # 계정 행 Label(인덱스=self._accounts 정렬)
        self._sync_mode_line()
        lv = self.query_one("#aalist", ListView)
        if not self._accounts:
            lv.append(ListItem(Label(i18n.t("acct.none"), markup=False)))
        else:
            for email in self._accounts:
                lab = Label(self._row_text(email), markup=False)
                self._labels.append(lab)
                lv.append(ListItem(lab))
        lv.focus()

    # ---- 데이터/표시 ----
    def _mode_name(self):
        return i18n.t("acct.mode_" + self._display)

    def _row_text(self, email):
        alias = self._aliases.get(email)
        tail = alias if alias else i18n.t("acct.no_alias")
        return f"{email}  →  {tail}"

    def _sync_mode_line(self):
        self.query_one("#aamode", Static).update(
            i18n.t("acct.mode_line", mode=self._mode_name()))

    def _refresh_row(self, email):
        if email in self._accounts:
            i = self._accounts.index(email)
            if i < len(getattr(self, "_labels", [])):
                self._labels[i].update(self._row_text(email))

    def update_data(self, accounts, aliases, display):
        """서버 account_list 재회신으로 데이터 갱신(편집 후 재요청 시). 계정 집합이
        같으면 행 라벨만 제자리 갱신(ListView clear 의 비동기 함정 회피)."""
        self._aliases = dict(aliases or {})
        self._display = display if display in self._MODES else "alias"
        self._sync_mode_line()
        if list(accounts or []) == self._accounts:
            for email in self._accounts:
                self._refresh_row(email)

    # ---- 표시모드 순환 ----
    def _cycle_mode(self):
        i = self._MODES.index(self._display)
        self._display = self._MODES[(i + 1) % len(self._MODES)]
        self.app.send_cmd("set_account_display", value=self._display)
        self._sync_mode_line()

    # ---- 별칭 편집 ----
    def _begin_edit(self, idx):
        if not (0 <= idx < len(self._accounts)):
            return
        self._editing = self._accounts[idx]
        inp = self.query_one("#aainput", Input)
        inp.value = self._aliases.get(self._editing, "")
        inp.add_class("editing")
        inp.focus()

    def _end_edit(self):
        self._editing = None
        inp = self.query_one("#aainput", Input)
        inp.remove_class("editing")
        inp.value = ""
        self.query_one("#aalist", ListView).focus()

    def on_input_submitted(self, event):
        if self._editing is None:
            return
        email = self._editing
        alias = (event.value or "").strip()
        self.app.send_cmd("set_account_alias", email=email, alias=alias)
        if alias:                       # 낙관적 즉시 반영(서버 broadcast 와 동치)
            self._aliases[email] = alias
        else:
            self._aliases.pop(email, None)
        self._end_edit()
        self._refresh_row(email)        # 해당 행만 제자리 갱신(전체 재구성 X)

    def on_list_view_selected(self, event):
        idx = getattr(self.query_one("#aalist", ListView), "index", None)
        if idx is not None:
            self._begin_edit(idx)

    def on_click(self, event: events.Click):
        w = getattr(event, "widget", None)
        wid = inside = None
        while w is not None:
            this = getattr(w, "id", None)
            if this and wid is None:
                wid = this
            if this == "aabox":
                inside = True
            w = w.parent
        if wid == "aaclose":
            event.stop()
            self.dismiss(None)
        elif not inside:
            event.stop()
            self.dismiss(None)

    def on_key(self, event: events.Key):
        k = event.key
        if self._editing is not None:
            if k == "escape":           # 편집 취소(입력은 on_input_submitted 가 처리)
                event.stop()
                self._end_edit()
            return
        if k == "escape":
            event.stop()
            self.dismiss(None)
        elif k in ("m", "ㅡ"):           # 표시모드 순환(IME ㅡ 도 허용)
            event.stop()
            self._cycle_mode()
