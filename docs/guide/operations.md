# Operations Runbook

This page is for operators who need reliable day-to-day OneColleague execution.

## 1) Runtime Topology

Default runtime home:
- `CCCC_HOME=~/.cccc`

Key paths:
- `~/.cccc/registry.json`
- `~/.cccc/daemon/onecolleagued.sock`
- `~/.cccc/daemon/onecolleagued.log`
- `~/.cccc/groups/<group_id>/group.yaml`
- `~/.cccc/groups/<group_id>/ledger.jsonl`

## 2) Startup and Health Checks

### Start

```bash
onecolleague
```

### Health Baseline

```bash
onecolleague doctor
onecolleague daemon status
onecolleague groups
```

Expected:
- daemon reachable
- runtimes detected
- active group list loadable

## 3) Incident Triage Order

When a group appears stuck:

1. Check daemon health.
2. Check group state (`active/idle/paused/stopped`).
3. Check actor runtime status.
4. Check message obligations (reply-required/attention ack).
5. Check automation and delivery policy.

Useful commands:

```bash
onecolleague daemon status
onecolleague actor list
onecolleague inbox --actor-id <actor_id>
onecolleague tail -n 100 -f
```

## 4) Fast Recovery Playbook

### Actor-level recovery (preferred)

```bash
onecolleague actor restart <actor_id>
```

Use this before group-level restart.

### Group-level recovery

```bash
onecolleague group stop
onecolleague group start
```

### Daemon-level recovery (last resort)

```bash
onecolleague daemon stop
onecolleague daemon start
```

## 5) Secure Remote Access

Required baseline:
- Create an **Admin Access Token** in **Settings > Web Access** before any non-local exposure.
- Use Cloudflare Access or Tailscale for network boundary.

Do not:
- Expose Web UI directly without an access gateway.
- Store secrets in repo files.

## 6) Upgrade Playbook (RC-safe)

### Before upgrade

1. Stop active high-risk sessions.
2. Backup `CCCC_HOME`.
3. Record current version and smoke state.

### Upgrade

```bash
python -m pip install -U no1
```

### After upgrade

```bash
onecolleague doctor
onecolleague daemon status
onecolleague mcp
```

Run a small end-to-end smoke:
- create/attach group
- add/start actor
- send/reply
- verify ledger and inbox behavior

## 7) Backup and Restore

### Backup (minimal)

Backup `CCCC_HOME`:
- registry
- daemon logs (optional)
- all groups (`group.yaml`, ledger, state)

### Restore

1. Stop daemon.
2. Restore `CCCC_HOME` directory.
3. Start daemon and verify with `onecolleague doctor`.

## 8) Operational Guardrails

- Keep one source of truth: decisions should be in OneColleague messages.
- Use `reply_required` for critical asks.
- Prefer explicit recipients over broad broadcast when scope is narrow.
- Keep automation focused on objective reminders, not chat noise.

## 9) Escalation Checklist

If an issue repeats:

1. Collect evidence:
   - group id
   - actor id
   - event ids
   - recent `onecolleague tail -n 100`
2. Capture reproducible sequence.
3. Classify severity (`P0/P1/P2`).
4. Register fix or risk in release findings.

## 10) Group Space (NotebookLM) Runbook

### Enable real adapter path (opt-in)

```bash
export CCCC_NOTEBOOKLM_REAL=1
onecolleague daemon restart
```

### Validate control plane

```bash
onecolleague space credential status
onecolleague space health
```

### Validate curated context export path

After a `context_sync` update (`vision.update` / `overview.manual.update` / `task.*` / `agent.*`), check queue:

```bash
onecolleague space jobs list --state pending
```

Expected: a `kind=context_sync` job appears for bound groups.

### Validate repo `space/` reconciliation

```bash
onecolleague space sync --force
```

Expected: result reports `converged=true` and `unsynced_count=0` when provider is healthy.

### Safe rollback (core workflows keep running)

```bash
unset CCCC_NOTEBOOKLM_REAL
onecolleague daemon restart
```

Expected after rollback:

- Group Space operations may return degraded/disabled provider results.
- Core OneColleague chat/task/actor workflows continue normally.

Optional throughput tuning:

```bash
export OneColleague_SPACE_PROVIDER_MAX_INFLIGHT=1   # safer
export OneColleague_SPACE_PROVIDER_MAX_INFLIGHT=4   # faster
```
