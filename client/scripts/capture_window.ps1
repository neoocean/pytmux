<#
.SYNOPSIS
  떠 있는 창을 pid 로 찾아 PNG 로 찍는다. GUI 슬라이스의 테스트 리포트에 싣는 증거물.

.DESCRIPTION
  이 저장소는 지금까지 "창 안의 그림은 사람이 본다"로 미뤄 왔다(설계문서 §7). 그런데
  P6 에서 배운 것이 있다 — **"헤드리스 불가"의 절반은 사람이 아니라 그 OS 의 장치가
  필요한 것**이었고, 장치는 프로그램이 만들 수 있다. 창도 같다: 사람의 눈이 필요한 것은
  "이 배색이 예쁜가"이지 "탭바가 그려졌는가"가 아니다. 후자는 픽셀을 세면 된다.

  캡처는 **화면 DC 에서 BitBlt** 로 한다. `PrintWindow` 를 쓰지 않는 이유는 이 창이
  wgpu(dx12) 스왑체인이라서다 — GPU 가 직접 표시하는 표면은 창 DC 에 없고, PrintWindow
  는 **까만 사각형을 성공으로** 돌려준다. 그러면 이 스크립트는 "찍었다"고 말하고 리포트에
  는 검은 그림이 실린다. 그래서 두 가지를 한다: 화면에서 뜨고, 아래 -MinColors 가드로
  **단색 그림을 실패로 떨어뜨린다**(check_licenses.sh 가 빈 결과를 실패로 떨어뜨리는 것과
  같은 이유 — 안 우는 게이트는 없는 것과 같다).

.PARAMETER ProcessId
  창을 가진 프로세스. 자식이 아니라 **그 프로세스가 직접 소유한** 최상위 창을 찾는다.

.PARAMETER Out
  PNG 경로.

.PARAMETER WaitSeconds
  창이 뜰 때까지 기다리는 상한(기본 20). 첫 프레임은 글꼴 로드 뒤에 온다.

.PARAMETER SettleMs
  창을 앞으로 올린 뒤 찍기 전까지의 여유(기본 700). 컴포지터가 그리는 시간이다.

.PARAMETER MinColors
  이 수보다 색 종류가 적으면 **실패**. 기본 8. 단색·2색 그림은 "캡처 실패"이지 결과가
  아니다.

.EXAMPLE
  powershell -File scripts/capture_window.ps1 -ProcessId 1234 -Out shot.png
#>
[CmdletBinding()]
param(
  # 창 소유 프로세스. **콘솔 앱에는 못 쓴다** — 아래 `-Hwnd` 참조.
  [int]$ProcessId = 0,
  # 창 핸들을 직접 준다(`launch_console_window.ps1` 이 돌려주는 값).
  #
  # 이 상자의 콘솔 창은 **Windows Terminal 프로세스 소유**라, 파이썬 정본 클라처럼
  # 콘솔에서 도는 것은 pid 로 창을 못 찾는다. 이름으로 찾는 것도 안 된다 — 사용자가
  # 쓰고 있는 터미널 창을 집어 **남의 화면이 리포트에 실린다**. 띄운 쪽이 새로 생긴
  # 창을 확정해 핸들을 넘기는 것만이 안전하다.
  [long]$Hwnd = 0,
  [Parameter(Mandatory = $true)][string]$Out,
  [int]$WaitSeconds = 20,
  [int]$SettleMs = 700,
  [int]$MinColors = 8
)

