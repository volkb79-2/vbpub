from __future__ import annotations

import subprocess
import tomllib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, release_preconditions

_REAL_CHECK_RELEASE_BUILD_IMAGES = release_preconditions.check_release_build_images_precondition

RUN_GATE_VERSION_SEQUENCE = r'''RUN_GATE_VERSION="$(
  git describe --tags --abbrev=0 --match 'run-gate-v*' 2>/dev/null ||
    awk -F'[][]' '/^## \[[0-9]+\.[0-9]+\.[0-9]+\] - / { print $2; exit }' \
      run-gate-project/CHANGES.md
)"
RUN_GATE_VERSION="${RUN_GATE_VERSION#run-gate-v}"
if [[ ! "$RUN_GATE_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Run-Gate version must come from a run-gate-v* tag or a stable run-gate-project/CHANGES.md heading; got '$RUN_GATE_VERSION'" >&2
  exit 2
fi
export RUN_GATE_VERSION'''


def _gate_preconditions(tmp_path: Path, monkeypatch, *, image_status="PASS", image_detail="labels match"):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    blobs = {
        "docs/BOOTSTRAP-FROM-ZERO.md": RUN_GATE_VERSION_SEQUENCE + "\n",
    }
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )
    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: pytest.fail("the standing release rule consults no per-version gate policy"),
    )
    monkeypatch.setattr(
        release_preconditions,
        "check_run_gate_image_precondition",
        lambda _root, _source_root, _revision, **_kwargs: release_preconditions.GatePrecondition(
            "cmru", "Run-Gate image identity", image_status, image_detail,
            "Rebuild tester-unified:local from the published Run-Gate wheel.",
        ),
    )
    return repo_root


@pytest.mark.parametrize(
    "version", ["5.9.9", "6.1.9", "7.1.4", "7.2.0", "7.4.0", "7.5.0", "8.0.0"],
)
def test_gate_preconditions_hold_no_waiver_window_row_for_any_version(
    tmp_path, monkeypatch, version,
):
    repo_root = _gate_preconditions(tmp_path, monkeypatch)

    rows = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": version},
    )

    assert [row.check for row in rows] == ["Run-Gate image identity", "Bootstrap Run-Gate version"]
    assert [row.status for row in rows] == ["PASS", "PASS"]
    assert not any("window" in f"{row.check} {row.detail} {row.remedy}".lower() for row in rows)


def test_the_waiver_window_precondition_helpers_no_longer_exist():
    assert not hasattr(release_preconditions, "run_tests_uses_provisional_gate")
    assert not hasattr(release_preconditions, "_prepared_version_error")


def test_tester_image_mismatch_is_a_failed_precondition(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    monkeypatch.setattr(
        release_preconditions, "_run_gate_release_identity_preflight",
        lambda *_args: release_preconditions.GatePrecondition(
            "cmru", "Run-Gate image identity", "FAIL",
            "tester-unified image labels do not match the released Run-Gate wheel",
            "Rebuild tester-unified:local with tester-unified/build-image.sh.",
        ),
    )

    row = release_preconditions.check_run_gate_image_precondition(repo_root, repo_root, "HEAD")

    assert row.status == "FAIL"
    assert "labels do not match" in row.detail
    assert "tester-unified/build-image.sh" in row.remedy


def test_image_precondition_is_skipped_when_docker_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: None)

    row = release_preconditions.check_run_gate_image_precondition(tmp_path, tmp_path, "HEAD")

    assert row.status == "SKIPPED"
    assert "Docker" in row.detail


def test_required_run_gate_image_precondition_turns_skip_into_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    monkeypatch.setattr(
        release_preconditions, "_run_gate_release_identity_preflight",
        lambda *_args: release_preconditions.GatePrecondition(
            "cmru", "Run-Gate image identity", "SKIPPED", "Docker daemon is unavailable.",
        ),
    )

    row = release_preconditions.check_run_gate_image_precondition(
        tmp_path, tmp_path, "HEAD", require_docker=True,
    )

    assert row.status == "FAIL"
    assert row.detail == "Docker daemon is unavailable."
    assert row.remedy == "Restore Docker daemon access on this host, then retry the release."


def test_bootstrap_version_check_passes_with_the_single_derivation():
    assert release_preconditions.bootstrap_run_gate_version_error(
        RUN_GATE_VERSION_SEQUENCE,
    ) is None


def test_bootstrap_version_check_rejects_a_literal_pin_even_beside_the_derivation():
    error = release_preconditions.bootstrap_run_gate_version_error(
        RUN_GATE_VERSION_SEQUENCE
        + "\nSETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=24.3.0\n",
    )

    assert error is not None
    assert "literal Run-Gate version" in error


def test_image_precondition_uses_the_run_gate_release_identity_path(tmp_path, monkeypatch):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    observed = {}

    def check(repo_root, source_root, revision):
        observed.update(repo_root=repo_root, source_root=source_root, revision=revision)
        return release_preconditions.GatePrecondition(
            "cmru", "Run-Gate image identity", "PASS",
            "Run-Gate 24.3.0 / wheel sha256 matches tester-unified:local",
        )

    monkeypatch.setattr(release_preconditions, "_run_gate_release_identity_preflight", check)

    row = release_preconditions.check_run_gate_image_precondition(tmp_path, tmp_path, "HEAD")

    assert row.status == "PASS"
    assert observed == {"repo_root": tmp_path, "source_root": tmp_path, "revision": "HEAD"}
    assert "24.3.0" in row.detail


def test_gate_preconditions_are_empty_when_no_cmru_version_is_planned(tmp_path):
    assert release_preconditions.evaluate_release_gate_preconditions(
        tmp_path, tmp_path, "HEAD", {},
    ) == []


def test_release_gate_loader_compiles_the_source_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, _path: "VALUE = 42\n",
    )

    module = release_preconditions._load_release_gate(tmp_path, "a" * 40)

    assert module.VALUE == 42
    assert module.__file__.endswith("cmru/tools/run_release_gate.py")


@pytest.mark.parametrize(
    "returncode, stdout, stderr, expected",
    [
        (0, "file content\n", "", "file content\n"),
        (1, "", "missing path\n", "cannot read docs/file.md from release source aaaaaaaaaaaa: missing path"),
    ],
)
def test_git_blob_reader_returns_content_or_names_the_failure(
    monkeypatch, tmp_path, returncode, stdout, stderr, expected,
):
    monkeypatch.setattr(
        release_preconditions.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr,
        ),
    )

    if returncode == 0:
        assert release_preconditions._git_blob_text(tmp_path, "a" * 40, "docs/file.md") == expected
    else:
        with pytest.raises(RuntimeError, match="cannot read docs/file.md") as caught:
            release_preconditions._git_blob_text(tmp_path, "a" * 40, "docs/file.md")
        assert expected in str(caught.value)


@pytest.mark.parametrize(
    "detail",
    [
        "Cannot connect to the Docker daemon",
        "permission denied while trying to connect to the Docker daemon",
        "context not found",
    ],
)
def test_docker_transport_errors_are_classified_as_unavailable(detail):
    assert release_preconditions._is_docker_unavailable(detail)


def test_other_preflight_errors_are_not_docker_unavailability():
    assert not release_preconditions._is_docker_unavailable("image labels do not match")


@pytest.mark.parametrize(
    "config, environment, expected",
    [
        (
            '[environments.tester-unified]\nimage = "tester-unified:local"\n',
            {},
            "tester-unified:local",
        ),
        (
            '[environments.tester-unified]\nimage = "tester-unified:local"\n'
            'image_env = "RUN_GATE_IMAGE"\n',
            {"RUN_GATE_IMAGE": "tester-unified:review"},
            "tester-unified:review",
        ),
        (
            '[environments.tester-unified]\nimage = "tester-unified:local"\n'
            'image_env = "RUN_GATE_IMAGE"\n',
            {},
            "tester-unified:local",
        ),
    ],
)
def test_selected_tester_image_reads_the_release_source_and_override(
    monkeypatch, tmp_path, config, environment, expected,
):
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda *_args: config,
    )
    for name in ("RUN_GATE_IMAGE",):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert release_preconditions._selected_tester_image_from_source(tmp_path, "HEAD") == expected


