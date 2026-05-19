# CLI Reference

Complete command reference for the OneColleague CLI.

## Global Commands

### `onecolleague`

Start the daemon and Web UI together.

```bash
onecolleague            # Start daemon + Web UI
onecolleague --help             # Show help
```

### `onecolleague doctor`

Check your environment and diagnose issues.

```bash
onecolleague doctor             # Full environment check
```

### `onecolleague runtime list`

List available agent runtimes.

```bash
onecolleague runtime list       # List detected runtimes
onecolleague runtime list --all # List all supported runtimes
```

## Daemon Commands

### `onecolleague daemon`

Manage the OneColleague daemon.

```bash
onecolleague daemon status      # Check daemon status
onecolleague daemon start       # Start daemon
onecolleague daemon stop        # Stop daemon
```

Notes:
- `onecolleague daemon start` refuses to spawn a duplicate daemon if the pid-file process is still alive but IPC is not responding.
- In that case, run `onecolleague daemon stop` (or clean stale runtime state) before retrying start.

## Group Commands

### `onecolleague attach`

Create or attach to a working group.

```bash
onecolleague attach .           # Attach current directory as scope
onecolleague attach /path/to/project
```

### `onecolleague groups`

List all working groups.

```bash
onecolleague groups             # List groups
```

### `onecolleague use`

Switch to a different working group.

```bash
onecolleague use <group_id>     # Switch to group
```

### `onecolleague group`

Manage the current working group.

```bash
onecolleague group create --title "my-group"         # Create group
onecolleague group show <group_id>                   # Show group metadata
onecolleague group update --group <id> --title "..." # Update title/topic
onecolleague group use <group_id> .                  # Set active scope
onecolleague group start --group <id>                # Start group actors
onecolleague group stop --group <id>                 # Stop group actors
onecolleague group set-state idle --group <id>       # Set state: active/idle/paused/stopped
onecolleague group detach-scope <scope_key> --group <id>
onecolleague group delete --group <id> --confirm <id>
```

## Actor Commands

### `onecolleague actor add`

Add a new actor to the group.

```bash
onecolleague actor add <actor_id> --runtime claude
onecolleague actor add <actor_id> --runtime codex
onecolleague actor add <actor_id> --runtime web_model
onecolleague actor add <actor_id> --runtime custom --command "my-agent"
```

Options:
- `--runtime`: Agent runtime (claude, codex, web_model, droid, etc.)
- `--command`: Custom command (for custom runtime)
- `--runner`: Runner type (pty or headless; web_model is headless-only)
- `--title`: Display title

For the ChatGPT Web Model actor, create the actor first, then finish MCP URL and chat binding in `Settings > Global > ChatGPT Web Model`.

### `onecolleague actor`

Manage actors.

```bash
onecolleague actor list                    # List actors
onecolleague actor start <actor_id>        # Start actor
onecolleague actor stop <actor_id>         # Stop actor
onecolleague actor restart <actor_id>      # Restart actor
onecolleague actor remove <actor_id>       # Remove actor
onecolleague actor update <actor_id> ...   # Update actor settings
onecolleague actor secrets <actor_id> ...  # Manage runtime-only secrets
```

## Message Commands

### `onecolleague send`

Send a message.

```bash
onecolleague send "Hello"                  # No --to: default recipient policy applies (default: foreman)
onecolleague send "Hello" --to @foreman    # Send to foreman
onecolleague send "Hello" --to peer-1      # Send to specific actor
onecolleague send "Announcement" --to @all # Explicit broadcast
```

### `onecolleague tracked-send`

Create a task and send one linked delegation message.

```bash
onecolleague tracked-send "Please implement this and reply with validation evidence." \
  --to peer-1 \
  --title "Implement feature" \
  --outcome "Feature is implemented and validation evidence is reported"
```

### `onecolleague reply`

Reply to a message.

```bash
onecolleague reply <event_id> "Reply text"
```

### `onecolleague inbox`

View inbox.

```bash
onecolleague inbox --actor-id <id>         # View actor unread messages
onecolleague inbox --actor-id <id> --mark-read
```

### `onecolleague tail`

Tail the ledger.

```bash
onecolleague tail                          # Show recent events
onecolleague tail -n 50                    # Show last 50 events
onecolleague tail -f                       # Follow new events
```

## IM Bridge Commands

