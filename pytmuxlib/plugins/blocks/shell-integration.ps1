# pytmux 셸 통합(PowerShell) — 명령 경계를 서버에 알린다.
#
# `shell-integration.sh` 의 PowerShell 판이다. **같은 OSC 규약**을 쓰므로 서버 쪽
# (`segment.py`)에는 셸별 분기가 없다 — 셸이 늘어도 서버는 그대로다.
#
# 설치 — **저장소 안의 이 파일을 직접 가리킨다.** 설치 스크립트가 셸 통합을 어디로
# 복사해 주지는 않는다(`.sh` 쪽도 마찬가지다 — 그 파일 머리말의 `~/.pytmux/…` 는 아직
# 그렇게 배포되지 않는다). 저장소를 옮기면 이 줄도 고쳐야 한다.
#
#     '. "<저장소>\pytmuxlib\plugins\blocks\shell-integration.ps1"' | Add-Content $PROFILE
#
# pytmux 밖에서 열린 PowerShell 에서도 무해하다 — OSC 를 이해 못 하는 터미널은 그냥
# 무시하고, 이해하는 터미널(WT·VSCode)은 자기 블록 기능에 쓴다.
#
# # 왜 별도 파일인가
#
# PowerShell 에는 `precmd`/`preexec` 이 없다. 대신 두 훅으로 같은 자리를 만든다:
#
#   `prompt`                 — 프롬프트 직전(= sh 의 precmd). 끝난 명령의 D 와 cwd·A 를 낸다.
#   `PSConsoleHostReadLine`  — 호스트가 한 줄을 읽는 지점(= sh 의 preexec). **사용자가 친
#                              줄 그대로**를 돌려주므로 화면에서 긁을 필요가 없다(zsh 의
#                              `$1` 과 같은 품질이고 bash 의 DEBUG 트랩보다 정확하다).
#
# `133;B`(명령 입력 시작)는 **안 보낸다** — `.sh` 도 안 보내고 서버도 요구하지 않는다.
# 프롬프트 문자열 끝에 넣으면 PSReadLine 의 프롬프트 폭 계산에 보이지 않는 바이트를
# 끼워 넣게 되므로, 얻는 것 없이 재그리기만 흔든다.
#
# # 종료코드
#
# `$?` 가 참이면 0, 거짓이면 `$LASTEXITCODE`(0 이면 1)다. 이 박스에서 실측한 의미론:
#
#     cmd /c "exit 3"   → $? = False, $LASTEXITCODE = 3
#     cmd /c "exit 0"   → $? = True,  $LASTEXITCODE = 0
#     실패한 cmdlet     → $? = False, $LASTEXITCODE = 직전 값 그대로(갱신 안 됨)
#
# 마지막 줄이 이 규칙의 한계다: 네이티브 명령 뒤에 cmdlet 이 실패하면 **묵은
# `$LASTEXITCODE`** 를 보고할 수 있다. PowerShell 이 cmdlet 실패를 정수로 주지 않아
# 근본적으로 못 가른다 — 참/거짓은 항상 맞고 숫자만 근사다. 안 맞느니 근사가 낫다
# (블록이 성공/실패로 갈리는 것이 사용자가 실제로 보는 것이다).

# 두 번 걸리면 블록이 두 번씩 생긴다. 막되 **이 세션 안에서만** 막는다 —
# `$env:` 로 두면 자식 프로세스가 물려받아, 패널 안에서 연 중첩 PowerShell 이
# 통합을 건너뛴다(`.sh` 쪽 가드가 export 안 된 셸 변수인 것과 같은 이유).
if ($global:__pytmux_shell_integration) { return }
$global:__pytmux_shell_integration = $true

