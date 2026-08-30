"""claude-code 상태줄 표식의 **규칙 한 벌** — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가 (M4 P6 후반)

시계(2026-08-02e)·달력(08-02b)·usage-view(08-02f)가 먼저 간 길이다. 그리는 규칙을 한 벌로
모으고 두 소비자가 **같은 목록**을 받는다:

- 정본(`clientstatus.render_segs`) — 이 목록으로 rich 세그먼트를 만들고 클릭존을 찍는다.
- 네이티브 클라 — 서버가 `plugin_badges` 로 같은 목록을 보낸다(설계 Tier B ③).

이 자리는 **"없는 것을 만든다"가 아니라 "두 벌을 합친다"** 였다. GUI 는 종전에 서버가
보내는 날 필드(`claude_model`·`tok5h_pct`)로 자기가 `opus-5 · 12%/5h` 를 조립했고
(`proto::session::claude_badge`), 정본은 여기서 그보다 자세한 것을 그렸다. 두 벌이 갈려
있었다는 뜻이라, 합치기 전에 **정본 출력의 골든**을 떠서 이 모듈이 그것을 재현하는지부터
잰다(`tests/test_claude_statusbadges.py`).

# 무엇을 돌려주나

표식 목록. 하나는 이렇게 생겼다:

    {"kind": "usage",              # 정본이 클릭존·포커스를 붙이는 데 쓴다
     "text": "12%/5h 사용",         # 짓는 쪽 로케일로 지은 글
     "i18n": {"text": {...}},      # 클라가 **자기 로케일로 다시 지을 재료**(로케일 ⓑ)
     "style": {"bo": 1, "f": "white"},
     "theme": {"b": "secondary"}}

- `kind` 는 넷이다: `model` · `usage` · `pending` · `warn`. 정본은 이 이름으로 종전의
  클릭존(`_model_zone`·`_usage_zone`·`_warn_zone`)을 그대로 붙인다 — 자리 계산은 여전히
  정본 것이다(**내용·선택은 규칙, 표현은 각 클라** — 설계 §6).
- 색은 **의미 이름**만 싣는다(`secondary`·`warning`·`error`). 서버가 hex 를 실으면
  서버가 UI 를 알게 된다(설계 §10 위험표).
- **원격 분홍은 안 싣는다.** "지금 원격 탭을 보고 있다"는 그 클라만 아는 상태라 정본이
  자기 색으로 덮는다(REC 배지가 포커스 노랑을 안 싣는 것과 같은 규율).

# 로케일

자리가 있는 글은 `i18n.phrase` 로 **원문 포맷 + 값**을 함께 싣는다. 안 그러면 서버가
지은 한국어가 영어 클라에 그대로 뜬다 — GUI 는 종전에 이 줄을 자기가 조립해 그 문제가
없었으므로, 재료 없이 옮기는 것은 **회귀**다(로케일 ⓑ · 2026-08-02m).
"""
from __future__ import annotations

from pytmuxlib import i18n

# 배지 두 벌의 표현 — `(축약 스타일, 의미 색)`. 축약 표기는 서버가 화면 런에 쓰는 것과 같다.
_SEC = ({"bo": 1, "f": "white"}, {"b": "secondary"})
_WARN = ({"bo": 1, "f": "black"}, {"b": "warning"})
_ERR = ({"bo": 1, "f": "white"}, {"b": "error"})


def _badge(kind, text, st, phrase=None, do=None):
    style, theme = st
    out = {"kind": kind, "text": text, "style": dict(style), "theme": dict(theme)}
    if phrase is not None:
        out["i18n"] = {"text": phrase}
    if do is not None:
        out["do"] = do
    return out


def _limit_badge(fields):
    """5시간(또는 주간 Sonnet) 사용률 배지 하나. 없으면 `?%/5h` 로라도 띄운다.

    ⚠ **비워 두지 않는다**: Claude 를 막 시작해 그림자 `/usage` 가 아직 없으면 두 값이
    모두 `None` 인데, 그때 아무것도 안 그리면 사용자는 "사용량 표시가 없는 클라"를 본다
    (제보 2026-06-18). 값 미상이라도 자리를 잡아 두면 실측이 오는 순간 숫자로 바뀐다.

    5h ↔ 주간 Sonnet 은 **상호배타**다 — Anthropic 이 5h 를 모델 통합으로만 줘서 활성
    모델이 Sonnet 이면 서버가 5h 대신 주간을 채운다(2026-06-16 사용자 결정)."""
    t5 = fields.get("tok5h_pct")
    if isinstance(t5, (int, float)):
        return i18n.phrase("claude.limit_used", pct=max(0, min(100, int(t5))))
    ws = fields.get("week_sonnet_pct")
    if isinstance(ws, (int, float)):
        return i18n.phrase("claude.limit_week_sonnet", pct=max(0, min(100, int(ws))))
    # 자리가 없는 글이라 재료가 필요 없다(원문이 곧 키 — 로케일 ⓐ).
    return i18n.t("claude.limit_unknown"), None


