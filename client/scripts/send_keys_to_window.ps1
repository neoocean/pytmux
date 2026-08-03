<#
.SYNOPSIS
  떠 있는 창에 키를 실제로 눌러 넣는다. GUI 입력 경로의 라이브 확인용.

.DESCRIPTION
  `capture_window.ps1` 이 "무엇이 그려졌나"를 답한다면 이 스크립트는 "무엇이 들어가나"를
  답한다. 창을 앞으로 올리고 `SendKeys` 로 실제 키 이벤트를 넣는다 — 우리 코드에 값을
  주입하는 것이 아니라 **OS 가 만든 키 이벤트**가 winit → warpui → 이 클라의
  `on_keydown` 을 타고 서버까지 가는지 보는 것이다.

  P6 에서 배운 것을 여기에도 민다: "헤드리스 불가"의 절반은 사람이 아니라 **그 OS 의
  장치**가 필요한 것이었고, 장치는 프로그램이 만들 수 있다. 바깥 터미널의 키 인코딩은
  정말 사람이 눌러야 알지만, "창이 키를 받아 패널에 넣는가"는 이렇게 물을 수 있다.

  ⚠️ SendKeys 는 **전경 창**에 들어간다. 대상 창이 앞에 없으면 남의 창에 타이핑하는
  것이므로, 넣기 전에 전경 창이 우리 창인지 확인하고 아니면 실패로 떨어뜨린다.

.PARAMETER ProcessId
  키를 받을 창을 가진 프로세스.

.PARAMETER Keys
  SendKeys 문법 문자열. 예: `"echo hi{ENTER}"` · `"^c"`(Ctrl+C) · `"{ESC}"`.
  `+^%~(){}[]` 는 SendKeys 의 메타 문자라 글자로 넣으려면 `{}` 로 감싼다.

.PARAMETER SettleMs
  창을 앞으로 올린 뒤 기다리는 시간(기본 800).

.EXAMPLE
  powershell -File scripts/send_keys_to_window.ps1 -ProcessId 1234 -Keys "echo hi{ENTER}"
#>
[CmdletBinding()]
param(
  # 창 소유 프로세스. **콘솔 앱에는 못 쓴다** — 아래 `-Hwnd` 참조.
  [int]$ProcessId = 0,
  # 창 핸들을 직접 준다(`launch_console_window.ps1` 이 돌려주는 값).
  #
  # 왜 필요한가: 이 상자의 콘솔 창은 **Windows Terminal 프로세스가 소유**한다. 그래서
  # 파이썬 정본 클라처럼 콘솔에서 도는 것은 pid 로 창을 못 찾고(자기 pid 에는 창이
  # 없다), 이름으로 찾으면 **사용자가 쓰고 있는 터미널 창**을 집을 수 있다. 띄운 쪽이
  # 새로 생긴 창을 확정해 그 핸들을 넘기는 것이 유일하게 안전한 길이다.
  [long]$Hwnd = 0,
  # SendKeys 문법 대신 **글자 그대로** 넣는다(그리고 Enter). IME 를 통째로 우회한다.
  #
  # 왜 필요한가(실측 2026-07-31): 한글 입력기가 켜진 상자에서는 `SendKeys` 가 보내는
  # 가상키를 IME 가 가로채 조합해 버린다 — 패널에 `claude` 대신 `치명ㄷ` 이 들어갔다.
  # 입력기 조합 모드를 끄고(WM_IME_CONTROL) 자판 언어를 영문으로 바꿔도(HKL) 안 됐다.
  # `KEYEVENTF_UNICODE` 로 **문자 자체**를 주입하면 IME 가 개입할 자리가 없다.
  [string]$Text = "",
  [string]$Keys = "",
  # 치기 **직전에** 입력기를 영문으로 돌린다.
  #
  # 왜 여기여야 하나(실측 2026-07-31): 창이 전경이 되는 순간 윈도가 그 창이 쓰던
  # 입력기(한글)를 되살린다. 그래서 부르는 쪽에서 미리 영문으로 바꿔 봐야 소용이 없고,
  # **전경 확보 뒤**에 바꿔야 한다. 안 하면 `claude` 가 패널에 `치명ㄷ` 으로 들어간다.
  [switch]$ImeEnglish,
  [int]$SettleMs = 800
)

