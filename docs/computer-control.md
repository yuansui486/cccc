# OneColleague computer control

The computer-control surface is isolated from project directories:

- Workflow definitions: `ONECOLLEAGUE_HOME/groups/<group_id>/computer-control/workflows/`
- Run state, temporary element captures, and audit records: `ONECOLLEAGUE_HOME/groups/<group_id>/state/computer-control/`
- The machine-wide lease: `ONECOLLEAGUE_HOME/state/computer-control/lease.json`

The web UI is available at `/computer-control/<group_id>`. On first entry it calls
`POST /api/v1/computer-control/setup/ensure`. If `uv` is unavailable,
OneColleague installs it with pip and locates the resulting executable without a
PATH restart. The first setup installs the latest Windows-MCP release into the
private `ONECOLLEAGUE_HOME/state/computer-control/uv-tools` Python 3.13 tool
environment. Normal starts reuse that verified environment; only the explicit
upgrade endpoint resolves a newer release. OneColleague never adds a package
version constraint to the Windows-MCP install command.

Chat/control-plane clients hand off a structured request through
`POST /api/v1/groups/<group_id>/computer-control/requests`; user text is kept
separate from the execution contract.

Workflow versions are immutable. Editing requires `expected_revision`; publishing
and trusting are separate operations. A trusted entry is bound to the exact
Windows-MCP package/tool-schema fingerprint. Any catalog change therefore removes
trust before a run can start.

Enabled interval triggers are evaluated by the lifecycle-owned scheduler. They
must point at a published, trusted version and acquire the same machine-wide
lease as manual runs; busy, paused, or unavailable groups are skipped without
backfill.

All action runs acquire the machine-wide lease. An expired lease is reclaimed
after its 30-second TTL, while emergency stop releases the lease and terminates
the MCP session. Secrets can only be represented as `${secret:NAME}` references;
plain sensitive values are rejected during workflow validation.
