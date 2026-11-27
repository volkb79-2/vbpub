import json
import math
import subprocess

import pytest

from fleetd import agent_entry, agent_providers
from fleetd.admission import AdmissionError, ExitCode, InspectorAnswer
from fleetd.agent_policy import load_agent_policy
from fleetd.agent_providers import docker_container_identity_inspector, docker_object_inspector, legacy_job_ids
from fleetd.contracts import ContractError
from test_agent_protocol import _policy

_CONTAINER_ID = "a" * 64


def _docker_record(container_id=_CONTAINER_ID, name="legacy-runner", labels=None):
    return {
        "Id": container_id,
        "Name": f"/{name}",
        "Config": {"Labels": labels},
        "State": {"Status": "running"},
    }


def _completed(argv, payload, *, returncode=0, stderr=""):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def test_legacy_job_ids_charge_every_entry_except_a_regular_exit_marker(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "active odd name").mkdir()
    finished = root / "finished"
    finished.mkdir()
    (finished / "EXIT").write_text("0\n", encoding="utf-8")
    marker_directory = root / "directory-marker"
    marker_directory.mkdir()
    (marker_directory / "EXIT").mkdir()
    marker_link = root / "symlink-marker"
    marker_link.mkdir()
    outside_marker = tmp_path / "outside-marker"
    outside_marker.write_text("0\n", encoding="utf-8")
    (marker_link / "EXIT").symlink_to(outside_marker)
    linked_entry = root / "entry-link"
    linked_entry.symlink_to(finished, target_is_directory=True)
    (root / "plain-file").write_text("unexpected", encoding="utf-8")
    (root / "unreadable").mkdir()

    original_open_beneath = agent_providers.open_beneath

    def open_beneath(root_fd, relative, *, directory=False):
        if relative == "unreadable":
            raise PermissionError("simulated unreadable entry")
        return original_open_beneath(root_fd, relative, directory=directory)

    monkeypatch.setattr(agent_providers, "open_beneath", open_beneath)
    names = sorted(path.name for path in root.iterdir())
    live_indexes = [index for index, name in enumerate(names) if name != "finished"]
    expected = tuple(f"legacy-job-{index}" for index in live_indexes)

    assert legacy_job_ids(root) == expected
    assert legacy_job_ids(root) == expected
    assert len(set(expected)) == len(expected)
    assert all(name[0].isalnum() and len(name) <= 128 for name in expected)


def test_legacy_job_ids_refuse_relative_roots_and_root_open_or_listing_failures(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="must be absolute"):
        legacy_job_ids("relative/jobs")

    missing = tmp_path / "missing"
    with pytest.raises(OSError):
        legacy_job_ids(missing)

    root = tmp_path / "jobs"
    root.mkdir()
    root_link = tmp_path / "jobs-link"
    root_link.symlink_to(root, target_is_directory=True)
    with pytest.raises(OSError):
        legacy_job_ids(root_link)

    monkeypatch.setattr(
        agent_providers.os, "listdir",
        lambda _fd: (_ for _ in ()).throw(PermissionError("simulated listing failure")),
    )
    with pytest.raises(PermissionError, match="listing failure"):
        legacy_job_ids(root)


