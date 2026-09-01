#!/bin/bash
# 글자 속성(SGR)을 **GUI 가 실제로 그린 그림**으로 뜬다 — pytmux-33 축 ⑷ 의 라이브 절반.
#
# # 왜 화면 캡처가 아닌가
#
# `--frame-dump` 은 앱이 자기 드로어블을 읽는 길이라 **화면도 화면 기록 권한도 필요 없다**
# (`gui/src/main.rs` §take_frame_dump). 맥에서 캡처 권한 없이 화면을 찍으면 실패가 아니라
# **벽지가 rc 0 으로** 돌아오므로, 이 자리는 정의상 그 길을 쓴다.
#
# # 무엇을 뜨나
#
# 줄마다 **같은 글**(`ABCDEFGH 한글가나`)에 **속성 하나만** 건 화면을 여덟 장 뜬다. 글이
# 같으므로 그림의 차이는 곧 그 속성의 차이다 — 판정은 `check_attr_shots.py` 가 한다.
# 한글이 함께 있는 것이 값이다: 기울임을 보조 글꼴에 걸면 한글이 **두부(▯)** 가 되는데
# (pytmux-133 · 2026-08-04 실측) 단위 오라클은 그것을 하나도 못 잡았다.
#
# ⛔ **게이트가 아니다**(`check_all.py` 에 안 넣는다) — 창과 서버가 있어야 도는 라이브
#    하네스다. 헤드리스 겹은 `gui/src/attr_render_conformance.rs` 가 상시로 잰다.
#
# ⚠ **내가 띄운 것만 겨냥한다**(루트 CLAUDE.md 의 안전 규율): 전용 `PYTMUX_HOME` 을 파서
#    거기에만 서버를 띄우고 같은 스코프로 내린다. 이름으로 죽이지 않는다.
#
# 사용법: gen_attr_shots.sh [출력디렉터리]        (기본 /tmp/pytmux-attr-shots)
# 종료:   0 다 떴다 · 2 GUI 이진이 없다 · 3 서버를 못 띄웠다 · 4 덤프가 떨어졌다
set -euo pipefail

out="${1:-/tmp/pytmux-attr-shots}"
here="$(cd "$(dirname "$0")" && pwd)"
client="$(dirname "$here")"
repo="$(dirname "$client")"
gui="${PYTMUX_GUI:-$client/target/debug/pytmux-gui}"
[ -x "$gui" ] || { echo "GUI 이진이 없다: $gui  (cd client && cargo build -p gui · 다른 데면 PYTMUX_GUI=<경로>)" >&2; exit 2; }

# 표본 한 줄 — 앞은 ASCII, 뒤는 한글(보조 글꼴로 간다). 둘을 한 줄에 두는 것이 요점이다.
sample='ABCDEFGH 한글가나'
# 이름:SGR. `plain` 이 기준이고 나머지는 그것과 견준다.
attrs='plain:0 bold:1 italic:3 underline:4 reverse:7 strike:9 fg:31 bg:44'
# ★ 그리고 **빈 줄 한 장**(`blank`)을 더 뜬다 — 판정기가 「글줄이 그림의 어디인가」를
#   heuristic 없이 잡는 자다: `plain` 과 `blank` 의 차이가 곧 그 줄이다. 창 크기·글꼴이
#   상자마다 달라 좌표를 못박을 수 없고, 크롬(탭 줄·제목 줄)을 글줄로 잘못 집으면
#   **모든 값이 0 이 되면서 「속성이 하나도 안 그려진다」로 읽힌다**(실측 2026-09-01).
#   ⚠ 빈 줄에도 `\n` 을 찍는다 — 안 찍으면 셸 프롬프트가 한 줄 위로 올라와 그 줄까지
#     차이에 섞인다.

rm -rf "$out"; mkdir -p "$out"
export PYTMUX_HOME="$out/home"
mkdir -p "$PYTMUX_HOME"
# ⛔ 에이전트 셸의 `NO_COLOR` 는 Textual 을 무채색 필터로 몰아 정본 쪽을 통째로 넘어뜨린다.
unset NO_COLOR || true

cleanup() {
  # ⚠ 스코프 안에서만 내린다 — 이름 매칭으로 넓히면 사용자의 라이브 세션이 죽는다.
  (cd "$repo" && python3 pytmux.py kill-server --yes >/dev/null 2>&1) || true
}
trap cleanup EXIT

(cd "$repo" && python3 pytmux.py start-server >/dev/null 2>&1) || { echo "서버를 못 띄웠다" >&2; exit 3; }
[ -e "$PYTMUX_HOME/state/default.sock" ] || { echo "소켓이 안 생겼다: $PYTMUX_HOME/state" >&2; exit 3; }

for spec in blank:_ $attrs; do
  name="${spec%%:*}"; sgr="${spec##*:}"
  probe="$out/p_$name.sh"
  # ⚠ `send-keys` 는 인자를 **붙여서** 보낸다(공백이 사라진다) — 그래서 공백 없는 경로 하나만 준다.
  {
    echo '#!/bin/sh'
    # 화면을 지우고 홈으로 — 명령을 친 줄까지 지워야 여덟 장의 «다른 곳»이 속성뿐이 된다.
    printf 'printf "\\033[2J\\033[H"\n'
    if [ "$name" = blank ]; then
      printf 'printf "\\n"\n'
    else
      printf 'printf "\\033[%sm""%s""\\033[0m\\n"\n' "$sgr" "$sample"
    fi
  } > "$probe"
  chmod +x "$probe"
  (cd "$repo" && python3 pytmux.py cmd send-keys "$probe" Enter >/dev/null 2>&1) || true
  sleep 1
  "$gui" --frame-dump="$out/$name.png" >/dev/null 2>&1 || { echo "덤프가 떨어졌다: $name" >&2; exit 4; }
done

echo "아홉 장 떴다(민 여덟 + 빈 줄 하나): $out"
echo "판정: python3 $here/check_attr_shots.py $out"
