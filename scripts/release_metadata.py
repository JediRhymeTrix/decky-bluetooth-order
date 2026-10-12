#!/usr/bin/env python3
"""Validate tag/version before release; emits metadata, never publishes."""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import zipfile

from package import ROOT, SEMVER, verify_archive


def metadata(version: str, tag: str) -> dict[str, str]:
    if not SEMVER.fullmatch(version) or tag != "v" + version:
        raise ValueError("release tag must be v followed by the exact package.json version")
    return {"tag": tag, "version": version, "prerelease": str("-" in version.split("+")[0]).lower()}


def changelog_notes(text: str, version: str) -> str:
    """Return exactly one dated version section, including its heading."""
    metadata(version, "v" + version)
    sections = list(re.finditer(r"(?m)^##[ \t]+([^\n]*)$", text))
    matches = [i for i, section in enumerate(sections)
               if section[1].strip().startswith(f"[{version}]")]
    if len(matches) != 1:
        raise ValueError(f"CHANGELOG must contain exactly one section for [{version}]")
    index = matches[0]
    section = sections[index]
    heading = re.fullmatch(rf"## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})", section[0])
    if not heading:
        raise ValueError("CHANGELOG version heading must be ## [version] - YYYY-MM-DD")
    date.fromisoformat(heading[1])
    end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
    body = text[section.end():end]
    content = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    content = re.sub(r"(?m)^#{1,6}[ \t]+.*$", "", content)
    if not content.strip():
        raise ValueError(f"CHANGELOG section [{version}] is empty")
    return text[section.start():end].strip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--package", type=Path, default=ROOT / "package.json")
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    parser.add_argument("--notes-file", type=Path, help="write the exact curated version section")
    parser.add_argument("--archive", type=Path, help="also verify the ZIP and its version matches the tag")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        result = metadata(json.loads(args.package.read_text(encoding="utf-8"))["version"], args.tag)
        notes = changelog_notes(args.changelog.read_text(encoding="utf-8"), result["version"])
        if args.archive:
            metadata(verify_archive(args.archive), args.tag)
        if args.notes_file:
            args.notes_file.write_text(notes, encoding="utf-8")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                for key, value in result.items():
                    output.write(f"{key}={value}\n")
        else:
            print(json.dumps(result, sort_keys=True))
    except (KeyError, OSError, ValueError, TypeError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Release metadata failed: {error}\n")


if __name__ == "__main__":
    main()
