# InfiniteTalk Router / Worker 使用说明

这是一个面向 InfiniteTalk / ComfyUI 工作流的轻量 API 网关项目。项目将公网入口和 GPU 执行端拆成两个程序：

- `router_app.py`：Router / Gateway，对外提供 REST API，先接收客户端上传到本地队列，再由后台调度器调用空闲 Worker，并把结果文件保存到 Router。
- `worker_app.py`：ComfyUI Worker，主动连接 Router，接收图片和音频，调用本机或内网 ComfyUI。

Router 会把待提交请求暂存在本地队列中，避免 Worker 短暂断线时直接拒绝公网请求。客户端提交后先拿到 Router 生成的 `task_id`，Router 后台调用空闲 Worker 完成任务，并在生成成功后把结果视频拉回 Router 本地保存。客户端查询和下载都从 Router 交付；Worker 不需要对公网提供结果下载能力。

## 目录结构

```text
.
├── router_app.py              # Router 启动入口
├── worker_app.py              # Worker 启动入口
├── test_client.py             # 简单接口测试客户端
├── common/                    # Router 和 Worker 共用协议、配置
├── router/                    # REST API、WebSocket、Worker 池、任务队列和结果交付
├── worker/                    # Worker WebSocket 客户端、ComfyUI 调用、workflow 注入
├── workflows/                 # ComfyUI API workflow JSON
└── DESIGN.md                  # 架构设计说明
```

## 环境要求

- Python 3.10+
- 可访问的 ComfyUI 服务，默认地址为 `http://127.0.0.1:8080`
- 已导出的 ComfyUI API 格式 workflow JSON
- workflow 中包含图片、音频、宽度、高度和视频输出节点

## 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 快速启动

以下示例默认使用当前公网 Router：`https://dongdongkc.shierkeji.com:6205`。如果要本地调试 Router，可以把相关 URL 改回 `http://127.0.0.1:38349` 或 `ws://127.0.0.1:38349/ws/workers`。

### 1. 启动 ComfyUI

先启动 ComfyUI，并确认 Worker 能访问它：

```bash
curl http://127.0.0.1:8080/system_stats
```

如果你的 ComfyUI 不在 `127.0.0.1:8080`，启动 Worker 时设置 `COMFYUI_BASE_URL`。

### 2. 启动 Router

```bash
export WORKER_TOKEN="change-me"
export ROUTER_HOST="127.0.0.1"
export ROUTER_PORT="38349"
export PUBLIC_BASE_URL="https://dongdongkc.shierkeji.com:6205"
export DATABASE_URL="postgresql+asyncpg://user:password@host:5432/database"
export ROUTER_DB_TABLE_PREFIX="itd_"

python router_app.py
```

Router 默认监听 `127.0.0.1:38349`。

首次部署可以先执行 [scripts/create_router_tables.sql](scripts/create_router_tables.sql) 创建表；Router 启动时也会按 `ROUTER_DB_TABLE_PREFIX` 自动执行 `CREATE TABLE IF NOT EXISTS`。配置 `DATABASE_URL` 后，Router 会把任务队列、prompt 路由和 Worker 状态快照维护到 PostgreSQL，所有 `created_at`、`updated_at`、`finished_at`、`next_attempt_at`、`last_accessed_at`、`connected_at`、`disconnected_at` 均使用 Unix 毫秒时间戳。

生产或公网部署时通常需要改成：

```bash
export ROUTER_HOST="0.0.0.0"
export PUBLIC_BASE_URL="https://dongdongkc.shierkeji.com:6205"
export DATABASE_URL="postgresql+asyncpg://user:password@host:5432/database"
export ROUTER_DB_TABLE_PREFIX="itd_"
python router_app.py
```

### 3. 启动 Worker

另开一个终端：

```bash
export WORKER_TOKEN="change-me"
export WORKER_ID="gpu-worker-01"
export ROUTER_WS_URL="https://dongdongkc.shierkeji.com:6205/"
export COMFYUI_BASE_URL="http://127.0.0.1:8080"
export WORKFLOW_FILE="./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json"

python worker_app.py
```

`WORKER_TOKEN` 必须和 Router 一致。Worker 会主动连接 Router 的 `/ws/workers`，因此 Worker 所在机器不需要暴露公网端口。`ROUTER_WS_URL` 可以直接填写 `https://dongdongkc.shierkeji.com:6205/`，程序会自动转换成 `wss://dongdongkc.shierkeji.com:6205/ws/workers`。

