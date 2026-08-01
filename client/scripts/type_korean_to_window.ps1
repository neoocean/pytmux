<#
.SYNOPSIS
  창의 입력 언어를 **한국어로 바꾼 뒤** 자판을 눌러 한글을 조합해 넣는다. IME 경로 확인용.

.DESCRIPTION
  `send_keys_to_window.ps1` 은 ASCII 만 넣는다 — 한글은 자판 한 번이 글자 하나가 아니라
  **입력기가 조합**해서 만든다(`ㄱ`+`ㅏ`→`가`). 그 경로는 키 이벤트가 아니라 IME 이벤트
  (`Ime::Preedit`/`Ime::Commit`)로 앱에 도착하므로, 키만 넣어서는 아예 확인이 안 된다.

  그래서 이 스크립트는 두 가지를 한다:

  1. `WM_INPUTLANGCHANGEREQUEST` 로 **그 창의** 입력 언어를 한국어(0x0412)로 바꾼다.
     `ActivateKeyboardLayout` 은 부르는 쪽 스레드에만 걸려 남의 창에는 안 먹는다.
  2. 자판을 하나씩 눌러 입력기가 조합하게 둔다. `gks` → `한` 처럼 **로마자 자판 배열**을
     그대로 쓴다(두벌식).

  ⚠️ 사용자의 **현재 입력 언어를 바꾼다**. 끝나고 되돌리려면 `-Restore` 를 준다(기본 켜짐).

.PARAMETER ProcessId
  대상 창을 가진 프로세스.

.PARAMETER Keys
  두벌식 자판 문자열. 예: `gksrmf` → `한글`.

.EXAMPLE
  powershell -File scripts/type_korean_to_window.ps1 -ProcessId 123 -Keys gksrmf
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][string]$Keys,
  [switch]$NoRestore,
  [int]$SettleMs = 800
)

$ErrorActionPreference = 'Stop'

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class WinIme {
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
  [DllImport("user32.dll")] public static extern IntPtr GetKeyboardLayout(uint threadId);
  [DllImport("user32.dll")] public static extern IntPtr LoadKeyboardLayout(string id, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr PostMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern short VkKeyScanEx(char ch, IntPtr hkl);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);
  [DllImport("imm32.dll")] public static extern IntPtr ImmGetDefaultIMEWnd(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);

  public const uint WM_IME_CONTROL = 0x0283;
  public const int IMC_GETCONVERSIONMODE = 0x0001, IMC_SETCONVERSIONMODE = 0x0002;
  public const int IME_CMODE_NATIVE = 0x0001;

  /// 지금 한글 조합 모드인가. **토글(VK_HANGUL)로는 상태를 알 수 없어** 물어봐야 한다 —
  /// 앞선 실행이 켜 둔 채 끝났으면 토글이 오히려 영문으로 되돌린다(실측 2026-07-28).
  public static bool IsHangulMode(IntPtr hwnd) {
    IntPtr ime = ImmGetDefaultIMEWnd(hwnd);
    if (ime == IntPtr.Zero) return false;
    IntPtr mode = SendMessage(ime, WM_IME_CONTROL, (IntPtr)IMC_GETCONVERSIONMODE, IntPtr.Zero);
    return (mode.ToInt64() & IME_CMODE_NATIVE) != 0;
  }

  /// 한글 조합 모드를 **켠다**(이미 켜져 있으면 그대로).
  public static bool SetHangulMode(IntPtr hwnd) {
    IntPtr ime = ImmGetDefaultIMEWnd(hwnd);
    if (ime == IntPtr.Zero) return false;
    IntPtr mode = SendMessage(ime, WM_IME_CONTROL, (IntPtr)IMC_GETCONVERSIONMODE, IntPtr.Zero);
    long want = mode.ToInt64() | IME_CMODE_NATIVE;
    SendMessage(ime, WM_IME_CONTROL, (IntPtr)IMC_SETCONVERSIONMODE, (IntPtr)want);
    return true;
  }

  public const uint WM_INPUTLANGCHANGEREQUEST = 0x0050;
  public const uint KEYUP = 0x0002;
  public const byte SHIFT = 0x10;

  public static void Tap(byte vk, bool down) {
    keybd_event(vk, (byte)MapVirtualKey(vk, 0), down ? 0u : KEYUP, UIntPtr.Zero);
  }

  public static IntPtr TopLevel(uint want) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, p) => {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == want && IsWindowVisible(h) && GetWindow(h, 4) == IntPtr.Zero) { found = h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }

  /// 그 창의 스레드가 지금 쓰는 레이아웃.
  public static IntPtr LayoutOf(IntPtr hwnd) {
    uint pid;
    uint tid = GetWindowThreadProcessId(hwnd, out pid);
    return GetKeyboardLayout(tid);
  }
}
'@

