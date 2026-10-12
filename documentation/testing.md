# Verification record and acceptance gates

## Completed development checks

- The ESM package includes `package.json` with `type: module`, matching Decky v3.2.10's module-loading path.
- Build uses the normal Decky Rollup preset and shared `DFL`; release checks reject duplicate UI/webpack initializers.
- Frontend tests cover stable sorting, unmapped slots, snapshot validation, serialized settings calls, bounded backend responses, diagnostics flush ordering, cache-listener cleanup and ignored observer bookkeeping.
- Native fixtures use real React reconciliation with mocked native components. They verify numeric Settings key, separate header/content tab consumers, paired-only order, unchanged controls/keys, stable mounted row state through off/native/cache loss/fault/unload, focus hold and operation hold.
- QAM attachment tests cover frozen/mismatched references, newer renderer ownership, and alternates created after attachment. Real React verifies exact renderer-pointer restoration after teardown and another render; the actual shared Decky patch helper verifies remaining stacked patches are preserved.
- Mocked backend tests cover read-only subscriptions, connection transitions, initial connected baselines, owner replacement, authentication EOF safeguards, invalidated/unknown values, bounded retries, persistence and redacted reports. An isolated subprocess also loads `main.py` by file from an unrelated directory, as Decky's sandbox does.
- Archive tests verify exact layout, ESM/default export, dependency/license checksums, path/secret exclusion, incomplete builds and tag/version agreement.
- `scripts/native_recon.mjs` performs bounded cached reads only and confirmed the known native entry signatures and key. No plugin install or service restart is part of recon.

Local verification after release hardening: 59 frontend/native-render tests,
41 backend tests, 41 packaging/release-metadata tests, and 11 offline release-shell
tests passed (152 total), plus type checking, Rollup build, hash-verified vendoring,
complete-input checks and release ZIP verification. The rebuilt ZIP includes the
late-alternate cleanup, XML compatibility fixes, and release documentation; all
45 archive entries are allowlisted. Its SHA256 was verified. Before release
hardening the Settings fix passed 132 tests and its earlier ZIP had 39 entries.
Independent scoped reviews found no proven blockers in the import-path, response-timeout,
status-notification, tabs-progress and attachment cleanup fixes. The observer
compatibility change was reviewed for missing/native XML handling, exact pins,
upstream license retention and release allowlist integrity.
Local tools were Node 25.9.0/Python 3.14; the CI matrix for Node 20/22 and Python
3.11 runs on GitHub before the maintainer tags a release. Hosted results are
available in the repository's Actions tab. Local checks alone do not establish
hardware certification or a completed hosted workflow.

## Read-only pre-install observations

Hosted Python 3.11 exposed an immediate-shutdown cancellation race absent from
the local Python 3.14 run: `wait_for` can consume cancellation when bus connection
has already completed, and the observer then entered its retry delay despite
stopping. Reproduced with isolated Python 3.11.17. The observer now checks stopping
before subscriptions and before retry delay. A deterministic regression failed
before the fix on both Python versions; the existing immediate-close deadline
remains unchanged. No live Bluetooth calls or installed plugin files were touched.

Before the first installation, Decky v3.2.10 was running and Bluetooth Order was not installed. A bounded cached
read found the QAM browser memo mounted with a writable, matching renderer and
matching alternate; the embedded memo was not mounted. No renderer was invoked
or changed, and no Bluetooth request was made.

A five-second process CPU snapshot before installation measured PluginLoader
0.0%, Steam 2.8%, and Steam web helpers 9.0%, with 100% representing one logical
CPU. This is a short comparison sample, not proof of idle load or controller
responsiveness. No installed plugin files or services were changed.

## First live installation — failed acceptance

The user reported the plugin panel worked with both overrides off, but turning
on QAM left the controls gray. Read-only diagnostics confirmed the installed
bundle matched the verified package, but its backend failed during import with
`ModuleNotFoundError: No module named 'backend'`. The frontend remained on its
initial disabled snapshot, and a backend diagnostic request timed out. Both
overrides were off during diagnosis. No enabled-adapter retry was performed.

`main.py` now adds its own plugin root to Python's search path before importing
adjacent backend modules. The isolated loader regression reproduced the exact
failure before this fix. Frontend backend requests now reject after five seconds
instead of keeping controls busy forever; this limits waiting, not cancellation
of a request already received by the backend.

Separately, real-React fixtures reproduced an enabled-only update loop when
mounted QAM groups reported conflicting statuses. Status notifications now update
only panel diagnostics, never native-row data snapshots. Tests verify competing
active/unsupported and active/held reports settle without remounting rows. An
unreplaceable tabs consumer also fails open instead of repeating its search.

The complete corrected ZIP requires reinstallation through Decky's supported
installer. No installed files, services, roots or native component state were
changed during diagnosis. Hardware acceptance remains unpassed.

## Connection-history acceptance — failed observer startup

The user connected and disconnected a device, which fell back down the list.
The installed current bundle was verified, but read-only categorical diagnostics
showed `observerStatus: unavailable`, zero recorded connections, recent mode and
connected-first enabled. The backend emitted one dependency-unavailable warning
before constructing its bus. Connected-first promotion alone does not persist
after disconnecting; no recorded history existed to rank this device by recency.

Static inspection of the installed Decky v3.2.10 executable confirmed its frozen
Python 3.11 archive omits `xml.etree`, required by dbus-next. The executable SHA256
matched the public release. A checked CPython 3.11.7 pure-Python XML fallback now
supplies only that missing package, in the plugin's backend subprocess. Native
XML is preserved; partial/unrelated import failures fail closed. The original
dbus-next files remain unchanged. All fallback sources and their upstream license
are required and hash-checked in the release.