def test_legacy_job_ids_refuse_unrepresentable_listing_results(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    root.mkdir()
    (root / "pending").mkdir()
    monkeypatch.setattr(agent_providers.os, "listdir", lambda _fd: [b"not-text"])
    with pytest.raises(OSError, match="non-text entry"):
        legacy_job_ids(root)

    monkeypatch.setattr(agent_providers.os, "listdir", lambda _fd: ["../odd-name"])
    assert len(legacy_job_ids(root)) == 1

    class RejectLegacyId:
        @staticmethod
        def fullmatch(_value):
            return None

    monkeypatch.setattr(agent_providers.os, "listdir", lambda _fd: ["pending"])
    monkeypatch.setattr(agent_providers, "_ADMISSION_NAME_RE", RejectLegacyId)
    with pytest.raises(OSError, match="identifier limits"):
        legacy_job_ids(root)


def test_docker_object_inspector_uses_one_argv_target_and_matches_exact_name_or_id():
    record = _docker_record()
    calls = []

    def runner(argv, **options):
        calls.append((argv, options))
        return _completed(argv, [record])

    inspect = docker_object_inspector(2.5, runner=runner)
    assert inspect(_CONTAINER_ID) is InspectorAnswer.PRESENT
    assert inspect("legacy-runner") is InspectorAnswer.PRESENT
    assert inspect(_CONTAINER_ID[:12]) is InspectorAnswer.ERROR
    assert inspect("--help") is InspectorAnswer.ERROR
    assert inspect(None) is InspectorAnswer.ERROR
    assert len(calls) == 3
    assert [call[0] for call in calls] == [
        ["docker", "inspect", "--type=container", _CONTAINER_ID],
        ["docker", "inspect", "--type=container", "legacy-runner"],
        ["docker", "inspect", "--type=container", _CONTAINER_ID[:12]],
    ]
    for _argv, options in calls:
        assert options == {
            "check": False, "capture_output": True, "text": True,
            "timeout": 2.5, "shell": False,
        }


def test_docker_object_inspector_returns_no_such_only_for_explicit_empty_errors():
    def missing_container(argv, **_options):
        return _completed(argv, "", returncode=1, stderr="error response from daemon: no such container: absent")

    def missing_object(argv, **_options):
        return _completed(argv, "", returncode=1, stderr="NO SUCH OBJECT: absent")

    def other_failure(argv, **_options):
        return _completed(argv, "", returncode=1, stderr="permission denied")

    def wrong_target(argv, **_options):
        return _completed(argv, "", returncode=1, stderr="No such object: another")

    def partial_output(argv, **_options):
        return _completed(argv, "{partial", returncode=1, stderr="No such object: absent")

    assert docker_object_inspector(1, runner=missing_container)("absent") is InspectorAnswer.NO_SUCH_OBJECT
    assert docker_object_inspector(1, runner=missing_object)("absent") is InspectorAnswer.NO_SUCH_OBJECT
    assert docker_object_inspector(1, runner=other_failure)("absent") is InspectorAnswer.ERROR
    assert docker_object_inspector(1, runner=wrong_target)("absent") is InspectorAnswer.ERROR
    assert docker_object_inspector(1, runner=partial_output)("absent") is InspectorAnswer.ERROR


@pytest.mark.parametrize(
    "runner",
    [
        lambda _argv, **_options: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
        lambda _argv, **_options: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("docker", 1),
        ),
        lambda _argv, **_options: (_ for _ in ()).throw(RuntimeError("runner failed")),
        lambda _argv, **_options: object(),
        lambda argv, **_options: subprocess.CompletedProcess(argv, "0", "[]", ""),
        lambda argv, **_options: subprocess.CompletedProcess(argv, 0, b"[]", ""),
        lambda argv, **_options: subprocess.CompletedProcess(argv, 0, "[]", b""),
        lambda argv, **_options: _completed(argv, "{}", stderr="unexpected warning"),
        lambda argv, **_options: _completed(argv, "{partial"),
        lambda argv, **_options: _completed(argv, []),
        lambda argv, **_options: _completed(argv, [_docker_record(), _docker_record()]),
        lambda argv, **_options: _completed(argv, True),
        lambda argv, **_options: _completed(argv, [None]),
        lambda argv, **_options: _completed(argv, [_docker_record(name="different")]),
        lambda argv, **_options: _completed(argv, [_docker_record(container_id="short")]),
        lambda argv, **_options: _completed(argv, [_docker_record(container_id=17)]),
        lambda argv, **_options: _completed(argv, [{**_docker_record(), "Name": None}]),
        lambda argv, **_options: _completed(argv, [_docker_record(name="bad/name")]),
        lambda argv, **_options: _completed(
            argv, [{key: value for key, value in _docker_record().items() if key != "Config"}],
        ),
        lambda argv, **_options: _completed(
            argv, [{**_docker_record(), "Config": {"Labels": []}}],
        ),
        lambda argv, **_options: _completed(
            argv, [{**_docker_record(), "Config": {"Labels": {"bad": 1}}}],
        ),
    ],
    ids=[
        "missing-binary", "timeout", "runner-exception", "invalid-runner-result", "bad-return-code",
        "bytes-stdout", "bytes-stderr", "success-stderr", "partial-json", "empty-array", "multiple-objects",
        "non-object-json", "non-object-array-entry", "mismatched-name", "invalid-id", "non-text-id", "missing-name",
        "invalid-name", "missing-config", "invalid-label-shape", "invalid-label-value",
    ],
)
def test_docker_object_inspector_fails_closed_for_incomplete_or_invalid_results(runner):
    assert docker_object_inspector(1, runner=runner)("legacy-runner") is InspectorAnswer.ERROR


