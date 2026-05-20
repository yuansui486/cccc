#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WORKSPACE_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
APP_ROOT="$WORKSPACE_ROOT/OneColleague_application"
APP_RESOURCES_DIR="$APP_ROOT/src-tauri/resources"
APP_DATA_DIR="$HOME/Library/Application Support/onecolleague"
BIZ_VENV_PYTHON="$APP_DATA_DIR/runtime/biz-venv/bin/python"

TARGET_WORKSPACE="${1:-$APP_ROOT}"
BUILD_TMP_ROOT=""

cleanup() {
  if [[ -n "$BUILD_TMP_ROOT" && -d "$BUILD_TMP_ROOT" ]]; then
    rm -rf "$BUILD_TMP_ROOT"
  fi
}
trap cleanup EXIT

if [[ ! -d "$REPO_ROOT/web" ]]; then
  echo "ERROR: web directory not found: $REPO_ROOT/web" >&2
  exit 1
fi

if [[ ! -d "$APP_ROOT" ]]; then
  echo "ERROR: application directory not found: $APP_ROOT" >&2
  exit 1
fi

if [[ ! -d "$TARGET_WORKSPACE" ]]; then
  echo "ERROR: target workspace not found: $TARGET_WORKSPACE" >&2
  exit 1
fi

EXISTING_NO1_WHL="$(ls -t "$APP_RESOURCES_DIR"/no1-*.whl 2>/dev/null | head -n 1 || true)"
if [[ -n "$EXISTING_NO1_WHL" && -f "$EXISTING_NO1_WHL" ]]; then
  EXISTING_NO1_BASENAME="$(basename "$EXISTING_NO1_WHL")"
  EXISTING_NO1_VERSION="$(printf '%s\n' "$EXISTING_NO1_BASENAME" | sed -n 's/^no1-\([^-]*\)-.*/\1/p')"
  if [[ -n "$EXISTING_NO1_VERSION" ]]; then
    cat > "$APP_RESOURCES_DIR/biz-manifest.json" <<EOF
{
  "bizVersion": "$EXISTING_NO1_VERSION",
  "whlFile": "$EXISTING_NO1_BASENAME"
}
EOF
  fi
fi

echo "==> Build bundled Web UI"
bash "$REPO_ROOT/scripts/build_web.sh"
test -f "$REPO_ROOT/src/cccc/ports/web/dist/index.html"

echo "==> Build Python package"
rm -rf "$REPO_ROOT/dist"
BUILD_TMP_ROOT="$(mktemp -d)"
BUILD_ROOT="$BUILD_TMP_ROOT/OneColleague"
if command -v rsync >/dev/null 2>&1; then
  rsync -a \
    --exclude '/.git/' \
    --exclude '/.venv/' \
    --exclude '__pycache__/' \
    --exclude '/node_modules/' \
    --exclude '/web/node_modules/' \
    --exclude '/dist/' \
    "$REPO_ROOT/" "$BUILD_ROOT/"
else
  mkdir -p "$BUILD_ROOT"
  cp -R "$REPO_ROOT/." "$BUILD_ROOT/"
  rm -rf "$BUILD_ROOT/.git" "$BUILD_ROOT/.venv" "$BUILD_ROOT/dist" "$BUILD_ROOT/web/node_modules"
fi
perl -0pi -e 's/^name\s*=\s*"cccc-pair"/name = "no1"/m' "$BUILD_ROOT/pyproject.toml"
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is required to build the no1 package." >&2
  exit 1
fi
(cd "$BUILD_ROOT" && uv build)
mkdir -p "$REPO_ROOT/dist"
cp "$BUILD_ROOT"/dist/no1-*.whl "$REPO_ROOT/dist/"

LATEST_WHL="$(ls -t "$REPO_ROOT"/dist/no1-*.whl | head -n 1)"
if [[ -z "${LATEST_WHL:-}" || ! -f "$LATEST_WHL" ]]; then
  echo "ERROR: no no1 wheel found under $REPO_ROOT/dist" >&2
  exit 1
fi

WHEEL_LIST="$BUILD_TMP_ROOT/wheel-files.txt"
unzip -Z1 "$LATEST_WHL" > "$WHEEL_LIST"
if ! grep -Fxq 'cccc/ports/web/dist/index.html' "$WHEEL_LIST"; then
  echo "ERROR: built wheel is missing bundled Web UI: cccc/ports/web/dist/index.html" >&2
  exit 1
fi

WHL_BASENAME="$(basename "$LATEST_WHL")"
BIZ_VERSION="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)"/\1/p' "$REPO_ROOT/pyproject.toml" | head -n 1)"
if [[ -z "$BIZ_VERSION" ]]; then
  echo "ERROR: failed to read version from $REPO_ROOT/pyproject.toml" >&2
  exit 1
fi

echo "==> Refresh application bundled resources"
mkdir -p "$APP_RESOURCES_DIR"
find "$APP_RESOURCES_DIR" -maxdepth 1 -type f -name 'no1-*.whl' ! -name "$WHL_BASENAME" -delete
cp "$LATEST_WHL" "$APP_RESOURCES_DIR/$WHL_BASENAME"
cat > "$APP_RESOURCES_DIR/biz-manifest.json" <<EOF
{
  "bizVersion": "$BIZ_VERSION",
  "whlFile": "$WHL_BASENAME"
}
EOF

echo "==> Prepare launcher workspace update package"
find "$TARGET_WORKSPACE" -maxdepth 1 -type f -name 'no1-*.whl' ! -name "$WHL_BASENAME" -delete
cp "$LATEST_WHL" "$TARGET_WORKSPACE/$WHL_BASENAME"

echo "==> Update installed application business package"
if [[ -x "$BIZ_VENV_PYTHON" ]]; then
  "$BIZ_VENV_PYTHON" -m pip install --force-reinstall --no-deps "$LATEST_WHL"
  "$BIZ_VENV_PYTHON" - <<'PY'
import importlib.metadata as metadata
import pathlib

import cccc

dist = pathlib.Path(cccc.__file__).resolve().parent / "ports" / "web" / "dist" / "index.html"
print(f"installed no1={metadata.version('no1')}")
print(f"cccc={pathlib.Path(cccc.__file__).resolve()}")
print(f"web_dist_index={dist}")
PY
else
  echo "WARN: application business venv not found; prepared wheel for launcher update: $BIZ_VENV_PYTHON" >&2
fi

echo "OK: prepared launcher update package"
echo "  wheel: $WHL_BASENAME"
echo "  version: $BIZ_VERSION"
echo "  workspace: $TARGET_WORKSPACE"
echo "  installed: $BIZ_VENV_PYTHON"
