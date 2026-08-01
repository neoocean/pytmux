<#
.SYNOPSIS
  **파이썬 정본 클라를 이 상자에서 실제로 띄워** 장면별 스크린샷(PNG)을 찍는다.
  우리 GUI 컷과 나란히 놓고 인터페이스를 대조하는 문서(`docs/reports/*ui-parity*`)의 왼쪽.

.DESCRIPTION
  # 왜 SVG 합성이 아니라 실물인가

  pytmux 저장소에도 정본 컷 생성기가 있다(`scripts/gen_screenshots.py` → Textual
  헤드리스 `save_screenshot` → SVG). 그 컷은 **맥에서·다른 시점에** 구운 것이라 셸도
  (zsh vs cmd) 날짜도 호스트도 우리 GUI 컷과 다르다 — 대조표에서 "크롬 차이"와 "환경
  차이"가 섞인다. 이 스크립트는 같은 상자·같은 셸·같은 로케일·같은 날짜로 찍어 **크롬
  차이만** 남긴다.

  # 어떻게 창을 잡나

  이 상자의 콘솔 창은 Windows Terminal 이 소유해서 pid 로 못 찾고, 제목으로 찾으면
  사용자의 창을 집을 수 있다. `launch_console_window.ps1` 이 **띄우기 전후의 창 목록을
  비교**해 새로 생긴 창의 핸들을 확정해 준다(그 문서 참조).

  # 무엇을 잘라내나

  캡처에는 Windows Terminal 의 탭 띠·최소화/최대화 단추가 같이 찍힌다. 그건 pytmux 가
  아니므로 위에서 `-ChromePx` 만큼 잘라낸다(이 상자 실측 기본값 72).

.PARAMETER Only
  쉼표로 장면 이름들(생략하면 전부).

.PARAMETER OutDir
  PNG 를 놓을 곳(기본 `docs/reports/images`).

.PARAMETER ChromePx
  잘라낼 터미널 크롬 높이(픽셀).

.EXAMPLE
  powershell -File scripts/gen_canon_shots.ps1 -Only base,menu,settings
#>
[CmdletBinding()]
param(
  [string]$Only = "",
  [string]$OutDir = "docs\reports\images",
  [string]$Prefix = "2026-07-30-cmp-",
  [int]$ChromePx = 72,
  [string]$Pytmux = "..\pytmux\pytmux.py",
  # 패널이 띄울 프로그램(`PYTMUX_SHELL`). 예: `claude.exe`.
  #
  # 왜 있나: Claude Code 화면을 찍으려면 패널 안에서 claude 가 돌아야 하는데, 이 상자에서
  # **패널에 글자를 넣는 길이 전부 막혔다** — SendKeys 는 한글 IME 가 가로채 `claude` 를
  # `치명ㄷ` 으로 만들고, 유니코드 주입(KEYEVENTF_UNICODE)과 정본의 `send-keys` 명령은
  # 패널에 닿지 않았다(2026-07-31 실측). 타이핑을 없애는 것이 답이다: 서버를 이 환경변수와
  # 함께 띄우면 **패널의 셸 자체가** 그 프로그램이 된다(`serverpty.py`).
  [string]$PaneShell = ""
)

$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Drawing

Add-Type @'
using System;
using System.Runtime.InteropServices;

