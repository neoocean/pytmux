#!/usr/bin/env python3
"""RTT 그래프 픽스처 생성기 (G9u).

파이썬 정본 `clientconn._NetReconnectMixin._rtt_graph_lines` 를 **직접 호출**해
표본 이력 → 그래프 줄들의 짝을 뽑는다. 손으로 옮겨 적으면 세로 1/8 블록·임계
점선·'측정 없음' 마커 같은 구석이 어긋나도 아무도 모른다 — 정본이 답지를 쓴다.

- `time.monotonic` 을 고정해(모듈 몽키패치) 출력이 결정적이게 한다.
- i18n 은 ko 기본 그대로 — 우리 msgid 가 곧 파이썬 ko 문구다(G9i 규약).

사용: python3 scripts/gen_rtt_fixture.py   (pytmux 트리가 옆에 있어야 한다)
출력: crates/proto/tests/fixtures/rtt_graph.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PYTMUX = os.path.join(HERE, "..", "..")
sys.path.insert(0, PYTMUX)

from pytmuxlib import clientconn  # noqa: E402

NOW = 1_000_000.0  # 고정 '지금'(monotonic 초)

clientconn.time.monotonic = lambda: NOW


class Stub:
    """_rtt_graph_lines 가 만지는 self 표면만 든 껍데기."""
    _RTT_WINDOW = clientconn._NetReconnectMixin._RTT_WINDOW
    _RTT_GRAPH_W = clientconn._NetReconnectMixin._RTT_GRAPH_W
    _RTT_GRAPH_H = clientconn._NetReconnectMixin._RTT_GRAPH_H
    # 세로 블록 표·그래프 함수는 _RestartVersionMixin 쪽에 있다.
    _RTT_VBLOCKS = clientconn._RestartVersionMixin._RTT_VBLOCKS

    def __init__(self, hist, thr=0.4):
        self._net_rtt_hist = hist
        self.net_rtt_threshold = thr


def lines(hist, thr=0.4, width=48, height=5):
    fn = clientconn._RestartVersionMixin._rtt_graph_lines
    return fn(Stub(hist, thr), width=width, height=height)


def age(sec):
    return NOW - sec


CASES = {
    # 낮은 RTT 가 창 전체에 촘촘히 — 자동 스케일이 1~3ms 를 뭉개지 않는다.
    "dense_low": {
        "hist": [(age(3600 - i * 30), 0.001 + (i % 3) * 0.001) for i in range(120)],
        "thr": 0.4,
    },
    # 임계를 넘는 스파이크 하나 — 기준선(┄)이 스케일 안으로 들어온다.
    "spike_over_threshold": {
        "hist": [(age(1800), 0.5), (age(1500), 0.02), (age(60), 0.03)],
        "thr": 0.4,
    },
    # 듬성듬성 — 측정 없는 칸의 '·' 바닥 마커와 범례.
    "sparse_gaps": {
        "hist": [(age(3500), 0.05), (age(1200), 0.08), (age(10), 0.06)],
        "thr": 0.4,
    },
    # 전부 0 — vmax 가드(0 나눗셈 없이 최소 1/8 칸).
    "all_zero": {
        "hist": [(age(600), 0.0), (age(300), 0.0)],
        "thr": 0.4,
    },
    # 좁은 폭(12) — 축 라벨 패딩이 안 깨진다.
    "narrow": {
        "hist": [(age(1800), 0.1), (age(30), 0.2)],
        "thr": 0.4,
        "width": 12,
    },
    # 창 밖(61분 전) 표본만 — None(그래프 생략).
    "only_stale": {
        "hist": [(age(3660), 0.05)],
        "thr": 0.4,
    },
}


def graph_data(hist, thr=0.4, width=48, height=5):
    """GraphData 값 계산 (Rust 코드의 graph_data 메서드와 동일 로직)."""
    if not hist or width == 0 or height == 0:
        return None

    # ⛔ 클래스는 모듈이 아니다 — `from clientconn._RestartVersionMixin import …` 는
    #   어느 트리에서도 안 도는 줄이었고(`ModuleNotFoundError: clientconn`), 그것 하나가
    #   **합본 게이트의 첫 스텝을 통째로 FAIL** 로 만들고 있었다. 값의 주인도 그 클래스가
    #   아니라 `_NetReconnectMixin` 이다(위 §Stub 이 이미 그렇게 읽는다 — 한 파일 안에서
    #   같은 값을 두 술어로 물던 자리다).
    span = clientconn._NetReconnectMixin._RTT_WINDOW

    buckets = [None] * width
    raw = []
    for ts, rtt in hist:
        age = NOW - ts
        if not (0.0 <= age <= span):
            continue
        raw.append(rtt)
        col_back = int(age / span * width)
        col = width - 1 - min(col_back, width - 1)
        buckets[col] = max(rtt, buckets[col]) if buckets[col] is not None else rtt

    if not raw:
        return None

    peak = max(raw)
    vmax = peak if peak > 0 else thr if thr > 0 else 1e-9
    avg = sum(raw) / len(raw)
    has_gaps = any(b is None for b in buckets)

    return {
        "buckets": buckets,
        "threshold": thr,
        "vmax": vmax,
        "peak": peak,
        "avg": avg,
        "count": len(raw),
        "has_gaps": has_gaps,
    }


def main():
    out = {"_comment": "gen_rtt_fixture.py 가 파이썬 정본에서 뽑음 — 손으로 고치지 말 것",
           "now": NOW,
           "cases": {}}
    for name, spec in CASES.items():
        lines_val = lines(spec["hist"], spec.get("thr", 0.4),
                          spec.get("width", 48), spec.get("height", 5))
        data_val = graph_data(spec["hist"], spec.get("thr", 0.4),
                              spec.get("width", 48), spec.get("height", 5))
        out["cases"][name] = {
            "hist": spec["hist"],
            "thr": spec.get("thr", 0.4),
            "width": spec.get("width", 48),
            "height": spec.get("height", 5),
            "lines": lines_val,   # None 이면 그래프 생략 계약
            "data": data_val,     # GraphData 값
        }
    path = os.path.join(HERE, "..", "crates", "proto",
                        "tests", "fixtures", "rtt_graph.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"{path}: {len(out['cases'])} cases")


if __name__ == "__main__":
    main()
