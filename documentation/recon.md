# Read-only reconnaissance and review baseline

## Sources

- Installed Decky Loader v3.2.10 and SteamUI source inspected as text only.
- SteamUI SHA256 at reconnaissance: `70ac9752f7747c5d117f3dbe8d5d697592ce6d8c713e1cd501fff840ab467a39`.
- Decky loader source: https://github.com/SteamDeckHomebrew/decky-loader/tree/v3.2.10
- Decky frontend/UI source: https://github.com/SteamDeckHomebrew/decky-frontend-lib
- BlueZ device API: https://bluez.readthedocs.io/en/latest/device-api/
- Ponytail pinned revision: `9b58c1ffb790c075ca32e70a89cf1d80588f4abf`
  - https://github.com/DietrichGebert/ponytail/blob/9b58c1ffb790c075ca32e70a89cf1d80588f4abf/skills/ponytail/SKILL.md
  - https://github.com/DietrichGebert/ponytail/blob/9b58c1ffb790c075ca32e70a89cf1d80588f4abf/skills/ponytail-review/SKILL.md

## Previous implementation failures

Verified local logs showed a missing frontend file while a partially written plugin folder was discovered, incorrect directory-name imports causing registry lookup failures, and a legacy loader evaluating an ESM bundle (`Unexpected token 'export'`). The old package lacked `package.json` with `type: module`.

Loader errors triggered native notifications followed by the known `notification_type` rendering error. This supports a packaging-error/notification-error chain, not a proven explanation of all CPU hangs. No freeze diagnosis is claimed from process command lines or short CDP timeouts.

## Native presentation boundaries

QAM and Settings render separate native paired-device row arrays. They share Bluetooth state, which Steam sorts connected-first then by signal strength. Reorder presentation arrays only; do not override data services or fabricate signal values.

Settings is reached through the `/settings` route and its Bluetooth content. QAM is reached through its normal native Settings tab renderer. Cached exported memo holders and scoped cloned elements are the intended patch targets; getter-only export namespaces are not writable patch targets.

No historical last-connected property is exposed by BlueZ. The observer records observed successful transitions only. Cached Steam device details map transient native IDs to stable local addresses. Cache misses do not justify per-row Bluetooth requests.

## Release gates

1. Complete ESM package metadata and default export; shared DFL external, no second UI module-cache initializer.
2. Pure model, native-tree mock, observer lifecycle and package tests.
3. Ponytail reviews with concrete findings resolved.
4. Disabled initial package install and baseline idle CPU/responsiveness.
5. QAM and Settings tests separately, native pairing/options/discovery/focus unchanged.
6. Teardown/reload tests, then user-approved normal boot/resume tests.
7. Assistant and user confirmation before public repository/release publication.

No service restarts are a development or install mechanism. Unsupported layouts fail open.

## Bounded cached live read (development only)

`scripts/native_recon.mjs` reads the existing shared Decky cache only. It found
the Settings tab key is numeric `4`, both QAM memo entry holders have writable
`type` fields, native Bluetooth/Settings component signatures are available, and
the shared QueryClient is cached. It made no imports, factory calls, patches,
Bluetooth requests, service changes, or notifications. This confirms discovery,
not proof of sorted paired rows. The user subsequently approved Decky's exact
scoped mounted-QAM attachment pattern. The implementation verifies both renderer
references before touching either, retains originals, restores only still-owned
references, and never resets a React root or changes other fiber fields.

An independent code review found query-cache observer bookkeeping must be ignored:
fresh native selector functions produce `observerOptionsUpdated` during rendering.
Publishing plugin snapshots for those events can sustain a render loop. Only
actual `updated`/`removed` query-state events trigger the plugin's refresh, with a
regression test confirming observer bookkeeping alone cannot continue rendering.