public class ImeCtl {
  [DllImport("imm32.dll")] public static extern IntPtr ImmGetDefaultIMEWnd(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
  const uint WM_IME_CONTROL = 0x0283;
  const int GET = 0x0001, SET = 0x0002, NATIVE = 0x0001;

  /// 그 창의 입력기를 **영문(조합 끄기)** 으로 돌린다.
  ///
  /// 왜 필요한가(실측 2026-07-30): 한글 조합이 켜진 채 자판을 넣으면 `z`·`w`·`"` 가
  /// 한글로 바뀌어 앱에 도착한다 — 정본 컷에서 `prefix "` 가 패널에 `ㄴㄷㅅ…` 을 넣었고,
  /// 줌·트리 장면은 아무 일도 안 일어난 것처럼 보였다(키가 다른 글자였으니 당연하다).
  /// 토글(VK_HANGUL)로는 상태를 모르니 **조회 후 비트를 내린다**.
  public static bool English(IntPtr hwnd) {
    IntPtr ime = ImmGetDefaultIMEWnd(hwnd);
    if (ime == IntPtr.Zero) return false;
    long mode = SendMessage(ime, WM_IME_CONTROL, (IntPtr)GET, IntPtr.Zero).ToInt64();
    SendMessage(ime, WM_IME_CONTROL, (IntPtr)SET, (IntPtr)(mode & ~NATIVE));
    return true;
  }
}
'@

# 장면 → SendKeys 단계들. 우리 GUI 쪽 장면 이름과 **같은 이름**을 쓴다(대조표가 짝을
# 이름으로 찾는다). 키는 정본 기준이고, 팔레트 경유가 필요한 것은 `^b:` 로 연다.
$scenes = [ordered]@{
  "base"        = @()
  "split-lr"    = @("^b{%}")
  "split-nest"  = @("^b{%}", '^b"')
  "zoom"        = @("^b{%}", "^bz")
  "tabs"        = @("^bc", "^bc")
  "menu"        = @("^b{ENTER}")
  "palette"     = @("^b:")
  "settings"    = @("^b:", "settings{ENTER}")
  # ★ 정본은 알림 이력에 팔레트 명령이 없다 — **상태줄 배지**(Esc 모드에서 아래로
  #   내려가 배지 포커스 → Enter)나 배지 클릭이 유일한 입구다(client.py `open_notice_history`).
  #   우리 클라는 `notice-history` 팔레트 명령이 있다 — 그 자체가 대조표의 재료다.
  # 정본은 알림이 **하나라도 쌓여야** 배지가 생긴다 — `source-file`(설정 다시 읽기)로
  # 알림을 하나 만든 뒤, Esc 모드에서 배지 줄로 내려가 알림 배지를 골라 연다.
  "notices"     = @("^b:", "source-file{ENTER}")
  # 상태 팝업의 입구는 팔레트가 아니라 **상태줄 배지**다(Esc → 배지 줄 → Enter).
  "status"      = @("{ESC}", "{DOWN}", "{ENTER}")
  "calendar"    = @("^b:", "calendar-mode{ENTER}")
  # 시계는 플러그인 명령이다(정본에 `prefix t` 바인딩이 없다 — 그건 우리 쪽 관습).
  "clock"       = @("^b:", "clock-mode{ENTER}")
  "tree"        = @("^bw")
  "buffers"     = @("^b=")
  "keys"        = @("^b:", "list-keys{ENTER}")
  "plugins"     = @("^b:", "plugins{ENTER}")
  "tabswitch"   = @("^bc", "{ESC}", "{TAB}")
  "compose"     = @("{ESC}", "{INSERT}")
  "confirm-tab" = @("^b&")
  "scrollback"  = @("^b{[}")

  # ── Claude Code 를 **패널 안에서 실제로 돌리는** 장면들 ────────────────────────
  # 사용자 요청 2026-07-31. 정본의 Claude 계열 화면(토큰 로그·사용 한도·프롬프트 이력)은
  # 살아 있는 claude 세션이 있어야 재료가 생긴다 — 그래서 진짜로 띄운다.
  # `~N` 대기가 넉넉한 이유: claude 부팅이 이 상자에서 10초 안팎이고, 응답은 더 걸린다.
  "claude"        = @("~25")   # 패널 셸이 이미 claude 다(-PaneShell)
  "claude-answer" = @("^b:", "send-keys claude Enter{ENTER}", "~25", "^b:", "send-keys hi Enter{ENTER}", "~40")
  "token-log"     = @("^b:", "send-keys claude Enter{ENTER}", "~25", "^b:", "send-keys hi Enter{ENTER}", "~40", "^b:", "token-log{ENTER}", "~3")
  "usage-panel"   = @("^b:", "send-keys claude Enter{ENTER}", "~25", "^b:", "usage-panel{ENTER}", "~8")
  "prompt-history"= @("^b:", "send-keys claude Enter{ENTER}", "~25", "^b:", "send-keys hi Enter{ENTER}", "~40", "^b:", "prompt-history{ENTER}", "~3")

  # ── 플러그인 화면(정본에만 있는 것들) ─────────────────────────────────────────
  "ncd"           = @("^b:", "ncd{ENTER}", "~4")
  "mdir"          = @("^b:", "mdir{ENTER}", "~4")
  "p4changes"     = @("^b:", "p4-show-submitted-changelists{ENTER}", "~8")
}

if (-not $env:PYTMUX_HOME) {
  throw "PYTMUX_HOME 을 먼저 세운다 — 안 세우면 **사용자의 라이브 서버**에 붙는다(CLAUDE.md ⛔)."
}
# 정본도 한국어로 맞춘다(우리 GUI 컷과 문구를 대조하려면 로케일이 같아야 한다).
if (-not $env:LANG) { $env:LANG = "ko" }
# ★ 이 상자에는 NO_COLOR=1 이 걸려 있다. 그대로 두면 Textual 이 색을 통째로 버리고
#   모노크롬 필터에서 죽기까지 한다(실측) — 색이 곧 비교 대상이라 반드시 지운다.
Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue

# ⚠ `$scenes.Keys` 를 쓰면 안 된다 — PowerShell 의 사전 어댑터는 **멤버보다 키를 먼저**
#   찾는데, 장면 중에 `keys` 가 있어서 `.Keys` 가 그 장면의 **키 배열**로 풀린다.
#   그러면 `-Only` 없이 돌릴 때 장면이 둘("^b:"·"list-keys{ENTER}")만 남고 전부
#   "모르는 장면"으로 떨어진다(2026-08-01 실측 — 3차 대조를 굽다 걸렸다).
$names = if ($Only) { $Only -split ',' | ForEach-Object { $_.Trim() } }
         else { $scenes.get_Keys() }

function Stop-Tree([int]$rootPid) {
  foreach ($c in (Get-CimInstance Win32_Process -Filter "ParentProcessId=$rootPid" -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $c.ProcessId -Force -ErrorAction SilentlyContinue
  }
  Stop-Process -Id $rootPid -Force -ErrorAction SilentlyContinue
}

foreach ($name in $names) {
  if (-not $scenes.Contains($name)) { "  ?? 모르는 장면: $name"; continue }
  # 서버까지 되돌린다 — 앞 장면의 분할·탭이 남으면 다음 컷이 그만큼 달라진다.
  python $Pytmux kill-server --yes | Out-Null
  Start-Sleep -Milliseconds 700
  # 패널 셸은 **서버가 정한다** — 그래서 기동 전에 세운다.
  if ($PaneShell) { $env:PYTMUX_SHELL = $PaneShell } else { Remove-Item Env:PYTMUX_SHELL -ErrorAction SilentlyContinue }
  # ★ Claude Code 를 패널에서 띄울 때는 **부모 세션의 표식을 지운다**. 안 지우면 패널의
  #   claude 가 이 세션의 자식으로 붙어(remote-control 자동 활성) 화면에 **세션 URL**
  #   까지 뜬다 — 리포트에 남의(사용자의) 세션 링크가 실릴 자리다(2026-07-31 실측).
  foreach ($v in "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                 "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
                 "CLAUDE_CODE_MAX_OUTPUT_TOKENS", "CLAUDE_PID") {
    Remove-Item "Env:$v" -ErrorAction SilentlyContinue
  }
  python $Pytmux start-server | Out-Null
  Start-Sleep -Milliseconds 900

  $launch = powershell -File scripts\launch_console_window.ps1 -KeepOpen `
    -Command "python `"$((Resolve-Path $Pytmux).Path)`" attach"
  if ($launch -notmatch 'hwnd=(\d+) pid=(\d+)') { "  !! $name 창을 못 잡았다: $launch"; continue }
  $hwnd = $Matches[1]; $cmdPid = [int]$Matches[2]
  Start-Sleep -Seconds 6      # Textual 이 뜨고 첫 프레임·셸 프롬프트가 찰 때까지
  # ★ 키를 넣기 전에 입력기를 영문으로. 안 하면 글자키가 한글로 바뀌어 도착한다.
  [void][ImeCtl]::English([IntPtr][long]$hwnd)   # $hwnd 는 정규식에서 온 **문자열**이다

  foreach ($keys in $scenes[$name]) {
    # `~N` 은 **N초 기다린다**는 뜻이다(키가 아니다). Claude Code 처럼 뜨는 데 오래
    # 걸리는 것을 패널 안에서 돌릴 때 필요하다 — 800ms 간격으로는 첫 화면도 못 본다.
    if ($keys -match '^~(\d+)$') { Start-Sleep -Seconds ([int]$Matches[1]); continue }
    # `>글자` 는 **패널에 글자 그대로**(IME 우회, 끝에 Enter). 셸 명령을 칠 때 쓴다 —
    # SendKeys 로 치면 한글 입력기가 가로채 `claude` 가 `치명ㄷ` 이 된다.
    if ($keys.StartsWith('>')) {
      powershell -File scripts\send_keys_to_window.ps1 -Hwnd $hwnd -Text ($keys.Substring(1) + "`n") -SettleMs 400 | Out-Null
      Start-Sleep -Milliseconds 800
      continue
    }
    # ★ **매 전송 직전에** 영문으로 되돌린다. 한 번만 세우면 창이 포커스를 받을 때
    #   입력기가 다시 한글로 돌아가 그 다음 키가 자모로 도착한다(실측: `"` 가 패널에
    #   `ㄴㄷㅅ…` 으로 들어갔다).
    [void][ImeCtl]::English([IntPtr][long]$hwnd)
    powershell -File scripts\send_keys_to_window.ps1 -Hwnd $hwnd -Keys $keys -ImeEnglish -SettleMs 400 | Out-Null
    Start-Sleep -Milliseconds 800
  }
  Start-Sleep -Milliseconds 900

  $raw = Join-Path $env:TEMP "canon-$name-raw.png"
  $r = powershell -File scripts\capture_window.ps1 -Hwnd $hwnd -Out $raw -SettleMs 500
  if (Test-Path $raw) {
    # 터미널 크롬(탭 띠·창 단추)을 잘라낸다 — pytmux 화면만 남긴다.
    $src = [System.Drawing.Bitmap]::new((Resolve-Path $raw).Path)
    $h = $src.Height - $ChromePx
    $dst = New-Object System.Drawing.Bitmap $src.Width, $h
    $g = [System.Drawing.Graphics]::FromImage($dst)
    $g.DrawImage($src, (New-Object System.Drawing.Rectangle 0, 0, $src.Width, $h),
                 (New-Object System.Drawing.Rectangle 0, $ChromePx, $src.Width, $h),
                 [System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose(); $src.Dispose()
    $out = Join-Path $OutDir "$Prefix$name-py.png"
    $dst.Save($out, [System.Drawing.Imaging.ImageFormat]::Png); $dst.Dispose()
    Remove-Item $raw -Force
    "  $name -> $out"
  } else {
    "  !! $name 캡처 실패: $r"
  }
  Stop-Tree $cmdPid
  Start-Sleep -Milliseconds 400
}
