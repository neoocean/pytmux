#!/bin/bash
# pid → PNG. macOS 판 capture_window.ps1 (라이브 확인 하네스 — CLAUDE.md 표 참조).
#
# 창 ID 는 CGWindowList 에서 pid 로 찾는다(스크린 레코딩 권한이 필요할 수 있다 —
# 권한이 없으면 창 이름이 비어 보이지만 ID 는 나온다). 캡처는 screencapture -l 이라
# 창이 **전경이 아니어도** 된다(윈도우 BitBlt 와 달리 가림도 무관).
#
# 사용법: capture_window_mac.sh <pid> <out.png>
set -euo pipefail
pid="${1:?pid}"; out="${2:?out.png}"

wid=$(swift - "$pid" <<'EOF'
import CoreGraphics
import Foundation
let pid = Int32(CommandLine.arguments[1])!
let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
guard let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else { exit(2) }
// 같은 pid 의 창이 여럿이면 가장 큰 것(본 창)을 고른다 — 그림자·팝오버가 따로 잡힌다.
var best: (id: Int, area: Double) = (0, 0)
for w in list {
    guard let owner = w[kCGWindowOwnerPID as String] as? Int32, owner == pid,
          let id = w[kCGWindowNumber as String] as? Int,
          let bounds = w[kCGWindowBounds as String] as? [String: Double] else { continue }
    let area = (bounds["Width"] ?? 0) * (bounds["Height"] ?? 0)
    if area > best.area { best = (id, area) }
}
guard best.id != 0 else { exit(3) }
print(best.id)
EOF
)

screencapture -x -o -l "$wid" "$out"
echo "captured window $wid of pid $pid -> $out"
