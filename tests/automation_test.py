"""Release shell regression tests: temporary assets and fake gh, never network."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text()
CI = (ROOT / ".github/workflows/ci.yml").read_text()


def run_block(name: str) -> str:
    """Read a literal step script without adding a YAML dependency to tests."""
    start = RELEASE.index(f"      - name: {name}\n")
    lines = RELEASE[start:].splitlines()
    index = lines.index("        run: |") + 1
    script = []
    for line in lines[index:]:
        if line and not line.startswith("          "):
            break
        script.append(line[10:])
    return "\n".join(script) + "\n"


STAGE = run_block("Stage a new draft with exactly one ZIP and SHA256SUMS")
PUBLISH = run_block("Verify uploaded draft assets and publish last")

FAKE_GH = r'''
import json
import os
from pathlib import Path
import shutil
import sys

args = sys.argv[1:]
root = Path(os.environ["RUNNER_TEMP"])
with (root / "commands.jsonl").open("a") as output:
    output.write(json.dumps(["gh", *args]) + "\n")
state = root / "state.json"
tag = os.environ["RELEASE_TAG"]
if args[0] == "api":
    if "--paginate" in args:
        if os.environ.get("EXISTING_RELEASE"):
            print(tag)
    else:
        print("moved" if os.environ.get("MOVED_TAG") else os.environ["RELEASE_TAG_OBJECT"])
elif args[:2] == ["release", "create"]:
    if state.exists() or "--draft" not in args or "--verify-tag" not in args or "--generate-notes" in args:
        raise SystemExit("unsafe release creation")
    assets = [Path(arg).name for arg in args if arg.startswith("out/")]
    if sorted(assets) != ["SHA256SUMS", "bluetooth-order.zip"]:
        raise SystemExit("unexpected assets")
    notes = Path(args[args.index("--notes-file") + 1]).read_text()
    state.write_text(json.dumps({"isDraft": True, "isPrerelease": "--prerelease" in args,
                               "tagName": tag, "assets": [{"name": name} for name in assets], "body": notes}))
    if os.environ.get("UPLOAD_FAIL"):
        raise SystemExit("upload interrupted")
elif args[:2] == ["release", "download"]:
    destination = Path(args[args.index("--dir") + 1])
    destination.mkdir()
    for name in ("bluetooth-order.zip", "SHA256SUMS"):
        shutil.copyfile(Path("out") / name, destination / name)
    if os.environ.get("CORRUPT_DOWNLOAD"):
        (destination / "bluetooth-order.zip").write_bytes(b"corrupted")
elif args[:2] == ["release", "view"]:
    draft = json.loads(state.read_text())
    mismatch = os.environ.get("DRAFT_MISMATCH")
    if mismatch == "assets":
        draft["assets"].append({"name": "second.zip"})
    elif mismatch == "body":
        draft["body"] = "Uncurated notes"
    elif mismatch == "published":
        draft["isDraft"] = False
    elif mismatch == "tag":
        draft["tagName"] = "v9.9.9"
    elif mismatch == "prerelease":
        draft["isPrerelease"] = not draft["isPrerelease"]
    print(json.dumps(draft))
elif args[:2] == ["release", "edit"]:
    draft = json.loads(state.read_text())
    if not draft["isDraft"] or "--draft=false" not in args:
        raise SystemExit("unsafe publication")
    draft["isDraft"] = False
    state.write_text(json.dumps(draft))
else:
    raise SystemExit("unexpected gh command")
'''

FAKE_PYTHON = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
if args and args[0] == "scripts/release_metadata.py":
    root = Path(os.environ["RUNNER_TEMP"])
    with (root / "commands.jsonl").open("a") as output:
        output.write(json.dumps(["metadata", *args]) + "\n")
    if "--archive" not in args or args[1] != os.environ["RELEASE_TAG"]:
        raise SystemExit("archive/version concordance check missing")
    if os.environ.get("ARCHIVE_FAIL"):
        raise SystemExit("archive version mismatch")
else:
    os.execv(sys.executable, [sys.executable, *args])
'''


class ReleaseAutomationTests(unittest.TestCase):
    def execute(self, script: str, **settings):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            (root / "out").mkdir()
            archive = b"fake archive; helper itself is covered by packaging tests"
            (root / "out/bluetooth-order.zip").write_bytes(archive)
            digest = hashlib.sha256(archive).hexdigest()
            (root / "out/SHA256SUMS").write_text(f"{digest}  bluetooth-order.zip\n")
            (root / "release-notes.md").write_text("## [0.1.0] - 2026-10-11\n\nCurated notes.\n")
            for name, source in (("gh", FAKE_GH), ("python3", FAKE_PYTHON)):
                target = root / "bin" / name
                target.write_text(f"#!{sys.executable}\n" + source)
                target.chmod(0o755)
            env = dict(os.environ, RUNNER_TEMP=str(root), RELEASE_TAG="v0.1.0",
                       RELEASE_TAG_OBJECT="immutable-tag-object", PRERELEASE="false",
                       GH_TOKEN="fake-token", GH_REPO="owner/repo")
            env.update(settings)
            env["PATH"] = str(root / "bin") + os.pathsep + os.environ["PATH"]
            result = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                                    cwd=root, env=env, text=True, capture_output=True, timeout=10)
            commands = root / "commands.jsonl"
            calls = [json.loads(line) for line in commands.read_text().splitlines()] if commands.exists() else []
            state = root / "state.json"
            return result, calls, json.loads(state.read_text()) if state.exists() else None

    def assert_not_published(self, result, calls):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(any(call[:3] == ["gh", "release", "edit"] for call in calls), calls)

    def test_stable_draft_verified_before_publication(self):
        result, calls, state = self.execute(STAGE + PUBLISH)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(state["isDraft"])
        create = next(call for call in calls if call[:3] == ["gh", "release", "create"])
        self.assertIn("--draft", create)
        self.assertIn("--notes-file", create)
        metadata = next(i for i, call in enumerate(calls) if call[0] == "metadata")
        self.assertLess(metadata, len(calls) - 1)
        self.assertEqual(calls[-1][:4], ["gh", "release", "edit", "v0.1.0"])
        self.assertIn("--prerelease=false", calls[-1])
        self.assertIn("--latest", calls[-1])
        self.assertTrue(all("--clobber" not in call for call in calls))

    def test_prerelease_is_never_latest(self):
        result, calls, state = self.execute(STAGE + PUBLISH, PRERELEASE="true", RELEASE_TAG="v0.1.0-rc.1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(state["isPrerelease"])
        self.assertIn("--prerelease", calls[-1])
        self.assertIn("--latest=false", calls[-1])

    def test_existing_release_or_leftover_draft_is_not_mutated(self):
        result, calls, state = self.execute(STAGE + PUBLISH, EXISTING_RELEASE="true")
        self.assert_not_published(result, calls)
        self.assertIsNone(state)
        self.assertFalse(any(call[:2] == ["gh", "release"] for call in calls))
        self.assertIn("refusing to replace", result.stderr)

    def test_partial_upload_leaves_only_an_unpublished_draft(self):
        result, calls, state = self.execute(STAGE + PUBLISH, UPLOAD_FAIL="true")
        self.assert_not_published(result, calls)
        self.assertTrue(state["isDraft"])

    def test_corrupt_download_does_not_publish(self):
        result, calls, state = self.execute(STAGE + PUBLISH, CORRUPT_DOWNLOAD="true")
        self.assert_not_published(result, calls)
        self.assertTrue(state["isDraft"])

    def test_archive_version_failure_does_not_publish(self):
        result, calls, state = self.execute(STAGE + PUBLISH, ARCHIVE_FAIL="true")
        self.assert_not_published(result, calls)
        self.assertTrue(state["isDraft"])

    def test_changed_tag_object_does_not_create_a_release(self):
        result, calls, state = self.execute(STAGE + PUBLISH, MOVED_TAG="true")
        self.assert_not_published(result, calls)
        self.assertIsNone(state)

    def test_draft_assets_notes_and_metadata_must_match(self):
        for mismatch in ("assets", "body", "published", "tag", "prerelease"):
            with self.subTest(mismatch=mismatch):
                result, calls, _ = self.execute(STAGE + PUBLISH, DRAFT_MISMATCH=mismatch)
                self.assert_not_published(result, calls)

    def test_actions_are_pinned_with_minimum_permissions_and_timeouts(self):
        for workflow in (CI, RELEASE):
            uses = re.findall(r"uses: (\S+)", workflow)
            self.assertTrue(uses)
            self.assertTrue(all(re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", action) for action in uses))
            self.assertIn("permissions:\n  contents: read\n", workflow)
            self.assertIn("timeout-minutes:", workflow)
            self.assertNotIn("pull_request_target", workflow)
            self.assertNotIn("persist-credentials: true", workflow)
        self.assertEqual(RELEASE.count("contents: write"), 1)
        self.assertNotIn("contents: write", CI)

    def test_dispatch_checks_existing_exact_tag_and_publishes_build_commit(self):
        self.assertIn("workflow_dispatch:", RELEASE)
        self.assertIn("ref: refs/tags/${{ inputs.tag || github.ref_name }}", RELEASE)
        self.assertIn('RELEASE_TAG: ${{ inputs.tag || github.ref_name }}', RELEASE)
        self.assertIn('git show-ref --verify --hash "refs/tags/$RELEASE_TAG"', RELEASE)
        self.assertIn('test "$(git rev-parse HEAD)" = "$commit"', RELEASE)
        self.assertIn('test "$commit" = "$EVENT_SHA"', RELEASE)
        self.assertIn("ref: ${{ needs.build.outputs.commit }}", RELEASE)
        self.assertNotIn("--generate-notes", RELEASE)
        self.assertNotIn("--clobber", RELEASE)
        self.assertNotRegex(RELEASE, r"run:.*\$\{\{.*inputs\.tag")

    def test_frontend_test_lane_requires_module_register_hooks(self):
        self.assertIn("22.15.0", CI)
        self.assertIn("22.15.0", RELEASE)
        guard = "import { registerHooks } from 'node:module'"
        self.assertIn(guard, CI)
        self.assertIn(guard, RELEASE)
        self.assertIn("pnpm test:automation", CI)


if __name__ == "__main__":
    unittest.main()