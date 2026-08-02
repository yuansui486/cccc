# 工作组定时器 API 文档

## 1. 结论

工作组定时器复用现有的 **Automation API**。定时器不是单独的数据表或独立 timer 模块，而是工作组自动化配置中的一条 `automation rule`。

推荐使用的接口是：

```http
POST /api/v1/groups/{group_id}/automation/manage
```

它支持增量创建、更新、启用、禁用和删除自动化规则，适合外部系统或前端调用。

## 2. 基本概念

一个定时器任务对应一条 `AutomationRule`。

规则主要由三部分组成：

| 字段 | 说明 |
| --- | --- |
| `id` | 规则唯一标识，同一个工作组内不能重复 |
| `trigger` | 什么时候触发，例如 cron、interval、at |
| `action` | 触发后做什么，例如发送通知 |

典型定时器规则：

```json
{
  "id": "daily_morning_reminder",
  "enabled": true,
  "scope": "group",
  "to": ["@foreman"],
  "trigger": {
    "kind": "cron",
    "cron": "0 9 * * *",
    "timezone": "Asia/Shanghai"
  },
  "action": {
    "kind": "notify",
    "title": "每日提醒",
    "message": "请检查今天的任务进展。",
    "priority": "normal",
    "requires_ack": false
  }
}
```

## 3. API 总览

基础路径：

```text
/api/v1/groups/{group_id}/automation
```