def test_selected_tester_image_rejects_malformed_or_missing_selection(monkeypatch, tmp_path):
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text", lambda *_args: "[environments]\n",
    )
    with pytest.raises(RuntimeError, match="cannot resolve tester-unified image"):
        release_preconditions._selected_tester_image_from_source(tmp_path, "HEAD")
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda *_args: '[environments.tester-unified]\nimage = ""\n',
    )
    with pytest.raises(RuntimeError, match="image is missing"):
        release_preconditions._selected_tester_image_from_source(tmp_path, "HEAD")
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda *_args: '[environments.tester-unified]\nimage = "tag"\nimage_env = 7\n',
    )
    with pytest.raises(RuntimeError, match="image_env is malformed"):
        release_preconditions._selected_tester_image_from_source(tmp_path, "HEAD")
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda *_args: '[environments.tester-unified]\nimage = "tag"\nimage_env = "RUN_GATE_IMAGE"\n',
    )
    monkeypatch.setenv("RUN_GATE_IMAGE", "")
    with pytest.raises(RuntimeError, match=r"override \$RUN_GATE_IMAGE is empty"):
        release_preconditions._selected_tester_image_from_source(tmp_path, "HEAD")
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text", lambda *_args: "not toml = [",
    )
    with pytest.raises(tomllib.TOMLDecodeError):
        release_preconditions._selected_tester_image_from_source(tmp_path, "HEAD")


def _mock_gate_identity_config(monkeypatch, *, image="tester-unified:local"):
    blobs = {
        "cmru.orchestration.toml": (
            "[orchestration.project.cmru]\nconfig = 'cmru/cmru.toml'\n"
        ),
        "run-gate.root.toml": (
            "[environments.tester-unified]\n"
            f'image = "{image}"\n'
        ),
        "cmru/cmru.toml": "[project]\nid = 'cmru'\n",
    }
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )


@pytest.mark.parametrize(
    "orchestration, message",
    [
        ("not toml = [", "cannot resolve projects"),
        ("[orchestration]\n", "cannot resolve projects"),
        ("[orchestration]\nproject = []\n", "no project configs"),
        ("[orchestration]\nproject = { cmru = 1 }\n", "project 'cmru' is malformed"),
        (
            "[orchestration]\nproject = { cmru = { config = '' } }\n",
            "project 'cmru' has no config path",
        ),
    ],
)
def test_gate_identity_config_rejects_unreadable_or_incomplete_orchestration(
    monkeypatch, tmp_path, orchestration, message,
):
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, path: (
            orchestration if path == "cmru.orchestration.toml" else ""
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        release_preconditions._materialize_gate_identity_config(
            tmp_path, "HEAD", tmp_path / "identity",
        )


@pytest.mark.parametrize("config_path", ["/outside/cmru.toml", "../cmru.toml", "cmru.txt"])
def test_gate_identity_config_rejects_unsafe_project_paths(
    monkeypatch, tmp_path, config_path,
):
    blobs = {
        "cmru.orchestration.toml": (
            "[orchestration.project.cmru]\n" f"config = '{config_path}'\n"
        ),
        "run-gate.root.toml": "[environments.tester-unified]\nimage = 'tag'\n",
    }
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )

    with pytest.raises(RuntimeError, match="unsafe config path"):
        release_preconditions._materialize_gate_identity_config(
            tmp_path, "HEAD", tmp_path / "identity",
        )


def test_gate_identity_config_materializes_only_source_revision_configs(
    monkeypatch, tmp_path,
):
    _mock_gate_identity_config(monkeypatch)
    destination = tmp_path / "identity"
    destination.mkdir()

    release_preconditions._materialize_gate_identity_config(tmp_path, "source", destination)

    assert (destination / "cmru.orchestration.toml").is_file()
    assert (destination / "run-gate.root.toml").is_file()
    assert (destination / "cmru/cmru.toml").read_text("utf-8") == "[project]\nid = 'cmru'\n"
    assert (destination / "cmru").is_dir()


def test_release_gate_identity_preflight_uses_the_current_gate_composition(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)
    observed = {"current_calls": 0}

    def current(_root):
        observed["current_calls"] += 1
        observed["source_image"] = release_preconditions.tomllib.loads(
            (_root / "run-gate.root.toml").read_text("utf-8")
        )["environments"]["tester-unified"]["image"]
        observed["orchestration"] = (_root / "cmru.orchestration.toml").read_text("utf-8")
        return {
            "version": "24.3.0",
            "published_wheel_sha256": "a" * 64,
            "tester_image": "tester-unified:local",
        }

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "PASS"
    assert "24.3.0" in row.detail
    assert "a" * 64 in row.detail
    assert observed == {
        "current_calls": 1,
        "source_image": "tester-unified:local",
        "orchestration": "[orchestration.project.cmru]\nconfig = 'cmru/cmru.toml'\n",
    }


def test_release_gate_identity_preflight_preserves_version_for_later_errors(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)

    class BrokenIdentity(dict):
        def get(self, key, default=None):
            if key == "tester_image":
                raise RuntimeError("identity response was malformed")
            return super().get(key, default)

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(
            _current_run_gate_identity=lambda _root: BrokenIdentity(version="24.3.0"),
        ),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "Resolve the Run-Gate preflight error" in row.remedy


def test_release_gate_identity_preflight_recovers_image_from_gate_diagnostic(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        release_preconditions, "_selected_tester_image_from_source",
        lambda *_args: (_ for _ in ()).throw(
            RuntimeError("tester-unified image 'tester-unified:from-gate' is invalid")
        ),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "tester-unified:from-gate" in row.detail
    assert "Resolve the Run-Gate preflight error" in row.remedy


def test_release_gate_identity_preflight_reports_source_config_remedy(tmp_path, monkeypatch):
    blobs = {
        "cmru.orchestration.toml": (
            "[orchestration.project.cmru]\nconfig = 'cmru/cmru.toml'\n"
        ),
        "run-gate.root.toml": "[environments]\n",
        "cmru/cmru.toml": "[project]\nid = 'cmru'\n",
    }
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "run-gate.root.toml" in row.detail
    assert "Repair the Run-Gate or CMRU orchestration config" in row.remedy


def test_release_gate_identity_preflight_calls_the_current_gate_without_extra_arguments(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)
    def current(_root):
        return {
            "version": "24.3.0",
            "published_wheel_sha256": "b" * 64,
            "tester_image": "tester-unified:legacy",
        }

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "PASS"
    assert "tester-unified:legacy" in row.detail


def test_release_gate_identity_preflight_builds_a_precise_image_remedy(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)

    def current(_root):
        raise RuntimeError(
            "tester-unified image does not contain the published wheel: expected "
            "version='24.3.0', sha256='" + "a" * 64 + "'"
        )

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "tester-unified/build-image.sh --run-gate-published 24.3.0" in row.remedy
    assert "--tag tester-unified:local" in row.remedy


def test_release_gate_identity_preflight_keeps_snapshot_selected_image_in_remedy(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch, image="tester-unified:from-source")

    def current(_root):
        raise RuntimeError(
            "tester-unified image labels do not match the published wheel: expected "
            "version='24.3.0', sha256='" + "b" * 64 + "'"
        )

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "--tag tester-unified:from-source" in row.remedy


def test_release_gate_identity_preflight_skips_a_docker_daemon_failure(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)
    def current(_root):
        raise RuntimeError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "SKIPPED"
    assert "Docker is unavailable" in row.detail


def test_release_gate_identity_preflight_reports_published_release_remedy(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)
    def current(_root):
        raise RuntimeError("cannot resolve the published Run-Gate release")

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "Restore access to the published Run-Gate release" in row.remedy


def test_release_gate_identity_preflight_reports_missing_identity_remedy(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)
    def current(_root):
        raise RuntimeError("the run-gate command is missing")

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(
            _current_run_gate_identity=current,
        ),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "Install a stable, non-editable Run-Gate release wheel" in row.remedy


def test_release_gate_identity_preflight_uses_generic_remedy_for_other_failures(tmp_path, monkeypatch):
    _mock_gate_identity_config(monkeypatch)
    def current(_root):
        raise RuntimeError("published asset checksum is malformed")

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: SimpleNamespace(_current_run_gate_identity=current),
    )

    row = release_preconditions._run_gate_release_identity_preflight(
        tmp_path, tmp_path, "HEAD",
    )

    assert row.status == "FAIL"
    assert "Resolve the Run-Gate preflight error" in row.remedy


@pytest.mark.parametrize(
    "bootstrap, message",
    [
        ("", "exactly one documented RUN_GATE_VERSION derivation"),
        (RUN_GATE_VERSION_SEQUENCE + "\n" + RUN_GATE_VERSION_SEQUENCE, "exactly one documented"),
        ("Run-Gate 24.3.0\n", "literal Run-Gate version"),
    ],
)
def test_bootstrap_version_check_rejects_missing_duplicate_or_literal_versions(
    bootstrap, message,
):
    error = release_preconditions.bootstrap_run_gate_version_error(bootstrap)

    assert error is not None and message in error


def test_bootstrap_version_precondition_returns_actionable_literal_version_remedy(
    monkeypatch, tmp_path,
):
    blobs = {
        "docs/BOOTSTRAP-FROM-ZERO.md": (
            RUN_GATE_VERSION_SEQUENCE
            + "\nSETUPTOOLS_SCM_PRETEND_VERSION_FOR_RUN_GATE=24.2.0\n"
        ),
    }
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda _root, _revision, path: blobs[path],
    )

    row = release_preconditions._bootstrap_version_precondition(tmp_path, "HEAD")

    assert row.status == "FAIL"
    assert "literal Run-Gate version(s) 24.2.0" in row.detail
    assert "replace every literal Run-Gate release version" in row.remedy


