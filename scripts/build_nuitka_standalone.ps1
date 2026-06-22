param(
  [switch]$InstallDeps,
  [switch]$Clean,
  [switch]$SkipSmokeTests,
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$webDir = Join-Path $rootDir "web"
$nuitkaOutputDir = Join-Path $rootDir "build\nuitka"
$distDir = Join-Path $nuitkaOutputDir "no1.frozen_entry.dist"
$exePath = Join-Path $distDir "onecolleague.exe"
$webDistDir = Join-Path $rootDir "src\no1\ports\web\dist"
$resourcesDir = Join-Path $rootDir "src\no1\resources"
$smokeHome = $null

function Resolve-Tool {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Name,
    [Parameter(Mandatory = $true)]
    [string]$InstallHint
  )

  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  throw $InstallHint
}

function Invoke-CheckedNative {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [string[]]$ArgumentList = @(),
    [string]$WorkingDirectory = ""
  )

  $previousLocation = Get-Location
  if ($WorkingDirectory) {
    Push-Location $WorkingDirectory
  }
  try {
    & $FilePath @ArgumentList | Out-Host
    $exitCode = $LASTEXITCODE
    if ($null -ne $exitCode -and $exitCode -ne 0) {
      throw "$FilePath failed with exit code $exitCode"
    }
  }
  finally {
    if ($WorkingDirectory) {
      Pop-Location
    }
    Set-Location $previousLocation
  }
}

function Test-PathExists {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$Message
  )

  if (-not (Test-Path -LiteralPath $Path)) {
    throw $Message
  }
}

function Resolve-ProjectPython {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PythonSelector
  )

  $venvPython = Join-Path $rootDir ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }

  $uvPath = Resolve-Tool -Name "uv" -InstallHint "Missing uv. Install uv first."
  Invoke-CheckedNative -FilePath $uvPath -ArgumentList @("sync", "--dev") -WorkingDirectory $rootDir
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }

  $cmd = Get-Command $PythonSelector -ErrorAction SilentlyContinue
  if ($cmd) {
    return $cmd.Source
  }
  throw "Missing Python command '$PythonSelector'. Run uv sync --dev first or pass -Python <path>."
}

