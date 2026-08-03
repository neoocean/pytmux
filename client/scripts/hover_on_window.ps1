<#
.SYNOPSIS
  떠 있는 창 위로 **버튼 없이** 커서를 옮긴다(hover). 옮긴 자리의 **커서 모양**을 이름으로
  돌려주고, `-Click` 이면 그 자리에서 한 번 누르고 뗀다.

.DESCRIPTION
  `drag_mouse_on_window.ps1` 이 못 하는 두 가지를 한다.

  1. **hover 만** — 드래그 스크립트는 항상 누른다. 그런데 크롬의 hover 배경·스플리터
     hover 강조는 "누르지 않은 채 위에 있을 때"의 그림이라, 누르면 다른 상태가 찍힌다.
  2. **커서 모양 판정** — 스플리터 위 리사이즈 커서(`Cursor::ResizeLeftRight`)는
     `capture_window.ps1` 로 **잡을 수 없다**. BitBlt 은 커서를 합성하지 않아서 그림에
     커서가 아예 없다(그림만 보면 "커서가 안 바뀐다"로 오판한다). 그래서 픽셀이 아니라
     `GetCursorInfo` 로 지금 커서 핸들을 읽고, 시스템 커서(`LoadCursorW(IDC_*)`)와 맞춰
     이름으로 돌려준다. winit 도 같은 `LoadCursorW` 로 시스템 커서를 얻으므로 핸들이
     같다.

  커서를 옮긴 뒤 **그 자리에 둔다** — 이어서 `capture_window.ps1` 을 부르면 hover 상태가
  그대로 찍힌다(캡처는 커서를 안 움직인다).

  좌표는 창 기준 비율(0~1)이 기본이고 `-ClientPixels` 면 클라이언트 영역 픽셀이다 —
  드래그 스크립트와 같은 규칙(그 스크립트 주석의 152px 원점 차이 참조).

  ⚠️ 진짜 커서를 움직인다 — 도는 동안 사람이 마우스를 만지면 결과가 섞인다.

.PARAMETER ProcessId
  대상 창을 가진 프로세스.

.PARAMETER X / Y
  창 안에서의 비율 좌표(0~1) 또는 `-ClientPixels` 면 클라이언트 픽셀.

.PARAMETER Click
  옮긴 자리에서 왼쪽 버튼을 누르고 뗀다(제자리 클릭 — 드래그가 아니다).

.PARAMETER Steps
  목표까지 나눠 보낼 이동 횟수(기본 4). 한 번에 점프해도 WM_MOUSEMOVE 는 오지만,
  중첩 원소의 진입 판정을 확실히 밟으려면 몇 칸 나누는 편이 안전하다.

