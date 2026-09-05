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

  ★ 그 «밖» 을 두 칸으로 좁힌다(2026-09-04 · pytmux-155 회계가 남긴 두 걸음):

    (4) 커서 클립 — 다른 앱이 `ClipCursor` 로 커서를 자기 창에 가둬 두면 **어떤 앱도**
        못 끌린다. 2026-08-25 에 실제로 `Switch.exe`(Electron)가 그렇게 해 둔 것이
        잡혔고, 그때 이 이슈는 그것을 우리 결함으로 한 번 오독했다. 그래서 이제
        **코드를 읽기 전에 이 한 줄부터** 찍는다.
    (5) OS 이동 루프 — `WM_SYSCOMMAND(SC_MOVE)` + **게시한** 방향키로 창을 실제로
        옮겨 본다. 포인터를 안 건드리므로 사용자가 상자를 쓰는 중에도 돌 수 있다
        (⛔ `SendInput` 하네스는 사용자의 커서를 빼앗아 그때는 금지다).
        여기서 창이 움직이면 「이 창은 OS 가 못 옮긴다」 부류가 통째로 죽는다 —
        남는 것은 **마우스 갈래**(진짜로 눌린 버튼을 요구하는 `HTCAPTION` 경로)뿐이다.

  ⚠ (5)는 `drag_window()` 가 쓰는 **바로 그 갈래**는 아니다. `WM_NCLBUTTONDOWN`
  (HTCAPTION)은 DefWindowProc 이 `SC_MOVE|0x0002`(=마우스 변종)로 바꾸는데, 그 루프는
  **실제로 눌려 있는 버튼**을 요구해 합성 메시지로는 즉시 끝난다(실측: 그 길로는
  dx=0). 키보드 변종은 그 요구가 없다 — 그래서 «옮길 수 있는 창인가»만 가른다.

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
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
[DllImport("user32.dll")] public static extern bool GetClipCursor(out RECT r);
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern int GetSystemMetrics(int i);
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

# ── (4) 커서 클립 — 우리 코드를 읽기 전에 볼 한 줄 ──────────────────────────
$clip = New-Object PtDrag.Msg+RECT
[void][PtDrag.Msg]::GetClipCursor([ref]$clip)
$vx = [PtDrag.Msg]::GetSystemMetrics(76); $vy = [PtDrag.Msg]::GetSystemMetrics(77)
$vw = [PtDrag.Msg]::GetSystemMetrics(78); $vh = [PtDrag.Msg]::GetSystemMetrics(79)
$clipFull = ($clip.L -le $vx -and $clip.T -le $vy -and
             $clip.R -ge ($vx + $vw) -and $clip.B -ge ($vy + $vh))
"  cursor-clip: {0},{1},{2},{3}  virtual: {4},{5} {6}x{7}  full={8}" -f `
  $clip.L, $clip.T, $clip.R, $clip.B, $vx, $vy, $vw, $vh, $clipFull
if (-not $clipFull) {
  "  !! 다른 앱이 커서를 가둬 뒀다 — 이 상태면 **어떤 앱도** 못 끌린다(우리 결함이 아니다)."
}

# ── (5) OS 이동 루프 — 포인터를 안 건드리고 «옮길 수 있는 창인가» 를 잰다 ────
$r0 = New-Object PtDrag.Msg+RECT
[void][PtDrag.Msg]::GetWindowRect($hwnd, [ref]$r0)
[void][PtDrag.Msg]::PostMessage($hwnd, 0x0112, [IntPtr]0xF010, [IntPtr]0)   # WM_SYSCOMMAND SC_MOVE
Start-Sleep -Milliseconds 300
for ($i = 0; $i -lt 20; $i++) {
  [void][PtDrag.Msg]::PostMessage($hwnd, 0x0100, [IntPtr]0x27, [IntPtr]0)   # VK_RIGHT down
  [void][PtDrag.Msg]::PostMessage($hwnd, 0x0101, [IntPtr]0x27, [IntPtr]0)   # VK_RIGHT up
  Start-Sleep -Milliseconds 12
}
[void][PtDrag.Msg]::PostMessage($hwnd, 0x0100, [IntPtr]0x0D, [IntPtr]0)     # Enter = 확정
[void][PtDrag.Msg]::PostMessage($hwnd, 0x0101, [IntPtr]0x0D, [IntPtr]0)
Start-Sleep -Milliseconds 500
$r1 = New-Object PtDrag.Msg+RECT
[void][PtDrag.Msg]::GetWindowRect($hwnd, [ref]$r1)
$osdx = $r1.L - $r0.L
# ⛔ 되돌려 놓는다 — 진단이 창을 옮겨 두고 가면 다음 사람이 그것을 증상으로 읽는다.
[void][PtDrag.Msg]::PostMessage($hwnd, 0x0112, [IntPtr]0xF010, [IntPtr]0)
Start-Sleep -Milliseconds 300
for ($i = 0; $i -lt 20; $i++) {
  [void][PtDrag.Msg]::PostMessage($hwnd, 0x0100, [IntPtr]0x25, [IntPtr]0)   # VK_LEFT
  [void][PtDrag.Msg]::PostMessage($hwnd, 0x0101, [IntPtr]0x25, [IntPtr]0)
  Start-Sleep -Milliseconds 12
}
[void][PtDrag.Msg]::PostMessage($hwnd, 0x0100, [IntPtr]0x0D, [IntPtr]0)
[void][PtDrag.Msg]::PostMessage($hwnd, 0x0101, [IntPtr]0x0D, [IntPtr]0)
Start-Sleep -Milliseconds 400
"  os-move-loop: dx={0} (SC_MOVE + 게시한 방향키 · 포인터 미사용)" -f $osdx
if ($osdx -eq 0) {
  "  !! OS 가 이 창을 키보드로도 못 옮겼다 — 그러면 머리줄 배선보다 **창 자체**를 먼저 본다."
}

$handled = ($new | Where-Object { $_.Line -match "handled=false" }).Count -gt 0
$dragged = ($new | Where-Object { $_.Line -match "drag_window\(\).*Ok" }).Count -gt 0
if ($handled -and $dragged) {
  "판정: 우리 쪽 세 조건이 **전부 통과**했다 — 못 끌린다면 원인은 이 코드 밖이다."
  exit 0
}
if (-not $handled) { "판정: **뷰가 그 누름을 먹었다**(handled=true) — 이동 갈래에 안 닿는다." }
if (-not $dragged) { "판정: **drag_window() 가 Ok 를 안 냈다** — 위 줄의 조건들을 볼 것." }
exit 1