接口列表：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/groups/{group_id}/automation` | 读取工作组自动化规则和运行状态 |
| `POST` | `/api/v1/groups/{group_id}/automation/manage` | 增量管理规则，推荐用于创建定时器 |
| `PUT` | `/api/v1/groups/{group_id}/automation` | 全量替换规则集，不推荐普通创建场景使用 |
| `POST` | `/api/v1/groups/{group_id}/automation/reset_baseline` | 重置为默认自动化规则 |

## 4. 认证和访问控制

这些接口都是工作组级接口，使用系统已有的 group 权限检查。

调用要求：

- 如果系统启用了 access token，调用方必须有目标工作组访问权限。
- 普通用户发起的请求建议传 `"by": "user"`。
- Web 如果运行在 read-only exhibit mode，`POST`、`PUT`、`DELETE` 会被拒绝。

只读模式错误示例：

```json
{
  "ok": false,
  "error": {
    "code": "read_only",
    "message": "OneColleague Web is running in read-only (exhibit) mode.",
    "details": {}
  }
}
```

## 5. 读取当前自动化配置

### 请求

```http
GET /api/v1/groups/{group_id}/automation
```

### 用途

创建或修改定时器之前，建议先读取当前配置，主要是为了拿到：

- 当前规则列表：`result.ruleset.rules`
- 当前版本号：`result.version`
- 当前规则状态：`result.status`

### 响应示例

```json
{
  "ok": true,
  "result": {
    "group_id": "g-demo",
    "ruleset": {
      "rules": [
        {
          "id": "daily_morning_reminder",
          "enabled": true,
          "scope": "group",
          "to": ["@foreman"],
          "trigger": {
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai"
          },
          "action": {
            "kind": "notify",
            "title": "每日提醒",
            "message": "请检查今天的任务进展。",
            "priority": "normal",
            "requires_ack": false
          }
        }
      ],
      "snippets": {}
    },
    "snippet_catalog": {
      "built_in": {},
      "built_in_overrides": {},
      "custom": {}
    },
    "status": {
      "daily_morning_reminder": {
        "last_fired_at": "",
        "last_error_at": "",
        "last_error": "",
        "next_fire_at": "2026-07-03T01:00:00Z",
        "completed": false,
        "completed_at": ""
      }
    },
    "supported_vars": [
      "interval_minutes",
      "group_title",
      "actor_names",
      "scheduled_at"
    ],
    "version": 3,
    "server_now": "2026-07-02T08:00:00Z",
    "config_path": "/path/to/group.yaml"
  }
}
```

### status 字段说明

`status` 是一个对象，key 是规则 id，value 是该规则运行状态。

```json
{
  "last_fired_at": "",
  "last_error_at": "",
  "last_error": "",
  "next_fire_at": "2026-07-03T01:00:00Z",
  "completed": false,
  "completed_at": ""
}
```

| 字段 | 说明 |
| --- | --- |
| `last_fired_at` | 上一次成功触发时间，没有则为空字符串 |
| `last_error_at` | 上一次错误时间，没有则为空字符串 |
| `last_error` | 上一次错误信息，没有则为空字符串 |
| `next_fire_at` | 预计下一次触发时间，UTC 字符串，没有则为空字符串 |
| `completed` | 一次性 `at` 规则是否已经完成 |
| `completed_at` | 一次性规则完成时间 |

## 6. 创建定时器

### 推荐接口

```http
POST /api/v1/groups/{group_id}/automation/manage
Content-Type: application/json
```

### 请求结构

```json
{
  "by": "user",
  "expected_version": 3,
  "actions": [
    {
      "type": "create_rule",
      "rule": {
        "id": "daily_morning_reminder",
        "enabled": true,
        "scope": "group",
        "to": ["@foreman"],
        "trigger": {
          "kind": "cron",
          "cron": "0 9 * * *",
          "timezone": "Asia/Shanghai"
        },
        "action": {
          "kind": "notify",
          "title": "每日提醒",
          "message": "请检查今天的任务进展。",
          "priority": "normal",
          "requires_ack": false
        }
      }
    }
  ]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `by` | 否 | 调用者，用户调用一般传 `"user"` |
| `expected_version` | 否 | 期望的 automation 版本号，用于并发保护 |
| `actions` | 是 | 操作数组，创建定时器时放一个 `create_rule` |
| `actions[].type` | 是 | 创建规则时固定为 `"create_rule"` |
| `actions[].rule` | 是 | 完整的自动化规则对象 |

### 成功响应

成功后返回最新自动化状态。

```json
{
  "ok": true,
  "result": {
    "group_id": "g-demo",
    "ruleset": {
      "rules": [],
      "snippets": {}
    },
    "status": {},
    "version": 4,
    "changed": true,
    "applied_actions": [
      {
        "type": "create_rule",
        "rule_id": "daily_morning_reminder"
      }
    ],
    "event": {
      "kind": "group.automation_update"
    }
  }
}
```

说明：

- `changed: true` 表示实际发生了变更。
- `version` 会递增。
- `event` 是写入工作组 ledger 的 `group.automation_update` 事件。

## 7. AutomationRule 规则结构

完整规则结构：

```ts
type AutomationRule = {
  id: string;
  enabled?: boolean;
  scope?: "group" | "personal";
  owner_actor_id?: string | null;
  to?: string[];
  trigger: AutomationTrigger;
  action: AutomationAction;
};
```

### 字段说明

| 字段 | 说明 |
| --- | --- |
| `id` | 规则 id，同一个工作组内必须唯一 |
| `enabled` | 是否启用，建议创建时显式传 `true` |
| `scope` | 规则作用域。工作组定时器用 `"group"` |
| `owner_actor_id` | personal 规则的所属 actor。group 规则通常不传 |
| `to` | 通知目标 |
| `trigger` | 触发器 |
| `action` | 触发后的动作 |

### id 建议

`id` 建议使用稳定、可读、无空格的字符串，例如：

- `daily_morning_reminder`
- `weekly_status_check`
- `one_time_project_review_20260703`

不要使用中文、空格或随机过长字符串，方便后续更新、禁用和删除。

## 8. 通知目标 to

`to` 决定 notify 动作投递给谁。

支持：

| 值 | 说明 |
| --- | --- |
| `@foreman` | 投递给 foreman |
| `@all` | 投递给所有 actor |
| `@peers` | 投递给 peer actors |
| `actor_id` | 投递给指定 actor，例如 `peer1` |

示例：

```json
["@foreman"]
```

```json
["@all"]
```

```json
["peer1", "peer2"]
```

重要行为：

- `notify` 只会投递给 **启用且正在运行中** 的 actor。
- 如果目标 actor 没运行，这次触发不会成功投递给它。
- `@user` 或 `user` 不适合用于 actor 通知定时器；常规提醒建议使用 `@foreman` 或具体 actor id。

## 9. 触发器 trigger

系统支持三种触发器：

| kind | 用途 |
| --- | --- |
| `cron` | 固定时间周期触发 |
| `interval` | 每隔一段时间触发 |
| `at` | 指定时间触发一次 |

### 9.1 cron 周期定时

适合每天、每周、每月固定时间提醒。

```json
{
  "kind": "cron",
  "cron": "0 9 * * *",
  "timezone": "Asia/Shanghai"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `kind` | 固定为 `"cron"` |
| `cron` | 5 字段 cron 表达式 |
| `timezone` | IANA 时区名，例如 `Asia/Shanghai` |

cron 字段顺序：

```text
minute hour day_of_month month day_of_week
```

示例：

| 场景 | cron | timezone |
| --- | --- | --- |
| 每天 09:00 | `0 9 * * *` | `Asia/Shanghai` |
| 每天 18:30 | `30 18 * * *` | `Asia/Shanghai` |
| 工作日 09:00 | `0 9 * * 1-5` | `Asia/Shanghai` |
| 每周一 10:00 | `0 10 * * 1` | `Asia/Shanghai` |
| 每月 1 号 09:00 | `0 9 1 * *` | `Asia/Shanghai` |

注意：

- cron 是 5 字段，不支持秒级字段。
- day_of_week 中 `0` 表示周日，`1` 表示周一。
- 系统按分钟匹配。
- 同一分钟命中后会记录 slot，避免同一分钟重复触发。
- 如果 cron 表达式非法，规则可能保存成功，但运行状态里会出现 `last_error`。

### 9.2 interval 间隔触发

适合“每隔 N 秒/分钟/小时提醒一次”。

```json
{
  "kind": "interval",
  "every_seconds": 3600
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `kind` | 固定为 `"interval"` |
| `every_seconds` | 间隔秒数，必须大于等于 1 |

示例：

| 场景 | every_seconds |
| --- | --- |
| 每 10 分钟 | `600` |
| 每 30 分钟 | `1800` |
| 每 1 小时 | `3600` |
| 每 6 小时 | `21600` |

注意：

- 新建 interval 规则后，系统从当前时间开始计时。
- 新建后不会立即触发。
- 下一次触发时间通常是 `last_fired_at + every_seconds`。

### 9.3 at 一次性触发

适合“某个时间只提醒一次”。

```json
{
  "kind": "at",
  "at": "2026-07-03T01:00:00Z"
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `kind` | 固定为 `"at"` |
| `at` | RFC3339/ISO 时间字符串 |

建议：

- 推荐传 UTC 时间，格式以 `Z` 结尾。
- 如果业务使用北京时间，需要先转换成 UTC。
- 例如北京时间 `2026-07-03 09:00:00` 等于 UTC `2026-07-03T01:00:00Z`。

触发后行为：

- 成功触发后，规则会自动设为 `enabled: false`。
- `status[rule_id].completed` 会变为 `true`。
- `status[rule_id].completed_at` 会记录完成时间。

## 10. 动作 action

### 10.1 notify 通知动作

最常用的定时器动作。

```json
{
  "kind": "notify",
  "title": "每日提醒",
  "message": "请检查今天的任务进展。",
  "priority": "normal",
  "requires_ack": false
}
```

字段说明：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `kind` | 是 | 固定为 `"notify"` |
| `title` | 否 | 通知标题，空时运行时会使用默认标题 |
| `message` | 是 | 通知内容。如果使用 `snippet_ref`，可为空 |
| `snippet_ref` | 否 | 引用 snippets 中的模板 |
| `priority` | 否 | 优先级，常用 `"normal"` |
| `requires_ack` | 否 | 是否需要目标确认 |

### 10.2 group_state 状态动作

用于在指定时间切换工作组状态。

```json
{
  "kind": "group_state",
  "state": "paused"
}
```

支持状态：

- `active`
- `idle`
- `paused`
- `stopped`

限制：

- `group_state` 只支持 `at` 一次性触发。
- 不支持 cron 或 interval 周期触发。

### 10.3 actor_control 运行控制动作

用于在指定时间启动、停止或重启 actor。

```json
{
  "kind": "actor_control",
  "operation": "restart",
  "targets": ["@all"]
}
```

支持操作：

- `start`
- `stop`
- `restart`

限制：

- `actor_control` 只支持 `at` 一次性触发。
- 不支持 cron 或 interval 周期触发。

## 11. 模板变量

notify 的 `message` 或 snippet 模板支持变量：

| 变量 | 说明 |
| --- | --- |
| `{{interval_minutes}}` | interval 触发器的分钟数 |
| `{{group_title}}` | 工作组标题 |
| `{{actor_names}}` | 启用 actor 的显示名 |
| `{{scheduled_at}}` | 本次计划触发时间 |

示例：

```json
{
  "kind": "notify",
  "title": "例会提醒",
  "message": "工作组 {{group_title}} 到了例会时间，当前成员：{{actor_names}}。",
  "priority": "normal",
  "requires_ack": false
}
```

## 12. 管理操作 actions

`POST /automation/manage` 支持多个 action。

### 12.1 create_rule

创建新规则。

```json
{
  "type": "create_rule",
  "rule": {
    "id": "daily_morning_reminder",
    "enabled": true,
    "scope": "group",
    "to": ["@foreman"],
    "trigger": {
      "kind": "cron",
      "cron": "0 9 * * *",
      "timezone": "Asia/Shanghai"
    },
    "action": {
      "kind": "notify",
      "message": "请检查今天的任务进展。"
    }
  }
}
```

如果 `id` 已存在，会失败。

### 12.2 update_rule

更新已有规则。

```json
{
  "type": "update_rule",
  "rule": {
    "id": "daily_morning_reminder",
    "enabled": true,
    "scope": "group",
    "to": ["@foreman"],
    "trigger": {
      "kind": "cron",
      "cron": "30 9 * * *",
      "timezone": "Asia/Shanghai"
    },
    "action": {
      "kind": "notify",
      "title": "每日提醒",
      "message": "提醒时间已调整为 09:30。",
      "priority": "normal",
      "requires_ack": false
    }
  }
}
```

注意：

- `update_rule` 需要传完整规则对象。
- 如果规则不存在，会失败。

### 12.3 set_rule_enabled

启用或禁用规则。

```json
{
  "type": "set_rule_enabled",
  "rule_id": "daily_morning_reminder",
  "enabled": false
}
```

### 12.4 delete_rule

删除规则。

```json
{
  "type": "delete_rule",
  "rule_id": "daily_morning_reminder"
}
```

### 12.5 replace_all_rules

全量替换规则集。

```json
{
  "type": "replace_all_rules",
  "ruleset": {
    "rules": [],
    "snippets": {}
  }
}
```

不建议普通定时器创建场景使用。除非调用方明确拥有整个工作组 automation 配置，否则应该使用 `create_rule`、`update_rule` 等增量操作。

## 13. 全量更新接口

接口：

```http
PUT /api/v1/groups/{group_id}/automation
Content-Type: application/json
```

请求：

```json
{
  "by": "user",
  "expected_version": 3,
  "rules": [],
  "snippets": {}
}
```

说明：

- 该接口会替换整个 ruleset。
- 如果只是创建一个定时器，不推荐使用。
- 更推荐 `POST /automation/manage`。

## 14. 重置默认规则

接口：

```http
POST /api/v1/groups/{group_id}/automation/reset_baseline
Content-Type: application/json
```

请求：

```json
{
  "by": "user",
  "expected_version": 3
}
```

用途：

- 将工作组 automation 规则和 snippets 重置为系统默认基线。
- 这会影响已有自定义规则，谨慎使用。

## 15. 并发处理

Automation 配置有版本号 `version`。

推荐调用流程：

1. 调用 `GET /api/v1/groups/{group_id}/automation`
2. 读取 `result.version`
3. 调用 `POST /api/v1/groups/{group_id}/automation/manage` 时传入 `expected_version`
4. 如果返回 `version_conflict`，重新读取后再重试

版本冲突示例：

```json
{
  "ok": false,
  "error": {
    "code": "version_conflict",
    "message": "automation version mismatch",
    "details": {
      "expected_version": 3,
      "current_version": 4
    }
  }
}
```

如果不传 `expected_version`：

- 服务端会基于当前最新配置直接应用修改。
- 简单内部调用可以这样做。
- 多客户端同时编辑时建议传版本号。

## 16. 常见错误

| 错误码 | 说明 |
| --- | --- |
| `missing_group_id` | 缺少工作组 id |
| `group_not_found` | 工作组不存在 |
| `invalid_request` | 请求格式错误，例如 actions 为空 |
| `version_conflict` | 版本冲突 |
| `group_automation_manage_failed` | 增量管理失败 |
| `group_automation_update_failed` | 全量更新失败 |
| `group_automation_reset_baseline_failed` | 重置失败 |
| `read_only` | Web 只读模式，禁止写入 |

`group_automation_manage_failed` 常见原因：

- `rule.id` 为空
- `rule.id` 重复
- `update_rule` 指向不存在的规则
- `delete_rule` 指向不存在的规则
- 使用了旧版字段，例如 `name`、`schedule`、`actions`
- `group_state` 或 `actor_control` 使用了非 `at` 触发器
- peer actor 试图管理不属于自己的 personal 规则

## 17. 完整示例

### 17.1 每天上午 9 点提醒 foreman

```json
{
  "by": "user",
  "actions": [
    {
      "type": "create_rule",
      "rule": {
        "id": "daily_9am_foreman_check",
        "enabled": true,
        "scope": "group",
        "to": ["@foreman"],
        "trigger": {
          "kind": "cron",
          "cron": "0 9 * * *",
          "timezone": "Asia/Shanghai"
        },
        "action": {
          "kind": "notify",
          "title": "每日任务检查",
          "message": "请检查今天的任务进展，并确认是否需要调整分工。",
          "priority": "normal",
          "requires_ack": false
        }
      }
    }
  ]
}
```

### 17.2 每 30 分钟提醒所有 actor

```json
{
  "by": "user",
  "actions": [
    {
      "type": "create_rule",
      "rule": {
        "id": "every_30min_progress_check",
        "enabled": true,
        "scope": "group",
        "to": ["@all"],
        "trigger": {
          "kind": "interval",
          "every_seconds": 1800
        },
        "action": {
          "kind": "notify",
          "title": "进度同步",
          "message": "请同步当前进展、阻塞点和下一步计划。",
          "priority": "normal",
          "requires_ack": false
        }
      }
    }
  ]
}
```

### 17.3 指定时间提醒一次

北京时间 `2026-07-03 09:00:00` 对应 UTC `2026-07-03T01:00:00Z`。

```json
{
  "by": "user",
  "actions": [
    {
      "type": "create_rule",
      "rule": {
        "id": "one_time_review_20260703_0900",
        "enabled": true,
        "scope": "group",
        "to": ["@foreman"],
        "trigger": {
          "kind": "at",
          "at": "2026-07-03T01:00:00Z"
        },
        "action": {
          "kind": "notify",
          "title": "一次性项目复盘提醒",
          "message": "请复盘项目当前状态，并整理下一步安排。",
          "priority": "normal",
          "requires_ack": true
        }
      }
    }
  ]
}
```

### 17.4 禁用定时器

```json
{
  "by": "user",
  "actions": [
    {
      "type": "set_rule_enabled",
      "rule_id": "daily_9am_foreman_check",
      "enabled": false
    }
  ]
}
```

### 17.5 删除定时器

```json
{
  "by": "user",
  "actions": [
    {
      "type": "delete_rule",
      "rule_id": "daily_9am_foreman_check"
    }
  ]
}
```

## 18. curl 示例

创建每天 9 点提醒：

```bash
curl -X POST "http://127.0.0.1:8848/api/v1/groups/GROUP_ID/automation/manage" \
  -H "Content-Type: application/json" \
  -d '{
    "by": "user",
    "actions": [
      {
        "type": "create_rule",
        "rule": {
          "id": "daily_9am_foreman_check",
          "enabled": true,
          "scope": "group",
          "to": ["@foreman"],
          "trigger": {
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai"
          },
          "action": {
            "kind": "notify",
            "title": "每日任务检查",
            "message": "请检查今天的任务进展，并确认是否需要调整分工。",
            "priority": "normal",
            "requires_ack": false
          }
        }
      }
    ]
  }'
```

带版本号创建：

```bash
curl -X POST "http://127.0.0.1:8848/api/v1/groups/GROUP_ID/automation/manage" \
  -H "Content-Type: application/json" \
  -d '{
    "by": "user",
    "expected_version": 3,
    "actions": [
      {
        "type": "create_rule",
        "rule": {
          "id": "daily_9am_foreman_check",
          "enabled": true,
          "scope": "group",
          "to": ["@foreman"],
          "trigger": {
            "kind": "cron",
            "cron": "0 9 * * *",
            "timezone": "Asia/Shanghai"
          },
          "action": {
            "kind": "notify",
            "title": "每日任务检查",
            "message": "请检查今天的任务进展。",
            "priority": "normal",
            "requires_ack": false
          }
        }
      }
    ]
  }'
