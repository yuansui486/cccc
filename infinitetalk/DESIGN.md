# InfiniteTalk 双程序轻量路由设计文档

## 1. 设计目标

当前 [app.py](app.py) 同时负责对外 REST API、文件接收、ComfyUI 调用和状态查询。拆分后只保留两个程序：

| 程序 | 暂定名称 | 部署位置 | 是否暴露公网 | 核心职责 |
| --- | --- | --- | --- | --- |
| 程序 1 | Router / Gateway | 公网机器 | 是 | 对外提供 REST API；接收客户端上传到本地队列；后台持续寻找可用 Worker 执行；把结果文件保存到 Router；对客户端交付状态和结果 |
| 程序 2 | ComfyUI Worker | 内网或 GPU 机器 | 否 | 主动连接 Router；接收上传流；调用 ComfyUI；拿到 `prompt_id` 后回传；后续按 `prompt_id` 查询 ComfyUI 状态和结果 |

核心原则：

- 程序 1 对公网请求要优先“可靠接收”，Worker 短暂离线或忙碌时不直接拒绝上传。
- 程序 1 生成 Router 侧 `task_id`，用于排队、重试和提交前状态查询。
- 程序 1 暂存待提交输入文件，并在生成成功后保存结果文件；任务完成或失败后清理输入文件。
- 程序 1 维护任务状态：receiving / queued / submitting / processing / fetching_result / success / error。
- 程序 1 在 Worker 返回 `prompt_id` 后继续轮询结果，成功后把结果拉回 `ROUTER_RESULT_DIR`。
- 客户端使用 `task_id` 查询和下载；任务提交到 ComfyUI 后，状态响应会同时包含真实 `prompt_id`。
- 程序 2 主动连接程序 1，程序 1 不需要访问程序 2 的公网地址。
- ComfyUI 只需要被程序 2 访问，不对公网开放。

## 2. 总体架构

```text
Client
  |
  | HTTPS REST multipart upload stream
  v
Program 1: Router / Gateway
  |  - POST /api/v1/predict_talking_video
  |  - GET  /api/v1/predict_talking_video/status/{task_id_or_prompt_id}
  |  - GET  /api/v1/predict_talking_video/result/{task_id_or_prompt_id}
  |  - WS   /ws/workers
  |
  | WebSocket stream, worker 主动连接
  v
Program 2: ComfyUI Worker
  |
  | HTTP / WebSocket, 仅内网
  v
ComfyUI
```

程序 1 只做六件事：

1. 接收客户端请求。
2. 把待提交输入暂存到本地队列，并立即返回 `task_id`。
3. 后台调度器持续寻找当前可用的 Worker。
4. 把队列中的输入提交给 Worker，并在连接中断时重新排队重试。
5. 在 Worker 返回 `prompt_id` 后，继续通过 Worker 查询 ComfyUI 进度。
6. 成功后把结果文件从 Worker 拉回 Router 本地保存，并由 Router 对客户端交付下载。

除此之外，程序 1 不复制 ComfyUI history，不要求客户端在下载时依赖 Worker 在线。

## 3. 对外 REST API

### 3.1 提交生成请求

`POST /api/v1/predict_talking_video`

请求类型：`multipart/form-data`

字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| image | file | 是 | 输入人物图片 |
| audio | file | 是 | 对口型音频 |
| width | int | 否 | 默认 480 |
| height | int | 否 | 默认 640 |

处理方式：

1. Router 检查本地队列是否还有容量。
2. Router 使用流式 multipart 解析客户端请求体，并把图片、音频写入 `ROUTER_QUEUE_DIR`。
3. Router 创建 `task_id`，记录任务为 `queued`，并向客户端返回排队响应。
4. 后台 dispatcher 持续查找 `idle` Worker。
5. 找到 Worker 后，Router 临时占用该 Worker，并把队列文件通过 WebSocket 分片发给 Worker。
6. 如果发送过程中 Worker 断开、无可用 Worker 或等待响应超时，Router 把任务重新置为 `queued`，稍后重试。
7. Worker 在本机写入临时文件，并在收齐输入后调用 ComfyUI。
8. Worker 调用 ComfyUI `/prompt` 成功后，把 ComfyUI 返回的 `prompt_id` 发回 Router。
9. Router 记录 `task_id -> prompt_id`，并保持该 Worker busy，持续查询任务状态。
10. ComfyUI 生成成功后，Router 请求 Worker 打开结果流，把视频保存到 `ROUTER_RESULT_DIR`。
11. Router 标记任务为 `success`，清理 Router 本地输入文件，后续由 Router 本地文件系统交付下载。

