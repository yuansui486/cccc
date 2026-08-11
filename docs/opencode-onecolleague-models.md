# OpenCode OneColleague 模型

OneColleague 会在启动 OpenCode actor 时通过 `OPENCODE_CONFIG_CONTENT` 注入一个名为
`onecolleague` 的 OpenAI-compatible provider。不会修改用户全局的 `opencode.json`。

## 默认配置

```json
{
  "provider": {
    "onecolleague": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OneColleague",
      "options": {
        "baseURL": "https://peer.shierkeji.com/v1",
        "apiKey": "{env:ONECOLLEAGUE_API_KEY}"
      }
    }
  }
}
```

默认地址也可以通过 `ONECOLLEAGUE_OPENCODE_BASE_URL` 覆盖（例如测试环境）；正常使用时无需设置。

模型目录来自 `https://peer.shierkeji.com/api/available_model`。服务器返回的模型都会加入
OpenCode 的模型列表，包括 `locked: true` 的模型；不会按 `locked` 字段过滤。目录会在本机
`ONECOLLEAGUE_HOME/state/cache`（未设置时使用 `CCCC_HOME/state/cache`）下短暂缓存，服务暂时
不可访问时使用缓存或内置模型列表。智能体启动只读取本地缓存，不会在启动链中等待目录网络请求；
创建或编辑智能体时的模型列表刷新会更新这份缓存。

## 密钥

OpenCode 使用和 Codex 相同的 `ONECOLLEAGUE_API_KEY`。DoneHub 登录后，创建或编辑 OpenCode
actor 时会自动复用当前 token；也可以在 actor/profile 的私有环境变量中手工设置：

```text
ONECOLLEAGUE_API_KEY="your-key"
```

`OPENAI_API_KEY` 属于其他 provider，不会自动转换成 `ONECOLLEAGUE_API_KEY`。已有智能体如果只
配置了 `OPENAI_API_KEY`，需要显式增加上面的专用变量。

真实密钥只保存在本地私有环境变量存储中，不会写入 OpenCode inline JSON、组账本或运行日志。

## 切换模型

在 OneColleague 的创建/编辑 actor 界面选择 OpenCode 模型预设，生成的命令类似：

```bash
opencode --auto -m onecolleague/gpt-5.4
opencode --auto -m onecolleague/deepseek-v4-pro
opencode --auto -m onecolleague/qwen3.6-plus
```

也可以在 OpenCode 中运行 `/models`，或在命令字段中手工填写任意：

```text
opencode -m onecolleague/<server-model-id>
```

如需使用 OpenCode 的其他 provider，保留原有配置即可；OneColleague provider 不会删除用户的
其他 provider 或 MCP 配置。

## DeepSeek V4 思考深度

`deepseek-v4-pro` 和 `deepseek-v4-flash` 提供以下 OpenCode 变体：

- `none`：关闭思考模式。
- `low`：开启思考模式，使用低思考强度。
- `high`：开启思考模式，使用高思考强度。
- `max`：开启思考模式，使用最大思考强度。

未选择变体时不额外传递思考参数，使用服务端默认值（思考模式开启，强度为 `high`）。在 OpenCode
中使用 `variant_cycle` 对应的快捷键可以循环切换变体。OneColleague 会按 Chat Completions 格式
发送 `thinking.type` 和 `reasoning_effort`；`none` 只发送关闭开关，不会发送不受支持的
`reasoning_effort=none`。

变体配置在 actor 启动时注入。配置更新后，需要重启已经运行的 OpenCode actor。

## MCP 工具与排障

OneColleague 的 OpenCode actor 会在进程启动时通过 `OPENCODE_CONFIG_CONTENT` 注入名为
`onecolleague` 的本地 MCP 服务，实际执行命令是：

```text
onecolleague mcp
```

不需要另外安装名为 `opencode mcp` 的 npm 包，也不会修改用户全局的 `opencode.json`。
直接在终端手工运行 `opencode` 时不会自动获得这份按 actor 注入的 MCP 配置。

如果 actor 日志提示没有 `onecolleague_*` 工具，可以在同一运行环境检查 OpenCode 的连接状态：

```powershell
opencode mcp list --print-logs --log-level DEBUG
```

需要更细的 OneColleague stdio 握手信息时，临时设置：

```powershell
$env:ONECOLLEAGUE_MCP_DEBUG = "1"
```

诊断信息写入 stderr，不会混入 MCP 的 stdout JSON-RPC 数据。确认后可以移除该环境变量，重新启动
actor 以恢复默认日志量。
