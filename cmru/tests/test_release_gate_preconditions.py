from __future__ import annotations

import os
import subprocess
import tomllib
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, release_preconditions

_REAL_LOAD_GATE = release_preconditions._load_release_gate  # captured before any test patches it

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
    def validate_postponement(project_root, tracking_id):
        assert tracking_id == "KI-79"
        prepared = (project_root / "CHANGES.md").read_text(encoding="utf-8")
        if "[7.2.0]" in prepared:
            raise RuntimeError("version 7.2.0 is outside the KI-79 window")
        return "7.1.4"

    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda _root, _revision: SimpleNamespace(
            _validate_postponement=validate_postponement,
        ),
    )
    monkeypatch.setattr(
        release_preconditions,
        "check_run_gate_image_precondition",
        lambda _root, _source_root, _revision: release_preconditions.GatePrecondition(
            "cmru", "Run-Gate image identity", image_status, image_detail,
            "Rebuild tester-unified:local from the published Run-Gate wheel.",
        ),
    )
    return repo_root


def test_minor_release_outside_ki79_window_is_a_failed_precondition(tmp_path, monkeypatch):
    repo_root = _gate_preconditions(tmp_path, monkeypatch)

    rows = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": "7.2.0"},
    )

    waiver = next(row for row in rows if row.check == "KI-79 waiver window")
    assert waiver.status == "FAIL"
    assert "7.2.0" in waiver.detail
    assert "to `run-gate gate`" in waiver.remedy


def test_minor_release_inside_ki79_window_passes_the_gate_validator(tmp_path, monkeypatch):
    repo_root = _gate_preconditions(tmp_path, monkeypatch)

    rows = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": "7.1.4"},
    )

    waiver = next(row for row in rows if row.check == "KI-79 waiver window")
    assert waiver.status == "PASS"
    assert "7.1.4" in waiver.detail


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


def test_prepared_version_reader_uses_gate_validator(tmp_path):
    observed = {}

    def validate(project_root, tracking_id):
        observed["tracking_id"] = tracking_id
        observed["changes"] = (project_root / "CHANGES.md").read_text("utf-8")
        raise RuntimeError("outside the authorized window")

    error = release_preconditions._prepared_version_error(
        SimpleNamespace(_validate_postponement=validate), "7.2.0",
    )

    assert error == "outside the authorized window"
    assert observed["tracking_id"] == "KI-79"
    assert "[7.2.0]" in observed["changes"]


def test_prepared_version_reader_returns_none_for_an_authorized_version():
    error = release_preconditions._prepared_version_error(
        SimpleNamespace(_validate_postponement=lambda _root, _tracking_id: "7.1.4"),
        "7.1.4",
    )

    assert error is None


def test_prepared_version_reader_matches_the_release_gate_policy_at_head(tmp_path):
    # Hermetic: the test tree may be an extracted source copy without .git, so
    # commit the real gate driver into a throwaway repository and load it from
    # that repository's HEAD.
    gate_source = Path(__file__).resolve().parents[1] / "tools" / "run_release_gate.py"
    source_root = tmp_path / "source"
    (source_root / "cmru" / "tools").mkdir(parents=True)
    (source_root / "cmru" / "tools" / "run_release_gate.py").write_text(
        gate_source.read_text("utf-8"), encoding="utf-8",
    )
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "-c", "commit.gpgsign=false"]
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    for args in (
        ["init", "-q"],
        ["add", "cmru/tools/run_release_gate.py"],
        [*identity, "commit", "-q", "-m", "gate"],
    ):
        subprocess.run(["git", *args], cwd=source_root, env=env, check=True,
                       capture_output=True)
    gate = release_preconditions._load_release_gate(source_root, "HEAD")

    assert release_preconditions._prepared_version_error(gate, "7.1.4") is None
    assert release_preconditions._prepared_version_error(gate, "7.2.0") is None
    assert release_preconditions._prepared_version_error(gate, "7.3.0") is None
    assert release_preconditions._prepared_version_error(gate, "7.3.9") is None
    error = release_preconditions._prepared_version_error(gate, "7.4.0")
    assert error is not None
    assert "authorized only for CMRU" in error


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


def test_release_gate_source_failure_becomes_a_failed_waiver_row(tmp_path, monkeypatch):
    repo_root = _gate_preconditions(tmp_path, monkeypatch)
    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("gate source is unreadable")),
    )

    rows = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": "7.1.4"},
    )

    waiver = next(row for row in rows if row.check == "KI-79 waiver window")
    assert waiver.status == "FAIL"
    assert "gate source is unreadable" in waiver.detail
    assert "Repair the release gate source" in waiver.remedy


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
    }
    values.update(updates)
    return SimpleNamespace(**values)


_CMRU_TOML_PROVISIONAL = (
    '[steps.run-tests]\n'
    'commands = [{ label = "gate", argv = ["run-gate", "gate-provisional"], cwd = "." }]\n'
)
_CMRU_TOML_FULL = _CMRU_TOML_PROVISIONAL.replace("gate-provisional", "gate")


