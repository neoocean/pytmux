# 배포용 GUI 이진을 굽는다(Windows) — `build_release.sh` 의 짝. 근거는 그쪽 머리말에
# 다 적었다. 요지: rustc 가 박는 절대 소스경로에 이 상자의 계정·워크스페이스가 들어가고
# (실측 `C:\Users\<계정>\.cargo` 1013건 · `<드라이브>:\<depot 루트>\...` 21건 — 자리표시자로
# 적는 이유는 실제 문자열을 적으면 이 파일이 미러 게이트 ⑤ 에 걸리기 때문이다), `build/` 는 공개 git
# 미러로 함께 나가므로 되돌릴 수 없다. `--remap-path-prefix` 로 두 뿌리를 중립화한다.
#
# 크로스 컴파일이 안 되므로(GUI 가 창·GPU 백엔드를 실제로 링크한다) Windows 이진은
# 반드시 이 상자에서 이 스크립트로 구워야 한다.

$ErrorActionPreference = "Stop"

$client = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repo   = (Resolve-Path (Join-Path $client "..")).Path
Set-Location $client

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Write-Error "build_release: cargo 를 찾을 수 없다 (PATH 에 %USERPROFILE%\.cargo\bin 이 있는지 확인)"
}

# cargo 홈은 환경변수가 있으면 그것이 정본이다.
$cargoRoot = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE ".cargo" }

# macOS 쪽과 달리 Windows 는 `.cargo/config.toml` 에 target 별 rustflags 가 없다 —
# RUSTFLAGS 를 세워도 잃을 값이 없다. 생기면 sh 쪽처럼 여기에 다시 적고 가드를 달 것.
$env:RUSTFLAGS = "--remap-path-prefix=$repo=/pytmux --remap-path-prefix=$cargoRoot=/cargo"

$out = "pytmux-gui-windows-x64.exe"
Write-Host "build_release: $out"
Write-Host "  워크스페이스 $repo -> /pytmux"
Write-Host "  cargo 홈     $cargoRoot -> /cargo"

# 남은 인자는 cargo 로 넘긴다 — CI 는 `--locked` 를 붙인다(sh 쪽과 같다).
cargo build --release -p gui @args
if ($LASTEXITCODE -ne 0) { Write-Error "build_release: cargo build 실패" }

$bin = Join-Path $client "target\release\pytmux-gui.exe"
if (-not (Test-Path $bin)) { Write-Error "build_release: $bin 이 안 나왔다" }

# 굽자마자 미러 문턱과 **같은 자**로 잰다.
#
# ⛔ **"있나" 가 아니라 "도나" 로 고른다**(pytmux-383) — `python` 이 깔려 있지 않은
# 상자에서 그 이름은 Store 앱 실행 별칭이고, 그것은 스크립트를 안 돌린다. 별칭이
# 조용히 rc 0 으로 끝나는 판에서는 아래 둘이 **아무것도 안 재고** 통과해, 서드파티
# 고지를 안 재고 이진이 `build\` 로 들어간다.
# ★ 그리고 이것이 `build_release.sh` 와 **같은 차례**를 본다 — 종전에는 이쪽 기본값이
# `python`, 저쪽이 `python3` 이라 같은 일을 하는 두 짝이 서로 다른 자를 집었다.
# (정본 규칙은 `scripts\pick_python.sh` 다. PowerShell 은 sh 를 source 할 수 없어
#  모양만 옮겨 적는다 — 그 파일 머리말이 왜 그렇게 고르는지를 쥔다.)
function Get-Python3 {
    # ⚠ 이 파일은 `$ErrorActionPreference = "Stop"` 아래 돈다 — 그대로 두면 **후보가
    #   죽는 것 자체가** 스크립트를 끝낸다(별칭은 rc 49 로 죽고 stderr 도 쓴다. 게다가
    #   Windows PowerShell 5.1 은 네이티브 명령의 `2>` 를 그 설정에서 종료 오류로 만든다).
    #   고르는 동안만 되돌린다 — 여기서 후보가 죽는 것은 «답»이지 «사고»가 아니다.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        foreach ($cand in @($env:PYTMUX_PYTHON, $env:PYTHON, "python3", "python", "py")) {
            if (-not $cand) { continue }
            if (-not (Get-Command $cand -ErrorAction SilentlyContinue)) { continue }
            $major = $null
            try { $major = & $cand -c "import sys; print(sys.version_info[0])" 2>$null }
            catch { continue }
            if ($LASTEXITCODE -eq 0 -and "$major".Trim() -eq "3") { return $cand }
        }
    } finally {
        $ErrorActionPreference = $prev
    }
    return $null
}

$py = Get-Python3
if (-not $py) {
    Write-Error "build_release: 쓸 만한 파이썬 3 을 못 찾았다 — 경로 유출·서드파티 고지를 못 쟀다. PYTHON=<경로> 로 지정할 것."
}
& $py (Join-Path $repo "scripts\check_mirror.py") --scan $bin
if ($LASTEXITCODE -ne 0) {
    Write-Error "build_release: 갓 구운 이진에 이 상자의 경로가 남았다 — build\ 에 넣지 않는다. remap 이 안 먹었다(CARGO_HOME 확인)."
}

# 방금 링크된 서드파티 크레이트가 **전부** 저작권 고지 안에 있나(pytmux-193). 근거는
# `build_release.sh` 의 같은 자리에 적었다 — 요지: 이진만 받아 간 사람 손에 고지가
# 닿아야 하고, 여기서 재는 것은 「이 이진을 덮나」이지 「고지가 최신인가」가 아니다.
& $py (Join-Path $client "scripts\third_party_notices.py") --covers
if ($LASTEXITCODE -ne 0) {
    Write-Error "build_release: 이 이진의 서드파티 고지가 모자라다 — build\ 에 넣지 않는다."
}

Copy-Item $bin (Join-Path $client "build\$out") -Force
$size = (Get-Item (Join-Path $client "build\$out")).Length

# 고지를 이진 옆에 둔다(sh 쪽과 같다 — 정본은 client\THIRD-PARTY-NOTICES.md 한 벌).
# ⚠ 줄바꿈 이음(백틱)을 안 쓴다 — 뒤에 공백 하나만 붙어도 그 줄이 조용히 끊긴다.
$notices = Join-Path $client "THIRD-PARTY-NOTICES.md"
Copy-Item $notices (Join-Path $client "build\THIRD-PARTY-NOTICES.md") -Force

Write-Host "build_release: build\$out ($size bytes) — p4 edit/add 후 제출할 것"
Write-Host "build_release: build\THIRD-PARTY-NOTICES.md 도 함께 갱신했다 — 같이 제출할 것"
