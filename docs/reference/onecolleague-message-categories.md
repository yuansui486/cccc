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

## 组合模型与触发逻辑

### 单条聊天消息的组合公式

渲染器不是把所有字段任意排列，而是按照固定阶段追加文本。设：

```text
E = event_id 存在
P = priority=attention
R = reply_required=true
C = collaboration_required=true
K = computer_control_request 是有效对象
X = src_group_id 与 src_event_id 同时存在
S = skill_capability_id 成功解析并生成 skill 指令
F = 至少一个引用可渲染
A = 至少一个附件通过规范化
I = insight 通过 normalize_insight
```

单条消息的结构公式为：

```text
Header = sender -> target + source_identity? + reply_to? + quote_preview?
Body = IMPORTANT? + REPLY_REQUIRED? + COLLABORATION? + COMPUTER_CONTROL?
       + RELAYED_FROM? + SKILL_INSTRUCTION? + MESSAGE_TEXT
       + REFERENCES? + ATTACHMENTS? + PEER_INSIGHT?
ChatMessage = Header + ": " + Body
```

固定顺序来自 `build_actor_delivery_text()`：控制前缀先追加，引用和附件在正文后追加，peer insight 最后追加。

有效组合约束如下：

```text
P => E
R => E
X <=> (src_group_id != "" AND src_event_id != "")
F = count(renderable_refs) > 0
A = count(normalized_attachments) > 0
I = normalize_insight(insight) != None
```

因此，10 个布尔开关的理论上限是 `2^10`；考虑依赖关系后，可用结构组合数为：

```text
N_chat_valid = 2^8 * (1 + 3) = 2^10
```

这里将 `P/R` 看作依赖于 `E` 的两个选项：`E=0` 时只能是 `P=R=0`，`E=1` 时有四种 `P/R` 组合；其余 `C/K/X/S/F/A/I` 按约束取值。若把“只填一半的 X”也作为输入错误状态统计，则那部分不属于有效组合。正文内容、收件人数量和字段值本身不计入组合数。

### 投递资格与扇出

对每个 actor 独立判断：

```text
eligible(actor, event) =
  actor != sender
  AND is_message_for_actor(actor, event)
  AND runner/runtime 有可用投递路径
```

一次事件的直接扇出为：

```text
fanout(event) = sum(eligible(actor_i, event) for actor_i in actors)
```

当 headless actor 未运行时，系统会尝试 auto-wake；若不能直接接收，则可能追加一个 `system.notify` 提醒。重复提醒会被已直接投递的 actor 排除。

组状态对 PTY 队列的过滤公式：

```text
deliverable(state) =
  all pending messages                 if state=active
  {chat.message, system.notify} only   if state=idle
  empty                                if state in {paused, stopped}
```

### PTY 批量排列

设当前批次有 `c` 条聊天消息、`s` 条系统通知，`n=c+s`：

```text
PTYBatch =
  (n > 1 ? "[onecolleague] n new messages:" + blank_line : "")
  + Message_1 + blank_line + ... + Message_n
  + (reminder_due ? MCP_REMINDER : "")
```

消息保持队列 FIFO 顺序；如果只考虑消息种类，聊天/通知的种类排列上限为 `C(n, c)`，不会在渲染阶段重新排序。

提醒条件为：

```text
floor((delivered_before + c) / REMINDER_EVERY_N_MESSAGES)
  > floor(delivered_before / REMINDER_EVERY_N_MESSAGES)
```

当前 `REMINDER_EVERY_N_MESSAGES=1`，所以成功投递且 `c>0` 的批次会追加 MCP 回复提醒。

首次 PTY 会话还会先发送 Preamble。普通运行时是两个提交阶段：`Preamble -> message batch`；OpenClaw 首次运行时会把两者合并为一个提交。

### Headless 与 Browser 排列

Codex/Claude headless 用户轮次：

```text
HeadlessUser = ChatMessage + MCP_REMINDER + ClaudeImageHint?
```

控制轮次：

```text
HeadlessControl = INTERNAL_CONTROL_WRAPPER + ControlBody
```

授权回执由 Codex/Claude 会话在提交轮次中追加：

```text
TurnText = turn_grant_receipt? + HeadlessUser|HeadlessControl
```

Browser push 轮次最多读取 20 个未读事件：

```text
BrowserPrompt = BootstrapSeed?
  + BrowserBatchHeader
  + completion_receipt?
  + MCP_REMINDER
  + Message_1 ... Message_n       (n <= 20)
```

Browser 合并文本超过 48,000 字符追加 `delivery text truncated`；Web Model pull 的合并文本上限为 24,000 字符。pull 模式同样最多 20 个事件，但返回结构化 turn，不经过 Browser 注入。

## 可复现场景与运行结果

下面示例使用占位符，命令在仓库根目录执行。输出是当前 renderer 的实际排列结果；JSON 回执中的动态字段用 `...` 表示。

### 场景 S01：普通单条聊天

输入 fixture：

```python
ActorInboundEnvelope(by="user", to=["ACTOR_ID"], text="inspect build")
```

运行：

```bash
uv run python -c "from no1.daemon.messaging.inbound_rendering import *; print(render_actor_inbound_message(ActorInboundEnvelope(by='user',to=['ACTOR_ID'],text='inspect build')))"
```

输出：

```text
[onecolleague] user → ACTOR_ID: inspect build
```

触发：`E=P=R=C=K=X=S=F=A=I=0`，仅产生 `chat`。

### 场景 S02：回复与引用

输入：`by=peer-2`、`to=[ACTOR_ID]`、`reply_to=EVENT_ID_OLD`、`quote_text="original"`、`text="ack"`。

