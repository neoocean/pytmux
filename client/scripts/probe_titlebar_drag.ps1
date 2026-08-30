<#
.SYNOPSIS
  머리줄 끌기가 **어디서 멎는지**를 화면 없이 잰다(pytmux/pytmux-155 · 365).

.DESCRIPTION
  그 이슈들은 반년 가까이 「안 끌린다」 한 줄이었고, 갈래가 여섯인데 밖에서 볼 길이
  없었다. 진단 로그(`drag_diag`)가 생긴 뒤에도 **그 로그를 띄울 방법**이 문제였다:
  합성 포인터(SendInput)는 **물리 모니터가 없는 상자에서 창에 안 닿는다**(실측
  2026-08-26 — 전경 승격은 성공하고 커서도 움직이는데 앱은 마우스를 하나도 못 받았다).

  ⛔ **그래서 포인터가 아니라 «메시지»를 넣는다.** `PostMessage(WM_LBUTTONDOWN)` 은
  창의 메시지 큐로 바로 가므로 표시 장치도 커서도 필요 없다 — winit 이 그것을 평소의
  `LeftMouseDown` 으로 읽고, 이동 갈래가 보는 조건 셋이 그대로 로그에 찍힌다.

  ⚠ **이 자는 「창이 실제로 움직였나」를 재지 않는다.** `drag_window()` 는 OS 에게
  끌기를 넘기고, OS 의 이동 루프는 **진짜로 눌려 있는 마우스 버튼**을 요구한다 —
  합성 메시지로는 그 루프가 즉시 끝난다. 여기서 재는 것은 **우리 쪽 세 조건**이다:

    (1) handled=false   — 뷰가 그 누름을 안 먹었나
    (2) y < titlebar_h  — 머리줄 띠 안인가
    (3) drag_window()   — Ok 를 냈나

  셋이 다 참인데 사용자가 못 끌면, 원인은 **우리 코드 밖**이다. 그 판정이 이 자의 값이다.

.PARAMETER ProcessId
  `pytmux-gui` 프로세스. ⚠ 그 프로세스는 `RUST_LOG=debug` 로 떠 있어야 한다 —
  진단은 `log::debug!` 라서 기본은 조용하다.

.PARAMETER LogPath
  그 프로세스의 stderr 를 받아 둔 파일. 여기서 진단 줄을 읽는다.

.PARAMETER ClientY
  누를 클라이언트 y(기본 13 = 배율 1 의 머리줄 띠 안).

.EXAMPLE
  $p = Start-Process .	arget
elease\pytmux-gui.exe -PassThru -RedirectStandardError err.txt
  .\scripts\probe_titlebar_drag.ps1 -ProcessId $p.Id -LogPath err.txt
#>
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][string]$LogPath,
  [int]$ClientX = 400,
  [int]$ClientY = 13,
  [int]$SettleMs = 900
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\winlib.ps1"   # 창 찾기 한 벌(Get-AppWindow)

Add-Type -Namespace PtDrag -Name Msg -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
public static IntPtr Lp(int x, int y) { return (IntPtr)((y << 16) | (x & 0xFFFF)); }
'@

$hwnd = Get-AppWindow -ProcessId $ProcessId
if (-not (Test-Path $LogPath)) { throw "로그 파일이 없다: $LogPath (RUST_LOG=debug 로 띄웠나?)" }
# ⛔ **UTF-8 로 읽는다.** 로그는 UTF-8 인데 Windows PowerShell 의 기본은 ANSI 라, 그냥
#    읽으면 한글이 깨지고 **한글로 판정하던 정규식이 조용히 빗나간다**(실측 2026-08-26 —
#    줄은 잡혔는데 「drag_window 가 Ok 를 안 냈다」로 오판했다). 아래 판정도 그래서
#    **ASCII 조각으로만** 한다.
$before = (Get-Content $LogPath -Encoding UTF8 -ErrorAction SilentlyContinue | Measure-Object -Line).Lines

# 움직임 → 누름. 뗌은 안 보낸다 — `drag_window()` 가 Ok 를 내면 그 뒤 뗌은 OS 몫이다.
[void][PtDrag.Msg]::PostMessage($hwnd, 0x200, [IntPtr]0, [PtDrag.Msg]::Lp($ClientX, $ClientY))
Start-Sleep -Milliseconds 150
[void][PtDrag.Msg]::PostMessage($hwnd, 0x201, [IntPtr]1, [PtDrag.Msg]::Lp($ClientX, $ClientY))
Start-Sleep -Milliseconds $SettleMs

$new = @(Get-Content $LogPath -Encoding UTF8 | Select-Object -Skip $before |
         Select-String -Pattern "drag-155")
if ($new.Count -eq 0) {
  throw ("진단이 한 줄도 안 나왔다 — 앱이 그 누름을 **아예 못 받았다**. " +
         "RUST_LOG=debug 인지, 그리고 이 창이 그 pid 의 것인지 볼 것.")
}
$new | ForEach-Object { "  " + $_.Line.Substring($_.Line.IndexOf("drag-155")) }

$handled = ($new | Where-Object { $_.Line -match "handled=false" }).Count -gt 0
$dragged = ($new | Where-Object { $_.Line -match "drag_window\(\).*Ok" }).Count -gt 0
if ($handled -and $dragged) {
  "판정: 우리 쪽 세 조건이 **전부 통과**했다 — 못 끌린다면 원인은 이 코드 밖이다."
  exit 0
}
if (-not $handled) { "판정: **뷰가 그 누름을 먹었다**(handled=true) — 이동 갈래에 안 닿는다." }
if (-not $dragged) { "판정: **drag_window() 가 Ok 를 안 냈다** — 위 줄의 조건들을 볼 것." }
exit 1
