<#
.SYNOPSIS
  떠 있는 창에 **수정키 조합**을 하나 눌러 넣는다(예: Ctrl+Shift+V).

.DESCRIPTION
  `send_keys_to_window.ps1` 과 목적은 같고 **층이 다르다**. 저쪽은
  `System.Windows.Forms.SendKeys` 를 쓰는데, 그것은 조합키를 자기 나름의 문법(`^+v`)으로
  풀어 보내며 무엇이 어떻게 나가는지 우리가 못 본다 — 2026-07-28 슬라이스 6에서 실제로
  `Ctrl+Shift+V` 가 창에 도달하지 못해 "실측 못 함"으로 남았던 자리다.

  이 스크립트는 `keybd_event`(SendInput 계열)로 **누름/뗌을 우리가 직접** 짝지어 보낸다.
  드래그·휠 하네스와 같은 결이고, 조합이 실제로 어떤 순서로 나가는지가 아래 코드에 그대로
  보인다.

  ⚠️ 키는 **전경 창**으로 간다. 대상 창이 앞에 없으면 남의 창에 타이핑하는 것이므로
  넣기 전에 확인하고 아니면 실패로 떨어뜨린다.

.PARAMETER ProcessId
  키를 받을 창을 가진 프로세스.

.PARAMETER Key
  글자 하나(`v`) 또는 가상키 이름(`RETURN`·`ESCAPE`·`TAB`).

.PARAMETER Ctrl / Shift / Alt
  함께 누를 수정키.

.EXAMPLE
  powershell -File scripts/send_chord_to_window.ps1 -ProcessId 123 -Key v -Ctrl -Shift
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][string]$Key,
  [switch]$Ctrl,
  [switch]$Shift,
  [switch]$Alt,
  [int]$SettleMs = 800
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\winlib.ps1"   # 창 찾기 한 벌(Get-AppWindow)

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class WinChord {
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
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern short VkKeyScan(char ch);
  [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);

  public const uint KEYUP = 0x0002;
  public const uint EXTENDED = 0x0001;
  public const byte CONTROL = 0x11, SHIFT = 0x10, MENU = 0x12;

  /// 스캔코드를 함께 싣는다 — 이것이 비면 앱(winit)이 키를 알아보지 못하는 경우가 있다.
  ///
  /// 화살표·Home·End 류는 **확장 키**라 그 플래그가 없으면 같은 스캔코드의 숫자패드
  /// 키로 읽힌다(NumLock 상태에 따라 숫자가 찍히거나 아무 일도 안 한다).
  public static void Tap(byte vk, bool down) {
    byte scan = (byte)MapVirtualKey(vk, 0);
    uint flags = down ? 0u : KEYUP;
    if (vk >= 0x21 && vk <= 0x2E) { flags |= EXTENDED; }
    keybd_event(vk, scan, flags, UIntPtr.Zero);
  }

  // ⛔ 창 찾기는 여기 두지 않는다 — `scripts/winlib.ps1` 의 `Get-AppWindow` 한 벌이다.
  //    종전에는 이 클래스마다 "그 pid 의 첫 보이는 최상위 창"을 복붙해 뒀는데, 그 술어는
  //    winit 의 숨은 15×15 이벤트 창(보이고 소유자도 없다)을 앱 창으로 집는다(pytmux-32).
}
'@

$vkNames = @{
  'RETURN' = 0x0D; 'ENTER' = 0x0D; 'ESCAPE' = 0x1B; 'ESC' = 0x1B;
  'TAB' = 0x09; 'SPACE' = 0x20; 'BACK' = 0x08;
  # 방향키·이동키(확장 키 — Tap 이 플래그를 붙인다). 인자 폼(G8v)처럼 **값을 방향키로
  # 고르는** 화면이 생기면서 필요해졌다.
  'LEFT' = 0x25; 'UP' = 0x26; 'RIGHT' = 0x27; 'DOWN' = 0x28;
  'HOME' = 0x24; 'END' = 0x23; 'PGUP' = 0x21; 'PGDN' = 0x22; 'DELETE' = 0x2E;
  # 작성창을 여는 키(패리티 e_ins). 확장키 구간(0x21~0x2E) 안이라 위 Tap 이
  # EXTENDED 플래그를 알아서 붙인다.
  'INSERT' = 0x2D;
  # 한/영 전환(VK_HANGUL). **이 상자에서 필요했다**: IME 가 한글 모드면
  # send_keys_to_window 로 넣은 `reconnect` 가 `ㄱㄷ채ㅜㅜㄷ차ㅅ` 로 조합돼 팔레트가
  # 아니라 셸에 들어간다(2026-07-30 실측 — TUI 라이브 드라이브가 그래서 막혔다).
  # 영문으로 맞춰 두고 타이핑하는 데 쓴다.
  'HANGUL' = 0x15;
}

if ($vkNames.ContainsKey($Key.ToUpper())) {
  $vk = [byte]$vkNames[$Key.ToUpper()]
} elseif ($Key.Length -eq 1) {
  $scan = [WinChord]::VkKeyScan([char]$Key)
  if ($scan -eq -1) { throw "키 '$Key' 를 이 레이아웃에서 못 찾았다." }
  $vk = [byte]($scan -band 0xFF)
} else {
  throw "키 '$Key' 를 모른다 — 글자 하나 또는 $($vkNames.Keys -join ', ') 중 하나."
}

$hwnd = Get-AppWindow -ProcessId $ProcessId

[void][WinChord]::ShowWindow($hwnd, 9)
[void][WinChord]::SetForegroundWindow($hwnd)
Start-Sleep -Milliseconds $SettleMs
if ([WinChord]::GetForegroundWindow() -ne $hwnd) {
  throw "대상 창이 앞에 없다 — 남의 창에 키를 넣지 않는다."
}

# 누름은 수정키부터, 뗌은 **역순**으로. 순서가 뒤집히면 앱이 조합이 아니라 낱개 키를 본다.
if ($Ctrl)  { [WinChord]::Tap([WinChord]::CONTROL, $true) }
if ($Shift) { [WinChord]::Tap([WinChord]::SHIFT, $true) }
if ($Alt)   { [WinChord]::Tap([WinChord]::MENU, $true) }
Start-Sleep -Milliseconds 60
[WinChord]::Tap($vk, $true)
Start-Sleep -Milliseconds 60
[WinChord]::Tap($vk, $false)
Start-Sleep -Milliseconds 60
if ($Alt)   { [WinChord]::Tap([WinChord]::MENU, $false) }
if ($Shift) { [WinChord]::Tap([WinChord]::SHIFT, $false) }
if ($Ctrl)  { [WinChord]::Tap([WinChord]::CONTROL, $false) }
Start-Sleep -Milliseconds 300

$mods = @()
if ($Ctrl) { $mods += 'Ctrl' }
if ($Shift) { $mods += 'Shift' }
if ($Alt) { $mods += 'Alt' }
Write-Output ("보냈다: " + (($mods + $Key) -join '+') + " (vk=0x{0:X2})" -f $vk)
