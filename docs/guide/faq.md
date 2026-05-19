# FAQ

Frequently asked questions about OneColleague.

## Installation & Setup

### How do I install OneColleague?

```bash
# From PyPI
pip install -U no1

# From TestPyPI (explicit RC testing)
pip install -U --pre \
  --index-url https://test.pypi.org/simple \
  --extra-index-url https://pypi.org/simple \
  no1

# From source
git clone https://github.com/ChesterRa/onecolleague
cd onecolleague
pip install -e .
```

### How do I upgrade from an older version (0.3.x)?

You must uninstall the old version first:

```bash
# For pipx users
pipx uninstall no1

# For pip users
pip uninstall no1

# Remove any leftover binaries
rm -f ~/.local/bin/onecolleague ~/.local/bin/onecolleagued
```

Then install the new version. Note that 0.4.x has a completely different command structure from 0.3.x.

### What are the system requirements?

- Python 3.9+
- macOS, Linux, or Windows
- At least one supported agent runtime CLI

### How do I check if OneColleague is working?

```bash
onecolleague doctor
```

This checks Python version, available runtimes, and daemon status.

## Agents

### Which AI agents are supported?

- Claude Code (`claude`)
- Codex CLI (`codex`)
- Droid (`droid`)
- Gemini CLI (`gemini`)
- Kimi CLI (`kimi`)
- Amp (`amp`)
- Auggie (`auggie`)
- Neovate (`neovate`)
- Custom (manual fallback; provide your own command and MCP wiring)

### What's the difference between Foreman and Peer?

- **Foreman**: The first enabled actor. Coordinates work, receives system notifications, can manage other actors.
- **Peer**: Independent expert. Has their own judgment, can only manage themselves.

### How do I add a custom agent?

```bash
onecolleague actor add my-agent --runtime custom --command "my-custom-cli"
```

### Agent won't start?

1. Check the terminal tab for error messages
2. Verify MCP is configured: `onecolleague setup --runtime <name>`
3. Ensure the CLI is installed and in PATH
4. Try: `onecolleague actor restart <actor_id>`

## Messaging

### How do I send a message to a specific agent?

```bash
onecolleague send "Please do X" --to agent-name
```

Or in the Web UI, type `@agent-name` in your message.

### Agent isn't responding to my messages?

1. Check if the agent is running (green indicator in Web UI)
2. Check the inbox: `onecolleague inbox --actor-id <agent-id>`
3. Look at the terminal tab for errors
4. Try restarting the agent

### How do read receipts work?

Agents call `onecolleague_inbox_mark_read` to mark messages as read. This is cumulative - marking message X means all messages up to X are read.

## Remote Access

### How do I access OneColleague from my phone?

**Option 1: Cloudflare Tunnel**
```bash
cloudflared tunnel --url http://127.0.0.1:8848
```

**Option 2: IM Bridge**
```bash
onecolleague im set telegram --token-env TELEGRAM_BOT_TOKEN
onecolleague im start
```

**Option 3: Tailscale**
```bash
CCCC_WEB_HOST=$(tailscale ip -4) onecolleague
```

### Is it safe to expose the Web UI?

Before exposing the Web UI, create an **Admin Access Token** in **Settings > Web Access** and then sign in with that token.

Use Cloudflare Access or Tailscale for additional security.

## Performance

### How much resources does OneColleague use?

- Daemon: Minimal (Python async)
- Web UI: Standard React app
- Agents: Depends on the runtime

### The ledger file is getting large

OneColleague supports snapshot/compaction. Large blobs are stored separately in the `blobs/` directory.

### How do I reduce message latency?

1. Ensure agents are already running
2. Use specific @mentions instead of broadcasts
3. Keep the daemon running (don't restart frequently)

## Troubleshooting

### Daemon won't start

```bash
onecolleague daemon status  # Check if already running
onecolleague daemon stop    # Stop existing instance
onecolleague daemon start   # Start fresh
```

### Port 8848 is unavailable

```bash
CCCC_WEB_PORT=9000 onecolleague
```

On Windows, Hyper-V / WSL / WinNAT / HNS can reserve a TCP port even when no
process is listening on it. If `8848` still fails to start and you do not see an
owning PID, check the excluded port ranges:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

If `8848` falls inside one of those ranges, start OneColleague on a different port:

```powershell
onecolleague web --port 9000
```

### MCP not working

```bash
onecolleague setup --runtime <name>  # Re-run setup
onecolleague doctor                  # Check configuration
```

### Web UI not loading

1. Check daemon is running: `onecolleague daemon status`
2. Check the port: http://127.0.0.1:8848/
3. Check browser console for errors
4. Try a different browser

## Concepts

### What is a Working Group?

A working group is like an IM group chat with execution capabilities. It includes:
- An append-only ledger (message history)
- One or more actors (agents)
- Optional scopes (project directories)

### What is the Ledger?

The ledger is an append-only event stream that stores all messages, state changes, and decisions. It's the single source of truth for a working group.

### What is MCP?

MCP (Model Context Protocol) is how agents interact with OneColleague. It exposes a rich tool surface for messaging, context management, automation, and system control.

### What is a Scope?

A scope is a project directory attached to a working group. Agents work within scopes, and events are attributed to scopes.
