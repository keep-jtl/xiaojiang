# XiaoLiCaiTong v0.1.0-preview Android debug build
# Requires Node >=22, Android SDK 36 + Build Tools 36, JDK 21+.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $Root
$env:JAVA_HOME = "D:\DevTools\jdk21\jdk-21.0.2"
$env:ANDROID_HOME = "D:\Android\Sdk"
$env:ANDROID_SDK_ROOT = "D:\Android\Sdk"
$env:GRADLE_USER_HOME = "D:\gradle-home"
# 构建渠道标记：前端据此显示「开发测试版」角标，正式构建不得设置该变量
$env:XTLC_BUILD_CHANNEL = "development-debug"

npm run build
npx cap sync android
Push-Location android
try {
  & .\gradlew.bat assembleDebug --no-daemon
  if ($LASTEXITCODE -ne 0) { throw "Gradle assembleDebug failed: $LASTEXITCODE" }
} finally { Pop-Location }
Write-Host "APK: android\app\build\outputs\apk\debug\app-debug.apk"
