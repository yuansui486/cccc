param(
  [switch]$InstallDeps,
  [switch]$Clean,
  [switch]$SkipSmokeTests,
  [string]$Python = "python",
  [string]$BuildVenv = ".venv-build",
  [ValidateRange(1, 64)]
  [int]$Jobs = 8
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
$releaseDir = Join-Path $rootDir "dist"
$smokeHome = $null

function Get-ProjectVersion {
  $pyprojectPath = Join-Path $rootDir "pyproject.toml"
  foreach ($line in Get-Content -Path $pyprojectPath -Encoding UTF8) {
    if ($line -match '^\s*version\s*=\s*"([^"]+)"') {
      return $Matches[1]
    }
  }
  throw "Could not determine project version from pyproject.toml"
}

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

function Test-OneColleagueVersion {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath
  )

  $expectedVersion = Get-ProjectVersion
  $actualVersion = (& $ExePath version)
  $exitCode = $LASTEXITCODE
  if ($null -ne $exitCode -and $exitCode -ne 0) {
    throw "$ExePath version failed with exit code $exitCode"
  }
  $actualVersion = (($actualVersion | Out-String).Trim())
  if (-not $actualVersion -or $actualVersion -eq "0.0.0" -or $actualVersion -ne $expectedVersion) {
    throw "Nuitka smoke version mismatch: expected $expectedVersion, got $actualVersion"
  }
}

function Prepare-WindowsUIAutomationBindings {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath
  )

  $stageScript = Join-Path $rootDir "scripts\stage_windows_uia_bindings.py"
  Test-PathExists -Path $stageScript -Message "Missing vendored UIAutomation staging script"
  Invoke-CheckedNative -FilePath $PythonPath -ArgumentList @($stageScript) -WorkingDirectory $rootDir
}

function Resolve-ProjectPython {
  param(
    [Parameter(Mandatory = $true)]
    [string]$PythonSelector
  )

  $venvPython = Join-Path (Join-Path $rootDir $BuildVenv) "Scripts\python.exe"
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

    Test-OneColleagueVersion -ExePath $ExePath
    Invoke-CheckedNative -FilePath $ExePath -ArgumentList @("doctor", "--require-native-picker") -WorkingDirectory $rootDir
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
  Test-PathExists -Path (Join-Path $rootDir "uv.lock") -Message "Missing uv.lock; run uv lock and commit it before packaging."
  $componentProjectDir = Join-Path $rootDir "components\desensitization"
  Test-PathExists -Path (Join-Path $componentProjectDir "pyproject.toml") -Message "Missing desensitization component project."
  Test-PathExists -Path (Join-Path $componentProjectDir "uv.lock") -Message "Missing desensitization component lock file."
  $env:UV_PROJECT_ENVIRONMENT = Join-Path $rootDir $BuildVenv
  Invoke-CheckedNative -FilePath $uvPath -ArgumentList @("sync", "--locked", "--dev", "--python", "3.12") -WorkingDirectory $rootDir
  Invoke-CheckedNative -FilePath $uvPath -ArgumentList @("lock", "--check", "--project", $componentProjectDir) -WorkingDirectory $rootDir

  Write-Host "==> Install frontend dependencies"
  Invoke-CheckedNative -FilePath $npmPath -ArgumentList @("ci", "--prefix", $webDir) -WorkingDirectory $rootDir
}

$pythonPath = Resolve-ProjectPython -PythonSelector $Python

Write-Host "==> Check Python and Nuitka"
Invoke-CheckedNative -FilePath $pythonPath -ArgumentList @("-c", "import struct,sys; from importlib.metadata import version; assert sys.version_info[:2] == (3,12), 'Python 3.12 required'; assert struct.calcsize('P') == 8, 'x64 Python required'; print(sys.version); print('Nuitka', version('Nuitka'))") -WorkingDirectory $rootDir

Write-Host "==> Build desensitization component source bundle"
$componentBundleScript = Join-Path $rootDir "scripts\build_desensitization_component_bundle.py"
$componentBundle = Join-Path $resourcesDir "desensitization_component.zip"
Test-PathExists -Path $componentBundleScript -Message "Missing desensitization component bundle script"
Invoke-CheckedNative -FilePath $pythonPath -ArgumentList @($componentBundleScript) -WorkingDirectory $rootDir
Test-PathExists -Path $componentBundle -Message "Missing desensitization component bundle"

