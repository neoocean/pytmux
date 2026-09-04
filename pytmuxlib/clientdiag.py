"""클라이언트 런타임 계측 — `:debug-stats` (pytmux-382 §2·§8-⑤).

## 왜 있나

제보는 *"tui 를 장시간(수 일) 띄워놓으면 응답이 점점 더 느려집니다"* 다. 그 재현이
**며칠**이라 이 이슈의 첫 산출물은 고침이 아니라 **재는 법**이어야 했고, 이슈가
*"그 한 줄이 이 이슈에서 가장 값이 크다 — 없으면 다음에도 며칠을 다시 태운다"* 로
적은 것이 이 명령이다.

☠ **한 번 지어졌다가 사라졌다.** 그 바이트는 박제 CL 73983 에 있었는데 그 CL 이
p4d 에서 사라졌고(2026-09-01 실측), depot 어디에도 안 들어갔다. 그래서 **다시**
짓는다 — 다행히 *무엇을* 재야 하는지는 그때의 계측이 이미 정해 뒀다:

| 잰 것 | 그때 나온 값 | 뜻 |
| --- | --- | --- |
| `_composite()` 한 번 | 0.68 → 0.85 ms (**평탄**) | 그리는 경로는 헛수고다 |
| 산 객체 | 52k → 193k 뒤 **눕는다** | 누적이 아니라 **포화** |
| `gc.collect()` | 18 → 84 ms 뒤 **멎는다** | 상시 세금이지 누적이 아니다 |
| gen2 **빈도** | 0.04회/사이클 **불변** | "자주 와서 느려진다"가 아니다 |
| 자라는 것의 이름 | `FIFOCache` = `Strip` × **7** | Textual 내부(클래스 `lru_cache`) |

⇒ 그래서 이 모듈이 내는 표는 **그 다섯 축**이다. 다음 사람이 「느려졌다」고 느낀
그 순간에 이 한 줄을 쳐서, 위 값과 견주면 «같은 기제인가»가 바로 갈린다.

## ⛔ 무엇을 안 하나

- **아무것도 안 고친다.** 재고 보여줄 뿐이다.
- **`gc.collect()` 를 부르지 않는다**(기본). 전체 수거는 단일 스레드 앱을 수십 ms
  멎게 하고, 그것을 진단 명령이 제 손으로 만들면 **재려던 그 증상을 자기가 만든다**.
  `collect=True` 로 명시할 때만 부르고, 그때는 그 사실을 표에 적는다.
- 이름을 보고 무엇을 짐작하지 않는다 — 세는 것은 `gc.get_objects()` 의 **실물**이다.
"""
from __future__ import annotations

import gc
import os
import sys
import time

from . import i18n

# 표에 실을 상위 종류 수. 길면 팝업이 스크롤되고, 짧으면 자라는 놈을 놓친다.
TOP_TYPES = 12

# pytmux-382 의 2026-09-01 계측이 남긴 **기준선**(macOS · Python 3.13 · Textual 8.2.5).
# ⚠ 값 자체가 계약은 아니다 — 다음 사람이 「같은 기제인가」를 견주는 **눈금**이다.
BASELINE = {
    "objects_settled": 193_000,     # PromptScreen 200사이클 뒤 평탄
    "gc2_pause_ms": 64.0,           # gen2 정지 중앙값(포화 후)
    "gc2_per_cycle": 0.04,          # gen2 빈도(불변)
    "composite_ms": 1.3,            # 평탄
}


def rss_bytes() -> int | None:
    """이 프로세스의 상주 메모리. 못 알아내면 None(추측하지 않는다)."""
    try:
        import resource
    except ImportError:
        return None
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ⚠ 단위가 OS 마다 다르다 — macOS 는 **바이트**, Linux 는 **KiB** 다.
    # 이걸 틀리면 표가 1024배 어긋난 채 「메모리가 폭증했다」로 읽힌다.
    return ru if sys.platform == "darwin" else ru * 1024