def test_bootstrap_version_precondition_reports_file_read_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("blob not found")),
    )

    row = release_preconditions._bootstrap_version_precondition(tmp_path, "HEAD")

    assert row.status == "FAIL"
    assert "blob not found" in row.detail
    assert "Restore a readable bootstrap" in row.remedy


def test_release_gate_report_includes_each_failure_remedy():
    rows = [
        release_preconditions.GatePrecondition("cmru", "image", "FAIL", "labels differ", "rebuild image"),
        release_preconditions.GatePrecondition("cmru", "docs", "PASS", "versions match"),
    ]

    report = release_preconditions.format_gate_precondition_report(rows)

    assert "FAIL" in report
    assert "PASS" in report
    assert "Remedy: rebuild image" in report


def _release_precondition_args(**updates):
    values = {
        "dry_run": True,
        "resume": None,
        "from_worktree": False,
        "minor": True,
        "major": False,
        "set_version": None,
        "skip_gates": None,
        "_cmru_process_build_image_env": {},
    }
    values.update(updates)
    return SimpleNamespace(**values)


_CMRU_TOML_RELEASE_GATE = (
    '[steps.run-tests]\n'
    'commands = [{ label = "gate", argv = ["run-gate", "release-gate"], cwd = "." }]\n'
)
_CMRU_TOML_FULL = _CMRU_TOML_RELEASE_GATE.replace("release-gate", "gate")


_CMRU_TOML_ENABLE_DOCKER = (
    '[steps.run-tests]\n'
    'commands = [\n'
    '  { label = "unit", argv = ["cmru", "tester-gate", "--", "pytest"], cwd = "." },\n'
    '  { label = "enroll", argv = ["cmru", "tester-gate", "--enable-docker", "--", "bash"], cwd = "." },\n'
    ']\n'
)


def _mock_cli_release_preconditions(monkeypatch, tmp_path, *, changed=True):
    project = SimpleNamespace(
        name="cmru", project_root=tmp_path / "cmru", prefix="cmru-v",
        paths=["cmru"], cwd="cmru",
        env={
            "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:test",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "a" * 64,
        },
    )
    configs = {"cmru": project}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda _root, projects: {tmp_path: list(projects)},
    )
    monkeypatch.setattr(
        cli, "_configs_for_git_family", lambda _configs, _names, _root: configs,
    )
    monkeypatch.setattr(
        cli, "_project_config_for_release_snapshot",
        lambda *_args: project,
    )
    monkeypatch.setattr(
        cli, "_orchestration_env_for_release_snapshot", lambda *_args: {},
    )
    monkeypatch.setattr(
        cli, "_known_project_scope_names", lambda _configs: frozenset({"cmru"}),
    )
    monkeypatch.setattr(
        "cmru.version.status_records",
        lambda *_args, **_kwargs: [{
            "changed": changed,
            "next_version": "cmru-v7.1.4" if changed else None,
        }],
    )
    monkeypatch.setattr(
        cli.transaction, "fetch_origin_main", lambda *_args, **_kwargs: "b" * 40,
    )
    monkeypatch.setattr(
        release_preconditions, "check_release_build_images_precondition",
        lambda _env, *, process_env=None, require_docker: [
            release_preconditions.GatePrecondition("cmru", "Wheel-builder image", "PASS", "present"),
            release_preconditions.GatePrecondition("cmru", "Cgroup probe image", "PASS", "present"),
        ],
    )
    return configs


def test_orchestration_image_defaults_are_read_from_the_release_source_snapshot(
    tmp_path, monkeypatch,
):
    config_path = tmp_path / "cmru.orchestration.toml"
    observed = {}
    content = (
        "[orchestration.defaults.env]\n"
        'CMRU_WHEEL_BUILDER_IMAGE = "wheel-builder:source"\n'
    )

    def read_snapshot(root, revision, relative_path, *, source_label):
        observed.update(root=root, revision=revision, path=relative_path, label=source_label)
        return content

    monkeypatch.setattr(cli, "_read_git_path_at_commit", read_snapshot)
    values = cli._orchestration_env_for_release_snapshot(
        tmp_path, "a" * 40, config_path,
    )

    assert values == {"CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:source"}
    assert observed["root"] == tmp_path
    assert observed["revision"] == "a" * 40
    assert observed["path"] == Path("cmru.orchestration.toml")


def test_orchestration_image_defaults_refuse_config_outside_release_source(tmp_path):
    with pytest.raises(RuntimeError, match="outside the release source"):
        cli._orchestration_env_for_release_snapshot(
            tmp_path, "a" * 40, tmp_path.parent / "cmru.orchestration.toml",
        )


def test_orchestration_image_defaults_refuse_malformed_source_config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "_read_git_path_at_commit", lambda *_args, **_kwargs: "[broken\n",
    )
    with pytest.raises(RuntimeError, match="cannot resolve image defaults"):
        cli._orchestration_env_for_release_snapshot(
            tmp_path, "a" * 40, tmp_path / "cmru.orchestration.toml",
        )


def test_project_config_snapshot_has_no_orchestration_image_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "_read_git_path_at_commit",
        lambda *_args, **_kwargs: pytest.fail("standalone config has no orchestration defaults"),
    )
    assert cli._orchestration_env_for_release_snapshot(
        tmp_path, "a" * 40, tmp_path / "cmru.toml",
    ) == {}


def test_release_process_image_overrides_are_captured_before_config_application(monkeypatch):
    monkeypatch.setenv("CMRU_WHEEL_BUILDER_IMAGE", "wheel-builder:invocation")
    monkeypatch.delenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", raising=False)
    monkeypatch.setenv("CMRU_TESTER_DIND_IMAGE", "docker@sha256:" + "e" * 64)
    monkeypatch.setenv("UNRELATED_SETTING", "ignored")

    assert cli._release_process_build_image_env() == {
        "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:invocation",
        "CMRU_TESTER_DIND_IMAGE": "docker@sha256:" + "e" * 64,
    }


@pytest.mark.parametrize(
    ("dry_run", "action"),
    [(True, "dry-run"), (False, "release")],
)
def test_release_source_change_message_names_the_invocation_to_rerun(dry_run, action):
    message = cli._release_source_changed_retry_message(dry_run=dry_run)

    assert "release source changed" in message
    assert f"rerun the {action} against one consistent source snapshot" in message


def test_release_precondition_uses_source_snapshot_image_not_stale_caller_config(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    caller_probe = "probe@sha256:" + "a" * 64
    source_probe = "probe@sha256:" + "b" * 64
    source_project = SimpleNamespace(
        name="cmru", env={"CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:source"},
    )
    configs["cmru"].env = {
        "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:caller",
        "CMRU_TESTER_CGROUP_PROBE_IMAGE": caller_probe,
    }
    monkeypatch.setattr(
        cli, "_project_config_for_release_snapshot", lambda *_args: source_project,
    )
    monkeypatch.setattr(
        cli, "_orchestration_env_for_release_snapshot",
        lambda *_args: {"CMRU_TESTER_CGROUP_PROBE_IMAGE": source_probe},
    )
    monkeypatch.setattr(
        release_preconditions, "check_release_build_images_precondition",
        _REAL_CHECK_RELEASE_BUILD_IMAGES,
    )
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    calls = []

    def fake_docker(argv, **_kwargs):
        calls.append(argv)
        if argv[-1] == source_probe:
            return SimpleNamespace(
                returncode=1, stdout="", stderr=f"No such image: {source_probe}",
            )
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_docker)
    args = _release_precondition_args(
        dry_run=False,
        _cmru_process_build_image_env={
            "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:ambient",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": caller_probe,
        },
    )
    with pytest.raises(SystemExit) as caught:
        cli._release_gate_preconditions_before_dispatch(
            tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
            args, git_auth=None, report=False,
        )

    assert caught.value.code == 4
    assert calls == [
        ["docker", "image", "inspect", "wheel-builder:source"],
        ["docker", "image", "inspect", source_probe],
    ]
    assert caller_probe not in repr(calls)
    assert source_probe in capsys.readouterr().err


@pytest.mark.parametrize("missing_probe", [False, True])
def test_release_build_image_precondition_uses_fake_docker_and_exact_pull_remedy(
    monkeypatch, missing_probe,
):
    builder = "wheel-builder:test"
    probe = "probe@sha256:" + "a" * 64
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if missing_probe and argv[-1] == probe:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"Error: No such image: {probe}")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = release_preconditions.check_release_build_images_precondition(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": builder,
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
        process_env={},
        require_docker=True,
    )

    assert calls == [
        ["docker", "image", "inspect", builder],
        ["docker", "image", "inspect", probe],
    ]
    assert next(row for row in rows if row.check == "Wheel-builder image").status == "PASS"
    probe_row = next(row for row in rows if row.check == "Cgroup probe image")
    if missing_probe:
        assert probe_row.status == "FAIL"
        assert "CMRU_TESTER_CGROUP_PROBE_IMAGE" in probe_row.detail
        assert probe in probe_row.detail
        assert probe_row.remedy == f"Run `docker pull {probe}` on this host, then retry the release."
    else:
        assert probe_row.status == "PASS"


