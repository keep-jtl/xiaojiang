$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ReleaseDir = Join-Path $Root "release"
$Version = (Get-Content -LiteralPath (Join-Path $Root "VERSION") -Raw).Trim()
$Stage = Join-Path $ReleaseDir "XiaoLiCaiTong-Windows-v$Version"
$Zip = Join-Path $ReleaseDir "XiaoLiCaiTong-Windows-v$Version-portable.zip"
if (-not (Test-Path $ReleaseDir)) { New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null }
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item (Join-Path $Root "dist\XiaoLiCaiTong\*") $Stage -Recurse -Force
# 便携包不带任何本机数据库：首次启动时由程序在 data/ 下创建空库，避免把测试/真实持仓带出去。
$StageData = Join-Path $Stage "data"
if (Test-Path $StageData) { Remove-Item $StageData -Recurse -Force }
Copy-Item (Join-Path $Root "README.md") $Stage -Force
Copy-Item (Join-Path $Root "THIRD_PARTY_NOTICES.md") $Stage -Force
Copy-Item (Join-Path $Root "VERSION") $Stage -Force
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($Stage, $Zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
Write-Output ("ZIP: " + $Zip)
Get-Item $Zip | Select-Object Length, LastWriteTime | Format-List
Get-FileHash $Zip -Algorithm SHA256 | Format-List
