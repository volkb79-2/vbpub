"""Behavioral checks for the exact-wheel release handshake."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
SPEC = importlib.util.spec_from_file_location(
    "release_candidate", TOOLS / "release_candidate.py"
)
assert SPEC is not None and SPEC.loader is not None
release_candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_candidate)


def _receipt(**overrides):
    record = {
        "schema_version": 1,
        "candidate_commit": "1" * 40,
        "version": "24.0.0",
        "filename": "run_gate-24.0.0-py3-none-any.whl",
        "sha256": "a" * 64,
        "image_tag": "tester-unified:local",
        "image_id": "sha256:" + "b" * 64,
    }
    record.update(overrides)
    return record


def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_release_image_bootstrap_uses_only_explicit_overrides(monkeypatch, tmp_path):
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(
        release_candidate, "PROJECT_ROOT", tmp_path / "run-gate-project",
    )
    monkeypatch.setenv(
        "RUN_GATE_TESTER_UNIFIED_BASE_IMAGE",
        "mcr.microsoft.com/devcontainers/python:3.14-trixie",
    )
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE", "source")
    monkeypatch.setattr(
        release_candidate, "_build_cli_extended_source_wheel",
        lambda: (tmp_path / "cli-extended.whl", "a" * 64),
    )

    command = release_candidate._tester_image_build_command("tester-unified:first")

    assert command == [
        str(tmp_path / "tester-unified" / "build-image.sh"),
        "--wheel-dir", str(tmp_path / "run-gate-project" / "dist"),
        "--tag", "tester-unified:first",
        "--base-image", "mcr.microsoft.com/devcontainers/python:3.14-trixie",
        "--cli-extended-resolve", "local",
        "--cli-extended-wheel-sha256", "a" * 64,
    ]

    monkeypatch.delenv("RUN_GATE_TESTER_UNIFIED_BASE_IMAGE")
    monkeypatch.delenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE")
    command = release_candidate._tester_image_build_command("tester-unified:normal")
    assert command == [
        str(tmp_path / "tester-unified" / "build-image.sh"),
        "--wheel-dir", str(tmp_path / "run-gate-project" / "dist"),
        "--tag", "tester-unified:normal",
    ]


def test_release_image_bootstrap_rejects_an_unknown_cli_extended_mode(monkeypatch):
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE", "automatic")

    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="must be source, pinned or latest",
    ):
        release_candidate._tester_image_build_command("tester-unified:bad")


def test_source_bootstrap_builds_and_digest_checks_cli_extended_from_checkout(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(
        release_candidate, "PROJECT_ROOT", tmp_path / "run-gate-project",
    )
    cmru_root = tmp_path / "cmru"
    cmru_root.mkdir()
    (cmru_root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["cli-extended>=0.3.0"]\n',
        encoding="utf-8",
    )
    source = tmp_path / "libraries" / "cli-extended"
    dist = source / "dist"
    local_wheels = tmp_path / "tester-unified" / "local-wheel"
    expected_wheel = dist / "cli_extended-0.3.0+bootstrap.source-py3-none-any.whl"
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "1760486400")

    def fake_run(argv, *, cwd, env, check, capture_output, text):
        assert argv == ["cmru", "handler", "wheel-build", "--cwd", str(source)]
        assert cwd == tmp_path
        assert env["SETUPTOOLS_SCM_PRETEND_VERSION_FOR_CLI_EXTENDED"] == "0.3.0+bootstrap.source"
        assert env["SOURCE_DATE_EPOCH"] == "1760486400"
        dist.mkdir(parents=True, exist_ok=True)
        with ZipFile(expected_wheel, "w") as archive:
            archive.writestr(
                "cli_extended-0.3.0+bootstrap.source.dist-info/METADATA",
                "Name: cli-extended\nVersion: 0.3.0+bootstrap.source\n",
            )
        return _completed()

    monkeypatch.setattr(release_candidate.subprocess, "run", fake_run)

    wheel, digest = release_candidate._build_cli_extended_source_wheel()

    assert wheel == local_wheels / expected_wheel.name
    assert wheel.read_bytes() == expected_wheel.read_bytes()
    assert digest == hashlib.sha256(expected_wheel.read_bytes()).hexdigest()


_SOURCE_VERSION = "0.3.0+bootstrap.source"
_SOURCE_WHEEL = f"cli_extended-{_SOURCE_VERSION}-py3-none-any.whl"
_SOURCE_METADATA = f"cli_extended-{_SOURCE_VERSION}.dist-info/METADATA"
_CMRU_DEPENDENCIES = '[project]\ndependencies = ["cli-extended>=0.3.0"]\n'


def _source_bootstrap(monkeypatch, tmp_path, *, pyproject=_CMRU_DEPENDENCIES, build=None):
    """A scratch checkout for `_build_cli_extended_source_wheel`.

    `pyproject` is the CMRU pyproject text (None leaves the file absent);
    `build(dist)` plays the `cmru handler wheel-build` child and returns its
    completed result (default: success that writes nothing).
    """
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", tmp_path / "run-gate-project")
    if pyproject is not None:
        (tmp_path / "cmru").mkdir()
        (tmp_path / "cmru" / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "1760486400")
    dist = tmp_path / "libraries" / "cli-extended" / "dist"

    def fake_run(_argv, **_kwargs):
        dist.mkdir(parents=True, exist_ok=True)
        return build(dist) if build is not None else _completed()

    monkeypatch.setattr(release_candidate.subprocess, "run", fake_run)
    return dist


def _write_source_wheel(dist, members, name=_SOURCE_WHEEL):
    """Write a wheel whose archive holds exactly `members` (name -> text)."""
    dist.mkdir(parents=True, exist_ok=True)
    with ZipFile(dist / name, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
    return dist / name


def _good_metadata(**overrides):
    fields = {"Name": "cli-extended", "Version": _SOURCE_VERSION}
    fields.update(overrides)
    return "".join(f"{key}: {value}\n" for key, value in fields.items() if value is not None)


@pytest.mark.parametrize(
    "pyproject",
    [None, "this is = [not toml"],
    ids=["missing-file", "invalid-toml"],
)
def test_source_bootstrap_refuses_an_unreadable_cmru_pyproject(monkeypatch, tmp_path, pyproject):
    _source_bootstrap(monkeypatch, tmp_path, pyproject=pyproject)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot read CMRU runtime requirements from .*pyproject.toml",
    ):
        release_candidate._build_cli_extended_source_wheel()


@pytest.mark.parametrize(
    "pyproject",
    [
        "[tool.other]\nx = 1\n",
        "[project]\nname = 'cmru'\n",
        '[project]\ndependencies = "cli-extended>=0.3.0"\n',
    ],
    ids=["no-project-table", "no-dependencies", "dependencies-not-a-list"],
)
def test_source_bootstrap_refuses_a_pyproject_without_a_dependency_list(
    monkeypatch, tmp_path, pyproject,
):
    _source_bootstrap(monkeypatch, tmp_path, pyproject=pyproject)
    with pytest.raises(
        release_candidate.ReleaseCandidateError, match="has no project.dependencies list",
    ):
        release_candidate._build_cli_extended_source_wheel()


def test_source_bootstrap_refuses_dependencies_without_a_cli_extended_floor(monkeypatch, tmp_path):
    # Non-strings, other packages, a pin (==) and a pre-release floor are not
    # a `cli-extended>=<release floor>` requirement.
    _source_bootstrap(
        monkeypatch, tmp_path,
        pyproject=(
            '[project]\ndependencies = ["pytest>=8", 7, "cli-extended==0.3.0", '
            '"cli-extended>=0.3.0rc1"]\n'
        ),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=r"declares no cli-extended>=<floor> runtime dependency",
    ):
        release_candidate._build_cli_extended_source_wheel()


def test_source_bootstrap_reports_a_failed_wheel_build_with_bounded_stderr(monkeypatch, tmp_path):
    _source_bootstrap(
        monkeypatch, tmp_path,
        build=lambda _dist: _completed(stderr="  " + "e" * 600 + "  ", returncode=3),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot build cli-extended from the selected checkout",
    ) as error:
        release_candidate._build_cli_extended_source_wheel()
    message = str(error.value)
    assert message.endswith(": " + "e" * 500)
    assert "e" * 501 not in message


@pytest.mark.parametrize(
    ("extra_names", "found"),
    [
        ((), 0),
        (
            (f"cli_extended-{_SOURCE_VERSION}-py3-none-any.whl",
             f"cli_extended-{_SOURCE_VERSION}-py3-none-linux_x86_64.whl"),
            2,
        ),
    ],
    ids=["none-built", "ambiguous"],
)
def test_source_bootstrap_requires_exactly_one_built_wheel(
    monkeypatch, tmp_path, extra_names, found,
):
    def build(dist):
        for name in extra_names:
            _write_source_wheel(dist, {_SOURCE_METADATA: _good_metadata()}, name=name)
        return _completed()

    _source_bootstrap(monkeypatch, tmp_path, build=build)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=rf"expected one cli-extended {re.escape(_SOURCE_VERSION)} source wheel .*found {found}",
    ):
        release_candidate._build_cli_extended_source_wheel()


@pytest.mark.parametrize(
    ("members", "count"),
    [
        ({"cli_extended/__init__.py": ""}, 0),
        (
            {
                _SOURCE_METADATA: _good_metadata(),
                "cli_extended-other.dist-info/METADATA": _good_metadata(),
            },
            2,
        ),
    ],
    ids=["no-metadata", "two-metadata"],
)
def test_source_bootstrap_requires_exactly_one_metadata_file(
    monkeypatch, tmp_path, members, count,
):
    _source_bootstrap(
        monkeypatch, tmp_path,
        build=lambda dist: (_write_source_wheel(dist, members), _completed())[1],
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=rf"cli-extended source wheel {re.escape(_SOURCE_WHEEL)} has {count} METADATA files",
    ):
        release_candidate._build_cli_extended_source_wheel()


def test_source_bootstrap_reports_a_wheel_that_is_not_a_zip_archive(monkeypatch, tmp_path):
    def build(dist):
        dist.mkdir(parents=True, exist_ok=True)
        (dist / _SOURCE_WHEEL).write_bytes(b"this is not a zip archive")
        return _completed()

    _source_bootstrap(monkeypatch, tmp_path, build=build)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot verify cli-extended source wheel .*" + re.escape(_SOURCE_WHEEL),
    ) as error:
        release_candidate._build_cli_extended_source_wheel()
    assert error.value.__cause__ is not None


def test_source_bootstrap_refuses_a_wheel_for_a_different_distribution(monkeypatch, tmp_path):
    _source_bootstrap(
        monkeypatch, tmp_path,
        build=lambda dist: (
            _write_source_wheel(dist, {_SOURCE_METADATA: _good_metadata(Name="cli-extended-extras")}),
            _completed(),
        )[1],
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=rf"{re.escape(_SOURCE_WHEEL)} is not a cli-extended wheel",
    ):
        release_candidate._build_cli_extended_source_wheel()


@pytest.mark.parametrize(
    ("version", "reported"),
    [("0.3.0", "'0.3.0'"), (None, r"'\(missing\)'")],
    ids=["different-version", "no-version-field"],
)
def test_source_bootstrap_refuses_a_wheel_with_the_wrong_version(
    monkeypatch, tmp_path, version, reported,
):
    _source_bootstrap(
        monkeypatch, tmp_path,
        build=lambda dist: (
            _write_source_wheel(dist, {_SOURCE_METADATA: _good_metadata(Version=version)}),
            _completed(),
        )[1],
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=rf"cli-extended source wheel version is {reported}, expected '{re.escape(_SOURCE_VERSION)}'",
    ):
        release_candidate._build_cli_extended_source_wheel()


def test_source_bootstrap_refuses_a_stale_unrelated_local_wheel(monkeypatch, tmp_path):
    local_wheels = tmp_path / "tester-unified" / "local-wheel"
    local_wheels.mkdir(parents=True)
    stale = local_wheels / "cli_extended-0.2.0-py3-none-any.whl"
    stale.write_bytes(b"stale bootstrap input")
    _source_bootstrap(
        monkeypatch, tmp_path,
        build=lambda dist: (
            _write_source_wheel(dist, {_SOURCE_METADATA: _good_metadata()}),
            _completed(),
        )[1],
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="contains another cli-extended wheel.*cli_extended-0.2.0-py3-none-any.whl",
    ):
        release_candidate._build_cli_extended_source_wheel()
    assert stale.read_bytes() == b"stale bootstrap input"
    assert not (local_wheels / _SOURCE_WHEEL).exists()


def test_source_bootstrap_replaces_its_own_previous_local_wheel(monkeypatch, tmp_path):
    """The same-named wheel from an earlier attempt is not 'unrelated'."""
    local_wheels = tmp_path / "tester-unified" / "local-wheel"
    local_wheels.mkdir(parents=True)
    (local_wheels / _SOURCE_WHEEL).write_bytes(b"previous attempt")
    built = {}

    def build(dist):
        built["path"] = _write_source_wheel(dist, {_SOURCE_METADATA: _good_metadata()})
        return _completed()

    _source_bootstrap(monkeypatch, tmp_path, build=build)
    wheel, digest = release_candidate._build_cli_extended_source_wheel()
    assert wheel == local_wheels / _SOURCE_WHEEL
    assert wheel.read_bytes() == built["path"].read_bytes()
    assert digest == hashlib.sha256(built["path"].read_bytes()).hexdigest()


def test_release_image_bootstrap_passes_pinned_mode_through(monkeypatch, tmp_path):
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", tmp_path / "run-gate-project")
    monkeypatch.delenv("RUN_GATE_TESTER_UNIFIED_BASE_IMAGE", raising=False)
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE", " pinned ")
    mode = "pinned"
    monkeypatch.setattr(
        release_candidate, "_build_cli_extended_source_wheel",
        lambda: pytest.fail("only source mode builds a wheel from the checkout"),
    )

    command = release_candidate._tester_image_build_command("tester-unified:mode")

    assert command == [
        str(tmp_path / "tester-unified" / "build-image.sh"),
        "--wheel-dir", str(tmp_path / "run-gate-project" / "dist"),
        "--tag", "tester-unified:mode",
        "--cli-extended-resolve", mode,
    ]


PUBLISHED = "c" * 64
OLD_PIN = "a" * 64


def test_latest_mode_passes_the_published_digest_to_the_builder(monkeypatch, tmp_path):
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", tmp_path / "run-gate-project")
    monkeypatch.delenv("RUN_GATE_TESTER_UNIFIED_BASE_IMAGE", raising=False)
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE", " latest ")
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_SHA256", f" {PUBLISHED} ")

    command = release_candidate._tester_image_build_command("tester-unified:mode")

    assert command == [
        str(tmp_path / "tester-unified" / "build-image.sh"),
        "--wheel-dir", str(tmp_path / "run-gate-project" / "dist"),
        "--tag", "tester-unified:mode",
        "--cli-extended-resolve", "latest",
        "--cli-extended-wheel-sha256", PUBLISHED,
    ]
    assert release_candidate._expected_cli_extended_sha256(command) == PUBLISHED


@pytest.mark.parametrize("digest", [None, "", "abc", "C" * 64, "c" * 63])
def test_latest_mode_refuses_to_build_without_a_valid_published_digest(monkeypatch, digest):
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE", "latest")
    if digest is None:
        monkeypatch.delenv("RUN_GATE_TESTER_CLI_EXTENDED_SHA256", raising=False)
    else:
        monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_SHA256", digest)

    with pytest.raises(
        release_candidate.ReleaseCandidateError, match="requires RUN_GATE_TESTER_CLI_EXTENDED_SHA256",
    ):
        release_candidate._tester_image_build_command("tester-unified:bad")


def test_expected_cli_extended_digest_follows_the_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    dockerfile = tmp_path / "tester-unified" / "Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text(
        "ARG CLI_EXTENDED_RESOLVE=pinned\nARG CLI_EXTENDED_WHEEL_SHA256=" + OLD_PIN + "\n",
        encoding="utf-8",
    )
    # pinned and the default install the Dockerfile's pinned wheel ...
    assert release_candidate._expected_cli_extended_sha256(["b", "--tag", "t"]) == OLD_PIN
    assert release_candidate._expected_cli_extended_sha256(
        ["b", "--cli-extended-resolve", "pinned"],
    ) == OLD_PIN
    # ... source (local) and latest install exactly the digest they pass.
    assert release_candidate._expected_cli_extended_sha256(
        ["b", "--cli-extended-resolve", "local", "--cli-extended-wheel-sha256", PUBLISHED],
    ) == PUBLISHED
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="no pinned cli-extended digest"):
        release_candidate._expected_cli_extended_sha256(["b"])


@pytest.mark.parametrize(
    ("labels", "message"),
    [
        ({}, "no valid org.vbpub.cli-extended.wheel-sha256 label"),
        ({"org.vbpub.cli-extended.wheel-sha256": "nope"}, "no valid org.vbpub.cli-extended"),
        ({"org.vbpub.cli-extended.wheel-sha256": PUBLISHED}, None),
    ],
)
def test_image_cli_extended_label_must_be_a_digest(monkeypatch, labels, message):
    monkeypatch.setattr(
        release_candidate.subprocess, "run",
        lambda *_args, **_kwargs: _completed(json.dumps(labels)),
    )
    if message is None:
        assert release_candidate._image_cli_extended_sha256("tester-unified:x") == PUBLISHED
        return
    with pytest.raises(release_candidate.ReleaseCandidateError, match=message):
        release_candidate._image_cli_extended_sha256("tester-unified:x")


def _prepare_with_label(monkeypatch, tmp_path, *, mode, label):
    wheel = {"version": "24.0.0", "filename": "run_gate-24.0.0-py3-none-any.whl", "sha256": "a" * 64}
    monkeypatch.setattr(
        release_candidate, "prepared_release_version", lambda _path, **_kwargs: "24.0.0",
    )
    monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", "1" * 40)
    monkeypatch.setattr(
        release_candidate, "_git", lambda *args: "1700000000" if args[0] == "log" else "1" * 40,
    )
    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: wheel.copy())
    monkeypatch.setattr(
        release_candidate, "_image_identity",
        lambda _tag: {"version": "24.0.0", "sha256": "a" * 64},
    )
    monkeypatch.setattr(release_candidate, "RECEIPT_PATH", tmp_path / "receipt.json")
    monkeypatch.setattr(release_candidate, "_image_cli_extended_sha256", lambda _tag: label)
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_RESOLVE", mode)
    monkeypatch.setenv("RUN_GATE_TESTER_CLI_EXTENDED_SHA256", PUBLISHED)
    results = iter((_completed(), _completed(), _completed("sha256:" + "b" * 64)))
    monkeypatch.setattr(release_candidate.subprocess, "run", lambda *_args, **_kwargs: next(results))
    return release_candidate.prepare


def test_prepare_accepts_a_latest_image_labelled_with_the_published_digest(monkeypatch, tmp_path):
    prepare = _prepare_with_label(monkeypatch, tmp_path, mode="latest", label=PUBLISHED)

    prepare()

    assert (tmp_path / "receipt.json").is_file()


def test_prepare_refuses_a_latest_image_that_kept_the_old_pinned_bytes(monkeypatch, tmp_path):
    """The newly published wheel differs from the old pin: an image labelled with
    the old digest must be refused before the gate may use it, and no receipt is
    written."""
    prepare = _prepare_with_label(monkeypatch, tmp_path, mode="latest", label=OLD_PIN)

    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=f"installed cli-extended wheel sha256 {OLD_PIN}, expected {PUBLISHED}",
    ):
        prepare()

    assert not (tmp_path / "receipt.json").exists()


def test_prepared_release_version_uses_first_dated_stable_section(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text(
        "# Changelog\n\n## [Unreleased]\n\nPending.\n\n"
        "<!-- cmru: release history -->\n\n"
        "## [24.0.0] - 2026-10-14\n\nCandidate.\n\n"
        "## [23.10.0] - 2026-10-07\n\nReleased.\n",
        encoding="utf-8",
    )

    source_entry = str(release_candidate.REPOSITORY_ROOT / "cmru" / "src")
    was_present = source_entry in sys.path
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *args: (
            "run-gate-v23.10.0\nrun-gate-v23.10.0.dev1\nrun-gate-vfuture"
            if args[0] == "tag" else ""
        ),
    )
    assert release_candidate.prepared_release_version(changes) == "24.0.0"
    assert (source_entry in sys.path) is was_present


def test_prepared_release_version_refuses_a_heading_that_is_already_released(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("## [24.0.0] - 2026-10-14\n\nAlready tagged.\n", encoding="utf-8")
    monkeypatch.setattr(release_candidate, "_git", lambda *args: "run-gate-v24.0.0" if args[0] == "tag" else "")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="not newer than the latest release tag"):
        release_candidate.prepared_release_version(changes)


def test_prepared_release_version_allows_an_explicit_first_release(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("## [24.0.0] - 2026-10-14\n\nFirst stable release.\n", encoding="utf-8")
    monkeypatch.setattr(release_candidate, "_git", lambda *args: "")

    with pytest.raises(release_candidate.NoStableReleaseTag, match="no stable.*release tag"):
        release_candidate.prepared_release_version(changes)

    assert release_candidate.prepared_release_version(
        changes, allow_first_release=True,
    ) == "24.0.0"


def test_prepared_release_version_refuses_unreleased_only_changelog(tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("# Changelog\n\n## [Unreleased]\n\nPending.\n", encoding="utf-8")

    with pytest.raises(release_candidate.ReleaseCandidateError, match="no dated stable release section"):
        release_candidate.prepared_release_version(changes)


def test_prepared_release_version_refuses_an_unrelated_installed_cmru(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("## [24.0.0] - 2026-10-14\n\nCandidate.\n", encoding="utf-8")
    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    parser = tmp_path / "elsewhere.py"
    parser.write_text("", encoding="utf-8")
    fake_cmru = type(sys)("cmru")
    fake_cmru.__path__ = []
    fake_notes = type(sys)("cmru.release_notes")
    fake_notes.__file__ = str(parser)
    fake_notes.markdown_h2_headings = lambda _markdown: ()
    monkeypatch.setitem(sys.modules, "cmru", fake_cmru)
    monkeypatch.setitem(sys.modules, "cmru.release_notes", fake_notes)

    with pytest.raises(release_candidate.ReleaseCandidateError, match="outside the selected checkout"):
        release_candidate.prepared_release_version(changes)


def test_prepared_release_version_keeps_existing_cmru_source_path(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("## [24.0.0] - 2026-10-14\n", encoding="utf-8")
    source_entry = str(release_candidate.REPOSITORY_ROOT / "cmru" / "src")
    monkeypatch.setattr(sys, "path", [source_entry, *(entry for entry in sys.path if entry != source_entry)])
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *args: "run-gate-v23.10.0" if args[0] == "tag" else "",
    )

    assert release_candidate.prepared_release_version(changes) == "24.0.0"
    assert sys.path.count(source_entry) == 1


def test_stable_semver_and_release_tag_parsing(monkeypatch):
    assert release_candidate._semver_key("24.0.0") == (24, 0, 0)
    with pytest.raises(release_candidate.ReleaseCandidateError, match="invalid stable Run-Gate version"):
        release_candidate._semver_key("24.0")

    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *_args: "run-gate-v23.9.1\nrun-gate-v23.10.0\nrun-gate-v23.10.0.dev1\nrun-gate-vgarbage",
    )
    assert release_candidate._latest_release_tag() == ("run-gate-v23.10.0", "23.10.0")

    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *_args: "run-gate-v23.10\nrun-gate-v23.10.0.dev1\nrun-gate-vlegacy",
    )
    with pytest.raises(release_candidate.ReleaseCandidateError, match="no stable.*release tag"):
        release_candidate._latest_release_tag()


def test_prepared_release_version_reports_import_and_read_failures(monkeypatch, tmp_path):
    import builtins

    monkeypatch.setattr(release_candidate, "REPOSITORY_ROOT", tmp_path)
    source_entry = str(tmp_path / "cmru" / "src")
    original_import = builtins.__import__

    def fail_cmru_import(name, *args, **kwargs):
        if name == "cmru.release_notes":
            raise ImportError("missing parser")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_cmru_import)
    with pytest.raises(release_candidate.ReleaseCandidateError, match="cannot load.*parser"):
        release_candidate.prepared_release_version(tmp_path / "CHANGES.md")
    assert source_entry not in sys.path

    monkeypatch.setattr(builtins, "__import__", original_import)
    fake_cmru = type(sys)("cmru")
    fake_cmru.__path__ = []
    fake_notes = type(sys)("cmru.release_notes")
    fake_notes.__file__ = str(tmp_path / "cmru" / "src" / "cmru" / "release_notes.py")
    fake_notes.markdown_h2_headings = lambda _markdown: ()
    monkeypatch.setitem(sys.modules, "cmru", fake_cmru)
    monkeypatch.setitem(sys.modules, "cmru.release_notes", fake_notes)
    missing = tmp_path / "missing-CHANGES.md"
    with pytest.raises(release_candidate.ReleaseCandidateError, match="cannot read prepared changelog"):
        release_candidate.prepared_release_version(missing)


def test_wheel_identity_must_match_both_version_and_digest():
    expected = {"version": "24.0.0", "sha256": "a" * 64}
    release_candidate._require_same_wheel(expected.copy(), expected, "rebuilt wheel")

    with pytest.raises(release_candidate.ReleaseCandidateError, match="wheel SHA-256"):
        release_candidate._require_same_wheel(
            {"version": "24.0.0", "sha256": "b" * 64}, expected, "rebuilt wheel"
        )
    with pytest.raises(release_candidate.ReleaseCandidateError, match="version"):
        release_candidate._require_same_wheel(
            {"version": "23.10.0", "sha256": "a" * 64}, expected, "rebuilt wheel"
        )


def test_wheel_identity_parses_valid_receipt_and_refuses_bad_outputs(monkeypatch):
    identity = {
        "version": "24.0.0",
        "filename": "run_gate-24.0.0-py3-none-any.whl",
        "sha256": "a" * 64,
    }
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(json.dumps(identity)),
    )
    assert release_candidate._wheel_identity() == identity

    cases = [
        (_completed(stderr="wheel scan failed", returncode=2), "cannot identify"),
        (_completed("not-json"), "not JSON"),
        (_completed(json.dumps({"version": "24.0.0"})), "incomplete"),
        (_completed(json.dumps({**identity, "sha256": "bad"})), "wheel SHA-256 is malformed"),
    ]
    for result, message in cases:
        monkeypatch.setattr(
            release_candidate.subprocess, "run", lambda *_args, _r=result, **_kwargs: _r
        )
        with pytest.raises(release_candidate.ReleaseCandidateError, match=message):
            release_candidate._wheel_identity()


def test_image_identity_parses_labels_and_refuses_bad_images(monkeypatch):
    labels = {
        release_candidate.IMAGE_VERSION_LABEL: "24.0.0",
        release_candidate.IMAGE_SHA256_LABEL: "a" * 64,
    }
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(json.dumps(labels)),
    )
    assert release_candidate._image_identity("tester-unified:local") == {
        "version": "24.0.0",
        "sha256": "a" * 64,
    }

    cases = [
        (_completed(stderr="image absent", returncode=1), "cannot inspect tester image"),
        (_completed("not-json"), "malformed labels"),
        (_completed("null"), "no label object"),
        (_completed("{}"), "no Run-Gate version and wheel digest labels"),
    ]
    for result, message in cases:
        monkeypatch.setattr(
            release_candidate.subprocess, "run", lambda *_args, _r=result, **_kwargs: _r
        )
        with pytest.raises(release_candidate.ReleaseCandidateError, match=message):
            release_candidate._image_identity("tester-unified:local")


def test_receipt_requires_a_candidate_commit_and_wheel_digest(tmp_path):
    path = tmp_path / "receipt.json"
    valid = _receipt()
    path.write_text(json.dumps(valid), encoding="utf-8")
    assert release_candidate._read_receipt(path) == valid

    valid["sha256"] = "not-a-digest"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="malformed SHA-256"):
        release_candidate._read_receipt(path)

    valid["sha256"] = "a" * 64
    valid["schema_version"] = True
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt .* is malformed"):
        release_candidate._read_receipt(path)

    valid["schema_version"] = 1
    valid["image_id"] = "sha256:not-an-image-id"
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="malformed image ID"):
        release_candidate._read_receipt(path)

    valid = _receipt(image_tag="")
    path.write_text(json.dumps(valid), encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt .* is malformed"):
        release_candidate._read_receipt(path)

    path.write_text("[]", encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt .* is malformed"):
        release_candidate._read_receipt(path)

    malformed_records = [
        (_receipt(candidate_commit="short"), "malformed commit"),
        (_receipt(version="24.0"), "malformed stable version"),
        (_receipt(filename="../run_gate.whl"), "malformed filename"),
        (_receipt(filename="folder\\run_gate.whl"), "malformed filename"),
    ]
    for record, message in malformed_records:
        path.write_text(json.dumps(record), encoding="utf-8")
        with pytest.raises(release_candidate.ReleaseCandidateError, match=message):
            release_candidate._read_receipt(path)

    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="cannot read Run-Gate wheel receipt"):
        release_candidate._read_receipt(path)

    with pytest.raises(release_candidate.ReleaseCandidateError, match="cannot read Run-Gate wheel receipt"):
        release_candidate._read_receipt(tmp_path / "missing.json")

    path.write_bytes(b"\xff")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="cannot read Run-Gate wheel receipt"):
        release_candidate._read_receipt(path)


def test_receipt_writer_creates_parent_and_atomically_writes_json(tmp_path):
    target = tmp_path / "nested" / "candidate.json"
    record = _receipt()

    release_candidate._write_receipt(record, target)

    assert json.loads(target.read_text(encoding="utf-8")) == record
    assert not target.with_suffix(".json.tmp").exists()


def test_git_reader_returns_output_and_surfaces_command_failure(monkeypatch):
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(" commit-id \n"),
    )
    assert release_candidate._git("rev-parse", "HEAD") == "commit-id"

    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stderr="not a repository", returncode=128),
    )
    with pytest.raises(release_candidate.ReleaseCandidateError, match="git rev-parse HEAD failed"):
        release_candidate._git("rev-parse", "HEAD")


def test_running_image_identity_must_match_receipted_candidate(monkeypatch, tmp_path):
    receipt = _receipt()
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    identity_path = tmp_path / "image-identity.json"
    identity_path.write_text(json.dumps({"version": "24.0.0", "sha256": "a" * 64}), encoding="utf-8")

    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])
    monkeypatch.setattr(release_candidate, "IMAGE_IDENTITY_PATH", identity_path)
    monkeypatch.setattr(release_candidate, "_verify_installed_candidate_wheel", lambda _receipt: None)
    release_candidate.verify_running_image(receipt_path)

    identity_path.write_text(json.dumps({"version": "24.0.0", "sha256": "c" * 64}), encoding="utf-8")
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="running tester image Run-Gate wheel SHA-256",
    ):
        release_candidate.verify_running_image(receipt_path)


def _patch_installed_wheel(monkeypatch, tmp_path, *, installed_override=None, cli_output=None):
    venv = tmp_path / "venv"
    project = tmp_path / "project"
    project.mkdir(parents=True)
    modules = {}
    for name in ("run_gate", "run_gate_admission"):
        source = project / f"{name}.py"
        installed = venv / "lib" / f"{name}.py"
        installed.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(name.encode("ascii"))
        installed.write_bytes(
            installed_override if name == "run_gate" and installed_override is not None
            else name.encode("ascii")
        )
        modules[name] = SimpleNamespace(__file__=str(installed))
    executable = venv / "bin" / "run-gate"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project)
    monkeypatch.setattr(release_candidate.sys, "prefix", str(venv))
    monkeypatch.setattr(release_candidate.importlib, "import_module", lambda name: modules[name])
    monkeypatch.setattr(release_candidate.importlib.metadata, "version", lambda _name: "24.0.0")
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(cli_output or "run-gate 24.0.0\n"),
    )


def test_installed_wheel_verification_checks_code_location_and_cli(monkeypatch, tmp_path):
    _patch_installed_wheel(monkeypatch, tmp_path)
    release_candidate._verify_installed_candidate_wheel(_receipt())


def test_installed_wheel_verification_rejects_module_or_cli_mismatch(monkeypatch, tmp_path):
    _patch_installed_wheel(monkeypatch, tmp_path, installed_override=b"old module")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="differs from the selected"):
        release_candidate._verify_installed_candidate_wheel(_receipt())

    _patch_installed_wheel(monkeypatch, tmp_path / "cli", cli_output="run-gate 23.10.0\n")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="CLI smoke expected"):
        release_candidate._verify_installed_candidate_wheel(_receipt())


def test_installed_wheel_verification_rejects_missing_wrong_version_and_imports(
    monkeypatch, tmp_path
):
    _patch_installed_wheel(monkeypatch, tmp_path)

    def no_distribution(_name):
        raise release_candidate.importlib.metadata.PackageNotFoundError("run-gate")

    monkeypatch.setattr(release_candidate.importlib.metadata, "version", no_distribution)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="no installed Run-Gate distribution",
    ):
        release_candidate._verify_installed_candidate_wheel(_receipt())

    monkeypatch.setattr(
        release_candidate.importlib.metadata,
        "version",
        lambda _name: "23.10.0",
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="does not match candidate",
    ):
        release_candidate._verify_installed_candidate_wheel(_receipt())

    monkeypatch.setattr(
        release_candidate.importlib.metadata,
        "version",
        lambda _name: "24.0.0",
    )
    monkeypatch.setattr(
        release_candidate.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("module missing")),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot import installed Run-Gate module",
    ):
        release_candidate._verify_installed_candidate_wheel(_receipt())


def test_installed_wheel_verification_rejects_modules_outside_tester_venv(
    monkeypatch, tmp_path
):
    _patch_installed_wheel(monkeypatch, tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    import_module = release_candidate.importlib.import_module
    monkeypatch.setattr(
        release_candidate.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(__file__=str(outside))
            if name == "run_gate" else import_module(name)
        ),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="resolved outside tester venv",
    ):
        release_candidate._verify_installed_candidate_wheel(_receipt())


def test_installed_wheel_verification_reports_unreadable_module_bytes(
    monkeypatch, tmp_path
):
    _patch_installed_wheel(monkeypatch, tmp_path)
    source = tmp_path / "project" / "run_gate.py"
    original_read_bytes = Path.read_bytes

    def fail_source_read(path):
        if path == source:
            raise OSError("source bytes unavailable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_source_read)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot compare installed Run-Gate module",
    ):
        release_candidate._verify_installed_candidate_wheel(_receipt())


def test_installed_wheel_verification_requires_an_executable_cli(monkeypatch, tmp_path):
    _patch_installed_wheel(monkeypatch, tmp_path)
    (tmp_path / "venv" / "bin" / "run-gate").unlink()
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="no executable installed CLI",
    ):
        release_candidate._verify_installed_candidate_wheel(_receipt())


def test_running_image_rejects_wrong_commit_bad_identity_and_resolves_relative_receipt(
    monkeypatch, tmp_path
):
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    identity_path = tmp_path / "image-identity.json"
    monkeypatch.setattr(release_candidate, "IMAGE_IDENTITY_PATH", identity_path)
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "2" * 40)
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt is for"):
        release_candidate.verify_running_image(receipt_path)

    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "1" * 40)
    monkeypatch.setattr(release_candidate, "_verify_installed_candidate_wheel", lambda _receipt: None)
    with pytest.raises(release_candidate.ReleaseCandidateError, match="no readable Run-Gate wheel identity"):
        release_candidate.verify_running_image(receipt_path)
    identity_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="no readable Run-Gate wheel identity"):
        release_candidate.verify_running_image(receipt_path)
    identity_path.write_bytes(b"\xff")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="no readable Run-Gate wheel identity"):
        release_candidate.verify_running_image(receipt_path)
    identity_path.write_text("[]", encoding="utf-8")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="identity is malformed"):
        release_candidate.verify_running_image(receipt_path)

    identity_path.write_text(json.dumps({"version": "24.0.0", "sha256": "a" * 64}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    release_candidate.verify_running_image(Path("receipt.json"))


def test_prepare_records_the_exact_wheel_and_tester_image(monkeypatch, tmp_path, capsys):
    wheel = {
        "version": "24.0.0",
        "filename": "run_gate-24.0.0-py3-none-any.whl",
        "sha256": "a" * 64,
    }
    image_id = "sha256:" + "b" * 64
    monkeypatch.setattr(
        release_candidate, "prepared_release_version",
        lambda _path, **_kwargs: "24.0.0",
    )
    monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", "1" * 40)
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *args: "1700000000" if args[0] == "log" else "1" * 40,
    )
    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: wheel.copy())
    monkeypatch.setattr(
        release_candidate,
        "_image_identity",
        lambda _tag: {"version": "24.0.0", "sha256": "a" * 64},
    )
    monkeypatch.setattr(release_candidate, "RECEIPT_PATH", tmp_path / "receipt.json")
    monkeypatch.setattr(
        release_candidate, "_image_cli_extended_sha256",
        lambda _tag: release_candidate._dockerfile_cli_extended_pin(),
    )
    results = iter((_completed(), _completed(), _completed(image_id)))
    monkeypatch.setattr(release_candidate.subprocess, "run", lambda *_args, **_kwargs: next(results))

    record = release_candidate.prepare()

    assert release_candidate._read_receipt(tmp_path / "receipt.json") == {
        **_receipt(),
        "image_tag": "tester-unified:run-gate-candidate-111111111111",
        "image_id": image_id,
    }
    assert record["candidate_commit"] == "1" * 40
    assert "prepared Run-Gate 24.0.0 wheel" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("wheel_version", "image_identity", "image_result", "message"),
    [
        (
            "23.10.0",
            {"version": "24.0.0", "sha256": "a" * 64},
            _completed("sha256:" + "b" * 64),
            "does not match prepared release",
        ),
        (
            "24.0.0",
            {"version": "23.10.0", "sha256": "a" * 64},
            _completed("sha256:" + "b" * 64),
            "tester image Run-Gate version",
        ),
        (
            "24.0.0",
            {"version": "24.0.0", "sha256": "a" * 64},
            _completed(stderr="missing image id", returncode=1),
            "cannot read tester image ID",
        ),
        (
            "24.0.0",
            {"version": "24.0.0", "sha256": "a" * 64},
            _completed("not-an-image-id"),
            "tester image ID is malformed",
        ),
    ],
)
def test_prepare_refuses_mismatched_build_and_image(
    monkeypatch, tmp_path, wheel_version, image_identity, image_result, message
):
    wheel = {
        "version": wheel_version,
        "filename": f"run_gate-{wheel_version}-py3-none-any.whl",
        "sha256": "a" * 64,
    }
    monkeypatch.setattr(
        release_candidate, "prepared_release_version",
        lambda _path, **_kwargs: "24.0.0",
    )
    monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", "1" * 40)
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *args: "1700000000" if args[0] == "log" else "1" * 40,
    )
    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: wheel)
    monkeypatch.setattr(release_candidate, "_image_identity", lambda _tag: image_identity)
    monkeypatch.setattr(release_candidate, "RECEIPT_PATH", tmp_path / "receipt.json")
    monkeypatch.setattr(
        release_candidate, "_image_cli_extended_sha256",
        lambda _tag: release_candidate._dockerfile_cli_extended_pin(),
    )
    results = iter((_completed(), _completed(), image_result))
    monkeypatch.setattr(release_candidate.subprocess, "run", lambda *_args, **_kwargs: next(results))

    with pytest.raises(release_candidate.ReleaseCandidateError, match=message):
        release_candidate.prepare()


@pytest.mark.parametrize(
    ("gate_commit", "message"),
    [
        (None, "requires CMRU's protected release-gate commit"),
        ("2" * 40, "does not match the selected Run-Gate checkout"),
    ],
)
def test_prepare_refuses_stable_candidate_without_matching_cmru_gate_commit(
    monkeypatch, gate_commit, message,
):
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "1" * 40)
    if gate_commit is None:
        monkeypatch.delenv("CMRU_RELEASE_GATE_COMMIT", raising=False)
    else:
        monkeypatch.setenv("CMRU_RELEASE_GATE_COMMIT", gate_commit)

    with pytest.raises(release_candidate.ReleaseCandidateError, match=message):
        release_candidate.prepare()


def test_verify_built_checks_receipt_wheel_image_and_image_id(monkeypatch, tmp_path, capsys):
    receipt = _receipt()
    wheel = {key: receipt[key] for key in ("version", "filename", "sha256")}
    monkeypatch.setattr(release_candidate, "_read_receipt", lambda _path: receipt)
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])
    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: wheel)
    monkeypatch.setattr(
        release_candidate,
        "_image_identity",
        lambda _tag: {"version": receipt["version"], "sha256": receipt["sha256"]},
    )
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(receipt["image_id"]),
    )

    release_candidate.verify_built(receipt_path=tmp_path / "receipt.json")
    assert "matches the gated wheel" in capsys.readouterr().out

    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "2" * 40)
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt is for"):
        release_candidate.verify_built(receipt_path=tmp_path / "receipt.json")
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])

    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: {**wheel, "filename": "other.whl"})
    with pytest.raises(release_candidate.ReleaseCandidateError, match="filename"):
        release_candidate.verify_built(receipt_path=tmp_path / "receipt.json")
    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: wheel)

    results = iter((_completed("different-image-id"), _completed(stderr="inspect failed", returncode=1)))
    monkeypatch.setattr(release_candidate.subprocess, "run", lambda *_args, **_kwargs: next(results))
    with pytest.raises(release_candidate.ReleaseCandidateError, match="changed after its gate"):
        release_candidate.verify_built("tester-unified:alternate", tmp_path / "receipt.json")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="changed after its gate"):
        release_candidate.verify_built(receipt_path=tmp_path / "receipt.json")


def test_main_dispatches_all_commands_and_reports_supported_failures(monkeypatch, capsys, tmp_path):
    calls = []
    monkeypatch.setattr(release_candidate, "prepare", lambda image: calls.append(("prepare", image)))
    monkeypatch.setattr(
        release_candidate,
        "verify_built",
        lambda image, receipt: calls.append(("verify", image, receipt)),
    )
    monkeypatch.setattr(
        release_candidate,
        "verify_running_image",
        lambda receipt: calls.append(("verify-running", receipt)),
    )
    monkeypatch.setattr(
        release_candidate,
        "run_selftest",
        lambda receipt: calls.append(("run-selftest", receipt)) or 0,
    )

    assert release_candidate.main(["prepare"]) == 0
    assert release_candidate.main(["prepare", "--image", "tester-unified:custom"]) == 0
    assert release_candidate.main(["verify"]) == 0
    assert release_candidate.main(
        ["verify", "--image", "tester-unified:custom", "--receipt", str(tmp_path / "receipt")]
    ) == 0
    assert release_candidate.main(["verify-running", "--receipt", str(tmp_path / "receipt")]) == 0
    assert release_candidate.main(["run-selftest", "--receipt", str(tmp_path / "receipt")]) == 0
    assert calls[0] == ("prepare", None)
    assert calls[1] == ("prepare", "tester-unified:custom")
    assert calls[-1] == ("run-selftest", Path(tmp_path / "receipt"))


def test_run_selftest_uses_receipt_specific_image_without_mutating_shared_tag(monkeypatch, tmp_path):
    project_root = tmp_path / "run-gate-project"
    receipt_path = project_root / ".assay" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    wheel_path = project_root / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel_path.parent.mkdir()
    wheel_path.write_bytes(b"exact candidate wheel")
    receipt = _receipt(
        image_tag="tester-unified:run-gate-candidate-deadbeef1234",
        sha256=hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
    )
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])
    monkeypatch.setattr(release_candidate, "_verify_receipt_image", lambda _receipt: None)
    candidate_cli = tmp_path / "candidate-venv" / "bin" / "run-gate"
    candidate_cli.parent.mkdir(parents=True)
    candidate_python = candidate_cli.parent / "python"
    candidate_python.write_text("unused by mocked process", encoding="utf-8")
    candidate_python.chmod(0o755)
    monkeypatch.setattr(
        release_candidate,
        "_candidate_cli",
        lambda wheel, temp_root: candidate_cli,
    )
    calls = []

    def run(argv, *, cwd, env, check):
        calls.append((argv, cwd, env, check))
        return _completed(returncode=7)

    monkeypatch.setattr(release_candidate.subprocess, "run", run)
    assert release_candidate.run_selftest(Path(".assay/receipt.json")) == 7
    argv, cwd, env, check = calls[0]
    assert argv[:2] == [str(candidate_cli.parent / "python"), "-I"]
    assert argv[2] == "-c"
    assert "distribution.locate_file" in argv[3]
    assert "run_gate_admission" in argv[3]
    assert cwd == project_root
    assert env["RUN_GATE_RELEASE_WHEEL_RECEIPT"] == ".assay/receipt.json"
    assert env["RUN_GATE_TESTER_UNIFIED_IMAGE"] == receipt["image_id"]
    assert check is False


def test_run_selftest_ignores_pythonpath_shadow_and_uses_installed_wheel(
    monkeypatch, tmp_path, capfd,
):
    project_root = tmp_path / "run-gate-project"
    receipt_path = project_root / ".assay" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    wheel_path = project_root / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel_path.parent.mkdir()
    wheel_path.write_bytes(b"exact candidate wheel")
    receipt = _receipt(sha256=hashlib.sha256(wheel_path.read_bytes()).hexdigest())
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])
    monkeypatch.setattr(release_candidate, "_verify_receipt_image", lambda _receipt: None)

    candidate_env = tmp_path / "candidate-venv"
    import venv

    venv.EnvBuilder(with_pip=False).create(candidate_env)
    python = candidate_env / "bin" / "python"
    site_packages = candidate_env / "lib" / (
        f"python{sys.version_info.major}.{sys.version_info.minor}"
    ) / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    dist_info = site_packages / "run_gate-24.0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: run-gate\nVersion: 24.0.0\n",
        encoding="utf-8",
    )
    (site_packages / "run_gate.py").write_text(
        "def main(argv):\n"
        "    assert argv == ['selftest']\n"
        "    print('candidate module:', __file__)\n"
        "    return 23\n",
        encoding="utf-8",
    )
    (site_packages / "run_gate_admission.py").write_text("\n", encoding="utf-8")
    files = ["run_gate.py", "run_gate_admission.py"]
    (dist_info / "RECORD").write_text(
        "".join(f"{name},,\n" for name in files), encoding="utf-8",
    )
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
    )
    console_script = candidate_env / "bin" / "run-gate"
    console_script.write_text("unused by isolated invocation\n", encoding="utf-8")
    monkeypatch.setattr(
        release_candidate, "_candidate_cli", lambda _wheel, _root: console_script,
    )

    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "run_gate.py").write_text(
        "raise SystemExit(91)\n", encoding="utf-8",
    )
    (shadow / "run_gate_admission.py").write_text(
        "raise SystemExit(92)\n", encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(shadow))

    assert release_candidate.run_selftest(Path(".assay/receipt.json")) == 23
    output = capfd.readouterr().out
    assert str(site_packages / "run_gate.py") in output


def test_run_selftest_refuses_wrong_commit_and_receipt_outside_checkout(
    monkeypatch, tmp_path
):
    project_root = tmp_path / "project"
    receipt_path = tmp_path / "outside" / "receipt.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(json.dumps(_receipt()), encoding="utf-8")
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: "2" * 40)
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt is for"):
        release_candidate.run_selftest(receipt_path)

    wheel_path = project_root / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel_path.parent.mkdir(parents=True)
    wheel_path.write_bytes(b"candidate wheel")
    receipt = _receipt(sha256=hashlib.sha256(wheel_path.read_bytes()).hexdigest())
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *_args: receipt["candidate_commit"],
    )
    monkeypatch.setattr(
        release_candidate,
        "_verify_receipt_image",
        lambda _receipt: None,
    )
    with pytest.raises(release_candidate.ReleaseCandidateError, match="receipt must be inside"):
        release_candidate.run_selftest(receipt_path)


def test_run_selftest_reports_a_spawn_error(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    receipt_path = project_root / ".assay" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    wheel_path = project_root / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel_path.parent.mkdir()
    wheel_path.write_bytes(b"candidate wheel")
    receipt = _receipt(sha256=hashlib.sha256(wheel_path.read_bytes()).hexdigest())
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *_args: receipt["candidate_commit"],
    )
    monkeypatch.setattr(
        release_candidate,
        "_verify_receipt_image",
        lambda _receipt: None,
    )
    monkeypatch.setattr(
        release_candidate,
        "_candidate_cli",
        lambda _wheel, _temp_root: tmp_path / "candidate-venv" / "bin" / "run-gate",
    )
    candidate_python = tmp_path / "candidate-venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text("unused by mocked process", encoding="utf-8")
    candidate_python.chmod(0o755)
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("exec unavailable")),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot start.*exec unavailable",
    ):
        release_candidate.run_selftest(receipt_path)


@pytest.mark.parametrize("python_state", ["absent", "not-executable"])
def test_run_selftest_refuses_a_candidate_environment_without_executable_python(
    monkeypatch, tmp_path, python_state,
):
    project_root = tmp_path / "project"
    receipt_path = project_root / ".assay" / "receipt.json"
    receipt_path.parent.mkdir(parents=True)
    wheel_path = project_root / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel_path.parent.mkdir()
    wheel_path.write_bytes(b"candidate wheel")
    receipt = _receipt(sha256=hashlib.sha256(wheel_path.read_bytes()).hexdigest())
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(release_candidate, "_git", lambda *_args: receipt["candidate_commit"])
    monkeypatch.setattr(release_candidate, "_verify_receipt_image", lambda _receipt: None)
    candidate_bin = tmp_path / "candidate-venv" / "bin"
    candidate_bin.mkdir(parents=True)
    monkeypatch.setattr(
        release_candidate, "_candidate_cli",
        lambda _wheel, _temp_root: candidate_bin / "run-gate",
    )
    if python_state == "not-executable":
        (candidate_bin / "python").write_text("not runnable", encoding="utf-8")
        (candidate_bin / "python").chmod(0o644)
    monkeypatch.setattr(
        release_candidate.subprocess, "run",
        lambda *_args, **_kwargs: pytest.fail("no selftest may start without Python"),
    )

    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match=f"no executable Python at {re.escape(str(candidate_bin / 'python'))}",
    ):
        release_candidate.run_selftest(receipt_path)


def test_candidate_wheel_path_is_bound_to_the_receipt(monkeypatch, tmp_path):
    project = tmp_path / "run-gate-project"
    wheel = project / "dist" / "run_gate-24.0.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"candidate")
    receipt = _receipt(sha256=hashlib.sha256(b"candidate").hexdigest())
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project)

    assert release_candidate._candidate_wheel_path(receipt) == wheel.resolve()

    wheel.write_bytes(b"changed")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="SHA-256 changed"):
        release_candidate._candidate_wheel_path(receipt)


def test_candidate_wheel_path_refuses_missing_and_escaping_paths(monkeypatch, tmp_path):
    project = tmp_path / "run-gate-project"
    monkeypatch.setattr(release_candidate, "PROJECT_ROOT", project)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot read receipted.*dist",
    ):
        release_candidate._candidate_wheel_path(_receipt())

    wheel_dir = project / "dist"
    wheel_dir.mkdir(parents=True)
    outside = tmp_path / "outside.whl"
    outside.write_bytes(b"wheel outside the project")
    wheel = wheel_dir / "run_gate-24.0.0-py3-none-any.whl"
    wheel.symlink_to(outside)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="not a regular file under",
    ):
        release_candidate._candidate_wheel_path(_receipt())

    wheel.unlink()
    inside_dir = wheel_dir / "not-a-wheel"
    inside_dir.mkdir()
    wheel.symlink_to(inside_dir, target_is_directory=True)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="not a regular file under",
    ):
        release_candidate._candidate_wheel_path(_receipt())

    wheel.unlink()
    wheel.write_bytes(b"candidate wheel")
    original_read_bytes = Path.read_bytes

    def fail_wheel_read(path):
        if path == wheel:
            raise OSError("wheel bytes unavailable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_wheel_read)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot read receipted Run-Gate wheel.*wheel bytes unavailable",
    ):
        release_candidate._candidate_wheel_path(_receipt())


def test_candidate_cli_installs_the_receipted_wheel_offline(monkeypatch, tmp_path):
    wheel = tmp_path / "run_gate-24.0.0-py3-none-any.whl"
    wheel.write_bytes(b"candidate wheel")
    temp_root = tmp_path / "isolated"
    temp_root.mkdir()
    calls = []

    def run(argv, *, cwd, check, capture_output, text):
        calls.append((argv, cwd, check, capture_output, text))
        if argv[1:3] == ["-m", "venv"]:
            executable = Path(argv[3]) / "bin" / "run-gate"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o755)
        return _completed()

    monkeypatch.setattr(release_candidate.subprocess, "run", run)
    result = release_candidate._candidate_cli(wheel, temp_root)

    assert result == temp_root / "candidate-venv" / "bin" / "run-gate"
    assert [call[0][1:3] for call in calls] == [["-m", "venv"], ["-m", "pip"]]
    assert calls[1][0][3:8] == ["install", "--no-deps", "--no-index", "--disable-pip-version-check", str(wheel)]
    assert all(call[1] == temp_root and call[2:] == (False, True, True) for call in calls)


def test_candidate_cli_refuses_creation_install_and_missing_entrypoint_failures(
    monkeypatch, tmp_path
):
    wheel = tmp_path / "run_gate-24.0.0-py3-none-any.whl"
    wheel.write_bytes(b"candidate wheel")
    temp_root = tmp_path / "isolated"
    temp_root.mkdir()

    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(stderr="venv creation failed", returncode=1),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot create.*venv creation failed",
    ):
        release_candidate._candidate_cli(wheel, temp_root)

    calls = 0

    def fail_install(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _completed()
        return _completed(stderr="pip install failed", returncode=1)

    monkeypatch.setattr(release_candidate.subprocess, "run", fail_install)
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="cannot install.*pip install failed",
    ):
        release_candidate._candidate_cli(wheel, temp_root)

    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(),
    )
    with pytest.raises(
        release_candidate.ReleaseCandidateError,
        match="did not install its console script",
    ):
        release_candidate._candidate_cli(wheel, temp_root)


def test_receipt_image_check_requires_the_prepared_image_id(monkeypatch, capsys):
    receipt = _receipt()
    monkeypatch.setattr(
        release_candidate,
        "_image_identity",
        lambda _tag: {"version": receipt["version"], "sha256": receipt["sha256"]},
    )
    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(receipt["image_id"] + "\n"),
    )
    release_candidate._verify_receipt_image(receipt)

    monkeypatch.setattr(
        release_candidate.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed("sha256:" + "c" * 64 + "\n"),
    )
    with pytest.raises(release_candidate.ReleaseCandidateError, match="changed after preparation"):
        release_candidate._verify_receipt_image(receipt)

    def fail_verify(*_args):
        raise OSError("broken receipt")

    monkeypatch.setattr(release_candidate, "verify_built", fail_verify)
    assert release_candidate.main(["verify"]) == 2
    assert "run-gate release candidate: broken receipt" in capsys.readouterr().err


def test_script_entrypoint_exits_with_reported_release_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["release_candidate.py", "verify-running", "--receipt", str(tmp_path / "absent.json")],
    )
    with pytest.raises(SystemExit) as error:
        runpy.run_path(str(TOOLS / "release_candidate.py"), run_name="__main__")
    assert error.value.code == 2


def test_release_image_builder_checks_governed_parent_and_exact_labels(tmp_path):
    builder = (TOOLS.parents[1] / "tester-unified" / "build-image.sh").read_text(encoding="utf-8")
    dockerfile = (TOOLS.parents[1] / "tester-unified" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CGROUP_PARENT_DEV_GATES or --cgroup-parent is required' in builder
    assert 'tester_gate_args=(--cwd run-gate-project --cgroup-parent "$cgroup_parent")' in builder
    assert 'tester_gate_args+=(--image "$base_image")' in builder
    assert 'cmru tester-gate "${tester_gate_args[@]}" /bin/true' in builder
    assert 'docker buildx build --load --cgroup-parent "$cgroup_parent"' in builder
    assert 'org.vbpub.run-gate.wheel-sha256' in builder
    assert 'org.vbpub.run-gate.version' in dockerfile
    assert '/etc/tester-unified-run-gate-wheel.json' in dockerfile


def test_cmru_release_gate_builds_before_testing_and_verifies_after_build():
    cmru = (TOOLS.parents[0] / "cmru.toml").read_text(encoding="utf-8")
    assert cmru.index('"tools/release_candidate.py", "prepare"') < cmru.index(
        '"tools/release_candidate.py", "run-selftest"'
    )
    assert '"tools/release_candidate.py", "verify"' in cmru