def _mock_cli_release_preconditions(monkeypatch, tmp_path, *, changed=True):
    project = SimpleNamespace(
        name="cmru", project_root=tmp_path / "cmru", prefix="cmru-v",
        paths=["cmru"], cwd="cmru",
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
        cli, "_cmru_snapshot_config_text",
        lambda *_args: (Path("cmru/cmru.toml"), _CMRU_TOML_PROVISIONAL),
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
    return configs


@pytest.mark.parametrize(
    "args, project_names",
    [
        (_release_precondition_args(dry_run=False), ["cmru"]),
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
        observed.update(gate_provisional=kwargs["gate_provisional"],
            repo_root=repo_root, source_root=source_root, revision=revision,
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
    assert observed["gate_provisional"] is True
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
            "cmru", "KI-79", "FAIL", "planned 7.2.0 is outside the window", "run full gate",
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
    assert "planned 7.2.0 is outside the window" in output.err
    assert "Remedy: run full gate" in output.err


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


def test_release_launcher_dispatches_after_gate_preconditions_pass(tmp_path, monkeypatch):
    project = SimpleNamespace(name="cmru")
    configs = {"cmru": project}
    observed = {}
    monkeypatch.setattr(
        cli.transaction, "project_git_family_groups",
        lambda *_args: {tmp_path: [project]},
    )

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


def test_non_provisional_gate_skips_the_ki79_window_even_for_a_minor_release(
    tmp_path, monkeypatch,
):
    repo_root = _gate_preconditions(tmp_path, monkeypatch)
    monkeypatch.setattr(
        release_preconditions, "_load_release_gate",
        lambda *_args: pytest.fail("no waiver is used, so the KI-79 validator must not run"),
    )

    rows = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": "7.2.0"}, gate_provisional=False,
    )

    waiver = next(row for row in rows if row.check == "KI-79 waiver window")
    assert waiver.status == "SKIPPED"
    assert "gate-provisional" in waiver.detail
    assert [row.check for row in rows][1:] == [
        "Run-Gate image identity", "Bootstrap Run-Gate version",
    ]
    assert not any(row.status == "FAIL" for row in rows)


def test_ki79_failure_advice_leads_to_a_passing_or_skipped_row(tmp_path, monkeypatch):
    repo_root = _gate_preconditions(tmp_path, monkeypatch)
    failed = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": "7.2.0"},
        gate_provisional=release_preconditions.run_tests_uses_provisional_gate(
            _CMRU_TOML_PROVISIONAL,
        ),
    )[0]
    assert failed.status == "FAIL"
    assert "run-gate gate-provisional" in failed.remedy and "run-gate gate`" in failed.remedy

    # Follow the remedy: the run-tests command becomes the full gate.
    followed = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": "7.2.0"},
        gate_provisional=release_preconditions.run_tests_uses_provisional_gate(
            _CMRU_TOML_FULL,
        ),
    )[0]
    assert followed.status == "SKIPPED"


def test_run_tests_provisional_detection_reads_the_configured_argv():
    assert release_preconditions.run_tests_uses_provisional_gate(_CMRU_TOML_PROVISIONAL)
    assert not release_preconditions.run_tests_uses_provisional_gate(_CMRU_TOML_FULL)
    assert not release_preconditions.run_tests_uses_provisional_gate("[project]\nname = 'x'\n")
    real = (Path(__file__).resolve().parents[1] / "cmru.toml").read_text("utf-8")
    assert release_preconditions.run_tests_uses_provisional_gate(real)


def test_release_precondition_helper_passes_the_full_gate_choice_through(
    tmp_path, monkeypatch,
):
    configs = _mock_cli_release_preconditions(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli, "_cmru_snapshot_config_text",
        lambda *_args: (Path("cmru/cmru.toml"), _CMRU_TOML_FULL),
    )
    observed = {}

    def evaluate(*_args, **kwargs):
        observed.update(kwargs)
        return [release_preconditions.GatePrecondition("cmru", "waiver", "SKIPPED", "full gate")]

    monkeypatch.setattr(release_preconditions, "evaluate_release_gate_preconditions", evaluate)

    cli._release_gate_preconditions_before_dispatch(
        tmp_path, tmp_path / "cmru.orchestration.toml", configs, ["cmru"],
        _release_precondition_args(), git_auth=None, report=False,
    )

    assert observed == {"gate_provisional": False}


@pytest.mark.parametrize(
    "version",
    [
        "6.1.9", "6.2.0", "6.3.0", "7.0.0", "7.1.9",
        "7.2.0", "7.2.1", "7.2.9", "7.3.0", "7.3.9", "7.4.0", "8.0.0",
    ],
)
def test_dry_run_precondition_agrees_with_the_real_gate_window_table(tmp_path, monkeypatch, version):
    """KI-79/KI-109: the dry-run row and the gate table are one source and cannot diverge."""
    from tests.test_release_gate import run_release_gate

    repo_root = _gate_preconditions(tmp_path, monkeypatch)
    gate_source = Path(run_release_gate.__file__).read_text(encoding="utf-8")
    real_blobs = release_preconditions._git_blob_text
    monkeypatch.setattr(
        release_preconditions, "_git_blob_text",
        lambda root, revision, path: (
            gate_source if path == "cmru/tools/run_release_gate.py" else real_blobs(root, revision, path)
        ),
    )
    monkeypatch.setattr(release_preconditions, "_load_release_gate", _REAL_LOAD_GATE)

    rows = release_preconditions.evaluate_release_gate_preconditions(
        repo_root, repo_root, "HEAD", {"cmru": version},
    )

    waiver = next(row for row in rows if row.check == "KI-79 waiver window")
    major_minor_patch = tuple(int(part) for part in version.split("."))
    in_table = any(
        low <= major_minor_patch < below
        for low, below in run_release_gate.POSTPONEMENT_RELEASE_VERSIONS["KI-79"]
    )
    assert (waiver.status == "PASS") is in_table
