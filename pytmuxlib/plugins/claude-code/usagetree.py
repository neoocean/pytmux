"""토큰 팝업 **계층 트리**의 산수 — 월→주→일→시각 (pytmux-371 ①).

# 왜 화면 밖으로 나왔나

이 트리는 `screens.TokenLogScreen` 의 메서드였다. 그래서 **정본 TUI 만** 그릴 수 있었다 —
`screenspec` 은 Textual 을 물지 않는 것이 규약이라(그 모듈 머리말 §UI 무의존) 네이티브
클라에 같은 판을 내려보낼 방법이 없었고, GUI 의 기간 판은 평면 막대에 머물렀다.

⛔ **두 벌로 적지 않는다.** 구역을 가르는 규칙(이번 주는 일 행 · 이번 달 지난 주는 주 행 ·
이전 달은 월 행)과 「각 날짜가 정확히 한 구역에만 든다」는 가산성은 이 트리의 **뜻 그
자체**다. 그것을 클라마다 다시 적으면 같은 기간이 두 화면에서 다른 합을 보이고, 그 갈림은
아무도 안 잰다. 그래서 산수는 여기 한 벌이고 두 화면이 **부른다**.

# 펼침 상태는 누가 드나

부르는 쪽이다. 정본은 화면이 `_tree_toggled` 집합으로 들고, 스펙 경로는 **클라가** 들고
왕복마다 실어 보낸다(`_warn_spec` 의 `warn_open` 과 같은 처방). 여기서 들면 클라 둘이
같은 서버를 볼 때 한쪽이 편 것이 다른 쪽에서도 펴진다.
"""
from __future__ import annotations

from datetime import date, datetime

from pytmuxlib import i18n

from . import usagelog


def build(records, full_recs=None, hourly=None, opened=(), *, today=None):
    """계층 트리 노드 목록을 만든다(표 행과 1:1). 반환은 `(nodes, total)`.

    - `records` — 최근 기록(시각 폴백에 쓰인다)
    - `full_recs` — 전체 기록(있으면 일 인덱스의 원본)
    - `hourly` — 서버가 SQL 로 집계한 시각 인덱스(없으면 `records` 폴백)
    - `opened` — **사용자가 뒤집은** 키 집합(기본값의 반대로 본다)
    - `today` — 오늘 날짜(시험이 못박는다). 안 주면 이 상자의 오늘.

    노드 dict 의 칸은 종전 그대로다:
      `kind`(month|week|day|hour|divider) · `key`(토글 키 · leaf/divider 는 None) ·
      `label` · `tokens` · `models` · `level`(들여쓰기) · `expandable` · `expanded` ·
      `bk`(시각 5h% 조인키).
    """
    toggled = set(opened or ())

    def _open(key, default):
        """행의 effective 펼침 = 기본값 ^ (사용자 토글) — 정본 `_tree_open` 그대로."""
        return default ^ (key in toggled)

    src = full_recs if full_recs is not None else records
    weekdays = i18n.t("pscreen.weekdays").split(",")
    hour_suffix = i18n.t("pscreen.hour_suffix")
    day_idx = usagelog.agg_index(src, "day", weekdays=weekdays,
                                 hour_suffix=hour_suffix)
    hour_idx = (usagelog.hourly_index(hourly, hour_suffix)
                if hourly else
                usagelog.agg_index(records, "hour",
                                   hour_suffix=hour_suffix))
    if today is None:
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
        opened = _open(key, default_open) if has_hours else False
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
        opened = _open(key, False)
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
        opened = _open(key, False)
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
