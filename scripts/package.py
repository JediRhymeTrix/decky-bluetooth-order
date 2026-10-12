#!/usr/bin/env python3
"""Build a complete, allowlisted Decky release; never read an installed plugin."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FOLDER = "bluetooth-order"
ASSET = f"{FOLDER}.zip"
SOURCE_FILES = (
    "LICENSE", "README.md", "package.json", "plugin.json", "main.py",
    "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md",
    "documentation/automation.md", "documentation/distribution.md", "documentation/testing.md",
    "backend/__init__.py", "backend/state.py", "backend/observer.py",
    "dist/index.js",
)
DBUS_FILES = json.loads(Path(__file__).with_name("dbus_next_files.json").read_text())
XML_FILES = json.loads(Path(__file__).with_name("stdlib_xml_files.json").read_text())
DEPENDENCY_FILES = (tuple(f"py_modules/dbus_next/{name}" for name in DBUS_FILES) +
                    tuple(f"py_modules/stdlib_xml/{name}" for name in XML_FILES))
MAP_FILE = "dist/index.js.map"
PRERELEASE_ID = r"(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(
    rf"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    rf"(?:-{PRERELEASE_ID}(?:\.{PRERELEASE_ID})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
MAX_FILE_SIZE = 10_000_000
MAX_TOTAL_SIZE = 25_000_000
API_LICENSE_SHA256 = "38965778962143b687faff3a5b69ef22dd228e0ca4f5a3752893e016ede87c6a"
# shortcut: signature screening is not a general secret detector; review every release payload.
SECRET = re.compile(
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b|"
    r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])"
)


class PackageError(ValueError):
    """Invalid or incomplete release input."""


def safe_name(name: str) -> None:
    if (not name or "\\" in name or ":" in name or any(ord(c) < 32 for c in name) or name.startswith("/")
            or any(p in ("", ".", "..") for p in name.split("/"))):
        raise PackageError("unsafe package path")


def safe_root(root: Path) -> None:
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink():
            raise PackageError("source/output ancestor symlink not allowed")
    if not root.is_dir():
        raise PackageError("source root must be a directory")


def read_regular(root: Path, name: str) -> bytes:
    safe_name(name)
    current = root
    for part in ("", *PurePosixPath(name).parts):
        if part:
            current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as error:
            raise PackageError(f"missing required file: {name}") from error
        if stat.S_ISLNK(mode):
            raise PackageError(f"symlink not allowed: {name}")
    if not stat.S_ISREG(mode):
        raise PackageError(f"not a regular file: {name}")
    if current.stat().st_nlink != 1:
        raise PackageError(f"hard link not allowed: {name}")
    if current.stat().st_size > MAX_FILE_SIZE:
        raise PackageError(f"file exceeds release size limit: {name}")
    return current.read_bytes()


def parse_json(data: bytes, name: str) -> dict:
    try:
        obj = json.loads(data)
    except (UnicodeError, ValueError) as error:
        raise PackageError(f"invalid JSON: {name}") from error
    if not isinstance(obj, dict):
        raise PackageError(f"expected JSON object: {name}")
    return obj


def check_bundle(data: bytes) -> None:
    try:
        js = data.decode("utf-8")
    except UnicodeError as error:
        raise PackageError("frontend must be UTF-8") from error
    if not re.search(r"\bexport\s+(?:default\b|\{[^}]*\bas\s+default\b)", js):
        raise PackageError("frontend is not a default-export ESM build")
    if not re.search(r"\bDFL\.", js):
        raise PackageError("frontend must use Decky's shared external DFL")
    for marker in ("initModuleCache", "webpackChunksteamui", "__webpack_require__",
                   "__webpack_modules__", "Initializing all modules", "module.exports"):
        if marker in js:
            raise PackageError("frontend embeds a module initializer or non-ESM code")
    # Parse only: no import, UI execution, Steam, or module factory invocation.
    result = subprocess.run(
        ["node", "--check", "--input-type=module"], input=data,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if result.returncode:
        raise PackageError("frontend ESM syntax check failed")


def clean_source_map(data: bytes) -> bytes:
    obj = parse_json(data, MAP_FILE)
    if obj.get("version") != 3 or obj.get("file") != "index.js":
        raise PackageError("unexpected source map format")
    if obj.get("sourceRoot") not in (None, "") or "sections" in obj:
        raise PackageError("unexpected source map root or sections")
    if (not isinstance(obj.get("mappings"), str) or not isinstance(obj.get("names"), list)
            or any(not isinstance(name, str) for name in obj["names"])):
        raise PackageError("invalid source map mappings or names")
    sources = obj.get("sources")
    if not isinstance(sources, list) or not sources:
        raise PackageError("source map missing sources")
    cleaned = []
    prefix = "decky://decky/plugin/Bluetooth%20Order/"
    for source in sources:
        if not isinstance(source, str):
            raise PackageError("invalid source map path")
        if source.startswith(prefix):
            source = source[len(prefix):]
        elif source.startswith("../"):
            # The known Rollup relative-root form; do not normalize other traversal.
            source = source[3:]
        safe_name(source)
        if "%" in source or "?" in source or "#" in source:
            raise PackageError("source map path has encoded or URL control characters")
        if not (source.startswith("src/") or source.startswith("node_modules/")):
            raise PackageError("source map includes a non-project path")
        if not source.endswith((".ts", ".tsx", ".js", ".mjs")):
            raise PackageError("source map includes a non-code source")
        cleaned.append(prefix + source)
    # Debug mappings only, no original source text or local filesystem paths.
    result = {key: obj[key] for key in ("version", "file", "names", "mappings") if key in obj}
    result["sources"] = cleaned
    return (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()


def validate_payload(files: dict[str, bytes], *, include_map: bool = False) -> str:
    expected = set(SOURCE_FILES) | set(DEPENDENCY_FILES)
    if include_map:
        expected.add(MAP_FILE)
    if set(files) != expected:
        raise PackageError("archive contents do not match the complete release allowlist")
    for name, data in files.items():
        safe_name(name)
        if name == "main.py" or name.startswith("backend/"):
            try:
                compile(data, name, "exec")
            except (SyntaxError, ValueError) as error:
                raise PackageError(f"invalid Python source: {name}") from error
        if SECRET.search(data.decode("utf-8", errors="replace")):
            raise PackageError(f"possible secret or device identity in {name}")
    package = parse_json(files["package.json"], "package.json")
    if package.get("name") != "decky-bluetooth-order" or package.get("type") != "module":
        raise PackageError("package.json must identify the complete ESM plugin")
    version = package.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise PackageError("invalid package version")
    manifest = parse_json(files["plugin.json"], "plugin.json")
    if manifest.get("name") != "Bluetooth Order" or type(manifest.get("api_version")) is not int or manifest["api_version"] != 1:
        raise PackageError("plugin manifest name/API does not match the Decky plugin")
    if not files["LICENSE"].strip() or not files["README.md"].strip() or not files["main.py"].strip():
        raise PackageError("release documentation or backend is empty")
    license_data = files["LICENSE"]
    api_start = license_data.find(b"                   GNU LESSER GENERAL PUBLIC LICENSE")
    if (not license_data.startswith(b"MIT License\n") or api_start < 0
            or hashlib.sha256(license_data[api_start:]).hexdigest() != API_LICENSE_SHA256):
        raise PackageError("project MIT or bundled @decky/api LGPL license is missing or changed")
    for name, digest in DBUS_FILES.items():
        if hashlib.sha256(files[f"py_modules/dbus_next/{name}"]).hexdigest() != digest:
            raise PackageError(f"vendored dbus-next file differs from checked 0.2.3 wheel: {name}")
    for name, digest in XML_FILES.items():
        if hashlib.sha256(files[f"py_modules/stdlib_xml/{name}"]).hexdigest() != digest:
            raise PackageError(f"vendored XML file differs from checked CPython 3.11.7: {name}")
    check_bundle(files["dist/index.js"])
    refs = re.findall(rb"(?m)^\s*//# sourceMappingURL=(.*?)\s*$", files["dist/index.js"])
    if (refs != ([b"index.js.map"] if include_map else [])
            or files["dist/index.js"].count(b"sourceMappingURL") != int(include_map)):
        raise PackageError("unexpected or dangling frontend source map reference")
    if include_map and clean_source_map(files[MAP_FILE]) != files[MAP_FILE]:
        raise PackageError("archive source map is not sanitized")
    return version


def collect(root: Path, *, include_map: bool = False) -> dict[str, bytes]:
    safe_root(root)
    files = {name: read_regular(root, name) for name in (*SOURCE_FILES, *DEPENDENCY_FILES)}
    # Ignore all non-allowlisted source files, never recursively archive the checkout.
    js = files["dist/index.js"]
    references = re.findall(rb"(?m)^\s*//# sourceMappingURL=(.*?)\s*$", js)
    if (references and references != [b"index.js.map"]) or js.count(b"sourceMappingURL") != len(references):
        raise PackageError("unsafe or unexpected build source map reference")
    if include_map:
        files[MAP_FILE] = clean_source_map(read_regular(root, MAP_FILE))
        if not references:
            js = js.rstrip() + b"\n//# sourceMappingURL=index.js.map\n"
    else:
        js = re.sub(rb"(?m)^\s*//# sourceMappingURL=index\.js\.map\s*$", b"", js)
    files["dist/index.js"] = js
    validate_payload(files, include_map=include_map)
    return files


def verify_archive(path: Path) -> str:
    files = {}
    total = 0
    allowed = set(SOURCE_FILES) | set(DEPENDENCY_FILES) | {MAP_FILE}
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            safe_name(info.filename)
            prefix = FOLDER + "/"
            if not info.filename.startswith(prefix):
                raise PackageError("archive must have exactly one bluetooth-order root")
            name = info.filename[len(prefix):]
            if name not in allowed:
                raise PackageError("archive contains a non-allowlisted path")
            if name in files or info.is_dir():
                raise PackageError("duplicate or directory archive entry")
            if info.create_system != 3 or info.external_attr >> 16 != (stat.S_IFREG | 0o644):
                raise PackageError("archive entry must be a non-executable regular file")
            if info.flag_bits & 1:
                raise PackageError("encrypted archive entry")
            total += info.file_size
            if info.file_size > MAX_FILE_SIZE or total > MAX_TOTAL_SIZE:
                raise PackageError("archive exceeds release size limit")
            files[name] = archive.read(info)
    return validate_payload(files, include_map=MAP_FILE in files)


def package(root: Path, *, include_map: bool = False) -> Path:
    files = collect(root, include_map=include_map)
    out = root / "out"
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise PackageError("unsafe output directory")
    out.mkdir(exist_ok=True)
    archive_path, sums_path = out / ASSET, out / "SHA256SUMS"
    for target in (archive_path, sums_path):
        if target.is_symlink() or (target.exists() and (not target.is_file() or target.stat().st_nlink != 1)):
            raise PackageError("unsafe output file or link")
    with tempfile.TemporaryDirectory(prefix=".package-", dir=out) as temporary:
        staged = Path(temporary) / ASSET
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(f"{FOLDER}/{name}", date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compresslevel=9)
        verify_archive(staged)
        checksum = hashlib.sha256(staged.read_bytes()).hexdigest()
        staged_sums = Path(temporary) / "SHA256SUMS"
        staged_sums.write_text(f"{checksum}  {ASSET}\n", encoding="ascii")
        staged.replace(archive_path)
        staged_sums.replace(sums_path)
    return archive_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="validate release inputs without writing")
    parser.add_argument("--verify", type=Path, help="validate an existing release archive")
    parser.add_argument("--include-source-map", action="store_true", help="include sanitized mappings, not source text")
    args = parser.parse_args()
    try:
        if args.verify:
            print(f"Verified {ASSET} version {verify_archive(args.verify)}")
        elif args.check:
            collect(args.root.absolute(), include_map=args.include_source_map)
            print("Complete release inputs validated")
        else:
            print(f"Built {package(args.root.absolute(), include_map=args.include_source_map)}")
    except (PackageError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Packaging failed: {error}\n")


if __name__ == "__main__":
    main()
