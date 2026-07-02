# Group Automation API

This document describes how external callers create timer jobs for a working group through the existing Web HTTP API.

Working group timers are stored as automation rules in the group's `automation.rules` configuration. They are executed by the daemon automation loop; callers do not need a separate timer service or a new `/timers` endpoint.

## Base Path

Group-scoped automation routes use:

```text
/api/v1/groups/{group_id}/automation
```

The recommended mutation endpoint is:

```text
POST /api/v1/groups/{group_id}/automation/manage
```

Use this endpoint for create, update, enable, disable, and delete operations. It updates only the requested rules and avoids replacing the full ruleset accidentally.

## Authentication and Access

All routes are group-scoped and use the same Web API group permission checks as other group routes.

- If access tokens are enabled, the caller must be an authenticated user with access to the target group.
- Mutating calls are blocked when Web runs in read-only exhibit mode.
- The request body should normally use `"by": "user"` for user-initiated API calls.

Read-only mode returns:

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

## Read Automation State

```http
GET /api/v1/groups/{group_id}/automation
```

Returns the effective ruleset, snippet catalog, runtime status, and version.

Example response:

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
            "title": "Daily reminder",
            "message": "Check today's task progress.",
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
    "supported_vars": ["interval_minutes", "group_title", "actor_names", "scheduled_at"],
    "version": 3,
    "server_now": "2026-07-02T08:00:00Z",
    "config_path": "/path/to/group.yaml"
  }
}
```

Status fields:

| Field | Description |
| --- | --- |
| `last_fired_at` | Last successful fire timestamp, or empty string. |
| `last_error_at` | Last validation or execution error timestamp, or empty string. |
| `last_error` | Last error message, or empty string. |
| `next_fire_at` | Next estimated fire timestamp in UTC, or empty string. |
| `completed` | `true` only for one-time `at` rules that already fired. |
| `completed_at` | Completion timestamp for completed one-time rules. |

## Create a Timer Rule

```http
POST /api/v1/groups/{group_id}/automation/manage
Content-Type: application/json
```

Request:

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
          "title": "Daily reminder",
          "message": "Check today's task progress.",
          "priority": "normal",
          "requires_ack": false
        }
      }
    }
  ]
}
```

Response includes the same automation payload as `GET /automation`, plus mutation metadata:

```json
{
  "ok": true,
  "result": {
    "group_id": "g-demo",
    "ruleset": { "rules": [], "snippets": {} },
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

## Rule Object

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

Required fields for normal group timer creation:

| Field | Requirement |
| --- | --- |
| `id` | Required. Must be unique inside the group's automation rules. Use a stable, URL-safe identifier such as `daily_morning_reminder`. |
| `enabled` | Optional. Defaults to `true`; set explicitly for clarity. |
| `scope` | Use `"group"` for a group timer. |
| `to` | Notification recipients. Use `["@foreman"]`, `["@all"]`, `["@peers"]`, or concrete actor ids. |
| `trigger` | Timer schedule. See trigger types below. |
| `action` | What to do when the timer fires. Use `notify` for reminder jobs. |

For `scope: "personal"`, `owner_actor_id` must be set. Peer actors can only manage their own personal notify rules.

## Trigger Types

### Cron Trigger

Use cron for recurring schedules at fixed local times.

```json
{
  "kind": "cron",
  "cron": "0 9 * * *",
  "timezone": "Asia/Shanghai"
}
```

Rules:

- `cron` uses five fields: `minute hour day_of_month month day_of_week`.
- `timezone` is an IANA timezone name. Use `Asia/Shanghai` for China Standard Time.
- The scheduler matches at minute precision.
- A matching cron slot is recorded before delivery so the same minute does not fire repeatedly.

Examples:

| Schedule | Trigger |
| --- | --- |
| Every day at 09:00 Shanghai time | `{ "kind": "cron", "cron": "0 9 * * *", "timezone": "Asia/Shanghai" }` |
| Every weekday at 18:30 Shanghai time | `{ "kind": "cron", "cron": "30 18 * * 1-5", "timezone": "Asia/Shanghai" }` |
| Every Monday at 10:00 UTC | `{ "kind": "cron", "cron": "0 10 * * 1", "timezone": "UTC" }` |

### Interval Trigger

Use interval for repeated reminders measured from the last fire time.

```json
{
  "kind": "interval",
  "every_seconds": 3600
}
```

Rules:

- `every_seconds` must be at least `1`.
- A newly created interval rule starts counting from the first automation check after it is saved.
- It does not fire immediately on creation.

### One-Time Trigger

Use `at` for a one-shot timer.

```json
{
  "kind": "at",
  "at": "2026-07-03T01:00:00Z"
}
```

Rules:

- `at` must be an RFC3339 timestamp.
- UTC timestamps ending with `Z` are recommended.
- After successful delivery, the rule is automatically disabled so it will not fire again.
- Completed one-time rules report `completed: true` in `status`.

## Action Types

### Notify Action

Use `notify` for timer reminders.

```json
{
  "kind": "notify",
  "title": "Daily reminder",
  "message": "Check today's task progress.",
  "priority": "normal",
  "requires_ack": false
}
```

Fields:

| Field | Description |
| --- | --- |
| `title` | Notification title. Defaults to `Reminder` at delivery time when empty. |
| `message` | Notification body. Required unless `snippet_ref` resolves to a snippet. |
| `snippet_ref` | Optional snippet id. If set and found, the snippet text is rendered instead of `message`. |
| `priority` | Notification priority. Typical value is `"normal"`. |
| `requires_ack` | Whether the recipient should acknowledge the notification. |

Delivery behavior:

- Recipients are resolved from the rule-level `to` field.
- `notify` only sends to enabled actors that are currently running.
- If no enabled recipient actor matches, the rule does not send.

Supported message template variables:

| Variable | Meaning |
| --- | --- |
| `{{interval_minutes}}` | Interval length in minutes for interval triggers. |
| `{{group_title}}` | Group title. |
| `{{actor_names}}` | Enabled actor display names. |
| `{{scheduled_at}}` | Scheduled fire timestamp. |

### Group State Action

```json
{
  "kind": "group_state",
  "state": "paused"
}
```

Allowed states:

- `active`
- `idle`
- `paused`
- `stopped`

Constraint: `group_state` only supports one-time `at` triggers.

### Actor Control Action

```json
{
  "kind": "actor_control",
  "operation": "restart",
  "targets": ["@all"]
}
```

Allowed operations:

- `start`
- `stop`
- `restart`

Constraint: `actor_control` only supports one-time `at` triggers.

## Manage Actions

Use `POST /automation/manage` with one or more actions.

### Create Rule

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
      "message": "Check today's task progress."
    }
  }
}
```

