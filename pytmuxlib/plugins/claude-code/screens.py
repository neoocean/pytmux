"""claude-code 플러그인의 모달 화면 — 시작 규칙 편집(RulesEditScreen)·토큰 절감
설정(ClaudeSaverScreen). client.py 의 clientscreens 에서 이리로 이전.

textual 의존이 있어 이 모듈은 **실제로 팝업을 열 때** 지연 import 된다(플러그인 __init__
은 가벼움 — 서버 프로세스도 plugins.load() 로 읽기 때문)."""
from __future__ import annotations

from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Label, ListItem, ListView, Static, TextArea

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
        "시나리오", "대사", "기간", "보기", "조회", "토큰 사용량",
        "구간", "실측(세션 5h)", "추정Σ", "항목", "토큰", "비율",
        "세션", "토큰순", "시간순",
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
        "기간": "Period", "보기": "View", "조회": "Query", "토큰 사용량": "Token usage",
        "구간": "Span", "실측(세션 5h)": "Measured (session 5h)", "추정Σ": "Est Σ",
        "항목": "Item", "토큰": "Tokens", "비율": "Ratio",
        "세션": "Session", "토큰순": "by tokens", "시간순": "by time",
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
    },
})
# 포맷 문자열(동적 인자 포함)은 semantic 키.
i18n.register({
    "ko": {
        "pscreen.perm_title": "권한모드 선택 (현재: {current})",
        "pscreen.tklog_title2": "토큰 사용량(추정) · {what}별",
        "pscreen.tklog_scope": "계정 {acct} · {order} · {sigma}",
        "pscreen.tklog_disp": " (표시 {n})",
        "pscreen.tklog_hint": "h시 d일 w주 m월 · c계정 p세션 · a필터 o정렬 · u/usage s설정 r대사 · Esc닫기",
        "pscreen.weekdays": "월,화,수,목,금,토,일",
        "pscreen.recon_top": "실측(/usage Δ%)과 추정(스크랩 ~Σ)은 의미가 다른 두 출처 — 절대 일치가 아니라 추세 상관을 봅니다",
        "pscreen.recon_empty": "(대사할 실측 스냅샷 구간이 없습니다 — /usage 실측이 2회 이상 쌓이면 생깁니다)",
        "pscreen.acct_all": "전체",
        "pscreen.fold_tag": "미식별 포함",
        "pscreen.win_session": "이번 5h창 ~Σ{tok}(리셋 {left} 후)",
        "pscreen.win_week": "이번 주 ~Σ{tok}(리셋 {left} 후)",
        "pscreen.left_hm": "{h}시간{m}분",
        "pscreen.left_m": "{m}분",
        "pscreen.left_d": "{d}일{h}시간",
    },
    "en": {
        "pscreen.perm_title": "Select permission mode (current: {current})",
        "pscreen.tklog_title2": "Token usage (est) · by {what}",
        "pscreen.tklog_scope": "Account {acct} · {order} · {sigma}",
        "pscreen.tklog_disp": " (shown {n})",
        "pscreen.tklog_hint": "h hour d day w week m month · c account p session · a filter o sort · u /usage s settings r recon · Esc close",
        "pscreen.weekdays": "Mo,Tu,We,Th,Fr,Sa,Su",
        "pscreen.recon_top": "Measured (/usage Δ%) and est (scrape ~Σ) are two different sources — look at trend correlation, not exact match",
        "pscreen.recon_empty": "(no measured snapshot spans to reconcile — appears once /usage measurements accumulate 2+)",
        "pscreen.acct_all": "All",
        "pscreen.fold_tag": "incl. unidentified",
        "pscreen.win_session": "this 5h window ~Σ{tok} (resets in {left})",
        "pscreen.win_week": "this week ~Σ{tok} (resets in {left})",
        "pscreen.left_hm": "{h}h {m}m",
        "pscreen.left_m": "{m}m",
        "pscreen.left_d": "{d}d {h}h",
    },
})