成功响应：

```json
{
  "status": "queued",
  "task_id": "task_router_id",
  "status_url": "/api/v1/predict_talking_video/status/task_router_id",
  "message": "Queued"
}
```

队列已满：

```json
{
  "detail": "router queue is full"
}
```

说明：

- 提交接口是“接收后立即排队”的异步网关，响应中先返回 `task_id`。
- 如果客户端上传中断，本次请求失败，不产生可查询任务。
- 如果 Worker 断开，任务保持可查询，并由 Router 后台继续重试提交。
- 如果 Worker 或 ComfyUI 暂时失败，Router 重新排队并继续重试，不向调用方返回失败状态。
- Worker 在任务完成并且结果保存到 Router 前保持 busy，避免一个 Worker 同时承担多个生成任务。

### 3.2 查询状态

`GET /api/v1/predict_talking_video/status/{task_id_or_prompt_id}`

处理方式：

1. Router 根据 `task_id` 查找本地任务；也可以通过已知 `prompt_id` 反查任务。
2. Router 把内部状态映射为三个对外状态：`queued`、`processing`、`success`。
3. 如果任务已 success，Router 返回本地结果元信息和 `video_url`。
4. 如果映射不存在，返回 `404`。

排队中响应：

```json
{
  "status": "queued",
  "task_id": "task_router_id",
  "attempts": 0,
  "message": "Queued"
}
```

处理中响应：

```json
{
  "status": "processing",
  "task_id": "task_router_id",
  "prompt_id": "comfy_prompt_id",
  "message": "Processing"
}
```

保存结果中响应：

```json
{
  "status": "processing",
  "task_id": "task_router_id",
  "prompt_id": "comfy_prompt_id",
  "message": "Processing"
}
```

