"""Independent locked acceptance for P24's distribution contract.

The suite builds real wheels with the carver-owned offline closure, inspects
them with the stdlib ZIP/email readers, installs into fresh environments, and
drives the standalone verifier as a subprocess.  It never imports Assay from
the source tree and never asks production code to generate its expected
manifest or hashes.
"""

from __future__ import annotations

import email
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

ASSET_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = Path(
    os.environ.get("ASSAY_P24_PROJECT_ROOT", ASSET_ROOT.parents[2])
).resolve()
REPO_ROOT = PROJECT_ROOT.parent
HELPER = PROJECT_ROOT / "gate" / "distribution" / "release_wheel.py"
WHEELHOUSE = ASSET_ROOT / "wheelhouse"
WHEELHOUSE_MANIFEST = ASSET_ROOT / "wheelhouse-manifest.json"
BUILD_REQUIREMENTS_FILE = ASSET_ROOT / "build-requirements.txt"
PRODUCTION_DISTRIBUTION = PROJECT_ROOT / "gate" / "distribution"
PRODUCTION_WHEELHOUSE = PRODUCTION_DISTRIBUTION / "build-wheelhouse"
FIXTURE_WHEEL = ASSET_ROOT / "fixtures" / "assay-1.2.3-py3-none-any.whl"
FIXTURE_MANIFEST = ASSET_ROOT / "fixtures" / "release-manifest.json"
RELEASE_SHA256 = "fdec65b2c944de61cfbfb0e0672f96136becc2c42db88c131d70ea19383a7578"
BUILD_REQUIREMENTS = (
    "setuptools==84.0.0",
    "wheel==0.47.0",
    "setuptools-scm==10.0.5",
    "packaging==26.3",
    "vcs-versioning==2.2.4",
)