class ClaudeSaverScreen(ModalScreen):
    """토큰 절감 설정 팝업(docs/TOKEN_SAVING_SCENARIO.md, `token-saver` 명령).

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
from pytmuxlib.clientutil import _char_cells, bar, format_option_row
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
    /* 버튼 행: 역할(위계)별 외곽선 그룹(기간/보기/조회)을 가로로 나란히. */
    #tktabs { width: 100%; height: auto; align-horizontal: left; }
    /* 같은 위계 버튼을 한 외곽선 안에 묶는 그룹 박스. 제목(기간/보기/조회)을
       왼쪽 윗변에 옅게 달아 역할을 표시한다(높이=테두리2+버튼1=3행). */
    .tkgroup { width: auto; height: 3; border: round $primary;
               border-title-color: $text-muted; border-title-align: left;
               padding: 0 1; margin: 0 1 0 0; }
    #tktabs Label { height: 1; padding: 0 1; margin: 0 1 0 0; }
    .tkbtab { background: $surface; color: $text-muted; }
    .tkbtab-active { background: $accent; color: $text; text-style: bold; }
    .tkbbtn { background: $primary-darken-2; color: $text; }
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
        "tab_hour": ("시간", "시"), "tab_day": ("일", "일"),
        "tab_week": ("주", "주"), "tab_month": ("월", "월"),
        "tab_acct": ("계정", "계"), "tab_panel": ("세션", "세"),
        "tab_order": ("정렬", "정"), "tab_usage": ("/usage", "U"),
        "tab_saver": ("시나리오", "S"), "tab_recon": ("대사", "R"),
    }
    # [패널]/계정 그룹이 많을 때 상위 N + '기타'로 접어 길이 폭주를 막는다(설계 §4).
    _GROUP_TOP = 8

    def __init__(self, records, usage=None, total_all=None, accounts_total=None,
                 daily=None, reconcile=None):
        super().__init__()
        self._records = records or []
        # 전체 이력 일자별 합성 레코드(서버 daily_breakdown). day/week/month 버킷은
        # 이걸로 집계해 옛 버킷이 cap 에 잘리지 않게 한다(None=구버전 서버 → 폴백으로
        # 최근 N 건 _records 사용). hour 버킷만 raw _records 를 쓴다(_refresh 참고).
        self._full_recs = usagelog.daily_to_records(daily) if daily else None
        self._usage = usage          # M19 그림자 /usage 한도(dict|None)
        # S6 T2: 대사 구간(서버 usagedb.reconcile 결과). [대사] 뷰 전용 진단 데이터.
        self._reconcile = reconcile or []
        self._recon_mode = False     # True 면 표가 대사 구간을 보여준다([r] 토글)
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
            # 마우스로 누르는 버튼 행. 역할(위계)별로 외곽선 그룹에 묶는다(사용자
            # 요청): ①기간(라디오: 시간/일/주/월) ②보기(필터/토글: 계정/패널/정렬)
            # ③조회(동작: /usage·시나리오). 같은 위계 버튼은 한 외곽선 안에 모인다.
            with Horizontal(id="tktabs"):
                with Horizontal(id="tkgrp_period", classes="tkgroup"):
                    yield Label("시간", id="tab_hour", classes="tkbtab",
                                markup=False)
                    yield Label("일", id="tab_day", classes="tkbtab",
                                markup=False)
                    yield Label("주", id="tab_week", classes="tkbtab",
                                markup=False)
                    yield Label("월", id="tab_month", classes="tkbtab",
                                markup=False)
                with Horizontal(id="tkgrp_view", classes="tkgroup"):
                    yield Label("계정", id="tab_acct", classes="tkbtab",
                                markup=False)
                    yield Label("패널", id="tab_panel", classes="tkbtab",
                                markup=False)
                    yield Label("정렬", id="tab_order", classes="tkbtab",
                                markup=False)
                with Horizontal(id="tkgrp_query", classes="tkgroup"):
                    yield Label("/usage", id="tab_usage", classes="tkbbtn",
                                markup=False)
                    yield Label("시나리오", id="tab_saver", classes="tkbbtn",
                                markup=False)
                    yield Label("대사", id="tab_recon", classes="tkbtab",
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
        # 역할별 외곽선 그룹 제목(위계 표시). compose 의 as-캡처 대신 관례대로
        # 마운트 후 위젯 참조로 설정한다.
        for gid, title in (("#tkgrp_period", "기간"), ("#tkgrp_view", "보기"),
                           ("#tkgrp_query", "조회")):
            try:
                self.query_one(gid, Horizontal).border_title = i18n.t(title)
            except Exception:
                pass
        await self._refresh()
        self.query_one(DataTable).focus()

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

    def _sync_tabs(self):
        """탭 라벨(폭 반응형)·활성 버킷·활성 그룹차원([패널])·정렬([정렬]) 강조."""
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
        for tid, bucket in self._TAB_BUCKET.items():
            try:
                lab = self.query_one("#" + tid, Label)
            except Exception:
                continue
            # 기간 탭은 기간 뷰일 때만 라디오 강조(계정/세션 뷰에선 비활성 표시).
            lab.set_class(self._view == "time" and bucket == self._bucket,
                          "tkbtab-active")
        # 보기 그룹: [계정]=계정 뷰, [세션]=세션 뷰, [정렬]=토큰순일 때 강조.
        try:
            self.query_one("#tab_acct", Label).set_class(
                self._view == "account", "tkbtab-active")
            self.query_one("#tab_panel", Label).set_class(
                self._view == "session", "tkbtab-active")
            self.query_one("#tab_order", Label).set_class(
                self._order == "tokens", "tkbtab-active")
            self.query_one("#tab_recon", Label).set_class(
                self._recon_mode, "tkbtab-active")
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
            return rows, vmax, total, i18n.t("계정")
        self._acct_rows = []
        if self._view == "session":
            v = usagelog.agg_view(src, self._bucket, self._account, "session",
                                  self._order, top=self._GROUP_TOP)
            return v["groups"], v["gmax"], v["total"], i18n.t("세션")
        weekdays = i18n.t("pscreen.weekdays").split(",")
        v = usagelog.agg_view(src, self._bucket, self._account, "account",
                              self._order, weekdays=weekdays)
        return v["buckets"], v["bmax"], v["total"], i18n.t("기간")

    async def _refresh(self):
        self._sync_tabs()
        table = self.query_one(DataTable)
        table.clear(columns=True)
        if self._recon_mode:
            self._refresh_recon(table)
            return
        label_w, bar_cells = self._metrics()
        rows, vmax, win, rowhdr = self._view_rows()
        # 토큰 열: 약식(1.7M·5.2k)은 단위가 자릿수를 가려 우측정렬만으론 대소가
        # 헷갈린다. 표시되는 모든 값의 '전체 자릿수' 최댓값을 기준으로 작은 값을
        # 더 들여써(큰 값일수록 왼쪽에서 시작) 한눈에 비교되게 한다(사용자 요청).
        toks = [t for _, t, _ in rows]
        maxdig = max((len(str(int(t))) for t in toks if t), default=1)
        tok_w = min(11, max(6, max((len(self._tok_aligned(t, maxdig))
                                    for t in toks), default=6)))
        # 컬럼: 행 차원(기간/계정/세션) | 토큰(자릿수 정렬, 좌측) | 비율(막대+%)
        table.add_column(rowhdr, key="label", width=label_w)
        table.add_column(Text(i18n.t("토큰"), justify="left"), key="tok",
                         width=tok_w)
        table.add_column(i18n.t("비율"), key="bar", width=bar_cells + 5)

        if win == 0:               # 선택 뷰/계정 집계 합이 0 (소스 무관)
            table.add_row(i18n.t("(기록된 토큰 사용량이 없습니다)"), "", "")
        else:
            for label, tok, pct in rows:
                table.add_row(
                    self._trunc(label, label_w),
                    Text(self._tok_aligned(tok, maxdig), justify="left"),
                    self._barcell(tok, vmax, pct, bar_cells))

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
        top = self._usage_lines() + self._window_lines() + [scope]
        self.query_one("#tktop", Static).update("\n".join(top))
        self.query_one("#tkhint", Static).update(i18n.t("pscreen.tklog_hint"))

    def on_click(self, event: events.Click):
        # 마우스 클릭: 닫기 [x]·서브탭(버킷)·동작 버튼을 위젯 id 로 분기한다.
        w = getattr(event, "widget", None)
        wid = None
        while w is not None:
            wid = getattr(w, "id", None)
            if wid:
                break
            w = w.parent
        if wid == "tklogclose":
            event.stop()
            self.dismiss(None)
        elif wid in self._TAB_BUCKET:
            event.stop()
            # 기간 탭: 기간 뷰로 전환하며 버킷 선택(계정/세션 뷰에서도 한 번에).
            self._view = "time"
            self._bucket = self._TAB_BUCKET[wid]
            self.run_worker(self._refresh())
        elif wid == "tab_acct":
            event.stop()
            # 계정 뷰 토글(행=계정별 전체 합). 행 선택=그 계정 필터+일별 드릴다운.
            self._view = "time" if self._view == "account" else "account"
            self.run_worker(self._refresh())
        elif wid == "tab_panel":
            event.stop()
            # 세션 뷰 토글(행=세션별 합).
            self._view = "time" if self._view == "session" else "session"
            self.run_worker(self._refresh())
        elif wid == "tab_order":
            event.stop()
            # 버킷 정렬 토글: 시간순 ↔ 토큰순.
            self._order = "tokens" if self._order == "time" else "time"
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
            # S6 T2: 집계 ↔ 대사(실측Δ% vs 추정Σ) 뷰 토글.
            self._recon_mode = not self._recon_mode
            self.run_worker(self._refresh())

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
            # 기간 뷰로 전환하며 버킷 선택(계정/세션 뷰에서도 한 번에).
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
            # 계정 뷰 토글(행=계정별 전체 합).
            self._view = "time" if self._view == "account" else "account"
            await self._refresh()
            return
        if k == "p":
            event.stop()
            # 세션 뷰 토글(행=세션별 합).
            self._view = "time" if self._view == "session" else "session"
            await self._refresh()
            return
        if k == "enter" and self._view == "account":
            # 계정 뷰 행 선택(드릴다운)은 DataTable 의 RowSelected 가 처리 — 닫지
            # 않고 표에 넘긴다.
            return
        if k == "o":
            event.stop()
            # 버킷 정렬 토글: 시간순 ↔ 토큰순.
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
            # S6 T2: 집계 ↔ 대사(실측Δ% vs 추정Σ) 뷰 토글.
            self._recon_mode = not self._recon_mode
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

