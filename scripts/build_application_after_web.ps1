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
$bizVenvPython = Join-Path $programData "onecolleague\runtime\biz-venv\Scripts\python.exe"
$buildTmpRoot = $null

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
    & $npm ci --prefix $webDir | Out-Host
  }
  & $npm -C $webDir run build | Out-Host
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
  Push-Location $buildRoot
  try {
    & $uv build | Out-Host
  }
  finally {
    Pop-Location
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
    & $bizVenvPython -m pip install --force-reinstall --no-deps $latestWheel.FullName | Out-Host
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
    & $bizVenvPython -c $verifyScript | Out-Host
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
