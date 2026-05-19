# Getting Started

Get OneColleague running in 10 minutes.

## Choose Your Approach

OneColleague offers two ways to get started:

<div class="vp-card-container">

### [Web UI Quick Start](./web)

**Recommended for most users**

- Visual interface for managing agents
- Point-and-click configuration
- Real-time terminal view
- Mobile-friendly

### [CLI Quick Start](./cli)

**For terminal enthusiasts**

- Full control via command line
- Scriptable and automatable
- Great for CI/CD integration
- Power user features

### [Docker Deployment](./docker)

**For servers and teams**

- One-command deployment
- Pre-installed AI agent CLIs
- Persistent data with volumes
- Docker Compose and K8s ready

</div>

## Prerequisites

Both approaches require:

- **Python 3.9+** installed
- At least one AI agent CLI:
  - [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (recommended)
  - [Codex CLI](https://github.com/openai/codex)
  - [Kimi CLI](https://github.com/MoonshotAI/kimi-cli)
- Or a ChatGPT account with remote MCP connector support for the ChatGPT Web Model runtime
- Or a custom runtime command if you wire MCP manually

## Installation

### Upgrading from older versions

If you have an older version of no1 installed (e.g., 0.3.x), you must uninstall it first:

```bash
# For pipx users
pipx uninstall no1

# For pip users
pip uninstall no1

# Remove any leftover binaries if needed
rm -f ~/.local/bin/onecolleague ~/.local/bin/onecolleagued
```

::: warning Version 0.4.x Breaking Changes
Version 0.4.x has a completely different command structure from 0.3.x. The old `init`, `run`, `bridge` commands are replaced with `attach`, `daemon`, `mcp`, etc.
:::

### From PyPI

```bash
pip install -U no1
```

### From TestPyPI (for explicit RC testing)

```bash
pip install -U --pre \
  --index-url https://test.pypi.org/simple \
  --extra-index-url https://pypi.org/simple \
  no1
```

### From Source

```bash
git clone https://github.com/ChesterRa/onecolleague
cd onecolleague
pip install -e .
```

## Verify Installation

```bash
onecolleague doctor
```

This checks Python version, available runtimes, and system configuration.

## Next Steps

- [Web UI Quick Start](./web) - Get started with the visual interface
- [CLI Quick Start](./cli) - Get started with the command line
- [Docker Deployment](./docker) - Deploy OneColleague in a Docker container
- [SDK Overview](/sdk/) - Integrate OneColleague into external apps/services
- [Use Cases](/guide/use-cases) - Learn high-ROI real-world patterns
- [Operations Runbook](/guide/operations) - Run OneColleague with operator-grade reliability
- [Positioning](/reference/positioning) - Decide where OneColleague should sit in your stack