def test_shared_build_image_resolver_rejects_an_unknown_context():
    from cmru.build_images import resolve_environment_value

    with pytest.raises(ValueError, match="unsupported environment resolution context"):
        resolve_environment_value(
            "CMRU_WHEEL_BUILDER_IMAGE",
            context="unknown",
            configured_env={},
            process_env={},
        )


def test_release_build_image_precondition_uses_config_over_snapshotted_process_environment(
    monkeypatch,
):
    configured = {
        "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:config",
        "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "a" * 64,
    }
    process = {
        "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:process",
        "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "b" * 64,
    }
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(
        configured, process_env=process, require_docker=True,
    )

    assert calls == [
        ["docker", "image", "inspect", configured["CMRU_WHEEL_BUILDER_IMAGE"]],
        ["docker", "image", "inspect", configured["CMRU_TESTER_CGROUP_PROBE_IMAGE"]],
    ]
    assert [row.status for row in rows] == ["PASS", "PASS"]


@pytest.mark.parametrize(
    "configured_builder",
    [{}, {"CMRU_WHEEL_BUILDER_IMAGE": None}],
    ids=["key-absent", "none-value"],
)
def test_release_build_image_precondition_falls_back_to_snapshotted_environment(
    monkeypatch, configured_builder,
):
    process_builder = "wheel-builder:ambient"
    probe = "probe@sha256:" + "f" * 64
    configured = {**configured_builder, "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe}
    process = {
        "CMRU_WHEEL_BUILDER_IMAGE": process_builder,
        "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "0" * 64,
    }
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(configured, process_env=process, require_docker=True)

    assert calls == [
        ["docker", "image", "inspect", process_builder],
        ["docker", "image", "inspect", probe],
    ]
    assert [row.status for row in rows] == ["PASS", "PASS"]


def test_release_build_image_precondition_empty_config_masks_snapshotted_environment(
    monkeypatch,
):
    probe = "probe@sha256:" + "9" * 64
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": "",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
        process_env={
            "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:ambient",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "8" * 64,
        },
        require_docker=True,
    )

    assert [row.status for row in rows] == ["FAIL", "PASS"]
    assert "CMRU_WHEEL_BUILDER_IMAGE" in rows[0].detail
    assert "wheel-builder:ambient" not in repr(calls)
    assert calls == [["docker", "image", "inspect", probe]]


def test_release_build_image_precondition_pulls_a_missing_builder_but_keeps_probe_local(
    monkeypatch,
):
    builder = "wheel-builder:pullable"
    probe = "probe@sha256:" + "c" * 64
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv == ["docker", "image", "inspect", builder]:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"No such image: {builder}")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": builder,
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
        process_env={}, require_docker=True,
    )

    assert calls == [
        ["docker", "image", "inspect", builder],
        ["docker", "pull", builder],
        ["docker", "image", "inspect", probe],
    ]
    assert [row.status for row in rows] == ["PASS", "PASS"]
    assert "docker pull` succeeded" in rows[0].detail
    assert "pulls a missing builder image by default" in rows[0].detail


def test_release_builder_pull_times_out_with_a_clear_refusal(monkeypatch):
    builder = "wheel-builder:slow-registry"
    probe = "probe@sha256:" + "c" * 64
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv == ["docker", "image", "inspect", builder]:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"No such image: {builder}")
        if argv == ["docker", "pull", builder]:
            raise subprocess.TimeoutExpired(argv, timeout=kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": builder,
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
        process_env={}, require_docker=True,
    )

    assert [row.status for row in rows] == ["FAIL", "PASS"]
    pull_argv, pull_kwargs = calls[1]
    assert pull_argv == ["docker", "pull", builder]
    assert pull_kwargs["timeout"] == 600
    assert "timed out after 600 seconds" in rows[0].detail
    assert rows[0].remedy == f"Run `docker pull {builder}` on this host, then retry the release."


@pytest.mark.parametrize("pull_failure", ["exit", "exception"])
def test_release_build_image_precondition_fails_when_missing_builder_cannot_be_pulled(
    monkeypatch, pull_failure,
):
    builder = "wheel-builder:unavailable"
    probe = "probe@sha256:" + "e" * 64
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv == ["docker", "image", "inspect", builder]:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"No such image: {builder}")
        if argv == ["docker", "pull", builder]:
            if pull_failure == "exception":
                raise OSError("registry connection refused")
            return SimpleNamespace(returncode=1, stdout="", stderr="unauthorized")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": builder,
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
        process_env={}, require_docker=True,
    )

    assert [row.status for row in rows] == ["FAIL", "PASS"]
    assert calls[:2] == [
        ["docker", "image", "inspect", builder],
        ["docker", "pull", builder],
    ]
    assert builder in rows[0].detail
    assert rows[0].remedy == f"Run `docker pull {builder}` on this host, then retry the release."
    if pull_failure == "exception":
        assert "could not pull it" in rows[0].detail
    else:
        assert "pull failed" in rows[0].detail


def test_release_build_image_precondition_dry_run_reports_unverified_builder_pullability(
    monkeypatch,
):
    builder = "wheel-builder:maybe-pullable"
    probe = "probe@sha256:" + "d" * 64
    calls = []
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv == ["docker", "image", "inspect", builder]:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"No such image: {builder}")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": builder,
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
        process_env={}, require_docker=False,
    )

    assert calls == [
        ["docker", "image", "inspect", builder],
        ["docker", "image", "inspect", probe],
    ]
    assert [row.status for row in rows] == ["SKIPPED", "PASS"]
    assert "did not verify registry availability" in rows[0].detail


@pytest.mark.parametrize(
    "require_docker, expected",
    [(False, "SKIPPED"), (True, "FAIL")],
)
def test_release_build_image_precondition_handles_unavailable_docker(
    monkeypatch, require_docker, expected,
):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: None)
    rows = release_preconditions.check_release_build_images_precondition(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:test",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "a" * 64,
        },
        process_env={},
        require_docker=require_docker,
    )

    assert [row.status for row in rows] == [expected, expected]


def test_release_build_image_precondition_refuses_missing_declared_values():
    rows = _REAL_CHECK_RELEASE_BUILD_IMAGES({}, process_env={})

    assert [row.status for row in rows] == ["FAIL", "FAIL"]
    assert "CMRU_WHEEL_BUILDER_IMAGE has no non-empty value" in rows[0].detail
    assert "CMRU_TESTER_CGROUP_PROBE_IMAGE has no non-empty value" in rows[1].detail
    assert "Set a non-empty CMRU_WHEEL_BUILDER_IMAGE" in rows[0].remedy


def test_release_build_image_precondition_skips_daemon_exception_when_optional(
    monkeypatch,
):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def daemon_unavailable(*_args, **_kwargs):
        raise OSError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(release_preconditions.subprocess, "run", daemon_unavailable)
    rows = release_preconditions.check_release_build_images_precondition(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": "builder:test",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe:test",
        },
        process_env={},
    )

    assert [row.status for row in rows] == ["SKIPPED", "SKIPPED"]
    assert all("Docker is unavailable" in row.detail for row in rows)


def test_release_build_image_precondition_skips_nonzero_daemon_response_when_optional(
    monkeypatch,
):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    monkeypatch.setattr(
        release_preconditions.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Cannot connect to the Docker daemon",
        ),
    )

    rows = release_preconditions.check_release_build_images_precondition(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": "builder:test",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe:test",
        },
        process_env={},
    )

    assert [row.status for row in rows] == ["SKIPPED", "SKIPPED"]
    assert all("Docker is unavailable" in row.detail for row in rows)


def test_release_build_image_precondition_fails_on_generic_inspect_exceptions(
    monkeypatch,
):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def docker_cannot_start(*_args, **_kwargs):
        raise OSError("permission denied starting docker")

    monkeypatch.setattr(release_preconditions.subprocess, "run", docker_cannot_start)
    rows = release_preconditions.check_release_build_images_precondition(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": "builder:test",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe:test",
        },
        process_env={},
    )

    assert [row.status for row in rows] == ["FAIL", "FAIL"]
    assert "permission denied starting docker" in rows[0].detail
    assert "Restore Docker daemon access" in rows[0].remedy


