param(
  [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$workspaceRoot = Split-Path -Parent $repoRoot
$appRoot = Join-Path $workspaceRoot "OneColleague_application"
$appResourcesDir = Join-Path $appRoot "src-tauri\resources"
$programData = if ($env:ProgramData) { $env:ProgramData } else { "C:\ProgramData" }
$bizVenvDir = Join-Path $programData "onecolleague\runtime\biz-venv"
$bizVenvPython = Join-Path $bizVenvDir "Scripts\python.exe"
$buildTmpRoot = $null
$defaultUvIndexUrl = "https://pypi.tuna.tsinghua.edu.cn/simple"

function Stop-OneColleagueProcesses {
  $processes = Get-Process -Name "onecolleague" -ErrorAction SilentlyContinue
  if (!$processes) {
    return
  }

  Write-Host "==> Stop running onecolleague.exe processes"
  foreach ($process in $processes) {
    Write-Host ("  stopping PID {0}" -f $process.Id)
    Stop-Process -Id $process.Id -Force -ErrorAction Stop
  }
}

function Remove-BuildTemp {
  if ($script:buildTmpRoot -and (Test-Path -LiteralPath $script:buildTmpRoot)) {
    Remove-Item -LiteralPath $script:buildTmpRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Resolve-Tool {
  param(
    [Parameter(Mandatory = $true)]
    [string[]]$Names,
    [Parameter(Mandatory = $true)]
    [string]$InstallHint
  )

  foreach ($name in $Names) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) {
      return $cmd.Source
    }
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

function Invoke-Robocopy {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Source,
    [Parameter(Mandatory = $true)]
    [string]$Destination
  )

  $excludeDirs = @(
    (Join-Path $Source ".git"),
    (Join-Path $Source ".venv"),
    (Join-Path $Source "dist"),
    (Join-Path $Source "node_modules"),
    (Join-Path $Source "web\node_modules"),
    "__pycache__"
  )

  & robocopy $Source $Destination /MIR /XD $excludeDirs /NFL /NDL /NJH /NJS /NP | Out-Host
  $exitCode = $LASTEXITCODE
  $global:LASTEXITCODE = 0
  if ($exitCode -ge 8) {
    throw "robocopy failed with exit code $exitCode"
  }
}

function Write-BizManifest {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$BizVersion,
    [Parameter(Mandatory = $true)]
    [string]$WhlFile
  )

  $json = @"
{
  "bizVersion": "$BizVersion",
  "whlFile": "$WhlFile"
}
"@
  Set-Content -LiteralPath $Path -Value $json -Encoding UTF8
}

function Test-WheelContainsWebDist {
  param(
    [Parameter(Mandatory = $true)]
    [string]$WheelPath
  )

  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $zip = [System.IO.Compression.ZipFile]::OpenRead($WheelPath)
  try {
    foreach ($entry in $zip.Entries) {
      if ($entry.FullName -eq "no1/ports/web/dist/index.html") {
        return
      }
    }
  }
  finally {
    $zip.Dispose()
  }
  throw "built wheel is missing bundled Web UI: no1/ports/web/dist/index.html"
}

function Get-BizVenvSitePackages {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
  )

  $probeScript = @"
import site

paths = site.getsitepackages()
print(paths[0] if paths else "")
"@
  $probePath = Join-Path $script:buildTmpRoot "probe-site-packages.py"
  Set-Content -LiteralPath $probePath -Value $probeScript -Encoding UTF8
  $output = & $PythonPath $probePath
  $exitCode = $LASTEXITCODE
  if ($null -ne $exitCode -and $exitCode -ne 0) {
    throw "failed to resolve biz venv site-packages with exit code $exitCode"
  }
  $sitePackages = ($output | Select-Object -First 1).ToString().Trim()
  if (-not $sitePackages) {
    throw "failed to resolve biz venv site-packages"
  }
  return $sitePackages
}

function Clear-InstalledNo1Package {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
  )

  $sitePackages = Join-Path $bizVenvDir "Lib\site-packages"
  if (-not (Test-Path -LiteralPath $sitePackages)) {
    $sitePackages = Get-BizVenvSitePackages -PythonPath $PythonPath
  }
  if (-not (Test-Path -LiteralPath $sitePackages)) {
    return
  }

  $resolvedSite = [System.IO.Path]::GetFullPath($sitePackages).TrimEnd('\')
  $targets = @()
  foreach ($pattern in @("no1", "no1-*.dist-info", "~*")) {
    $targets += Get-ChildItem -LiteralPath $sitePackages -Force -Filter $pattern -ErrorAction SilentlyContinue
  }
  $targets = $targets | Sort-Object FullName -Unique
  if ($targets) {
    Write-Host "  clearing stale package paths:"
    foreach ($target in $targets) {
      Write-Host "    $($target.FullName)"
    }
  }

  foreach ($target in $targets) {
    $resolvedTarget = [System.IO.Path]::GetFullPath($target.FullName)
    if (-not $resolvedTarget.StartsWith($resolvedSite + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "refusing to remove package path outside site-packages: $resolvedTarget"
    }

    try {
      Get-ChildItem -LiteralPath $resolvedTarget -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Attributes = $_.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly) }
      $target.Attributes = $target.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
      Remove-Item -LiteralPath $resolvedTarget -Recurse -Force -ErrorAction Stop
    }
    catch {
      throw "failed to remove stale package path $resolvedTarget. Close OneColleague and rerun from an Administrator PowerShell if Windows still reports access denied. $($_.Exception.Message)"
    }
  }
}

