# Workflow Examples

Common patterns for using OneColleague to coordinate AI agents.

## Solo Development with One Agent

The simplest setup: one agent assisting you with a project.

### Setup

```bash
cd /your/project
onecolleague attach .
onecolleague actor add assistant --runtime claude
onecolleague
```

### Workflow

1. Open the Web UI at http://127.0.0.1:8848/
2. Start the agent
3. Send quick requests via chat, and use task-backed delegation when the work needs an owner, outcome, or evidence trail
4. Watch the agent work in the terminal tab
5. Review changes and provide feedback

## Pair Programming with Two Agents

Use one agent for implementation and another for review.

### Setup

```bash
onecolleague actor add implementer --runtime claude
onecolleague actor add reviewer --runtime codex
onecolleague group start
```

### Workflow

1. Send implementation tasks to `@implementer`, or use task-backed delegation when the work needs completion evidence
2. When complete, ask `@reviewer` to review the changes
3. Iterate based on review feedback

### Tips

- The reviewer can catch bugs and suggest improvements
- Use different runtimes for diverse perspectives
- Keep tasks focused and specific

## Multi-Agent Team

For complex projects, use multiple specialized agents.

### Setup Example

```bash
onecolleague actor add architect --runtime claude    # Design decisions
onecolleague actor add frontend --runtime codex      # UI implementation
onecolleague actor add backend --runtime droid       # API implementation
onecolleague actor add tester --runtime kimi         # Testing
```

### Coordination

- The first enabled actor (architect) becomes foreman
- Foreman coordinates work across peers
- Use @mentions to direct tasks to specific agents
- Use Context panel for shared understanding

### Best Practices

- Define clear responsibilities for each agent
- Use milestones to track progress
- Regular check-ins to ensure alignment

## Remote Monitoring via Phone

Monitor and control your agents from anywhere.

### Setup Options

**Option 1: Cloudflare Tunnel (Recommended)**

```bash
# Quick (temporary URL)
cloudflared tunnel --url http://127.0.0.1:8848

# Stable (custom domain)
cloudflared tunnel create onecolleague
cloudflared tunnel route dns onecolleague onecolleague.yourdomain.com
cloudflared tunnel run onecolleague
```

**Option 2: IM Bridge**

```bash
onecolleague im set telegram --token-env TELEGRAM_BOT_TOKEN
onecolleague im start
```

Then use your Telegram app to:
- Send messages to agents
- Receive status updates
- Control the group with slash commands

### Workflow

1. Set up remote access
2. Leave agents running on your development machine
3. Monitor and send commands from your phone
4. Receive notifications on important events

## Overnight Tasks

Run long-running tasks unattended.

### Setup

1. Define clear success criteria
2. Set up IM Bridge for notifications
3. Configure automation timeouts

### Example

```bash
# Configure notifications
onecolleague im set telegram --token-env TELEGRAM_BOT_TOKEN
onecolleague im start

# Start the task
onecolleague tracked-send "Please refactor the authentication module and report progress every hour." \
  --to @foreman \
  --title "Refactor authentication module" \
  --outcome "Refactor is complete, risks are reported, and validation evidence is provided"
```

### Monitoring

- IM Bridge sends updates to your phone
- Check progress via Web UI when convenient
- Agents notify on completion or errors