运行：

```bash
uv run python -c "from no1.daemon.messaging.inbound_rendering import *; print(render_actor_inbound_message(ActorInboundEnvelope(by='peer-2',to=['ACTOR_ID'],reply_to='EVENT_ID_OLD',quote_text='original',text='ack')))"
```

输出：

```text
[onecolleague] peer-2 → ACTOR_ID (reply:EVENT)
> "original": ack
```

触发：`reply_to` 和 `quote_text` 只影响 `Header`，不改变消息顺序。

### 场景 S03：重要且要求回复的协作消息

输入：`priority=attention`、`reply_required=true`、`collaboration_required=true`、`event_id=EVENT_ID`、正文 `TASK_TEXT`。

运行：

```bash
uv run python -c "from no1.daemon.messaging.actor_turn_rendering import build_actor_delivery_text; print(build_actor_delivery_text(text='TASK_TEXT',priority='attention',reply_required=True,event_id='EVENT_ID',refs=[],attachments=[],collaboration_required=True))"
```

输出结构：

```text
[onecolleague] IMPORTANT (event_id=EVENT_ID):
[onecolleague] REPLY REQUIRED (event_id=EVENT_ID): reply via onecolleague_message_reply.
[onecolleague] 需协作：...
...协作约束正文...
TASK_TEXT
```

排列：`IMPORTANT -> REPLY REQUIRED -> COLLABORATION -> 正文`。

如果输入还带 `insight=INSIGHT_TEXT`，则在正文及其他引用段之后追加：

```text
[Peer higher-order perspective - provisional. Rebuild independently; if it never rises above the message's working level, treat it as ordinary content rather than privileged framing.]
INSIGHT_TEXT
```

### 场景 S04：正文同时带引用和附件

输入：`text=inspect`、一个 `task_ref`、一个附件 `{title:"FILE_NAME", bytes:12, path:"BLOB_PATH"}`。

运行：

```bash
uv run python -c "from no1.daemon.messaging.actor_turn_rendering import build_actor_delivery_text; print(build_actor_delivery_text(text='inspect',priority='normal',reply_required=False,event_id='EVENT_ID',refs=[{'kind':'task_ref','task_id':'TASK_ID','title':'TASK_TITLE','status':'planned'}],attachments=[{'title':'FILE_NAME','bytes':12,'path':'BLOB_PATH'}]))"
```

输出：

```text
inspect

[onecolleague] References:
- Task TASK_ID [planned] — TASK_TITLE

[onecolleague] Attachments: use onecolleague_file(action="read", rel_path=...) for text; use action="blob_path" for binary/local tools.
- FILE_NAME (12 bytes) [BLOB_PATH]
```

排列：`正文 -> References -> Attachments`。

### 场景 S05：两条 PTY 聊天批次

输入 fixture：`PendingMessage(EVENT_1, ...)` 和 `PendingMessage(EVENT_2, ...)`。

运行：

```bash
uv run python -c "from no1.daemon.messaging.delivery import *; print(render_batched_messages([PendingMessage(event_id='EVENT_1',by='user',to=['ACTOR_ID'],text='first'),PendingMessage(event_id='EVENT_2',by='peer-2',to=['ACTOR_ID'],text='second')]))"
```

输出：

```text
[onecolleague] 2 new messages:

[onecolleague] user → ACTOR_ID: first

[onecolleague] peer-2 → ACTOR_ID: second
```

### 场景 S06：Browser 批次

输入 fixture：`delivery_id=DELIVERY_ID`、`event_ids=[EVENT_1, EVENT_2]`、`actor_id=ACTOR_ID`、`coalesced_text=COALESCED_TEXT`。

运行：

```bash
uv run python -c "from no1.daemon.actors.web_model_browser_delivery import build_web_model_browser_turn_prompt; print(build_web_model_browser_turn_prompt({'delivery_id':'DELIVERY_ID','actor_id':'ACTOR_ID','event_ids':['EVENT_1','EVENT_2'],'coalesced_text':'COALESCED_TEXT'}))"
```

输出：

```text
[onecolleague] Browser batch DELIVERY_ID events=EVENT_1,EVENT_2 actor=ACTOR_ID
[onecolleague] Use onecolleague_message_reply for replies; use onecolleague_message_send for new messages. Terminal output is not delivered. Verify reply_to/to; avoid routine @all. Use onecolleague_help if unsure.
COALESCED_TEXT
```

### 场景 S07：系统通知

输入：`notify_kind=info`、`title=TITLE`、`message=MESSAGE`。

运行：

```bash
uv run python -c "from no1.daemon.messaging.delivery import PendingMessage, render_single_message; print(render_single_message(PendingMessage(event_id='EVENT_ID',by='system',to=['ACTOR_ID'],text='',kind='system.notify',notify_kind='info',notify_title='TITLE',notify_message='MESSAGE')))"
```

输出：

```text
[onecolleague] SYSTEM (info): TITLE
MESSAGE
```

### 场景 S08：无投递结果

| 条件 | 结果 |
| --- | --- |
| actor 是发送者 | `eligible=false`，不回投给自己 |
| `to` 不包含 actor 且不是 `@all/@peers/@foreman` | 不入该 actor 的投递队列 |
| group=`paused` 或 `stopped` | PTY `deliverable=[]`，消息留在 inbox |
| group=`idle` 且消息类型不是 `chat.message/system.notify` | 当前批次过滤掉 |
| Browser `kind_filter=chat` | 只读取 `chat.message` |
| Browser `kind_filter=notify` | 只读取 `system.notify` |

这些是合法的“零消息”场景，不会生成新的消息前缀。

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