成功响应：

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
  "video_url": "https://api.example.com/api/v1/predict_talking_video/result/task_router_id"
}
```

调用方不会看到 `error` 或内部状态名。Router 会把可重试失败保持为 `queued` 或 `processing`，继续后台重试。

### 3.3 下载结果

`GET /api/v1/predict_talking_video/result/{task_id_or_prompt_id}`

推荐实现：

1. Router 根据 `task_id` 查找本地任务，也可以通过该任务的 `prompt_id` 反查。
2. 如果任务不是 `success`，返回 `409 result not ready`。
3. 如果任务是 `success`，Router 读取 `ROUTER_RESULT_DIR` 中的本地结果文件并返回 HTTP 响应。

这样下载不依赖 Worker 在线，Router 是对外结果交付方。

如果后续需要支持长期保存、多 Router 实例或断点下载，可以把 `ROUTER_RESULT_DIR` 替换成对象存储，并把任务索引放到 Redis 或数据库。

## 4. Router 状态设计

程序 1 的业务状态有两部分：

```text
task_id -> queued task state / prompt_id
prompt_id -> worker_id / worker_connection
```

待提交任务结构：

| 字段 | 说明 |
| --- | --- |
| task_id | Router 生成的任务 ID |
| status | receiving / queued / submitting / processing / fetching_result / success / error |
| params | width、height 等表单参数 |
| files | Router 本地待提交输入文件路径和元信息 |
| attempts | 已尝试提交次数 |
| last_error | 最近一次可见错误 |
| prompt_id | ComfyUI 返回的任务 ID，提交成功后写入 |
| worker_id | 最近一次提交使用的 Worker |
| result | Router 本地结果文件路径、文件名、类型和大小 |

状态取值：

| 状态 | 说明 |
| --- | --- |
| receiving | Router 正在接收上传 |
| queued | 等待空闲 Worker |
| submitting | 正在把输入提交给 Worker |
| processing | Worker/ComfyUI 正在生成 |
| fetching_result | Router 正在从 Worker 拉取并保存结果 |
| success | 结果已保存在 Router |
| error | 任务失败 |

任务成功或失败后，Router 删除本地输入文件；成功任务会保留结果文件和轻量 task 状态到 TTL。

Prompt 路由表：

```text
prompt_id -> worker_id / worker_connection
```

建议结构：

| 字段 | 说明 |
| --- | --- |
| prompt_id | ComfyUI 返回的任务 ID |
| worker_id | 当前负责该 prompt 的 Worker |
| created_at | 记录映射的时间，用于 TTL 清理 |
| last_accessed_at | 最近查询时间，用于 TTL 清理 |

说明：

- `worker_connection` 可以通过 `worker_id` 从在线连接表中找到。
- 在线连接表是 WebSocket 连接管理所需的运行时信息，不是业务任务状态。
- `prompt_id` 反查映射可以先放内存；如果 Router 多实例部署，再放 Redis 或数据库。
- 映射应设置 TTL，例如 24 小时或 48 小时。
- TTL 到期后，状态查询返回 `404`，客户端需要重新提交或使用其他业务侧记录。

Router 不维护以下内容：

- 不保存 ComfyUI history 内容。
- 不长期保存已提交输入文件。
- 不对 ComfyUI 业务错误做无限重试。

## 5. Worker 连接管理

Router 仍然需要维护 Worker 在线连接表，用来选择可用 Worker 和路由请求。

| 字段 | 说明 |
| --- | --- |
| worker_id | Worker 标识 |
| ws | WebSocket 连接对象 |
| status | idle / busy / offline |
| capabilities | Worker 能力，例如 GPU、模型版本、最大分辨率 |
| last_heartbeat_at | 最近心跳时间 |

这张表只是连接池，不是任务状态库。

第一版调度策略：

- 如果有 `idle` Worker，dispatcher 提交队列任务前临时占用它。
- 如果没有 `idle` Worker，任务保持 `queued`，稍后继续尝试。
- 一个 Worker 同一时间只处理一个提交请求，避免上传流交叉。
- 拿到 `prompt_id` 后，Worker 可以恢复 `idle`，因为后续 ComfyUI 任务已经提交；状态查询和结果下载按 `prompt_id` 路由回这个 Worker。
- 如果 Worker 在 ComfyUI 生成期间不能并发处理新任务，可以让 Worker 自己保持 `busy`，直到 ComfyUI 完成。

## 6. Worker 职责

Worker 是真正执行端，负责：

- 主动连接 Router 的 `/ws/workers`。
- 接收 Router 转发的上传流。
- 在本机保存输入临时文件。
- 读取工作流 JSON，并注入图片、音频、宽、高参数。
- 调用 ComfyUI `/upload/image` 上传输入文件。
- 调用 ComfyUI `/prompt` 提交任务。
- 把 ComfyUI 返回的 `prompt_id` 发回 Router。
- 后续按 Router 请求，查询 ComfyUI `/history/{prompt_id}`。
- 后续按 Router 请求，读取或下载生成结果并流式回传。
- 任务结束后清理 Worker 本机临时文件。

Worker 本地目录建议：

```text
worker_data/
  inflight/
    current/
      image.jpg
      audio.wav
  comfy_results_cache/
  workflows/
    kijai-wanvideo_I2V_InfiniteTalk_example_01.json
