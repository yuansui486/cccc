# InfiniteTalk 双程序轻量路由设计文档

## 1. 设计目标

当前 [app.py](app.py) 同时负责对外 REST API、文件接收、ComfyUI 调用和状态查询。拆分后只保留两个程序：

| 程序 | 暂定名称 | 部署位置 | 是否暴露公网 | 核心职责 |
| --- | --- | --- | --- | --- |
| 程序 1 | Router / Gateway | 公网机器 | 是 | 对外提供 REST API；接收客户端上传流；通过 WebSocket 直接转发给 Worker；记录 `prompt_id -> worker` 映射；把状态查询路由到对应 Worker |
| 程序 2 | ComfyUI Worker | 内网或 GPU 机器 | 否 | 主动连接 Router；接收上传流；调用 ComfyUI；拿到 `prompt_id` 后回传；后续按 `prompt_id` 查询 ComfyUI 状态和结果 |

核心原则：

- 程序 1 只是轻量路由层，不是任务系统。
- 程序 1 不生成自己的任务 ID。
- 程序 1 不保存上传文件，不完整缓存上传文件。
- 程序 1 不维护复杂任务状态机。
- 程序 1 唯一业务状态是 `prompt_id -> worker` 映射关系。
- 客户端最终拿到和查询的唯一任务标识就是 ComfyUI 返回的 `prompt_id`。
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
  |  - GET  /api/v1/predict_talking_video/status/{prompt_id}
  |  - GET  /api/v1/predict_talking_video/result/{prompt_id}
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

程序 1 只做四件事：

1. 接收客户端请求。
2. 找一个当前可用的 Worker。
3. 把请求流和后续查询转发给这个 Worker。
4. 在 Worker 返回 `prompt_id` 后，记录 `prompt_id -> worker`。

除此之外，程序 1 不关心 ComfyUI 内部进度，不保存输入文件，不保存业务任务详情。

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

1. Router 在读取请求体前检查是否有空闲 Worker。
2. 如果没有空闲 Worker，直接返回 `503 Service Unavailable` 或 `429 Too Many Requests`，不接收上传文件。
3. 如果有空闲 Worker，Router 临时占用该 Worker。
4. Router 使用流式 multipart 解析客户端请求体。
5. Router 解析到图片、音频 chunk 时，立即通过 WebSocket 发给 Worker。
6. Worker 在本机写入临时文件，并在收齐输入后调用 ComfyUI。
7. Worker 调用 ComfyUI `/prompt` 成功后，把 ComfyUI 返回的 `prompt_id` 发回 Router。
8. Router 记录 `prompt_id -> worker` 映射，然后把 `prompt_id` 返回给客户端。

成功响应：

```json
{
  "status": "submitted",
  "prompt_id": "comfy_prompt_id",
  "status_url": "/api/v1/predict_talking_video/status/comfy_prompt_id"
}
```

无可用 Worker：

```json
{
  "status": "error",
  "message": "当前没有可用执行端，请稍后重试"
}
```

提交失败：

```json
{
  "status": "error",
  "message": "提交到执行端或 ComfyUI 失败",
  "detail": {
    "stage": "queue_prompt",
    "reason": "..."
  }
}
```

说明：

- 提交接口不是“接收后立即返回”的异步网关，而是等 Worker 已经拿到 ComfyUI `prompt_id` 后再响应。
- 如果客户端上传中断、Worker 断开、ComfyUI 提交失败，本次请求直接失败，不产生可查询的任务标识。
- Router 不需要保存 width、height、文件名、文件 hash 等业务状态；这些只在当前请求转发过程中临时存在。

### 3.2 查询状态

`GET /api/v1/predict_talking_video/status/{prompt_id}`

处理方式：

1. Router 根据 `prompt_id` 查找对应 Worker。
2. 如果映射不存在，返回 `404`。
3. 如果 Worker 已断开，返回 `503` 或 `502`。
4. 如果 Worker 在线，Router 通过 WebSocket 向该 Worker 发起状态查询。
5. Worker 查询 ComfyUI `/history/{prompt_id}`，把结果返回给 Router。
6. Router 原样转成 REST 响应给客户端。

