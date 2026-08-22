#!/bin/sh
# MIT 경계 게이트.
#
# 이 워크스페이스는 MIT 인데, 출처인 warp 저장소는 두 크레이트를 빼면 전부 AGPL 이다.
# 실수로 AGPL 크레이트가 의존 그래프에 다시 들어오는 것을 막는다 — 사람이 눈으로
# 확인하는 대신 cargo 에게 물어본다.
#
# 통과 조건은 둘이다:
#
#  ① 의존 그래프의 **로컬(path) 크레이트**가 아래 ALLOWED 와 정확히 일치한다(이 파일).
#  ② **외부 의존**의 라이선스가 전부 허용 목록 안이고, 배포 이진 옆에 갈 저작권 고지
#     (`THIRD-PARTY-NOTICES.md`)가 지금 의존 그래프와 같다
#     (`third_party_notices.py --check` — 그 파일 머리말이 근거를 쥔다).
#
# ⛔ **②는 2026-08-17 에 생겼다**(검수 2026-08-09 B-7 · pytmux/pytmux-193). 그전까지 여기
# 적혀 있던 *"외부 crates.io 의존은 이 검사 대상이 아니다(전부 허용적 라이선스로 확인된
# 것들)"* 는 **아무도 다시 확인하지 않는 문장**이었다 — 사람이 한 번 세어 적어 둔 값이고,
# 새 의존이 GPL 단독으로 들어와도 이 게이트는 초록이었다. 그리고 MIT·BSD·ISC·Zlib·
# Apache-2.0 은 이진 재배포에도 고지 재현을 요구하는데 `client/build/` 의 배포 이진 셋
# 옆에는 그 글이 **한 줄도** 없었다.
#
# 실패하면 PROVENANCE.md 를 읽고, 새 크레이트가 정말 자체 구현인지 확인한 뒤
# 아래 ALLOWED 에 추가하고 PROVENANCE.md §2 표도 함께 갱신할 것.

set -eu

cd "$(dirname "$0")/.."

# warpui/warpui_core = warp 에서 가져온 MIT 크레이트.
# 나머지 여섯은 AGPL 원본을 대체한 자체 구현(PROVENANCE.md §2).
# base·proto·gui·tui·claude·clip = 우리가 새로 쓴 클라이언트 코드
# (2026-08-01 에 `pytmux_client_` 접두를 벗겼다 — 경로가 이미 pytmux/client 아래다).
ALLOWED="command
markdown_parser
claude
clip
base
gui
proto
string-offset
sum_tree
warp_errors
warp_util
warpui
warpui_core"

# ★ rustup 은 이진을 `~/.cargo/bin` 에 두고 PATH 는 셸 프로필(`~/.cargo/env`)에서
#   세우는데, **비대화형 셸은 그 프로필을 안 읽는다**(에이전트 툴 환경·launchd·CI).
#   깔려 있는 것을 "없다"고 말하면 게이트가 조용히 SKIP 되고, 그 SKIP 이 *"이 상자에서는
#   Rust 를 못 잰다"* 로 굳는다 — pytmux-33 이 그 값을 나흘 치렀다. 그러니 안내만 하지
#   말고 **여기서 찾아 세운다**(합본 게이트 `check_all.py` 의 `find_cargo` 와 같은 규칙).
if ! command -v cargo >/dev/null 2>&1 && [ -x "$HOME/.cargo/bin/cargo" ]; then
    PATH="$HOME/.cargo/bin:$PATH"
    export PATH
fi

if ! command -v cargo >/dev/null 2>&1; then
    echo "check_licenses: cargo 를 찾을 수 없다 (PATH 에도 ~/.cargo/bin 에도 없다)" >&2
    exit 2
fi

# 워크스페이스 경로는 **cargo 에게 묻는다**. `pwd` 로 만들면 Windows 에서 어긋난다 —
# Git Bash 의 `pwd` 는 `/d/...` 인데 cargo 는 `D:\...` 를 찍으므로 `grep -F` 가 한 줄도
# 못 잡고, 그러면 이 게이트는 **아무것도 안 지킨 채 rc 0 으로 통과한다**(2026-07-28 에
# 실제로 그 상태였다 — P6 에서 게이트를 Windows 로 옮길 때 같이 따라오지 않았다).
# 양쪽의 구분자를 `/` 로 통일해 비교한다.
ROOT=$(cargo locate-project --workspace --message-format plain 2>/dev/null | tr '\\' '/')
ROOT=${ROOT%/Cargo.toml}

