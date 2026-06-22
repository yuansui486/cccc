param(
  [ValidateSet("Wheel", "Editable", "Both")]
  [string]$Mode = "Both",
  [string]$Python = "python",
  [string]$WheelPath = "",
  [switch]$BuildWheel
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$createdPaths = @()
$uvPath = $null

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

function Get-VenvPython {
  param(
    [Parameter(Mandatory = $true)]
    [string]$VenvPath
  )

  $windowsPython = Join-Path $VenvPath "Scripts\python.exe"
  if (Test-Path -LiteralPath $windowsPython) {
    return $windowsPython
  }
  return Join-Path $VenvPath "bin\python"
}

function Get-VenvOneColleague {
  param(
    [Parameter(Mandatory = $true)]
    [string]$VenvPath
  )

  $windowsExe = Join-Path $VenvPath "Scripts\onecolleague.exe"
  if (Test-Path -LiteralPath $windowsExe) {
    return $windowsExe
  }
  return Join-Path $VenvPath "bin\onecolleague"
}

function Resolve-WheelPath {
  if ($WheelPath) {
    $resolved = [System.IO.Path]::GetFullPath($WheelPath)
    if (-not (Test-Path -LiteralPath $resolved)) {
      throw "wheel not found: $resolved"
    }
    return $resolved
  }

  $latestWheel = Get-ChildItem -Path (Join-Path $rootDir "dist") -Filter "no1-*.whl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($null -eq $latestWheel) {
    throw "No no1 wheel found in dist. Run scripts\build_package.ps1 first or pass -BuildWheel."
  }
  return $latestWheel.FullName
}

function Invoke-InstallSmoke {
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Wheel", "Editable")]
    [string]$InstallMode,
    [Parameter(Mandatory = $true)]
    [string]$PythonSelector
  )

  $venvRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("onecolleague-install-smoke-" + $InstallMode.ToLowerInvariant() + "-" + [System.Guid]::NewGuid().ToString("N"))
  $homeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("onecolleague-home-smoke-" + $InstallMode.ToLowerInvariant() + "-" + [System.Guid]::NewGuid().ToString("N"))
  $script:createdPaths += $venvRoot
  $script:createdPaths += $homeRoot

  Write-Host "==> Create $InstallMode verification venv"
  Invoke-CheckedNative -FilePath $script:uvPath -ArgumentList @("venv", $venvRoot, "--python", $PythonSelector) -WorkingDirectory $rootDir
  $venvPython = Get-VenvPython -VenvPath $venvRoot

  if ($InstallMode -eq "Wheel") {
    $resolvedWheel = Resolve-WheelPath
    Write-Host "==> Install wheel $resolvedWheel"
    Invoke-CheckedNative -FilePath $script:uvPath -ArgumentList @("pip", "install", "--python", $venvPython, $resolvedWheel) -WorkingDirectory $rootDir
  }
  else {
    Write-Host "==> Install editable source"
    Invoke-CheckedNative -FilePath $script:uvPath -ArgumentList @("pip", "install", "--python", $venvPython, "-e", $rootDir) -WorkingDirectory $rootDir
  }

  $exePath = Get-VenvOneColleague -VenvPath $venvRoot
  if (-not (Test-Path -LiteralPath $exePath)) {
    throw "onecolleague entrypoint not found after $InstallMode install: $exePath"
  }

  New-Item -ItemType Directory -Force -Path $homeRoot | Out-Null
  $oldOneColleagueHome = $env:ONECOLLEAGUE_HOME
  $oldCcccHome = $env:CCCC_HOME
  try {
    $env:ONECOLLEAGUE_HOME = $homeRoot
    $env:CCCC_HOME = $homeRoot

    Write-Host "==> Run $InstallMode CLI smoke tests"
    Invoke-CheckedNative -FilePath $exePath -ArgumentList @("version") -WorkingDirectory $rootDir
    Invoke-CheckedNative -FilePath $exePath -ArgumentList @("doctor") -WorkingDirectory $rootDir
    Invoke-CheckedNative -FilePath $exePath -ArgumentList @("daemon", "start") -WorkingDirectory $rootDir
    Invoke-CheckedNative -FilePath $exePath -ArgumentList @("daemon", "status") -WorkingDirectory $rootDir
  }
  finally {
    try {
      if (Test-Path -LiteralPath $exePath) {
        & $exePath daemon stop | Out-Host
        $global:LASTEXITCODE = 0
      }
    }
    catch {
      Write-Warning "failed to stop $InstallMode smoke-test daemon: $($_.Exception.Message)"
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
  }
}

$script:uvPath = Resolve-Tool -Name "uv" -InstallHint "Missing uv. Install uv first."

try {
  if ($BuildWheel) {
    & (Join-Path $rootDir "scripts\build_package.ps1") -Python $Python | Out-Host
    $exitCode = $LASTEXITCODE
    if ($null -ne $exitCode -and $exitCode -ne 0) {
      throw "build_package.ps1 failed with exit code $exitCode"
    }
  }

  if ($Mode -in @("Wheel", "Both")) {
    Invoke-InstallSmoke -InstallMode "Wheel" -PythonSelector $Python
  }
  if ($Mode -in @("Editable", "Both")) {
    Invoke-InstallSmoke -InstallMode "Editable" -PythonSelector $Python
  }
}
finally {
  foreach ($path in $script:createdPaths) {
    if ($path -and (Test-Path -LiteralPath $path)) {
      Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    }
  }
}

Write-Host "OK: install smoke tests passed for mode $Mode"