def run(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,  # failsafe only; no assertion depends on elapsed time
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {argv!r}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def helper(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    assert HELPER.is_file(), f"P24 helper is absent: {HELPER}"
    return run([sys.executable, str(HELPER), *args], check=check, timeout=15)


def manifest_document(**updates: object) -> dict[str, object]:
    document = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    document.update(updates)
    return document


def write_manifest(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def metadata_from_wheel(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        assert len(names) == 1, names
        info = archive.getinfo(names[0])
        assert info.file_size <= 1024 * 1024
        document = email.message_from_bytes(archive.read(info))
    assert document.get_all("Name") is not None
    assert document.get_all("Version") is not None
    assert len(document.get_all("Name")) == 1
    assert len(document.get_all("Version")) == 1
    return document["Name"], document["Version"]


def rewrite_metadata(source: Path, target: Path, replacement: bytes, *, duplicate: bool = False) -> None:
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED
    ) as outgoing:
        metadata_name = next(
            name for name in incoming.namelist() if name.endswith(".dist-info/METADATA")
        )
        for info in incoming.infolist():
            data = incoming.read(info)
            if info.filename == metadata_name:
                data = replacement
            outgoing.writestr(info, data)
        if duplicate:
            outgoing.writestr(metadata_name, replacement)


def assert_refused(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout == ""
    assert proc.stderr.startswith("release-wheel: REFUSED:")
    assert "Traceback" not in proc.stderr


def test_locked_asset_hashes() -> None:
    locked = json.loads((ASSET_ROOT / "fixture-manifest.json").read_text())
    assert locked["schema_version"] == 1
    for relative, expected in locked["files"].items():
        actual = hashlib.sha256((ASSET_ROOT / relative).read_bytes()).hexdigest()
        assert actual == expected, relative


def test_build_requirements_are_the_exact_closed_wheelhouse() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert tuple(pyproject["build-system"]["requires"]) == BUILD_REQUIREMENTS
    lock = json.loads(WHEELHOUSE_MANIFEST.read_text())
    assert tuple(item["requirement"] for item in lock["requirements"]) == BUILD_REQUIREMENTS
    locked_lines = BUILD_REQUIREMENTS_FILE.read_text().splitlines()
    assert tuple(line.split(" --hash=", 1)[0] for line in locked_lines) == BUILD_REQUIREMENTS
    assert all(re.fullmatch(r"[^ ]+ --hash=sha256:[0-9a-f]{64}", line) for line in locked_lines)
    assert {path.name for path in WHEELHOUSE.glob("*.whl")} == {
        item["filename"] for item in lock["requirements"]
    }
    for item in lock["requirements"]:
        wheel = WHEELHOUSE / item["filename"]
        assert wheel.stat().st_size == item["size"]
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == item["sha256"]
        production_wheel = PRODUCTION_WHEELHOUSE / item["filename"]
        assert production_wheel.read_bytes() == wheel.read_bytes()
    assert (
        PRODUCTION_DISTRIBUTION / "build-requirements.txt"
    ).read_bytes() == BUILD_REQUIREMENTS_FILE.read_bytes()
    assert (
        PRODUCTION_DISTRIBUTION / "build-wheelhouse-manifest.json"
    ).read_bytes() == WHEELHOUSE_MANIFEST.read_bytes()
    assert {path.name for path in PRODUCTION_WHEELHOUSE.glob("*.whl")} == {
        path.name for path in WHEELHOUSE.glob("*.whl")
    }


def test_wheelhouse_metadata_proves_the_transitive_closure() -> None:
    observed: dict[str, tuple[str, set[str]]] = {}
    for wheel in WHEELHOUSE.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            distribution = wheel.name.split("-")[0].replace("_", "-").lower()
            candidates = [
                name
                for name in archive.namelist()
                if name.lower().startswith(distribution.replace("-", "_") + "-")
                and name.endswith(".dist-info/METADATA")
            ]
            assert len(candidates) == 1, (wheel.name, candidates)
            document = email.message_from_bytes(archive.read(candidates[0]))
        observed[document["Name"].lower()] = (
            document["Version"],
            set(document.get_all("Requires-Dist", [])),
        )
    assert observed["setuptools-scm"][0] == "10.0.5"
    assert "vcs-versioning>=1.0.0.dev0" in observed["setuptools-scm"][1]
    assert "packaging>=20" in observed["setuptools-scm"][1]
    assert observed["vcs-versioning"][0] == "2.2.4"
    assert "packaging>=26.2" in observed["vcs-versioning"][1]
    assert "packaging >= 24.0" in observed["wheel"][1]
    assert observed["packaging"][0] == "26.3"


def test_positive_fixture_is_independent_and_exact() -> None:
    assert FIXTURE_WHEEL.stat().st_size == 232651
    assert hashlib.sha256(FIXTURE_WHEEL.read_bytes()).hexdigest() == RELEASE_SHA256
    assert metadata_from_wheel(FIXTURE_WHEEL) == ("assay", "1.2.3")
    assert FIXTURE_MANIFEST.read_bytes() == (
        b'{"schema_version":1,"filename":"assay-1.2.3-py3-none-any.whl",'
        b'"version":"1.2.3","sha256":"' + RELEASE_SHA256.encode() + b'"}\n'
    )


def test_verifier_emits_exact_hash_requirement_only_after_success() -> None:
    proc = helper(
        "verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(FIXTURE_MANIFEST)
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout == (
        f"assay @ {FIXTURE_WHEEL.absolute().as_uri()} "
        f"--hash=sha256:{RELEASE_SHA256}\n"
    )


def test_manifest_command_recreates_only_the_locked_document(tmp_path: Path) -> None:
    output = tmp_path / "release-manifest.json"
    proc = helper(
        "manifest",
        "--wheel",
        str(FIXTURE_WHEEL),
        "--version",
        "1.2.3",
        "--output",
        str(output),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == proc.stderr == ""
    assert output.read_bytes() == FIXTURE_MANIFEST.read_bytes()
    assert_refused(
        helper(
            "manifest",
            "--wheel",
            str(FIXTURE_WHEEL),
            "--version",
            "1.2.3",
            "--output",
            str(output),
        )
    )


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 1, "filename": "assay-1.2.3-py3-none-any.whl", "version": "1.2.3"},
        {**manifest_document(), "extra": 1},
        {**manifest_document(), "schema_version": True},
        {**manifest_document(), "schema_version": 2},
        {**manifest_document(), "version": "01.2.3"},
        {**manifest_document(), "version": "1.2"},
        {**manifest_document(), "sha256": "A" * 64},
    ],
)
def test_closed_manifest_rejects_each_wrong_shape(tmp_path: Path, document: object) -> None:
    candidate = write_manifest(tmp_path / "manifest.json", document)
    assert_refused(
        helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(candidate))
    )


def test_duplicate_manifest_member_is_rejected(tmp_path: Path) -> None:
    candidate = tmp_path / "manifest.json"
    candidate.write_text(
        '{"schema_version":1,"schema_version":1,'
        '"filename":"assay-1.2.3-py3-none-any.whl","version":"1.2.3",'
        f'"sha256":"{RELEASE_SHA256}"}}\n',
        encoding="utf-8",
    )
    assert_refused(
        helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(candidate))
    )


def test_basename_and_hash_are_independent_refusals(tmp_path: Path) -> None:
    wrong_name = tmp_path / "renamed.whl"
    shutil.copyfile(FIXTURE_WHEEL, wrong_name)
    assert_refused(
        helper("verify", "--wheel", str(wrong_name), "--manifest", str(FIXTURE_MANIFEST))
    )
    wrong_hash = write_manifest(
        tmp_path / "wrong-hash.json", manifest_document(sha256="1" * 64)
    )
    assert_refused(
        helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(wrong_hash))
    )


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("mutation", ["name", "version", "duplicate"])
def test_metadata_is_independently_bound_after_hash(
    tmp_path: Path, mutation: str
) -> None:
    target = tmp_path / FIXTURE_WHEEL.name
    with zipfile.ZipFile(FIXTURE_WHEEL) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        original = archive.read(metadata_name)
    if mutation == "name":
        replacement = original.replace(b"Name: assay\n", b"Name: other\n", 1)
    elif mutation == "version":
        replacement = original.replace(b"Version: 1.2.3\n", b"Version: 9.9.9\n", 1)
    else:
        replacement = original
    rewrite_metadata(FIXTURE_WHEEL, target, replacement, duplicate=mutation == "duplicate")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        manifest_document(sha256=hashlib.sha256(target.read_bytes()).hexdigest()),
    )
    assert_refused(helper("verify", "--wheel", str(target), "--manifest", str(manifest)))


