# Group Control State Testing Guide

This guide explains how to test the message push / pause / resume button state without forcing the real daemon into broken states.

## Why This Exists

The backend returns canonical `control_state` for group controls. The frontend selector uses it first and only falls back to `runtime_status` for display when `control_state` is missing.

The test harness creates synthetic backend states and feeds them into the same selector used by the UI.

## Automated Tests

Run:

```bash
cd web
npx vitest run tests/utils/groupControlState.test.ts tests/utils/groupControlStateScenarios.test.ts
```

The scenario matrix lives in:

- `web/src/utils/groupControlStateScenarios.ts`

The selector lives in:

- `web/src/utils/groupControlState.ts`

## Browser Console

Open the web app in dev or production mode and run:

```js
window.__OC_GROUP_CONTROL_DEBUG__.ids()
```

Print the current selected group's real control state:

```js
window.__OC_GROUP_CONTROL_DEBUG__.printCurrent()
```

Read the current selected group's real control state without logging:

```js
window.__OC_GROUP_CONTROL_DEBUG__.current()
```

Fetch and print the backend canonical control state for the current selected group:

```js
await window.__OC_GROUP_CONTROL_DEBUG__.fetchCurrent()
```

Run all built-in synthetic scenarios:

```js
window.__OC_GROUP_CONTROL_DEBUG__.scenarios()
```

Run one scenario:

```js
window.__OC_GROUP_CONTROL_DEBUG__.scenario("actors-running-runtime-stopped")
```

Evaluate a custom state:

```js
window.__OC_GROUP_CONTROL_DEBUG__.state({
  selectedGroupId: "g-debug",
  selectedGroupRuntimeStatus: {
    lifecycle_state: "paused",
    runtime_running: false,
    running_actor_count: 0,
    has_running_foreman: false
  },
  actors: []
})
```

## Useful Scenario IDs

- `backend-active`
- `backend-paused`
- `backend-stopped`
- `backend-invalid`
- `missing-backend-control-state`
- `missing-group`

## What To Look At

Each result includes:

- `statusKey`: what the UI should display
- `deliveryToggleKind`: `pause`, `resume`, or `start`
- `launchDisabled`, `pauseDisabled`, `stopDisabled`
- `issues`: backend issues or missing/invalid backend control state

The goal is not to fake the whole UI. The goal is to verify the backend canonical control state and the frontend busy overlay.