```

当前 [app.py](app.py) 中配置的 workflow 文件名大小写与根目录实际文件名不一致。拆分时建议统一成现有文件名：`kijai-wanvideo_I2V_InfiniteTalk_example_01.json`。

## 7. WebSocket 协议

WebSocket 使用“JSON 控制消息 + 二进制分片”。上传和下载都通过 binary frame 传输文件内容，避免 base64 体积膨胀。

### 7.1 Worker 注册

Worker 连接：

```text
GET /ws/workers?worker_id=gpu-worker-01&token=change-me
```

连接成功后 Worker 发送：

```json
{
  "type": "worker.register",
  "worker_id": "gpu-worker-01",
  "capabilities": {
    "kind": "infinitetalk",
    "gpu": "RTX 4090",
    "max_width": 1280,
    "max_height": 1280
  }
}
```

Router 回复：

```json
{
  "type": "worker.registered",
  "worker_id": "gpu-worker-01"
}
```

### 7.2 心跳

Worker 每 20 到 30 秒发送：

```json
{
  "type": "heartbeat"
}
```

Router 回复：

```json
{
  "type": "heartbeat.ack"
}
```

### 7.3 提交流程

Router 选中一个空闲 Worker 后，发送：

```json
{
  "type": "submit.start"
}
```

Worker 回复：

```json
{
  "type": "submit.accepted"
}
```

Router 解析到表单参数时发送：

```json
{
  "type": "submit.params",
  "params": {
    "width": 480,
    "height": 640
  }
}
```

Router 解析到文件 part 时发送：

```json
{
  "type": "file.start",
  "file_id": "image",
  "filename": "input.jpg",
  "content_type": "image/jpeg"
}
```

随后每个 chunk 发送一条元数据：

```json
{
  "type": "file.chunk",
  "file_id": "image",
  "chunk_index": 0,
  "offset": 0,
  "size": 262144
}
```

紧跟一个 binary frame，内容就是该 chunk 的原始字节。

单个文件结束后发送：

```json
{
  "type": "file.end",
  "file_id": "image",
  "size": 123456,
  "sha256": "..."
}
```

整个上传请求解析完成后发送：

```json
{
  "type": "submit.complete"
}
```

Worker 收齐输入、提交 ComfyUI 成功后回复：

```json
{
  "type": "submit.result",
  "status": "submitted",
  "prompt_id": "comfy_prompt_id"
}
```

Router 收到 `prompt_id` 后把任务标记为 `processing`，后续继续在后台查询和拉取结果。客户端已经在提交阶段拿到 `task_id`，不需要等待这个 WebSocket 往返完成。

提交失败时 Worker 回复：

```json
{
  "type": "submit.error",
  "message": "Failed to submit to ComfyUI",
  "detail": {
    "stage": "queue_prompt",
    "reason": "..."
  }
}
```

### 7.4 状态查询流程

Router 后台 dispatcher 在任务进入 `processing` 后，向当前 Worker 发送：

```json
{
  "type": "status.query",
  "rpc_id": "rpc_001",
  "prompt_id": "comfy_prompt_id"
}
```

Worker 查询 ComfyUI 后回复：

```json
{
  "type": "status.result",
  "rpc_id": "rpc_001",
  "prompt_id": "comfy_prompt_id",
  "status": "processing",
  "message": "Task is still queued or processing"
}
```

Router 用返回值更新本地 `task_id` 状态。外部 `GET /status/{task_id_or_prompt_id}` 只读取 Router 本地任务状态，不在 HTTP 请求内临时访问 Worker。`rpc_id` 只用于一次 WebSocket 往返匹配，不对外暴露，不入库，不作为业务状态。

### 7.5 结果保存流程

Router 后台确认 ComfyUI 成功后，向当前 Worker 发送：

```json
{
  "type": "result.open",
  "rpc_id": "rpc_002",
  "prompt_id": "comfy_prompt_id"
}
```

Worker 找到结果后发送：

```json
{
  "type": "result.start",
  "rpc_id": "rpc_002",
  "prompt_id": "comfy_prompt_id",
  "filename": "output.mp4",
  "content_type": "video/mp4",
  "size": 9876543,
  "sha256": "..."
}
```

随后 Worker 发送 `result.chunk` 元数据和 binary frame。Router 把 binary frame 写入 `ROUTER_RESULT_DIR` 下的临时文件，收到 `result.end` 后原子替换成最终结果文件，并把任务标记为 `success`。

完成后 Worker 发送：

```json
{
  "type": "result.end",
  "rpc_id": "rpc_002",
  "prompt_id": "comfy_prompt_id"
}
```

## 8. FastAPI 实现注意事项

Router 需要自己控制上传落盘位置和队列容量，上传接口不能继续写成：

```python
async def generate_talking_video(
    image: UploadFile = File(...),
    audio: UploadFile = File(...),
    width: int = Form(480),
    height: int = Form(640),
):
    ...
```

原因是 `UploadFile` / `File(...)` / `request.form()` 可能把大文件写入 `SpooledTemporaryFile` 或系统临时目录，绕过 `ROUTER_QUEUE_DIR` 的容量管理和清理策略。

建议实现方式：

- 路由接收 `Request` 对象。
- 在读取 `request.stream()` 之前先检查 Router 队列容量。
- 使用 `async for chunk in request.stream()` 读取原始请求体。
- 使用支持流式回调的 multipart parser，例如 `streaming-form-data`，或基于 `python-multipart` 的低层流式解析能力。
- parser 每解析到文件数据 chunk，就写入 Router 队列目录下的当前 task 文件。
- 对 `width`、`height` 这种小字段保存在 task 元信息中。
- 结果流也写入 Router 管理的 `ROUTER_RESULT_DIR`，并使用 `.part` 临时文件避免半成品被下载。
- 通过反向代理时建议关闭请求体缓冲，例如 Nginx 配置 `proxy_request_buffering off;`，减少额外落盘和延迟。

## 9. 安全与限制

### 9.1 公网入口

- Router 必须使用 HTTPS。
- REST API 建议加入调用方鉴权，例如 API Key、JWT 或内部业务 token。
- 上传文件限制大小和类型，例如图片只允许 jpeg/png/webp，音频只允许 wav/mp3/m4a。
- 对 `width`、`height` 做范围限制，防止异常参数拖垮 GPU。
- Router 要在流式读取过程中累计大小，超过限制立即中止 HTTP 请求并清理当前 task 目录。
- Router 和 Worker 的临时文件路径都由各自服务端生成，不信任客户端文件名。

### 9.2 Worker WebSocket

- 使用 `wss://`。
- Worker 使用固定 token 或 mTLS 认证。
- Router 只接受白名单 Worker ID。
- 心跳超时后关闭连接，并移除该 Worker 的在线连接。
- Worker 断开后，如果任务还没有保存结果，Router 可以把任务重新排队并稍后重试；如果结果已保存，下载不再依赖 Worker 在线。

