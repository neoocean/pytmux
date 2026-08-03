<#
.SYNOPSIS
  떠 있는 창 위의 한 지점에서 실제 휠을 굴린다. GUI 휠 경로의 라이브 확인용.

.DESCRIPTION
  `drag_mouse_on_window.ps1`·`send_keys_to_window.ps1` 과 같은 결이다 — 우리 코드에 값을
  주입하는 것이 아니라 **OS 가 만든 휠 이벤트**가 winit → warpui → 좌표 보정 → 서버까지
  가는지 본다.

  휠은 "무엇 위에서 굴렸나"가 뜻의 일부인 제스처다(이 클라는 커서 아래 패널을 굴린다).
  그래서 이 스크립트의 요점은 델타가 아니라 **커서를 어디에 두느냐**이고, 좌표 규칙은
  드래그 스크립트와 같다: 기본은 창 기준 비율, `-ClientPixels` 면 클라이언트 영역 픽셀
  (winit 이 앱에 주는 좌표와 같은 기준 — 스크린샷의 DWM 확장 프레임 기준과 이 상자에서
  152px 어긋난다).

  ⚠️ 진짜 커서를 움직인다 — 도는 동안 사람이 마우스를 만지면 결과가 섞인다.

.PARAMETER ProcessId
  대상 창을 가진 프로세스.

.PARAMETER X / Y
  창 안에서의 비율 좌표(0~1), 또는 `-ClientPixels` 면 클라이언트 영역 픽셀.

.PARAMETER Notches
  휠 눈금 수. 양수면 위(과거 방향), 음수면 아래. 한 눈금 = WHEEL_DELTA(120).

.EXAMPLE
  powershell -File scripts/wheel_on_window.ps1 -ProcessId 123 -X 600 -Y 300 -ClientPixels -Notches -3
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][double]$X,
  [Parameter(Mandatory = $true)][double]$Y,
  [switch]$ClientPixels,
  [int]$Notches = 1,
  [int]$SettleMs = 700
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\winlib.ps1"   # 창 찾기 한 벌(Get-AppWindow)

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class WinWheel {
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  public delegate bool EnumProc(IntPtr hwnd, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT val, int size);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);

  [StructLayout(LayoutKind.Sequential)]
  public struct POINT { public int X, Y; }

  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }

  /// 클라이언트 영역의 화면상 원점 — **winit 이 앱에 주는 좌표의 기준점**이다.
  public static POINT ClientOrigin(IntPtr h) {
    POINT p; p.X = 0; p.Y = 0;
    ClientToScreen(h, ref p);
    return p;
  }

  public const uint WHEEL = 0x0800;

  // ⛔ 창 찾기는 여기 두지 않는다 — `scripts/winlib.ps1` 의 `Get-AppWindow` 한 벌이다.
  //    종전에는 이 클래스마다 "그 pid 의 첫 보이는 최상위 창"을 복붙해 뒀는데, 그 술어는
  //    winit 의 숨은 15×15 이벤트 창(보이고 소유자도 없다)을 앱 창으로 집는다(pytmux-32).

  public static RECT Bounds(IntPtr h) {
    RECT r;
    if (DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT))) == 0) return r;
    GetWindowRect(h, out r);
    return r;
  }
}
'@

$hwnd = Get-AppWindow -ProcessId $ProcessId

[void][WinWheel]::ShowWindow($hwnd, 9)
[void][WinWheel]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs
# 남의 창 위에서 휠을 굴리지 않는다 — 사용자가 보던 문서가 흘러간다.
if ([WinWheel]::GetForegroundWindow() -ne $hwnd) {
  throw "대상 창이 앞에 없다 — 남의 창에서 휠을 굴리지 않는다."
}

if ($ClientPixels) {
  $o = [WinWheel]::ClientOrigin($hwnd)
  $px = [int]($o.X + $X); $py = [int]($o.Y + $Y)
} else {
  $r = [WinWheel]::Bounds($hwnd)
  $px = [int]($r.Left + ($r.Right - $r.Left) * $X)
  $py = [int]($r.Top + ($r.Bottom - $r.Top) * $Y)
}

[void][WinWheel]::SetCursorPos($px, $py)
Start-Sleep -Milliseconds 150
# 눈금을 한 번에 몰아 보내지 않는다 — 앱이 델타를 합쳐 한 번으로 읽으면 "여러 번 굴린
# 것"을 확인할 수 없다.
$step = if ($Notches -ge 0) { 1 } else { -1 }
# ⚠ `mouseData` 는 DWORD 지만 담는 값은 **부호 있는** 휠 델타다. `[uint32](-120)` 은
#   변환이 아니라 **오류**다("Value was either too large or too small for a UInt32") —
#   그래서 종전에는 **아래로 굴리는 것이 아예 불가능**했다(이 파일의 .EXAMPLE 이 바로
#   `-Notches -3` 인데도 그 예제가 죽었다). 값을 바꾸는 게 아니라 **비트를 그대로**
#   읽어야 하므로 2의 보수 바이트를 재해석한다.
$delta = [BitConverter]::ToUInt32([BitConverter]::GetBytes([int]($step * 120)), 0)
for ($i = 0; $i -lt [Math]::Abs($Notches); $i++) {
  [WinWheel]::mouse_event([WinWheel]::WHEEL, 0, 0, $delta, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 120
}
Start-Sleep -Milliseconds 250

Write-Output "휠: ($px,$py) 에서 $Notches 눈금"
