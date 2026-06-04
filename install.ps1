# pytmux 설치(Windows) — install.sh 의 PowerShell 대응판.
# 어디서든 `pytmux` 로 실행되도록 PATH 상의 디렉터리에 얇은 래퍼(pytmux.cmd)를
# 만든다. 래퍼는 이 저장소의 pytmux.py 절대경로를 가리키므로 저장소를 옮기지
# 않는 한 그대로 동작한다.
#
# 사용법(PowerShell):
#   .\install.ps1                 # 기본 위치($env:LOCALAPPDATA\pytmux\bin)에 설치
#   .\install.ps1 -Dir C:\bin     # 다른 디렉터리에 설치
#   .\install.ps1 -Bin pytmux2    # 다른 이름으로 설치
#
# 제거: .\uninstall.ps1 (같은 인자 규칙)
[CmdletBinding()]
param(
    [string]$Dir = (Join-Path $env:LOCALAPPDATA 'pytmux\bin'),
    [string]$Bin = 'pytmux'
)
$ErrorActionPreference = 'Stop'

# 이 스크립트(=저장소 루트)의 절대경로.
$Repo  = $PSScriptRoot
$Entry = Join-Path $Repo 'pytmux.py'
$Target = Join-Path $Dir ("{0}.cmd" -f $Bin)

if (-not (Test-Path -LiteralPath $Entry)) {
    Write-Error "오류: 진입점을 찾을 수 없습니다: $Entry"
    exit 1
}

# python 런처 탐색(python 우선, 없으면 py 런처).
$Py = 'python'
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Py = 'py'
    } else {
        Write-Warning "python(또는 py)을 PATH 에서 찾지 못했습니다. 설치는 계속하지만 실행 시 필요합니다."
    }
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null

# pytmux.cmd 래퍼 — 진입점으로 전달. %* 로 모든 인자 전달, @ 로 에코 억제.
$wrapper = @"
@echo off
rem pytmux 런처 — install.ps1 가 생성. 진입점: $Entry
$Py "$Entry" %*
"@
Set-Content -LiteralPath $Target -Value $wrapper -Encoding ASCII

Write-Host "설치 완료: $Target -> $Entry"

# DIR 이 PATH 에 없으면 안내.
$paths = ($env:PATH -split ';') | ForEach-Object { $_.TrimEnd('\') }
if ($paths -notcontains $Dir.TrimEnd('\')) {
    Write-Host ''
    Write-Host "주의: $Dir 가 PATH 에 없습니다. 사용자 PATH 에 추가하려면:"
    Write-Host "  setx PATH `"`$env:PATH;$Dir`""
    Write-Host "(새 터미널부터 적용됩니다.)"
}
