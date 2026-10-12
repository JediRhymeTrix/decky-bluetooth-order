# Security and privacy

## Reporting

Please report vulnerabilities through this repository's **Security → Report a
vulnerability** page when private reporting is available. Otherwise open an issue
asking for a private reporting channel without publishing exploit details,
credentials, device identities, or private logs. Maintainer: **JediRhymeTrix**.

Only the latest stable version is maintained. There is no guaranteed response SLA.

## Runtime boundary

- No root flag, shell commands, automatic service restarts, runtime downloads,
  telemetry, pairing actions, or Bluetooth service mutation.
- A passive system-bus observer reads BlueZ device state/signals to record
  successful connection transitions. Startup/retry reads are bounded and do not
  poll continuously.
- Sorting modifies native paired-row presentation only. Unsupported shapes remain
  in native order; native handlers and available-device discovery remain intact.
- Local settings/history contain device addresses as identity keys. Diagnostic
  exports and plugin logs omit device addresses/names and credentials. Do not
  upload the settings/history file.
- Uninstall via Decky stops plugin activity but normally retains local settings.
  Reset history in the panel before uninstalling if you want that history erased.

## Supply chain

Dependencies and lockfile are pinned. The build vendors an exact checked
`dbus-next` wheel and CPython XML fallback with upstream licenses and per-file
hashes. Release assets are an allowlisted ZIP plus `SHA256SUMS`, never a recursive
archive of the developer's machine. CI uses SHA-pinned actions and grants release
write permission only to the publishing job. Source and rebuild instructions are
public, including notices for bundled LGPL code.

Automated tests and store scanners are not guarantees of security or compatibility.
This plugin was developed with AI assistance. Report regressions and disable an
override if native behavior changes unexpectedly.