<#
.SYNOPSIS
  떠 있는 창의 크기를 바꾼다. 리사이즈 경로·좌표 보정 확인용.

.DESCRIPTION
  창 크기가 바뀌면 이 클라는 서버에 새 격자 크기를 알리고, 서버가 새 배치를 보내고,
  렌더가 **자리표를 다시 남긴다**. 즉 크기 변경은 좌표 보정이 계산이 아니라 실측임을
  확인하는 가장 싼 축이다 — 클릭이 여전히 겨눈 칸에 떨어지면 보정이 따라온 것이다.

  ⚠️ 진짜 창을 움직인다. 사용자가 그 창을 보고 있으면 눈에 띈다.

.PARAMETER ProcessId
  대상 창을 가진 프로세스.

.PARAMETER Width / Height
  새 크기(픽셀, 창 테두리 포함).

.EXAMPLE
  powershell -File scripts/resize_window.ps1 -ProcessId 123 -Width 1000 -Height 700
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][int]$Width,
  [Parameter(Mandatory = $true)][int]$Height,
  [int]$SettleMs = 1200
)

$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class WinSize {
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  public delegate bool EnumProc(IntPtr hwnd, IntPtr p);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);

  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }

  public const uint SWP_NOMOVE = 0x0002, SWP_NOZORDER = 0x0004, SWP_NOACTIVATE = 0x0010;

  public static IntPtr TopLevel(uint want) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == want && IsWindowVisible(h) && GetWindow(h, 4) == IntPtr.Zero) { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
'@

$hwnd = [WinSize]::TopLevel([uint32]$ProcessId)
if ($hwnd -eq [IntPtr]::Zero) { throw "pid $ProcessId 에 최상위 창이 없다." }

# 최대화 상태면 크기 지정이 안 먹는다 — 먼저 보통 창으로 되돌린다(SW_RESTORE).
[void][WinSize]::ShowWindow($hwnd, 9)
Start-Sleep -Milliseconds 200

$ok = [WinSize]::SetWindowPos($hwnd, [IntPtr]::Zero, 0, 0, $Width, $Height,
  [WinSize]::SWP_NOMOVE -bor [WinSize]::SWP_NOZORDER -bor [WinSize]::SWP_NOACTIVATE)
if (-not $ok) { throw "크기를 못 바꿨다." }
Start-Sleep -Milliseconds $SettleMs

$r = New-Object WinSize+RECT
[void][WinSize]::GetClientRect($hwnd, [ref]$r)
Write-Output ("크기: 클라이언트 {0}x{1}" -f ($r.Right - $r.Left), ($r.Bottom - $r.Top))
