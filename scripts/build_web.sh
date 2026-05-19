#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

npm ci --prefix "$ROOT_DIR/web"
npm -C "$ROOT_DIR/web" run build

test -f "$ROOT_DIR/src/no1/ports/web/dist/index.html"
echo "OK: built bundled Web UI -> src/no1/ports/web/dist"