def type_counts(objs=None) -> dict:
    """산 객체를 **종류 이름**으로 센다.

    ⛔ `type(o).__qualname__` 으로 묶지 않는다 — 서로 다른 모듈의 같은 이름이 한
    열쇠에 겹쳐, 2026-08-25 계측이 실제로 그 함정을 밟았다(`ScrollBar` 가 새는
    것처럼 보였다). 모듈명을 붙여 구분한다."""
    objs = gc.get_objects() if objs is None else objs
    out: dict = {}
    for o in objs:
        t = type(o)
        key = f"{t.__module__}.{t.__qualname__}"
        out[key] = out.get(key, 0) + 1
    return out


def gc_generations() -> list:
    """세대별 수거 **횟수**. gen2 빈도가 늘었나를 보는 자리다.

    ★ 2026-09-01 계측의 결론이 여기 걸린다: *"gen2 빈도는 0.04회/사이클로 **안
    변한다** — '자주 와서 느려진다'가 아니다."* 이 값이 그때와 다르면 그 결론이
    이 상자에서는 안 서는 것이고, 조사가 다시 열린다."""
    return [{"gen": i, "collections": s.get("collections", 0),
             "collected": s.get("collected", 0),
             "uncollectable": s.get("uncollectable", 0)}
            for i, s in enumerate(gc.get_stats())]


def screen_depth(app) -> int | None:
    """열려 있는 판의 수. 판을 여닫은 것이 **거둬지나**를 보는 가장 싼 값이다
    (pytmux-382 ③ⓒ — 실측 반증됐지만 회귀는 이 한 값으로 잡힌다)."""
    stack = getattr(app, "screen_stack", None)
    return len(stack) if stack is not None else None


def live_timers(app) -> int | None:
    """살아 있는 Textual `Timer` 수 — ③ⓐ(모달 0.2초 인터벌)의 회귀 감시.

    그 후보는 실측으로 **반증**됐다(100회 여닫기 → 6 → 6). 그래도 값을 낸다:
    반증된 것이 다시 참이 되는 것이 곧 회귀이고, 그때 이 숫자가 먼저 움직인다."""
    try:
        from textual.timer import Timer
    except ImportError:
        return None
    return sum(1 for o in gc.get_objects() if isinstance(o, Timer))


def collect_stats(app, *, collect: bool = False) -> dict:
    """한 장. `collect=True` 일 때만 전체 수거를 부르고 그 시간을 잰다.

    ⛔ 기본이 False 인 이유는 모듈 docstring 에 있다 — 진단이 제 손으로 멈칫을
    만들면 안 된다."""
    t0 = time.perf_counter()
    gc_ms = None
    if collect:
        gc.collect()
        gc_ms = (time.perf_counter() - t0) * 1000.0
    objs = gc.get_objects()
    counts = type_counts(objs)
    return {
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "rss": rss_bytes(),
        "objects": len(objs),
        "collected_now": collect,
        "gc_collect_ms": gc_ms,
        "generations": gc_generations(),
        "thresholds": gc.get_threshold(),
        "screen_depth": screen_depth(app),
        "timers": live_timers(app),
        "top": sorted(counts.items(), key=lambda kv: -kv[1])[:TOP_TYPES],
    }


def _mb(n):
    return "?" if n is None else f"{n / 1048576:.1f} MB"


