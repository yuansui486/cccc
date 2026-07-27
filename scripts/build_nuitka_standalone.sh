#!/usr/bin/env bash
set -euo pipefail

INSTALL_DEPS=0
CLEAN=0
SKIP_SMOKE_TESTS=0
PYTHON_SELECTOR="python3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-deps) INSTALL_DEPS=1 ;;
    --clean) CLEAN=1 ;;
    --skip-smoke-tests) SKIP_SMOKE_TESTS=1 ;;
    --python)
      shift
      [[ $# -gt 0 ]] || { echo "--python requires a value" >&2; exit 2; }
      PYTHON_SELECTOR="$1"
      ;;
    -h|--help)
      cat <<'EOF'
Usage: scripts/build_nuitka_standalone.sh [options]

Options:
  --install-deps       Run uv sync --dev and npm ci --prefix web
  --clean              Remove build/nuitka before building
  --skip-smoke-tests   Skip packaged version/doctor/daemon checks
  --python PATH        Python command or path (default: python3)
EOF
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
OUTPUT_DIR="$ROOT_DIR/build/nuitka"
DIST_DIR="$OUTPUT_DIR/no1.frozen_entry.dist"
BIN_PATH="$DIST_DIR/onecolleague"
WEB_DIST_DIR="$ROOT_DIR/src/no1/ports/web/dist"
RESOURCES_DIR="$ROOT_DIR/src/no1/resources"

die() { echo "ERROR: $*" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "$2"; }

[[ "$(uname -s)" == "Darwin" ]] || die "macOS Nuitka packaging must run on macOS"
require_command uv "uv is required; install it from https://docs.astral.sh/uv/"
require_command npm "Node.js/npm is required"

if [[ "$CLEAN" == 1 ]]; then
  case "$OUTPUT_DIR" in
    "$ROOT_DIR/build/nuitka") rm -rf "$OUTPUT_DIR" ;;
    *) die "refusing to clean output outside repository build directory" ;;
  esac
fi

if [[ "$INSTALL_DEPS" == 1 ]]; then
  uv sync --dev
  npm ci --prefix "$WEB_DIR"
fi

PYTHON_PATH="$PYTHON_SELECTOR"
if [[ "$PYTHON_SELECTOR" == "python3" && -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_PATH="$ROOT_DIR/.venv/bin/python"
fi
"$PYTHON_PATH" -c "import sys; from importlib.metadata import version; print(sys.version); print('Nuitka', version('Nuitka'))"

npm -C "$WEB_DIR" run build
[[ -f "$WEB_DIST_DIR/index.html" ]] || die "Web build did not produce $WEB_DIST_DIR/index.html"
[[ -d "$RESOURCES_DIR" ]] || die "Missing $RESOURCES_DIR"

"$PYTHON_PATH" -m nuitka \
  --standalone \
  --assume-yes-for-downloads \
  --output-dir="$OUTPUT_DIR" \
  --output-filename=onecolleague \
  --output-folder-name=no1.frozen_entry.dist \
  --include-package=no1 \
  --include-package-data=no1 \
  --include-distribution-metadata=no1 \
  --include-data-dir="$WEB_DIST_DIR=no1/ports/web/dist" \
  --include-data-dir="$RESOURCES_DIR=no1/resources" \
  --nofollow-import-to=lark_oapi.* \
  --nofollow-import-to=dingtalk_stream.* \
  --nofollow-import-to=wechatbot_sdk.* \
  --main="$ROOT_DIR/src/no1/frozen_entry.py"

[[ -x "$BIN_PATH" ]] || die "Nuitka build did not produce $BIN_PATH"
[[ -f "$DIST_DIR/no1/ports/web/dist/index.html" ]] || die "Nuitka dist is missing bundled Web UI"
[[ -f "$DIST_DIR/no1/resources/onecolleague-help.md" ]] || die "Nuitka dist is missing resources"

EXPECTED_VERSION="$("$PYTHON_PATH" -c 'import pathlib; print(next(line.split("\"")[1] for line in pathlib.Path("pyproject.toml").read_text(encoding="utf-8").splitlines() if line.startswith("version")))')"
ACTUAL_VERSION="$("$BIN_PATH" version | tr -d '\r' | tail -n 1)"
[[ "$ACTUAL_VERSION" == "$EXPECTED_VERSION" && "$ACTUAL_VERSION" != "0.0.0" ]] || die "Nuitka smoke version mismatch: expected $EXPECTED_VERSION, got $ACTUAL_VERSION"

SMOKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/onecolleague-nuitka-smoke.XXXXXX")"
cleanup() {
  if [[ "$SKIP_SMOKE_TESTS" == 0 ]]; then
    "$BIN_PATH" daemon stop >/dev/null 2>&1 || true
  fi
  rm -rf "$SMOKE_HOME"
}
trap cleanup EXIT

if [[ "$SKIP_SMOKE_TESTS" == 0 ]]; then
  export ONECOLLEAGUE_HOME="$SMOKE_HOME"
  export CCCC_HOME="$SMOKE_HOME"
  "$BIN_PATH" doctor
  "$BIN_PATH" daemon start
  "$BIN_PATH" daemon status
fi

mkdir -p "$ROOT_DIR/dist"
ZIP_PATH="$ROOT_DIR/dist/onecolleague-${EXPECTED_VERSION}-macos-arm64.zip"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$DIST_DIR" "$ZIP_PATH"
echo "OK: macOS arm64 standalone distribution ready: $ZIP_PATH"