if (($ProcessId -eq 0) -eq ($Hwnd -eq 0)) {
  throw "-ProcessId 나 -Hwnd 중 **하나만** 준다(둘 다이거나 둘 다 아니면 무엇을 찍을지 모른다)."
}

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class WinCap {
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool EnumWindows(EnumProc cb, IntPtr p);
  public delegate bool EnumProc(IntPtr hwnd, IntPtr p);

  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)]
  public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
  [DllImport("dwmapi.dll")]
  public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT val, int size);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);

  [StructLayout(LayoutKind.Sequential)]
  public struct POINT { public int X, Y; }

  /// 그 점을 실제로 **덮고 있는** 최상위 창. 전경 핸들만 보는 것으로는 부족하다 —
  /// 다른 앱 창(실측: Windows 설정)이 위에 떠 있어도 전경 검사는 통과하고, 캡처에는
  /// 그 창이 찍힌다(흰 화면). 화면 DC 에서 뜨는 이상 **픽셀을 가린 창**을 봐야 한다.
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
  static readonly IntPtr HWND_TOPMOST = new IntPtr(-1), HWND_NOTOPMOST = new IntPtr(-2);
  const uint SWP_NOMOVE = 0x0002, SWP_NOSIZE = 0x0001, SWP_NOACTIVATE = 0x0010;

  /// **내 창만** 잠깐 최상위로 올린다(남의 창은 안 건드린다).
  ///
  /// 왜 필요한가(실측 2026-07-31): 사용자가 쓰는 창이 위에 있으면 `SetForegroundWindow`
  /// 가 거절될 수 있고(포커스 도둑질 방지), 그러면 찍을 방법이 없다. 남의 창을 내리는
  /// 것은 사고이므로, 내 창을 올렸다가 **반드시 되돌린다**.
  public static void Topmost(IntPtr h, bool on) {
    SetWindowPos(h, on ? HWND_TOPMOST : HWND_NOTOPMOST, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
  }

  public static IntPtr TopAt(int x, int y) {
    POINT p; p.X = x; p.Y = y;
    IntPtr h = WindowFromPoint(p);
    return h == IntPtr.Zero ? IntPtr.Zero : GetAncestor(h, 2 /*GA_ROOT*/);
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }

  // 그 pid 가 소유한, 보이고, 소유자 창이 없는(= 툴팁·팝업이 아닌) 최상위 창.
  public static List<IntPtr> TopLevel(uint want) {
    var found = new List<IntPtr>();
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == want && IsWindowVisible(h) && GetWindow(h, 4 /*GW_OWNER*/) == IntPtr.Zero) {
        RECT r;
        if (GetWindowRect(h, out r) && (r.Right - r.Left) > 64 && (r.Bottom - r.Top) > 64)
          found.Add(h);
      }
      return true;
    }, IntPtr.Zero);
    return found;
  }

  public static string Title(IntPtr h) {
    int n = GetWindowTextLength(h);
    var sb = new System.Text.StringBuilder(n + 1);
    GetWindowText(h, sb, sb.Capacity);
    return sb.ToString();
  }

  // 창 테두리는 실제 픽셀과 어긋난다(Win10+ 의 보이지 않는 리사이즈 여백).
  // DWM 이 아는 값을 먼저 묻고, 못 얻으면 GetWindowRect 로 떨어진다.
  public static RECT Bounds(IntPtr h) {
    RECT r;
    if (DwmGetWindowAttribute(h, 9 /*EXTENDED_FRAME_BOUNDS*/, out r, Marshal.SizeOf(typeof(RECT))) == 0)
      return r;
    GetWindowRect(h, out r);
    return r;
  }
}
'@

function Find-Window {
  param([int]$Pid_, [int]$TimeoutSec)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    $proc = Get-Process -Id $Pid_ -ErrorAction SilentlyContinue
    if (-not $proc) { throw "pid $Pid_ 가 이미 죽었다 — 창이 뜨기 전에 종료됐다(로그를 볼 것)." }
    $wins = [WinCap]::TopLevel([uint32]$Pid_)
    if ($wins.Count -gt 0) { return $wins[0] }
    Start-Sleep -Milliseconds 200
  }
  throw "pid $Pid_ 가 $TimeoutSec 초 안에 최상위 창을 안 만들었다."
}

$hwnd = if ($Hwnd -ne 0) { [IntPtr]$Hwnd } else { Find-Window -Pid_ $ProcessId -TimeoutSec $WaitSeconds }
$title = [WinCap]::Title($hwnd)

# 앞으로 올린다. 가려진 창을 화면에서 뜨면 **남의 창이 찍힌다** — 조용히 틀린 그림이라
# 리포트만 보면 안 보인다.
[void][WinCap]::ShowWindow($hwnd, 9)   # SW_RESTORE
[void][WinCap]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs

# ★ 앞으로 **올라갔는지 확인**한다. SetForegroundWindow 는 실패해도 조용하고(포커스
# 도둑질 방지 규칙 때문에 OS 가 거절한다), 시작 메뉴·알림처럼 위에 뜬 것이 있으면
# 화면 DC 캡처에 **그게 그대로 찍힌다**. 단색 가드는 이걸 못 잡는다 — 남의 창은
# 알록달록해서 색 종류가 오히려 늘어난다(실측: 시작 메뉴가 반쯤 덮인 장면이 색 282
# 종으로 가드를 통과했다). 실패로 떨어뜨리고 부른 쪽이 다시 찍게 한다.
$fg = [WinCap]::GetForegroundWindow()
if ($fg -ne $hwnd) {
  throw "대상 창이 앞에 없다(전경='$([WinCap]::Title($fg))'). 다른 창이 덮인 채로 찍히면 리포트에 남의 화면이 실린다 — 찍지 않는다."
}

$r = [WinCap]::Bounds($hwnd)
$w = $r.Right - $r.Left
$h = $r.Bottom - $r.Top
if ($w -le 0 -or $h -le 0) { throw "창 크기가 이상하다: ${w}x${h}" }

# ★ **가려졌나**를 픽셀 기준으로 본다. 전경 검사만으로는 다른 앱 창이 위에 떠 있는 것을
# 못 잡는다(실측 2026-07-31: Windows 설정 창이 덮여 흰 화면이 리포트에 실릴 뻔했다).
# 몇 번 다시 앞으로 올려 보고, 그래도 덮여 있으면 **찍지 않는다**.
$madeTopmost = $false
for ($try = 0; $try -lt 3; $try++) {
  $mid = [WinCap]::TopAt(($r.Left + [int]($w / 2)), ($r.Top + [int]($h / 2)))
  if ($mid -eq $hwnd) { break }
  [void][WinCap]::ShowWindow($hwnd, 9)
  [void][WinCap]::SetForegroundWindow($hwnd)
  if ($try -ge 1) {
    # 전경 요청이 거절당하는 경우(사용자 창이 위에 있을 때) — 내 창만 올린다.
    [WinCap]::Topmost($hwnd, $true)
    $madeTopmost = $true
  }
  Start-Sleep -Milliseconds 600
  $mid = [WinCap]::TopAt(($r.Left + [int]($w / 2)), ($r.Top + [int]($h / 2)))
  if ($mid -eq $hwnd) { break }
  if ($try -eq 2) {
    if ($madeTopmost) { [WinCap]::Topmost($hwnd, $false) }
    throw "대상 창이 다른 창에 덮여 있다(가운데를 가진 창='$([WinCap]::Title($mid))') — 덮인 채로 찍으면 남의 화면이 리포트에 실린다."
  }
}

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $h))
$g.Dispose()

# ★ 단색 가드. wgpu 창을 잘못된 방법으로 찍으면 결과는 오류가 아니라 **검은 사각형**이다.
# 그리드로 성기게 훑는다(전수는 느리고, 색이 8종이나 되는 그림에서 격자가 전부 같은 색일
# 확률은 없다).
$colors = New-Object 'System.Collections.Generic.HashSet[int]'
for ($y = 0; $y -lt $h; $y += [Math]::Max(1, [int]($h / 60))) {
  for ($x = 0; $x -lt $w; $x += [Math]::Max(1, [int]($w / 60))) {
    [void]$colors.Add($bmp.GetPixel($x, $y).ToArgb())
  }
}
if ($colors.Count -lt $MinColors) {
  $bmp.Dispose()
  throw "캡처가 사실상 단색이다(색 $($colors.Count) 종 < $MinColors) — 창이 가려졌거나 아직 안 그려졌다. 찍힌 것을 결과로 쓰지 않는다."
}

$dir = Split-Path -Parent $Out
if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force $dir | Out-Null }
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
# 올렸으면 **반드시 되돌린다** — 최상위로 남겨 두면 사용자의 다른 창을 계속 가린다.
if ($madeTopmost) { [WinCap]::Topmost($hwnd, $false) }

Write-Output "찍었다: $Out (${w}x${h}, 색 $($colors.Count) 종, 창 '$title')"
