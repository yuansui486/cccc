# SDK Overview

Use the official SDK when you need to integrate OneColleague with external applications and services.

## Official SDK

- Repository: [ChesterRa/onecolleague-sdk](https://github.com/ChesterRa/onecolleague-sdk)
- Python package: `onecolleague-sdk` (import as `onecolleague_sdk`)
- TypeScript package: `onecolleague-sdk`

## Install

```bash
pip install -U onecolleague-sdk
npm install onecolleague-sdk
```

## Relationship to OneColleague Core

- OneColleague core (`no1`) is the runtime control plane (daemon + ledger + ports).
- SDK is a client interface to that running control plane.
- SDK does not replace core and does not persist state on its own.

## Next

- [Client SDK](./CLIENT_SDK)
