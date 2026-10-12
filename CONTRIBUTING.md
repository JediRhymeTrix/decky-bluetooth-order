# Contributing

Keep the plugin name **Bluetooth Order** and its install folder `bluetooth-order`
stable: Decky and third-party catalogs use these identities for updates.

## Bug reports

Open a GitHub issue with plugin version, Decky version, Steam client/OS version,
affected menu, selected sort mode, and whether connected-first is enabled. Include
reproduction steps and the plugin's **redacted** report, if useful. List other
plugins that modify Settings or Quick Access. Never attach raw Bluetooth dumps,
device addresses/names, account information, or credentials.

First test Native mode or **Restore both native menus**. After updating, return to
Decky's plugin list and reopen Bluetooth Order. Do not reset the React root or
restart services as a diagnostic shortcut.

## Development

Read `AGENTS.md` before changing code. Use Node 22.15+, pnpm 9.15.9, and Python
3.11+ with pip. Work outside the installed plugin directory:

```sh
pnpm install --frozen-lockfile
pnpm release:build
python3 scripts/package.py --verify out/bluetooth-order.zip
(cd out && sha256sum --check SHA256SUMS)
```

All native/DBus tests are isolated fixtures, not live Steam or Bluetooth calls.
Keep changes small and add a failing regression for a bug before fixing it.
Review correctness, safety, expected load, tests, performance, then lean scope.
Preserve native controls, row keys, discovery, and pairing data. Unknown layouts
must fail open; never fabricate signal strength or connection timestamps.

## Pull requests and releases

Explain the user-visible change, regression evidence, and any unverified hardware
behavior. Do not commit build outputs, unverified dependencies, diagnostics, or
session logs. The two checked runtime dependency directories under `py_modules/`
are tracked so store auditors can compare shipped Python against tagged source.
Do not modify those upstream files; their recorded hashes and licenses must
match. Other installed dependencies and generated frontend outputs remain ignored.

Update `CHANGELOG.md` for a release. The maintainer follows
[distribution.md](documentation/distribution.md); contributors do not need
publishing tokens. AI-assisted contributions must be disclosed honestly.