.EXAMPLE
  powershell -File scripts/hover_on_window.ps1 -ProcessId 123 -X 0.1 -Y 0.02
  powershell -File scripts/hover_on_window.ps1 -ProcessId 123 -X 640 -Y 300 -ClientPixels -Click
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][double]$X,
  [Parameter(Mandatory = $true)][double]$Y,
  [switch]$ClientPixels,
  [switch]$Click,
  [int]$Steps = 4,
  [int]$SettleMs = 700,
  # 옮긴 뒤 커서 모양을 읽기 전까지의 여유. 앱이 MouseMoved 를 처리하고 커서를 세우는
  # 시간이다 — 0 이면 아직 옛 모양을 읽는다.
  [int]$CursorMs = 400
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\winlib.ps1"   # 창 찾기 한 벌(Get-AppWindow)

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class WinHover {
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
  [DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT val, int size);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern IntPtr LoadCursorW(IntPtr hInstance, int id);
  [DllImport("user32.dll")] public static extern bool GetCursorInfo(ref CURSORINFO ci);

  [StructLayout(LayoutKind.Sequential)]
  public struct POINT { public int X, Y; }
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)]
  public struct CURSORINFO { public int cbSize; public int flags; public IntPtr hCursor; public POINT pt; }

  public const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004;

  // ⛔ 창 찾기는 여기 두지 않는다 — `scripts/winlib.ps1` 의 `Get-AppWindow` 한 벌이다.
  //    종전에는 이 클래스마다 "그 pid 의 첫 보이는 최상위 창"을 복붙해 뒀는데, 그 술어는
  //    winit 의 숨은 15×15 이벤트 창(보이고 소유자도 없다)을 앱 창으로 집는다(pytmux-32).

  public static string Title(IntPtr h) {
    int n = GetWindowTextLength(h);
    var sb = new System.Text.StringBuilder(n + 1);
    GetWindowText(h, sb, sb.Capacity);
    return sb.ToString();
  }

  public static RECT Bounds(IntPtr h) {
    RECT r;
    if (DwmGetWindowAttribute(h, 9, out r, Marshal.SizeOf(typeof(RECT))) == 0) return r;
    GetWindowRect(h, out r);
    return r;
  }

  public static POINT ClientOrigin(IntPtr h) {
    POINT p; p.X = 0; p.Y = 0;
    ClientToScreen(h, ref p);
    return p;
  }

  /// 지금 화면의 커서 모양을 이름으로. 표에 없으면 핸들을 그대로 적는다(앱이 만든
  /// 커스텀 커서일 수 있다 — "모른다"를 "화살표다"로 뭉개지 않는다).
  public static string CursorName() {
    CURSORINFO ci = new CURSORINFO();
    ci.cbSize = Marshal.SizeOf(typeof(CURSORINFO));
    if (!GetCursorInfo(ref ci)) return "?(GetCursorInfo 실패)";
    if (ci.hCursor == IntPtr.Zero) return "숨김";
    int[] ids = { 32512, 32513, 32514, 32642, 32643, 32644, 32645, 32646, 32648, 32649, 32650, 32651 };
    string[] names = { "ARROW", "IBEAM", "WAIT", "SIZENWSE", "SIZENESW", "SIZEWE", "SIZENS",
                       "SIZEALL", "NO", "HAND", "APPSTARTING", "HELP" };
    for (int i = 0; i < ids.Length; i++) {
      if (LoadCursorW(IntPtr.Zero, ids[i]) == ci.hCursor) return names[i];
    }
    return "custom(0x" + ci.hCursor.ToString("x") + ")";
  }
}
'@

$hwnd = Get-AppWindow -ProcessId $ProcessId

[void][WinHover]::ShowWindow($hwnd, 9)
[void][WinHover]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs
if ([WinHover]::GetForegroundWindow() -ne $hwnd) {
  throw "대상 창이 앞에 없다(전경='$([WinHover]::Title([WinHover]::GetForegroundWindow()))') — 남의 창에 마우스를 넣지 않는다."
}

if ($ClientPixels) {
  $o = [WinHover]::ClientOrigin($hwnd)
  $tx = [int]($o.X + $X); $ty = [int]($o.Y + $Y)
  $w = 0; $h = 0
} else {
  $r = [WinHover]::Bounds($hwnd)
  $w = $r.Right - $r.Left
  $h = $r.Bottom - $r.Top
  $tx = [int]($r.Left + $w * $X)
  $ty = [int]($r.Top + $h * $Y)
}

# 목표 바로 옆에서 출발해 몇 칸 다가간다 — 창 밖에서 순간이동해 들어오면 진입 이벤트가
# 한 번뿐이고, 중첩 원소(탭 안의 ×)의 판정이 프레임에 따라 갈릴 수 있다.
$sx = $tx - 24; $sy = $ty - 12
[void][WinHover]::SetCursorPos($sx, $sy)
Start-Sleep -Milliseconds 80
for ($i = 1; $i -le $Steps; $i++) {
  $x = [int]($sx + ($tx - $sx) * $i / $Steps)
  $y = [int]($sy + ($ty - $sy) * $i / $Steps)
  [void][WinHover]::SetCursorPos($x, $y)
  Start-Sleep -Milliseconds 50
}
[void][WinHover]::SetCursorPos($tx, $ty)
Start-Sleep -Milliseconds $CursorMs

$cursor = [WinHover]::CursorName()

if ($Click) {
  [WinHover]::mouse_event([WinHover]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 90
  [WinHover]::mouse_event([WinHover]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 300
}

$what = if ($Click) { "눌렀다" } else { "올렸다" }
Write-Output "$what : ($tx,$ty) (창 ${w}x${h}) 커서=$cursor"
