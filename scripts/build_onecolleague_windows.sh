#!/usr/bin/env bash
# Linux cargo-xwin builder; Python 3.11+, Node/npm, Rust/rustup, clang/lld,
# cmake/ninja, NASM and the cached Windows SDK/native libraries are prerequisites.
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root_dir"
python3 scripts/onecolleague_release.py check
toolchain="$(python3 -c 'import tomllib; print(tomllib.load(open("rust-toolchain.toml", "rb"))["toolchain"]["channel"])')"
rustup run "$toolchain" cargo xwin --version
npm ci --include=dev --prefix web
node scripts/prepare_rust_web_assets.mjs
rustup run "$toolchain" cargo xwin build --release --locked --features standalone \
  --target x86_64-pc-windows-msvc -p cccc --bin cccc
binary="${CARGO_TARGET_DIR:-target}/x86_64-pc-windows-msvc/release/cccc.exe"
python3 scripts/onecolleague_release.py package --binary "$binary" --output-dir "${OUTPUT_DIR:-dist}"
