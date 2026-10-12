# Build, package, and release automation

This repository uses the actual Decky `@decky/rollup` **1.0.2** preset, pnpm
**9.15.9**, Node **22**, and Python **3.11 or later with pip for builds**. There is no alternate
packaging framework. CI also checks Node 20 build compatibility; Node 20 cannot
run the frontend tests' `--experimental-strip-types` option.

## Local verification

Run from a clean source checkout, **not** an installed plugin directory:

```sh
pnpm install --frozen-lockfile
pnpm vendor
pnpm validate
python3 scripts/package.py --check
pnpm package
python3 scripts/package.py --verify out/bluetooth-order.zip
(cd out && sha256sum --check SHA256SUMS)
```

`pnpm release:build` is the combined local build command. It produces
`out/bluetooth-order.zip` and `out/SHA256SUMS`, without installing or publishing
anything. `--check` validates complete release inputs without writing a ZIP.
`--verify` validates a ZIP without extracting or executing it. JavaScript is
parsed with `node --check --input-type=module`; importing the bundle would
initialize native UI, so packaging never imports it.
Project Python source is also syntax-compiled without execution or bytecode
files; dependency integrity is checked against the exact upstream wheel.

Decky v3.2.10's frozen Python omits `xml.etree`, which dbus-next imports. Vendoring
also stages three exact CPython **v3.11.7** pure-Python etree files and its upstream
license from the pinned upstream tag. `scripts/stdlib_xml_files.json` records every
SHA256. Existing checked copies require no download; new downloads are bounded,
hash-checked and atomically staged. The observer adds this fallback only when the
native etree package is missing, without replacing Decky's parent XML package or
editing the checked dbus-next sources.

If the host Python has no pip, activate a project-local environment first:

```sh
python3 -m venv .venv
. .venv/bin/activate
```

This uses Python's bundled `ensurepip`, not a downloaded bootstrap script, and
does not change system packages. CI's setup-python installation includes pip.

Packaging regression tests run with:

```sh
python3 -m unittest discover -s tests -p 'packaging*.py'
```

Tests use fake fixtures, mocks, and temporary directories. No test connects to
Steam, Bluetooth, DBus, GitHub, or pip. A real build and checked vendoring are
separate integration checks. CI runs frontend/backend/packaging tests and the
real Rollup build. It does not deploy a live plugin, restart services, or change
system configuration.

## Complete release layout

The ZIP has one root folder, `bluetooth-order/`, containing exactly:

```text
bluetooth-order/
  package.json
  plugin.json
  README.md
  CHANGELOG.md
  CONTRIBUTING.md
  SECURITY.md
  LICENSE
  documentation/automation.md
  documentation/distribution.md
  documentation/testing.md
  main.py
  backend/__init__.py
  backend/state.py
  backend/observer.py
  dist/index.js
  py_modules/dbus_next/       # exact upstream 0.2.3 runtime files + LICENSE
  py_modules/stdlib_xml/      # checked CPython 3.11.7 etree sources + LICENSE
```

`package.json` must retain `name: "decky-bluetooth-order"`, `type: "module"`,
and a valid version. `plugin.json` must retain the display name
`"Bluetooth Order"` and integer `api_version: 1`. The folder name and URL are
not the manifest display name. A frontend-only or copied partial installed
folder is not a release.

The Rollup output must be ESM with a default export, reference Decky's shared
`DFL` external, and not bundle the UI library's webpack/module-cache
initializer. The preset supplies React and `@decky/ui` externally. Packaging
checks characteristic initializer markers and performs an ESM syntax check;
these checks supplement, not replace, frontend source review.

Only the enumerated release paths are read. The checkout is never recursively
zipped. Tests, diagnostics, saved settings, caches, temporary files, `.git`,
`.github`, `.env`, `node_modules`, dependency metadata, build tools, and unused
frontend sources are excluded. All vendored files and the upstream MIT license
must match the checked file-hash allowlist. New backend modules require an
explicit reviewed allowlist change. Included text is screened for common
credential/private-key patterns and device MAC addresses; this heuristic is
not a general secret detector, so review is still required.

