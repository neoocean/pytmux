<#
.SYNOPSIS
  우리 GUI(`pytmux-gui.exe`)를 장면별로 띄워 찍는다 — 대조표의 **오른쪽** 컷.

.DESCRIPTION
  `gen_canon_shots.ps1`(왼쪽 = 정본)의 짝이다. **장면 이름을 같게** 두는 것이 계약이다 —
  `compose_side_by_side.ps1` 이 `<장면>-py.png` ↔ `<장면>-gui.png` 를 이름으로 짝짓는다.

  # 왜 스크립트로 두나

  종전 판(2026-07-30)은 오른쪽 컷을 손으로 찍었다. 그러면 다음 사람이 **같은 장면을 같은
  키로** 다시 못 만든다 — 대조표는 한 번 찍고 끝나는 문서가 아니라 UI 를 고칠 때마다 다시
  굽는 자다. 왼쪽에만 생성기가 있는 것은 반쪽이다.

  # 장면마다 서버를 되돌린다

  앞 장면의 분할·탭이 남으면 다음 컷이 그만큼 달라진다(정본 생성기와 같은 이유).

.PARAMETER Only
  쉼표로 가른 장면 이름들(생략하면 전부).

.PARAMETER Exe
  띄울 GUI 이진. 기본은 디버그 산출물.

.EXAMPLE
  $env:PYTMUX_HOME = "$sp\pytmuxhome"
  powershell -File scripts/gen_gui_shots.ps1 -Only base,menu,settings
#>
[CmdletBinding()]
param(
  [string]$Only,
  [string]$Exe = "target\debug\pytmux-gui.exe",
  [string]$Pytmux = "..\pytmux\pytmux.py",
  [string]$OutDir = "docs\reports\images",
  [string]$Prefix = "2026-07-31-cmp-",
  # 창을 이 크기로 줄이고 찍는다. **왜 줄이나**: 대조표는 두 컷을 같은 높이로 굽는데,
  # 1920x1200 창을 콘솔 창 높이에 맞춰 줄이면 우리 글자가 읽을 수 없게 작아진다(첫 판이
  # 그랬다). 콘솔 창과 비슷한 크기로 찍으면 **배치도 글자도** 나란히 읽힌다.
  [int]$WinWidth = 1240,
  [int]$WinHeight = 720
)

$ErrorActionPreference = 'Stop'

# 장면 → 키 단계들. **정본 생성기와 같은 이름**을 쓴다(대조표가 이름으로 짝을 찾는다).
# 키는 우리 클라 기준이지만, prefix 표가 정본 미러라 대부분 같은 자판이다.
$scenes = [ordered]@{
  "base"        = @()
  "split-lr"    = @("^b{%}")
  "split-nest"  = @("^b{%}", '^b"')
  "zoom"        = @("^b{%}", "^bz")
  "tabs"        = @("^bc", "^bc")
  "menu"        = @("^b{ENTER}")
  "palette"     = @("^b:")
  "settings"    = @("^b:", "settings{ENTER}")
  # 우리는 `notice-history` 팔레트 명령이 있다(정본은 배지가 유일한 입구 — 그 차이 자체가
  # 대조표의 재료다). 알림을 하나 만든 뒤 연다.
  "notices"     = @("^b:", "source-file{ENTER}", "^b:", "notice-history{ENTER}")
  "status"      = @("^b:", "status{ENTER}")
  "calendar"    = @("^b:", "calendar-mode{ENTER}")
  "clock"       = @("^b:", "clock-mode{ENTER}")
  "tree"        = @("^bw")
  "buffers"     = @("^b=")
  "keys"        = @("^b:", "list-keys{ENTER}")
  "plugins"     = @("^b:", "plugins{ENTER}")
  "tabswitch"   = @("^bc", "{ESC}", "{TAB}")
  "compose"     = @("{ESC}", "{INSERT}")
  "confirm-tab" = @("^b&")
  "scrollback"  = @("^b{[}")
}

if (-not $env:PYTMUX_HOME) {
  throw "PYTMUX_HOME 을 먼저 세운다 — 안 세우면 **사용자의 라이브 서버**에 붙는다(CLAUDE.md ⛔)."
}
# 정본 컷과 문구를 대조하려면 로케일이 같아야 한다(첫 판이 이걸 안 맞춰 통째로 무의미했다).
if (-not $env:LANG) { $env:LANG = "ko" }

New-Item -ItemType Directory -Force $OutDir | Out-Null
# ⚠ `$scenes.Keys` 를 쓰면 안 된다 — PowerShell 의 사전 어댑터는 **멤버보다 키를 먼저**
#   찾는데, 장면 중에 `keys` 가 있어서 `.Keys` 가 그 장면의 **키 배열**로 풀린다.
#   그러면 `-Only` 없이 돌릴 때 장면이 둘("^b:"·"list-keys{ENTER}")만 남고 전부
#   "모르는 장면"으로 떨어진다(2026-08-01 실측 — 3차 대조를 굽다 걸렸다).
$names = if ($Only) { $Only -split ',' | ForEach-Object { $_.Trim() } }
         else { $scenes.get_Keys() }

foreach ($name in $names) {
  if (-not $scenes.Contains($name)) { "  ?? 모르는 장면: $name"; continue }
  # 서버까지 되돌린다 — 앞 장면의 분할·탭이 남으면 다음 컷이 달라진다.
  python $Pytmux kill-server --yes 2>&1 | Out-Null
  Start-Sleep -Milliseconds 700
  python $Pytmux start-server 2>&1 | Out-Null
  Start-Sleep -Milliseconds 900

  $p = Start-Process -FilePath $Exe -PassThru
  Start-Sleep -Seconds 6      # 창·글꼴·첫 프레임
  if ($WinWidth -gt 0) {
    powershell -File scripts\resize_window.ps1 -ProcessId $p.Id -Width $WinWidth -Height $WinHeight | Out-Null
    Start-Sleep -Milliseconds 900   # 서버에 새 크기를 알리고 화면이 다시 차기까지
    # ★ 크기를 바꾸면 셸이 **다시 안 그린다**(cmd 는 SIGWINCH 로 리페인트하지 않는다) —
    #   그대로 찍으면 패널이 비어 보이고, 그건 제품 차이가 아니라 **찍는 방법의 자국**이다.
    #   Enter 한 번으로 프롬프트를 새로 뽑아 정본 컷과 같은 "셸이 있는 화면"으로 만든다.
    powershell -File scripts\send_keys_to_window.ps1 -ProcessId $p.Id -Keys "{ENTER}" -SettleMs 300 | Out-Null
    Start-Sleep -Milliseconds 700
  }

  foreach ($keys in $scenes[$name]) {
    if ($keys -match '^~(\d+)$') { Start-Sleep -Seconds ([int]$Matches[1]); continue }
    powershell -File scripts\send_keys_to_window.ps1 -ProcessId $p.Id -Keys $keys -SettleMs 400 | Out-Null
    Start-Sleep -Milliseconds 800
  }
  Start-Sleep -Milliseconds 900

  $out = Join-Path $OutDir "$Prefix$name-gui.png"
  $r = powershell -File scripts\capture_window.ps1 -ProcessId $p.Id -Out $out -SettleMs 500
  if (Test-Path $out) { "  $name -> $out" } else { "  !! $name 캡처 실패: $r" }

  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}