def test_release_build_image_precondition_fails_on_generic_inspect_results(monkeypatch):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")
    monkeypatch.setattr(
        release_preconditions.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="permission denied while reading image metadata",
        ),
    )
    rows = release_preconditions.check_release_build_images_precondition(
        {
            "CMRU_WHEEL_BUILDER_IMAGE": "builder:test",
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe:test",
        },
        process_env={},
    )

    assert [row.status for row in rows] == ["FAIL", "FAIL"]
    assert "permission denied while reading image metadata" in rows[0].detail
    assert "Restore Docker daemon access" in rows[0].remedy


_DIND_IMAGE = "docker@sha256:" + "d" * 64
_DIND_ENV = {
    "CMRU_WHEEL_BUILDER_IMAGE": "wheel-builder:test",
    "CMRU_TESTER_CGROUP_PROBE_IMAGE": "probe@sha256:" + "a" * 64,
    "CMRU_TESTER_DIND_IMAGE": _DIND_IMAGE,
}


def _dind_row(rows):
    return next((row for row in rows if row.check == "DinD sidecar image"), None)


def _fake_docker_missing(monkeypatch, missing, calls):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker")

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        if argv[-1] == missing:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"Error: No such image: {missing}")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_run)


@pytest.mark.parametrize(
    ("config_text", "expected"),
    [
        (_CMRU_TOML_ENABLE_DOCKER, True),
        (_CMRU_TOML_FULL, False),
        # Only an exact argv element counts; the flag inside a shell string does not.
        (
            '[steps.run-tests]\n'
            'commands = [{ label = "x", argv = ["bash", "-c", "echo --enable-docker"], cwd = "." }]\n',
            False,
        ),
        # Any step counts, not just run-tests.
        (
            '[steps.run-tests]\n'
            'commands = [{ label = "x", argv = ["true"], cwd = "." }]\n'
            '[steps.build]\n'
            'commands = [{ label = "y", argv = ["cmru", "tester-gate", "--enable-docker"], cwd = "." }]\n',
            True,
        ),
        ('[project]\nid = "x"\n', False),
        # Shape robustness: nothing below may raise, and none of it counts.
        ('steps = "--enable-docker"\n', False),
        ('steps = ["--enable-docker"]\n', False),
        ('[steps]\nrun-tests = "--enable-docker"\n', False),
        ('[steps.run-tests]\ncommands = "--enable-docker"\n', False),
        ('[steps.run-tests]\ncommands = ["--enable-docker"]\n', False),
        ('[steps.run-tests]\ncommands = [{ label = "x", argv = "cmru tester-gate --enable-docker" }]\n', False),
        ('[steps.run-tests]\ncommands = [{ label = "x", argv = [1, true, "--enable-docker"] }]\n', False),
        ('[steps.run-tests]\ncommands = [{ label = "x" }]\n', False),
        # A bad step does not hide a good one.
        (
            '[steps]\nbad = "x"\n'
            '[steps.good]\ncommands = [{ argv = ["--enable-docker"] }]\n',
            True,
        ),
    ],
    ids=[
        "enable-docker", "no-flag", "flag-in-shell-string", "other-step", "no-steps",
        "steps-string", "steps-list", "step-string", "commands-string", "command-string",
        "argv-string", "argv-mixed-types", "no-argv", "bad-step-then-good",
    ],
)
def test_release_config_enable_docker_detection_reads_the_toml_argv(config_text, expected):
    assert release_preconditions.release_config_enables_docker(config_text) is expected


def test_dind_image_row_fails_naming_variable_image_and_remedy_when_absent(monkeypatch):
    calls = []
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, calls)

    row = release_preconditions.check_dind_image_precondition(
        _DIND_ENV, process_env={}, require_docker=True,
    )

    assert row.check == "DinD sidecar image"
    assert row.project == "cmru"
    assert calls == [["docker", "image", "inspect", _DIND_IMAGE]]
    assert row.status == "FAIL"
    assert row.detail == (
        f"CMRU_TESTER_DIND_IMAGE image {_DIND_IMAGE!r} is not present on this Docker host. "
        "The DinD sidecar is started with --pull=never, so this reference must already be local."
    )
    assert row.remedy == f"Run `docker pull {_DIND_IMAGE}` on this host, then retry the release."
    assert all(call[1] != "pull" for call in calls)  # the sidecar is --pull=never: never pulled here


def test_dind_image_row_passes_when_the_image_is_present(monkeypatch):
    calls = []
    _fake_docker_missing(monkeypatch, "not-any-image", calls)

    row = release_preconditions.check_dind_image_precondition(
        _DIND_ENV, process_env={}, require_docker=True,
    )

    assert row.status == "PASS"
    assert f"CMRU_TESTER_DIND_IMAGE image {_DIND_IMAGE!r} is present locally." == row.detail
    assert calls == [["docker", "image", "inspect", _DIND_IMAGE]]


def test_build_image_rows_never_include_the_dind_image(monkeypatch):
    """The wheel-builder and probe rows stay as they were; the DinD row is separate."""
    calls = []
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, calls)
    rows = release_preconditions.check_release_build_images_precondition(
        _DIND_ENV, process_env={}, require_docker=True,
    )

    assert [row.check for row in rows] == ["Wheel-builder image", "Cgroup probe image"]
    assert ["docker", "image", "inspect", _DIND_IMAGE] not in calls


@pytest.mark.parametrize("configured", [{}, {"CMRU_TESTER_DIND_IMAGE": ""}, {"CMRU_TESTER_DIND_IMAGE": "  "}])
def test_dind_image_row_refuses_an_unset_or_empty_value(configured):
    env = {key: value for key, value in _DIND_ENV.items() if key != "CMRU_TESTER_DIND_IMAGE"}
    env.update(configured)

    row = release_preconditions.check_dind_image_precondition(env, process_env={})

    assert row.status == "FAIL"
    assert "CMRU_TESTER_DIND_IMAGE has no non-empty value" in row.detail
    assert "Set a non-empty CMRU_TESTER_DIND_IMAGE" in row.remedy


def test_dind_image_value_resolves_like_the_probe_image_from_config_before_process(monkeypatch):
    calls = []
    _fake_docker_missing(monkeypatch, "never", calls)
    configured_wins = "docker@sha256:" + "1" * 64

    row = release_preconditions.check_dind_image_precondition(
        {**_DIND_ENV, "CMRU_TESTER_DIND_IMAGE": configured_wins},
        process_env={"CMRU_TESTER_DIND_IMAGE": "docker@sha256:" + "2" * 64},
        require_docker=True,
    )

    assert calls[-1] == ["docker", "image", "inspect", configured_wins]
    assert row.status == "PASS"

    process_only = release_preconditions.check_dind_image_precondition(
        {k: v for k, v in _DIND_ENV.items() if k != "CMRU_TESTER_DIND_IMAGE"},
        process_env={"CMRU_TESTER_DIND_IMAGE": _DIND_IMAGE},
        require_docker=True,
    )
    assert calls[-1] == ["docker", "image", "inspect", _DIND_IMAGE]
    assert process_only.status == "PASS"


@pytest.mark.parametrize(
    "require_docker, expected",
    [(False, "SKIPPED"), (True, "FAIL")],
)
def test_dind_image_row_handles_unavailable_docker(monkeypatch, require_docker, expected):
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: None)

    row = release_preconditions.check_dind_image_precondition(
        _DIND_ENV, process_env={}, require_docker=require_docker,
    )

    assert row.status == expected
    assert "CMRU_TESTER_DIND_IMAGE" in row.detail


def test_dind_image_row_carries_a_project_and_a_distinguishing_label(monkeypatch):
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, [])

    row = release_preconditions.check_dind_image_precondition(
        _DIND_ENV, process_env={}, require_docker=True,
        project="ciu", label="DinD sidecar image (ciu)",
    )

    assert (row.project, row.check, row.status) == ("ciu", "DinD sidecar image (ciu)", "FAIL")
    report = release_preconditions.format_gate_precondition_report([row])
    assert "DinD sidecar image (ciu)" in report
    assert f"Remedy: Run `docker pull {_DIND_IMAGE}`" in report


# ---- per-project DinD preflight in the release launcher (every project in the scope) ----

_CIU_TOML = (
    '[steps.run-tests]\n'
    'commands = [{ label = "enroll", cwd = ".", argv = '
    '["cmru", "tester-gate", "--enable-docker", "--", "bash", "-c", "x"] }]\n'
    '[env]\n'
    f'CMRU_TESTER_DIND_IMAGE = "{_DIND_IMAGE}"\n'
)
_PLAIN_TOML = (
    '[steps.run-tests]\n'
    'commands = [{ label = "t", cwd = ".", argv = ["cmru", "tester-gate", "--", "pytest"] }]\n'
)


