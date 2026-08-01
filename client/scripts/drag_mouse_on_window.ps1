<#
.SYNOPSIS
  떠 있는 창 위에서 실제 마우스를 눌러 끌고 놓는다. GUI 마우스 경로의 라이브 확인용.

.DESCRIPTION
  키(`send_keys_to_window.ps1`)와 같은 결이다 — 우리 코드에 좌표를 주입하는 것이 아니라
  **OS 가 만든 마우스 이벤트**가 winit → warpui → 이 클라의 좌표 보정을 타고 서버까지
  가는지 본다. 그래서 `SendInput` 으로 진짜 커서를 움직인다.

  좌표는 **창 기준 비율**(0~1)로 준다. 창 크기가 상자마다 달라 절대 픽셀로 적으면 다른
  상자에서 엉뚱한 곳을 누른다.

  ⚠️ 진짜 커서를 움직인다 — 도는 동안 사람이 마우스를 만지면 결과가 섞인다.

.PARAMETER ProcessId
  대상 창을 가진 프로세스.

.PARAMETER FromX / FromY / ToX / ToY
  창 안에서의 비율 좌표(0~1). From 에서 누르고 To 에서 놓는다.

.PARAMETER Steps
  끄는 동안 나눠 보낼 이동 횟수(기본 12). 한 번에 점프하면 드래그가 아니라 순간이동이라
  드래그 이벤트가 한 번밖에 안 간다.

.EXAMPLE
  powershell -File scripts/drag_mouse_on_window.ps1 -ProcessId 123 -FromX 0.1 -FromY 0.3 -ToX 0.4 -ToY 0.3
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][double]$FromX,
  [Parameter(Mandatory = $true)][double]$FromY,
  [Parameter(Mandatory = $true)][double]$ToX,
  [Parameter(Mandatory = $true)][double]$ToY,
  # 켜면 From/To 를 비율이 아니라 **클라이언트 영역 픽셀**로 읽는다.
  #
  # 왜 필요한가(2026-07-28 실측): 스크린샷은 DWM 의 확장 프레임 기준인데 winit 이 앱에
  # 주는 마우스 좌표는 **클라이언트 영역** 기준이고, 이 상자에서 둘의 원점이 152px
  # 어긋나 있었다. 그림을 보고 고른 비율이 실제로는 다른 칸을 눌렀고, 증상은 "경계선이
  # 안 끌린다"였다. 클라이언트 기준으로 주면 앱이 보는 좌표와 같아진다.
  [switch]$ClientPixels,
  # 끄는 동안 Shift 를 누르고 있는다. 이 클라에서 Shift+드래그는 **패널 안 앱에게 넘김**
  # 이라(평드래그는 복사다), 패스스루를 확인하려면 이 스위치가 필요하다.
  [switch]$Shift,
  [int]$Steps = 12,
  [int]$SettleMs = 700,
  # 목적지에서 **버튼을 누른 채** 이만큼 머문다. 끄는 도중의 그림(드롭 대상 강조·
  # hover 표시)을 찍으려면 이 틈이 필요하다 — 안 그러면 누름·이동·뗌이 한 호흡에
  # 끝나 캡처가 늘 뗀 뒤의 화면을 잡는다(2026-07-31 탭 드래그 hover 실측에서 필요했다).
  [int]$HoldMs = 0
)

$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class WinDrag {
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
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);

  public const byte SHIFT = 0x10;
  public const uint KEYUP = 0x0002;

  /// 수정키 누름/뗌. 스캔코드를 함께 싣지 않으면 winit 이 못 알아보는 경우가 있다.
  public static void Modifier(byte vk, bool down) {
    keybd_event(vk, (byte)MapVirtualKey(vk, 0), down ? 0u : KEYUP, UIntPtr.Zero);
  }
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT val, int size);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);

  [StructLayout(LayoutKind.Sequential)]
  public struct POINT { public int X, Y; }

  /// 클라이언트 영역의 화면상 원점 — **winit 이 앱에 주는 좌표의 기준점**이다.
  public static POINT ClientOrigin(IntPtr h) {
    POINT p; p.X = 0; p.Y = 0;
    ClientToScreen(h, ref p);
    return p;
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }

  public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004;

  public static IntPtr TopLevel(uint want) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == want && IsWindowVisible(h) && GetWindow(h, 4) == IntPtr.Zero) { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }

  public static RECT Bounds(IntPtr h) {
    RECT r;
    if (DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT))) == 0) return r;
    GetWindowRect(h, out r);
    return r;
  }
}
'@

$hwnd = [WinDrag]::TopLevel([uint32]$ProcessId)
if ($hwnd -eq [IntPtr]::Zero) { throw "pid $ProcessId 에 최상위 창이 없다." }

[void][WinDrag]::ShowWindow($hwnd, 9)
[void][WinDrag]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs
if ([WinDrag]::GetForegroundWindow() -ne $hwnd) {
  throw "대상 창이 앞에 없다 — 남의 창을 클릭하지 않는다."
}

if ($ClientPixels) {
  $o = [WinDrag]::ClientOrigin($hwnd)
  $x0 = [int]($o.X + $FromX); $y0 = [int]($o.Y + $FromY)
  $x1 = [int]($o.X + $ToX);   $y1 = [int]($o.Y + $ToY)
  $w = 0; $h = 0
} else {
  $r = [WinDrag]::Bounds($hwnd)
  $w = $r.Right - $r.Left
  $h = $r.Bottom - $r.Top
  $x0 = [int]($r.Left + $w * $FromX)
  $y0 = [int]($r.Top + $h * $FromY)
  $x1 = [int]($r.Left + $w * $ToX)
  $y1 = [int]($r.Top + $h * $ToY)
}

[void][WinDrag]::SetCursorPos($x0, $y0)
Start-Sleep -Milliseconds 120
# Shift 는 **누르기 전에** 눌러 둔다 — 넘김 판정은 누름 시점의 수정키로 정해진다.
if ($Shift) { [WinDrag]::Modifier([WinDrag]::SHIFT, $true); Start-Sleep -Milliseconds 60 }
[WinDrag]::mouse_event([WinDrag]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 120
for ($i = 1; $i -le $Steps; $i++) {
  $x = [int]($x0 + ($x1 - $x0) * $i / $Steps)
  $y = [int]($y0 + ($y1 - $y0) * $i / $Steps)
  [void][WinDrag]::SetCursorPos($x, $y)
  Start-Sleep -Milliseconds 40
}
if ($HoldMs -gt 0) { Start-Sleep -Milliseconds $HoldMs }
[WinDrag]::mouse_event([WinDrag]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
# 뗌까지 보낸 뒤에 놓는다 — 먼저 놓으면 앱이 받는 뗌 리포트의 수정키가 달라진다.
if ($Shift) { Start-Sleep -Milliseconds 60; [WinDrag]::Modifier([WinDrag]::SHIFT, $false) }
Start-Sleep -Milliseconds 250

Write-Output "끌었다: ($x0,$y0) → ($x1,$y1) (창 ${w}x${h})"