def test_manifest_and_wheel_safe_open_bounds(tmp_path: Path) -> None:
    oversized_manifest = tmp_path / "manifest.json"
    oversized_manifest.write_bytes(b" " * 4097)
    assert_refused(
        helper(
            "verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(oversized_manifest)
        )
    )

    link_manifest = tmp_path / "link.json"
    link_manifest.symlink_to(FIXTURE_MANIFEST)
    assert_refused(
        helper("verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(link_manifest))
    )

    link_wheel = tmp_path / FIXTURE_WHEEL.name
    link_wheel.symlink_to(FIXTURE_WHEEL)
    assert_refused(
        helper("verify", "--wheel", str(link_wheel), "--manifest", str(FIXTURE_MANIFEST))
    )


def test_special_and_oversized_wheels_refuse_without_waiting(tmp_path: Path) -> None:
    fifo_dir = tmp_path / "fifo"
    fifo_dir.mkdir()
    fifo = fifo_dir / FIXTURE_WHEEL.name
    os.mkfifo(fifo)
    assert_refused(
        helper("verify", "--wheel", str(fifo), "--manifest", str(FIXTURE_MANIFEST))
    )
    huge_dir = tmp_path / "huge"
    huge_dir.mkdir()
    huge = huge_dir / FIXTURE_WHEEL.name
    with huge.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024 + 1)
    assert_refused(
        helper("verify", "--wheel", str(huge), "--manifest", str(FIXTURE_MANIFEST))
    )


