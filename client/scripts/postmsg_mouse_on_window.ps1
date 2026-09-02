<#
.SYNOPSIS
  창의 메시지 큐에 마우스 메시지를 **직접 넣는다**(이동·클릭·휠). 커서를 안 움직인다.

.DESCRIPTION
  `hover_on_window.ps1`·`drag_mouse_on_window.ps1` 은 **진짜 커서**를 옮긴다(SendInput).
  그것이 옳은 자리도 있지만 두 곳에서 못 쓴다:

  ⑴ **물리 모니터가 없는 상자** — 합성 포인터가 창에 아예 안 닿는다(pytmux-155 실측
     2026-08-26: 전경 승격 성공 · 커서 이동 성공 · 그런데 앱이 마우스를 하나도 못 받았다).
  ⑵ **사람이 그 상자를 쓰고 있을 때** — 전경을 빼앗고 사용자의 커서를 끌고 다닌다.
     실측(2026-09-02): Slack 이 전경을 계속 되찾아 hover 5회 중 **4회가 거절**됐고,
     그동안 사용자의 포인터가 화면을 돌아다녔다. 그 회차의 결과는 재현으로 못 쓴다.

  ⛔ **그래서 포인터가 아니라 메시지를 넣는다.** `PostMessage` 는 창의 메시지 큐로 바로
  가므로 표시 장치도 커서도 전경도 필요 없고, winit 이 그것을 평소의 `MouseMoved`·
  `LeftMouseDown`·`LeftMouseUp` 으로 읽는다. `probe_titlebar_drag.ps1` 이 이미 그 길로
  누름을 넣어 진단을 띄웠다 — 이 자는 그것을 **이동·클릭·휠까지** 넓힌 한 벌이다.

  ⚠ **이것으로 못 재는 것**: OS 가 «진짜로 눌린 버튼»을 요구하는 동작(창 끌기의 이동
  루프 · 네이티브 드래그 리사이즈)은 합성 메시지로 즉시 끝난다. 그 자리는 여전히
  `drag_mouse_on_window.ps1` + 물리 모니터가 필요하다(그 사유는 위 probe 스크립트 머리말).

.PARAMETER ProcessId
  대상 창을 가진 프로세스.

.PARAMETER X / Y
  **클라이언트 영역 픽셀**(물리). 창 기준 비율이 아니다 — 메시지의 lParam 이 그 좌표다.

.PARAMETER Move
  그 자리로 이동 메시지(`WM_MOUSEMOVE`)를 넣는다. `-Steps` 만큼 나눠 넣는다.

.PARAMETER Click
  그 자리에서 왼쪽 누름+뗌(`WM_LBUTTONDOWN`/`UP`)을 넣는다. 이동을 먼저 한 번 넣는다 —
  hover 판정을 지나야 클릭을 받는 원소가 있다(`Hoverable` 은 누름에서 `click_count` 를
  적어 두고 뗌에서 `take()` 한다).

.PARAMETER Wheel
  그 자리에서 휠을 굴린다(양수=위). `WM_MOUSEWHEEL` 은 **화면 좌표**를 쓰므로 이 자가
  변환한다.

.PARAMETER Steps
  이동을 몇 번에 나눠 넣나(기본 5). 중첩 원소의 진입 판정을 확실히 밟게 한다.

.EXAMPLE
  # 상태줄 오른쪽 끝의 시각을 클릭한다(pytmux-366)
  .\scripts\postmsg_mouse_on_window.ps1 -ProcessId 123 -X 1200 -Y 781 -Click

.EXAMPLE
  # 패널 위를 버튼 없이 훑는다(any-motion 패스스루 확인 · pytmux-423)
  .\scripts\postmsg_mouse_on_window.ps1 -ProcessId 123 -X 400 -Y 300 -Move
#>
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][int]$X,
  [Parameter(Mandatory = $true)][int]$Y,
  [switch]$Move,
  [switch]$Click,
  [int]$Wheel = 0,
  [int]$Steps = 5,
  [int]$SettleMs = 120
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\winlib.ps1"   # 창 찾기 한 벌(Get-AppWindow)

Add-Type -Namespace PtMsg -Name Mouse -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
[DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
public struct POINT { public int X; public int Y; }
public static IntPtr Lp(int x, int y) { return (IntPtr)((y << 16) | (x & 0xFFFF)); }
public static POINT ToScreen(IntPtr h, int x, int y) {
  POINT p = new POINT(); p.X = x; p.Y = y; ClientToScreen(h, ref p); return p;
}
'@

$WM_MOUSEMOVE = 0x0200
$WM_LBUTTONDOWN = 0x0201
$WM_LBUTTONUP = 0x0202
$WM_MOUSEWHEEL = 0x020A
$MK_LBUTTON = 0x0001

$hwnd = Get-AppWindow -ProcessId $ProcessId
if (-not $Move -and -not $Click -and $Wheel -eq 0) {
  throw "-Move · -Click · -Wheel 중 하나는 줘야 한다(무엇을 넣을지 모른다)."
}

# 이동은 늘 먼저 넣는다 — 클릭·휠도 그 자리에 «있다가» 일어나는 것이라, 진입 판정을
# 안 밟으면 hover 상태가 없는 원소가 클릭을 안 받는다.
$path = @()
for ($i = 1; $i -le [Math]::Max(1, $Steps); $i++) {
  $t = $i / [double]([Math]::Max(1, $Steps))
  $path += , @([int]($X * $t), [int]($Y * $t))
}
foreach ($p in $path) {
  [PtMsg.Mouse]::PostMessage($hwnd, $WM_MOUSEMOVE, [IntPtr]::Zero,
    [PtMsg.Mouse]::Lp($p[0], $p[1])) | Out-Null
  Start-Sleep -Milliseconds 15
}
[PtMsg.Mouse]::PostMessage($hwnd, $WM_MOUSEMOVE, [IntPtr]::Zero,
  [PtMsg.Mouse]::Lp($X, $Y)) | Out-Null
Start-Sleep -Milliseconds $SettleMs

if ($Click) {
  [PtMsg.Mouse]::PostMessage($hwnd, $WM_LBUTTONDOWN, [IntPtr]$MK_LBUTTON,
    [PtMsg.Mouse]::Lp($X, $Y)) | Out-Null
  Start-Sleep -Milliseconds 60
  [PtMsg.Mouse]::PostMessage($hwnd, $WM_LBUTTONUP, [IntPtr]::Zero,
    [PtMsg.Mouse]::Lp($X, $Y)) | Out-Null
  Start-Sleep -Milliseconds $SettleMs
}

if ($Wheel -ne 0) {
  # ⚠ 휠만 **화면 좌표**다(다른 마우스 메시지는 클라이언트 좌표) — 안 바꾸면 창 밖을
  #   가리켜 대상이 그 이벤트를 버린다.
  $s = [PtMsg.Mouse]::ToScreen($hwnd, $X, $Y)
  $delta = 120 * $Wheel
  $wp = [IntPtr](([int64]$delta) -shl 16)
  [PtMsg.Mouse]::PostMessage($hwnd, $WM_MOUSEWHEEL, $wp,
    [PtMsg.Mouse]::Lp($s.X, $s.Y)) | Out-Null
  Start-Sleep -Milliseconds $SettleMs
}

$what = @()
if ($Move) { $what += "move" }
if ($Click) { $what += "click" }
if ($Wheel -ne 0) { $what += "wheel($Wheel)" }
Write-Output ("넣었다(메시지): hwnd={0} client=({1},{2}) {3}" -f $hwnd, $X, $Y, ($what -join "+"))
