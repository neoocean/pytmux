#!/bin/bash
# pid → PNG. macOS 판 capture_window.ps1 (라이브 확인 하네스 — CLAUDE.md 표 참조).
#
# 루트가 셋이고 **이 순서로** 밟는다(2026-08-09 에 이 상자에서 재서 정했다 — `pytmux-150`):
#   ① ScreenCaptureKit 창 캡처(macOS 14+) — 전경이 아니어도·가려져도 되고, 창 이미지라
#      **OS 크롬(맥 신호등)이 담긴다**. `--frame-dump` 가 정의상 못 담는 것이 이것이다.
#   ② `screencapture -l <창id>` — macOS 13 까지의 길.
#      ⚠ **macOS 15 에서는 안 된다**: 그 경로가 쓰는 `CGWindowListCreateImage` 가 15.0 에서
#      obsoleted 라 「could not create image from window」로 rc 1 을 낸다(실측 Darwin 24.6).
#   ③ `screencapture -R <창 rect>` — 화면을 오린다. 창 이미지가 아니라 **화면**이므로 그 창을
#      가리는 다른 창이 있으면 남의 그림이 찍힌다 → 앞선 창이 있으면 **찍지 않고 실패**한다.
#
# ⛔ **셋 다 화면 기록(Screen Recording) 권한이 있어야 한다.** 권한이 없는 셸에서 화면을 찍으면
#    실패하지 않고 **벽지 + 메뉴바만 담긴 그림이 rc 0 으로** 나온다(= 거짓 초록. 실측: 에이전트
#    셸에서 TextEdit 창을 띄워도 화면 캡처에 안 나오고, 화면 픽셀은 메뉴바 글자 말고는 창을
#    띄우기 전과 **바이트가 같다**). 그래서 ①이 맨 앞이다 — SCK 는 권한이 없으면 조용히
#    벽지를 주는 대신 `-3801 "The user declined TCCs…"` 로 **말한다**. 그 신호를 받으면 ②③을
#    시도하지 않고 진단을 찍고 rc 4 로 떨어진다(어차피 같은 권한을 쓴다).
#
# 사용법: capture_window_mac.sh <pid> <out.png>
# 종료:   0 찍었다 · 2 인자 · 3 그 pid 의 창을 못 찾았다 · 4 화면 기록 권한이 없다
#         5 창은 찾았는데 세 루트가 다 떨어졌다(까닭은 stderr 에 있다)
set -euo pipefail
pid="${1:?pid}"; out="${2:?out.png}"

# ⛔ `screencapture` 는 /usr/sbin 에 있다 — launchd 가 띄운 셸의 PATH 에는 그 자리가 없어서
#    이름으로 부르면 `command not found` 로 죽는다(에이전트 셸에서 실측). 절대경로로 잡는다.
sc="$(command -v screencapture || true)"
[ -n "$sc" ] || sc=/usr/sbin/screencapture
[ -x "$sc" ] || { echo "screencapture 를 못 찾았다: $sc" >&2; exit 5; }
# 창 찾기·SCK 캡처가 swift 로 돈다(Xcode 명령줄 도구). 없으면 여기서 말하고 끝낸다 —
# 없는 채로 가면 명령 치환이 빈 값을 주고 "창을 못 찾았다" 로 읽힌다.
command -v swift >/dev/null || { echo "swift 가 없다 — Xcode 명령줄 도구가 필요하다(xcode-select --install)" >&2; exit 5; }

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
swift_src="$tmpdir/capwin.swift"
cat > "$swift_src" <<'SWIFT'
// 창 찾기 + ① SCK 캡처. 창 정보는 어느 루트로 가든 필요하니 한 프로그램에서 낸다
// (컴파일이 호출마다 도는 값이라 두 번 부르지 않는다).
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let pid = Int32(CommandLine.arguments[1])!
let out = CommandLine.arguments[2]

// 같은 pid 의 창이 여럿이면 가장 큰 것(본 창)을 고른다 — winit 의 숨은 이벤트 타깃 창과
// 팝오버·그림자가 따로 잡힌다(Windows 쪽 `winlib.ps1` 이 같은 값을 치른다).
let opts: CGWindowListOption = [.optionOnScreenOnly, .excludeDesktopElements]
guard let list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID) as? [[String: Any]] else {
    FileHandle.standardError.write("CGWindowListCopyWindowInfo 가 nil 을 줬다\n".data(using: .utf8)!)
    exit(3)
}
// ⛔ 목록 순서(앞 = 위)를 쓰므로 걸러낸 뒤의 첨자로 앞뒤를 재면 어긋난다 — 원래 자리를 들고 간다.
struct Win { let z: Int; let id: Int; let pid: Int32; let rect: CGRect; let layer: Int; let owner: String }
var wins: [Win] = []
for (z, w) in list.enumerated() {
    guard let id = w[kCGWindowNumber as String] as? Int,
          let b = w[kCGWindowBounds as String] as? [String: Double] else { continue }
    wins.append(Win(z: z, id: id, pid: w[kCGWindowOwnerPID as String] as? Int32 ?? 0,
                    rect: CGRect(x: b["X"] ?? 0, y: b["Y"] ?? 0,
                                 width: b["Width"] ?? 0, height: b["Height"] ?? 0),
                    layer: w[kCGWindowLayer as String] as? Int ?? 0,
                    owner: w[kCGWindowOwnerName as String] as? String ?? "?"))
}
guard let win = wins.filter({ $0.pid == pid })
    .max(by: { $0.rect.width * $0.rect.height < $1.rect.width * $1.rect.height }) else {
    // ⛔ 「창이 없다」한 줄로 끝내지 않는다 — 그 문구가 *앱이 깨졌다* 로 읽힌 전례가 있다
    //    (CLAUDE.md 의 pytmux-32). 본 것을 적어서 던진다.
    var msg = "pid \(pid) 의 창이 화면 목록에 없다. 본 창 \(wins.count)개:\n"
    for w in wins.prefix(20) { msg += "  id=\(w.id) \(w.owner) \(w.rect) layer=\(w.layer)\n" }
    FileHandle.standardError.write(msg.data(using: .utf8)!)
    exit(3)
}
print("WID \(win.id)")
print("RECT \(Int(win.rect.origin.x)) \(Int(win.rect.origin.y)) \(Int(win.rect.width)) \(Int(win.rect.height))")
// 목록은 앞(위)에서 뒤로 온다 — 내 앞의 같은 층 창이 내 rect 를 물면 ③은 남의 그림을 찍는다.
let blockers = wins.filter { $0.z < win.z && $0.pid != pid && $0.layer <= win.layer && $0.rect.intersects(win.rect) }
print("BLOCKERS \(blockers.count) \(blockers.map { $0.owner }.joined(separator: ","))")