@pytest.mark.parametrize("timeout", [True, "1", 0, -1, math.inf, math.nan, 10**400])
def test_docker_inspector_requires_a_finite_positive_timeout(timeout):
    with pytest.raises(ValueError, match="finite positive number"):
        docker_object_inspector(timeout)


def test_docker_inspector_rejects_an_invalid_executable_without_running_it():
    for executable in ("-docker", "", None):
        calls = []
        inspect = docker_object_inspector(
            1, runner=lambda *args, **kwargs: calls.append((args, kwargs)), docker_binary=executable,
        )
        assert inspect("legacy-runner") is InspectorAnswer.ERROR
        assert calls == []


def test_docker_object_inspector_accepts_a_valid_unprefixed_docker_name():
    record = _docker_record()
    record["Name"] = "legacy-runner"
    inspect = docker_object_inspector(
        1, runner=lambda argv, **_options: _completed(argv, record),
    )
    assert inspect("legacy-runner") is InspectorAnswer.PRESENT


def test_container_identity_inspector_requires_exact_full_id_and_returns_bind_metadata():
    record = _docker_record(labels={"fleetd.lease": "controller-lease"})
    calls = []

    def runner(argv, **options):
        calls.append((argv, options))
        return _completed(argv, record)

    inspect = docker_container_identity_inspector(3, runner=runner)
    assert inspect(_CONTAINER_ID) == {
        "id": _CONTAINER_ID,
        "name": "/legacy-runner",
        "labels": {"fleetd.lease": "controller-lease"},
    }
    assert calls[0][0] == ["docker", "inspect", "--type=container", _CONTAINER_ID]
    assert calls[0][1]["timeout"] == 3

    missing = docker_container_identity_inspector(
        3,
        runner=lambda argv, **_options: _completed(argv, "", returncode=1, stderr=f"Error: No such object: {_CONTAINER_ID}"),
    )
    assert missing(_CONTAINER_ID) is None

    invalid_calls = []
    invalid = docker_container_identity_inspector(
        3,
        runner=lambda *args, **kwargs: invalid_calls.append((args, kwargs)),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="full lowercase Docker ID"):
        invalid(_CONTAINER_ID[:12])
    with pytest.raises(agent_providers.DockerInspectionError, match="target must be text"):
        invalid(None)
    assert invalid_calls == []


def test_container_identity_inspector_errors_on_a_mismatched_full_id_or_bad_labels():
    wrong = _docker_record(container_id="b" * 64)
    inspect_wrong = docker_container_identity_inspector(
        1, runner=lambda argv, **_options: _completed(argv, [wrong]),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="different container ID"):
        inspect_wrong(_CONTAINER_ID)

    no_labels = docker_container_identity_inspector(
        1, runner=lambda argv, **_options: _completed(argv, _docker_record(labels=None)),
    )
    assert no_labels(_CONTAINER_ID)["labels"] == {}

    failed = docker_container_identity_inspector(
        1, runner=lambda _argv, **_options: (_ for _ in ()).throw(FileNotFoundError("docker missing")),
    )
    with pytest.raises(agent_providers.DockerInspectionError, match="identity inspection failed"):
        failed(_CONTAINER_ID)