if (($ProcessId -eq 0) -eq ($Hwnd -eq 0)) {
  throw "-ProcessId 나 -Hwnd 중 **하나만** 준다(둘 다이거나 둘 다 아니면 무엇을 칠지 모른다)."
}
if (($Keys -eq "") -eq ($Text -eq "")) {
  throw "-Keys 나 -Text 중 **하나만** 준다."
}

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\winlib.ps1"   # 창 찾기 한 벌(Get-AppWindow)
Add-Type -AssemblyName System.Windows.Forms

Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class WinSend {
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

  [DllImport("imm32.dll")] public static extern IntPtr ImmGetDefaultIMEWnd(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  const uint WM_IME_CONTROL = 0x0283;
  const int IMC_GET = 0x0001, IMC_SET = 0x0002, CMODE_NATIVE = 0x0001;

  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)]
  public static extern IntPtr LoadKeyboardLayout(string id, uint flags);
  const uint WM_INPUTLANGCHANGEREQUEST = 0x0050;

  /// 그 창의 입력을 **영문으로** 돌린다 — 두 겹으로 건다.
  ///
  /// ① 입력 **언어(HKL)** 를 en-US 로 바꾼다(`WM_INPUTLANGCHANGEREQUEST`). ② 그래도
  /// 남는 조합 상태를 위해 IME 변환 모드의 한글 비트를 내린다.
  ///
  /// ②만으로는 부족했다(실측 2026-07-31): 변환 모드를 껐는데도 `claude` 가 패널에
  /// `치명ㄷ` 으로 들어갔다 — 자판 자체가 한글 배열이면 조합을 꺼도 자모가 나온다.
  public static void ImeEnglish(IntPtr hwnd) {
    IntPtr en = LoadKeyboardLayout("00000409", 1 /*KLF_ACTIVATE*/);
    if (en != IntPtr.Zero) PostMessage(hwnd, WM_INPUTLANGCHANGEREQUEST, IntPtr.Zero, en);
    IntPtr ime = ImmGetDefaultIMEWnd(hwnd);
    if (ime == IntPtr.Zero) return;
    long mode = SendMessage(ime, WM_IME_CONTROL, (IntPtr)IMC_GET, IntPtr.Zero).ToInt64();
    SendMessage(ime, WM_IME_CONTROL, (IntPtr)IMC_SET, (IntPtr)(mode & ~CMODE_NATIVE));
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct INPUT { public uint type; public KEYBDINPUT ki; public int pad1, pad2; }
  [StructLayout(LayoutKind.Sequential)]
  public struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr extra; }
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int size);
  const uint INPUT_KEYBOARD = 1, KEYEVENTF_KEYUP = 0x0002, KEYEVENTF_UNICODE = 0x0004;
  const ushort VK_RETURN = 0x0D;

  /// 글자를 **유니코드 그대로** 주입한다(IME 우회). `\n` 은 Enter 로 친다.
  public static void TypeUnicode(string text) {
    foreach (char ch in text) {
      if (ch == '\n') { Key(VK_RETURN, '\0'); continue; }
      Key(0, ch);
    }
  }

  static void Key(ushort vk, char ch) {
    uint flags = (vk == 0) ? KEYEVENTF_UNICODE : 0;
    var down = new INPUT { type = INPUT_KEYBOARD,
      ki = new KEYBDINPUT { wVk = vk, wScan = (ushort)ch, dwFlags = flags, time = 0, extra = IntPtr.Zero } };
    var up = down;
    up.ki.dwFlags = flags | KEYEVENTF_KEYUP;
    SendInput(2, new[] { down, up }, Marshal.SizeOf(typeof(INPUT)));
    System.Threading.Thread.Sleep(12);
  }

  // ⛔ 창 찾기는 여기 두지 않는다 — `scripts/winlib.ps1` 의 `Get-AppWindow` 한 벌이다.
  //    종전에는 이 클래스마다 "그 pid 의 첫 보이는 최상위 창"을 복붙해 뒀는데, 그 술어는
  //    winit 의 숨은 15×15 이벤트 창(보이고 소유자도 없다)을 앱 창으로 집는다(pytmux-32).
}
'@

if ($Hwnd -ne 0) {
  $hwnd = [IntPtr]$Hwnd
} else {
  $hwnd = Get-AppWindow -ProcessId $ProcessId
}

[void][WinSend]::ShowWindow($hwnd, 9)
[void][WinSend]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs

# ★ SendKeys 는 전경 창으로 간다. 확인 안 하면 **남의 창에 타이핑**한다.
if ([WinSend]::GetForegroundWindow() -ne $hwnd) {
  throw "대상 창이 앞에 없다 — 남의 창에 키를 넣지 않는다."
}

if ($ImeEnglish) {
  [WinSend]::ImeEnglish($hwnd)
  Start-Sleep -Milliseconds 150   # 입력기 전환이 앉을 틈
}

if ($Text) {
  [WinSend]::TypeUnicode($Text)
  Start-Sleep -Milliseconds 300
  Write-Output "쳤다(유니코드): $Text"
} else {
  [System.Windows.Forms.SendKeys]::SendWait($Keys)
  Start-Sleep -Milliseconds 300
  Write-Output "보냈다: $Keys"
}