def _warn_text(fields):
    """경고 배지의 글. 종류별로 로케일 판이 다르고, 장기 턴만 언어중립이다.

    ⚠ `⚠`(U+26A0)는 wcwidth=1 이지만 터미널이 컬러 이모지(2칸)로 그려서 바로 뒤 한 칸이
    둘째 칸에 먹힌다 — 표시용으로만 공백을 하나 더 넣는다(저장값·파서는 원문 그대로).
    제보 2026-06-17."""
    kind = fields.get("claude_warn_kind")
    if kind == "repeat":
        text, spec = i18n.phrase("claude.warn_repeat_badge",
                                 n=fields.get("claude_warn_n") or 0)
    elif kind == "fmt_unknown":
        text, spec = i18n.t("claude.warn_fmt_badge"), None
    else:
        # 종류 미상(구버전 서버)이면 서버 문자열 폴백 — 한국어일 수 있으나 호환 유지.
        text, spec = fields.get("claude_warn") or "", None
    if text.startswith("⚠ "):
        text = "⚠  " + text[2:]
    return f" {text} ", spec


def badges(fields) -> list:
    """상태줄에 붙일 표식 목록. `fields` 는 status 메시지(또는 그와 같은 키의 dict).

    순서가 곧 정본의 종전 순서다: 모델 → 사용량 → 카운트다운 → 경고. 자리는 각 클라가
    정하지만 **순서는 뜻의 일부**라 여기서 정한다(정본은 왼쪽부터, GUI 는 칩 줄에서
    같은 순서로 그린다)."""
    out = []
    if fields.get("claude_active"):
        model = fields.get("claude_model")
        if model:
            # 모델 이름은 번역하지 않는다 — `opus-5` 는 철자 그 자체다.
            #
            # ★ **누르면 열린다**(pytmux-379). 아래 ⚠ 주석은 오래 *"model 에는 아직 안
            #   싣는다 — 그 클릭이 여는 화면이 Tier C 에 없다"* 였는데, 그 사이에 생겼다:
            #   `screenspec.MODEL` → `_model_spec`(모델×컨텍스트 목록, 고르면 `/model` 주입).
            #   정본도 이 배지 클릭을 같은 곳으로 보낸다(`open_model_config` → 토큰 팝업의
            #   `[한도]` 탭 = 모델·컨텍스트 섹션. 그 탭의 GUI 대역이 이 스펙이다).
            out.append(_badge("model", str(model), _SEC, do="model"))
        text, spec = _limit_badge(fields)
        # ★ **누르면 열리는 이름**을 싣는다(pytmux-20). 이 칸을 오래 비워 둔 이유는
        #   "선언은 있고 배선이 없는 칸"을 안 만들려는 것이었다 — 그 화면(Tier C)이
        #   없었으니까. 이제 `usage-panel` 이 화면 스펙을 내므로 조건이 섰다.
        #   클라는 이 이름의 뜻을 모른 채 `plugin_open` 으로 되돌려 보낸다(오버레이의
        #   `do` 와 같은 규약 — 행동은 서버가 정한다).
        #   ⚠ 나머지 둘(pending·warn)에는 아직 안 싣는다. 정본에서 그 클릭이 여는
        #   화면들은 여전히 Tier C 가 없다 — **선언은 있고 배선이 없는 칸**을 안 만든다.
        out.append(_badge("usage", text, _SEC, spec, do="usage-panel"))
    pending = fields.get("claude_pending")
    if isinstance(pending, dict):
        # 라벨을 **포맷 안에** 둔 판을 쓴다 — 인자로 넘기면 클라가 자기 포맷에 서버
        # 로케일 조각을 끼워 언어가 섞인다(`i18n.phrase` 경고).
        text, spec = i18n.phrase("claude.countdown_ar", eta=pending.get("eta", 0))
        out.append(_badge("pending", text, _WARN, spec))
    if fields.get("claude_warn"):
        text, spec = _warn_text(fields)
        out.append(_badge("warn", text, _ERR, spec))
    return out