def test_pip_rechecks_the_verified_hash_at_install_time(tmp_path: Path) -> None:
    verified = helper(
        "verify", "--wheel", str(FIXTURE_WHEEL), "--manifest", str(FIXTURE_MANIFEST)
    )
    assert verified.returncode == 0, verified.stderr
    requirements = tmp_path / "verified-requirement.txt"
    requirements.write_text(verified.stdout, encoding="utf-8")
    venv = tmp_path / "positive-venv"
    run([sys.executable, "-m", "venv", str(venv)])
    run(
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--require-hashes",
            "-r",
            str(requirements),
        ]
    )
    witness = run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "from importlib.metadata import version; import assay; "
            "print(version('assay'), assay.__version__, assay.__file__)",
        ]
    ).stdout.strip()
    assert witness.startswith("1.2.3 1.2.3 ")
    assert str(venv) in witness
    assert str(REPO_ROOT) not in witness

    raced_dir = tmp_path / "raced"
    raced_dir.mkdir()
    raced_wheel = raced_dir / FIXTURE_WHEEL.name
    shutil.copyfile(FIXTURE_WHEEL, raced_wheel)
    raced_manifest = write_manifest(raced_dir / "manifest.json", manifest_document())
    before_race = helper(
        "verify", "--wheel", str(raced_wheel), "--manifest", str(raced_manifest)
    )
    assert before_race.returncode == 0, before_race.stderr
    raced_wheel.write_bytes(raced_wheel.read_bytes() + b"changed-after-verification")
    raced_requirements = raced_dir / "verified-requirement.txt"
    raced_requirements.write_text(before_race.stdout, encoding="utf-8")
    raced_venv = tmp_path / "raced-venv"
    run([sys.executable, "-m", "venv", str(raced_venv)])
    refused = run(
        [
            str(raced_venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--require-hashes",
            "-r",
            str(raced_requirements),
        ],
        check=False,
    )
    assert refused.returncode != 0
    assert "THESE PACKAGES DO NOT MATCH THE HASHES" in refused.stderr


@dataclass(frozen=True)
class BuildMatrix:
    clean_first: Path
    clean_second: Path
    dirty: Path
    fallback: Path
    build_python: Path


def tracked_distribution_source(destination: Path) -> None:
    paths = run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "-z",
            "--",
            "assay/pyproject.toml",
            "assay/src",
        ]
    ).stdout.split("\0")
    for relative in filter(None, paths):
        source = REPO_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def build_environment(build_python: Path, home: Path) -> dict[str, str]:
    return {
        "HOME": str(home),
        "PATH": f"{build_python.parent}:/usr/bin:/bin",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": "946684800",
    }


def make_release_source(root: Path) -> Path:
    tracked_distribution_source(root)
    run(["git", "init", "-q", "-b", "main"], cwd=root)
    run(["git", "add", "assay"], cwd=root)
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay",
        "GIT_AUTHOR_EMAIL": "assay@example.invalid",
        "GIT_COMMITTER_NAME": "Assay",
        "GIT_COMMITTER_EMAIL": "assay@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }
    run(["git", "commit", "-q", "-m", "release"], cwd=root, env=identity)
    run(["git", "tag", "assay-v1.2.3"], cwd=root)
    return root / "assay"


