# InfiniteTalk Router / Worker 使用说明

这是一个面向 InfiniteTalk / ComfyUI 工作流的轻量 API 网关项目。项目将公网入口和 GPU 执行端拆成两个程序：

- `router_app.py`：Router / Gateway，对外提供 REST API，接收客户端上传，并把上传流转发给 Worker。
- `worker_app.py`：ComfyUI Worker，主动连接 Router，接收图片和音频，调用本机或内网 ComfyUI。

Router 不保存上传文件和结果文件，只维护 `prompt_id -> worker_id` 的临时路由关系。客户端拿到的任务 ID 就是 ComfyUI 返回的 `prompt_id`。

## 目录结构

```text
.
├── router_app.py              # Router 启动入口
├── worker_app.py              # Worker 启动入口
├── test_client.py             # 简单接口测试客户端
├── common/                    # Router 和 Worker 共用协议、配置
├── router/                    # REST API、WebSocket、Worker 池、上传流转发
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

以下示例适合 Router、Worker、ComfyUI 都在同一台机器上的本地调试。

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
export PUBLIC_BASE_URL="http://127.0.0.1:38349"

python router_app.py
```

Router 默认监听 `127.0.0.1:38349`。

生产或公网部署时通常需要改成：

```bash
export ROUTER_HOST="0.0.0.0"
export PUBLIC_BASE_URL="https://你的公网域名"
python router_app.py
```

### 3. 启动 Worker

另开一个终端：

```bash
export WORKER_TOKEN="change-me"
export WORKER_ID="gpu-worker-01"
export ROUTER_WS_URL="ws://127.0.0.1:38349/ws/workers"
export COMFYUI_BASE_URL="http://127.0.0.1:8080"
export WORKFLOW_FILE="./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json"

python worker_app.py
```

`WORKER_TOKEN` 必须和 Router 一致。Worker 会主动连接 Router 的 `/ws/workers`，因此 Worker 所在机器不需要暴露公网端口。

### 4. 检查连接状态

```bash
curl http://127.0.0.1:38349/healthz
curl http://127.0.0.1:38349/api/v1/workers
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
| `PUBLIC_BASE_URL` | `http://127.0.0.1:38349` | 返回给客户端的视频下载 URL 前缀 |
| `WORKER_TOKEN` | `change-me` | Worker 接入鉴权 token |
| `MAX_UPLOAD_MB` | `200` | 单次上传最大体积，包含 multipart 请求体 |
| `UPLOAD_STREAM_CHUNK_BYTES` | `262144` | 上传流分片大小预留配置 |
| `WORKER_ASSIGN_TIMEOUT_SECONDS` | `10.0` | Worker 分配超时预留配置 |
| `WORKER_RPC_TIMEOUT_SECONDS` | `30.0` | Router 等待 Worker RPC 响应超时 |
| `PROMPT_ROUTE_TTL_HOURS` | `24.0` | `prompt_id -> worker_id` 路由映射保留时间 |

### Worker 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ROUTER_WS_URL` | `ws://127.0.0.1:38349/ws/workers` | Router Worker WebSocket 地址 |
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
curl -X POST "http://127.0.0.1:38349/api/v1/predict_talking_video" \
  -F "image=@./input.jpg" \
  -F "audio=@./input.wav" \
  -F "width=480" \
  -F "height=640"
```

成功响应：

```json
{
  "status": "submitted",
  "prompt_id": "comfy_prompt_id",
  "status_url": "/api/v1/predict_talking_video/status/comfy_prompt_id"
}
```

说明：

- 请求类型必须是 `multipart/form-data`。
- `image` 和 `audio` 必填。
- `width` 默认 `480`，`height` 默认 `640`。
- Router 会等待 Worker 成功提交到 ComfyUI 并拿到 `prompt_id` 后再响应。
- 如果当前没有空闲 Worker，会返回 `503`。

### 查询任务状态

```bash
curl "http://127.0.0.1:38349/api/v1/predict_talking_video/status/comfy_prompt_id"
```

生成中：

```json
{
  "status": "processing",
  "prompt_id": "comfy_prompt_id",
  "message": "任务仍在队列或生成中"
}
```

生成成功：

```json
{
  "status": "success",
  "prompt_id": "comfy_prompt_id",
  "video_url": "http://127.0.0.1:38349/api/v1/predict_talking_video/result/comfy_prompt_id"
}
```

生成失败：

```json
{
  "status": "error",
  "prompt_id": "comfy_prompt_id",
  "message": "ComfyUI 任务状态异常: error",
  "detail": {}
}
```

### 下载结果视频

当状态为 `success` 后，使用 `video_url` 下载结果：

```bash
curl -L "http://127.0.0.1:38349/api/v1/predict_talking_video/result/comfy_prompt_id" \
  -o output.mp4
```

Router 会向对应 Worker 请求结果文件，并将视频内容流式返回给客户端。

## 使用测试客户端

`test_client.py` 会提交任务并轮询状态。使用前需要准备测试图片和音频文件，或修改脚本中的文件路径：

```python
IMAGE_FILE_PATH = "d389bfdc4302270e9f1542fdf5a7c59d.jpg"
AUDIO_FILE_PATH = "output.mp3"
```

运行本地 Router 时：

```bash
export INFINITETALK_API_BASE_URL="http://127.0.0.1:38349"
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
- 当前 Router 的 `prompt_id -> worker_id` 映射保存在内存中；如果 Router 重启，旧 `prompt_id` 将无法继续路由查询和下载。

## 常见问题

### 提交任务返回 503

说明 Router 当前没有可用 Worker。检查：

```bash
curl http://127.0.0.1:38349/api/v1/workers
```

确认 Worker 是否已经启动、`ROUTER_WS_URL` 是否正确、`WORKER_TOKEN` 是否一致。

### 查询状态返回 404

说明 Router 中没有这个 `prompt_id` 的路由记录。常见原因：

- Router 重启过。
- `PROMPT_ROUTE_TTL_HOURS` 已过期。
- 查询的 `prompt_id` 不是当前 Router 提交返回的 ID。

### 查询或下载返回 worker offline

说明负责该 `prompt_id` 的 Worker 已断开。需要恢复同一个 `WORKER_ID` 的 Worker 连接后再试。

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