def _mock_dind_scope(monkeypatch, tmp_path, texts, *, orchestration_env=None):
    """Scope helper: `texts` maps project name -> release-source config text."""
    configs = {name: SimpleNamespace(name=name, project_root=tmp_path / name) for name in texts}
    fetched = []
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda _root, projects: {tmp_path: list(projects)},
    )
    monkeypatch.setattr(
        cli.transaction, "fetch_origin_main",
        lambda root, **_kwargs: fetched.append(root) or "b" * 40,
    )
    monkeypatch.setattr(
        cli, "_project_snapshot_config_text",
        lambda _root, _revision, _config, _configs, name: (Path(name) / "cmru.toml", texts[name]),
    )
    monkeypatch.setattr(
        cli, "_orchestration_env_for_release_snapshot",
        lambda *_args: dict(orchestration_env or {}),
    )
    return configs, fetched


def test_dind_preflight_refuses_a_non_cmru_release_before_dispatch_in_dry_run_and_release(
    tmp_path, monkeypatch, capsys,
):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"ciu": _CIU_TOML})
    calls = []
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, calls)

    for dry_run in (True, False):
        with pytest.raises(SystemExit) as caught:
            cli._release_dind_image_preconditions(
                tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
                _release_precondition_args(dry_run=dry_run), git_auth=None, report=True,
            )
        assert caught.value.code == 4
        error = capsys.readouterr().err
        assert "DinD sidecar image (ciu)" in error
        assert "CMRU_TESTER_DIND_IMAGE" in error
        assert f"docker pull {_DIND_IMAGE}" in error
    assert calls == [["docker", "image", "inspect", _DIND_IMAGE]] * 2


def test_release_launcher_stops_a_ciu_release_before_dispatch_on_a_missing_dind_image(
    tmp_path, monkeypatch, capsys,
):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"ciu": _CIU_TOML})
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, [])
    monkeypatch.setattr(
        cli, "_release_gate_preconditions_before_dispatch",
        lambda *_a, **_k: pytest.fail("a ciu-only scope has no cmru preconditions"),
    )
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families",
        lambda *_a, **_k: pytest.fail("dispatch must not run after the DinD refusal"),
    )
    monkeypatch.setattr(
        cli, "_preflight_multi_family_release_tag_support", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        cli.transaction, "create_workspace",
        lambda *_a, **_k: pytest.fail("no release workspace may be created"),
    )

    for dry_run in (True, False):
        with pytest.raises(SystemExit) as caught:
            cli._release_launcher(
                [], _release_precondition_args(
                    dry_run=dry_run, target="ciu", allow_uncommitted=False, ahead_check_ref=None,
                ),
                tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["ciu"],
                ["ciu"], ["ciu"], None, None, github_config=None,
            )
        assert caught.value.code == 4
        assert "DinD sidecar image (ciu)" in capsys.readouterr().err


def test_dind_preflight_adds_a_ciu_row_next_to_unchanged_cmru_rows(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    configs["ciu"] = SimpleNamespace(name="ciu", project_root=tmp_path / "ciu")
    texts = {"cmru": _CMRU_TOML_FULL, "ciu": _CIU_TOML}
    monkeypatch.setattr(
        cli, "_project_snapshot_config_text",
        lambda _root, _revision, _config, _configs, name: (Path(name) / "cmru.toml", texts[name]),
    )
    monkeypatch.setattr(
        release_preconditions, "evaluate_release_gate_preconditions",
        lambda *_a, **_k: [release_preconditions.GatePrecondition("cmru", "gate", "PASS", "ok")],
    )
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, [])

    # cmru rows come from the existing function, unchanged, and carry no DinD row.
    revision = cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru", "ciu"],
        _release_precondition_args(), git_auth=None, report=True,
    )
    assert revision == "b" * 40
    assert "DinD" not in capsys.readouterr().out

    with pytest.raises(SystemExit) as caught:
        cli._release_dind_image_preconditions(
            tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru", "ciu"],
            _release_precondition_args(), git_auth=None, report=True,
        )
    assert caught.value.code == 4
    error = capsys.readouterr().err
    assert "DinD sidecar image (ciu)" in error
    assert "DinD sidecar image (cmru)" not in error  # cmru's own config has no docker step


@pytest.mark.parametrize(
    ("scope", "expected_labels"),
    [
        (["cmru"], ["DinD sidecar image"]),
        (["cmru", "ciu"], ["DinD sidecar image (cmru)", "DinD sidecar image (ciu)"]),
    ],
    ids=["cmru-alone", "cmru-and-ciu"],
)
def test_dind_preflight_suffixes_labels_only_when_rows_could_be_confused(
    tmp_path, monkeypatch, capsys, scope, expected_labels,
):
    configs, _ = _mock_dind_scope(
        monkeypatch, tmp_path, {name: _CIU_TOML for name in scope},
    )
    _fake_docker_missing(monkeypatch, _DIND_IMAGE, [])

    with pytest.raises(SystemExit):
        cli._release_dind_image_preconditions(
            tmp_path, tmp_path / "cmru.orchestration.toml", configs, scope,
            _release_precondition_args(), git_auth=None, report=True,
        )

    error = capsys.readouterr().err
    for label in expected_labels:
        assert label in error
    if scope == ["cmru"]:
        assert "(cmru)" not in error


def test_dind_preflight_has_no_rows_and_no_docker_call_without_the_flag(
    tmp_path, monkeypatch, capsys,
):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"other": _PLAIN_TOML})
    monkeypatch.setattr(
        release_preconditions.subprocess, "run",
        lambda *_a, **_k: pytest.fail("no docker call without an --enable-docker step"),
    )

    result = cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["other"],
        _release_precondition_args(), git_auth=None, report=True,
    )

    assert result is None
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_release_launcher_skips_the_dind_preflight_on_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli, "_release_dind_image_preconditions",
        lambda *_a, **_k: pytest.fail("a resumed release reads no snapshot"),
    )
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *_a, **_k: 0)
    configs = {"ciu": SimpleNamespace(name="ciu")}

    with pytest.raises(SystemExit):
        cli._release_launcher(
            [], _release_precondition_args(
                resume=Path("retained"), target="ciu", allow_uncommitted=False,
                ahead_check_ref=None,
            ),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["ciu"],
            ["ciu"], ["ciu"], None, None, github_config=None,
        )


def test_dind_preflight_present_image_prints_a_pass_line_naming_the_row(
    tmp_path, monkeypatch, capsys,
):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"ciu": _CIU_TOML})
    _fake_docker_missing(monkeypatch, "not-any-image", [])

    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
        _release_precondition_args(), git_auth=None, report=True,
    )

    assert "DinD sidecar image preconditions: PASS (ciu)." in capsys.readouterr().out


def test_dind_preflight_dry_run_report_shows_a_skipped_row_without_docker(
    tmp_path, monkeypatch, capsys,
):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"ciu": _CIU_TOML})
    monkeypatch.setattr(release_preconditions.shutil, "which", lambda _command: None)

    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
        _release_precondition_args(), git_auth=None, report=True,
    )

    output = capsys.readouterr().out
    assert "DinD sidecar image (ciu)" in output
    assert "SKIPPED" in output


def test_dind_preflight_reports_by_default_for_one_family_and_stays_quiet_for_several(
    tmp_path, monkeypatch, capsys,
):
    configs, _ = _mock_dind_scope(
        monkeypatch, tmp_path, {"ciu": _CIU_TOML, "mdt": _CIU_TOML},
    )
    _fake_docker_missing(monkeypatch, "not-any-image", [])

    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu", "mdt"],
        _release_precondition_args(), git_auth=None,
    )
    assert "DinD sidecar image preconditions: PASS (ciu, mdt)." in capsys.readouterr().out

    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda _root, projects: {Path(f"fam{index}"): [p] for index, p in enumerate(projects)},
    )
    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu", "mdt"],
        _release_precondition_args(), git_auth=None,
    )
    assert capsys.readouterr().out == ""


def test_dind_preflight_report_false_stays_quiet_on_success(tmp_path, monkeypatch, capsys):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"ciu": _CIU_TOML})
    _fake_docker_missing(monkeypatch, "not-any-image", [])

    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
        _release_precondition_args(), git_auth=None, report=False,
    )

    assert capsys.readouterr().out == ""


def test_dind_preflight_uses_each_projects_own_env_and_the_process_fallback(
    tmp_path, monkeypatch,
):
    other_image = "docker@sha256:" + "9" * 64
    texts = {
        "ciu": _CIU_TOML,
        # No [env] pin: falls back to the orchestration default.
        "mdt": _CIU_TOML.split("[env]")[0],
    }
    configs, _ = _mock_dind_scope(
        monkeypatch, tmp_path, texts, orchestration_env={"CMRU_TESTER_DIND_IMAGE": other_image},
    )
    calls = []
    _fake_docker_missing(monkeypatch, "not-any-image", calls)

    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu", "mdt"],
        _release_precondition_args(), git_auth=None, report=False,
    )

    assert calls == [
        ["docker", "image", "inspect", _DIND_IMAGE],
        ["docker", "image", "inspect", other_image],
    ]


