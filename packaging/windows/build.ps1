# XiaoLiCaiTong v0.1.0-preview Windows portable build
# Usage: powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1
#        powershell -ExecutionPolicy Bypass -File packaging/windows/build.ps1 -Console
param(
  [switch]$Console
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Python venv not found: $Python" }

# Keep PyInstaller cache on D: (never C: user profile).
$env:PYINSTALLER_CONFIG_DIR = "D:\pyinstaller-cache"
if (-not (Test-Path $env:PYINSTALLER_CONFIG_DIR)) {
  New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null
}

if ($Console) { $env:XLTC_CONSOLE = "1" } else { $env:XLTC_CONSOLE = "0" }

Write-Host "=== clean build dirs ==="
if (Test-Path "build\windows\work") { Remove-Item "build\windows\work" -Recurse -Force }
if (Test-Path "dist\XiaoLiCaiTong") { Remove-Item "dist\XiaoLiCaiTong" -Recurse -Force }

Write-Host "=== PyInstaller onedir ==="
& $Python -m PyInstaller "packaging\windows\xltc.spec" --noconfirm --clean --workpath "build\windows\work" --distpath "dist"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$Exe = Join-Path $Root "dist\XiaoLiCaiTong\XiaoLiCaiTong.exe"
if (-not (Test-Path $Exe)) { throw "EXE not found: $Exe" }
Write-Host "=== built: $Exe ==="
Get-Item $Exe | Select-Object FullName,Length,LastWriteTime | Format-List
