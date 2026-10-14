"""Behavioral checks for the exact-wheel release handshake."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
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
    monkeypatch.setattr(release_candidate, "_git", lambda *args: "run-gate-v23.10.0" if args[0] == "tag" else "")
    assert release_candidate.prepared_release_version(changes) == "24.0.0"
    assert (source_entry in sys.path) is was_present


def test_prepared_release_version_refuses_a_heading_that_is_already_released(monkeypatch, tmp_path):
    changes = tmp_path / "CHANGES.md"
    changes.write_text("## [24.0.0] - 2026-10-14\n\nAlready tagged.\n", encoding="utf-8")
    monkeypatch.setattr(release_candidate, "_git", lambda *args: "run-gate-v24.0.0" if args[0] == "tag" else "")
    with pytest.raises(release_candidate.ReleaseCandidateError, match="not newer than the latest release tag"):
        release_candidate.prepared_release_version(changes)


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

    assert release_candidate.prepared_release_version(changes) == "24.0.0"
    assert sys.path.count(source_entry) == 1


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
    monkeypatch.setattr(release_candidate, "prepared_release_version", lambda _path: "24.0.0")
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
    monkeypatch.setattr(release_candidate, "prepared_release_version", lambda _path: "24.0.0")
    monkeypatch.setattr(
        release_candidate,
        "_git",
        lambda *args: "1700000000" if args[0] == "log" else "1" * 40,
    )
    monkeypatch.setattr(release_candidate, "_wheel_identity", lambda: wheel)
    monkeypatch.setattr(release_candidate, "_image_identity", lambda _tag: image_identity)
    monkeypatch.setattr(release_candidate, "RECEIPT_PATH", tmp_path / "receipt.json")
    results = iter((_completed(), _completed(), image_result))
    monkeypatch.setattr(release_candidate.subprocess, "run", lambda *_args, **_kwargs: next(results))

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
    assert release_candidate.run_selftest(receipt_path) == 7
    argv, cwd, env, check = calls[0]
    assert argv == [str(candidate_cli), "selftest"]
    assert cwd == project_root
    assert env["RUN_GATE_RELEASE_WHEEL_RECEIPT"] == ".assay/receipt.json"
    assert env["RUN_GATE_TESTER_UNIFIED_IMAGE"] == receipt["image_tag"]
    assert check is False


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


def test_receipt_image_check_requires_the_prepared_image_id(monkeypatch):
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
    assert 'cmru tester-gate --cwd run-gate-project --cgroup-parent "$cgroup_parent" /bin/true' in builder
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