处理中响应：

```json
{
  "status": "processing",
  "prompt_id": "comfy_prompt_id",
  "message": "任务仍在队列或生成中"
}
```

成功响应：

```json
{
  "status": "success",
  "prompt_id": "comfy_prompt_id",
  "video_url": "https://api.example.com/api/v1/predict_talking_video/result/comfy_prompt_id"
}
```

失败响应：

```json
{
  "status": "error",
  "prompt_id": "comfy_prompt_id",
  "message": "ComfyUI 任务失败",
  "detail": {
    "status_str": "error"
  }
}
```

### 3.3 下载结果

`GET /api/v1/predict_talking_video/result/{prompt_id}`

推荐实现：

1. Router 根据 `prompt_id` 找到 Worker。
2. Router 通过 WebSocket 请求 Worker 读取或下载 ComfyUI 结果文件。
3. Worker 把视频内容通过 WebSocket 分片发回 Router。
4. Router 直接把分片流式写入 HTTP 响应。

这样程序 1 不需要本地保存结果文件，也不需要对象存储。

如果后续需要支持断点下载、长期保存或 Worker 离线后仍可下载，再引入对象存储。但第一版按轻量路由层设计，不引入持久化结果存储。

## 4. Router 状态设计

程序 1 的业务状态只有一张映射表：

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
- `prompt_id` 映射可以先放内存；如果 Router 多实例部署，再放 Redis。
- 映射应设置 TTL，例如 24 小时或 48 小时。
- TTL 到期后，状态查询返回 `404`，客户端需要重新提交或使用其他业务侧记录。

Router 不维护以下内容：

- 不维护自定义任务 ID。
- 不维护 queued / running / success 等本地状态机。
- 不保存上传文件路径。
- 不保存结果文件路径。
- 不保存 ComfyUI history 内容。
- 不做任务重试。

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

- 如果有 `idle` Worker，提交请求开始前临时占用它。
- 如果没有 `idle` Worker，直接拒绝客户端请求。
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

Router 收到 `prompt_id` 后记录映射，并返回 REST 响应给客户端。

提交失败时 Worker 回复：

```json
{
  "type": "submit.error",
  "message": "无法提交到 ComfyUI",
  "detail": {
    "stage": "queue_prompt",
    "reason": "..."
  }
}
```

### 7.4 状态查询流程

Router 收到 `GET /status/{prompt_id}` 后，根据映射找到 Worker，并发送：

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
  "message": "任务仍在队列或生成中"
}
```

`rpc_id` 只用于一次 WebSocket 往返匹配，不对外暴露，不入库，不作为业务状态。

### 7.5 结果下载流程

Router 收到 `GET /result/{prompt_id}` 后，根据映射找到 Worker，并发送：

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

随后 Worker 发送 `result.chunk` 元数据和 binary frame。Router 直接把 binary frame 写入 HTTP streaming response。

完成后 Worker 发送：

```json
{
  "type": "result.end",
  "rpc_id": "rpc_002",
  "prompt_id": "comfy_prompt_id"
}
```

## 8. FastAPI 实现注意事项

要满足“Router 不本地暂存上传文件”，上传接口不能继续写成：

```python
async def generate_talking_video(
    image: UploadFile = File(...),
    audio: UploadFile = File(...),
    width: int = Form(480),
    height: int = Form(640),
):
    ...
