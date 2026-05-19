# OneColleague Client SDK

The official SDK for integrating external apps/services with a running OneColleague daemon.

## Repository and Packages

- Repository: [ChesterRa/onecolleague-sdk](https://github.com/ChesterRa/onecolleague-sdk)
- Python package: `onecolleague-sdk` (import as `onecolleague_sdk`)
- TypeScript package: `onecolleague-sdk`

## How It Fits with OneColleague Core

OneColleague core (`no1`) is the runtime system:

- daemon
- ledger/state
- Web/CLI/MCP/IM ports

The SDK is a client layer:

- it does not start/own daemon state
- it connects to an existing daemon
- it uses the same control-plane semantics as Web/CLI/MCP

## When to Use SDK vs MCP

Use SDK when you are building:

- backend services
- bots
- IDE integrations
- automation services outside the agent runtime

Use MCP when the caller is an in-session agent/tool runtime.

## Install

```bash
# Python
pip install -U onecolleague-sdk

# TypeScript
npm install onecolleague-sdk
```

## Runtime Requirement

A OneColleague daemon must already be running.

```bash
onecolleague daemon status
```

The SDK client then connects to the daemon transport configured by your OneColleague runtime (`CCCC_HOME`, daemon socket/TCP settings).

## Integration Model

Typical production setup:

1. Run OneColleague core (`no1`) as the local control plane.
2. Connect your app/service through the SDK.
3. Use SDK calls for group/actor/messaging/context/automation operations.
4. Keep operational truth in the OneColleague ledger and group state.

## Compatibility Notes

- SDK and core are released independently, but should stay on the same major/minor line for best compatibility.
- For protocol-level details, see:
  - `docs/standards/CCCS_V1.md`
  - `docs/standards/CCCC_DAEMON_IPC_V1.md`

## Next

For concrete API examples and language-specific usage, follow the SDK repo documentation:

- [onecolleague-sdk README](https://github.com/ChesterRa/onecolleague-sdk)
