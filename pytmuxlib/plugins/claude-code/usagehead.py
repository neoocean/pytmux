"""토큰 사용량 판의 **머리줄 한 벌**(pytmux-419 ②).

정본 토큰 팝업의 위쪽 요약은 한 줄이다 —

    5h 29% · wk 22% · Σ22641.9M 실측(캐시 읽기…+쓰기…) · 활동~15M
      ⇅ 91ddca94 85% · 이 머신 15%   미상 45%

`screens.py`(Textual)가 그것을 `_limit_summary` + `_sigma_text`(+ `_host_text` ·
`_unknown_text`)로 지었는데, GUI 로 내려가는 **화면 스펙을 짓는 것은 서버**이고 서버는
그 파일을 안 읽는다(플러그인 머리말의 무게 규칙 — 화면은 열 때 지연 import 한다).
그래서 GUI 판에는 이 줄이 **아예 없었다**(pytmux-371 ⓑ · pytmux-419 ②).

⛔ **여기가 그 산수의 한 벌이다.** 두 벌로 적으면 같은 값이 두 화면에서 다른 글로 뜬다 —
`usagetree.build` 를 뽑을 때와 **같은 이유이고 같은 처방**이다(그 모듈 머리말). 정본
화면은 이제 이 함수들을 부르고, 제 화면 상태를 인자로 넘길 뿐이다.

⚠ 이 모듈은 **Textual 을 안 문다.** 물는 순간 서버가 이것을 못 읽고 처음 자리로 돌아간다
(`tests/test_i18n.py` 의 카탈로그 게이트가 그 부류를 센다).
"""
from pytmuxlib import i18n

from . import usagelog

#: 원산지가 이 머신인 적재분에 `usagedb` 가 다는 이름표(`host` 칸이 NULL 인 줄).
LOCAL_HOST = "<local>"


def limit_summary(usage):
    """머리줄 **접두** — `5h 17% · 주 14% · `. 실측이 없으면 빈 문자열.

    상세(막대·리셋·계정·창Σ)는 [한도] 판의 것이다(작은 화면 정리 2026-06-14) — 여기
    남는 것은 「지금 얼마나 찼나」 두 숫자뿐이다.
    """
    if not isinstance(usage, dict):
        return ""
    parts = []
    for key, fmt in (("session", "pscreen.lim_5h"), ("week_all", "pscreen.lim_wk")):
        d = usage.get(key)
        if isinstance(d, dict) and d.get("pct") is not None:
            parts.append(i18n.t(fmt, p=d["pct"]))
    return (" · ".join(parts) + " · ") if parts else ""


def host_text(xc_hosts):
    """Σ 뒤에 붙는 **원산지 머신별 비중**(`⇅ 이 머신 62% · a1b2 38%`).

    동기화를 켜면 Σ 가 계정 **전역**(다른 머신 포함)으로 뛰는데, 그게 어디서 왔는지
    안 보이면 「왜 갑자기 늘었나」를 사람이 못 푼다. 머신이 하나뿐이면 볼 것이 없어
    빈 문자열이다(잡음 0). `⇅` 는 동기화된 값이라는 표식.
    """
    hosts = {h: int(v) for h, v in (xc_hosts or {}).items() if v}
    if len(hosts) < 2:
        return ""
    total = sum(hosts.values()) or 1
    parts = []
    for h, v in sorted(hosts.items(), key=lambda kv: (-kv[1], kv[0]))[:4]:
        name = i18n.t("pscreen.tklog_host_local") if h == LOCAL_HOST else h[:8]
        parts.append("%s %d%%" % (name, round(100.0 * v / total)))
    return "  ⇅ " + " · ".join(parts)


def unknown_text(xc_cov):
    """Σ 뒤에 붙는 **미상 계정 비중**(`· 미상 12%`).

    계정 미상 행은 계정별 Σ 에 안 섞는다(사용자 결정 2026-07-25 · 설계 §10.2-4).
    분리만 하고 숨기면 「왜 계정 합이 총합보다 작나」가 미궁이 되므로 비중을 한 조각으로
    노출한다. 미상이 0(=전량 귀속)이면 빈 문자열이고, 반올림이 0% 로 떨어지는 소량은
    `<1%` 로 적어 「있는데 0」을 안 만든다.
    """
    if not isinstance(xc_cov, dict):
        return ""     # 신뢰불가 상류(원격 보기 릴레이)가 실어 보낸 잡값
    try:
        total = int(xc_cov.get("total") or 0)
        unknown = int(xc_cov.get("unknown") or 0)
    except (TypeError, ValueError):
        return ""     # 문자열·dict 등 — 판이 터지는 대신 표기를 생략한다
    if total <= 0 or unknown <= 0 or unknown > total:
        return ""
    pct = 100.0 * unknown / total
    shown = "<1" if pct < 0.5 else "%d" % round(pct)
    return "  " + i18n.t("pscreen.tklog_unknown", pct=shown)


def sigma_text(total_all, win, xc, xc_hosts, xc_cov):
    """Σ 요약(§10-D P6).

    트랜스크립트 실측(`usage_xc` full)이 있으면 그것을 1차 Σ 로 보이고 캐시를 별도로
    적으며, 스크랩 누계는 `활동~` 보조신호로 강등한다 — 스크랩은 cache 를 못 봐 실제의
    ~0.4%만 잡아서, 그대로 Σ 로 쓰면 **두 자릿수 배율 과소표시**다. 캐시는 읽기/쓰기를
    나눠 적는다(단가·의미가 달라 합치면 cache 구조를 못 본다).
    실측이 없으면(구판 서버·빈 `usage_xc`) 종전 스크랩 `~Σ`(+표시창 n) 폴백.
    """
    life = total_all if total_all is not None else win
    xc_full = (xc or {}).get("full", 0) or 0
    if xc_full > 0:
        return (i18n.t("pscreen.tklog_xc",
                       full=usagelog._fmt_tokens(xc_full),
                       cr=usagelog._fmt_tokens((xc or {}).get("cache_read", 0)),
                       cc=usagelog._fmt_tokens((xc or {}).get("cache_create", 0)),
                       scrape=usagelog._fmt_tokens(life))
                + host_text(xc_hosts) + unknown_text(xc_cov))
    sigma = "~Σ%s" % usagelog._fmt_tokens(life)   # ~ = 추정 라벨(S6 T3)
    if life != win:
        sigma += i18n.t("pscreen.tklog_disp", n=usagelog._fmt_tokens(win))
    return sigma


def summary_line(usage, total_all, win, xc, xc_hosts, xc_cov):
    """머리줄 **한 줄 전체** — 정본이 `#tktop` 에 넣는 그 문자열과 같다."""
    return limit_summary(usage) + i18n.t(
        "pscreen.tklog_scope",
        sigma=sigma_text(total_all, win, xc, xc_hosts, xc_cov))