```

## 19. 前端代码调用

前端已有封装：

- `api.fetchAutomation(groupId)`
- `api.manageAutomation(groupId, actions, expectedVersion?)`
- `api.updateAutomation(groupId, ruleset, expectedVersion?)`
- `api.resetAutomationBaseline(groupId, expectedVersion?)`

示例：

```ts
import * as api from "@/services/api";

async function createDailyReminder(groupId: string) {
  const state = await api.fetchAutomation(groupId);
  const version = state.ok ? state.result.version : undefined;

  return api.manageAutomation(
    groupId,
    [
      {
        type: "create_rule",
        rule: {
          id: "daily_9am_foreman_check",
          enabled: true,
          scope: "group",
          to: ["@foreman"],
          trigger: {
            kind: "cron",
            cron: "0 9 * * *",
            timezone: "Asia/Shanghai",
          },
          action: {
            kind: "notify",
            title: "每日任务检查",
            message: "请检查今天的任务进展。",
            priority: "normal",
            requires_ack: false,
          },
        },
      },
    ],
    version,
  );
}
```

## 20. 实现位置

后端路由：

```text
src/no1/ports/web/routes/groups.py
```

相关接口：

- `group_automation_get`
- `group_automation_update`
- `group_automation_manage`
- `group_automation_reset_baseline`

规则协议：

```text
src/no1/contracts/v1/automation.py
```

后台执行逻辑：

```text
src/no1/daemon/automation/engine.py
```

前端 API 封装：

```text
web/src/services/api/context.ts
```

## 21. 推荐实践

1. 创建定时器优先使用 `POST /automation/manage`。
2. 规则 id 使用稳定英文标识，后续更新、禁用、删除都依赖它。
3. 多客户端场景传 `expected_version`，避免覆盖其他人的修改。
4. 周期定时优先用 `cron`，一次性提醒用 `at`，纯间隔提醒用 `interval`。
5. `notify` 目标建议使用 `@foreman` 或具体 actor id。
6. 如果希望北京时间触发，cron 使用 `timezone: "Asia/Shanghai"`。
7. 如果使用 `at`，建议调用方自己把本地时间转换成 UTC `Z` 时间。
8. 注意 actor 必须启用且运行中，通知才会实际投递。

