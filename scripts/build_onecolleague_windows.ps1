param(
  [string]$OutputDir = "dist",
  [string]$Tag = $env:RELEASE_TAG,
  [string]$BuildId = $env:BUILD_ID,
  [string]$ExpectedCommit = $env:EXPECTED_COMMIT
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$rootDir = Split-Path -Parent $PSScriptRoot
Push-Location $rootDir
try {
  # Omit empty values for Windows PowerShell's native argument handling.
  $identityArgs = @()
  if ($Tag) { $identityArgs += @("--tag", $Tag) }
  if ($ExpectedCommit) { $identityArgs += @("--expected-commit", $ExpectedCommit) }
  $identityJson = & python scripts/onecolleague_release.py check @identityArgs
  if ($LASTEXITCODE -ne 0) { throw "Release identity check failed" }
  $identity = $identityJson | ConvertFrom-Json
  & npm ci --prefix web
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  & node scripts/prepare_rust_web_assets.mjs
  if ($LASTEXITCODE -ne 0) { throw "Web asset build failed" }
  & rustup run $identity.toolchain cargo build --release --locked --features standalone --target x86_64-pc-windows-msvc -p cccc --bin cccc
  if ($LASTEXITCODE -ne 0) { throw "Windows core compilation failed" }
  $targetDir = if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { "target" }
  $binary = Join-Path $targetDir "x86_64-pc-windows-msvc/release/cccc.exe"
  & $binary --version
  if ($LASTEXITCODE -ne 0) { throw "Windows core smoke test failed" }
  $packageArgs = @("--binary", $binary, "--output-dir", $OutputDir) + $identityArgs
  if ($BuildId) { $packageArgs += @("--build-id", $BuildId) }
  & python scripts/onecolleague_release.py package @packageArgs
  if ($LASTEXITCODE -ne 0) { throw "Windows core packaging failed" }
} finally { Pop-Location }