function Get-ProjectVersion {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PyprojectPath
  )

  foreach ($line in Get-Content -LiteralPath $PyprojectPath) {
    if ($line -match '^\s*version\s*=\s*"([^"]+)"') {
      return $Matches[1]
    }
  }
  throw "failed to read version from $PyprojectPath"
}

try {
  Stop-OneColleagueProcesses

  if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "web"))) {
    throw "web directory not found: $(Join-Path $repoRoot "web")"
  }
  if (-not (Test-Path -LiteralPath $appRoot)) {
    throw "application directory not found: $appRoot"
  }

  New-Item -ItemType Directory -Force -Path $appResourcesDir | Out-Null

  $existingWheel = Get-ChildItem -LiteralPath $appResourcesDir -Filter "no1-*.whl" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if ($existingWheel -and $existingWheel.Name -match '^no1-([^-]+)-') {
    Write-BizManifest -Path (Join-Path $appResourcesDir "biz-manifest.json") -BizVersion $Matches[1] -WhlFile $existingWheel.Name
  }

  Write-Host "==> Build bundled Web UI"
  $npm = Resolve-Tool -Names @("npm.cmd", "npm") -InstallHint "ERROR: npm is required to build the web UI."
  $webDir = Join-Path $repoRoot "web"
  if ($InstallDeps) {
    Invoke-CheckedNative -FilePath $npm -ArgumentList @("ci", "--prefix", $webDir)
  }
  Invoke-CheckedNative -FilePath $npm -ArgumentList @("-C", $webDir, "run", "build")
  $distIndex = Join-Path $repoRoot "src\no1\ports\web\dist\index.html"
  if (-not (Test-Path -LiteralPath $distIndex)) {
    throw "Web build failed, missing $distIndex"
  }

  Write-Host "==> Build Python package"
  $repoDist = Join-Path $repoRoot "dist"
  if (Test-Path -LiteralPath $repoDist) {
    Remove-Item -LiteralPath $repoDist -Recurse -Force
  }
  $script:buildTmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("onecolleague-build-" + [System.Guid]::NewGuid().ToString("N"))
  $buildRoot = Join-Path $script:buildTmpRoot "OneColleague"
  New-Item -ItemType Directory -Force -Path $buildRoot | Out-Null

  Invoke-Robocopy -Source $repoRoot -Destination $buildRoot

  Get-ChildItem -LiteralPath (Join-Path $buildRoot "src") -Filter "*.egg-info" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

  $uv = Resolve-Tool -Names @("uv.exe", "uv") -InstallHint "ERROR: uv is required to build the no1 package."
  $previousUvCacheDir = $env:UV_CACHE_DIR
  $previousUvIndexUrl = $env:UV_INDEX_URL
  $env:UV_CACHE_DIR = Join-Path $script:buildTmpRoot "uv-cache"
  if (!$env:UV_INDEX_URL) {
    $env:UV_INDEX_URL = $defaultUvIndexUrl
    Write-Host "  using UV_INDEX_URL=$env:UV_INDEX_URL"
  }
  try {
    Invoke-CheckedNative -FilePath $uv -ArgumentList @("build") -WorkingDirectory $buildRoot
  }
  finally {
    $env:UV_CACHE_DIR = $previousUvCacheDir
    $env:UV_INDEX_URL = $previousUvIndexUrl
  }

  New-Item -ItemType Directory -Force -Path $repoDist | Out-Null
  Copy-Item -LiteralPath (Get-ChildItem -LiteralPath (Join-Path $buildRoot "dist") -Filter "no1-*.whl").FullName -Destination $repoDist -Force

  $latestWheel = Get-ChildItem -LiteralPath $repoDist -Filter "no1-*.whl" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (-not $latestWheel) {
    throw "no no1 wheel found under $repoDist"
  }

  Test-WheelContainsWebDist -WheelPath $latestWheel.FullName

  $whlBasename = $latestWheel.Name
  $bizVersion = Get-ProjectVersion -PyprojectPath (Join-Path $repoRoot "pyproject.toml")

  Write-Host "==> Refresh application bundled resources"
  Get-ChildItem -LiteralPath $appResourcesDir -Filter "no1-*.whl" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne $whlBasename } |
    Remove-Item -Force
  Copy-Item -LiteralPath $latestWheel.FullName -Destination (Join-Path $appResourcesDir $whlBasename) -Force
  Write-BizManifest -Path (Join-Path $appResourcesDir "biz-manifest.json") -BizVersion $bizVersion -WhlFile $whlBasename

  Write-Host "==> Update installed application business package"
  if (Test-Path -LiteralPath $bizVenvPython) {
    Clear-InstalledNo1Package -PythonPath $bizVenvPython
    Invoke-CheckedNative -FilePath $bizVenvPython -ArgumentList @("-m", "pip", "install", "--no-deps", $latestWheel.FullName)
    $verifyScript = @'
import importlib.metadata as metadata
import pathlib
import shutil
import site

import no1

for site_dir in site.getsitepackages():
    stale = pathlib.Path(site_dir) / "cccc"
    if stale.exists():
        shutil.rmtree(stale)

dist = pathlib.Path(no1.__file__).resolve().parent / "ports" / "web" / "dist" / "index.html"
print(f"installed no1={metadata.version('no1')}")
print(f"no1={pathlib.Path(no1.__file__).resolve()}")
print(f"web_dist_index={dist}")
'@
    $verifyScriptPath = Join-Path $script:buildTmpRoot "verify-installed-no1.py"
    Set-Content -LiteralPath $verifyScriptPath -Value $verifyScript -Encoding UTF8
    Invoke-CheckedNative -FilePath $bizVenvPython -ArgumentList @($verifyScriptPath)
  }
  else {
    Write-Warning "application business venv not found; bundled wheel refreshed only: $bizVenvPython"
  }

  Write-Host "OK: web + application business package updated"
  Write-Host "  wheel: $whlBasename"
  Write-Host "  version: $bizVersion"
  Write-Host "  resources: $(Join-Path $appResourcesDir $whlBasename)"
  Write-Host "  installed: $bizVenvPython"
}
finally {
  Remove-BuildTemp
}