let sem = DispatchSemaphore(value: 0)
Task {
    if #available(macOS 14.0, *) {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
            guard let target = content.windows.first(where: { $0.windowID == CGWindowID(win.id) }) else {
                print("SCK error SCK 가 창 \(win.id) 를 모른다"); sem.signal(); return
            }
            let cfg = SCStreamConfiguration()
            cfg.width = Int(target.frame.width * 2)   // Retina 기준 — 배율은 그림에서 재라
            cfg.height = Int(target.frame.height * 2)
            cfg.showsCursor = false
            let img = try await SCScreenshotManager.captureImage(contentFilter:
                SCContentFilter(desktopIndependentWindow: target), configuration: cfg)
            guard let dest = CGImageDestinationCreateWithURL(
                URL(fileURLWithPath: out) as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                print("SCK error PNG 대상을 못 만들었다: \(out)"); sem.signal(); return
            }
            CGImageDestinationAddImage(dest, img, nil)
            guard CGImageDestinationFinalize(dest) else {
                print("SCK error PNG 를 못 썼다: \(out)"); sem.signal(); return
            }
            print("SCK ok \(img.width)x\(img.height)")
        } catch let e as NSError where e.code == -3801 {
            print("SCK denied \(e.localizedDescription)")
        } catch {
            print("SCK error \(error)")
        }
    } else {
        print("SCK unavailable macOS 14 미만 — ②로 간다")
    }
    sem.signal()
}
sem.wait()
exit(0)
SWIFT

info="$(swift "$swift_src" "$pid" "$out")"   # 창을 못 찾으면 여기서 rc 3 으로 끝난다
wid="$(awk '/^WID /{print $2}' <<<"$info")"
rect="$(awk '/^RECT /{print $2","$3","$4","$5}' <<<"$info")"
blockers="$(awk '/^BLOCKERS /{print $2}' <<<"$info")"
blocker_names="$(awk '/^BLOCKERS /{print $3}' <<<"$info")"
sck="$(awk '/^SCK /{print $2}' <<<"$info")"
sck_why="$(sed -n 's/^SCK [a-z]* //p' <<<"$info")"

case "$sck" in
    ok)
        echo "captured window $wid of pid $pid -> $out  (SCK · $sck_why)"
        exit 0
        ;;
    denied)
        # 여기서 멈추는 것이 값이다 — ②③도 같은 권한을 쓰므로, 계속 가면 **벽지 그림**을
        # 성공으로 돌려준다. 그 거짓 초록이 「창이 안 그려진다」는 오진을 만든다.
        cat >&2 <<MSG
화면 기록(Screen Recording) 권한이 없다 — 이 셸에서는 창을 찍을 수 없다.
  SCK: $sck_why
  ⛔ 화면 캡처가 실패하지 않고 **벽지 + 메뉴바만** 담긴 그림을 rc 0 으로 준다(거짓 초록).
  줄 수 있는 사람만 줄 수 있다: 시스템 설정 → 개인정보 보호 및 보안 → 화면 기록에서
  이 셸을 띄운 앱(에이전트라면 그 launchd 작업의 책임 앱)을 켠다. 켠 뒤 그 앱을 다시 띄운다.
  권한 없이 맥에서 볼 수 있는 것은 앱 드로어블뿐이다: pytmux-gui --frame-dump=<png>
  (⛔ 그것은 정의상 OS 크롬 — 맥 신호등 — 을 담지 못한다. CLAUDE.md 의 macOS 절.)
MSG
        exit 4
        ;;
esac
echo "① SCK 로는 못 찍었다($sck: $sck_why) — ②로 간다" >&2

if "$sc" -x -o -l "$wid" "$out" 2>/dev/null; then
    echo "captured window $wid of pid $pid -> $out  (screencapture -l)"
    exit 0
fi
echo "② screencapture -l 이 떨어졌다(macOS 15 에서는 이 실패가 정상이다 — 머리말 ②) — ③으로 간다" >&2

if [ "${blockers:-0}" != "0" ]; then
    echo "③ 을 안 한다: 창 앞에 다른 창 ${blockers}개가 겹쳐 있다($blocker_names)." >&2
    echo "   ③은 화면을 오리는 것이라 그대로 찍으면 **남의 창**이 담긴다. 그 창을 치우고 다시 부르라." >&2
    exit 5
fi
IFS=, read -r x y w h <<<"$rect"
"$sc" -x -R "$x,$y,$w,$h" "$out"
echo "captured screen region $rect (pid $pid 창 자리) -> $out  (screencapture -R)"
echo "⚠ 창 이미지가 아니라 화면을 오린 것이다 — 그림의 테두리가 창 경계와 맞는지 눈으로 보라." >&2
