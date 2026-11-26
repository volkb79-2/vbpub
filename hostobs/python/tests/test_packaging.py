#!/usr/bin/env python3
"""Build and verify the hostobs client zipapp without pip or third-party packages.

The conformance lane runs this module through its normal unittest discovery.
For mutation evidence, ``--fault-probe missing-module`` and
``--fault-probe unresolved-entry-point`` deliberately build broken artifacts
and exit nonzero when the ordinary package checks reject them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipapp
import zipfile
from pathlib import Path


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = PYTHON_ROOT / "hostobs_client"
PYPROJECT = PYTHON_ROOT / "pyproject.toml"

IMPORT_ENTRYPOINT = r"""
import importlib, json, sys
archive, entrypoint = sys.argv[1:]
sys.path.insert(0, archive)
module_name, separator, attribute = entrypoint.partition(":")
if not separator or not module_name or not attribute:
    raise SystemExit("invalid console entry point: " + entrypoint)
package = importlib.import_module("hostobs_client")
target_module = importlib.import_module(module_name)
target = getattr(target_module, attribute)
if not callable(target):
    raise SystemExit("console entry point is not callable: " + entrypoint)
if any(name == "cgprofile" or name.startswith("cgprofile.") for name in sys.modules):
    raise SystemExit("hostobs_client imported cgprofile")
print(json.dumps({"loader": type(package.__spec__.loader).__name__, "entrypoint": entrypoint}))
"""


class PackagingFailure(AssertionError):
    """The built artifact violates the package contract."""


def project_metadata() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def expected_package_files() -> set[str]:
    return {f"hostobs_client/{path.name}" for path in PACKAGE_ROOT.glob("*.py")}


def build_zipapp(artifact: Path, *, entrypoint: str, omit: frozenset[str] = frozenset()) -> None:
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        staged_package = stage / "hostobs_client"
        staged_package.mkdir()
        for source in sorted(PACKAGE_ROOT.glob("*.py")):
            if source.name not in omit:
                shutil.copyfile(source, staged_package / source.name)
        zipapp.create_archive(
            stage,
            artifact,
            interpreter="/usr/bin/env python3",
            main=entrypoint,
        )


def verify_package_files(artifact: Path) -> None:
    with zipfile.ZipFile(artifact) as archive:
        actual = {
            name
            for name in archive.namelist()
            if name.startswith("hostobs_client/") and not name.endswith("/")
        }
    expected = expected_package_files()
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing package module(s): " + ", ".join(missing))
        if unexpected:
            details.append("unexpected package file(s): " + ", ".join(unexpected))
        raise PackagingFailure("; ".join(details))


def verify_entrypoint(artifact: Path, entrypoint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", IMPORT_ENTRYPOINT, str(artifact), entrypoint],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PackagingFailure(f"console entry point {entrypoint!r} does not resolve: {detail}")
    try:
        seen = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PackagingFailure(f"entry-point subprocess returned invalid output: {result.stdout!r}") from error
    if seen != {"loader": "zipimporter", "entrypoint": entrypoint}:
        raise PackagingFailure(f"entry point did not load from the artifact: {seen!r}")


def verify_zipapp_command(artifact: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(artifact), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode or "usage: hostobs-mark" not in result.stdout:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PackagingFailure(f"zipapp console command did not run: {detail}")


def run_fault_probe(mode: str) -> None:
    contract = project_metadata()
    entrypoint = contract["project"]["scripts"]["hostobs-mark"]
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "hostobs-client.pyz"
        if mode == "missing-module":
            build_zipapp(artifact, entrypoint=entrypoint, omit=frozenset({"wire.py"}))
            verify_package_files(artifact)
        elif mode == "unresolved-entry-point":
            broken = "hostobs_client.mark:missing_main"
            build_zipapp(artifact, entrypoint=broken)
            verify_entrypoint(artifact, broken)
        else:
            raise SystemExit(f"unknown fault probe: {mode}")
    raise SystemExit(f"packaging checks failed to reject fault: {mode}")


class Packaging(unittest.TestCase):
    def test_distribution_metadata_and_zipapp(self) -> None:
        contract = project_metadata()
        project = contract["project"]
        self.assertEqual(project["name"], "hostobs-client")
        self.assertEqual(project.get("dependencies", []), [])
        self.assertEqual(project["scripts"]["hostobs-mark"], "hostobs_client.mark:main")
        self.assertNotIn("version", project)
        self.assertEqual(project["dynamic"], ["version"])
        scm = contract["tool"]["setuptools_scm"]
        self.assertNotIn("fallback_version", scm)
        self.assertEqual(scm["tag_regex"], r"^hostobs-v(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:[a-zA-Z0-9.+-]*)?)$")

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "hostobs-client.pyz"
            entrypoint = project["scripts"]["hostobs-mark"]
            build_zipapp(artifact, entrypoint=entrypoint)
            verify_package_files(artifact)
            verify_entrypoint(artifact, entrypoint)
            verify_zipapp_command(artifact)

    def test_missing_module_is_rejected(self) -> None:
        contract = project_metadata()
        entrypoint = contract["project"]["scripts"]["hostobs-mark"]
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "hostobs-client.pyz"
            build_zipapp(artifact, entrypoint=entrypoint, omit=frozenset({"wire.py"}))
            with self.assertRaisesRegex(PackagingFailure, "missing package module.*wire.py"):
                verify_package_files(artifact)

    def test_unresolved_console_entry_point_is_rejected(self) -> None:
        broken = "hostobs_client.mark:missing_main"
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "hostobs-client.pyz"
            build_zipapp(artifact, entrypoint=broken)
            with self.assertRaisesRegex(PackagingFailure, "does not resolve"):
                verify_entrypoint(artifact, broken)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fault-probe":
        run_fault_probe(sys.argv[2])
    unittest.main()
