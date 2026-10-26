#!/usr/bin/env bash
# Linux cargo-xwin builder; Python 3.11+, Node/npm, Rust/rustup, clang/lld,
# cmake/ninja, NASM and the cached Windows SDK/native libraries are prerequisites.
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"
python3 scripts/onecolleague_release.py check
toolchain="$(python3 -c 'import tomllib; print(tomllib.load(open("rust-toolchain.toml", "rb"))["toolchain"]["channel"])')"
rustup run "$toolchain" cargo xwin --version
# ONNX Runtime's prebuilt objects request PathCch.lib with Windows casing.
# xwin caches the SDK import library in lowercase on Linux. Provide that exact
# alias without changing its bytes or adding alternate linker search paths.
sdk_lib_dir="${XWIN_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/cargo-xwin}/xwin/sdk/lib/um/x86_64"
ensure_sdk_case_alias() {
  if [[ -f "$sdk_lib_dir/pathcch.lib" && ! -e "$sdk_lib_dir/PathCch.lib" ]]; then
    ln -s pathcch.lib "$sdk_lib_dir/PathCch.lib"
    return 0
  fi
  return 1
}
ensure_sdk_case_alias || true
npm ci --include=dev --prefix web
node scripts/prepare_rust_web_assets.mjs
build_core() {
  rustup run "$toolchain" cargo xwin build --release --locked --features standalone \
    --target x86_64-pc-windows-msvc -p cccc --bin cccc
}
if build_core; then
  :
else
  build_status=$?
  # A first-ever xwin invocation populates the SDK only during compilation.
  # Retry once only when that invocation made the missing alias repairable.
  if ensure_sdk_case_alias; then build_core; else exit "$build_status"; fi
fi
binary="${CARGO_TARGET_DIR:-target}/x86_64-pc-windows-msvc/release/cccc.exe"
python3 scripts/onecolleague_release.py package --binary "$binary" --output-dir "${OUTPUT_DIR:-dist}"