function Invoke-OneColleagueSmoke {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath
  )

  $script:smokeHome = Join-Path ([System.IO.Path]::GetTempPath()) ("onecolleague-nuitka-smoke-" + [System.Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Force -Path $script:smokeHome | Out-Null

  $oldOneColleagueHome = $env:ONECOLLEAGUE_HOME
  $oldCcccHome = $env:CCCC_HOME
  try {
    $env:ONECOLLEAGUE_HOME = $script:smokeHome
    $env:CCCC_HOME = $script:smokeHome

    Invoke-CheckedNative -FilePath $ExePath -ArgumentList @("version") -WorkingDirectory $rootDir
    Invoke-CheckedNative -FilePath $ExePath -ArgumentList @("doctor") -WorkingDirectory $rootDir
    Invoke-CheckedNative -FilePath $ExePath -ArgumentList @("daemon", "start") -WorkingDirectory $rootDir
    Invoke-CheckedNative -FilePath $ExePath -ArgumentList @("daemon", "status") -WorkingDirectory $rootDir
  }
  finally {
    try {
      if (Test-Path -LiteralPath $ExePath) {
        & $ExePath daemon stop | Out-Host
        $global:LASTEXITCODE = 0
      }
    }
    catch {
      Write-Warning "failed to stop smoke-test daemon: $($_.Exception.Message)"
    }

    if ($null -eq $oldOneColleagueHome) {
      Remove-Item Env:\ONECOLLEAGUE_HOME -ErrorAction SilentlyContinue
    }
    else {
      $env:ONECOLLEAGUE_HOME = $oldOneColleagueHome
    }
    if ($null -eq $oldCcccHome) {
      Remove-Item Env:\CCCC_HOME -ErrorAction SilentlyContinue
    }
    else {
      $env:CCCC_HOME = $oldCcccHome
    }

    if ($script:smokeHome -and (Test-Path -LiteralPath $script:smokeHome)) {
      Remove-Item -LiteralPath $script:smokeHome -Recurse -Force -ErrorAction SilentlyContinue
    }
    $script:smokeHome = $null
  }
}

$isWindowsPlatform = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
if (-not $isWindowsPlatform) {
  throw "Nuitka standalone packaging script currently supports Windows only."
}

$uvPath = Resolve-Tool -Name "uv" -InstallHint "Missing uv. Install uv first."
$npmPath = Resolve-Tool -Name "npm" -InstallHint "Missing npm. Install Node.js first."

if ($Clean -and (Test-Path -LiteralPath $nuitkaOutputDir)) {
  $resolvedOutput = [System.IO.Path]::GetFullPath($nuitkaOutputDir)
  $resolvedBuild = [System.IO.Path]::GetFullPath((Join-Path $rootDir "build")).TrimEnd('\')
  if (-not $resolvedOutput.StartsWith($resolvedBuild + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "refusing to clean output outside repository build directory: $resolvedOutput"
  }
  Write-Host "==> Cleaning $nuitkaOutputDir"
  Remove-Item -LiteralPath $nuitkaOutputDir -Recurse -Force
}

if ($InstallDeps) {
  Write-Host "==> Sync Python dependencies"
  Invoke-CheckedNative -FilePath $uvPath -ArgumentList @("sync") -WorkingDirectory $rootDir

  Write-Host "==> Install frontend dependencies"
  Invoke-CheckedNative -FilePath $npmPath -ArgumentList @("ci", "--prefix", $webDir) -WorkingDirectory $rootDir
}

$pythonPath = Resolve-ProjectPython -PythonSelector $Python

Write-Host "==> Check Python and Nuitka"
Invoke-CheckedNative -FilePath $pythonPath -ArgumentList @("-c", "import sys; from importlib.metadata import version; print(sys.version); print('Nuitka', version('Nuitka'))") -WorkingDirectory $rootDir

Write-Host "==> Build bundled Web UI"
Invoke-CheckedNative -FilePath $npmPath -ArgumentList @("-C", $webDir, "run", "build") -WorkingDirectory $rootDir
Test-PathExists -Path (Join-Path $webDistDir "index.html") -Message "Web build failed, missing src\no1\ports\web\dist\index.html"

Test-PathExists -Path $resourcesDir -Message "Missing src\no1\resources"

Write-Host "==> Build Nuitka standalone distribution"
$nuitkaArgs = @(
  "-m", "nuitka",
  "--standalone",
  "--assume-yes-for-downloads",
  "--output-dir=build\nuitka",
  "--output-filename=onecolleague.exe",
  "--output-folder-name=no1.frozen_entry.dist",
  "--include-package=no1",
  "--include-package-data=no1",
  "--include-data-dir=src\no1\ports\web\dist=no1\ports\web\dist",
  "--include-data-dir=src\no1\resources=no1\resources",
  "--nofollow-import-to=lark_oapi.*",
  "--nofollow-import-to=dingtalk_stream.*",
  "--nofollow-import-to=wechatbot_sdk.*",
  "--main=src\no1\frozen_entry.py"
)
Invoke-CheckedNative -FilePath $pythonPath -ArgumentList $nuitkaArgs -WorkingDirectory $rootDir

Write-Host "==> Validate Nuitka distribution"
Test-PathExists -Path $exePath -Message "Nuitka build did not produce $exePath"
Test-PathExists -Path (Join-Path $distDir "no1\ports\web\dist\index.html") -Message "Nuitka dist is missing bundled Web UI"
Test-PathExists -Path (Join-Path $distDir "no1\resources\onecolleague-help.md") -Message "Nuitka dist is missing no1 resources"

if (-not $SkipSmokeTests) {
  Write-Host "==> Run packaged smoke tests with temporary CCCC_HOME"
  Invoke-OneColleagueSmoke -ExePath $exePath
}

Write-Host ""
Write-Host "OK: Nuitka standalone distribution ready:"
Write-Host "  $distDir"
Write-Host ""
Write-Host "Local verification commands:"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe version"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe doctor"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe daemon start"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe daemon status"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe daemon stop"
