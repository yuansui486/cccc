param(
  [switch]$InstallDeps,
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$rootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$verifyVenv = $null

function Test-WindowsPtyWheel {
  param(
    [Parameter(Mandatory = $true)]
    [string]$WheelPath
  )

  $isWindowsPlatform = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
  if (-not $isWindowsPlatform) {
    return
  }

  $verifyRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("onecolleague-wheel-verify-" + [System.Guid]::NewGuid().ToString("N"))
  $script:verifyVenv = $verifyRoot
  try {
    & $Python -m venv $verifyRoot | Out-Host
    $venvPython = Join-Path $verifyRoot "Scripts\python.exe"
    & $venvPython -m ensurepip --upgrade | Out-Host
    & $venvPython -m pip install -U pip | Out-Host
    & $venvPython -m pip install $WheelPath | Out-Host
    & $venvPython -c "import json, winpty; from no1.runners.platform_support import pty_support_details; details = pty_support_details(); print(json.dumps(details, ensure_ascii=False)); raise SystemExit(0 if details.get('supported') else 1)" | Out-Host
  }
  finally {
    if ($script:verifyVenv -and (Test-Path $script:verifyVenv)) {
      Remove-Item -LiteralPath $script:verifyVenv -Recurse -Force -ErrorAction SilentlyContinue
    }
    $script:verifyVenv = $null
  }
}

& (Join-Path $rootDir "scripts\build_web.ps1") -InstallDeps:$InstallDeps

& $Python -m pip install -U pip build twine | Out-Host
& $Python -m compileall -q (Join-Path $rootDir "src\no1") | Out-Host
& $Python -m build $rootDir | Out-Host
& $Python -m twine check (Join-Path $rootDir "dist\*") | Out-Host

$latestWheel = Get-ChildItem -Path (Join-Path $rootDir "dist") -Filter "no1-*.whl" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if ($null -eq $latestWheel) {
  throw "No no1 wheel found in dist"
}
Test-WindowsPtyWheel -WheelPath $latestWheel.FullName

Write-Host "OK: 已构建 dist/*，并打包 bundled Web UI"