def build_one(project: Path, output: Path, build_python: Path, home: Path) -> Path:
    output.mkdir(parents=True)
    run(
        [
            str(build_python),
            "-m",
            "pip",
            "wheel",
            "--no-index",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(output),
            str(project),
        ],
        env=build_environment(build_python, home),
    )
    wheels = list(output.glob("assay-*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


@pytest.fixture(scope="session")
def build_matrix(tmp_path_factory: pytest.TempPathFactory) -> BuildMatrix:
    root = tmp_path_factory.mktemp("p24-real-builds")
    build_venv = root / "build-venv"
    run([sys.executable, "-m", "venv", str(build_venv)])
    build_python = build_venv / "bin" / "python"
    run(
        [
            str(build_python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(WHEELHOUSE),
            "--require-hashes",
            "-r",
            str(BUILD_REQUIREMENTS_FILE),
        ]
    )
    installed = json.loads(
        run(
            [
                str(build_python),
                "-c",
                "import json; from importlib.metadata import version; "
                f"print(json.dumps({{n: version(n) for n in {tuple(r.split('==')[0] for r in BUILD_REQUIREMENTS)!r}}}))",
            ]
        ).stdout
    )
    assert installed == {requirement.split("==")[0]: requirement.split("==")[1] for requirement in BUILD_REQUIREMENTS}

    first_project = make_release_source(root / "first" / "release-root")
    second_project = make_release_source(root / "second" / "release-root")
    clean_first = build_one(first_project, root / "out-first", build_python, root / "home")
    clean_second = build_one(second_project, root / "out-second", build_python, root / "home")

    with (first_project / "src" / "assay" / "__init__.py").open("a", encoding="utf-8") as stream:
        stream.write("\nP24_DIRTY_WITNESS = True\n")
    dirty = build_one(first_project, root / "out-dirty", build_python, root / "home")

    no_vcs = root / "no-vcs"
    tracked_distribution_source(no_vcs)
    fallback = build_one(no_vcs / "assay", root / "out-fallback", build_python, root / "home")
    return BuildMatrix(clean_first, clean_second, dirty, fallback, build_python)


def test_two_real_offline_tagged_builds_are_byte_identical(build_matrix: BuildMatrix) -> None:
    assert build_matrix.clean_first.name == build_matrix.clean_second.name == "assay-1.2.3-py3-none-any.whl"
    assert build_matrix.clean_first.read_bytes() == build_matrix.clean_second.read_bytes()
    assert metadata_from_wheel(build_matrix.clean_first) == ("assay", "1.2.3")
    with zipfile.ZipFile(build_matrix.clean_first) as archive:
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in archive.namelist())


def test_dirty_and_no_vcs_builds_have_explicit_distinct_identities(
    build_matrix: BuildMatrix,
) -> None:
    dirty_name, dirty_version = metadata_from_wheel(build_matrix.dirty)
    assert dirty_name == "assay"
    assert dirty_version != "1.2.3"
    assert ".dev" in dirty_version and "+g" in dirty_version
    assert build_matrix.dirty.read_bytes() != build_matrix.clean_first.read_bytes()
    assert build_matrix.fallback.name == "assay-0.1.0-py3-none-any.whl"
    assert metadata_from_wheel(build_matrix.fallback) == ("assay", "0.1.0")


def test_real_tagged_wheel_install_and_artifact_versions_agree(
    build_matrix: BuildMatrix, tmp_path: Path
) -> None:
    venv = tmp_path / "consumer-venv"
    run([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin" / "python"
    run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(build_matrix.clean_first)])
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    run(["git", "init", "-q", "-b", "main"], cwd=consumer)
    (consumer / "assay.toml").write_text(
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0"]\n'
        'enforcement = "gate"\n'
        f'argv = ["{python}", "-c", "raise SystemExit(0)"]\n'
        "env = {}\n"
        "env_passthrough = []\n"
        'budget = "5m"\n'
        "allow_argv_append = false\n",
        encoding="utf-8",
    )
    run(["git", "add", "assay.toml"], cwd=consumer)
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Consumer",
        "GIT_AUTHOR_EMAIL": "consumer@example.invalid",
        "GIT_COMMITTER_NAME": "Consumer",
        "GIT_COMMITTER_EMAIL": "consumer@example.invalid",
    }
    run(["git", "commit", "-q", "-m", "consumer"], cwd=consumer, env=identity)
    verdict = tmp_path / "verdict.json"
    clean_env = {"HOME": str(tmp_path), "PATH": f"{venv / 'bin'}:/usr/bin:/bin", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    run(
        [str(venv / "bin" / "assay"), "run", "package", "--verdict-json", str(verdict)],
        cwd=consumer,
        env=clean_env,
    )
    document = json.loads(verdict.read_text())
    assert document["assay_version"] == "1.2.3"
    witness = run(
        [
            str(python),
            "-c",
            "from importlib.metadata import version; import assay; "
            "print(version('assay'), assay.__version__, assay.__file__)",
        ],
        env=clean_env,
    ).stdout.strip()
    assert witness.startswith("1.2.3 1.2.3 ")
    assert str(venv) in witness
    assert str(REPO_ROOT) not in witness
    assert tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["project"]["dependencies"] == []