Fails if `rule.id` already exists.

### Update Rule

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
      "title": "Daily reminder",
      "message": "The reminder time changed to 09:30.",
      "priority": "normal",
      "requires_ack": false
    }
  }
}
```

`update_rule` requires the full replacement rule object.

### Enable or Disable Rule

```json
{
  "type": "set_rule_enabled",
  "rule_id": "daily_morning_reminder",
  "enabled": false
}
```

### Delete Rule

```json
{
  "type": "delete_rule",
  "rule_id": "daily_morning_reminder"
}
```

### Replace All Rules

```json
{
  "type": "replace_all_rules",
  "ruleset": {
    "rules": [],
    "snippets": {}
  }
}
```

Use this only when the caller intentionally owns the full ruleset. For timer creation, prefer `create_rule`.

## Full Update Endpoint

```http
PUT /api/v1/groups/{group_id}/automation
Content-Type: application/json
```

This endpoint replaces all rules and snippets.

```json
{
  "by": "user",
  "expected_version": 3,
  "rules": [],
  "snippets": {}
}
```

Use this endpoint only for full ruleset editors. For normal timer APIs, use `POST /automation/manage`.

## Reset Baseline

```http
POST /api/v1/groups/{group_id}/automation/reset_baseline
Content-Type: application/json
```

```json
{
  "by": "user",
  "expected_version": 3
}
```

This resets automation rules and snippets to built-in baseline defaults.

## Concurrency

Automation configuration has a monotonic `version`.

Recommended write flow:

1. Call `GET /api/v1/groups/{group_id}/automation`.
2. Read `result.version`.
3. Send `expected_version` in the manage request.
4. If the server returns `version_conflict`, reload automation state and retry with the new version.

Version conflict response:

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

If `expected_version` is omitted, the server applies the request to the current ruleset.

## Error Codes

| Code | Meaning |
| --- | --- |
| `group_not_found` | The target group does not exist. |
| `missing_group_id` | The request did not identify a group. |
| `invalid_request` | Request shape is invalid, such as an empty actions array. |
| `version_conflict` | `expected_version` does not match the current automation version. |
| `group_automation_manage_failed` | Action validation failed, rule id already exists, rule not found, unsupported action type, or permission restriction. |
| `group_automation_update_failed` | Full ruleset replacement failed validation. |
| `group_automation_reset_baseline_failed` | Baseline reset failed. |
| `read_only` | Web is running in read-only mode and blocked the mutation. |

Legacy rule shapes are rejected. Use canonical fields: `id`, `trigger`, `action`, `to`, `scope`, and `owner_actor_id`.

## Curl Examples

Create a daily cron reminder:

```bash
curl -X POST "http://127.0.0.1:8848/api/v1/groups/GROUP_ID/automation/manage" \
  -H "Content-Type: application/json" \
  -d '{
    "by": "user",
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
            "title": "Daily reminder",
            "message": "Check today'\''s task progress.",
            "priority": "normal",
            "requires_ack": false
          }
        }
      }
    ]
  }'
```

Create a one-time reminder:

```bash
curl -X POST "http://127.0.0.1:8848/api/v1/groups/GROUP_ID/automation/manage" \
  -H "Content-Type: application/json" \
  -d '{
    "by": "user",
    "actions": [
      {
        "type": "create_rule",
        "rule": {
          "id": "one_time_project_check",
          "enabled": true,
          "scope": "group",
          "to": ["@foreman"],
          "trigger": {
            "kind": "at",
            "at": "2026-07-03T01:00:00Z"
          },
          "action": {
            "kind": "notify",
            "title": "Project check",
            "message": "Review the project status.",
            "priority": "normal",
            "requires_ack": false
          }
        }
      }
    ]
  }'
```

Disable a rule:

```bash
curl -X POST "http://127.0.0.1:8848/api/v1/groups/GROUP_ID/automation/manage" \
  -H "Content-Type: application/json" \
  -d '{
    "by": "user",
    "actions": [
      {
        "type": "set_rule_enabled",
        "rule_id": "daily_morning_reminder",
        "enabled": false
      }
    ]
  }'
```

## Frontend Helper API

The Web frontend already wraps these routes:

```ts
import * as api from "@/services/api";

const state = await api.fetchAutomation(groupId);
const version = state.ok ? state.result.version : undefined;

await api.manageAutomation(
  groupId,
  [
    {
      type: "create_rule",
      rule: {
        id: "daily_morning_reminder",
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
          title: "Daily reminder",
          message: "Check today's task progress.",
          priority: "normal",
          requires_ack: false,
        },
      },
    },
  ],
  version,
);
```