$hwnd = [WinIme]::TopLevel([uint32]$ProcessId)
if ($hwnd -eq [IntPtr]::Zero) { throw "pid $ProcessId 에 최상위 창이 없다." }

[void][WinIme]::ShowWindow($hwnd, 9)
[void][WinIme]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs
if ([WinIme]::GetForegroundWindow() -ne $hwnd) {
  throw "대상 창이 앞에 없다 — 남의 창에 키를 넣지 않는다."
}

$before = [WinIme]::LayoutOf($hwnd)
$korean = [WinIme]::LoadKeyboardLayout("00000412", 1)  # KLF_ACTIVATE
if ($korean -eq [IntPtr]::Zero) { throw "한국어 레이아웃(0412)을 못 불러왔다 — 설치돼 있나?" }

# 창의 스레드에 요청한다. ActivateKeyboardLayout 은 **부르는 스레드에만** 걸려 남의 창은
# 안 바뀐다.
[void][WinIme]::PostMessage($hwnd, [WinIme]::WM_INPUTLANGCHANGEREQUEST, [IntPtr]::Zero, $korean)
Start-Sleep -Milliseconds 600

$after = [WinIme]::LayoutOf($hwnd)
Write-Output ("레이아웃: {0:X} → {1:X}" -f $before.ToInt64(), $after.ToInt64())

# ★ 레이아웃만 바꾸면 **영문 모드**다. 한국어 입력기는 조합 모드가 켜져야 비로소 자모를
# 조합한다 — 이걸 빼면 자판이 그대로 로마자로 들어간다(실측 2026-07-28).
#
# VK_HANGUL 을 그냥 두드리면 **토글**이라 이미 켜져 있을 때 오히려 꺼진다(같은 날 두 번째로
# 밟았다). 그래서 IME 창에 물어보고 **켜는 방향으로만** 맞춘다.
if (-not [WinIme]::SetHangulMode($hwnd)) {
  [WinIme]::Tap(0x15, $true); [WinIme]::Tap(0x15, $false)   # 폴백: VK_HANGUL 토글
}
Start-Sleep -Milliseconds 400
Write-Output ("조합 모드: " + [WinIme]::IsHangulMode($hwnd))

foreach ($ch in $Keys.ToCharArray()) {
  $scan = [WinIme]::VkKeyScanEx($ch, $korean)
  if ($scan -eq -1) { throw "'$ch' 를 이 레이아웃에서 못 찾았다." }
  $vk = [byte]($scan -band 0xFF)
  $needShift = (($scan -shr 8) -band 1) -eq 1
  if ($needShift) { [WinIme]::Tap([WinIme]::SHIFT, $true) }
  [WinIme]::Tap($vk, $true)
  Start-Sleep -Milliseconds 40
  [WinIme]::Tap($vk, $false)
  if ($needShift) { [WinIme]::Tap([WinIme]::SHIFT, $false) }
  # 조합이 끝날 틈을 준다. 너무 빠르면 입력기가 앞 글자를 확정하기 전에 다음 자모가 온다.
  Start-Sleep -Milliseconds 120
}

# 마지막 글자는 **다른 키가 와야 확정된다**(조합 중인 채로 남는다). 오른쪽 화살표로
# 조합을 끊는다 — 패널 안 내용은 안 건드리면서 확정만 시킨다.
Start-Sleep -Milliseconds 200
[WinIme]::Tap(0x27, $true); [WinIme]::Tap(0x27, $false)   # VK_RIGHT
Start-Sleep -Milliseconds 300

if (-not $NoRestore) {
  [void][WinIme]::PostMessage($hwnd, [WinIme]::WM_INPUTLANGCHANGEREQUEST, [IntPtr]::Zero, $before)
  Start-Sleep -Milliseconds 300
}

Write-Output ("보냈다(한글 자판): " + $Keys)