### 9.3 ComfyUI

- ComfyUI 仅监听 Worker 本机或内网地址。
- 不在公网开放 ComfyUI 管理界面。
- Worker 不把 ComfyUI 内网 URL 直接暴露给客户端。

## 10. 异常处理

| 场景 | 处理方式 |
| --- | --- |
| 没有空闲 Worker | Router 先接收上传并返回 `task_id`，任务保持 `queued`，后台持续重试 |
| Router 队列已满 | 返回 `503`，客户端稍后重试提交 |
| 客户端上传中断 | Router 清理当前接收目录；不产生 `task_id` |
| Worker 接收上传时断开 | Router 把任务重新置为 `queued`，稍后重试提交 |
| Worker 提交 ComfyUI 失败 | Router 将任务重新置为 `queued`，稍后继续重试；调用方仍看到 `queued` 或 `processing` |
| Worker 返回 `prompt_id` 后断开但结果未保存 | Router 把任务重新置为 `queued`，稍后重新调用 Worker 完成任务 |
| Worker 返回 `prompt_id` 后断开且结果已保存 | 下载继续从 Router 本地结果文件返回 |
| `task_id` 或 `prompt_id` 映射不存在 | 返回 `404` |
| ComfyUI 任务失败 | Router 将任务重新置为 `queued`，稍后继续重试；调用方不会看到 `error` |
| Router 拉取结果流中断 | 任务重新排队，稍后重试执行或拉取结果 |
| 客户端下载中断 | Router 本地结果文件保留，客户端可重试下载 |

## 11. 建议代码拆分

```text
infinitetalk/
  router_app.py
  worker_app.py
  common/
    protocol.py              # WebSocket 消息类型、校验模型
    settings.py              # 环境变量配置
  router/
    api.py                   # REST routes
    job_queue.py             # Router 任务队列、Worker 调度、结果落盘
    streaming_upload.py      # 旧版 HTTP multipart 流式解析与 WS 转发
    ws.py                    # Worker WebSocket endpoint
    worker_pool.py           # Worker 连接池
    prompt_routes.py         # prompt_id -> worker 映射
  worker/
    client.py                # 连接 Router WS
    receiver.py              # 接收上传流并写入 Worker 临时目录
    comfyui.py               # ComfyUI HTTP API 封装
    workflow.py              # workflow JSON 注入
    runner.py                # 提交 ComfyUI 与查询结果
  workflows/
    kijai-wanvideo_I2V_InfiniteTalk_example_01.json
  tests/
    test_router_streaming_upload.py
    test_prompt_routes.py
    test_worker_comfyui.py
```

第一版也可以只实现 `router_app.py` 和 `worker_app.py`，但 Router 上传路由必须按流式解析实现，不能使用 `UploadFile`。

## 12. 配置建议

### Public Endpoint

程序 1 会替代当前 `dongdongkc.shierkeji.com:6205` 端口，因此公网入口固定为：

```text
https://dongdongkc.shierkeji.com:6205
```

Router 进程本身建议只监听本机 HTTP 端口，例如 `127.0.0.1:38349`，由 Nginx 在 `6205` 上负责 SSL、上传流透传和 WebSocket 代理。

### Client 设置

客户端提交地址：

```text
https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video
```

客户端状态查询地址模板：

```text
https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video/status/{task_id_or_prompt_id}
```

当前 [test_client.py](test_client.py) 建议使用环境变量覆盖 API 地址：

```bash
export INFINITETALK_API_BASE_URL="https://dongdongkc.shierkeji.com:6205"
python test_client.py
```