### 3.1 使用 tmux 一键重启 ComfyUI 和 Worker

如果项目已同步到服务器 `/root/infinitetalk`，可以用内置脚本按顺序重启两个 tmux session：

```bash
cd /root/infinitetalk
bash scripts/start_tmux.sh
```

脚本会先执行模型下载命令：

```bash
bash /root/shells/download_model.sh umt5
bash /root/shells/download_model.sh wan2.1-i2v-480p
```

模型下载成功后，脚本会停止已存在的 `infinitetalk-worker` 和 `comfyui` session，然后启动：

- `comfyui` session：在 `/root/ComfyUI` 执行 `python main.py --use-sage-attention --port 8080`
- `infinitetalk-worker` session：在 `/root/infinitetalk` 执行 `python worker_app.py`

常用查看和停止命令：

```bash
tmux attach -t comfyui
tmux attach -t infinitetalk-worker
tmux kill-session -t comfyui
tmux kill-session -t infinitetalk-worker
```

脚本支持用环境变量覆盖默认值，例如：

```bash
COMFYUI_DIR=/root/ComfyUI \
COMFYUI_PORT=8080 \
WORKER_TOKEN="change-me" \
WORKER_ID="gpu-worker-01" \
ROUTER_WS_URL="https://dongdongkc.shierkeji.com:6205/" \
COMFYUI_BASE_URL="http://127.0.0.1:8080" \
WORKFLOW_FILE="./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json" \
bash scripts/start_tmux.sh
```

Worker 配置会自动读取 `/root/infinitetalk/.env.worker`；如果要指定其他环境变量文件，可以设置 `WORKER_ENV_FILE=/path/to/.env.worker`。默认会在启动 ComfyUI 后等待 3 秒再启动 Worker，可用 `COMFYUI_STARTUP_WAIT_SECONDS=0` 关闭等待。

### 4. 检查连接状态

```bash
curl https://dongdongkc.shierkeji.com:6205/healthz
curl https://dongdongkc.shierkeji.com:6205/api/v1/workers
```

正常情况下可以看到已连接的 Worker，例如：

```json
{
  "workers": [
    {
      "worker_id": "gpu-worker-01",
      "status": "idle",
      "capabilities": {"kind": "infinitetalk"}
    }
  ]
}
```

## 配置项

### Router 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ROUTER_HOST` | `127.0.0.1` | Router 监听地址 |
| `ROUTER_PORT` | `38349` | Router 监听端口 |
| `PUBLIC_BASE_URL` | `https://dongdongkc.shierkeji.com:6205` | 返回给客户端的视频下载 URL 前缀 |
| `DATABASE_URL` | 空 | Router 状态库连接串，支持 `postgresql+asyncpg://...` |
| `ROUTER_DB_TABLE_PREFIX` | `itd_` | Router 状态表名前缀 |
| `WORKER_TOKEN` | `change-me` | Worker 接入鉴权 token |
| `MAX_UPLOAD_MB` | `200` | 单次上传最大体积，包含 multipart 请求体 |
| `UPLOAD_STREAM_CHUNK_BYTES` | `262144` | 上传流分片大小预留配置 |
| `WORKER_ASSIGN_TIMEOUT_SECONDS` | `10.0` | Worker 分配超时预留配置 |
| `WORKER_RPC_TIMEOUT_SECONDS` | `30.0` | Router 等待 Worker RPC 响应超时 |
| `PROMPT_ROUTE_TTL_HOURS` | `24.0` | 任务记录、结果文件和 prompt 路由的保留时间 |
| `ROUTER_QUEUE_DIR` | `./router_data/queued_jobs` | Router 待提交任务的临时上传目录 |
| `ROUTER_RESULT_DIR` | `./router_data/results` | Router 保存结果视频的目录 |
| `ROUTER_QUEUE_MAX_JOBS` | `128` | Router 同时接收和等待提交的最大任务数 |
| `ROUTER_SUBMIT_RETRY_SECONDS` | `5.0` | 没有可用 Worker 或连接中断后的重试间隔 |
| `ROUTER_STATUS_POLL_SECONDS` | `2.0` | Router 调用 Worker 后轮询 ComfyUI 状态的间隔 |

