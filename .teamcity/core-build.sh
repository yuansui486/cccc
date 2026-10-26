#!/usr/bin/env bash
set -euo pipefail
root_dir="$(pwd)"
docker build -f docker/Dockerfile.onecolleague-windows \
  -t onecolleague-windows-builder:rust-1.88-node24 .
docker run --rm \
  -v "$root_dir:/src" \
  -v /opt/Agent/cache/cargo/registry:/usr/local/cargo/registry \
  -v /opt/Agent/cache/cargo/git:/usr/local/cargo/git \
  -v /opt/Agent/cache/xwin:/opt/xwin-cache \
  -v /opt/Agent/cache/npm:/root/.npm \
  -v /opt/Agent/cache/onecolleague-rust-target:/target \
  -e CARGO_TARGET_DIR=/target -e XWIN_CACHE_DIR=/opt/xwin-cache \
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
