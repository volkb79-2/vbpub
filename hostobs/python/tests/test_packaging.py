#!/usr/bin/env python3
"""Build and verify the hostobs client zipapp without pip or third-party packages.

The conformance lane runs this module through its normal unittest discovery.
For mutation evidence, ``--fault-probe missing-module`` and
``--fault-probe unresolved-entry-point`` deliberately build broken artifacts
and exit successfully only when the ordinary package checks reject them.
"""

from __future__ import annotations

import fnmatch
import json
import os
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
EXPECTED_BUILD_BACKEND = "setuptools.build_meta"
EXPECTED_BUILD_REQUIREMENTS = [
    "setuptools==84.0.0",
    "wheel==0.47.0",
    "setuptools_scm==10.0.5",
]
EXPECTED_ENTRYPOINT = "hostobs_client.mark:main"
EXPECTED_PACKAGE_INCLUDE = ["hostobs_client", "hostobs_client.*"]
EXPECTED_PACKAGE_WHERE = ["."]

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
    """Inventory the source tree independently from the pyproject-driven builder."""
    return {
        f"hostobs_client/{path.relative_to(PACKAGE_ROOT).as_posix()}"
        for path in PACKAGE_ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def package_directories_missing_initializer(package_root: Path) -> list[str]:
    missing = set()
    for source in package_root.rglob("*.py"):
        if "__pycache__" in source.parts:
            continue
        directory = source.parent
        while directory == package_root or package_root in directory.parents:
            if not (directory / "__init__.py").is_file():
                missing.add(directory.relative_to(package_root.parent).as_posix())
            if directory == package_root:
                break
            directory = directory.parent
    return sorted(missing)


def source_package_directories() -> dict[str, Path]:
    packages = {}
    for initializer in sorted(PACKAGE_ROOT.rglob("__init__.py")):
        if "__pycache__" in initializer.parts:
            continue
        package_dir = initializer.parent
        package_name = ".".join(package_dir.relative_to(PYTHON_ROOT).parts)
        packages[package_name] = package_dir
    return packages


def discovered_packages(contract: dict) -> dict[str, Path]:
    """Apply the package roots and include patterns declared for setuptools."""
    find = contract["tool"]["setuptools"]["packages"]["find"]
    packages = {}
    for where in find["where"]:
        source_root = (PYTHON_ROOT / where).resolve()
        if not source_root.is_dir():
            continue
        for initializer in sorted(source_root.rglob("__init__.py")):
            if "__pycache__" in initializer.parts:
                continue
            package_dir = initializer.parent
            package_name = ".".join(package_dir.relative_to(source_root).parts)
            if package_name and any(
                fnmatch.fnmatchcase(package_name, pattern)
                for pattern in find["include"]
            ):
                packages[package_name] = package_dir
    return packages


def package_data_matches(contract: dict, package_name: str, relative_path: str) -> bool:
    package_data = contract["tool"]["setuptools"].get("package-data", {})
    patterns = list(package_data.get(package_name, [])) + list(package_data.get("*", []))
    return any(package_data_pattern_matches(relative_path, pattern) for pattern in patterns)


def package_data_pattern_matches(relative_path: str, pattern: str) -> bool:
    path_parts = relative_path.split("/")
    pattern_parts = pattern.split("/")
    return len(path_parts) == len(pattern_parts) and all(
        fnmatch.fnmatchcase(path_part, pattern_part)
        for path_part, pattern_part in zip(path_parts, pattern_parts)
    )


def undeclared_package_data(contract: dict) -> list[str]:
    source_packages = source_package_directories()
    violations = []
    for source in sorted(PACKAGE_ROOT.rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts or source.suffix == ".py":
            continue
        owners = [
            (len(package_dir.parts), package_name, package_dir)
            for package_name, package_dir in source_packages.items()
            if source.is_relative_to(package_dir)
        ]
        if not owners:
            violations.append(source.relative_to(PYTHON_ROOT).as_posix())
            continue
        _, package_name, package_dir = max(owners)
        relative_path = source.relative_to(package_dir).as_posix()
        if not package_data_matches(contract, package_name, relative_path):
            violations.append(source.relative_to(PYTHON_ROOT).as_posix())
    return violations


def package_owner(source: Path, packages: dict[str, Path]) -> tuple[str, Path] | None:
    owners = [
        (len(package_dir.parts), package_name, package_dir)
        for package_name, package_dir in packages.items()
        if source.is_relative_to(package_dir)
    ]
    if not owners:
        return None
    _, package_name, package_dir = max(owners)
    return package_name, package_dir


def build_zipapp(artifact: Path, *, entrypoint: str, omit: frozenset[str] = frozenset()) -> None:
    contract = project_metadata()
    packages = discovered_packages(contract)
    with tempfile.TemporaryDirectory() as directory:
        stage = Path(directory)
        for package_name, package_dir in sorted(packages.items()):
            for source in sorted(package_dir.rglob("*")):
                if not source.is_file() or "__pycache__" in source.parts:
                    continue
                owner = package_owner(source, packages)
                if owner is None or owner[0] != package_name:
                    continue
                relative_path = source.relative_to(package_dir)
                if source.suffix != ".py" and not package_data_matches(
                    contract, package_name, relative_path.as_posix()
                ):
                    continue
                if source.name in omit:
                    continue
                staged_file = stage.joinpath(*package_name.split("."), relative_path)
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, staged_file)

        module_name, separator, attribute = entrypoint.partition(":")
        if not separator or not module_name or not attribute:
            raise PackagingFailure(f"invalid console entry point: {entrypoint}")
        (stage / "__main__.py").write_text(
            f"import sys\nfrom {module_name} import {attribute}\nsys.exit({attribute}())\n",
            encoding="utf-8",
        )
        zipapp.create_archive(
            stage,
            artifact,
            interpreter="/usr/bin/env python3",
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
            details.append("missing package file(s): " + ", ".join(missing))
        if unexpected:
            details.append("unexpected package file(s): " + ", ".join(unexpected))
        raise PackagingFailure("; ".join(details))


def verify_entrypoint(artifact: Path, entrypoint: str) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", IMPORT_ENTRYPOINT, str(artifact), entrypoint],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
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
        timeout=60,
    )
    if result.returncode or "usage: hostobs-mark" not in result.stdout:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise PackagingFailure(f"zipapp console command did not run: {detail}")


def verify_command_exit(
    artifact: Path,
    arguments: list[str],
    *,
    expected_exit: int,
    environment: dict[str, str],
    output_fragment: str,
) -> None:
    result = subprocess.run(
        [sys.executable, "-I", str(artifact), *arguments],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
        timeout=60,
    )
    if result.returncode != expected_exit:
        raise PackagingFailure(
            f"hostobs-mark {arguments!r} exited {result.returncode}, expected {expected_exit}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    if output_fragment not in result.stderr:
        raise PackagingFailure(
            f"hostobs-mark {arguments!r} did not report {output_fragment!r}: {result.stderr.strip()}"
        )


def verify_console_exit_contract(artifact: Path, missing_socket: Path) -> None:
    if missing_socket.exists():
        raise PackagingFailure(f"test socket path unexpectedly exists: {missing_socket}")
    environment = os.environ.copy()
    environment["HOSTOBS_OBSERVER_SOCKET"] = str(missing_socket)
    verify_command_exit(
        artifact,
        ["point", "hostobs.packaging-test"],
        expected_exit=0,
        environment=environment,
        output_fragment="hostobs-mark: mark not written:",
    )
    verify_command_exit(
        artifact,
        ["point", "hostobs.packaging-test", "--require"],
        expected_exit=1,
        environment=environment,
        output_fragment="hostobs-mark: mark not written:",
    )
    verify_command_exit(
        artifact,
        ["point"],
        expected_exit=2,
        environment=environment,
        output_fragment="usage: hostobs-mark",
    )


def run_fault_probe(mode: str) -> int:
    contract = project_metadata()
    entrypoint = contract["project"]["scripts"]["hostobs-mark"]
    with tempfile.TemporaryDirectory() as directory:
        artifact = Path(directory) / "hostobs-client.pyz"
        if mode == "missing-module":
            build_zipapp(artifact, entrypoint=entrypoint, omit=frozenset({"wire.py"}))
            check = lambda: verify_package_files(artifact)
            expected_rejection = "missing package file(s):"
        elif mode == "unresolved-entry-point":
            broken = "hostobs_client.mark:missing_main"
            build_zipapp(artifact, entrypoint=broken)
            check = lambda: verify_entrypoint(artifact, broken)
            expected_rejection = "does not resolve"
        else:
            raise SystemExit(f"unknown fault probe: {mode}")
        try:
            check()
        except PackagingFailure as error:
            return int(expected_rejection not in str(error))
    return 1


class Packaging(unittest.TestCase):
    def test_distribution_metadata_and_zipapp(self) -> None:
        contract = project_metadata()
        project = contract["project"]
        self.assertEqual(project["name"], "hostobs-client")
        self.assertEqual(project.get("dependencies", []), [])
        self.assertEqual(project["scripts"]["hostobs-mark"], EXPECTED_ENTRYPOINT)
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
            verify_console_exit_contract(artifact, Path(directory) / "hostobs.sock")

    def test_build_configuration_and_package_data(self) -> None:
        contract = project_metadata()
        self.assertEqual(contract["build-system"]["build-backend"], EXPECTED_BUILD_BACKEND)
        self.assertEqual(contract["build-system"]["requires"], EXPECTED_BUILD_REQUIREMENTS)
        self.assertEqual(contract["project"]["requires-python"], ">=3.11")

        setuptools = contract["tool"]["setuptools"]
        self.assertEqual(
            setuptools,
            {
                "include-package-data": False,
                "packages": {
                    "find": {
                        "where": EXPECTED_PACKAGE_WHERE,
                        "include": EXPECTED_PACKAGE_INCLUDE,
                        "namespaces": False,
                    }
                },
            },
        )
        for legacy_config in ("setup.py", "setup.cfg", "MANIFEST.in"):
            self.assertFalse(
                (PYTHON_ROOT / legacy_config).exists(),
                f"legacy packaging config {legacy_config} could override pyproject.toml",
            )
        find = setuptools["packages"]["find"]

        source_packages = source_package_directories()
        for package_name in source_packages:
            self.assertTrue(
                any(fnmatch.fnmatchcase(package_name, pattern) for pattern in find["include"]),
                f"package directory {package_name!r} is excluded by packages.find.include",
            )
        self.assertEqual(set(discovered_packages(contract)), set(source_packages))
        self.assertEqual(
            package_directories_missing_initializer(PACKAGE_ROOT),
            [],
            "every directory containing a module must be a regular package for setuptools",
        )
        self.assertEqual(
            undeclared_package_data(contract),
            [],
            "every non-.py file under hostobs_client must match tool.setuptools.package-data",
        )

    def test_consumer_docs_describe_the_unreleased_install(self) -> None:
        project_root = PYTHON_ROOT.parent
        packaging = (project_root / "docs" / "PACKAGING.md").read_text(encoding="utf-8")
        consumers = (project_root / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
        readme = (project_root / "README.md").read_text(encoding="utf-8")
        changes = (project_root / "CHANGES.md").read_text(encoding="utf-8")
        run_gate = (project_root / "run-gate.toml").read_text(encoding="utf-8")
        self.assertIn("build requirements in `python/pyproject.toml`", packaging)
        self.assertIn("`hostobs/cmru.toml` is not present yet", packaging)
        self.assertIn("planned for package A1", packaging)
        self.assertNotIn("wheel is built by `cmru handler wheel-build`", packaging)
        self.assertNotIn("A1's CMRU release contract uses", packaging)
        self.assertIn("`hostobs/cmru.toml` is not present yet", changes)
        self.assertNotIn("A1's CMRU wheel handler builds the release wheel", changes)
        self.assertIn("`hostobs/cmru.toml` is not present yet", run_gate)
        self.assertNotIn("wheel is built only by A1's CMRU handler", run_gate)
        self.assertIn(
            'python -m pip install --no-index --no-deps "$HOSTOBS_CLIENT_WHEEL"',
            consumers,
        )
        self.assertIn("python3 -m venv .venv", consumers)
        self.assertIn("`.venv/bin/hostobs-mark`", consumers)
        self.assertNotIn("python3 -m pip install", consumers)
        self.assertLess(
            readme.index("The Rust host-observability daemon"),
            readme.index("## Python client"),
        )
        for document in (consumers, readme):
            self.assertIn("No hostobs probe currently serves `annotate`", document)
            self.assertIn("`deploy/` does not install an observer socket yet", document)
            self.assertIn("prints a notice and exits 0", document)
            self.assertIn("`--require` exits 1", document)
            self.assertIn("membership in the `hostobs-observer` group", document)
        self.assertIn("hostobs-mark point hostobs.consumer-check --require", consumers)
        self.assertIn("hostobs-mark", readme)
        self.assertIn("No release exists yet", readme)

    def test_package_layout_and_data_globs_do_not_hide_wheel_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            package_root = Path(directory) / "hostobs_client"
            nested_package = package_root / "namespace" / "child"
            nested_package.mkdir(parents=True)
            (package_root / "__init__.py").write_text("", encoding="utf-8")
            (package_root / "namespace" / "module.py").write_text("", encoding="utf-8")
            (nested_package / "__init__.py").write_text("", encoding="utf-8")
            (nested_package / "module.py").write_text("", encoding="utf-8")
            self.assertEqual(
                package_directories_missing_initializer(package_root),
                ["hostobs_client/namespace"],
            )

            data_dir = package_root / "data"
            data_dir.mkdir()
            nested_data = data_dir / "nested.json"
            nested_data.write_text("{}", encoding="utf-8")
            root_data = package_root / "root.json"
            root_data.write_text("{}", encoding="utf-8")
            contract = {
                "tool": {
                    "setuptools": {"package-data": {"hostobs_client": ["*.json"]}}
                }
            }
            self.assertFalse(
                package_data_matches(
                    contract,
                    "hostobs_client",
                    nested_data.relative_to(package_root).as_posix(),
                )
            )
            self.assertTrue(
                package_data_matches(
                    contract,
                    "hostobs_client",
                    root_data.relative_to(package_root).as_posix(),
                )
            )

    def test_missing_module_is_rejected(self) -> None:
        contract = project_metadata()
        entrypoint = contract["project"]["scripts"]["hostobs-mark"]
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "hostobs-client.pyz"
            build_zipapp(artifact, entrypoint=entrypoint, omit=frozenset({"wire.py"}))
            with self.assertRaisesRegex(PackagingFailure, "missing package file.*wire.py"):
                verify_package_files(artifact)

    def test_unresolved_console_entry_point_is_rejected(self) -> None:
        broken = "hostobs_client.mark:missing_main"
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "hostobs-client.pyz"
            build_zipapp(artifact, entrypoint=broken)
            with self.assertRaisesRegex(PackagingFailure, "does not resolve"):
                verify_entrypoint(artifact, broken)

    def test_fault_probes_report_rejection_unambiguously(self) -> None:
        self.assertEqual(run_fault_probe("missing-module"), 0)
        self.assertEqual(run_fault_probe("unresolved-entry-point"), 0)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--fault-probe":
        if run_fault_probe(sys.argv[2]):
            raise SystemExit(f"packaging checks failed to reject fault: {sys.argv[2]}")
        raise SystemExit(0)
    unittest.main()