Input files and ancestors cannot be symlinks; included files cannot be hard
links or non-regular files. Archive paths cannot be absolute, contain traversal,
backslashes, drive separators, or control characters. Archive entries are
regular files with mode **0644** (no executable bits or links). Output links are
rejected. ZIP entries are sorted with fixed 1980 timestamps. The same payload
and Python/zlib toolchain produce identical bytes regardless of source mtimes
or executable bits; compressor-version changes can change ZIP bytes.
The ZIP and checksum are staged and individually replaced only after archive
verification. An interrupted final replacement can leave a mismatched pair;
checksum verification rejects that pair rather than permitting installation.

### Optional source maps

Default releases omit `dist/index.js.map` and remove its JS reference. To include
debug mappings intentionally:

```sh
python3 scripts/package.py --include-source-map
```

The map must be Rollup v3 for `index.js`. Local absolute paths, arbitrary URLs,
and traversal are rejected. Paths are rewritten to the public
`decky://decky/plugin/Bluetooth%20Order/` namespace. `sourcesContent` and unknown
metadata are removed so original source text and local filesystem information
are not shipped. A missing map or dangling/unexpected map URL is rejected.

## Checked backend dependency

`requirements.txt` pins **only** `dbus-next==0.2.3` (MIT, Tony Crisci).
`scripts/vendor_deps.py` obtains the universal pure-Python wheel from its fixed
HTTPS PyPI URL, verifies SHA256 **before** pip sees it, validates its file layout,
then runs isolated `pip --target --no-index --no-deps --no-compile` against that
local wheel. No source-distribution build or unchecked package installation is
allowed. Only `dbus_next/` plus its unchanged MIT `LICENSE` are retained in
`py_modules/`; no pip metadata, download URLs, or Python bytecode is shipped.

Wheel: `dbus_next-0.2.3-py3-none-any.whl`

```text
58948f9aff9db08316734c0be2a120f6dc502124d9642f55e90ac82ffb16a18b
```

