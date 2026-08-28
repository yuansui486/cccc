# OneColleague Actor-Facing 消息分类

本文档盘点当前会发送给智能体的 OneColleague 消息。范围包括 PTY 终端、Codex/Claude headless 会话和 Browser 注入；CLI 日志、MCP 工具输出及 Ledger 存储文本不属于 actor-facing 消息，单独列在排除项中。

## 投递链路

```text
chat/event operation
    -> actor_turn_rendering.py / inbound_rendering.py
    -> delivery.py 批量排队与包装
    -> transport adapter
       - PTY: pty_submit_text()
       - Codex/Claude: submit_user_message() / submit_control_message()
       - Browser: build_web_model_browser_turn_prompt()
```

## 分类定义

| 分类键 | 含义 |
| --- | --- |
| `chat` | 用户或智能体之间的业务消息及其消息级元数据 |
| `system` | 守护进程通知、运行状态和异常/截断状态 |
| `control` | 要求智能体遵循的协作、工具或安装执行指令 |
| `reference` | 引用、附件和媒体文件定位信息 |
| `protocol` | 投递批次、回复提醒和回执等协议数据 |
| `session` | 身份、会话启动和传输初始化信息 |

## 当前消息清单

### `chat`

| 当前格式 | 触发条件 | 来源 | 传输 |
| --- | --- | --- | --- |
| `[onecolleague] user → peer: message` | 普通 `chat.message` | `src/no1/daemon/messaging/inbound_rendering.py:63` | PTY、Codex/Claude headless、Browser |
| `[onecolleague] user[platform / name / id] → peer: message` | 消息带外部平台或发送者身份 | `src/no1/daemon/messaging/inbound_rendering.py:47-63` | 同上 |
| `[onecolleague] IMPORTANT (event_id=...):` | `priority=attention` 且存在事件 ID | `src/no1/daemon/messaging/actor_turn_rendering.py:218` | 同上 |
| `[onecolleague] REPLY REQUIRED (event_id=...): ...` | `reply_required=true` 且存在事件 ID | `src/no1/daemon/messaging/actor_turn_rendering.py:220` | 同上 |
| `[onecolleague] RELAYED FROM (group_id=..., event_id=...):` | 跨组转发字段存在 | `src/no1/daemon/messaging/actor_turn_rendering.py:226` | 同上 |
| `[onecolleague] {event_kind} (event_id=...):` | 非 `chat.message` 的通用事件兜底渲染 | `src/no1/daemon/messaging/actor_turn_rendering.py:328` | headless、Browser；PTY 事件由队列类型决定 |

`IMPORTANT`、`REPLY REQUIRED` 和 `RELAYED FROM` 是聊天消息的附加段，不是独立事件。

### `system`

| 当前格式 | 触发条件 | 来源 | 传输 |
| --- | --- | --- | --- |
| `[onecolleague] SYSTEM (kind): title/message` | `system.notify` | `src/no1/daemon/messaging/delivery.py:613`、`src/no1/daemon/messaging/actor_turn_rendering.py:323` | PTY、Codex/Claude headless |
| `[onecolleague] coalesced turn text truncated` | Web model 合并文本超过上限 | `src/no1/daemon/actors/web_model_runtime_ops.py:70` | Browser/headless runtime |
| `[onecolleague] delivery text truncated` | Browser prompt 超过文本上限 | `src/no1/daemon/actors/web_model_browser_delivery.py:263` | Browser |
| `[onecolleague] {event_kind} ...` | 未知事件类型的系统兜底内容 | `src/no1/daemon/messaging/actor_turn_rendering.py:328` | headless、Browser |

### `control`

| 当前格式 | 触发条件 | 来源 | 传输 |
| --- | --- | --- | --- |
| `[onecolleague] 需协作：...` | `collaboration_required=true` | `src/no1/daemon/messaging/actor_turn_rendering.py:20` | PTY、headless、Browser |
| `[onecolleague] 电脑控制执行契约（request_id=...）：` | 存在电脑控制请求 | `src/no1/daemon/messaging/actor_turn_rendering.py:38` | PTY、headless、Browser |
| `[OneColleague] INTERNAL CONTROL: session bootstrap` | headless 会话启动控制轮次 | `src/no1/daemon/messaging/delivery.py:681` | Codex/Claude headless |
| `[OneColleague] INTERNAL CONTROL: system notification` | headless 系统通知控制轮次 | `src/no1/daemon/messaging/delivery.py:687` | Codex/Claude headless |
| `[OneColleague] BOOTSTRAP TOOL CONTRACT:` | Codex 启动时要求调用 bootstrap 工具 | `src/no1/daemon/codex_app_sessions.py:1295` | Codex headless |
| `[onecolleague] Slash command: /install` / `Capability: ...` | 安装能力请求 | `src/no1/daemon/messaging/install_slash_command.py:63-64` | 按能力投递路径 |
| `[OneColleague] SYSTEM PREFETCH: ...` / `FETCHED INPUT:` | Voice Secretary 预取输入 | `src/no1/daemon/voice_secretary_control_turns.py:384-388` | Voice Secretary 控制轮次 |
| `[OneColleague] REPAIR HINT: ...` | Voice Secretary 控制轮次缺少必需动作 | `src/no1/daemon/voice_secretary_control_turns.py:539-546` | Voice Secretary 控制轮次 |

