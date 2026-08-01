<#
.SYNOPSIS
  같은 장면의 두 컷(`<장면>-py.png` 정본 · `<장면>-gui.png` 우리 GUI)을 **한 장에 나란히**
  굽는다. 대조 문서(`docs/reports/*ui-parity*`)가 싣는 그림이다.

.DESCRIPTION
  두 컷은 창 크기가 다르다(정본은 콘솔 창, 우리는 1920x1200 GUI 창). **같은 높이로**
  맞춰 옆에 붙이고 위에 어느 쪽인지 라벨을 얹는다 — 픽셀이 아니라 배치를 비교하는
  그림이므로 높이 정렬이면 충분하다.

  짝이 없는 장면(한쪽만 있는 컷)은 **건너뛴다** — 반쪽짜리 그림을 대조표에 실으면
  "정본에 없다"로 오독된다.

.PARAMETER Dir
  두 컷이 있고 결과도 놓을 곳(기본 `docs/reports/images`).

.PARAMETER Height
  나란히 놓을 때의 공통 높이(픽셀).

.EXAMPLE
  powershell -File scripts/compose_side_by_side.ps1
#>
[CmdletBinding()]
param(
  [string]$Dir = "docs\reports\images",
  [string]$Prefix = "2026-07-30-cmp-",
  [int]$Height = 560
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$font = New-Object System.Drawing.Font("Segoe UI", 15, [System.Drawing.FontStyle]::Bold)
$pad = 12
$head = 34

foreach ($py in Get-ChildItem $Dir -Filter "$Prefix*-py.png") {
  $scene = ($py.Name -replace [regex]::Escape($Prefix), "") -replace "-py\.png$", ""
  $guiPath = Join-Path $Dir "$Prefix$scene-gui.png"
  if (-not (Test-Path $guiPath)) { "  건너뜀(짝 없음): $scene"; continue }

  $left = [System.Drawing.Image]::FromFile($py.FullName)
  $right = [System.Drawing.Image]::FromFile((Resolve-Path $guiPath).Path)
  $lw = [int]($left.Width * $Height / $left.Height)
  $rw = [int]($right.Width * $Height / $right.Height)

  $out = New-Object System.Drawing.Bitmap ($lw + $rw + $pad * 3), ($Height + $head + $pad * 2)
  $g = [System.Drawing.Graphics]::FromImage($out)
  $g.Clear([System.Drawing.Color]::FromArgb(16, 16, 22))
  $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
  $g.DrawString("pytmux 정본 (python Textual · 이 상자 실물)", $font, [System.Drawing.Brushes]::LightSkyBlue, $pad, 6)
  $g.DrawString("pytmux-gui GUI", $font, [System.Drawing.Brushes]::Gold, ($pad * 2 + $lw), 6)
  $g.DrawImage($left, (New-Object System.Drawing.Rectangle $pad, ($head + $pad), $lw, $Height))
  $g.DrawImage($right, (New-Object System.Drawing.Rectangle ($pad * 2 + $lw), ($head + $pad), $rw, $Height))
  $g.Dispose(); $left.Dispose(); $right.Dispose()

  $dst = Join-Path $Dir "$Prefix$scene.png"
  $out.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
  $out.Dispose()
  "  $scene -> $dst"
}