The hash and per-file hashes in `scripts/dbus_next_files.json` were verified
against [PyPI's 0.2.3 metadata](https://pypi.org/pypi/dbus-next/0.2.3/json) and
the wheel. Offline builds can supply that exact checked wheel:

```sh
python3 scripts/vendor_deps.py --wheel /path/to/dbus_next-0.2.3-py3-none-any.whl
```

Already-valid vendoring is reused without network. The exact checked runtime
directories are tracked for source/artifact auditing. A changed dependency fails
closed; dependency updates require deliberately regenerating files and hashes,
reviewing the resulting diff, and retaining licenses. Runtime never runs pip or
downloads dependencies. The project's MIT license names JediRhymeTrix; the separate upstream dbus-next license
retains its author attribution. No Decky template source was copied into these
automation scripts.

The Rollup preset also bundles portions of **`@decky/api` 1.1.3**, which is
**LGPL-2.1**, not MIT. Root `LICENSE` retains its full unchanged upstream license,
SteamDeckHomebrew attribution, and the published source commit
`d1b7f16070777a0ada939cbacd3606eeba0574f5`. The exact license was verified against
the npm registry's integrity-checked package. The root project license does not
relicense third-party code. Before public distribution, review applicable LGPL
source/rebuild obligations together with the published plugin sources, frozen
lockfile, and build instructions; a development artifact is not a legal review.

## GitHub Actions

- `ci.yml`: pull requests, main/master pushes, or manual runs; Node 20/22,
  Python 3.11, frozen pnpm lockfile, tests, build, complete archive/checksum
  validation, and a downloadable CI artifact. Workflow permissions are
  `contents: read`; checkout does not persist credentials.
- `release.yml`: `v*` tag pushes, or manual retry of an existing exact tag. The
  tag must equal `v` plus the exact package version (for example `v0.1.0`), with
  one nonempty dated changelog section. A read-only build captures the tag's
  commit/object and validates/stages the two assets. The `contents: write` job
  checks out that commit, verifies archive version/checksum, and rechecks the
  remote tag object. It creates a draft, downloads and compares the uploaded
  assets and notes, and publishes last. No third-party release action is used.
- SemVer prerelease tags, for example `v0.2.0-rc.1`, create a prerelease with
  `--latest=false`. Stable tags use `--latest`. Rerunning a release that already
  exists fails rather than silently replacing published files. This includes an
  interrupted draft: inspect and delete only that unpublished draft before
  retrying the unchanged tag via Actions → Tagged release → Run workflow.
- The ephemeral workflow token is scoped only to release creation and is never
  placed in the archive or printed. Repository creation, tag pushes, and store
  submission are separate maintainer operations, not build-script side effects.

Action pins were verified from each upstream repository's `refs/tags` using
unauthenticated `git ls-remote` on 2026-10-11, not inferred or guessed:

| Action tag | Verified commit |
| --- | --- |
| `actions/checkout@v4` | `11d5960a326750d5838078e36cf38b85af677262` |
| `actions/setup-node@v4` | `49933ea5288caeca8642d1e84afbd3f7d6820020` |
| `actions/setup-python@v5` | `a26af69be951a213d495a4c3e4e4022e16d87065` |
| `actions/upload-artifact@v4` | `ea165f8d65b6e75b540449e92b4886f43607fa02` |
| `actions/download-artifact@v4` | `d3f86a106a0bac45b974a628896c90dbdf5c8093` |
| `pnpm/action-setup@v4` | `f40ffcd9367d9f12939873eb1018b921a783ffaa` |

Dependency upgrades and action-pin updates require re-verification, tests, and
review rather than automatic moving-tag adoption.

## Development and review process

Read and applied the project `AGENTS.md` and both pinned Ponytail skills at
commit `9b58c1ffb790c075ca32e70a89cf1d80588f4abf`:

- [Ponytail development](https://raw.githubusercontent.com/DietrichGebert/ponytail/9b58c1ffb790c075ca32e70a89cf1d80588f4abf/skills/ponytail/SKILL.md)
- [Ponytail review](https://raw.githubusercontent.com/DietrichGebert/ponytail/9b58c1ffb790c075ca32e70a89cf1d80588f4abf/skills/ponytail-review/SKILL.md)

The automation follows the existing project tools, Python's `zipfile` and
`unittest`, and the runner's `gh` CLI. Review order is correctness, safety,
expected load, risky-path tests, speed, and lean scope. The expected load is one
trusted developer or one isolated CI job building a small plugin; simultaneous
local packaging into the same output directory is not supported. Security
checks protect release boundaries, not a concurrently hostile checkout. Tests
cover incomplete builds, layout, maps, allowlists, dependency/license hashes,
links, traversal, archive modes, secrets, deterministic bytes, and tag versions.
No new runtime/build framework or live deployment machinery was added.

## Stable release download URL

The stable release asset name is always **`bluetooth-order.zip`**, with checksum
asset **`SHA256SUMS`**. The canonical latest-stable-release URLs are:

```text
https://github.com/JediRhymeTrix/decky-bluetooth-order/releases/latest/download/bluetooth-order.zip
https://github.com/JediRhymeTrix/decky-bluetooth-order/releases/latest/download/SHA256SUMS
```

A particular tagged release uses `releases/download/v0.1.0/bluetooth-order.zip`.
The latest stable URL does not select prereleases. Verify the public ZIP and its
checksum after publication and before installation. Curated notes come from the
matching version in `CHANGELOG.md`; a release without notes must fail validation.
See [distribution.md](distribution.md) for tag/CI gates and the Decky Plugins
Extended registration process.