Isolated subprocess tests cover missing, native, partial and unrelated XML import
cases. An import-only integration check with the actual vendored dependency,
missing etree and blocked optional accelerators passed XML/introspection parsing
without creating a real bus. Disconnect regressions verify recency is retained
and connected-first remains an explicit priority. Previously unobserved history
cannot be reconstructed; after reinstall the observer must say **observing**
before a fresh connect/disconnect acceptance test. No live Bluetooth requests,
service restarts or installed-file changes were made during diagnosis.

## Sort-dropdown acceptance — retained panel and QAM session state

The user reported the dropdown stayed on Most recently connected and repeated
selection changes were needed. Read-only runtime-identity checks confirmed Decky
retained an active panel from the previous installation: its runtime was stopped
at revision 4, while the current plugin was running at revision 11. The current
observer was observing. The dropdown callback and positional settings RPC matched
the installed UI contract; the old panel's stopped runtime intentionally ignores
new commands. Returning to Decky's plugin list and reopening the plugin selects
the current panel, without any root reset or service action.

Stopped panels now disable every control and show an explicit reopen notice.
Real-React panel tests cover all four confirmed mode selections, rejected request
recovery, ignored stale-panel commands and reopening with the current runtime.

QAM close/open tracking is now owned by each mounted entry, not shared across
browser/embedded or same-renderer instances. Regressions verify sibling renders
cannot release another entry's hold, closing/reopening clears stale holds even
when operations/focus finish after closing, and cache/native passthrough records
the order actually displayed. Native pending work establishes a hold even while
the cache is unavailable. Real Runtime tests apply every mode without remounting
rows. No installed renderer, settings or Decky state were changed in diagnosis.

Known cleanup boundary: restoring the approved exact original/shared attached
renderer pointer removes its transformation on a later entry render. React can
then remount rows under the native ancestry. This pre-existing attached-teardown
limitation is not fixed by changing session tracking; keeping the wrapper pointer
would violate the exact-reference cleanup requirement. Ordinary mode/enable
changes retain row state. No broader fiber edits or root reset were introduced.

## Settings acceptance — prior renderer patch blocked attachment

The user confirmed QAM sorting worked but Settings did not. The installed bundle
matched the verified package and both override settings were enabled. A bounded
read-only renderer inspection found a Settings function wrapped by the shared
Decky patcher; its recorded original was the exact cached native Settings export.
Our former strict function-identity check missed this element. Installed TabMaster
source uses the corresponding Settings element-type patch pattern. No other
plugin was disabled or changed during diagnosis.

The Settings entry now recognizes only verified shared `type`-patch links ending
at that exact original renderer, with cycle detection. It wraps the current
callable rather than bypassing another patch. Source-text lookalikes and malformed
metadata are rejected. The QAM attachment path is unchanged.

The regression failed before the fix and passes with two real shared renderer
patches. It verifies sorting only paired rows, preserving both patches' output,
leaving available-device order unchanged, and retaining row state through mode
changes, disable and teardown. Fixtures now normalize page `route` descriptors
into `identifier` as Steam does. No native component was directly invoked and no
settings, renderer fields, services or installed files were changed during diagnosis.
At that stage the corrected ZIP still required reinstallation and live Settings
retesting; the subsequent confirmation is recorded below.

## User confirmation and publication scope

Following the Settings fix, the user reported that everything appeared to work
and explicitly authorized public repository/release publication and submission
to Decky Plugins Extended. The installed frontend was compared with the corrected
ZIP and matched. QAM and Settings therefore have user-reported acceptance on the
tested Deck with Decky 3.2.10. This does not establish every item below: no new
boot/resume or exhaustive plugin-combination test was performed for publication.

The late composition review found a conditional TabMaster load-order boundary.
The patch-chain regression verifies preceding shared renderer patches; it does
not cover every subsequent route patch. When Bluetooth Order registers first,
its component boundary can hide native Home Settings content from TabMaster's
expected lookup stage. Actual registration order and a live failure in that order
were not established. This is disclosed, not represented as fixed.

Settings also holds order on interaction without QAM's explicit session-generation
reset; retained pages may require disabling/re-enabling the Settings override.
The README documents this behavior and the exact QAM teardown remount boundary.

## Hardware regression checklist — completeness not claimed

1. Record idle CPU and UI responsiveness before install, with native overrides off.
2. Install the complete ZIP using Decky's supported installer. Confirm startup succeeds without loader-error notifications.
3. Verify the user-approved scoped QAM attachment updates only matching native renderer references and restores owned references on cleanup. It follows Decky's own attachment pattern, without root reset/service restarts. Unknown mounted shapes stay pending; the plugin must not claim success until paired rows render.
4. Enable Settings alone, then QAM separately once it genuinely attaches. Test real connect/disconnect/reconnect while menus are both open and closed.
5. Test all sort modes and connected-first. Confirm pairing/discovery/options/device forgetting, focus navigation and button labels/actions remain native.
6. Disable both overrides, unload/reload, and check no duplicate patch/listener or CPU increase.
7. Only with user approval, perform a normal boot and suspend/resume test. No development service-restarter is provided.
8. Record developer checks and user confirmation separately; do not describe
   unperformed boot/resume or plugin-combination checks as passed.

The first failed import, repeated rendering, elevated CPU, or lost native control stops testing. Restore native mode/disable adapters; do not repeat restart experiments.

## GitHub publication gate

GitHub authentication and user authorization are confirmed. Public metadata uses
JediRhymeTrix as maintainer and the canonical `decky-bluetooth-order` repository.
The release workflow publishes `bluetooth-order.zip` and `SHA256SUMS` with stable
asset names. See [distribution.md](distribution.md) for the publication checklist
and third-party submission contract. Store acceptance is a separate maintainer
decision, not implied by release publication.
