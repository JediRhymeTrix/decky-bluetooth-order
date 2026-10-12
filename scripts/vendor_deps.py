#!/usr/bin/env python3
"""Vendor the hash-checked pure-Python dbus-next wheel using pip --target."""
from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from package import DBUS_FILES, XML_FILES, PackageError, ROOT, read_regular, safe_name, safe_root

WHEEL_NAME = "dbus_next-0.2.3-py3-none-any.whl"
WHEEL_SHA256 = "58948f9aff9db08316734c0be2a120f6dc502124d9642f55e90ac82ffb16a18b"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/d2/fc/"
    "c0a3f4c4eaa5a22fbef91713474666e13d0ea2a69c84532579490a9f2cc8/" + WHEEL_NAME
)
METADATA_FILES = {f"dbus_next-0.2.3.dist-info/{name}" for name in (
    "LICENSE", "METADATA", "WHEEL", "top_level.txt", "RECORD",
)}
CPYTHON_URL = "https://raw.githubusercontent.com/python/cpython/v3.11.7/"


def checked_wheel(data: bytes) -> bytes:
    if hashlib.sha256(data).hexdigest() != WHEEL_SHA256:
        raise PackageError("dbus-next wheel SHA256 mismatch")
    expected = {f"dbus_next/{name}" for name in DBUS_FILES if name != "LICENSE"} | METADATA_FILES
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or set(names) != expected:
            raise PackageError("dbus-next wheel contents differ from allowlist")
        for info in archive.infolist():
            safe_name(info.filename)
            if info.is_dir() or info.flag_bits & 1:
                raise PackageError("unexpected wheel entry")
        for name, digest in DBUS_FILES.items():
            source = "dbus_next-0.2.3.dist-info/LICENSE" if name == "LICENSE" else f"dbus_next/{name}"
            if hashlib.sha256(archive.read(source)).hexdigest() != digest:
                raise PackageError("dbus-next wheel file hash mismatch")
        return archive.read("dbus_next-0.2.3.dist-info/LICENSE")


def check_requirements(root: Path) -> None:
    requirements = read_regular(root, "requirements.txt").decode("ascii")
    pins = [line.strip() for line in requirements.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if pins != ["dbus-next==0.2.3"]:
        raise PackageError("requirements.txt must pin only dbus-next==0.2.3")


def vendor_xml(root: Path) -> None:
    safe_root(root / "py_modules")
    target = root / "py_modules" / "stdlib_xml"
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise PackageError("unsafe XML vendor target")
    if target.exists():
        for name, digest in XML_FILES.items():
            if hashlib.sha256(read_regular(root, f"py_modules/stdlib_xml/{name}")).hexdigest() != digest:
                raise PackageError("existing XML dependency differs from checked CPython 3.11.7")
        return
    with tempfile.TemporaryDirectory(prefix=".xml-vendor-", dir=root) as temporary:
        staged = Path(temporary) / "stdlib_xml"
        for name, digest in XML_FILES.items():
            source = "LICENSE" if name == "LICENSE" else "Lib/xml/" + name
            url = CPYTHON_URL + source
            with urllib.request.urlopen(url, timeout=30) as response:
                if response.geturl() != url:
                    raise PackageError("unexpected XML source download redirect")
                data = response.read(200_001)
            if len(data) > 200_000 or hashlib.sha256(data).hexdigest() != digest:
                raise PackageError("XML source differs from checked CPython 3.11.7")
            path = staged / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        staged.replace(target)


def vendor(root: Path, wheel: Path | None = None) -> None:
    safe_root(root)
    check_requirements(root)
    target = root / "py_modules"
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise PackageError("unsafe vendor target")
    if target.exists():
        raise PackageError("py_modules already exists; remove the generated directory before re-vendoring")
    if wheel:
        safe_root(wheel.absolute().parent)
        data = read_regular(wheel.absolute().parent, wheel.name)
    else:
        # Fixed PyPI wheel, HTTPS only, bounded size, verified before pip sees it.
        with urllib.request.urlopen(WHEEL_URL, timeout=30) as response:
            if response.geturl() != WHEEL_URL:
                raise PackageError("unexpected wheel download redirect")
            data = response.read(1_000_001)
        if len(data) > 1_000_000:
            raise PackageError("unexpected wheel size")
    license_data = checked_wheel(data)
    with tempfile.TemporaryDirectory(prefix=".vendor-", dir=root) as temporary:
        temporary = Path(temporary)
        wheel_path = temporary / WHEEL_NAME
        wheel_path.write_bytes(data)
        installed = temporary / "installed"
        env = os.environ.copy()
        env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_NO_INPUT="1")
        subprocess.run([
            sys.executable, "-m", "pip", "--isolated", "install", "--no-index", "--no-deps",
            "--only-binary=:all:", "--no-compile", "--disable-pip-version-check",
            "--target", str(installed), str(wheel_path),
        ], env=env, check=True, stdout=subprocess.DEVNULL)
        dependency = installed / "dbus_next"
        (dependency / "LICENSE").write_bytes(license_data)
        for name, digest in DBUS_FILES.items():
            if hashlib.sha256(read_regular(dependency, name)).hexdigest() != digest:
                raise PackageError("pip output differs from checked dependency")
        # Retain only runtime package and its MIT license, not pip metadata or URLs.
        staged = temporary / "py_modules"
        staged.mkdir()
        shutil.move(str(dependency), staged / "dbus_next")
        staged.replace(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--wheel", type=Path, help="use the exact checked wheel offline")
    args = parser.parse_args()
    try:
        # Idempotent normal builds do not perform a network request if already valid.
        root = args.root.absolute()
        safe_root(root)
        check_requirements(root)
        if (root / "py_modules").exists():
            for name, digest in DBUS_FILES.items():
                if hashlib.sha256(read_regular(root, f"py_modules/dbus_next/{name}")).hexdigest() != digest:
                    raise PackageError("existing dependency differs; remove generated py_modules and re-vendor")
        else:
            vendor(root, args.wheel)
        vendor_xml(root)
        print("Vendored checked dbus-next 0.2.3 (MIT) and CPython 3.11.7 XML fallback; no runtime network needed")
    except (PackageError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Vendoring failed: {error}\n")


if __name__ == "__main__":
    main()