def render(stats: dict) -> list:
    """팝업에 실을 줄들. 값 옆에 **기준선**을 함께 적는다 — 숫자 하나만 보면
    「많은 건가」를 알 수 없고, 이 명령은 그것을 알려주려고 있다."""
    b = BASELINE
    lines = [
        f"pid {stats['pid']} · python {stats['python']} · RSS {_mb(stats['rss'])}",
        f"산 객체 {stats['objects']:,}"
        f"   (기준선 ≈{b['objects_settled']:,} 에서 «눕는다» — 그보다 크게·계속"
        f" 자라면 그때가 새 결함이다)",
    ]
    if stats["collected_now"]:
        lines.append(
            f"gc.collect() {stats['gc_collect_ms']:.1f} ms"
            f"   (기준선 ≈{b['gc2_pause_ms']:.0f} ms · 이 값은 **포화**한다)")
    else:
        lines.append("gc.collect() 안 불렀다 — `debug-stats -c` 로 재면 잰다"
                     "(전체 수거는 앱을 그만큼 멎게 한다)")
    for g in stats["generations"]:
        lines.append(f"  gen{g['gen']}: 수거 {g['collections']:,}회 · "
                     f"거둔 것 {g['collected']:,} · "
                     f"못 거둔 것 {g['uncollectable']:,}")
    lines.append(f"임계 {stats['thresholds']}"
                 f" · 판 깊이 {stats['screen_depth']}"
                 f" · 산 Timer {stats['timers']}")
    lines.append("― 산 객체 상위 (자라는 것의 이름) ―")
    for name, n in stats["top"]:
        note = ""
        if name.endswith("FIFOCache"):
            note = "  ← Strip 하나가 일곱 개를 짓는다(대부분 빈 캐시)"
        elif name.endswith("Strip"):
            note = "  ← 이것이 줄어야 위가 준다"
        lines.append(f"  {n:>8,}  {name}{note}")
    lines.append("")
    lines.append("pytmux-382: 여기 값이 «자라다 눕는» 모양이면 상시 세금이지 누적이"
                 " 아니다. 계속 자라면 그것이 새 결함이다.")
    return lines


# ── 서버 절반(pytmux-382) ─────────────────────────────────────────────────────
#
# `debug_stats` 회신(`serverdiag.collect_stats` 가 만든 숫자 표)을 **이 클라의 로케일로**
# 줄로 짓는다. 서버는 문장을 짓지 않는다(서버가 지은 글은 서버 로케일로 굳어 영어
# 사용자에게 한국어로 뜬다 — pytmux-419 부류). Rust 클라의 같은 자리는
# `proto::diag::ServerStats::lines` — 두 벌이 **같은 줄 순서·같은 라벨**을 낸다.


def _fmt_secs(v):
    return "?" if v is None else f"{float(v):.1f}s"


def _uptime(s):
    """초 → `3d 4h` · `2h 05m` · `12m 30s`(Rust `proto::diag::uptime` 과 같은 모양)."""
    s = max(0, int(s))
    d, h, m, sec = s // 86400, (s // 3600) % 24, (s // 60) % 60, s % 60
    if d > 0:
        return f"{d}d {h}h"
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m {sec:02d}s"