### Worker 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ROUTER_WS_URL` | `https://dongdongkc.shierkeji.com:6205/` | Router Worker WebSocket 地址，会自动转换为 `wss://.../ws/workers` |
| `WORKER_ID` | `gpu-worker-01` | Worker 唯一标识 |
| `WORKER_TOKEN` | `change-me` | 必须和 Router 一致 |
| `COMFYUI_BASE_URL` | `http://127.0.0.1:8080` | Worker 访问 ComfyUI 的地址 |
| `WORKFLOW_FILE` | `./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json` | ComfyUI API workflow JSON 路径 |
| `IMAGE_NODE_ID` | `284` | workflow 中图片输入节点 ID |
| `AUDIO_NODE_ID` | `125` | workflow 中音频输入节点 ID |
| `VIDEO_NODE_ID` | `131` | workflow 中视频输出节点 ID |
| `WIDTH_NODE_ID` | `245` | workflow 中宽度参数节点 ID |
| `HEIGHT_NODE_ID` | `246` | workflow 中高度参数节点 ID |
| `POLL_INTERVAL_SECONDS` | `2.0` | 轮询间隔预留配置 |
| `POLL_TIMEOUT_SECONDS` | `600.0` | 轮询超时预留配置 |
| `WORKER_TMP_DIR` | `./worker_data/inflight` | Worker 接收上传时的临时目录 |
| `WORKER_RECONNECT_SECONDS` | `5.0` | Worker 断线后重连间隔 |
| `WORKER_HEARTBEAT_SECONDS` | `25.0` | Worker 心跳间隔 |

## API 使用

### 提交生成任务

```bash
curl -X POST "https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video" \
  -F "image=@./input.jpg" \
  -F "audio=@./input.wav" \
  -F "width=480" \
  -F "height=640"
```

成功接收响应：

```json
{
  "status": "queued",
  "task_id": "task_router_id",
  "status_url": "/api/v1/predict_talking_video/status/task_router_id",
  "message": "Queued"
}
```

说明：

- 请求类型必须是 `multipart/form-data`。
- `image` 和 `audio` 必填。
- `width` 默认 `480`，`height` 默认 `640`。
- Router 会先完整接收上传并返回 `task_id`，后台不断寻找可用 Worker 提交任务。
- 如果 Worker 暂时全部离线或忙碌，任务会保持 `queued`，不会因为当前没有 Worker 直接失败。
- 如果 Router 队列已满，会返回 `503`。

### 查询任务状态

```bash
curl "https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video/status/task_router_id"
```

状态路径可以传 `task_id`，也兼容已提交后的 ComfyUI `prompt_id`。

Queued:

```json
{
  "status": "queued",
  "task_id": "task_router_id",
  "attempts": 0,
  "message": "Queued"
}
```

Processing:

```json
{
  "status": "processing",
  "task_id": "task_router_id",
  "attempts": 1,
  "worker_id": "gpu-worker-01",
  "message": "Processing"
}
```

Processing after ComfyUI submission:

```json
{
  "status": "processing",
  "task_id": "task_router_id",
  "prompt_id": "comfy_prompt_id",
  "message": "Processing"
}
```

Saving result is also reported as processing:

```json
{
  "status": "processing",
  "task_id": "task_router_id",
  "prompt_id": "comfy_prompt_id",
  "message": "Processing"
}
```

生成成功：

```json
{
  "status": "success",
  "task_id": "task_router_id",
  "prompt_id": "comfy_prompt_id",
  "result": {
    "filename": "output.mp4",
    "content_type": "video/mp4",
    "size": 12345678
  },
  "message": "Completed",
  "video_url": "https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video/result/task_router_id"
}
```

For callers, the status API only returns `queued`, `processing`, or `success`. Worker connection failures and temporary ComfyUI failures stay internal and are retried by Router.

### 下载结果视频

当状态为 `success` 后，使用 `video_url` 下载结果：

```bash
curl -L "https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video/result/task_router_id" \
  -o output.mp4
```

结果路径可以传 `task_id`，也兼容该任务对应的 `prompt_id`。Router 会直接读取 `ROUTER_RESULT_DIR` 中已保存的视频返回给客户端，不再要求下载时 Worker 在线。

## 使用测试客户端

`test_client.py` 会提交任务并轮询状态。使用前需要准备测试图片和音频文件，或修改脚本中的文件路径：

```python
IMAGE_FILE_PATH = "d389bfdc4302270e9f1542fdf5a7c59d.jpg"
AUDIO_FILE_PATH = "output.mp3"
```

默认会请求公网 Router。如需显式指定：

