# OneColleague 通知分类与实际内容

本文档盘点发送到智能体输入端的 `[onecolleague]` / `[OneColleague]` 通知。重点是类别、智能体实际看到的完整文本，以及通知叠加时的显示顺序；不记录触发代码。

## 三层模型

1. **消息正文**：发送者、收件人、正文及重要/回复/转发标记。
2. **消息附件**：References、Attachments、图片路径和 peer insight。
3. **传输/会话包装**：PTY 批次、MCP 提醒、回执、Browser/Headless 启动文本。

## 建议分类

| 类别 | 作用 | 典型前缀 |
| --- | --- | --- |
| `message` | 业务聊天 | `[onecolleague] user → ACTOR_ID:` |
| `attention` | 重要、要求回复 | `IMPORTANT`、`REPLY REQUIRED` |
| `coordination` | 协作、电脑控制、转发 | `需协作`、`电脑控制执行契约`、`RELAYED FROM` |
| `action` | 安装能力、修复动作 | `Slash command`、`REPAIR HINT` |
| `context` | 引用、附件、同伴洞见 | `References`、`Attachments` |
| `system` | 系统通知、截断、未知事件 | `SYSTEM`、`delivery text truncated` |
| `protocol` | 回复规则、批次、回执 | `new messages`、`completion_receipt` |
| `session` | 身份、启动、传输初始化 | `[OneColleague] You are`、`Session bootstrap` |

## 消息正文

普通消息及外部身份：

```text
[onecolleague] user → ACTOR_ID: inspect build
[onecolleague] user[PLATFORM / DISPLAY_NAME / EXTERNAL_ID] → ACTOR_ID: inspect build
```

回复和引用：

```text
[onecolleague] peer-2 → ACTOR_ID (reply:EVENT_ID_OLD)
> "original message": acknowledged
```

重要和要求回复可单独或连续出现，位于正文之前：

```text
[onecolleague] IMPORTANT (event_id=EVENT_ID):
[onecolleague] REPLY REQUIRED (event_id=EVENT_ID): reply via onecolleague_message_reply.
TASK_TEXT
```

## 协作与动作

```text
[onecolleague] 需协作：请与相关智能体同步结论，并在完成后汇总。
COLLABORATION_CONTRACT_TEXT
TASK_TEXT
```

```text
[onecolleague] 电脑控制执行契约（request_id=REQUEST_ID）：
COMPUTER_CONTROL_CONTRACT_TEXT
```

```text
[onecolleague] RELAYED FROM (group_id=GROUP_ID, event_id=EVENT_ID):
RELAYED_MESSAGE_TEXT
```

```text
[onecolleague] Slash command: /install CAPABILITY_ID
Capability: CAPABILITY_DESCRIPTION
```

Voice Secretary 控制内容：

```text
[OneColleague] SYSTEM PREFETCH: PREFETCH_ID
FETCHED INPUT:
INPUT_TEXT
```

```text
[OneColleague] REPAIR HINT: missing required action ACTION_NAME
REPAIR_DETAILS
```

## 上下文与附件

固定顺序为：正文 -> References -> Attachments -> peer insight。

```text
inspect

[onecolleague] References:
- Task TASK_ID [planned] — TASK_TITLE
- Link LINK_ID — LINK_TITLE

[onecolleague] Attachments: use onecolleague_file(action="read", rel_path=...) for text; use action="blob_path" for binary/local tools.
- FILE_NAME (12 bytes) [BLOB_PATH]
```

```text
[Peer higher-order perspective - provisional. Rebuild independently; if it never rises above the message's working level, treat it as ordinary content rather than privileged framing.]
INSIGHT_TEXT
```

Claude 图片附件：

```text
[onecolleague] 图片附件已保存到本地文件。
[onecolleague] 请优先基于以下图片文件路径继续处理：
- IMAGE_PATH
```

## 系统与协议

