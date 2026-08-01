#!/bin/sh
# 계층 게이트 — "뷰만 두 벌이고 상태·키맵·명령은 한 벌"을 강제한다.
#
# 뷰는 GUI 한 벌이지만(2026-08-01 에 Rust TUI 를 지웠다) **상태·키맵·명령은 뷰와 갈라
# 둔다**. 이유가 사라진 것이 아니다: 정본 Textual 클라와의 대조가 `base`/`proto` 를 통해
# 이뤄지고(픽스처·적합성 테스트), 뷰가 그 지식을 자기 안으로 끌어들이면 그 대조가 무의미
# 해진다. 그리고 뷰가 하나여도 `base` 에 UI 의존이 들어오면 `base` 를 헤드리스로 못 잰다.
#
# 이 스크립트는 그 규칙을 기계로 확인한다 — 문서로 적어 두는 것과 달리 어기면 CI 가 멈춘다.

set -eu

cd "$(dirname "$0")/.."

if ! command -v cargo >/dev/null 2>&1; then
    echo "check_layering: cargo 를 찾을 수 없다 (PATH 에 ~/.cargo/bin 이 있는지 확인)" >&2
    exit 2
fi

rc=0

# ── 1. 중립 크레이트의 의존 그래프에 UI 가 없어야 한다 ────────────────────────
# cargo tree 로 묻는 이유: Cargo.toml 만 grep 하면 간접 의존(다른 크레이트를 거쳐
# 딸려오는 경우)을 놓친다.
#
# base  = 상태·액션·키맵, proto = 서버와의 대화, claude = 트랜스크립트 읽기,
# clip = OS 클립보드. 넷 다 뷰와 갈라 둔 것이므로 어느 UI 백엔드도
# 알면 안 된다.
for neutral in claude clip base proto; do
    deps=$(cargo tree -p "$neutral" --prefix none 2>/dev/null | awk '{print $1}' | sort -u)
    ui=$(echo "$deps" | grep -xE 'warpui|warpui_core|ratatui' || true)
    if [ -n "$ui" ]; then
        echo "FAIL: $neutral 이 UI 크레이트를 의존한다:" >&2
        echo "$ui" | sed 's/^/  - /' >&2
        echo "  → 백엔드 중립이어야 한다. 뷰에 둘 코드가 내려온 것은 아닌지 볼 것." >&2
        rc=1
    fi

    # 위를 통과해도 소스가 UI 타입을 참조하면 여기서 걸린다.
    if ! cargo check -p "$neutral" --quiet 2>/dev/null; then
        echo "FAIL: $neutral 이 단독으로 빌드되지 않는다" >&2
        cargo check -p "$neutral" 2>&1 | sed 's/^/  /' >&2
        rc=1
    fi
done

# ── 3. 지운 TUI 백엔드가 되살아나지 않았는지 ────────────────────────────────
# ratatui 는 지운 Rust TUI 의 백엔드였다. 다시 딸려 들어오면 뷰가 둘로 갈라지기
# 시작했다는 뜻이다(그 갈라짐을 재는 장치는 이제 없다).
if cargo tree -p gui --prefix none 2>/dev/null | awk '{print $1}' | grep -qx 'ratatui'; then
    echo "FAIL: GUI 크레이트가 ratatui(지운 TUI 백엔드)를 의존한다" >&2
    rc=1
fi

# ── 4. 키 목록이 뷰에 복제되지 않았는지 ──────────────────────────────────────
# 두 뷰는 base 의 BINDINGS 를 순회해서 키를 건다. 뷰 안에 키 이름 문자열이 직접
# 나타나면 목록을 따로 적기 시작했다는 신호다.
for crate_dir in crates/gui; do
    # 테스트는 입력을 합성해야 하므로 키 이름을 쓰는 게 정상이다 — 제외한다.
    hits=$(grep -rnE '"(down|up|escape|enter|space|pageup|pagedown)"' "$crate_dir/src" \
        --include='*.rs' 2>/dev/null | grep -v '_tests.rs' || true)
    if [ -n "$hits" ]; then
        echo "FAIL: $crate_dir 뷰 코드에 키 이름이 직접 적혀 있다:" >&2
        echo "$hits" | sed 's/^/  /' >&2
        echo "  → 키는 base::BINDINGS 한 곳에서만 정의한다." >&2
        rc=1
    fi
done

[ "$rc" -eq 0 ] && echo "OK: base·proto·claude·clip 은 UI 무의존 · 지운 TUI 백엔드 부재 · 키 정의는 한 곳"
exit "$rc"
