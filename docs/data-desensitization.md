# 数据脱敏工作台

数据脱敏紧跟工作组侧栏的“控制电脑”。进入工作台时主侧栏自动折叠，移动端抽屉关闭；页面内可重新展开。识别和处理均在本机执行，任务、规则、策略和输出按工作组隔离。

## 输出与数据目录

- 工作组存在活动 scope 时，结果写入 `<scope>/脱敏后`。
- 工作组没有活动 scope 时，结果写入 `<group.path>/脱敏后`。
- 单文件输出使用 `<原文件名>_脱敏.<扩展名>`，同名文件自动增加序号，绝不覆盖已有文件。
- 文件夹批次保留相对目录层级，并生成 `results.zip` 和 `report.json`。
- 选择文件夹后先确认保存目录，确认前不上传文件或创建批次。默认使用 `脱敏后/批次-时间-随机标识`，可编辑当前工作目录内的相对子目录。目录冲突、越界和工作目录变化会报错，不自动改名；已有批次的目录保持冻结。
- SQLite 状态位于 `<group.path>/state/desensitization/desensitization.sqlite3`。
- 规则和策略引擎状态位于 `<group.path>/state/desensitization/engine`。
- 共享模型位于 `<ONECOLLEAGUE_HOME>/state/desensitization/models`。

支持 TXT、Markdown、DOCX、XLSX、XLSM、PDF、PNG、JPG/JPEG、BMP 和 TIFF。文件夹上传会跳过隐藏文件和不支持的格式。

## 使用流程

单文件会先进入复核页。用户可以逐项勾选文本实体或 OCR 区域、生成预览，再执行脱敏。

NER 实体默认勾选阈值为 `score >= 0.05`，低分实体仍可手动勾选。

DOCX 图片按独立资源 OCR，覆盖正文、表格、页眉页脚和浮动图片，共享图片只识别一次。图片与正文使用独立实体偏移，可逐图复核、手动画框及修改归一化坐标。结果预览展示实际脱敏文本与图片。损坏、超限、不支持、外链及 OCR 失败的图片保留原资源并列出位置警告，任务标记“部分完成”；外链图片不下载，报告不保存 OCR 原文。多帧图片暂保留原资源并警告，旧 `.doc` 不在支持范围。

识别规则分为“内置规则、自定义敏感项、自定义规则”，默认打开内置规则。旧关键词规则仍在自定义规则下，分类管理也位于该页。

文件夹支持两种模式：

- 自动执行：逐文件分析后直接写入批次输出目录。
- 分析后复核：所有文件分析完成后暂停，用户逐项确认再执行。

开启可逆脱敏时必须设置至少 12 个字符的口令。口令只保存在当前进程内存中，不写入 SQLite。可逆批次在应用重启后需要重新提交；生产操作应在批次结束前保持服务运行。历史记录中的可逆文件可通过原口令恢复。

## 模型初始化

首次进入工作台会检查依赖并启动 Sidecar。模型权重不包含在安装包中，需要可访问 ModelScope 的网络环境。模型下载完成后可离线使用，Sidecar 安装过程不会阻塞主 Web 进程。

模型失败时在“本地模型”页查看 Sidecar 阶段、安装错误、目录和模型错误，再执行“初始化 / 重试”。状态文件位于 `<CCCC_HOME>/state/desensitization/sidecar.json`，日志位于 `<CCCC_HOME>/state/desensitization/sidecar.log`。企业网络可通过标准 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NO_PROXY` 配置代理；镜像配置应通过部署环境设置，不应写入仓库。

## 生产配置

| 环境变量 | 默认值 | 说明 |
| --- | ---: | --- |
| `ONECOLLEAGUE_DESENSITIZATION_ENABLED` | `1` | 功能总开关，设为 `0`、`false` 或 `no` 时禁用 |
| `ONECOLLEAGUE_DESENSITIZATION_MAX_FILE_BYTES` | `0` | 单文件上限，`0` 表示不设固定业务字节上限 |
| `ONECOLLEAGUE_DESENSITIZATION_MAX_BATCH_FILES` | `0` | 单批文件数量上限，`0` 表示不设业务上限 |
| `ONECOLLEAGUE_DESENSITIZATION_MAX_BATCH_BYTES` | `0` | 单批总字节上限，`0` 表示不设固定业务字节上限 |
| `ONECOLLEAGUE_DESENSITIZATION_MIN_FREE_DISK_BYTES` | `1073741824` | 处理后必须保留的磁盘空间 |
| `ONECOLLEAGUE_DESENSITIZATION_SOURCE_TTL_HOURS` | `24` | 原始上传文件保留时间 |

即使业务上限为 `0`，服务仍检查磁盘空间和可用内存；Office、PDF、图片和 OCR 也有格式解析保护。上传以 8 MiB 分块落盘，Windows 文件流不会使用超大读取参数。Sidecar 依赖从 `components/desensitization/uv.lock` 安装。

可逆脱敏仍使用兼容历史任务的 AES-GCM JSON 映射格式，会把原文件内容编码到加密载荷中，因此受 `security.py` 的约 500 MiB 载荷保护。超过该范围的文件可使用不可逆模式，或通过配置业务上限后重试。

## 权限与安全

- 查询、上传、复核和下载要求当前用户拥有对应工作组访问权。
- 规则、规则分类、策略修改和模型初始化要求管理员权限。
- 下载通过 Authorization header 获取，二进制响应使用 `private, no-store`。
- 所有相对路径经过穿越检查和 containment 校验；任务及批次查询同时校验 `group_id`。
- 可逆映射由 AES-GCM 保护。部署时应将恢复口令通过独立渠道交付，避免与脱敏结果一同传输。

## Windows x64 发布

构建机要求：Windows x64、Python 3.12 x64、Node.js 20、uv、Visual C++ Build Tools 和 Nuitka 4.1+。

```powershell
uv lock
npm -C web ci
npm -C web run lint
npm -C web run typecheck
npm -C web run test
npm -C web run build
uv run python -m pytest -q
uv run --project components/desensitization --directory components/desensitization pytest -q
.\scripts\build_nuitka_standalone.ps1 -InstallDeps -Clean
```

Nuitka 主包不包含脱敏重型依赖。构建完成后生成 `build/nuitka/no1.frozen_entry.dist`，其中同时复制 `components/desensitization` 源码和锁文件，并在 `dist/` 生成版本化 standalone ZIP 和 SHA-256 清单。解压 ZIP 后直接运行目录内的 `onecolleague.exe`，首次使用时由 Sidecar 创建独立 venv。

## 发布验收

1. 校验 standalone ZIP 的 SHA-256 清单并解压运行。
2. 启动程序，确认“数据脱敏”入口、模型初始化和能力状态。
3. 分别处理 TXT、DOCX、XLSX、XLSM、PDF 和图片，核对输出目录及不覆盖行为。
4. 上传含子目录的文件夹，验证自动模式、复核模式、取消、ZIP 和 JSON 报告。
5. 切换活动 scope 后新建任务，确认任务继续写入创建时快照目录。
6. 用两个工作组验证任务、批次、规则和策略互不可见。
7. 验证可逆文件恢复、错误口令拒绝和原文件 TTL 清理。
8. 替换 standalone 目录后确认工作组、模型、历史和输出仍保留。

Windows 10/11 虚拟机验收不在本发布流程中；CI 和本机构建烟测作为安装包门禁。
