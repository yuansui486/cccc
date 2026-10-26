# 一号同事 Windows 核心

此包是“一号同事”启动器使用的 Rust 核心，支持 Windows x64。
通过启动器安装和更新，不需要安装 Python 或 Rust。AI 工具由启动器按需安装。

包内 `cccc.exe` 保留上游内部名称和核心版本。产品发行版本及提交信息见同批
`core-release-manifest.json`，两种版本不要求相等。许可证见 `LICENSE`。

启动器以当前用户运行服务，数据目录为 `%LOCALAPPDATA%\OneColleague\data`。
安装或更新核心不应删除数据目录。此发行不导入旧 Python 版的数据。

手动诊断（应先退出启动器）：

```powershell
$env:CCCC_HOME = Join-Path $env:LOCALAPPDATA 'OneColleague\data'
.\cccc.exe --host 127.0.0.1 --port 19001
.\cccc.exe daemon stop
```

请勿同时运行两份核心。核心更新由“一号同事”启动器和自有发布渠道管理。