```

原因是 `UploadFile` / `File(...)` / `request.form()` 可能把大文件写入 `SpooledTemporaryFile` 或系统临时目录。

建议实现方式：

- 路由接收 `Request` 对象。
- 在读取 `request.stream()` 之前先选择空闲 Worker。
- 使用 `async for chunk in request.stream()` 读取原始请求体。
- 使用支持流式回调的 multipart parser，例如 `streaming-form-data`，或基于 `python-multipart` 的低层流式解析能力。
- parser 每解析到文件数据 chunk，就立即通过 WebSocket 发给 Worker。
- 对 `width`、`height` 这种小字段可以保存在当前请求的局部变量中。
- 通过反向代理时也要关闭请求体缓冲，例如 Nginx 需要配置 `proxy_request_buffering off;`，否则文件可能先被 Nginx 暂存。

## 9. 安全与限制

### 9.1 公网入口

- Router 必须使用 HTTPS。
- REST API 建议加入调用方鉴权，例如 API Key、JWT 或内部业务 token。
- 上传文件限制大小和类型，例如图片只允许 jpeg/png/webp，音频只允许 wav/mp3/m4a。
- 对 `width`、`height` 做范围限制，防止异常参数拖垮 GPU。
- Router 要在流式读取过程中累计大小，超过限制立即中止 HTTP 请求和 Worker 提交流程。
- 所有临时文件路径都由 Worker 服务端生成，不信任客户端文件名。

### 9.2 Worker WebSocket

- 使用 `wss://`。
- Worker 使用固定 token 或 mTLS 认证。
- Router 只接受白名单 Worker ID。
- 心跳超时后关闭连接，并移除该 Worker 的在线连接。
- Worker 断开后，指向该 Worker 的 `prompt_id` 映射可以保留到 TTL，但查询时应返回 Worker 不可用。

### 9.3 ComfyUI

- ComfyUI 仅监听 Worker 本机或内网地址。
- 不在公网开放 ComfyUI 管理界面。
- Worker 不把 ComfyUI 内网 URL 直接暴露给客户端。

## 10. 异常处理

| 场景 | 处理方式 |
| --- | --- |
| 没有空闲 Worker | Router 不读取上传体，直接返回 `503` 或 `429` |
| 客户端上传中断 | Router 通知 Worker 取消当前提交；不产生 `prompt_id` |
| Worker 接收上传时断开 | Router 中止请求；不产生 `prompt_id` |
| Worker 提交 ComfyUI 失败 | Router 返回提交失败；不记录映射 |
| Worker 返回 `prompt_id` 后断开 | 状态/结果查询根据映射找到 Worker，但连接不可用，返回 `503` 或 `502` |
| 映射不存在 | 返回 `404` |
| ComfyUI 任务失败 | Worker 查询 history 后把失败信息返回，Router 透传给客户端 |
| 结果流中断 | Router 中断 HTTP 下载响应；客户端可重试下载 |

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
    streaming_upload.py      # HTTP multipart 流式解析与 WS 转发
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
https://dongdongkc.shierkeji.com:6205/api/v1/predict_talking_video/status/{prompt_id}
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

这个配置保留你现有的证书路径和 `6205` 端口，把请求代理到本机 Router。关键点是 `proxy_request_buffering off;`，否则 Nginx 可能先把客户端上传体缓存到磁盘，这会破坏“程序 1 不暂存上传文件”的设计目标。

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

如果这个 `server` 位于某个统一认证网关后面，还需要确认统一认证层也没有对大文件请求体做完整缓存；否则即使 Router 和 Nginx 配置正确，上传文件仍可能先被前置层落盘。

## 13. 第一版实现范围

第一版只做这些能力：

- 一个 Router。
- 一个或多个 Worker 主动连接 Router。
- Router 提交请求时只选择当前空闲 Worker，没有就拒绝。
- Router 不生成业务任务 ID。
- Router 不保存输入文件、不保存结果文件、不维护任务状态机。
- Router 等 Worker 成功提交 ComfyUI 并返回 `prompt_id` 后，再响应客户端。
- Router 唯一业务状态是 `prompt_id -> worker` 映射。
- 状态查询和结果下载都通过 `prompt_id` 路由到对应 Worker。
- Worker 负责所有 ComfyUI 交互。
- 客户端仍使用当前提交接口和状态查询接口，只是状态路径里的值变成真实 ComfyUI `prompt_id`。

后续如果需要 Worker 离线后仍可查询/下载，或需要长时间排队，再单独引入 Redis、对象存储或任务队列。第一版不要把这些能力塞进 Router。
