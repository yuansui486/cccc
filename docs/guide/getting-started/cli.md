# CLI Quick Start

Get started with OneColleague using the command line.

## Step 1: Navigate to Your Project

```bash
cd /path/to/your/project
```

## Step 2: Create a Working Group

```bash
onecolleague attach .
```

This binds the current directory as a "scope" and creates a working group.

## Step 3: Configure MCP for Your Runtime

```bash
onecolleague setup --runtime claude   # or codex, droid, gemini, kimi
```

This configures the MCP (Model Context Protocol) so agents can interact with OneColleague.

## Step 4: Add Your First Agent

```bash
onecolleague actor add assistant --runtime claude
```

The first enabled actor automatically becomes the "foreman" (coordinator).

## Step 5: Start the Agent

```bash
onecolleague group start
```

Or start a specific agent:

```bash
onecolleague actor start assistant
```

## Step 6: Send a Message

```bash
onecolleague send "Hello! Please introduce yourself."
```

## Step 7: View Responses

Watch the ledger in real-time:

```bash
onecolleague tail -f
```

Or check inbox:

```bash
onecolleague inbox --actor-id assistant
```

## Adding More Agents

Add a second agent:

```bash
onecolleague actor add reviewer --runtime codex
onecolleague actor start reviewer
```

Send to specific agents:

```bash
onecolleague send "Please implement the feature" --to assistant
onecolleague send "Please review the code" --to reviewer
onecolleague send "Please coordinate the next step" --to @foreman
onecolleague send "Team-wide constraint: pause deploys until CI is green" --to @all
```

Use task-backed delegation when the work should survive chat context switches and needs an owner, outcome, or completion evidence:

```bash
onecolleague tracked-send "Please implement the feature and reply with validation evidence." \
  --to assistant \
  --title "Implement feature" \
  --outcome "Feature is implemented and validation evidence is reported"
```

## Reply to Messages

```bash
# Find the event ID from onecolleague tail
onecolleague reply evt_abc123 "Thanks, that looks good!"
```

## Common Commands

### Group Management

```bash
onecolleague groups              # List all groups
onecolleague use <group_id>      # Switch group
onecolleague active              # Show active group
onecolleague group show <group_id> # Show group metadata
onecolleague group start         # Start all agents
onecolleague group stop          # Stop all agents
```

### Actor Management

```bash
onecolleague actor list                    # List actors
onecolleague actor add <id> --runtime <r>  # Add actor
onecolleague actor start <id>              # Start actor
onecolleague actor stop <id>               # Stop actor
onecolleague actor restart <id>            # Restart actor
onecolleague actor remove <id>             # Remove actor
```

### Messaging

```bash
onecolleague send "message"                # No --to: default recipient policy applies (default: foreman)
onecolleague send "msg" --to assistant     # To specific actor
onecolleague send "msg" --to @foreman      # Ask the coordinator
onecolleague send "msg" --to @all          # Explicit broadcast, not default task dispatch
onecolleague tracked-send "work" --to assistant --title "Task title" --outcome "Done criterion"
onecolleague reply <event_id> "response"   # Reply to message
onecolleague inbox --actor-id assistant    # View unread for one actor
onecolleague tail -n 50                    # Recent events
onecolleague tail -f                       # Follow events
```

### Daemon Control

```bash
onecolleague daemon status    # Check status
onecolleague daemon start     # Start daemon
onecolleague daemon stop      # Stop daemon
```

## Start Web UI (Optional)

While using CLI, you can also open the Web UI:

```bash
onecolleague   # Starts daemon + Web UI
```

Or just the Web UI (if daemon is already running):

```bash
onecolleague web
```

Access at http://127.0.0.1:8848/

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CCCC_HOME` | `~/.cccc` | Runtime directory |
| `CCCC_WEB_PORT` | `8848` | Web UI port |
| `CCCC_WEB_READY_TIMEOUT_SECONDS` | `10` | Web startup readiness timeout for slower machines |
| `OneColleague_LOG_LEVEL` | `INFO` | Log verbosity |

## Example Workflow

```bash
# Setup
cd ~/projects/my-app
onecolleague attach .
onecolleague setup --runtime claude
onecolleague actor add dev --runtime claude

# Work
onecolleague group start
onecolleague send "Please plan the smallest safe authentication task." --to @foreman
onecolleague tracked-send "Please implement the first authentication task and reply with validation evidence." \
  --to dev \
  --title "Implement first authentication slice" \
  --outcome "Implementation is complete and validation evidence is reported"

# Monitor
onecolleague tail -f

# Interact
onecolleague reply evt_123 "Use JWT tokens please"
onecolleague send "What's the progress?" --to dev

# Cleanup
onecolleague group stop
```

## Troubleshooting

### Daemon not starting?

```bash
onecolleague daemon status
onecolleague daemon stop      # Stop any stuck instance
onecolleague daemon start
```

### Agent not responding?

```bash
# Check agent status
onecolleague actor list

# Restart the agent
onecolleague actor restart <actor_id>

# Check MCP setup
onecolleague setup --runtime <name>
```

### Can't find my group?

```bash
# List all groups
onecolleague groups

# Re-attach if needed
cd /path/to/project
onecolleague attach .
```

## Next Steps

- [Workflows](/guide/workflows) - Learn collaboration patterns
- [CLI Reference](/reference/cli) - Complete command reference
- [IM Bridge](/guide/im-bridge/) - Set up mobile access
