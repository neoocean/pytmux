#!/bin/sh
# Windows 게이트 — 두 번째 OS(사용자 결정 2026-07-27b)가 조용히 깨지지 않게 한다.
#
# macOS 에서 개발하는 동안 Windows 지원은 **아무도 안 보면 즉시 썩는다**. `#[cfg(unix)]`
# 하나, `std::os::unix` import 하나면 끝이고, 그건 Windows 박스에 가서야 드러난다.
# 그래서 매번 크로스 컴파일로 확인한다 — 링크는 안 하지만(그건 MSVC 링커가 필요하다)
# **cfg 분기와 타입은 전부 검사된다**.
#
# 범위: TUI 쪽 5개 + **GUI**.
#
# GUI 는 2026-07-28 까지 "macOS Metal 경로에 묶여 있어 Windows 목표가 아니다"라며 빠져
# 있었는데 **그 값이 틀렸다**: Metal 셰이더 컴파일은 macOS 빌드에서만 걸리고 Windows 는
# wgpu 의 dx12 경로를 탄다. 실제로 이 상자에서 `cargo build -p gui` 가
# 링크되고 창까지 떴다. "게이트가 영영 붉다"던 예상이 사실이 아니었으므로 넣는다 —
# 안 넣으면 GUI 가 다시 조용히 썩는다. 실제로 썩어 있었다: 키 표에 GUI 문법으로 못 읽는
# 항목(`"G"`)이 들어간 채 몇 달이 지났고, 아무도 GUI 를 안 띄워서 안 드러났다.
#
# 이 게이트가 통과해도 **실행은 보장하지 않는다**. ConPTY·실 서버 연결은 헤드리스로
# 검증할 수 없어 박스에서 손으로 봐야 한다(설계문서 §7 P6 의 라이브 목록).

set -eu

cd "$(dirname "$0")/.."

TARGET=x86_64-pc-windows-msvc
# clip 은 **이 게이트가 특히 필요한 크레이트**다 — 유일하게 OS 별 분기를 몸통에 갖고
# 있고(Windows 만 PowerShell 경로를 탄다), 그 분기는 다른 OS 에서 컴파일조차 안 된다.
CRATES="-p base -p proto -p claude \
        -p clip -p gui"

if ! command -v cargo >/dev/null 2>&1; then
    echo "check_windows: cargo 를 찾을 수 없다 (PATH 에 ~/.cargo/bin 이 있는지 확인)" >&2
    exit 2
fi

if ! rustup target list --installed 2>/dev/null | grep -qx "$TARGET"; then
    echo "check_windows: 대상 $TARGET 이 없다 — 설치: rustup target add $TARGET" >&2
    exit 2
fi

# --all-targets: 테스트·예제까지 함께 검사한다. P1 에서 배운 것 — 라이브러리만 보면
# 테스트 코드에 숨은 플랫폼 의존을 놓친다.
# 출력은 실패했을 때만 보여 준다 — 임포트한 upstream 트리(warpui_core)가 경고를
# 여럿 뱉어서, 그대로 두면 게이트의 성공/실패가 그 소음에 묻힌다.
log=$(mktemp)
if ! cargo check --target "$TARGET" $CRATES --all-targets --quiet >"$log" 2>&1; then
    echo "FAIL: Windows 대상 컴파일이 깨졌다" >&2
    grep -E "^(error|  -->)" "$log" | head -40 >&2
    echo "  → std::os::unix / UnixStream / cfg(unix) 가 중립 계층에 새어들지 않았는지 볼 것." >&2
    rm -f "$log"
    exit 1
fi
rm -f "$log"

echo "OK: $TARGET 컴파일(테스트·예제 포함) 통과 — 크레이트 5개"