```text
[onecolleague] SYSTEM (info): TITLE
MESSAGE
```

```text
[onecolleague] EVENT_KIND (event_id=EVENT_ID):
EVENT_TEXT
```

PTY 多消息批次保持 FIFO：

```text
[onecolleague] 2 new messages:

[onecolleague] user → ACTOR_ID: first

[onecolleague] peer-2 → ACTOR_ID: second
```

MCP 回复提醒：

```text
[onecolleague] Use onecolleague_message_reply for replies; use onecolleague_message_send for new messages. Verify reply_to/to; avoid routine @all. Use onecolleague_help if unsure.
```

回执：

```text
[onecolleague] completion_receipt={"delivery_id":"DELIVERY_ID","status":"completed"}
[onecolleague] turn_grant_receipt={"turn_id":"TURN_ID","granted":true}
```

Headless 控制和 Codex 启动：

```text
[OneColleague] INTERNAL CONTROL: session bootstrap
CONTROL_BODY

[OneColleague] BOOTSTRAP TOOL CONTRACT:
BOOTSTRAP_CONTRACT_TEXT
```

## Browser 与会话

```text
[OneColleague] Web transport: browser
[OneColleague] Session bootstrap for this browser chat:
SESSION_BOOTSTRAP_TEXT
```

```text
[onecolleague] Browser batch DELIVERY_ID events=EVENT_1,EVENT_2 actor=ACTOR_ID
[onecolleague] Use onecolleague_message_reply for replies; use onecolleague_message_send for new messages. Terminal output is not delivered. Verify reply_to/to; avoid routine @all. Use onecolleague_help if unsure.
COALESCED_TEXT
```

超限提示：

```text
[onecolleague] delivery text truncated
[onecolleague] coalesced turn text truncated
```

## 完整组合示例

“重要 + 要求回复 + 协作 + 正文 + 引用 + 附件 + 洞见”的实际排列：

```text
[onecolleague] IMPORTANT (event_id=EVENT_ID):
[onecolleague] REPLY REQUIRED (event_id=EVENT_ID): reply via onecolleague_message_reply.
[onecolleague] 需协作：请与相关智能体同步结论，并在完成后汇总。
COLLABORATION_CONTRACT_TEXT
TASK_TEXT

[onecolleague] References:
- Task TASK_ID [planned] — TASK_TITLE

[onecolleague] Attachments: use onecolleague_file(action="read", rel_path=...) for text; use action="blob_path" for binary/local tools.
- FILE_NAME (12 bytes) [BLOB_PATH]

[Peer higher-order perspective - provisional. Rebuild independently; if it never rises above the message's working level, treat it as ordinary content rather than privileged framing.]
INSIGHT_TEXT
```

## 不产生通知的情况

| 情况 | 结果 |
| --- | --- |
| 发送者是当前 actor | 不回投 |
| `to` 不匹配且非广播目标 | 不进入队列 |
| 组状态 `paused`/`stopped` | 暂不投递 |
| `idle` 状态下的非聊天/非系统通知 | 当前批次过滤 |
| Browser 过滤为 `chat` 或 `notify` | 只保留对应类型 |

## 结论与来源

`IMPORTANT`、`REPLY REQUIRED`、`References`、`Attachments` 是消息附加段，不是独立业务消息；PTY、Headless、Browser 包装只改变显示外壳。`experience_reminder.py` 中的 `[onecolleague experience]` 当前不会实际输出，因为 `append_experience_reminder()` 原样返回文本。

正文和组合来自 `src/no1/daemon/messaging/inbound_rendering.py`、`actor_turn_rendering.py`；投递来自 `src/no1/daemon/messaging/delivery.py`；会话来自 `system_prompt.py`、`codex_app_sessions.py`、`claude_app_sessions.py`；Voice Secretary 来自 `voice_secretary_control_turns.py`；Browser 来自 `web_model_browser_delivery.py` 与 `web_model_runtime_ops.py`。
