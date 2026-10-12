# Bluetooth Order

A Decky plugin for configurable ordering of **Steam's native Bluetooth device rows** in the game-mode QAM and Settings page. The plugin panel contains controls and diagnostics, not a replacement Bluetooth device list.

## Install and update

Requires Decky Loader in Steam Deck game mode. Tested with **Decky Loader 3.2.10**;
Steam updates can change the native UI this plugin attaches to.

In Decky Settings, open the developer installation controls and use **Install from
URL** with this latest-stable link:

```text
https://github.com/JediRhymeTrix/decky-bluetooth-order/releases/latest/download/bluetooth-order.zip
```

[Releases and version-specific downloads](https://github.com/JediRhymeTrix/decky-bluetooth-order/releases)
include `SHA256SUMS`. Download both assets from the **same release** to verify the
ZIP with `sha256sum --check SHA256SUMS`. GitHub's automatic source archives are not
installable plugin packages. Update by installing a newer complete release ZIP;
settings and connection history are retained.

Bluetooth Order is being submitted to
[Decky Plugins Extended](https://github.com/beallio/decky-plugins-extended), an
independent third-party store. Submission is not approval or immediate catalog
availability. See [distribution and release management](documentation/distribution.md).
This is not an official Decky Store listing.

## First use and compatibility

Both overrides default **off on a fresh install**. Open Bluetooth Order in Decky's
plugin list, select a sort mode, enable QAM or Settings separately, and reopen the
corresponding native Bluetooth menu. The panel contains controls, not its own
Bluetooth list.

The developer ran automated checks and the user confirmed QAM and Settings working
on the tested Deck. That is not a claim of exhaustive hardware certification:
normal boot, suspend/resume, other Steam builds, and every plugin combination are
not fully verified. See the [verification record](documentation/testing.md) and
[changelog](CHANGELOG.md).

QAM attaches only verified native render-function references and their matching
alternate, with owned-reference cleanup. It does not reset React roots or restart
services. Unexpected mounted shapes remain pending/native. Settings recognizes
verified shared Decky renderer patches; compatibility with arbitrary patches or
every TabMaster load order is not guaranteed.

## Controls

- Native order (no transformations), recent connections first, or names A–Z/Z–A.
- Optional connected devices first (ignored in Native mode). When enabled, connected devices stay above disconnected devices, even a more recently used one.
- Independent QAM and Settings switches; **Restore both native menus** turns both off.
- Reset history and show a redacted diagnostic report.

Only successful connection transitions observed by the passive BlueZ listener are recorded. Previous connection history cannot be reconstructed. Already-connected devices at startup receive no invented timestamps. Unmapped devices keep their native slots; history ties retain the existing order.

Disconnecting does not erase recorded recency. For a strict most-recent list
regardless of connection state, turn **Connected devices first** off. Check that
the observer says **observing** before testing a new connection.

After reinstalling or reloading the plugin, go back to Decky's plugin list and
open Bluetooth Order again; Decky can retain the previous panel. Inactive panels
disable their controls and show a reopen notice. Changing the sort mode updates
its displayed selection after backend confirmation. Paired-row focus or an
in-progress operation holds the visible QAM order until closing and reopening
that menu, so rows do not move under the controller. Settings can also hold an
order during interaction; leave and reopen that page, or disable its override
and enable it again if a retained page keeps the old order.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Controls are inactive after an update | Return to Decky's plugin list and open Bluetooth Order again; an old retained panel is intentionally disabled. |
| Recent connections do not reorder devices | Observer must say **observing**. Only connections observed after startup count; a pre-existing connection is not assigned a fabricated timestamp. |
| Device moves down after disconnecting | **Connected devices first** puts currently connected devices above disconnected ones. Turn it off for strict recency; disconnecting never deletes history. |
| Order does not change immediately | Release the interaction hold by reopening the menu. Some unmapped devices deliberately keep their native slots. |
| Settings or QAM says unavailable/pending | The native layout may have changed. Disable that override and report Decky/Steam versions and the redacted diagnostics. Do not restart Bluetooth services to force attachment. |
| Another plugin's Settings features change | Disable this plugin's Settings override first. Route-patch registration order can affect composition; include the other plugin's version in an issue. |

Use **Restore both native menus** for immediate presentation rollback. Uninstall
through Decky to remove the plugin and observer. Exact mounted-QAM renderer
restoration can remount native row components on a later render; ordinary sort
and enable changes retain row state. It does not delete pairing data.

## Safety and privacy

- No changes to Steam files, Bluetooth service responses, signal strength, pairing, discovery or device settings.
- Uses the shared `@decky/ui` presentation helpers. Unsupported native layouts remain unchanged.
- Connection tracking is event-driven; no Bluetooth polling or automatic service restarts.
- Native row keys, connection actions, options and available-device discovery are preserved.
- Device addresses are local identity keys. Logs/reports omit addresses, names, credentials and raw Bluetooth dumps.
- Disable overrides or uninstall via Decky to remove presentation patches and observers. Settings/history can be reset from the panel. Uninstall normally retains Decky-managed settings; delete that plugin's settings directory only if you also want to erase saved history.

## Build

Requirements: Node 22.15+, pnpm 9, Python 3.11+ with pip, and Git.

```sh
pnpm install --frozen-lockfile
pnpm release:build
```

On systems without pip, use a project-local virtual environment (`python3 -m venv .venv` and `. .venv/bin/activate`) for the build; no system Python packages need changing.

Build and test outside the installed plugin directory. Packaging produces `out/bluetooth-order.zip` and `out/SHA256SUMS`. The archive includes the correct ESM package metadata, frontend, Python backend and pinned `dbus-next` dependency. Install only the complete archive via Decky's Install from URL; do not copy partially written files into its watched plugin directory.

The project code is MIT-licensed. Bundled `@decky/api` code retains its LGPL-2.1 license and `dbus-next` retains MIT. A hash-verified CPython 3.11.7 XML fallback is included with its Python license for Decky's frozen runtime; it is used only when the native XML module is absent. Complete third-party notices are included in the release. Public sources, pinned dependencies and build instructions allow rebuilding with a replacement API library.

## Verification and contributions

1. Type checks, frontend tests, mocked backend tests and packaging tests.
2. Ponytail correctness/safety/lean review at each functional change.
3. Disabled initial install and baseline CPU/responsiveness.
4. QAM-only, then Settings-only real-device tests, including controller focus and native controls.
5. Disable/unload/reload cleanup, then user-approved normal boot and suspend/resume checks.

The first release records the checks actually performed, not blanket completion
of this checklist. The project was developed with AI assistance and scoped code
reviews. Third-party distribution does not imply official Decky approval.

See [CONTRIBUTING.md](CONTRIBUTING.md) for changes and bug reports and
[SECURITY.md](SECURITY.md) for privacy and vulnerability reporting. Do not upload
device addresses, names, raw Bluetooth dumps, tokens, or unredacted logs.

## Updates

Release automation publishes `bluetooth-order.zip` and `SHA256SUMS` from a validated
version tag, with notes from the changelog. The latest-stable installation URL
above remains reusable. See [build automation](documentation/automation.md) and
[release procedures](documentation/distribution.md).