# cargo tree 의 로컬 크레이트 줄에서 이름만 뽑는다. 중복(*)·버전은 버린다.
FOUND=$(cargo tree --workspace --prefix none 2>/dev/null \
    | tr '\\' '/' \
    | grep -F "$ROOT/crates/" \
    | awk '{print $1}' \
    | sort -u)

# 한 줄도 못 잡았다면 통과가 아니라 **고장**이다. 이 워크스페이스에는 로컬 크레이트가
# 반드시 있으므로, 빈 결과는 위 경로 짝맞춤이 깨졌다는 뜻이다.
if [ -z "$FOUND" ]; then
    echo "FAIL: 로컬 크레이트를 한 개도 못 찾았다 — 이 검사가 고장 난 것이다" >&2
    echo "  워크스페이스 경로: ${ROOT:-(못 구함)}" >&2
    echo "  → cargo tree 의 경로 표기와 맞는지 확인할 것(OS 별 구분자)" >&2
    exit 1
fi

UNEXPECTED=$(echo "$FOUND" | grep -vxF "$ALLOWED" || true)
MISSING=$(echo "$ALLOWED" | grep -vxF "$FOUND" || true)

rc=0

if [ -n "$UNEXPECTED" ]; then
    echo "FAIL: 허용 목록에 없는 로컬 크레이트가 의존 그래프에 있다:" >&2
    echo "$UNEXPECTED" | sed 's/^/  - /' >&2
    echo "  → AGPL 원본을 되살린 것이 아닌지 확인할 것 (PROVENANCE.md §4)" >&2
    rc=1
fi

if [ -n "$MISSING" ]; then
    echo "WARN: 허용 목록에 있지만 의존 그래프에 없는 크레이트:" >&2
    echo "$MISSING" | sed 's/^/  - /' >&2
    echo "  → 더 이상 안 쓰면 ALLOWED 와 PROVENANCE.md 에서 지울 것" >&2
fi

# AGPL 라이선스 전문이 트리에 다시 들어오지 않았는지도 본다(가장 굵은 신호).
# 이 스크립트 자신은 그 문자열을 담고 있으므로 스캔에서 뺀다.
if grep -rl "GNU AFFERO GENERAL PUBLIC LICENSE" . \
        --exclude-dir=target --exclude-dir=.git \
        --exclude="$(basename "$0")" 2>/dev/null | grep -q .; then
    echo "FAIL: AGPL 라이선스 전문이 트리에 있다 — AGPL 코드가 딸려 들어왔다는 뜻이다" >&2
    rc=1
fi

[ "$rc" -eq 0 ] && echo "OK: 로컬 크레이트 $(echo "$FOUND" | wc -l | tr -d ' ')개, 전부 허용 목록 안"

# ② 외부 의존 + 배포 고지. **여기서 멈추지 않는다** — 위가 떨어져도 이것을 재서 한 번에
# 다 보여 준다(합본 게이트가 "한 스텝이 넘어져도 나머지를 돈다"로 사는 것과 같은 결).
#
# 판정을 파이썬에 두는 이유: 라이선스 식(`(MIT OR Apache-2.0) AND Unicode-DFS-2016`)을
# 평가하고 크레이트 449개의 전문을 모아 비교하는 일이라 sh 로 적으면 그 자체가 결함
# 원천이 된다. `build_release.sh` 가 `check_mirror.py` 를 부르는 것과 같은 자리다.
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "check_licenses: $PY 를 찾을 수 없다 — 외부 의존·배포 고지를 못 쟀다" >&2
    echo "  → 못 쟀으면 통과가 아니다. PYTHON=<경로> 로 지정할 것" >&2
    exit 2
fi
"$PY" scripts/third_party_notices.py --check || rc=$?

exit "$rc"
