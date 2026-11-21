"""Build fundkit and package it as dist/fundkit-<version>.zip.

Usage: python3 scripts/release.py

Standard library only. The zip holds the wheel, the sdist, the README, the two
docs the scheduled run and the quarterly runbook depend on, and an INSTALL.txt.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIST = REPO / "dist"
DOC_FILES = [
    REPO / "README.md",
    REPO / "docs" / "weekly-run-prompt.md",
    REPO / "docs" / "quarterly-deep-dive.md",
]


def read_version() -> str:
    with (REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def build_artifacts() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--sdist", "--outdir", str(DIST)],
        cwd=REPO,
        check=True,
    )


def find_one(pattern: str) -> Path:
    matches = sorted(DIST.glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"expected one file matching {pattern} in {DIST}, found {len(matches)}")
    return matches[0]


def install_text(wheel_name: str) -> str:
    return f"pip install {wheel_name} --break-system-packages\n"


def package(version: str) -> Path:
    wheel = find_one(f"fundkit-{version}-*.whl")
    sdist = find_one(f"fundkit-{version}.tar.gz")
    zip_path = DIST / f"fundkit-{version}.zip"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(wheel, arcname=wheel.name)
        zf.write(sdist, arcname=sdist.name)
        for doc in DOC_FILES:
            zf.write(doc, arcname=doc.name)
        zf.writestr("INSTALL.txt", install_text(wheel.name))
    return zip_path


def main() -> None:
    version = read_version()
    build_artifacts()
    zip_path = package(version)
    print(zip_path)


if __name__ == "__main__":
    main()