Write-Host "==> Prepare Windows UI Automation bindings"
Prepare-WindowsUIAutomationBindings -PythonPath $pythonPath

Write-Host "==> Build bundled Web UI"
Invoke-CheckedNative -FilePath $npmPath -ArgumentList @("-C", $webDir, "run", "build") -WorkingDirectory $rootDir
Test-PathExists -Path (Join-Path $webDistDir "index.html") -Message "Web build failed, missing src\no1\ports\web\dist\index.html"

Test-PathExists -Path $resourcesDir -Message "Missing src\no1\resources"

Write-Host "==> Build Nuitka standalone distribution"
$nuitkaArgs = @(
  "-m", "nuitka",
  "--standalone",
  "--assume-yes-for-downloads",
  "--jobs=$Jobs",
  "--include-windows-runtime-dlls=yes",
  "--output-dir=build\nuitka",
  "--output-filename=onecolleague.exe",
  "--output-folder-name=no1.frozen_entry.dist",
  "--report=build\nuitka\nuitka-report.xml",
  "--include-package=no1",
  "--include-package=comtypes.gen",
  "--include-module=comtypes.gen.UIAutomationClient",
  "--include-module=comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0",
  "--include-module=comtypes.gen.stdole",
  "--include-module=comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0",
  "--include-distribution-metadata=no1",
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
Test-PathExists -Path (Join-Path $distDir "no1\resources\desensitization_component.zip") -Message "Nuitka dist is missing the desensitization component bundle"
Invoke-CheckedNative -FilePath $pythonPath -ArgumentList @($componentBundleScript, "--check") -WorkingDirectory $rootDir
foreach ($winptyRuntime in @(
    "winpty\winpty.pyd",
    "winpty\conpty.dll",
    "winpty\winpty.dll",
    "winpty\winpty-agent.exe",
    "winpty\OpenConsole.exe"
  )) {
  Test-PathExists -Path (Join-Path $distDir $winptyRuntime) -Message "Nuitka dist is missing Windows PTY runtime file: $winptyRuntime"
}
Test-OneColleagueVersion -ExePath $exePath

if (-not $SkipSmokeTests) {
  Write-Host "==> Run packaged smoke tests with temporary CCCC_HOME"
  Invoke-OneColleagueSmoke -ExePath $exePath
}

Write-Host "==> Create Windows x64 standalone archive"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
if (Get-ChildItem -LiteralPath $distDir -Recurse -File | Where-Object { $_.FullName -match "no1[\\/]desensitization|presidio|paddleocr|torch|transformers|modelscope" }) {
  throw "Nuitka output unexpectedly contains desensitization runtime packages"
}
$version = Get-ProjectVersion
$releaseZip = Join-Path $releaseDir ("onecolleague-{0}-windows-x64.zip" -f $version)
$releaseHash = "$releaseZip.SHA256"
if (Test-Path -LiteralPath $releaseZip) { Remove-Item -LiteralPath $releaseZip -Force }
if (Test-Path -LiteralPath $releaseHash) { Remove-Item -LiteralPath $releaseHash -Force }
# Nuitka bundles already-compressed DLLs and model assets. Store the files
# directly so CI does not spend an unbounded amount of time recompressing
# multi-gigabyte native libraries; the standalone directory remains intact.
Compress-Archive -Path (Join-Path $distDir "*") -DestinationPath $releaseZip -CompressionLevel NoCompression
$archive = [System.IO.Compression.ZipFile]::OpenRead($releaseZip)
try {
  $archiveNames = @($archive.Entries | ForEach-Object { $_.FullName.Replace('\', '/') })
  foreach ($requiredEntry in @(
      "no1/resources/desensitization_component.zip"
    )) {
    if ($archiveNames -notcontains $requiredEntry) {
      throw "Standalone ZIP is missing $requiredEntry"
    }
  }
}
finally {
  $archive.Dispose()
}
$hash = (Get-FileHash -LiteralPath $releaseZip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $releaseHash -Encoding ASCII -Value ("{0}  {1}" -f $hash, (Split-Path -Leaf $releaseZip))

Write-Host ""
Write-Host "OK: Nuitka standalone distribution ready:"
Write-Host "  $distDir"
Write-Host "  $releaseZip"
Write-Host "  $releaseHash"
Write-Host ""
Write-Host "Local verification commands:"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe version"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe doctor"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe daemon start"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe daemon status"
Write-Host "  .\build\nuitka\no1.frozen_entry.dist\onecolleague.exe daemon stop"