### `onecolleague im`

Manage IM Bridge.

```bash
onecolleague im set telegram --token-env TELEGRAM_BOT_TOKEN
onecolleague im set slack --bot-token-env SLACK_BOT_TOKEN --app-token-env SLACK_APP_TOKEN
onecolleague im set discord --token-env DISCORD_BOT_TOKEN
onecolleague im set feishu --app-key-env FEISHU_APP_ID --app-secret-env FEISHU_APP_SECRET
onecolleague im set dingtalk --app-key-env DINGTALK_APP_KEY --app-secret-env DINGTALK_APP_SECRET --robot-code-env DINGTALK_ROBOT_CODE

onecolleague im start                      # Start IM bridge
onecolleague im stop                       # Stop IM bridge
onecolleague im status                     # Check IM bridge status
onecolleague im logs                       # View IM bridge logs
onecolleague im logs -f                    # Follow IM bridge logs
```

## Group Space Commands

### `onecolleague space`

Manage Group Space provider-backed shared memory.

```bash
onecolleague space status
onecolleague space credential status
onecolleague space credential set --auth-json '{"cookies":[{"name":"SID","value":"...","domain":".google.com"}]}'
onecolleague space credential set --auth-json-file ./notebooklm.storage_state.json
onecolleague space credential clear
onecolleague space health

onecolleague space bind [remote_space_id]    # omit to auto-create NotebookLM notebook
onecolleague space unbind
onecolleague space sync --force

onecolleague space ingest --kind context_sync --payload '{"vision":"v0.5 plan"}'
onecolleague space ingest --kind resource_ingest --payload '{"path":"docs/spec.md"}' --idempotency-key ingest-docs-1

onecolleague space query "What is the latest shared plan?"
onecolleague space query "Summarize risks from these sources" --options '{"source_ids":["src_1","src_2"]}'

onecolleague space jobs list
onecolleague space jobs list --state failed --limit 20
onecolleague space jobs retry <job_id>
onecolleague space jobs cancel <job_id>
```

Notes:
- `--group` is optional; defaults to the active group.
- Current provider is `notebooklm`.
- `--payload` and `--options` must be JSON objects.
- `onecolleague space query --options` only supports `source_ids` (array of source IDs).
- `language` / `lang` are not valid query options (put language requirement in query text).
- Provider credentials are write-only; CLI/Web only return masked metadata.
- `onecolleague space health` validates credential format and adapter compatibility.
- When a group is bound, curated `context_sync` exports are also auto-enqueued from `context_sync` updates.
- `onecolleague space sync` performs two-way reconcile for Group Space:
  - local `repo/space/` files -> provider sources,
  - provider source/artifact projection -> local `repo/space/` (`.sync/remote-sources` and `artifacts/`).

## Setup Commands

### `onecolleague setup`

Configure MCP for an agent runtime.

```bash
onecolleague setup --runtime claude        # Auto-configure for Claude Code
onecolleague setup --runtime codex         # Auto-configure for Codex
onecolleague setup --runtime kimi          # Auto-configure for Kimi CLI
```

### `onecolleague update`

Upgrade OneColleague in the current Python environment.

```bash
onecolleague update                        # Upgrade using the detected channel
onecolleague update --channel stable       # Force the stable PyPI channel
onecolleague update --channel rc           # Force the TestPyPI RC channel
onecolleague update --check                # Show install detection + planned command
```

Notes:
- The default channel follows the detected install metadata when possible, then falls back to `stable`.
- Editable and local-path installs are reported but not updated automatically.

## Web Commands

### `onecolleague web`

Start only the Web UI (daemon must be running).

```bash
onecolleague web                           # Start Web UI
onecolleague web --port 9000               # Custom port
```

## MCP Commands

### `onecolleague mcp`

Start the MCP server (for agent integration).

```bash
onecolleague mcp                           # Start MCP server (stdio mode)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CCCC_HOME` | `~/.cccc` | Runtime home directory |
| `CCCC_WEB_HOST` | `127.0.0.1` | Web UI bind address |
| `CCCC_WEB_PORT` | `8848` | Web UI port |
| `CCCC_WEB_READY_TIMEOUT_SECONDS` | `10` | Supervised Web child readiness timeout before OneColleague treats startup as failed |
| `CCCC_WEB_LOG_LEVEL` | `INFO` | Web log level |