```bash
export INFINITETALK_API_BASE_URL="https://dongdongkc.shierkeji.com:6205"
python test_client.py
```

## workflow 配置说明

Worker 会加载 `WORKFLOW_FILE` 指向的 ComfyUI API JSON，并在每次提交时写入以下节点：

- 图片节点：`IMAGE_NODE_ID` 的 `inputs.image`
- 音频节点：`AUDIO_NODE_ID` 的 `inputs.audio`
- 宽度节点：`WIDTH_NODE_ID` 的 `inputs.value`
- 高度节点：`HEIGHT_NODE_ID` 的 `inputs.value`

如果你替换了 workflow，通常需要同步修改这些节点 ID。否则 Worker 会返回 `workflow missing node ...`。

## 部署建议

典型部署方式：

```text
Client -> Router 公网机器 -> Worker 内网/GPU 机器 -> ComfyUI
```

- Router 暴露 HTTP/HTTPS REST API 和 `/ws/workers`。
- Worker 主动连 Router，不需要公网入站端口。
- ComfyUI 只需要 Worker 能访问，不建议直接暴露公网。
- 多个 Worker 可以使用不同的 `WORKER_ID` 同时连接同一个 Router。
- Router 队列目录和结果目录都需要有足够磁盘空间，建议把 `ROUTER_QUEUE_DIR` 和 `ROUTER_RESULT_DIR` 放在可监控、可清理的本地数据盘。
- 当前 Router 的任务队列和结果索引保存在内存中；如果 Router 重启，未提交的 `task_id` 会丢失，已落盘结果也暂时没有索引恢复能力。队列目录和结果目录可能留下孤儿文件，需要运维清理或后续接入持久化任务索引。

## 常见问题

### 提交任务返回 503

说明 Router 本地队列已满。可以先检查队列和 Worker 状态：

```bash
curl https://dongdongkc.shierkeji.com:6205/api/v1/workers
curl https://dongdongkc.shierkeji.com:6205/api/v1/queue
```

如果任务长时间堆积，确认 Worker 是否已经启动、`ROUTER_WS_URL` 是否正确、`WORKER_TOKEN` 是否一致。

### 任务一直 queued

说明 Router 已经接收上传，但还没有可用 Worker 成功接收提交。重点检查：

- `/api/v1/workers` 是否能看到在线 Worker。
- Worker 是否处于 `idle`。
- Worker 端日志是否有 WebSocket 断开、ComfyUI 不可达或 workflow 错误。
- `ROUTER_SUBMIT_RETRY_SECONDS` 是否设置得过大。

### 任务一直 processing 或 fetching_result

`processing` 表示 Worker 已经提交到 ComfyUI，Router 正在等待生成完成。`fetching_result` 表示 ComfyUI 已完成，Router 正在把视频从 Worker 拉回 `ROUTER_RESULT_DIR`。如果长时间不变，重点检查 Worker 到 ComfyUI 的连接、ComfyUI 生成日志、Router 到 Worker 的 WebSocket 稳定性，以及 Router 结果目录磁盘空间。

### 查询状态返回 404

说明 Router 中没有这个 `task_id` 或 `prompt_id` 的记录。常见原因：

- 未配置 `DATABASE_URL` 时，Router 重启过；配置数据库后任务状态会从 PostgreSQL 恢复。
- `PROMPT_ROUTE_TTL_HOURS` 已过期。
- 查询的 ID 不是当前 Router 提交返回的 `task_id`，也不是已提交成功后的 `prompt_id`。

### 下载返回 result not ready

说明任务还没有进入 `success`，或者 Router 还在保存结果文件。继续轮询状态接口，等响应中出现 `video_url` 后再下载。

### Worker 启动失败，提示 workflow file not found

检查 `WORKFLOW_FILE` 路径是否存在。默认路径是：

```bash
./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json
```

### Worker 提交失败，提示 workflow missing node

当前 workflow 中没有 README 配置的节点 ID。请打开 ComfyUI 导出的 API JSON，确认图片、音频、宽高和视频输出节点 ID，并通过环境变量覆盖。

### 上传过大返回 413

默认上传上限是 `200MB`。可以在 Router 启动前调整：

```bash
export MAX_UPLOAD_MB="500"
python router_app.py
```

## 旧单体入口说明

`app.py` 是早期把 REST API、文件接收和 ComfyUI 调用放在同一个进程里的示例入口。当前推荐使用 `router_app.py` + `worker_app.py` 的拆分版架构。