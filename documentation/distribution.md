# Distribution and release management

Canonical source: https://github.com/JediRhymeTrix/decky-bluetooth-order

Plugin identity is `Bluetooth Order`; package name is `decky-bluetooth-order`;
install folder and release ZIP are `bluetooth-order` and `bluetooth-order.zip`.
Keep these stable so existing installations match catalog updates.

## Maintainer release checklist

1. Update `package.json` to a new Semantic Version and add one matching dated
   `## [VERSION] - YYYY-MM-DD` section to `CHANGELOG.md`. Do not change a released
   tag or overwrite published assets.
2. Run `pnpm release:build`, verify the ZIP and checksum, and review the complete
   release payload. Native behavior changes need focused regressions and live
   user acceptance. Record only hardware tests actually performed.
3. Commit reviewed source and push `main`. Wait for both GitHub CI matrix jobs to
   pass; do not tag a failed or untested commit.
4. Tag that commit, for example `git tag -a v0.1.0 -m 'Bluetooth Order 0.1.0'`, and
   push that tag. Release automation verifies tag/version/notes, repeats validation,
   builds the archive, and publishes the checked assets with curated notes.
5. Verify the published release is not a draft, its version/tag is correct, and
   it has **exactly one `.zip` asset** plus `SHA256SUMS`. Download the public assets
   and run `python3 scripts/package.py --verify PATH_TO_ZIP` and the checksum check.
6. Verify the stable download URL resolves to the new release. If publication
   fails, inspect the workflow/draft; never replace a public asset silently.
   An existing release (even a draft) blocks automatic retry. After reviewing a
   failed unpublished draft, delete only that draft and manually run **Tagged
   release** with the same immutable tag. Never delete/move its tag or replace
   an already-public release.

Use prerelease package versions/tags such as `0.2.0-rc.1` / `v0.2.0-rc.1` for
testing. Prereleases are not marked latest and appear only in the extended store's
Testing catalog. Stable releases appear in Stable and Testing after inclusion.

## Installation links

Latest stable:

```text
https://github.com/JediRhymeTrix/decky-bluetooth-order/releases/latest/download/bluetooth-order.zip
https://github.com/JediRhymeTrix/decky-bluetooth-order/releases/latest/download/SHA256SUMS
```

A fixed release uses `/releases/download/v0.1.0/` instead of
`/releases/latest/download/`. Always verify the ZIP against the checksum from the
same release. GitHub's source `.zip`/`.tar.gz` links are not Decky install packages.

## Decky Plugins Extended submission

Rules checked against the store's
[Developer Guide](https://github.com/beallio/decky-plugins-extended/blob/main/Developer.md).
This is independent distribution, not official Decky Store approval.

The repository's default branch provides:

- `plugin.json.name`: **Bluetooth Order**, matching Decky's installed identity.
- `plugin.json.publish.description` and `.tags` for the store card.
- `package.json.author`, repository URL, description, keywords, and exact version.
- At least one public non-draft GitHub release containing exactly one `.zip` asset.

`publish.image` is optional; without it the store uses the GitHub repository card.
The plugin does not declare root privileges. The store scans release archives and
source. Exact checked `dbus-next` and CPython XML runtime files are tracked under
`py_modules/` so the shipped Python matches the release tag; generated frontend
JavaScript remains reproducible from the lockfile and source.
Do not bypass its policy by submitting allowlist/verdict changes with a new plugin.

Submission is a small PR to `beallio/decky-plugins-extended` adding this single
line to **`additional_plugins.txt`**:

```text
https://github.com/JediRhymeTrix/decky-bluetooth-order
```

Do not edit generated catalogs, `store_plugins.txt`, or store audit policy. The PR
should link the first release, summarize privacy/privileges and test evidence,
disclose AI assistance, and explain known compatibility limits. Store audits and
maintainer review determine acceptance. **An open PR is not a live listing.**

After merge and store refresh, users can select Custom Store in Decky Settings:

```text
https://decky-extended-plugins.beallio.com/plugins.json
```

Subsequent releases are discovered by the store's scheduled automation; no new
registration PR is needed while the canonical repository identity stays the same.
Availability may lag publication and depends on audit/refresh success.