def test_dind_preflight_uses_the_process_snapshot_when_no_config_declares_the_image(
    tmp_path, monkeypatch,
):
    configs, _ = _mock_dind_scope(
        monkeypatch, tmp_path, {"ciu": _CIU_TOML.split("[env]")[0]},
    )
    calls = []
    _fake_docker_missing(monkeypatch, "not-any-image", calls)

    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
        _release_precondition_args(_cmru_process_build_image_env={
            "CMRU_TESTER_DIND_IMAGE": _DIND_IMAGE,
        }), git_auth=None, report=False,
    )

    assert calls == [["docker", "image", "inspect", _DIND_IMAGE]]


def test_dind_preflight_fetches_one_revision_per_git_family_and_honours_from_worktree(
    tmp_path, monkeypatch,
):
    configs, fetched = _mock_dind_scope(
        monkeypatch, tmp_path, {"ciu": _CIU_TOML, "mdt": _CIU_TOML},
    )
    _fake_docker_missing(monkeypatch, "not-any-image", [])
    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu", "mdt"],
        _release_precondition_args(), git_auth=None, report=False,
    )
    assert fetched == [tmp_path]  # one family, one fetch

    monkeypatch.setattr(
        cli, "_preflight_feature_worktree_source",
        lambda *_a, **_k: ("feature", "c" * 40),
    )
    monkeypatch.setattr(
        cli, "_project_snapshot_config_text",
        lambda _root, revision, _config, _configs, name: (
            pytest.fail("feature revision expected") if revision != "c" * 40
            else (Path(name) / "cmru.toml", _CIU_TOML)
        ),
    )
    cli._release_dind_image_preconditions(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
        _release_precondition_args(from_worktree=True), git_auth=None, report=False,
    )


def test_dind_preflight_surfaces_an_unreadable_config_like_the_cmru_path(
    tmp_path, monkeypatch,
):
    configs, _ = _mock_dind_scope(monkeypatch, tmp_path, {"ciu": _CIU_TOML})

    def unreadable(*_args):
        raise RuntimeError("cannot read ciu/cmru.toml from release source")

    monkeypatch.setattr(cli, "_project_snapshot_config_text", unreadable)

    with pytest.raises(RuntimeError, match="cannot read ciu/cmru.toml"):
        cli._release_dind_image_preconditions(
            tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["ciu"],
            _release_precondition_args(), git_auth=None, report=True,
        )


def test_release_launcher_runs_the_dind_preflight_for_a_scope_without_cmru(
    tmp_path, monkeypatch,
):
    seen = []
    monkeypatch.setattr(
        cli, "_release_dind_image_preconditions",
        lambda *args, **kwargs: seen.append((list(args[3]), "report" in kwargs)),
    )
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families", lambda *_a, **_k: 0,
    )
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda _root, projects: {tmp_path: list(projects)},
    )
    configs = {"ciu": SimpleNamespace(name="ciu")}

    with pytest.raises(SystemExit):
        cli._release_launcher(
            [], _release_precondition_args(
                target="ciu", allow_uncommitted=False, ahead_check_ref=None,
            ),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["ciu"],
            ["ciu"], ["ciu"], None, None, github_config=None,
        )
    assert seen == [(["ciu"], False)]


@pytest.mark.parametrize(
    "args, project_names",
    [
        (_release_precondition_args(resume=Path("retained")), ["cmru"]),
        (_release_precondition_args(), ["other"]),
    ],
)
def test_release_precondition_helper_ignores_non_applicable_runs(args, project_names):
    assert cli._release_gate_preconditions_before_dispatch(
        Path("repo"), Path("repo/cmru.orchestration.toml"), {}, project_names,
        args, git_auth=None, report=True,
    ) is None


def test_release_precondition_helper_reports_one_pass_line_and_uses_planned_version(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    rows = [release_preconditions.GatePrecondition("cmru", "waiver", "PASS", "accepted")]
    observed = {}

    def evaluate(repo_root, source_root, revision, versions, **kwargs):
        observed.update(
            kwargs=kwargs, repo_root=repo_root, source_root=source_root, revision=revision,
            versions=versions,
        )
        return rows

    monkeypatch.setattr(release_preconditions, "evaluate_release_gate_preconditions", evaluate)

    revision = cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(),
        git_auth=None, report=True,
    )

    assert revision == "b" * 40
    assert observed["versions"] == {"cmru": "7.1.4"}
    assert observed["kwargs"] == {"require_docker": False}
    assert "Release gate preconditions: PASS (cmru 7.1.4)." in capsys.readouterr().out


def test_release_precondition_helper_keeps_parent_dispatch_output_quiet(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    monkeypatch.setattr(
        release_preconditions, "evaluate_release_gate_preconditions",
        lambda *_args, **_kwargs: [release_preconditions.GatePrecondition("cmru", "waiver", "PASS", "ok")],
    )

    cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(),
        git_auth=None, report=False,
    )

    assert capsys.readouterr().out == ""


def test_release_precondition_helper_reports_explicit_gate_skip(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    monkeypatch.setattr(
        release_preconditions, "evaluate_release_gate_preconditions",
        lambda *_args, **_kwargs: pytest.fail("a skipped gate must not run gate preconditions"),
    )

    cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(skip_gates="operator-approved reason"),
        git_auth=None, report=True,
    )

    output = capsys.readouterr().out
    assert "SKIPPED" in output
    assert "operator-approved reason" in output


def test_release_precondition_helper_reports_no_derived_release_version(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path, changed=False)

    cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(),
        git_auth=None, report=True,
    )

    output = capsys.readouterr().out
    assert "SKIPPED" in output
    assert "no CMRU-derived tagged release version" in output


def test_release_precondition_helper_fails_with_policy_exit_and_remedy(
    tmp_path, monkeypatch, capsys,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    monkeypatch.setattr(
        release_preconditions, "evaluate_release_gate_preconditions",
        lambda *_args, **_kwargs: [release_preconditions.GatePrecondition(
            "cmru", "Run-Gate image identity", "FAIL", "labels differ", "rebuild the image",
        )],
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_gate_preconditions_before_dispatch(
            tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
            _release_precondition_args(),
            git_auth=None, report=True,
        )

    assert caught.value.code == 4
    output = capsys.readouterr()
    assert "labels differ" in output.err
    assert "Remedy: rebuild the image" in output.err


def test_release_precondition_helper_uses_feature_source_revision(
    tmp_path, monkeypatch,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli, "_preflight_feature_worktree_source",
        lambda *_args, **_kwargs: ("feature", "c" * 40),
    )
    observed = {}

    def evaluate(_repo_root, _source_root, revision, versions, **_kwargs):
        observed.update(revision=revision, versions=versions)
        return [release_preconditions.GatePrecondition("cmru", "waiver", "PASS", "accepted")]

    monkeypatch.setattr(release_preconditions, "evaluate_release_gate_preconditions", evaluate)

    revision = cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(from_worktree=True), git_auth=None, report=False,
    )

    assert revision == "c" * 40
    assert observed == {"revision": "c" * 40, "versions": {"cmru": "7.1.4"}}


@pytest.mark.parametrize("config_rel", [Path("cmru/cmru.toml"), Path("cmru.toml")])
def test_project_config_for_release_snapshot_parses_source_config_without_a_worktree(
    tmp_path, monkeypatch, config_rel,
):
    config_text = (Path(__file__).resolve().parents[1] / "cmru.toml").read_text("utf-8")
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda *_args: {"cmru": config_rel},
    )
    monkeypatch.setattr(
        cli, "_read_git_path_at_commit", lambda *_args, **_kwargs: config_text,
    )

    project = cli._project_config_for_release_snapshot(
        tmp_path, "d" * 40, tmp_path / "cmru.orchestration.toml", {},
    )

    assert project.name == "cmru"
    assert project.prefix == "cmru-v"
    assert project.project_root == tmp_path / config_rel.parent
    assert project.cwd == (config_rel.parent.as_posix() if config_rel.parent.parts else ".")
    assert project.paths[0] == project.cwd


def test_release_launcher_stops_before_dispatch_on_gate_precondition_failure(
    tmp_path, monkeypatch,
):
    project = SimpleNamespace(name="cmru")
    configs = {"cmru": project}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )
    monkeypatch.setattr(
        cli, "_release_gate_preconditions_before_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(4)),
    )
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families",
        lambda *_args, **_kwargs: pytest.fail("dispatch must not run after a precondition refusal"),
    )
    monkeypatch.setattr(
        cli.transaction, "create_workspace",
        lambda *_args, **_kwargs: pytest.fail("no release workspace may be created"),
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], _release_precondition_args(target="cmru", allow_uncommitted=False, ahead_check_ref=None),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["cmru"],
            ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == 4