def test_agent_entry_wires_real_providers_and_they_work_at_the_service_boundary(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    jobs = policy.legacy_jobs_dir
    jobs.mkdir()
    (jobs / "running odd name").mkdir()
    finished = jobs / "finished"
    finished.mkdir()
    (finished / "EXIT").write_text("0\n", encoding="utf-8")
    labels = {"fleetd.lease": "controller-lease"}
    record = _docker_record(name="legacy-runner", labels=labels)
    calls = []

    def runner(argv, **options):
        calls.append((argv, options))
        return _completed(argv, [record])

    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    monkeypatch.setattr(agent_providers.subprocess, "run", runner)
    service, loaded_policy = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    service.admission._legacy_call = lambda callback: callback()
    service.admission._inspector_call = lambda callback: callback()

    assert loaded_policy.legacy_jobs_dir == jobs
    assert service.admission.legacy_jobs_provider is not None
    assert service.admission.inspector is not None
    assert service.admission.container_inspector is not None
    assert "lease" in service._capabilities()
    assert len(service.admission._legacy_provider()()) == 1
    assert service.admission._inspector_provider()("legacy-runner") is InspectorAnswer.PRESENT
    service.admission._verify_container_binding("controller-lease", "legacy-runner", _CONTAINER_ID)
    assert [call[0][-1] for call in calls] == ["legacy-runner", _CONTAINER_ID]
    assert all(call[1]["timeout"] == loaded_policy.query_timeout_s for call in calls)


def test_agent_entry_legacy_listing_errors_and_timeouts_map_to_exit_75(tmp_path, monkeypatch):
    policy, _checkout = _policy(tmp_path)
    policy.legacy_jobs_dir.mkdir()
    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    service, _loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    service.admission._legacy_call = lambda callback: callback()

    monkeypatch.setattr(
        agent_providers.os, "listdir",
        lambda _fd: (_ for _ in ()).throw(PermissionError("listing denied")),
    )
    provider = service.admission._legacy_provider()
    with pytest.raises(AdmissionError) as listing_error:
        service.admission.book._legacy_job_ids(provider)
    assert listing_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN

    service.admission._legacy_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("query timed out"))
    with pytest.raises(AdmissionError) as timeout_error:
        service.admission.book._legacy_job_ids(service.admission._legacy_provider())
    assert timeout_error.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_agent_entry_maps_bounded_object_and_identity_inspector_failures(tmp_path, monkeypatch):
    _policy(tmp_path)
    monkeypatch.setattr(agent_entry, "installed_agent_version", lambda: "1.2.3")
    service, _loaded = agent_entry._service(tmp_path / "agent.toml", tmp_path / "state")
    service.admission._inspector_call = lambda _callback: (_ for _ in ()).throw(TimeoutError("inspect timed out"))

    assert service.admission._inspector_provider()("legacy-runner") is InspectorAnswer.ERROR
    with pytest.raises(AdmissionError) as identity_timeout:
        service.admission._verify_container_binding("controller-lease", "legacy-runner", _CONTAINER_ID)
    assert identity_timeout.value.exit_code == ExitCode.QUEUED_WITHDRAWN


def test_agent_policy_requires_an_absolute_legacy_jobs_directory(tmp_path):
    policy, _checkout = _policy(tmp_path)
    path = tmp_path / "agent.toml"
    original = path.read_text(encoding="utf-8")
    valid_line = f'legacy_jobs_dir = "{policy.legacy_jobs_dir}"'

    path.write_text(original.replace(valid_line + "\n", ""), encoding="utf-8")
    with pytest.raises(ContractError, match="missing key.*legacy_jobs_dir"):
        load_agent_policy(path)

    path.write_text(original.replace(valid_line, 'legacy_jobs_dir = "relative/jobs"'), encoding="utf-8")
    with pytest.raises(ContractError, match="legacy_jobs_dir: expected an absolute path"):
        load_agent_policy(path)
