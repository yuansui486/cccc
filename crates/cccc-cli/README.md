# 一号同事 Windows core

This is the native Rust core bundled with the 一号同事 (OneColleague) launcher.
It provides shared agent messages, delivery tracking, runtime state, the Web UI,
and MCP access. Internal executable and crate names remain `cccc`.

## Installation and updates

Install the Windows x64 package published through the OneColleague release
channel. The launcher includes `resources/OneColleague_Core/cccc.exe`; users do
not need Python or Rust. AI tools can be installed separately when selected.

Use the launcher to check for and install updates to the complete application.
In this fork, `cccc update`, `cccc update --check`, and
`cccc update --check --offline` only report launcher-managed update instructions.
They never download upstream release metadata or execute an installer. Existing
`--channel stable` and `--channel rc` arguments remain accepted for compatibility.

The launcher keeps data in `%LOCALAPPDATA%\OneColleague\data`, outside the
installation directory. Upgrading the application preserves this directory.
This distribution does not import the previous Python application's data.

## Workspace development

Use the pinned Rust 1.88.0 toolchain and the Node version supported by
`web/package.json`. A Windows source build also requires the Visual Studio C++
build tools and the native prerequisites used by the release pipeline.
From the repository root:

```powershell
npm ci --prefix web
npm -C web run build
cargo build --locked --features standalone -p cccc --bin cccc
cargo run --locked --features standalone -p cccc --bin cccc -- --version
cargo test --locked --features standalone -p cccc
```

For a local desktop integration run with an isolated development data directory:

```powershell
$env:CCCC_HOME = Join-Path $env:LOCALAPPDATA 'OneColleague\dev-data'
$env:ONECOLLEAGUE_DESKTOP = '1'
cargo run --locked --features standalone -p cccc --bin cccc -- --host 127.0.0.1 --port 19001
```

Desktop embedding is enabled only on Windows when the actual listener is
`127.0.0.1:19001` and `ONECOLLEAGUE_DESKTOP=1`. Release builds allow the parent
origin `http://tauri.localhost`; debug builds also allow `http://localhost:1420`.
Standalone browser use retains the normal security headers.

Web Model and NotebookLM browser projection require a locally installed Chrome,
Chromium, or Microsoft Edge. The CLI, daemon, MCP server, and Web UI do not.
Internal `cccc-pair-*` crates are implementation details.

`main` tracks upstream; `OneColleague` contains the desktop distribution changes.
OneColleague release tags use `onecolleague-v<version>`. Upstream installer and
packaging scripts are retained as upstream source, outside this fork's Windows
release path.

Fork source: <https://github.com/yuansui486/cccc>

Upstream project: <https://github.com/chesterra/cccc>

License: Apache-2.0
