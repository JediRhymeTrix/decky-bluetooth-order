"""Packaging tests use only fake files/tempdirs; no pip, Steam, DBus or network."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
# Explicit local module path: scripts/package.py is not the package-manager API.
sys.path.insert(0, str(SCRIPTS))
import package as packaging
import release_metadata
import vendor_deps


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.files = {name: b"# harmless fake source\n" for name in packaging.SOURCE_FILES}
        self.files.update({
            "package.json": b'{"name":"decky-bluetooth-order","version":"0.1.0","type":"module"}',
            "plugin.json": b'{"name":"Bluetooth Order","api_version":1}',
            "LICENSE": (SCRIPTS.parent / "LICENSE").read_bytes(),
            "README.md": b"# Bluetooth Order\n",
            "dist/index.js": b"const ui = DFL.PanelSection; export { ui as default };\n//# sourceMappingURL=index.js.map\n",
        })
        # Deterministic fixture content for every real dependency filename.
        # Production validation uses the immutable upstream 0.2.3 content hashes.
        fake_hashes = {}
        for name in packaging.DBUS_FILES:
            data = b"MIT License for fake test dependency\n" if name == "LICENSE" else b"# fake dependency\n"
            self.files[f"py_modules/dbus_next/{name}"] = data
            fake_hashes[name] = hashlib.sha256(data).hexdigest()
        patcher = mock.patch.object(packaging, "DBUS_FILES", fake_hashes)
        patcher.start()
        self.addCleanup(patcher.stop)
        xml_hashes = {}
        for name in packaging.XML_FILES:
            data = b"fixture Python license\n" if name == "LICENSE" else b"# fake XML dependency\n"
            self.files[f"py_modules/stdlib_xml/{name}"] = data
            xml_hashes[name] = hashlib.sha256(data).hexdigest()
        xml_patch = mock.patch.object(packaging, "XML_FILES", xml_hashes)
        xml_patch.start()
        self.addCleanup(xml_patch.stop)
        for name, data in self.files.items():
            self.write(name, data)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def archive(self, entries):
        target = self.root / "fixture.zip"
        with zipfile.ZipFile(target, "w") as archive:
            for name, data, mode in entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = mode << 16
                archive.writestr(info, data)
        return target

    def entries(self):
        return [("bluetooth-order/" + name, data, stat.S_IFREG | 0o644)
                for name, data in packaging.collect(self.root).items()]

    def test_complete_layout_modes_and_checksum(self):
        target = packaging.package(self.root)
        self.assertEqual(packaging.verify_archive(target), "0.1.0")
        with zipfile.ZipFile(target) as archive:
            expected = {"bluetooth-order/" + name for name in self.files}
            self.assertEqual(set(archive.namelist()), expected)
            self.assertIn("bluetooth-order/package.json", archive.namelist())
            self.assertIn("bluetooth-order/py_modules/dbus_next/LICENSE", archive.namelist())
            self.assertNotIn(b"sourceMappingURL", archive.read("bluetooth-order/dist/index.js"))
            for info in archive.infolist():
                self.assertEqual(info.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(info.external_attr >> 16, stat.S_IFREG | 0o644)
        expected_sum = f"{hashlib.sha256(target.read_bytes()).hexdigest()}  bluetooth-order.zip\n"
        self.assertEqual((target.parent / "SHA256SUMS").read_text(), expected_sum)

    def test_release_archive_version_concordance(self):
        target = packaging.package(self.root)
        self.write("CHANGELOG.md", b"## [0.1.0] - 2026-10-11\n\nInitial release.\n")
        notes = self.root / "notes.md"
        command = ["release_metadata.py", "v0.1.0", "--package", str(self.root / "package.json"),
                   "--changelog", str(self.root / "CHANGELOG.md"), "--archive", str(target),
                   "--notes-file", str(notes)]
        with mock.patch.object(sys, "argv", command), mock.patch("sys.stdout", new=io.StringIO()):
            release_metadata.main()
        self.assertTrue(notes.read_text().startswith("## [0.1.0] - 2026-10-11\n"))
        notes.unlink()
        # A valid archive for a different version must not publish or write notes.
        self.write("package.json", b'{"name":"decky-bluetooth-order","version":"0.2.0","type":"module"}')
        packaging.package(self.root)
        self.write("package.json", self.files["package.json"])
        with mock.patch.object(sys, "argv", command), mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                release_metadata.main()
        self.assertEqual(error.exception.code, 1)
        self.assertFalse(notes.exists())

    def test_deterministic_despite_source_mtime_and_executable_bits(self):
        target = packaging.package(self.root)
        original = target.read_bytes()
        for name in self.files:
            os.chmod(self.root / name, 0o755)
            os.utime(self.root / name, (1_000_000_000, 1_000_000_000))
        self.assertEqual(packaging.package(self.root).read_bytes(), original)

    def test_bad_input_does_not_overwrite_last_verified_artifacts(self):
        target = packaging.package(self.root)
        original_zip = target.read_bytes()
        sums = target.parent / "SHA256SUMS"
        original_sums = sums.read_bytes()
        (self.root / "dist" / "index.js").unlink()
        with self.assertRaises(packaging.PackageError):
            packaging.package(self.root)
        self.assertEqual(target.read_bytes(), original_zip)
        self.assertEqual(sums.read_bytes(), original_sums)

    def test_allowlist_omits_private_and_unrecognized_files(self):
        for name in (".env", "tests/private.py", "backend/debug.py", "backend/__pycache__/x.pyc",
                     "dist/stale.js", "dist/index.js.map", "node_modules/secret.js",
                     "py_modules/dbus_next/private.py", "py_modules/dbus_next-0.2.3.dist-info/METADATA",
                     "diagnostics.log", "settings.json", "private-key.pem"):
            self.write(name, b"should never be packaged")
        payload = packaging.collect(self.root)
        self.assertEqual(set(payload), set(self.files))

    def test_incomplete_build_or_backend_or_dependency_license_rejected(self):
        for name in ("dist/index.js", "main.py", "backend/state.py", "package.json", "py_modules/dbus_next/LICENSE"):
            with self.subTest(name=name):
                path = self.root / name
                data = path.read_bytes()
                path.unlink()
                with self.assertRaises(packaging.PackageError):
                    packaging.collect(self.root)
                path.write_bytes(data)

    def test_changed_dependency_rejected(self):
        self.write("py_modules/dbus_next/message.py", b"# changed or incomplete dependency\n")
        with self.assertRaisesRegex(packaging.PackageError, "differs"):
            packaging.collect(self.root)

    def test_xml_fallback_and_license_are_required_and_hash_checked(self):
        for name in packaging.XML_FILES:
            with self.subTest(name=name):
                path = self.root / "py_modules" / "stdlib_xml" / name
                original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(packaging.PackageError, "missing required"):
                    packaging.collect(self.root)
                path.write_bytes(b"changed fallback\n")
                with self.assertRaisesRegex(packaging.PackageError, "checked CPython"):
                    packaging.collect(self.root)
                path.write_bytes(original)

    def test_invalid_backend_source_rejected_without_execution(self):
        self.write("backend/state.py", b"def broken(:\n")
        with self.assertRaisesRegex(packaging.PackageError, "invalid Python"):
            packaging.collect(self.root)
        marker = self.root / "must-not-be-created"
        self.write("backend/state.py", f"open({str(marker)!r}, 'w').write('executed')\n".encode())
        packaging.collect(self.root)
        self.assertFalse(marker.exists())

    def test_manifest_and_esm_package_must_match(self):
        cases = (
            ("package.json", {"name": "decky-bluetooth-order", "version": "0.1.0", "type": "commonjs"}),
            ("package.json", {"name": "bluetooth-order", "version": "0.1.0", "type": "module"}),
            ("plugin.json", {"name": "bluetooth-order", "api_version": 1}),
            ("plugin.json", {"name": "Bluetooth Order", "api_version": True}),
        )
        for name, obj in cases:
            with self.subTest(obj=obj):
                self.write(name, json.dumps(obj).encode())
                with self.assertRaises(packaging.PackageError):
                    packaging.collect(self.root)
                self.write(name, self.files[name])

    def test_missing_external_duplicate_initializer_and_invalid_js_rejected(self):
        for js in (b"export default {};", b"module.exports = DFL;",
                   b"const x = DFL.X; initModuleCache(); export default x;",
                   b"const x = DFL.X; window.webpackChunksteamui.push([]); export default x;",
                   b"const x = DFL.X; export default (;" ):
            with self.subTest(js=js):
                self.write("dist/index.js", js)
                with self.assertRaises(packaging.PackageError):
                    packaging.collect(self.root)

    def test_secrets_and_device_identifiers_rejected_in_included_files(self):
        for value in ("-----BEGIN PRIVATE KEY-----", "ghp_" + "X" * 36, "AB:CD:EF:01:23:45"):
            with self.subTest(value=value):
                self.write("README.md", value.encode())
                with self.assertRaisesRegex(packaging.PackageError, "possible secret"):
                    packaging.collect(self.root)

    def test_symlink_file_and_parent_rejected(self):
        path = self.root / "README.md"
        path.unlink()
        outside = self.root / "elsewhere.txt"
        outside.write_bytes(b"external")
        path.symlink_to(outside)
        with self.assertRaisesRegex(packaging.PackageError, "symlink"):
            packaging.collect(self.root)
        path.unlink()
        path.write_bytes(self.files["README.md"])
        (self.root / "backend").rename(self.root / "other-backend")
        (self.root / "backend").symlink_to(self.root / "other-backend", target_is_directory=True)
        with self.assertRaisesRegex(packaging.PackageError, "symlink"):
            packaging.collect(self.root)

    def test_hardlink_rejected(self):
        os.link(self.root / "README.md", self.root / "linked.txt")
        with self.assertRaisesRegex(packaging.PackageError, "hard link"):
            packaging.collect(self.root)

    def test_source_ancestor_symlink_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(packaging.PackageError, "ancestor symlink"):
            packaging.collect(alias)

    def test_output_symlink_does_not_write_outside(self):
        outside = self.root / "other"
        outside.mkdir()
        (self.root / "out").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(packaging.PackageError, "output"):
            packaging.package(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_output_asset_symlink_does_not_overwrite_target(self):
        (self.root / "out").mkdir()
        outside = self.root / "saved"
        outside.write_bytes(b"keep this")
        (self.root / "out" / "bluetooth-order.zip").symlink_to(outside)
        with self.assertRaisesRegex(packaging.PackageError, "unsafe output"):
            packaging.package(self.root)
        self.assertEqual(outside.read_bytes(), b"keep this")

    def test_release_size_limits_reject_oversized_input_and_archive(self):
        with mock.patch.object(packaging, "MAX_FILE_SIZE", 1):
            with self.assertRaisesRegex(packaging.PackageError, "size limit"):
                packaging.collect(self.root)
        target = self.archive(self.entries())
        with mock.patch.object(packaging, "MAX_TOTAL_SIZE", 1):
            with self.assertRaisesRegex(packaging.PackageError, "size limit"):
                packaging.verify_archive(target)

    def test_archive_traversal_absolute_backslash_or_other_root_rejected(self):
        for name in ("bluetooth-order/../secret", "/bluetooth-order/a", "bluetooth-order/./a",
                     "bluetooth-order//a", "bluetooth-order/..\\secret", "other/README.md",
                     "bluetooth-order/C:/secret", "bluetooth-order/evil\nname"):
            with self.subTest(name=name):
                target = self.archive([(name, b"bad", stat.S_IFREG | 0o644)])
                with self.assertRaises(packaging.PackageError):
                    packaging.verify_archive(target)

    def test_archive_symlink_executable_duplicate_extra_or_missing_rejected(self):
        entries = self.entries()
        cases = (
            [(entries[0][0], b"/etc/passwd", stat.S_IFLNK | 0o777), *entries[1:]],
            [(entries[0][0], entries[0][1], stat.S_IFREG | 0o755), *entries[1:]],
            [*entries, ("bluetooth-order/.env", b"secret", stat.S_IFREG | 0o644)],
            entries[1:],
        )
        for case in cases:
            with self.assertRaises(packaging.PackageError):
                packaging.verify_archive(self.archive(case))
        with self.assertWarns(UserWarning):
            target = self.archive([*entries, entries[0]])
        with self.assertRaisesRegex(packaging.PackageError, "duplicate"):
            packaging.verify_archive(target)

    def test_optional_source_map_sanitized_without_original_sources(self):
        source_map = {"version": 3, "file": "index.js", "sources": ["../src/index.tsx"],
                      "names": [], "mappings": "AAAA", "sourcesContent": ["private original text"],
                      "x_local_path": "/home/private"}
        self.write("dist/index.js.map", json.dumps(source_map).encode())
        target = packaging.package(self.root, include_map=True)
        self.assertEqual(packaging.verify_archive(target), "0.1.0")
        with zipfile.ZipFile(target) as archive:
            result = json.loads(archive.read("bluetooth-order/dist/index.js.map"))
            self.assertNotIn("sourcesContent", result)
            self.assertNotIn("x_local_path", result)
            self.assertEqual(result["sources"], ["decky://decky/plugin/Bluetooth%20Order/src/index.tsx"])

    def test_source_maps_reject_absolute_url_traversal_and_missing_file(self):
        with self.assertRaises(packaging.PackageError):
            packaging.collect(self.root, include_map=True)
        for source in ("/home/private/index.ts", "../../private.ts", "../src/../../private.ts", "https://example.com/a.ts",
                       "../src/%2e%2e/private.ts", "../src/index.ts?private", "../src/index.ts#private"):
            obj = {"version": 3, "file": "index.js", "sources": [source], "names": [], "mappings": ""}
            self.write("dist/index.js.map", json.dumps(obj).encode())
            with self.subTest(source=source), self.assertRaises(packaging.PackageError):
                packaging.collect(self.root, include_map=True)
        self.write("dist/index.js", b"const ui=DFL.X; export default ui;\n//# sourceMappingURL=../../private.map\n")
        with self.assertRaises(packaging.PackageError):
            packaging.collect(self.root)
        self.write("dist/index.js", b"const ui=DFL.X; export default ui; //# sourceMappingURL=data:private\n")
        with self.assertRaises(packaging.PackageError):
            packaging.collect(self.root)

    def test_invalid_source_map_names_and_mappings_rejected(self):
        for names, mappings in (("not an array", ""), ([None], ""), ([], None)):
            obj = {"version": 3, "file": "index.js", "sources": ["../src/index.tsx"],
                   "names": names, "mappings": mappings}
            self.write("dist/index.js.map", json.dumps(obj).encode())
            with self.assertRaisesRegex(packaging.PackageError, "mappings or names"):
                packaging.collect(self.root, include_map=True)


class ReleaseMetadataTests(unittest.TestCase):
    def test_exact_version_and_prerelease(self):
        self.assertEqual(release_metadata.metadata("0.1.0", "v0.1.0")["prerelease"], "false")
        self.assertEqual(release_metadata.metadata("0.1.0-rc.1", "v0.1.0-rc.1")["prerelease"], "true")
        self.assertEqual(release_metadata.metadata("0.1.0+build-1", "v0.1.0+build-1")["prerelease"], "false")
        self.assertEqual(release_metadata.metadata("0.1.0-rc.1+build-1", "v0.1.0-rc.1+build-1"),
                         {"tag": "v0.1.0-rc.1+build-1", "version": "0.1.0-rc.1+build-1", "prerelease": "true"})
        for tag in ("v0.2.0", "0.1.0", "v0.1.0\nprerelease=false"):
            with self.assertRaises(ValueError):
                release_metadata.metadata("0.1.0", tag)

    def test_invalid_semver_rejected(self):
        for version in ("01.1.0", "0.1.0-01", "0.1.0-", "0.1.0-rc..1", "0.1.0\n", "0.1.0+."):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release_metadata.metadata(version, "v" + version)

    def test_curated_notes_are_exact_version_section(self):
        section = "## [0.1.0] - 2026-10-11\n\nInitial public release.\n\n### Features\n\n- Native rows."
        text = "# Changelog\n\n## [Unreleased]\n\nNot shipped.\n\n" + section + "\n\n## [0.0.1] - 2026-01-01\n\nOlder.\n"
        self.assertEqual(release_metadata.changelog_notes(text, "0.1.0"), section + "\n")
        self.assertEqual(release_metadata.changelog_notes(section + "\n", "0.1.0"), section + "\n")

    def test_missing_or_duplicate_changelog_section_rejected(self):
        section = "## [0.1.0] - 2026-10-11\n\nRelease notes.\n"
        for text in ("# Changelog\n", "## [0.1.0-rc.1] - 2026-10-11\n\nPrerelease.\n",
                     section + section, section + "## [0.1.0]\n\nDuplicate malformed heading.\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "exactly one"):
                release_metadata.changelog_notes(text, "0.1.0")

    def test_empty_changelog_section_rejected(self):
        for body in ("", "\n \t\n", "\n<!-- TODO notes -->\n", "\n### Features\n\n<!-- TODO -->\n"):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "empty"):
                release_metadata.changelog_notes("## [0.1.0] - 2026-10-11\n" + body, "0.1.0")

    def test_changelog_heading_must_be_exact_and_date_valid(self):
        for heading in ("## [0.1.0]", "## [0.1.0] - 2026-2-01", "## [0.1.0] - 2026-02-30",
                        "##  [0.1.0] - 2026-10-11", "## [0.1.0] - 2026-10-11 extra"):
            with self.subTest(heading=heading), self.assertRaises(ValueError):
                release_metadata.changelog_notes(heading + "\n\nNotes.\n", "0.1.0")

    def test_prerelease_notes_require_exact_prerelease_section(self):
        text = "## [0.1.0-rc.1] - 2026-10-11\n\nPreview.\n"
        self.assertEqual(release_metadata.changelog_notes(text, "0.1.0-rc.1"), text)
        with self.assertRaises(ValueError):
            release_metadata.changelog_notes(text, "0.1.0")

    def test_cli_writes_notes_file_and_existing_github_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "package.json").write_text('{"version":"0.1.0"}')
            section = "## [0.1.0] - 2026-10-11\n\nCurated notes with **Markdown**.\n"
            (root / "CHANGELOG.md").write_text("# Changelog\n\n" + section)
            output = root / "github-output"
            output.write_text("existing=value\n")
            command = ["release_metadata.py", "v0.1.0", "--package", str(root / "package.json"),
                       "--changelog", str(root / "CHANGELOG.md"), "--notes-file", str(root / "notes.md"),
                       "--github-output", str(output)]
            with mock.patch.object(sys, "argv", command):
                release_metadata.main()
            self.assertEqual((root / "notes.md").read_text(), section)
            self.assertEqual(output.read_text(), "existing=value\ntag=v0.1.0\nversion=0.1.0\nprerelease=false\n")
            for text in ("", section + section, "## [0.1.0] - 2026-10-11\n\n"):
                (root / "notes.md").unlink(missing_ok=True)
                (root / "CHANGELOG.md").write_text(text)
                with mock.patch.object(sys, "argv", command), mock.patch("sys.stderr", new=io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        release_metadata.main()
                self.assertEqual(error.exception.code, 1)
                self.assertFalse((root / "notes.md").exists())
                self.assertEqual(output.read_text(), "existing=value\ntag=v0.1.0\nversion=0.1.0\nprerelease=false\n")

    def test_public_store_metadata_and_linked_docs_are_release_inputs(self):
        manifest = json.loads((SCRIPTS.parent / "plugin.json").read_text())
        package = json.loads((SCRIPTS.parent / "package.json").read_text())
        self.assertEqual(manifest["name"], "Bluetooth Order")
        self.assertEqual(manifest["author"], "JediRhymeTrix")
        self.assertEqual(manifest["api_version"], 1)
        self.assertIsInstance(manifest["flags"], list)
        self.assertTrue(manifest["publish"]["description"].strip())
        self.assertTrue(all(isinstance(tag, str) and tag for tag in manifest["publish"]["tags"]))
        self.assertEqual(package["name"], "decky-bluetooth-order")
        self.assertEqual(package["type"], "module")
        self.assertTrue(packaging.SEMVER.fullmatch(package["version"]))
        self.assertEqual(package["author"], "JediRhymeTrix")
        expected_docs = {"README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
                         "documentation/automation.md", "documentation/distribution.md", "documentation/testing.md"}
        self.assertTrue(expected_docs <= set(packaging.SOURCE_FILES))
        for name in expected_docs:
            self.assertTrue((SCRIPTS.parent / name).read_text().strip(), name)
        notes = release_metadata.changelog_notes((SCRIPTS.parent / "CHANGELOG.md").read_text(), package["version"])
        self.assertTrue(notes.startswith(f"## [{package['version']}] - "))

    def test_checked_wheel_rejects_unverified_data(self):
        with self.assertRaisesRegex(packaging.PackageError, "SHA256"):
            vendor_deps.checked_wheel(b"not a verified wheel")

    def test_upstream_hash_manifest_includes_license_and_asyncio(self):
        manifest = json.loads((SCRIPTS / "dbus_next_files.json").read_text())
        self.assertEqual(manifest["LICENSE"], "c454ab6b8e0653a8eab828a8cbaaa368a1bbfba982178bb18b6177e311eae4cc")
        self.assertIn("aio/message_bus.py", manifest)
        self.assertIn("py.typed", manifest)
        self.assertEqual(len(manifest), 26)

    def test_bundled_api_license_and_project_mit_retained(self):
        license_text = (SCRIPTS.parent / "LICENSE").read_text()
        self.assertTrue(license_text.startswith("MIT License\n"))
        self.assertIn("Third-party notice: @decky/api 1.1.3", license_text)
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("d1b7f16070777a0ada939cbacd3606eeba0574f5", license_text)


class VendorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "requirements.txt").write_text("dbus-next==0.2.3\n")
        self.contents = {"__init__.py": b"# fake runtime\n", "LICENSE": b"MIT fixture license\n"}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in self.contents.items()}
        patcher = mock.patch.object(vendor_deps, "DBUS_FILES", hashes)
        patcher.start()
        self.addCleanup(patcher.stop)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("dbus_next/__init__.py", self.contents["__init__.py"])
            for name in sorted(vendor_deps.METADATA_FILES):
                data = self.contents["LICENSE"] if name.endswith("/LICENSE") else b"fixture metadata\n"
                archive.writestr(name, data)
        self.wheel_data = buffer.getvalue()
        self.wheel = self.root / vendor_deps.WHEEL_NAME
        self.wheel.write_bytes(self.wheel_data)
        patcher = mock.patch.object(vendor_deps, "WHEEL_SHA256", hashlib.sha256(self.wheel_data).hexdigest())
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_pip(self, command, **kwargs):
        self.assertIn("--target", command)
        for option in ("--isolated", "--no-index", "--no-deps", "--no-compile", "--only-binary=:all:"):
            self.assertIn(option, command)
        target = Path(command[command.index("--target") + 1])
        (target / "dbus_next").mkdir(parents=True)
        (target / "dbus_next" / "__init__.py").write_bytes(self.contents["__init__.py"])
        (target / "dbus_next-0.2.3.dist-info").mkdir()
        (target / "dbus_next-0.2.3.dist-info" / "METADATA").write_text("do not ship")

    def test_offline_checked_vendor_keeps_license_not_metadata(self):
        with mock.patch.object(vendor_deps.urllib.request, "urlopen", side_effect=AssertionError("no network")):
            with mock.patch.object(vendor_deps.subprocess, "run", side_effect=self.fake_pip) as pip:
                vendor_deps.vendor(self.root, self.wheel)
        pip.assert_called_once()
        target = self.root / "py_modules"
        self.assertEqual(sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()),
                         ["dbus_next/LICENSE", "dbus_next/__init__.py"])
        self.assertEqual((target / "dbus_next" / "LICENSE").read_bytes(), self.contents["LICENSE"])

    def test_wrong_pin_or_existing_target_fails_before_install(self):
        for requirements in ("dbus-next>=0.2.3\n", "dbus-next==0.2.3\nother==1\n"):
            (self.root / "requirements.txt").write_text(requirements)
            with mock.patch.object(vendor_deps.subprocess, "run") as pip:
                with self.assertRaises(packaging.PackageError):
                    vendor_deps.vendor(self.root, self.wheel)
                pip.assert_not_called()
        (self.root / "requirements.txt").write_text("dbus-next==0.2.3\n")
        (self.root / "py_modules").mkdir()
        with self.assertRaisesRegex(packaging.PackageError, "already exists"):
            vendor_deps.vendor(self.root, self.wheel)

    def test_pip_failure_does_not_leave_partial_runtime(self):
        import subprocess
        with mock.patch.object(vendor_deps.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
            with self.assertRaises(subprocess.CalledProcessError):
                vendor_deps.vendor(self.root, self.wheel)
        self.assertFalse((self.root / "py_modules").exists())
        self.assertEqual(list(self.root.glob(".vendor-*")), [])

    def test_wheel_layout_not_just_hash_is_checked(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../private.py", b"bad")
        data = buffer.getvalue()
        with mock.patch.object(vendor_deps, "WHEEL_SHA256", hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(packaging.PackageError, "allowlist"):
                vendor_deps.checked_wheel(data)

    def test_xml_fallback_is_hash_checked_staged_and_offline_when_present(self):
        (self.root / "py_modules").mkdir()
        contents = {"etree/__init__.py": b"# fake XML\n", "LICENSE": b"fixture license\n"}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        def download(url, **kwargs):
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.geturl.return_value = url
            response.read.return_value = contents["LICENSE" if url.endswith("/LICENSE") else "etree/__init__.py"]
            return response
        with mock.patch.object(vendor_deps, "XML_FILES", hashes):
            with mock.patch.object(vendor_deps.urllib.request, "urlopen", side_effect=download) as network:
                vendor_deps.vendor_xml(self.root)
            self.assertEqual(network.call_count, len(contents))
            with mock.patch.object(vendor_deps.urllib.request, "urlopen", side_effect=AssertionError("no network")):
                vendor_deps.vendor_xml(self.root)
        for name, data in contents.items():
            self.assertEqual((self.root / "py_modules" / "stdlib_xml" / name).read_bytes(), data)
        self.assertEqual(list(self.root.glob(".xml-vendor-*")), [])

    def test_xml_hash_mismatch_never_leaves_a_partial_dependency(self):
        (self.root / "py_modules").mkdir()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = vendor_deps.CPYTHON_URL + "Lib/xml/etree/__init__.py"
        response.read.return_value = b"not checked source"
        with mock.patch.object(vendor_deps, "XML_FILES", {"etree/__init__.py": "0" * 64}):
            with mock.patch.object(vendor_deps.urllib.request, "urlopen", return_value=response):
                with self.assertRaisesRegex(packaging.PackageError, "checked CPython"):
                    vendor_deps.vendor_xml(self.root)
        self.assertFalse((self.root / "py_modules" / "stdlib_xml").exists())
        self.assertEqual(list(self.root.glob(".xml-vendor-*")), [])


if __name__ == "__main__":
    unittest.main()