def render_server(stats: dict, now=None) -> list:
    """`debug-stats` 판의 **서버 절반** 줄들. `now` 는 epoch 초(프로브 회차의 나이용).

    ⛔ 못 잰 값은 `?` 다 — 0 으로 적으면 「fd 가 0 개」로 읽힌다(Windows)."""
    from . import i18n
    t = i18n.t
    g = stats.get

    def cnt(v):
        return "?" if v is None else str(v)

    up = g("uptime_s")
    lines = [
        f"― {t('diag.server', '서버 쪽')} ―",
        f"  pid {g('pid', '?')} · python {g('python') or '?'} · "
        f"{t('diag.up', '기동 뒤')} {'?' if up is None else _uptime(up)}",
        f"  {t('diag.rss', '상주 메모리')} {_mb(g('rss'))} · fd {cnt(g('fds'))} · "
        f"{t('diag.threads', '스레드')} {cnt(g('threads'))} · "
        f"{t('diag.tasks', 'asyncio 태스크')} {cnt(g('tasks'))}",
    ]
    idle = [("?" if v is None else f"{float(v):.0f}s") for v in (g("client_idle_s") or [])]
    seen = f" ({t('diag.last_seen', '마지막 수신')} {' · '.join(idle)})" if idle else ""
    lines.append(
        f"  {t('diag.clients', '클라')} {g('clients', 0)}{seen} · "
        f"{t('diag.remote_links', '원격 링크')} {g('remote_links', 0)} · "
        f"{t('diag.reconnecting', '재연결 중')} {g('remote_reconnecting', 0)}")
    lines.append(
        f"  {t('diag.sessions', '세션')} {g('sessions', 0)} · {t('diag.tabs', '탭')} "
        f"{g('windows', 0)} · {t('diag.panes', '패널')} {g('panes', 0)} · "
        f"{t('diag.scrollback', '스크롤백 행')} {cnt(g('scrollback_rows'))}")
    if g("objects") is not None:
        lines.append(f"  {t('diag.objects', '산 객체')} {g('objects'):,}")
    for gen in g("gc") or ():
        lines.append(
            f"    gen{gen.get('gen')}: {t('diag.gc_collections', '수거')} "
            f"{gen.get('collections', 0):,} · {t('diag.gc_collected', '거둔 것')} "
            f"{gen.get('collected', 0):,} · {t('diag.gc_uncollectable', '못 거둔 것')} "
            f"{gen.get('uncollectable', 0):,}")
    for name, n in (g("top") or ())[:6]:
        lines.append(f"    {n:>8,}  {name}")
    probe = g("usage_probe")
    if isinstance(probe, dict):
        ago = ""
        at = probe.get("at")
        if at is not None and now is not None and now >= at:
            ago = f" · {_uptime(now - at)} {t('diag.ago', '전')}"
        ok = probe.get("ok")
        verdict = ("?" if ok is None else
                   t("diag.ok", "성공") if ok else t("diag.failed", "실패"))
        lines.append(
            f"  /usage {t('diag.probe_last', '프로브 마지막 회차')}: "
            f"boot {_fmt_secs(probe.get('boot'))} · panel {_fmt_secs(probe.get('panel'))} · "
            f"total {_fmt_secs(probe.get('total'))} · {verdict}{ago}")
    else:
        lines.append(f"  /usage {t('diag.probe_never', '프로브가 아직 한 번도 안 돌았다')}")
    lines.append(f"  error.log {_mb(g('error_log_bytes'))}")
    return lines


i18n.register({
    "ko": {
        "diag.server": "서버 쪽", "diag.up": "기동 뒤", "diag.rss": "상주 메모리",
        "diag.threads": "스레드", "diag.tasks": "asyncio 태스크", "diag.clients": "클라",
        "diag.last_seen": "마지막 수신", "diag.remote_links": "원격 링크",
        "diag.reconnecting": "재연결 중", "diag.sessions": "세션", "diag.tabs": "탭",
        "diag.panes": "패널", "diag.scrollback": "스크롤백 행", "diag.objects": "산 객체",
        "diag.gc_collections": "수거", "diag.gc_collected": "거둔 것",
        "diag.gc_uncollectable": "못 거둔 것", "diag.probe_last": "프로브 마지막 회차",
        "diag.ok": "성공", "diag.failed": "실패", "diag.ago": "전",
        "diag.probe_never": "프로브가 아직 한 번도 안 돌았다",
        "diag.waiting": "서버가 아직 답하지 않았다",
    },
    "en": {
        "diag.server": "Server", "diag.up": "up", "diag.rss": "Resident memory",
        "diag.threads": "threads", "diag.tasks": "asyncio tasks", "diag.clients": "Clients",
        "diag.last_seen": "last seen", "diag.remote_links": "remote links",
        "diag.reconnecting": "reconnecting", "diag.sessions": "Sessions", "diag.tabs": "Tabs",
        "diag.panes": "Panes", "diag.scrollback": "scrollback rows", "diag.objects": "Live objects",
        "diag.gc_collections": "collections", "diag.gc_collected": "collected",
        "diag.gc_uncollectable": "uncollectable", "diag.probe_last": "probe last run",
        "diag.ok": "ok", "diag.failed": "failed", "diag.ago": "ago",
        "diag.probe_never": "probe has not run yet",
        "diag.waiting": "The server has not answered yet",
    },
})
