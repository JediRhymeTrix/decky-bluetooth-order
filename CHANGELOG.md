# Changelog

Versions follow Semantic Versioning. Release tags are `v` followed by the exact
`package.json` version. Released tags and assets are not replaced; fixes receive
a new version.

## [0.1.0] - 2026-10-11

Initial public release of **Bluetooth Order**.

### Features

- Reorders Steam's existing paired Bluetooth rows in Quick Access and Settings.
- Native order, most recently connected, name A–Z, and name Z–A modes.
- Optional connected-first priority and independently enabled menu overrides.
- Passive, persisted successful-connection history; disconnects retain recency.
- Redacted diagnostics, history reset, and one-button restoration of native order.

### Reliability and distribution

- Scoped mounted-QAM attachment with owned-reference restoration; no root reset
  or automatic service restart.
- Settings adapter recognizes verified shared Decky renderer-patch chains.
- Stale panels disable their controls and explain how to reopen after updating.
- Bounded backend waits, render-loop regressions, isolated QAM session tracking,
  and a hash-checked XML fallback for Decky's frozen Python runtime.
- Complete ESM release ZIP, pinned dependencies, checksums, and CI/release checks.

### Verification and known limits

- User confirmed QAM and Settings working on the tested Deck with Decky 3.2.10.
- Automated frontend, backend, and packaging tests cover sorting, lifecycle,
  observer behavior, archive safety, and release metadata.
- Connection history starts with observed transitions; it cannot reconstruct
  earlier use. Fresh installs default both overrides off.
- Steam UI changes and other plugins' route patches can affect attachment.
  Every TabMaster registration order is not verified.
- Interaction can hold visible ordering. Exact QAM unload restoration can remount
  native rows on a later render. Full boot/resume acceptance remains incomplete.

For installation, privacy, and rollback instructions, see the repository README.