### `reference`

| 当前格式 | 触发条件 | 来源 | 传输 |
| --- | --- | --- | --- |
| `[onecolleague] References:` | 事件包含任务、演示文稿、链接或快照引用 | `src/no1/daemon/messaging/actor_turn_rendering.py:104` | PTY、headless、Browser |
| `[onecolleague] Attachments: use onecolleague_file(...) ...` | 事件包含附件 | `src/no1/daemon/messaging/actor_turn_rendering.py:234` | PTY、headless、Browser |
| `[onecolleague] 图片附件已保存到本地文件。` | Claude 文本通道收到图片附件 | `src/no1/daemon/claude_app_sessions.py:1040` | Claude headless |
| `[onecolleague] 请优先基于以下图片文件路径继续处理：` | 同上，随后列出本地路径 | `src/no1/daemon/claude_app_sessions.py:1041` | Claude headless |

### `protocol`

| 当前格式 | 触发条件 | 来源 | 传输 |
| --- | --- | --- | --- |
| `[onecolleague] N new messages:` | 同一批次包含多条待投递消息 | `src/no1/daemon/messaging/delivery.py:721` | PTY |
| `[onecolleague] Use onecolleague_message_reply ...` | 需要提醒可见回复与收件人规则 | `src/no1/daemon/messaging/delivery.py:577` | PTY、headless、Browser |
| `[onecolleague] completion_receipt={...}` | 一轮投递完成并附带完成回执 | `src/no1/daemon/messaging/delivery.py:737`、`src/no1/daemon/actors/web_model_browser_delivery.py:271` | PTY、Codex/Claude headless、Browser |
| `[onecolleague] turn_grant_receipt={...}` | 一轮获得工具执行授权回执 | `src/no1/daemon/messaging/delivery.py:751` | PTY、Codex/Claude headless |
| `[onecolleague] Browser batch ...` | Browser 注入批次建立 | `src/no1/daemon/actors/web_model_browser_delivery.py:279` | Browser |

### `session`

| 当前格式 | 触发条件 | 来源 | 传输 |
| --- | --- | --- | --- |
| `[OneColleague] You are ...` | 首次建立 actor 会话，注入身份和组信息 | `src/no1/kernel/system_prompt.py:103` | PTY、headless、Browser bootstrap |
| `[OneColleague] Web transport:` | Browser 会话传输说明 | `src/no1/daemon/actors/web_model_browser_delivery.py:179` | Browser |
| `[OneColleague] Session bootstrap for this browser chat:` | Browser 会话初始化 | `src/no1/daemon/actors/web_model_browser_delivery.py:191` | Browser |
| `[OneColleague] INTERNAL CONTROL` | headless 控制面包装器的通用前缀 | `src/no1/daemon/messaging/delivery.py:693` | Codex/Claude headless |

## 暂未实际输出的定义

- `src/no1/daemon/messaging/experience_reminder.py` 定义了 `[onecolleague experience]`，但 `append_experience_reminder()` 当前只返回原文本，因此不会产生实际 actor-facing 输出。

## 排除项

以下源码命中不属于发送给智能体的消息，不应与上述清单混淆：

- `src/no1/cli/common.py` 和 `src/no1/ports/web/main.py` 中写入 stderr 的启动、错误和状态日志。
- `src/no1/ports/mcp/handlers/onecolleague_repo.py` 中的工具执行输出截断或错误文本。
- `src/no1/kernel/ledger.py` 中用于附件正文存储的 `[onecolleague] (chat text stored at ...)` 占位符。
- 测试样例、帮助文档、工具名称和环境变量名称中的 `onecolleague` 文本。

## 维护说明

本文档是当前实现的盘点，不规定本轮源码替换方案。新增 actor-facing 前缀或新增传输适配器时，应同步补充分类、触发条件、来源和传输范围，并重新执行全仓库前缀扫描核对。
