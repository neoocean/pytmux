# pytmux 제거(Windows) — install.ps1 가 만든 래퍼(pytmux.cmd)를 지운다.
# uninstall.sh 의 PowerShell 대응판.
#
# 사용법(PowerShell):
#   .\uninstall.ps1                 # 기본 위치($env:LOCALAPPDATA\pytmux\bin)에서 제거
#   .\uninstall.ps1 -Dir C:\bin     # 해당 디렉터리에서 제거
#   .\uninstall.ps1 -Bin pytmux2    # 다른 이름으로 설치했을 때
[CmdletBinding()]
param(
    [string]$Dir = (Join-Path $env:LOCALAPPDATA 'pytmux\bin'),
    [string]$Bin = 'pytmux'
)
$ErrorActionPreference = 'Stop'

$Target = Join-Path $Dir ("{0}.cmd" -f $Bin)

if (Test-Path -LiteralPath $Target) {
    Remove-Item -LiteralPath $Target -Force
    Write-Host "제거 완료: $Target"
} else {
    Write-Host "이미 없습니다: $Target"
}
