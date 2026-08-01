<#
.SYNOPSIS
  콘솔 명령을 **새 콘솔 창**에 띄우고, 그 창의 핸들(HWND)과 pid 를 돌려준다.
  파이썬 정본 클라(Textual)를 이 상자에서 실물로 찍기 위한 입구다.

.DESCRIPTION
  # 왜 pid 로 안 되나

  이 상자의 기본 터미널은 **Windows Terminal** 이다. 콘솔 앱을 띄우면 창은 그 앱의
  프로세스가 아니라 **WindowsTerminal.exe 가 소유**한다(실측 2026-07-30:
  `cmd /k title …` 로 띄운 창의 소유 pid 가 이미 돌고 있던 WindowsTerminal 이었다).
  그래서 `capture_window.ps1 -ProcessId <python pid>` 는 "최상위 창이 없다"로 실패한다.

  # 왜 창 제목으로도 안 되나

  제목으로 찾으면 **사용자가 그 순간 쓰고 있는 터미널 창**을 집을 수 있다. 게다가
  Textual 은 뜨면서 콘솔 제목을 자기 앱 이름으로 바꾼다 — 내가 붙인 표식이 사라진다.
  남의 창을 찍어 리포트에 싣는 것은 조용한 사고다(캡처 스크립트가 전경 가드를 두는 것과
  같은 이유).

  # 그래서: 띄우기 **전후의 창 목록을 비교**한다

  전에 없던 최상위 창이 새로 생기면 그게 내 창이다. 여러 개가 새로 생기면(드물다)
  실패로 떨어뜨린다 — 아무거나 고르면 그게 곧 남의 창을 찍는 길이다.

.PARAMETER Command
  콘솔에서 돌릴 명령줄(cmd 문법). 예: `python x.py attach`

.PARAMETER WaitSeconds
  새 창이 뜨기를 기다리는 상한(기본 20).

.PARAMETER KeepOpen
  명령이 끝나도 창을 닫지 않는다(`cmd /k`). 기본은 `/c`(끝나면 닫힘).

.OUTPUTS
  `hwnd=<수> pid=<수>` 한 줄. 부르는 쪽이 그 hwnd 를 캡처·키 스크립트에 넘긴다.

.EXAMPLE
  $r = powershell -File scripts/launch_console_window.ps1 -Command "python ..\pytmux\pytmux.py attach"
  # → "hwnd=1247890 pid=1234"
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Command,
  [int]$WaitSeconds = 20,
  [switch]$KeepOpen
)

$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class WinList {
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  public delegate bool EnumProc(IntPtr hwnd, IntPtr p);
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);

  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }

  /// 보이는 최상위 창들(소유자 없음 · 툴팁만 한 것 제외). 창 목록 비교의 단위다.
  public static List<long> Visible() {
    var found = new List<long>();
    EnumWindows((h, p) => {
      if (IsWindowVisible(h) && GetWindow(h, 4 /*GW_OWNER*/) == IntPtr.Zero) {
        RECT r;
        if (GetWindowRect(h, out r) && (r.Right - r.Left) > 200 && (r.Bottom - r.Top) > 120)
          found.Add((long)h);
      }
      return true;
    }, IntPtr.Zero);
    return found;
  }

  public static uint OwnerPid(long h) {
    uint pid; GetWindowThreadProcessId((IntPtr)h, out pid); return pid;
  }
}
'@

$before = @([WinList]::Visible())
$switch = if ($KeepOpen) { "/k" } else { "/c" }
# `Start-Process cmd` 는 새 콘솔을 만든다. 환경(PYTMUX_HOME·LANG·NO_COLOR…)은 이 세션에서
# 상속되므로, 부르는 쪽이 미리 세워 두면 그대로 간다.
$proc = Start-Process cmd.exe -ArgumentList "$switch $Command" -PassThru

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$new = @()
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 250
  $now = @([WinList]::Visible())
  $new = @($now | Where-Object { $before -notcontains $_ })
  if ($new.Count -ge 1) { break }
}

if ($new.Count -eq 0) {
  throw "새 콘솔 창이 $WaitSeconds 초 안에 안 떴다(명령: $Command)."
}
if ($new.Count -gt 1) {
  # 내 것을 못 가리면 **아무것도 찍지 않는다** — 남의 창을 찍는 것보다 실패가 낫다.
  throw "창이 여러 개 새로 떴다($($new.Count)개) — 어느 것이 내 것인지 못 가린다. 다시 시도할 것."
}

$hwnd = $new[0]
"hwnd=$hwnd pid=$($proc.Id) owner_pid=$([WinList]::OwnerPid($hwnd))"