def test_actual_release_stops_before_dispatch_when_probe_image_is_missing(
    tmp_path, monkeypatch, capsys,
):
    probe = "debian@sha256:" + "a" * 64
    builder = "wheel-builder:test"
    docker_calls = []
    project = SimpleNamespace(
        name="cmru", prefix="cmru-v", env={
            "CMRU_WHEEL_BUILDER_IMAGE": builder,
            "CMRU_TESTER_CGROUP_PROBE_IMAGE": probe,
        },
    )
    configs = {"cmru": project}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )
    monkeypatch.setattr(
        cli, "_preflight_multi_family_release_tag_support",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(cli.transaction, "fetch_origin_main", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(cli, "_project_config_for_release_snapshot", lambda *_args: project)
    monkeypatch.setattr(
        cli, "_orchestration_env_for_release_snapshot", lambda *_args: {},
    )
    monkeypatch.setattr(cli, "_configs_for_git_family", lambda *_args: configs)
    monkeypatch.setattr(cli, "_known_project_scope_names", lambda _configs: frozenset({"cmru"}))
    monkeypatch.setattr(
        "cmru.version.status_records",
        lambda *_args, **_kwargs: [{"changed": True, "next_version": "cmru-v7.1.4"}],
    )
    monkeypatch.setattr(
        release_preconditions.shutil, "which", lambda _command: "/usr/bin/docker",
    )

    def fake_docker(argv, **_kwargs):
        docker_calls.append(argv)
        if argv[-1] == probe:
            return SimpleNamespace(returncode=1, stdout="", stderr=f"Error: No such image: {probe}")
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(release_preconditions.subprocess, "run", fake_docker)
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families",
        lambda *_args, **_kwargs: pytest.fail("dispatch must not run after image refusal"),
    )
    monkeypatch.setattr(
        cli.transaction, "create_workspace",
        lambda *_args, **_kwargs: pytest.fail("no release workspace may be created"),
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], _release_precondition_args(
                dry_run=False, target="cmru", allow_uncommitted=False,
                ahead_check_ref=None,
            ),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["cmru"],
            ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == 4
    error = capsys.readouterr().err
    assert "CMRU_TESTER_CGROUP_PROBE_IMAGE" in error
    assert probe in error
    assert f"docker pull {probe}" in error
    assert docker_calls == [
        ["docker", "image", "inspect", builder],
        ["docker", "image", "inspect", probe],
    ]


def test_release_launcher_dispatches_after_gate_preconditions_pass(tmp_path, monkeypatch):
    project = SimpleNamespace(name="cmru")
    configs = {"cmru": project}
    observed = {}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )
    monkeypatch.setattr(cli, "_release_dind_image_preconditions", lambda *_a, **_k: None)

    def preconditions(*_args, **kwargs):
        observed["report"] = kwargs["report"]
        return "b" * 40

    monkeypatch.setattr(cli, "_release_gate_preconditions_before_dispatch", preconditions)
    monkeypatch.setattr(cli, "_dispatch_independent_git_families", lambda *_args, **_kwargs: 0)

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], _release_precondition_args(target="cmru", allow_uncommitted=False, ahead_check_ref=None),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["cmru"],
            ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == 0
    assert observed == {"report": True}


def _prepare_release_launcher_to_workspace(monkeypatch, tmp_path, *, feature=False):
    project = SimpleNamespace(name="cmru", project_root=tmp_path / "cmru")
    configs = {"cmru": project}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )
    monkeypatch.setattr(cli, "_release_dind_image_preconditions", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        cli.transaction, "source_git_root_for_projects", lambda *_args: tmp_path,
    )
    monkeypatch.setattr(cli, "_consume_release_snapshot_handoff", lambda *_args: None)
    monkeypatch.setattr(cli, "_child_release_args", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli.transaction, "release_lock", lambda *_args: nullcontext())
    monkeypatch.setattr(
        cli.transaction, "assert_local_main_not_ahead", lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        cli, "_project_config_paths_at_snapshot",
        lambda *_args, **_kwargs: {"cmru": Path("cmru/cmru.toml")},
    )
    monkeypatch.setattr(
        cli.transaction, "create_workspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SystemExit(97)),
    )
    args = _release_precondition_args(
        target="cmru", from_worktree=feature, allow_uncommitted=False,
        ahead_check_ref=None, allow_tag_ahead_of_head=False,
    )
    return project, configs, args


def test_release_launcher_rechecks_origin_snapshot_before_workspace_creation(tmp_path, monkeypatch):
    _project, configs, args = _prepare_release_launcher_to_workspace(monkeypatch, tmp_path)
    revision = "e" * 40
    monkeypatch.setattr(
        cli, "_release_gate_preconditions_before_dispatch",
        lambda *_args, **_kwargs: revision,
    )
    observed = []
    monkeypatch.setattr(
        cli.transaction, "fetch_origin_main",
        lambda *_args, **_kwargs: observed.append("fetch") or revision,
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], args, tmp_path / "cmru.orchestration.toml", tmp_path, configs, {},
            ["cmru"], ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == 97
    assert observed == ["fetch"]


def test_release_launcher_refuses_origin_advance_after_precondition_check(tmp_path, monkeypatch):
    _project, configs, args = _prepare_release_launcher_to_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli, "_release_gate_preconditions_before_dispatch",
        lambda *_args, **_kwargs: "e" * 40,
    )
    monkeypatch.setattr(
        cli.transaction, "fetch_origin_main", lambda *_args, **_kwargs: "f" * 40,
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], args, tmp_path / "cmru.orchestration.toml", tmp_path, configs, {},
            ["cmru"], ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == 4


def test_feature_release_refuses_source_advance_after_precondition_check(tmp_path, monkeypatch):
    _project, configs, args = _prepare_release_launcher_to_workspace(
        monkeypatch, tmp_path, feature=True,
    )
    monkeypatch.setattr(
        cli, "_release_gate_preconditions_before_dispatch",
        lambda *_args, **_kwargs: "c" * 40,
    )
    monkeypatch.setattr(
        cli, "_preflight_feature_worktree_source",
        lambda *_args, **_kwargs: ("release-branch", "d" * 40),
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], args, tmp_path / "cmru.orchestration.toml", tmp_path, configs, {},
            ["cmru"], ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == 4


@pytest.mark.parametrize(
    "error, exit_code",
    [
        (cli.RefusedBeforeChange("policy refusal"), 4),
        (RuntimeError("source preflight error"), 1),
    ],
)
def test_release_launcher_preserves_dind_preflight_error_exit_codes(
    tmp_path, monkeypatch, error, exit_code,
):
    configs = {"ciu": SimpleNamespace(name="ciu")}
    monkeypatch.setattr(
        cli, "_release_dind_image_preconditions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families",
        lambda *_args, **_kwargs: pytest.fail("a failed DinD preflight must stop before dispatch"),
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], _release_precondition_args(
                target="ciu", allow_uncommitted=False, ahead_check_ref=None,
            ),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["ciu"],
            ["ciu"], ["ciu"], None, None, github_config=None,
        )

    assert caught.value.code == exit_code


@pytest.mark.parametrize(
    "error, exit_code",
    [
        (cli.RefusedBeforeChange("policy refusal"), 4),
        (RuntimeError("source preflight error"), 1),
    ],
)
def test_release_launcher_preserves_precondition_error_exit_codes(
    tmp_path, monkeypatch, error, exit_code,
):
    project = SimpleNamespace(name="cmru")
    configs = {"cmru": project}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )
    monkeypatch.setattr(
        cli, "_release_gate_preconditions_before_dispatch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(
        cli, "_dispatch_independent_git_families",
        lambda *_args, **_kwargs: pytest.fail("precondition errors must stop before dispatch"),
    )

    with pytest.raises(SystemExit) as caught:
        cli._release_launcher(
            [], _release_precondition_args(
                target="cmru", allow_uncommitted=False, ahead_check_ref=None,
            ),
            tmp_path / "cmru.orchestration.toml", tmp_path, configs, {}, ["cmru"],
            ["cmru"], ["cmru"], None, None, github_config=None,
        )

    assert caught.value.code == exit_code


@pytest.mark.parametrize(
    "config_text", [_CMRU_TOML_RELEASE_GATE, _CMRU_TOML_FULL], ids=["release-gate", "gate"],
)
def test_dry_run_accepts_a_run_tests_step_with_either_release_lane(
    tmp_path, monkeypatch, capsys, config_text,
):
    """The dry-run needs no window check, whichever lane the run-tests step names."""
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    read_configs = []
    monkeypatch.setattr(
        cli, "_cmru_snapshot_config_text",
        lambda *_args: read_configs.append(True) or (Path("cmru/cmru.toml"), config_text),
    )
    _gate_preconditions(tmp_path, monkeypatch)

    revision = cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(minor=True), git_auth=None, report=True,
    )

    assert revision == "b" * 40
    assert read_configs == []  # nothing about the run-tests step is consulted any more
    output = capsys.readouterr()
    assert "Release gate preconditions: PASS (cmru 7.1.4)." in output.out
    assert "window" not in (output.out + output.err).lower()
