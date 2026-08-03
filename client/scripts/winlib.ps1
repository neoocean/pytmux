<#
.SYNOPSIS
  pid → 앱의 진짜 최상위 창. 라이브 하네스 여덟이 공유하는 한 벌.

.DESCRIPTION
  ⛔ **"pid 가 소유한 첫 번째 보이는 최상위 창"은 우리 창이 아니다.**

  `pytmux-gui` 프로세스는 최상위 창을 **여러 개** 갖는다. 실측(2026-08-03, pytmux-32):

      hwnd=51774978  1295x837  owner=0  class="Window Class"               title="pytmux"   ← 우리 창
      hwnd=12519852     15x15  owner=0  class="Winit Thread Event Target"  title=""         ← winit 의 숨은 창
      hwnd=6226208   1920x1025 owner=0  class="NVOpenGLPbuffer"            (visible=False)
      hwnd=75761948    132x37  owner=0  class="wgpu Device Class …"        (visible=False)

  `Winit Thread Event Target` 은 **보이고(visible=True) 소유자도 없다**(owner=0). 즉
  종전 술어 `pid == want && IsWindowVisible(h) && GetWindow(h, GW_OWNER) == 0` 을
  **그대로 만족한다.** EnumWindows 는 Z 순서로 돌기 때문에, 그 15×15 창이 우리 창보다
  위에 있는 순간 하네스는 **그것을 앱 창으로 집는다**. 그러면:

    · `SendInput` 은 성공을 찍고 **글자는 아무 데도 안 들어간다**
    · `SetForegroundWindow` 가 먹지 않아 전경이 **바탕화면(Program Manager)** 으로 간다
    · `GetWindowRect` 가 **15×15** 를 돌려준다

  이 셋이 pytmux-32 에 "최소화했다 복원하면 창이 15×15 로 남고 키를 하나도 안 받는다"로
  적힌 증상 전부다. **제품 결함이 아니었다** — 최소화하면 우리 창이 Z 순서에서 내려가
  숨은 창이 첫 자리로 올라오는 것뿐이고, 실제로 `ShowWindow(SW_RESTORE)` 도
  `WM_SYSCOMMAND/SC_RESTORE`(사람이 누르는 경로)도 창을 1295×837 로 **정상 복원한다**.
  같은 세션에서 둘 다 실측했다.

  ⚠ 그러니 이건 최소화 전용 함정이 **아니다** — Z 순서만 뒤집히면 언제든 성립하는
  **잠복 레이스**였고, 여덟 스크립트 중 **일곱**이 그 술어를 복붙하고 있었다
  (`capture_window.ps1` 만 크기 필터가 있어 오집 대신 "창을 못 찾았다"로 떨어졌다 —
  그 문구가 이번 오진의 출발점이었다).

  그래서 판정을 **한 벌로** 모으고, 크기가 아니라 **상태로** 가른다:

    · 최소화된 창은 `GetWindowRect` 가 158×26 같은 작업표시줄 크기를 준다 → 크기만으로
      거르면 **우리 창이 최소화된 순간 사라진다**. 그래서 `IsIconic` 이면 크기 필터를
      건너뛴다(그 창이 우리 창이다).
    · 숨은 이벤트 창은 최소화된 적이 없으므로 `IsIconic` 이 거짓이고 64×64 를 못 넘는다.

  클래스 이름으로 거르지 않는다 — winit 판이 바뀌면 이름도 바뀐다. 위 두 축은 창의
  **상태**라 그런 드리프트가 없다.

.EXAMPLE
  . "$PSScriptRoot\winlib.ps1"
  $hwnd = Get-AppWindow -ProcessId 1234
#>

Add-Type -Namespace PtWin -Name Api -UsingNamespace System.Collections.Generic -MemberDefinition @'
public delegate bool EnumProc(IntPtr h, IntPtr p);
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr p);
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
[DllImport("user32.dll")] public static extern int GetWindowTextLength(IntPtr h);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
[StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);

/// 창 하나의 판정 재료. 실패했을 때 **무엇을 보고 버렸는지**를 사람에게 보여주려고
/// 거르지 않은 후보까지 전부 담아 돌려준다.
public class Cand {
  public IntPtr Hwnd; public int W, H; public bool Visible, Iconic; public string Cls, Title;
  public bool IsApp;      // 앱의 진짜 창인가
  public string Why;      // 아니라면 왜
  public override string ToString() {
    return string.Format("hwnd={0} {1}x{2} visible={3} iconic={4} class=\"{5}\" title=\"{6}\"{7}",
      Hwnd, W, H, Visible, Iconic, Cls, Title, IsApp ? "  ← 앱 창" : "  (버림: " + Why + ")");
  }
}