本地调试时可以改成：

```bash
export INFINITETALK_API_BASE_URL="http://127.0.0.1:38349"
python test_client.py
```

### Router `.env`

```env
ROUTER_HOST=127.0.0.1
ROUTER_PORT=38349
PUBLIC_BASE_URL=https://dongdongkc.shierkeji.com:6205
WORKER_TOKEN=change-me
MAX_UPLOAD_MB=200
UPLOAD_STREAM_CHUNK_BYTES=262144
WORKER_ASSIGN_TIMEOUT_SECONDS=10
WORKER_RPC_TIMEOUT_SECONDS=30
PROMPT_ROUTE_TTL_HOURS=24
ROUTER_QUEUE_DIR=./router_data/queued_jobs
ROUTER_RESULT_DIR=./router_data/results
ROUTER_QUEUE_MAX_JOBS=128
ROUTER_SUBMIT_RETRY_SECONDS=5
ROUTER_STATUS_POLL_SECONDS=2
```

### Worker `.env`

```env
ROUTER_WS_URL=wss://dongdongkc.shierkeji.com:6205/ws/workers
WORKER_ID=gpu-worker-01
WORKER_TOKEN=change-me
COMFYUI_BASE_URL=http://127.0.0.1:8080
WORKFLOW_FILE=./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json
IMAGE_NODE_ID=284
AUDIO_NODE_ID=125
VIDEO_NODE_ID=131
WIDTH_NODE_ID=245
HEIGHT_NODE_ID=246
POLL_INTERVAL_SECONDS=2
POLL_TIMEOUT_SECONDS=600
WORKER_TMP_DIR=./worker_data/inflight
```

### Nginx 配置示例

这个配置保留你现有的证书路径和 `6205` 端口，把请求代理到本机 Router。建议保留 `proxy_request_buffering off;`，让 Router 尽早开始接收并写入自己的队列目录，减少代理层额外缓存和长上传超时风险。

下面给出只需要放入 `server` 块的写法；这里直接代理到本机 Router，不再单独定义 `map` 和 `upstream`。

```nginx
server {
  listen 6205 ssl;
  server_name dongdongkc.shierkeji.com;

  ssl_certificate /opt/ssl/dongdongkc.shierkeji.com.pem;
  ssl_certificate_key /opt/ssl/dongdongkc.shierkeji.com.key;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_ciphers HIGH:!aNULL:!MD5;
  ssl_prefer_server_ciphers on;
  ssl_session_cache shared:SSL:1m;
  ssl_session_timeout 5m;

  client_max_body_size 200m;

  location /ws/workers {
    proxy_pass http://127.0.0.1:38349;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Port 6205;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
    proxy_buffering off;
  }

  location / {
    proxy_pass http://127.0.0.1:38349;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Port 6205;

    proxy_request_buffering off;
    proxy_buffering off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
  }
}
```

如果这个 `server` 位于某个统一认证网关后面，还需要确认统一认证层的大文件限制和超时设置合理。当前 Router 会把待提交输入落到 `ROUTER_QUEUE_DIR`，因此不再要求反向代理完全不落盘，但仍建议关闭不必要的上游缓冲以降低公网请求的端到端延迟。

## 13. 第一版实现范围

第一版只做这些能力：

- 一个 Router。
- 一个或多个 Worker 主动连接 Router。
- Router 提交请求时先接收上传并返回 `task_id`。
- Router 本地维护轻量队列状态，并把待提交输入暂存在 `ROUTER_QUEUE_DIR`。
- Router 后台 dispatcher 持续选择当前空闲 Worker，没有就等待重试。
- Router 在 Worker 成功提交 ComfyUI 并返回 `prompt_id` 后继续等待结果。
- Router 把成功结果保存到 `ROUTER_RESULT_DIR` 后，清理本地输入文件。
- Router 维护 `task_id -> prompt_id` 映射和结果文件元信息。
- 状态查询可以通过 `task_id` 或 `prompt_id`；结果下载从 Router 本地结果目录返回。
- Worker 负责所有 ComfyUI 交互。
- 客户端仍使用当前提交接口和状态查询接口，只是状态路径里的值变成真实 ComfyUI `prompt_id`。

后续如果需要 Router 重启后恢复未提交任务、多实例调度或长期结果保存，再单独引入 Redis、数据库、对象存储或专用任务队列。第一版只做单 Router 的本地可靠排队和本地结果交付。
