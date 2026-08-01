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
$py = if ($env:PYTHON) { $env:PYTHON } else { "python" }
& $py (Join-Path $repo "scripts\check_mirror.py") --scan $bin
if ($LASTEXITCODE -ne 0) {
    Write-Error "build_release: 갓 구운 이진에 이 상자의 경로가 남았다 — build\ 에 넣지 않는다. remap 이 안 먹었다(CARGO_HOME 확인)."
}

Copy-Item $bin (Join-Path $client "build\$out") -Force
$size = (Get-Item (Join-Path $client "build\$out")).Length
Write-Host "build_release: build\$out ($size bytes) — p4 edit/add 후 제출할 것"
