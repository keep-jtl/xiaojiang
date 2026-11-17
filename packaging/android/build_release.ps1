# XiaoLiCaiTong v0.1.0-preview-r3 Android signed release build
# Requires android/keystore.properties (ignored by Git) and a keystore outside the repo.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$Properties = Join-Path $Root "android\keystore.properties"
if (-not (Test-Path $Properties)) {
  throw "Missing android/keystore.properties. Generate/hold the release keystore outside the repo first."
}
$env:JAVA_HOME = "D:\DevTools\jdk21\jdk-21.0.2"
$env:ANDROID_HOME = "D:\Android\Sdk"
$env:ANDROID_SDK_ROOT = "D:\Android\Sdk"
$env:GRADLE_USER_HOME = "D:\gradle-home"

npm run build
npx cap sync android
Push-Location android
try {
  & .\gradlew.bat assembleRelease --no-daemon
  if ($LASTEXITCODE -ne 0) { throw "Gradle assembleRelease failed: $LASTEXITCODE" }
} finally { Pop-Location }

$Source = Join-Path $Root "android\app\build\outputs\apk\release\app-release.apk"
if (-not (Test-Path $Source)) { throw "Release APK not found: $Source" }
$OutDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Out = Join-Path $OutDir "XiaoLiCaiTong-Android-v0.1.0-preview-r3.apk"
Copy-Item $Source $Out -Force
Write-Host ("APK: " + $Out)
Get-FileHash $Out -Algorithm SHA256 | Format-List