/// 최소 변 길이. 이보다 작고 최소화도 아니면 앱 창이 아니다(winit 이벤트 타깃 = 15×15).
public const int MIN_EDGE = 64;

static string Text(IntPtr h) {
  var sb = new System.Text.StringBuilder(GetWindowTextLength(h) + 1);
  GetWindowText(h, sb, sb.Capacity);
  return sb.ToString();
}

static string Cls(IntPtr h) {
  var sb = new System.Text.StringBuilder(256);
  GetClassName(h, sb, sb.Capacity);
  return sb.ToString();
}

/// 그 pid 의 최상위 창 **전부**를 판정과 함께. 버린 것도 담는다(진단용).
public static List<Cand> Candidates(uint want) {
  var all = new List<Cand>();
  EnumWindows((h, p) => {
    uint pid; GetWindowThreadProcessId(h, out pid);
    if (pid != want) return true;
    if (GetWindow(h, 4 /*GW_OWNER*/) != IntPtr.Zero) return true;   // 툴팁·팝업·IME
    RECT r; GetWindowRect(h, out r);
    var c = new Cand {
      Hwnd = h, W = r.Right - r.Left, H = r.Bottom - r.Top,
      Visible = IsWindowVisible(h), Iconic = IsIconic(h), Cls = Cls(h), Title = Text(h),
    };
    if (!c.Visible) { c.Why = "안 보임"; }
    else if (c.Iconic) { c.IsApp = true; }        // 최소화 = 우리 창. 크기는 안 본다.
    else if (c.W <= MIN_EDGE || c.H <= MIN_EDGE) { c.Why = "너무 작다(숨은 이벤트 창)"; }
    else { c.IsApp = true; }
    all.Add(c);
    return true;
  }, IntPtr.Zero);
  return all;
}
'@

<#
.SYNOPSIS
  pid 의 앱 창 하나. 못 찾으면 **본 것을 전부 적어** 던진다.
.PARAMETER AllowIconic
  최소화된 창도 받아들일지($true 면 그대로 돌려준다). 기본은 복원하고 돌려준다.
#>
function Get-AppWindow {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [int]$TimeoutSec = 0,
    [switch]$AllowIconic
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  do {
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
      throw "pid $ProcessId 가 이미 죽었다 — 창이 뜨기 전에 종료됐다(로그를 볼 것)."
    }
    $cands = [PtWin.Api]::Candidates([uint32]$ProcessId)
    $apps = @($cands | Where-Object { $_.IsApp })
    if ($apps.Count -gt 0) {
      # 여러 개면 제목 있는 것을 먼저(우리 창은 "pytmux", 숨은 것들은 빈 제목).
      $titled = @($apps | Where-Object { $_.Title -ne '' })
      $pick = if ($titled.Count -gt 0) { $titled[0] } else { $apps[0] }
      if ($pick.Iconic -and -not $AllowIconic) {
        # 호출부가 곧바로 ShowWindow(SW_RESTORE) 를 부른다 — 여기서는 알리기만 한다.
        Write-Verbose "창이 최소화돼 있다(hwnd=$($pick.Hwnd)) — 호출부가 복원한다."
      }
      return $pick.Hwnd
    }
    if ($TimeoutSec -gt 0) { Start-Sleep -Milliseconds 200 }
  } while ((Get-Date) -lt $deadline)

  # ★ 여기서 "창을 못 찾았다"만 던지면 다음 세션이 **앱이 깨졌다**고 읽는다(pytmux-32 가
  #   정확히 그렇게 읽혔다). 무엇을 보고 무엇을 왜 버렸는지 전부 적는다.
  $seen = if ($cands.Count -eq 0) { '  (최상위 창이 하나도 없다)' }
          else { ($cands | ForEach-Object { '  ' + $_.ToString() }) -join "`n" }
  throw "pid $ProcessId 에 앱 창이 없다. 본 창:`n$seen"
}

<# 진단용 — 후보 전부를 사람이 읽는 줄로. #>
function Show-AppWindowCandidates {
  [CmdletBinding()]
  param([Parameter(Mandatory = $true)][int]$ProcessId)
  [PtWin.Api]::Candidates([uint32]$ProcessId) | ForEach-Object { $_.ToString() }
}
