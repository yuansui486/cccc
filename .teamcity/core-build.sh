#!/usr/bin/env bash
set -euo pipefail
root_dir="$(pwd)"
native_cache_dir=/opt/Agent/cache/onecolleague-native
native_archive=sherpa-onnx-v1.13.4-win-x64-static-MT-Release-lib.tar.bz2
test -f "$native_cache_dir/$native_archive" || {
  echo "Missing pinned native archive: $native_cache_dir/$native_archive" >&2
  echo "Seed the CI native cache using the checksum in sherpa-onnx-sys/build.rs." >&2
  exit 1
}
printf '%s  %s\n' d81bd1d25112540862d2387072e76b2b6843ef962918d6b5c7db5a19c6276b4c \
  "$native_cache_dir/$native_archive" | sha256sum --check
docker build -f docker/Dockerfile.onecolleague-windows \
  -t onecolleague-windows-builder:rust-1.88-node24 .
docker run --rm \
  -v "$root_dir:/src" \
  -v /opt/Agent/cache/cargo/registry:/usr/local/cargo/registry \
  -v /opt/Agent/cache/cargo/git:/usr/local/cargo/git \
  -v /opt/Agent/cache/xwin:/opt/xwin-cache \
  -v /opt/Agent/cache/npm:/root/.npm \
  -v /opt/Agent/cache/onecolleague-rust-target:/target \
  -v "$native_cache_dir:/opt/onecolleague-native:ro" \
  -e CARGO_TARGET_DIR=/target -e XWIN_CACHE_DIR=/opt/xwin-cache \
  -e SHERPA_ONNX_ARCHIVE_DIR=/opt/onecolleague-native \
  -e CARGO_BUILD_JOBS=2 -e CI=true \
  -e BUILD_ID -e RELEASE_TAG -e EXPECTED_COMMIT \
  -w /src onecolleague-windows-builder:rust-1.88-node24 \
  bash scripts/build_onecolleague_windows.sh
python3 - <<'PY'
import json
data = json.load(open('dist/core-release-manifest.json', encoding='utf-8'))
for key, value in [('onecolleague.version', data['version']), ('onecolleague.coreCommit', data['commit'])]:
    print(f"##teamcity[setParameter name='{key}' value='{value}']")
print(f"##teamcity[buildNumber '{data['version']}.{data['buildId']}']")
PY