# ESC ] <payload> ESC \ — `.sh` 의 __pytmux_osc 와 같은 형식(BEL 이 아니라 ST).
function global:__pytmux_osc([string]$payload) {
    [Console]::Write("$([char]27)]$payload$([char]27)\")
}

# 명령줄을 OSC 필드로 안전하게 만든다. **백슬래시를 먼저** 늘린다 — 뒤 치환이 만든
# 백슬래시와 섞이면 서버의 `_unescape` 가 다른 글자를 복원한다.
# Windows 경로에 백슬래시가 흔해 `.sh` 보다 이 경로가 훨씬 자주 밟힌다.
function global:__pytmux_escape([string]$s) {
    if ($null -eq $s) { return '' }
    $s = $s.Replace('\', '\\')
    $s = $s.Replace(';', '\x3b')
    $s = $s.Replace("`n", '\x0a')
    $s = $s.Replace("`r", '\x0d')
    $s = $s.Replace([string][char]27, '\x1b')
    # BEL 도 **OSC 종결자**다(파서는 BEL / C1 ST / ESC \ 셋을 받는다). 안 바꾸면 명령줄에
    # 든 BEL 하나가 문자열을 여기서 끊어 블록의 명령이 잘리고 남은 글자가 화면에 쏟아진다
    # (검수 2026-07-30 — `.sh` 와 같은 수정. 머리말의 "제어문자는 escape 한다" 계약).
    $s = $s.Replace([string][char]7, '\x07')
    # 서버도 자르지만(MAX_CMD_LEN) 긴 붙여넣기를 파이프에 흘리지 않는다.
    if ($s.Length -gt 1024) { $s = $s.Substring(0, 1024) }
    return $s
}

# cwd 를 file:// URL 로. `D:\a\b` → `file:///D:/a/b` 가 규격이고, 서버가 앞의 `/` 를
# 뗀다(`_parse_file_url`). PowerShell 의 위치는 파일시스템이 아닐 수 있으므로
# (`Cert:\`·`HKLM:\`) 파일시스템일 때만 보낸다 — 아니면 cwd 가 경로가 아닌 것이 된다.
# URL 필드에 실을 글에서 **OSC 종결자를 퍼센트 인코딩**한다(검수 2026-08-05 · `.sh` 와
# 같은 수정). 명령줄 쪽(`__pytmux_escape`)에만 있던 규율이 cwd 에는 없어서, BEL·ESC 가
# 든 디렉터리 이름 하나가 OSC 7 을 끊고 남은 바이트를 화면으로 흘렸다. `\xHH` 가 아니라
# 퍼센트인 이유는 서버가 이 필드를 `unquote` 하기 때문이다(`.sh` 주석 참조).
function global:__pytmux_urlsafe([string]$s) {
    if ($null -eq $s) { return '' }
    $s = $s.Replace([string][char]27, '%1B')
    $s = $s.Replace([string][char]7, '%07')
    $s = $s.Replace("`r", '%0D')
    $s = $s.Replace("`n", '%0A')
    return $s
}

function global:__pytmux_report_cwd() {
    $loc = Get-Location
    if ($loc.Provider.Name -ne 'FileSystem') { return }
    $p = $loc.ProviderPath.Replace('\', '/')
    # pwsh 는 Linux/macOS 에도 있고 거기서는 ProviderPath 가 이미 `/` 로 시작한다 —
    # 그때도 `/` 를 덧붙이면 `file://host//home/me` 가 되어 파서에 `//home/me` 가 남고
    # (`_parse_file_url` 은 드라이브 경로의 `/` 만 뗀다) `.sh` 판과 cwd 가 어긋난다.
    if (-not $p.StartsWith('/')) { $p = "/$p" }
    $host_ = $env:COMPUTERNAME
    if (-not $host_) { $host_ = 'localhost' }
    __pytmux_osc ("7;file://" + (__pytmux_urlsafe ($host_ + $p)))
}

function global:__pytmux_report_cmd([string]$line) {
    __pytmux_osc ("633;E;" + (__pytmux_escape $line))
}

# ── 프롬프트 훅(= precmd) ────────────────────────────────────────────────────
# 기존 `prompt` 를 지우지 않는다 — 사용자가 쓰던 것이 있을 수 있다(oh-my-posh 등).
if (-not (Test-Path function:global:__pytmux_inner_prompt)) {
    $existing = Get-Item function:global:prompt -ErrorAction SilentlyContinue
    if ($existing) {
        Set-Item function:global:__pytmux_inner_prompt $existing.ScriptBlock
    } else {
        function global:__pytmux_inner_prompt { "PS $(Get-Location)> " }
    }
}

function global:prompt {
    # **이 두 줄이 맨 앞이어야 한다** — 아래 어떤 문장이든 `$?` 를 덮어쓴다.
    $ok = $global:?
    $native = $global:LASTEXITCODE

    if ($global:__pytmux_running) {
        $code = if ($ok) { 0 } elseif ($native -is [int] -and $native -ne 0) { $native } else { 1 }
        __pytmux_osc "133;D;$code"
        $global:__pytmux_running = $false
    }
    __pytmux_report_cwd
    __pytmux_osc "133;A"

    # 원래 프롬프트를 그대로 돌려준다. OSC 는 위에서 이미 콘솔로 나갔으므로
    # 반환 문자열에는 보이지 않는 바이트가 없다(PSReadLine 폭 계산 무손상).
    __pytmux_inner_prompt
}

# ── 입력 훅(= preexec) ───────────────────────────────────────────────────────
# 호스트가 한 줄을 읽는 지점을 감싼다. PSReadLine 이 있으면 그것이 이 함수를 정의해
# 두므로 **기존 구현을 보관하고 감싼다** — 덮어쓰면 PSReadLine 이 통째로 꺼져
# 편집·히스토리가 사라진다(사용자가 즉시 알아채는 열화다).
if (-not (Test-Path function:global:__pytmux_inner_readline)) {
    $existingRead = Get-Item function:global:PSConsoleHostReadLine -ErrorAction SilentlyContinue
    if ($existingRead) {
        Set-Item function:global:__pytmux_inner_readline $existingRead.ScriptBlock
    } elseif (Get-Module PSReadLine) {
        function global:__pytmux_inner_readline {
            [Microsoft.PowerShell.PSConsoleReadLine]::ReadLine($host.Runspace, $ExecutionContext)
        }
    } else {
        # PSReadLine 이 없는 호스트(-NoProfile 최소 구성 등). 감쌀 것이 없으면
        # 이 훅을 걸지 않는다 — 직접 읽으면 우리가 라인 에디터가 되어 버린다.
        $global:__pytmux_no_readline = $true
    }
}

if (-not $global:__pytmux_no_readline) {
    function global:PSConsoleHostReadLine {
        $line = __pytmux_inner_readline
        if ($line) {
            __pytmux_report_cmd $line
            __pytmux_osc "133;C"
            $global:__pytmux_running = $true
        }
        $line
    }
}
