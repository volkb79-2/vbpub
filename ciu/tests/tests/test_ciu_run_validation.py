"""CIU-141 validator, planner and helper branches (F3 hardening and coverage)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ciu import engine, oneshot


def _base(**updates):
    base = {
        "image": "python:3.11-slim",
        "volumes": [
            {"type": "bind", "source": "/repo", "target": "/workspace", "read_only": True},
        ],
        "environment": {"TOKEN": "x"},
    }
    base.update(updates)
    return base


def _validate(service, *, base=None, root=None, secrets=()):
    document = {"services": {"worker": service}}
    document.update(root or {})
    return oneshot.validate_extension_document(
        document, service_name="worker", base_service=base if base is not None else _base(),
        secret_values=set(secrets),
    )


def _bind(source, target="/mnt/extra", **extra):
    return {"volumes": [{"type": "bind", "source": source, "target": target, **extra}]}


# ---------------------------------------------------------------------------
# F3: privileged keys, dangerous bind sources, normalised targets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["cap_add", "security_opt"])
def test_privilege_widening_keys_are_refused_even_when_the_service_has_none(key):
    with pytest.raises(ValueError, match=f"refuses service key '{key}'"):
        _validate({key: ["x"]}, base={"image": "busybox"})


@pytest.mark.parametrize(
    "source",
    [
        "/", "//", "/./", "/.", "///",
        "/var/run/docker.sock", "/run/docker.sock", "//var//run//docker.sock",
        "/var/./run/docker.sock", "/run/./docker.sock",
        "/var/run", "/run", "/var", "/var/run/",
        "/srv/custom/docker.sock",
    ],
)
def test_bind_sources_that_expose_the_host_or_the_docker_socket_are_refused(source):
    with pytest.raises(ValueError, match="bind source"):
        _validate(_bind(source))


def test_bind_source_dotdot_is_refused_before_normalisation():
    with pytest.raises(ValueError, match="may not contain '..'"):
        _validate(_bind("/safe/../var/run/docker.sock"))


def test_symlinked_bind_sources_are_judged_by_where_they_point(tmp_path):
    to_root = tmp_path / "to-root"
    to_root.symlink_to("/")
    to_socket = tmp_path / "to-socket"
    to_socket.symlink_to("/run/docker.sock")
    to_var_run = tmp_path / "to-var-run"
    to_var_run.symlink_to("/var/run")
    for link in (to_root, to_socket, to_var_run):
        with pytest.raises(ValueError, match="bind source"):
            _validate(_bind(str(link)))
    harmless = tmp_path / "harmless"
    harmless.mkdir()
    validated = _validate(_bind(str(harmless)))
    assert validated["services"]["worker"]["volumes"][0]["source"] == str(harmless.resolve()) or (
        validated["services"]["worker"]["volumes"][0]["source"] == str(harmless)
    )


@pytest.mark.parametrize("source", ["/repo", "/repo/", "//repo//sub/./dir", "/run/observer"])
def test_ordinary_bind_sources_are_accepted_and_normalised(source):
    validated = _validate(_bind(source))
    mount = validated["services"]["worker"]["volumes"][0]
    assert mount["source"] == "/" + os.path.normpath(source).lstrip("/")
    assert mount["bind"] == {"create_host_path": False}


@pytest.mark.parametrize(
    "target", ["/workspace/", "//workspace", "/./workspace", "/workspace/.", "/workspace//"],
)
def test_volume_target_collisions_compare_normalised_paths(target):
    with pytest.raises(ValueError, match="collides with another mount"):
        _validate(_bind("/data", target))


def test_extension_targets_collide_with_each_other_after_normalisation():
    with pytest.raises(ValueError, match="collides with another mount"):
        _validate({"volumes": [
            {"type": "bind", "source": "/a", "target": "/data"},
            {"type": "bind", "source": "/b", "target": "/data/"},
        ]})


def test_volume_targets_are_stored_normalised():
    validated = _validate(_bind("/data", "//mnt//extra/"))
    assert validated["services"]["worker"]["volumes"][0]["target"] == "/mnt/extra"


def test_short_form_base_volumes_expose_their_real_target():
    base = _base(volumes=["/src:/workspace:ro", "named:/cache", "/anon"])
    with pytest.raises(ValueError, match="collides with another mount"):
        _validate(_bind("/data", "/cache"), base=base)
    with pytest.raises(ValueError, match="collides with another mount"):
        _validate(_bind("/data", "/anon"), base=base)
    assert _validate(_bind("/data", "/free"), base=base)


def test_volume_target_helper_edge_cases():
    assert oneshot._volume_target({"target": "/a"}, where="x") == "/a"
    for bad in (5, {"target": 5}, {"target": "rel"}, "rel", {"target": "/a/../b"}):
        with pytest.raises(ValueError, match="volume"):
            oneshot._volume_target(bad, where="x")


# ---------------------------------------------------------------------------
# Remaining extension-shape refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "match"),
    [
        (["not", "a", "map"], "must be a YAML mapping"),
        ({"services": {"worker": {}}, "bogus": 1}, "refuses top-level key 'bogus'"),
        ({"services": ["worker"]}, "exactly services.worker"),
        ({"services": {"other": {}}}, "exactly services.worker"),
        ({"services": {"worker": "text"}}, "must be a mapping"),
        ({"services": {"worker": {"init": "yes"}}}, "init must be a boolean"),
        ({"services": {"worker": {"group_add": "7"}}}, "numeric gids"),
        ({"services": {"worker": {"group_add": [True]}}}, "numeric gids"),
        ({"services": {"worker": {"group_add": ["abc"]}}}, "numeric gids"),
        ({"services": {"worker": {"group_add": [-3]}}}, "numeric gids"),
        ({"services": {"worker": {"volumes": "x"}}}, "list of long-form mounts"),
        ({"services": {"worker": {"volumes": ["/a:/b"]}}}, "long-form mappings"),
        ({"services": {"worker": {"volumes": [
            {"type": "tmpfs", "target": "/t"}]}}}, "must be 'bind' or 'volume'"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "target": "/t"}]}}}, "non-empty path/name"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "source": 5, "target": "/t"}]}}}, "non-empty path/name"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "source": "rel", "target": "/t"}]}}}, "must be absolute"),
        ({"services": {"worker": {"volumes": [
            {"type": "volume", "source": "a/b", "target": "/t"}]}}}, "must be a volume name"),
        ({"services": {"worker": {"volumes": [
            {"type": "volume", "source": "..", "target": "/t"}]}}}, "must be a volume name"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "source": "/a", "target": "/t", "read_only": "yes"}]}}},
         "read_only must be a boolean"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "source": "/a", "target": "/t", "bind": "x"}]}}},
         "bind options must be a mapping"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "source": "/a", "target": "/t", "bind": {"propagation": "rshared"}}]}}},
         "refuses bind option 'propagation'"),
        ({"services": {"worker": {"volumes": [
            {"type": "bind", "source": "/a", "target": "/t",
             "bind": {"create_host_path": True}}]}}}, "create_host_path must be false"),
        ({"services": {"worker": {"environment": ["A=1"]}}}, "map of literal values"),
        ({"services": {"worker": {"environment": {"TOKEN": "y"}}}}, "'TOKEN' collides"),
        ({"services": {"worker": {"environment": {"bad name": "y"}}}}, "variable names"),
        ({"services": {"worker": {"environment": {"A": 5}}}}, "literal strings"),
        ({"services": {"worker": {"environment": {"A": "$HOME"}}}}, "literal strings"),
        ({"services": {"worker": {"labels": "x"}}}, "must be a map or list"),
        ({"services": {"worker": {"labels": [5]}}}, "labels must be strings"),
        ({"services": {"worker": {"labels": {"bad key!": "v"}}}}, "invalid label key"),
        ({"services": {"worker": {"labels": {"k": True}}}}, "scalar strings"),
        ({"services": {"worker": {"labels": {"k": "$X"}}}}, "literal values"),
        ({"services": {"worker": {"labels": {"com.docker.compose.x": "v"}}}}, "reserved"),
        ({"services": {"worker": {}}, "x-ciu-run": "x"}, "only accepts observer-socket-dir"),
        ({"services": {"worker": {}}, "x-ciu-run": {"other": 1}}, "only accepts"),
        ({"services": {"worker": {}}, "x-ciu-run": {"observer-socket-dir": "rel"}},
         "must be an absolute path"),
        ({"services": {"worker": {}}, "x-ciu-run": {"observer-socket-dir": "/a/../b"}},
         "must be an absolute path"),
        ({"services": {"worker": {}}, "x-ciu-run": {"observer-socket-dir": 5}},
         "must be an absolute path"),
        ({"services": {"worker": {}}, "x-ciu-run": {"observer-socket-dir": "/run/o"}},
         "requires one matching read-only bind"),
    ],
)
def test_extension_shape_refusals(document, match):
    with pytest.raises(ValueError, match=match):
        oneshot.validate_extension_document(
            document, service_name="worker", base_service=_base(), secret_values=set(),
        )


def test_extension_accepts_label_list_scalars_and_a_matching_observer_mount():
    validated = _validate(
        {
            "labels": ["k=v", "bare", "dict=x"],
            "volumes": [
                {"type": "volume", "source": "cache", "target": "/cache"},
                {"type": "bind", "source": "/run/observer", "target": "/obs",
                 "read_only": True},
            ],
            "environment": {"NEW": "literal"},
        },
        root={"x-ciu-run": {"observer-socket-dir": "/run//observer/"}},
    )
    service = validated["services"]["worker"]
    assert service["labels"] == {"k": "v", "bare": "", "dict": "x"}
    assert service["volumes"][0] == {"type": "volume", "source": "cache", "target": "/cache"}
    assert service["volumes"][1]["read_only"] is True
    assert validated["x-ciu-run"] == {"observer-socket-dir": "/run//observer/"}
    numeric = _validate({"labels": {"count": 3, "ratio": 0.5}})
    assert numeric["services"]["worker"]["labels"] == {"count": "3", "ratio": "0.5"}


def test_base_environment_keys_accepts_list_and_ignores_other_shapes():
    assert oneshot._base_environment_keys(["A=1", "B", 5]) == {"A", "B"}
    assert oneshot._base_environment_keys(None) == set()
    with pytest.raises(ValueError, match="'A' collides"):
        _validate({"environment": {"A": "x"}}, base=_base(environment=["A=1"]))


def test_compose_labels_helper_accepts_none():
    assert oneshot._compose_labels(None, where="x") == {}


def test_secret_values_in_extension_text_are_refused_everywhere():
    with pytest.raises(ValueError, match="secret value"):
        _validate({"labels": {"planted-secret": "v"}}, secrets=["planted-secret"])
    with pytest.raises(ValueError, match="secret value"):
        _validate({"labels": ["k=planted-secret"]}, secrets=["planted-secret"])
    assert oneshot._secret_values(["", "a", None]) == ("a",)


# ---------------------------------------------------------------------------
# --extend files, plans and Compose config
# ---------------------------------------------------------------------------


def _prepared(tmp_path, **updates):
    prepared = SimpleNamespace(
        working_dir=tmp_path / "tools" / "runner",
        repo_root=tmp_path,
        project="demo-runner",
        file_args=["-f", "ciu.compose.yml"],
        compose_env={},
        rendered_compose="",
        ownership_path=None,
        config={},
        secret_values=frozenset({"planted-secret"}),
    )
    for key, value in updates.items():
        setattr(prepared, key, value)
    return prepared


def _compose_returning(monkeypatch, services, *, networks=None, seen=None):
    config = {"services": services}
    if networks is not None:
        config["networks"] = networks

    def run_cmd(argv, **kwargs):
        if seen is not None:
            seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps(config), "")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)


def test_read_extension_error_paths(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="does not exist"):
        oneshot._read_extension(
            tmp_path / "missing.yml", service_name="worker", base_service={},
            secret_values=set(),
        )
    unreadable = tmp_path / "unreadable.yml"
    unreadable.write_text("services: {}\n")
    real_read_text = Path.read_text

    def failing_read_text(self, *args, **kwargs):
        if self == unreadable.resolve():
            raise OSError("denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", failing_read_text)
    with pytest.raises(ValueError, match="cannot read --extend file"):
        oneshot._read_extension(
            unreadable, service_name="worker", base_service={}, secret_values=set(),
        )
    monkeypatch.undo()
    leaky = tmp_path / "leaky.yml"
    leaky.write_text("services:\n  worker:\n    labels:\n      k: planted-secret\n")
    with pytest.raises(ValueError, match="file contains a CIU secret value"):
        oneshot._read_extension(
            leaky, service_name="worker", base_service={}, secret_values={"planted-secret"},
        )
    broken = tmp_path / "broken.yml"
    broken.write_text("services: [unclosed\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        oneshot._read_extension(
            broken, service_name="worker", base_service={}, secret_values=set(),
        )
    good = tmp_path / "good.yml"
    good.write_text("services:\n  worker:\n    init: true\n")
    path, validated = oneshot._read_extension(
        good, service_name="worker", base_service={}, secret_values=set(),
    )
    assert path == good.resolve()
    assert validated["services"]["worker"] == {"init": True}


def test_plan_with_an_extension_file_resolves_the_merged_service(tmp_path, monkeypatch):
    extension = tmp_path / "extend.yml"
    extension.write_text("services:\n  worker:\n    init: true\n    mem_limit: 256m\n")
    seen = []
    _compose_returning(monkeypatch, {"worker": {"image": "busybox"}}, seen=seen)

    plan = oneshot.build_run_plan(
        _prepared(tmp_path), stack="tools/runner", service="worker", extend=extension,
    )

    assert plan["extend_accepted"] is True
    with_extension = [
        argv for argv in seen if any(token.endswith("/extend.yml") for token in argv)
    ]
    assert len(with_extension) == 1 and seen[0] != with_extension[0]
    extra = with_extension[0]
    assert extra[-3:] == ["config", "--format", "json"]
    token = next(item for item in extra if item.endswith("/extend.yml"))
    assert extra[extra.index(token) - 1] == "-f"


def test_plan_refuses_duplicate_labels_reserved_service_labels_and_bad_shapes(
    tmp_path, monkeypatch,
):
    prepared = _prepared(tmp_path)
    _compose_returning(monkeypatch, {"worker": {"image": "x"}})
    with pytest.raises(ValueError, match="label keys must be unique"):
        oneshot.build_run_plan(
            prepared, stack="s", service="worker", labels=["a=1", "a=2"],
        )
    _compose_returning(monkeypatch, {"worker": {"labels": {"ciu.run.id": "x"}}})
    with pytest.raises(ValueError, match="service label 'ciu.run.id' is reserved"):
        oneshot.build_run_plan(prepared, stack="s", service="worker")
    _compose_returning(monkeypatch, {"worker": {"networks": 5}})
    with pytest.raises(ValueError, match="networks have an invalid shape"):
        oneshot.build_run_plan(prepared, stack="s", service="worker")
    _compose_returning(monkeypatch, {"worker": {"volumes": ["x"]}})
    with pytest.raises(ValueError, match="volume has an invalid shape"):
        oneshot.build_run_plan(prepared, stack="s", service="worker")
    _compose_returning(monkeypatch, {"worker": {"networks": ["b-net", "a-net"]}})
    plan = oneshot.build_run_plan(prepared, stack="s", service="worker")
    assert plan["networks"] == ["a-net", "b-net"]


def test_compose_config_and_service_lookup_shape_errors(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)

    def respond(stdout):
        monkeypatch.setattr(
            oneshot.procutil, "run_cmd",
            lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout, ""),
        )

    respond("not json")
    with pytest.raises(RuntimeError, match="invalid JSON config"):
        oneshot._compose_config(prepared)
    respond(json.dumps({"services": []}))
    with pytest.raises(RuntimeError, match="no services mapping"):
        oneshot._compose_config(prepared)
    respond(json.dumps(["x"]))
    with pytest.raises(RuntimeError, match="no services mapping"):
        oneshot._compose_config(prepared)
    with pytest.raises(ValueError, match="has no services"):
        oneshot._service_from_config({"services": []}, "worker")


def test_compose_config_adds_the_extension_file_argument(tmp_path, monkeypatch):
    seen = []
    _compose_returning(monkeypatch, {"worker": {}}, seen=seen)
    oneshot._compose_config(_prepared(tmp_path), extension_path=tmp_path / "e.yml")
    assert seen[0][-5:-2] == ["-f", str(tmp_path / "e.yml"), "config"]


# ---------------------------------------------------------------------------
# Per-run compose files
# ---------------------------------------------------------------------------


def test_compose_file_args_handle_ownership_overlay_variants(tmp_path):
    working = tmp_path / "tools" / "runner"
    (working / ".machine").mkdir(parents=True)
    ownership = working / ".machine" / "own.yml"
    ownership.write_text("services: {}\n")
    private = tmp_path / "private"
    private.mkdir()

    # relative reference resolved against the working dir is replaced
    prepared = _prepared(
        tmp_path, ownership_path=ownership,
        file_args=["--profile", "x", "-f", "a.yml", "-f", ".machine/own.yml"],
    )
    args = oneshot._compose_file_args(prepared, temp_dir=private, extend=None, service_name="w")
    assert args[:4] == ["--profile", "x", "-f", "a.yml"]
    assert args[-1] == str(private / "ownership.yml")

    # an absolute reference is replaced too
    prepared.file_args = ["-f", str(ownership)]
    args = oneshot._compose_file_args(prepared, temp_dir=private, extend=None, service_name="w")
    assert args == ["-f", str(private / "ownership.yml")]

    # an overlay Compose was not told about is appended
    prepared.file_args = ["-f", "a.yml"]
    args = oneshot._compose_file_args(prepared, temp_dir=private, extend=None, service_name="w")
    assert args == ["-f", "a.yml", "-f", str(private / "ownership.yml")]

    # an unreadable ownership overlay is refused
    prepared.ownership_path = working / ".machine" / "gone.yml"
    with pytest.raises(ValueError, match="cannot read prepared ownership overlay"):
        oneshot._compose_file_args(prepared, temp_dir=private, extend=None, service_name="w")


def test_compose_file_args_validate_and_copy_the_extension(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    extension = tmp_path / "extend.yml"
    extension.write_text("services:\n  worker:\n    init: true\n")
    _compose_returning(monkeypatch, {"worker": {"image": "busybox"}})

    args = oneshot._compose_file_args(
        _prepared(tmp_path), temp_dir=private, extend=extension, service_name="worker",
    )

    assert args[-2:] == ["-f", str(private / "extend.yml")]
    assert "init: true" in (private / "extend.yml").read_text()


def test_run_compose_argv_without_cgroup_parent_or_tty(tmp_path):
    argv = oneshot._run_compose_argv(
        _prepared(tmp_path), file_args=["-f", "a.yml"], service="worker", argv=["x", 1],
        name="n-1", labels={"a": "b"}, env_names=["E"], run_id="rid", cgroup_parent=None,
        detach=False, interactive=False,
    )
    assert "CIU_RUN_CGROUP_PARENT" not in " ".join(argv)
    assert argv[-3:] == ["worker", "x", "1"]
    assert "-d" not in argv
    assert argv[argv.index("-l") + 1] == "a=b"
    with pytest.raises(ValueError, match="invalid environment variable name"):
        oneshot._run_compose_argv(
            _prepared(tmp_path), file_args=[], service="w", argv=["x"], name="n",
            labels={}, env_names=["bad name"], run_id="r", cgroup_parent=None,
            detach=False, interactive=True,
        )


def test_docker_rm_reports_failure_and_missing_docker(monkeypatch):
    monkeypatch.setattr(
        oneshot.procutil, "run_cmd",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", ""),
    )
    assert oneshot._run_docker_rm("a" * 64) is False

    def no_docker(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(oneshot.procutil, "run_cmd", no_docker)
    assert oneshot._run_docker_rm("a" * 64) is False


# ---------------------------------------------------------------------------
# Ownership identity, lock and event-shape helpers
# ---------------------------------------------------------------------------


def test_owner_identity_refusals(monkeypatch):
    for bad in (0, -1, True, "7", None):
        with pytest.raises(ValueError, match="positive integer"):
            oneshot._owner_identity(bad)
    with pytest.raises(ValueError, match="cannot read the liveness identity"):
        oneshot._owner_identity(2_000_000_000)
    real_read_text = Path.read_text

    def empty_boot_id(self, *args, **kwargs):
        if str(self) == "/proc/sys/kernel/random/boot_id":
            return "\n"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", empty_boot_id)
    with pytest.raises(ValueError, match="boot ID is unavailable"):
        oneshot._owner_identity(os.getpid())


def test_stack_prepare_lock_refusals(tmp_path):
    symlinked = tmp_path / "linked"
    symlinked.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (symlinked / ".machine").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="symlinked preparation directory"), (
        oneshot._stack_prepare_lock(symlinked)
    ):
        pytest.fail("must not lock")

    linked_lock = tmp_path / "stack2"
    (linked_lock / ".machine").mkdir(parents=True)
    (linked_lock / ".machine" / ".ciu-run.lock").symlink_to(tmp_path / "target-file")
    with pytest.raises(RuntimeError, match="cannot open preparation lock"), (
        oneshot._stack_prepare_lock(linked_lock)
    ):
        pytest.fail("must not lock")

    fifo_stack = tmp_path / "stack3"
    (fifo_stack / ".machine").mkdir(parents=True)
    os.mkfifo(fifo_stack / ".machine" / ".ciu-run.lock")
    with pytest.raises(ValueError, match="not a regular file"), (
        oneshot._stack_prepare_lock(fifo_stack)
    ):
        pytest.fail("must not lock")


def test_event_label_and_launch_event_shape_errors(tmp_path):
    with pytest.raises(RuntimeError, match="labels have an invalid shape"):
        oneshot._event_labels({"Config": {"Labels": "x"}})
    assert oneshot._event_labels({"Config": None}) == {}
    assert oneshot._event_labels({"Config": {"Labels": None}}) == {}
    with pytest.raises(RuntimeError, match="configuration has an invalid shape"):
        oneshot._launched_event(
            {"Id": "a" * 64, "Config": {}, "HostConfig": "x"}, run_id="r", name="n",
            prepared=_prepared(tmp_path), stack="s", service="w", plan={},
        )
    event = oneshot._launched_event(
        {"Id": "a" * 64}, run_id="r", name="n", prepared=_prepared(tmp_path), stack="s",
        service="w", plan={"image": "planned:image"},
    )
    assert event["image"] == "planned:image" and event["networks"] == []


# ---------------------------------------------------------------------------
# Network ownership verification
# ---------------------------------------------------------------------------


def _network_world(monkeypatch, tmp_path, *, services=None, networks=None, inspect=None):
    prepared = _prepared(tmp_path, working_dir=tmp_path)
    monkeypatch.setattr(engine, "verify_workspace_runtime_network", lambda *_a: None)
    config = {
        "services": services or {"worker": {"networks": {"net": {}}}},
        "networks": networks if networks is not None else {"net": {"name": "demo_net"}},
    }

    def run_cmd(argv, **kwargs):
        argv = list(argv)
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(config), "")
        return inspect(argv)

    monkeypatch.setattr(oneshot.procutil, "run_cmd", run_cmd)
    return prepared


def _ok(stdout):
    return lambda argv: subprocess.CompletedProcess(argv, 0, stdout, "")


def test_network_verification_failure_modes(tmp_path, monkeypatch):
    owned = json.dumps([{"Name": "demo_net", "Labels": {
        "com.docker.compose.project": "demo-runner", "com.docker.compose.network": "net",
    }}])

    prepared = _network_world(monkeypatch, tmp_path, inspect=_ok(owned))
    oneshot._verify_run_networks(prepared, "worker")  # compose-owned network: accepted

    prepared = _network_world(
        monkeypatch, tmp_path, inspect=_ok(json.dumps([{"Name": "demo_net", "Labels": {}}])),
    )
    with pytest.raises(ValueError, match="is not owned by Compose project"):
        oneshot._verify_run_networks(prepared, "worker")

    prepared = _network_world(
        monkeypatch, tmp_path, inspect=_ok(json.dumps([{"Name": "demo_net", "Labels": "x"}])),
    )
    with pytest.raises(ValueError, match="is not owned by Compose project"):
        oneshot._verify_run_networks(prepared, "worker")

    prepared = _network_world(
        monkeypatch, tmp_path, networks={"net": {"name": "demo_net", "external": True}},
        inspect=_ok(json.dumps([{"Name": "demo_net"}])),
    )
    oneshot._verify_run_networks(prepared, "worker")  # external networks carry no compose labels

    for stdout, match in (
        ("not json", "invalid network details"),
        (json.dumps([{}, {}]), "invalid network details"),
        (json.dumps([5]), "invalid network details"),
        (json.dumps(5), "did not prove exact network"),
        (json.dumps([{"Name": "other"}]), "did not prove exact network"),
        (json.dumps({"Name": "other"}), "did not prove exact network"),
    ):
        prepared = _network_world(monkeypatch, tmp_path, inspect=_ok(stdout))
        with pytest.raises(RuntimeError, match=match):
            oneshot._verify_run_networks(prepared, "worker")

    def no_docker(argv):
        raise FileNotFoundError("docker")

    prepared = _network_world(monkeypatch, tmp_path, inspect=no_docker)
    with pytest.raises(RuntimeError, match="Docker unavailable while checking"):
        oneshot._verify_run_networks(prepared, "worker")

    prepared = _network_world(monkeypatch, tmp_path, networks={}, inspect=_ok("[]"))
    with pytest.raises(ValueError, match="cannot prove the resolved Docker network name"):
        oneshot._verify_run_networks(prepared, "worker")
    prepared = _network_world(
        monkeypatch, tmp_path, networks={"net": {"name": 5}}, inspect=_ok("[]"),
    )
    with pytest.raises(ValueError, match="cannot prove the resolved Docker network name"):
        oneshot._verify_run_networks(prepared, "worker")

    prepared = _network_world(
        monkeypatch, tmp_path, services={"worker": {"networks": 5}}, inspect=_ok("[]"),
    )
    with pytest.raises(ValueError, match="network declaration is invalid"):
        oneshot._verify_run_networks(prepared, "worker")

    prepared = _network_world(
        monkeypatch, tmp_path, services={"worker": {"networks": ["net"]}},
        networks="not a mapping", inspect=_ok("[]"),
    )
    with pytest.raises(ValueError, match="cannot prove the resolved Docker network name"):
        oneshot._verify_run_networks(prepared, "worker")


def test_network_verification_wraps_unexpected_workspace_errors(tmp_path, monkeypatch):
    prepared = _prepared(tmp_path)

    def broken(*_args):
        raise KeyError("boom")

    monkeypatch.setattr(engine, "verify_workspace_runtime_network", broken)
    with pytest.raises(RuntimeError, match="could not verify managed instance network"):
        oneshot._verify_run_networks(prepared, "worker")

    def refused(*_args):
        raise ValueError("not a managed network")

    monkeypatch.setattr(engine, "verify_workspace_runtime_network", refused)
    with pytest.raises(ValueError, match="not a managed network"):
        oneshot._verify_run_networks(prepared, "worker")
