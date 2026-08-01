#!/bin/bash
# pid 의 앱을 전경으로 세우고 글자·특수키를 넣는다. macOS 판 send_keys_to_window.ps1.
#
# System Events 를 쓰므로 **손보다 먼저 권한**이다: 이 셸(터미널)에
# 손쉬운 사용(Accessibility) 권한이 없으면 첫 호출이 조용히 실패하거나 OS 가 묻는다.
# 전경 규칙은 윈도우 하네스와 같다 — 남의 창에 키를 넣지 않기 위한 가드다.
#
# 사용법: send_keys_mac.sh <pid> text "<글자들>"     # 글자 입력
#         send_keys_mac.sh <pid> key  <keycode>      # 특수키(36=Return, 53=Esc)
set -euo pipefail
pid="${1:?pid}"; mode="${2:?text|key}"; value="${3:?value}"

/usr/bin/osascript - "$pid" "$mode" "$value" <<'EOF'
on run argv
    set thePid to item 1 of argv as integer
    set theMode to item 2 of argv
    set theValue to item 3 of argv
    tell application "System Events"
        set theProc to first process whose unix id is thePid
        set frontmost of theProc to true
        delay 0.15
        if theMode is "text" then
            keystroke theValue
        else
            key code (theValue as integer)
        end if
    end tell
end run
EOF
echo "sent $mode to pid $pid"
