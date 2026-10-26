# 一号同事 Rust 定制版维护

## 分支与定制边界

`main` 只同步上游，`OneColleague` 保存品牌、启动器接入、更新策略与交付适配。
保留上游目录结构和其他平台源码；不迁移旧 Python 版账号、业务改动或数据。
内部 Cargo 包名、命令名及版本仍属于上游；用户产品名为“一号同事”。

一次性迁移先保存旧 GitHub/Codeup 分支指针，再从当前 `main` 重建定制分支。
以后正常 merge，不反复覆盖历史：

```powershell
git fetch upstream
git switch main
git merge --ff-only upstream/main
git push origin main
git switch OneColleague
git merge main
# 检查下述五项、运行验证，再推同一提交。
git push origin OneColleague
git push codeup OneColleague
```

同步后检查品牌、作者更新入口禁用、桌面嵌入来源、启动/停止与数据目录、交付脚本五处。
启动器是独立仓库；核心与启动器提交由流水线快照和发行清单关联，不依赖“最近成功包”。

## 构建与版本

`onecolleague-release.json` 是产品发行版本源，首版 `1.0.0`。
上游 `Cargo.toml` 版本目前是 `0.4.45`，不要为产品发布改写它。
正式标签为 `onecolleague-v1.0.0`；上游 `v*` 标签不是自有发布入口。
发布前先更新发行配置，完成测试构建，再在已验证提交上打同版本专用标签。

构建工具使用 Python 3.11+、Node 24.19.0、仓库 `rust-toolchain.toml` 的 Rust 1.88.0。
Windows 需要 Visual Studio C++/Windows SDK、CMake、NASM；Linux 使用自有 cargo-xwin
镜像，包含 Clang/LLD、CMake/Ninja、NASM 和经校验的原生依赖缓存。

```powershell
python scripts/onecolleague_release.py check
python -m unittest discover -s scripts/tests -p test_onecolleague_release.py -v
.\scripts\build_onecolleague_windows.ps1 -BuildId local
```

Linux 构建运行 `bash scripts/build_onecolleague_windows.sh`，支持环境变量
`BUILD_ID`、`RELEASE_TAG`、`EXPECTED_COMMIT`、`OUTPUT_DIR`、`CARGO_TARGET_DIR`。
脚本执行 `npm ci`、打包 Web、`cargo build --locked --features standalone`（Linux 使用 xwin）
并检查 PE x64 文件。Windows 脚本额外执行 `cccc.exe --version`。
正式构建须具有 Git checkout 和对应标签，并设置 `RELEASE_TAG` 与准确 `EXPECTED_COMMIT`；
标签指向与 checkout 不一致即失败。分支构建不设置 `RELEASE_TAG`。

产物在 `dist/`：

- `onecolleague-1.0.0-windows-x64.zip`：根目录含 `cccc.exe`、`LICENSE`、发行专用 `README.md`。
- `onecolleague-windows-standalone.zip`：同一 ZIP 的固定名副本，用于 TeamCity 依赖规则。
- `core-release-manifest.json`：发行/核心版本、提交、构建 ID、标签、目标、工具链、包及 EXE 的大小与 SHA256。

## 交付与验收

启动器通过 `scripts/prepare-core.py` 验证本次快照 ZIP 和清单，替换
`src-tauri/resources/OneColleague_Core`；不会读旧 Python 包或自动挑选最近的核心。
启动器在构建工作区同步发行版本，完整 NSIS 安装包沿用自有测试与正式更新源。
流水线配置和发布步骤见启动器仓库 `docs/teamcity-release.md`。

交叉编译成功不代表 Windows 验收完成。正式发布前必须在真实 Windows 普通用户环境验证：
无 Python/Rust 安装启动、中文路径、工具按需安装、团队与消息、PTY/WebSocket、文件、
麦克风与剪贴板、非白名单嵌入被拒绝、端口冲突、重复启动、停止和重启、
完整安装包更新校验、断网/坏包处理、数据保留与提交可追溯性。
