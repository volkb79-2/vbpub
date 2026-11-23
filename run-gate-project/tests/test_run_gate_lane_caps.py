"""Behavioral oracles for P7.0a selected lane caps (Run-Gate D-001).

The suite uses real subprocesses at every environment boundary it can, but
never a live Docker daemon. The executing shim below models Docker's name-only
and explicit-empty ``-e`` semantics: a child starts with a deliberately wrong
image-baked sentinel and only the emitted ``-e`` assignments can replace it.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import tomllib
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from test_run_gate import (
    RUN_GATE_DIR,
    TestUserDocumentationOracles,
    _docker_calls,
    commit_all,
    dead_owner_fields,
    docker_execs,
    docker_runs,
    fake_docker_stateful,
    lane_runs,
    lane_slot,
    live_owner_fields,
    make_repo,
    plant_inflight,
    run_gate,
    shim_dir_of,
    state_containers,
)


CAP = run_gate.LANE_CAPS_ENV
RECORD = run_gate.LANE_CAPS_RECORD_FIELD
OBSERVER_CONTROL_NAMES = (
    "P7A_TARGET_LANE",
    "P7A_SOCKET_PATH",
    "P7A_EXPECTED_IMAGE_ID",
    "P7A_RECEIPT_PATH",
    "P7A_REAL_DOCKER",
    "P7A_EXPECTED_EXEC_CONTAINER_ID",
    "P7A_EXPECTED_EXEC_CONTAINER_NAME",
    "P7A_EXPECTED_EXEC_CIU_INSTANCE",
    "P7A_EXPECTED_EXEC_CIU_REPO_ROOT",
    "P7A_EXPECTED_EXEC_COMPOSE_SERVICE",
)
AMBIENT_SENTINEL = "AMBIENT_P7A_UNIT_SENTINEL"
BAKED_SENTINEL = "BAKED_P7A_UNIT_SENTINEL"
D001_EXACT = """\
## D-001 — Selected lane caps as an exact launch and reattachment fact

- Date / decided by: 2026-11-22, Codex controller after GPT-6-Sol xhigh design review at operator direction.
- Decision:
  - RUN_GATE_LANE_CAPS_V1 is a Run-Gate-owned fact derived only from the selected parsed Run-Gate lane and selected parsed Run-Gate environment.
  - Eligibility requires ephemeral mode, a positive effective memory cap, and a positive integer resources.pids cap. No host, image, swap, or ambient fallback is permitted.
  - Eligible delivery is one compact four-field JSON value, sent through the Docker client environment and name-only Docker -e option. Ineligible ephemeral, exec, bare-host, and probe paths clear or explicitly empty the name.
  - Schema-2 inflight records store the exact fact string for eligible ephemeral runs and JSON null for ineligible ephemeral and exec runs. Missing is distinct from null. Non-fresh reattachment requires exact stored/current equality before following, adopting, Docker inspection, verdict access, or record mutation.
  - Preserve foreign-runner and Assay-artifact refusal priority. Check selected effective caller-name collisions before reading image overrides or beginning preflight, inventory, Docker, or child work.
  - A fresh run may remove a prior container only when the owner is positively proven dead and the inspected Docker container ID equals the recorded ID. Missing or unreadable identity is unknown and refuses removal.
  - Every unknown-schema inflight record refuses, including under --fresh, even if familiar-looking owner or container fields appear dead. This is a deliberate recovery-contract change: use a client that understands the record schema; otherwise the lifecycle owner verifies the run and identity, handles any container through its owning lifecycle tooling, and removes the record only after verification. This client must not prescribe --fresh for unknown-schema recovery.
  - Test and merge the exact candidate wheel before any public release. Product lanes cover eligible and ineligible ephemeral delivery and Run-Gate-to-Assay transport. A separate CIU-managed persistent exec fixture must provide a real docker exec child observation before merge. This product change does not prove CMRU consumption.
- Why: a lane fact must report only declared Docker caps actually selected by Run-Gate, survive delivery without ambient or image values taking precedence, and refuse reattachment across configuration drift. Owner uncertainty must never be treated as death. The real container boundaries need acceptance evidence separate from fake-Docker construction tests.
- Binds: the P7.0a interface and source precedence; collision and refusal order; Docker and host environment handling; schema-2 record field; --fresh owner and container-ID safety; registered ephemeral acceptance lanes; synchronized image-identity acceptance; pre-merge candidate-wheel and CIU exec receipts; the SPEC recovery contract; and synchronized README/DESIGN-GUIDE/CONSUMERS documentation.
- Consequences: legacy schema-2 records without lane_caps_v1 remain visible in status but cannot be non-freshly reattached. --fresh may recover them only with a provably dead owner and a matching container identity; unknown identity refuses. Unknown-schema records now refuse --fresh, changing the prior name-only escape: update SPEC.md and its recovery tests, and direct operators to a schema-aware client or lifecycle-owner verification and handling. Public wheel release requires separate operator approval after live acceptance."""


@pytest.fixture(autouse=True)
def lane_caps_test_isolation(monkeypatch, tmp_path_factory):
    """Keep the P7a suite independent of host gate/profile configuration."""
    fake = tmp_path_factory.mktemp("p7a-isolation")
    monkeypatch.setenv(run_gate.CGROUP_ENV_VAR, "dev-gates.slice")
    monkeypatch.setenv(run_gate.PROFILE_AMBIENT_ENV_VAR, "off")
    monkeypatch.setenv("CGPROFILE_CONTROL_SOCKET", str(fake / "control.sock"))
    monkeypatch.setenv("CGPROFILE_OBSERVER_SOCKET", str(fake / "observer.sock"))
    monkeypatch.delenv(CAP, raising=False)
    monkeypatch.delenv(run_gate.HOST_IMAGE_ENV_VAR, raising=False)
    monkeypatch.delenv(run_gate.MOUNT_ALIAS_ENV_VAR, raising=False)
    for name in OBSERVER_CONTROL_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name in ("REQUIRED_P7A", "REQUIRED_P7A_EMPTY"):
        monkeypatch.delenv(name, raising=False)


def _expected(lane: str, *, environment: str = "tester-unified",
              memory: int = 2147483648, pids: int = 8192) -> str:
    return json.dumps({
        "environment": environment,
        "lane": lane,
        "memory_max_bytes": memory,
        "pids_max": pids,
    }, ensure_ascii=True, separators=(",", ":"))


def _history_lane_names(project: Path) -> set[str]:
    path = project / ".run-gate" / "history.json"
    if not path.is_file():
        return set()
    document = json.loads(path.read_text(encoding="utf-8"))
    return set(document.get("lanes", {}))


def _observer_script(repo: Path) -> Path:
    script = repo / "observe_lane_caps.py"
    script.write_text(textwrap.dedent(f"""\
        import json
        from pathlib import Path
        import os
        import sys

        cap = {CAP!r}
        observer = {list(OBSERVER_CONTROL_NAMES)!r}
        payload = {{
            "present": cap in os.environ,
            "value": os.environ.get(cap) if cap in os.environ else None,
            "observer_controls": [name for name in observer if name in os.environ],
        }}
        output = Path(sys.argv[1])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, sort_keys=True))
        print(json.dumps(payload, sort_keys=True))
        """))
    commit_all(repo, "lane-caps observer")
    return script


def _set_observer_controls(monkeypatch) -> None:
    for name in OBSERVER_CONTROL_NAMES:
        monkeypatch.setenv(name, f"P7A_CONTROL_SENTINEL_{name}")


def _make_single_project(
        tmp_path: Path, *, environment: str = "tester-unified",
        extra_lane_fields: str = "", resources: str = 'memory = "2g"\npids = 8192',
        environment_fields: str = "") -> tuple[Path, Path, Path, Path]:
    repo = make_repo(tmp_path)
    script = _observer_script(repo)
    artifact = repo / ".run-gate" / "caps-observed.json"
    config = f"""\
        schema_version = 1

        [environments.tester-unified]
        mode = "ephemeral"
        image = "tester-unified:local"
        {textwrap.indent(environment_fields, "        ").lstrip()}

        [lanes.caps]
        kind = "command"
        environment = "{environment}"
        argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
        clean_tree = false
        {extra_lane_fields}

        [lanes.caps.resources]
        {resources}
        """
    project = repo / "proj"
    project.mkdir()
    (project / "run-gate.toml").write_text(textwrap.dedent(config))
    commit_all(repo, "lane-caps config")
    return repo, project, script, artifact


def _make_sequence_project(
        tmp_path: Path, bad_lane_fields: str, *,
        bad_environment: str = "caps",
        bad_resources: str = 'memory = "2g"\npids = 8192',
        extra_environments: str = "",
        environment_fields: str = "") -> tuple[Path, Path, Path, Path]:
    repo = make_repo(tmp_path)
    script = _observer_script(repo)
    first_artifact = repo / ".run-gate" / "first-observed.json"
    bad_artifact = repo / ".run-gate" / "bad-observed.json"
    config = f"""\
        schema_version = 1

        [environments.caps]
        mode = "ephemeral"
        image = "tester-unified:local"
        {textwrap.indent(environment_fields, "        ").lstrip()}
        {textwrap.indent(extra_environments, "        ").lstrip()}

        [lanes.first]
        kind = "command"
        environment = "caps"
        argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(first_artifact))}]
        clean_tree = false

        [lanes.first.resources]
        memory = "1g"
        pids = 111

        [lanes.bad]
        kind = "command"
        environment = "{bad_environment}"
        argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(bad_artifact))}]
        clean_tree = false
        {textwrap.indent(bad_lane_fields, "        ").lstrip()}

        [lanes.bad.resources]
        {bad_resources}

        [lanes.nested]
        kind = "sequence"
        lanes = ["bad"]
        stop_on = "FAIL"

        [lanes.gate]
        kind = "sequence"
        lanes = ["first", "nested"]
        stop_on = "FAIL"
        """
    project = repo / "proj"
    project.mkdir()
    (project / "run-gate.toml").write_text(textwrap.dedent(config))
    commit_all(repo, "lane-caps sequence config")
    return repo, project, first_artifact, bad_artifact


def _dispatch(monkeypatch, project: Path, *argv: str):
    monkeypatch.chdir(project)
    monkeypatch.setattr(run_gate, "physical_path",
                        lambda path, **kwargs: Path("/phys"))
    return run_gate._dispatch(list(argv))


def _capture_inflight_writes(monkeypatch) -> list[dict]:
    payloads: list[dict] = []
    real = run_gate.write_inflight_record

    def capture(project_dir, worktree, lane_name, payload):
        payloads.append(copy.deepcopy(payload))
        return real(project_dir, worktree, lane_name, payload)

    monkeypatch.setattr(run_gate, "write_inflight_record", capture)
    return payloads


def _fake_admission(monkeypatch) -> list[str]:
    events: list[str] = []

    class Ticket:
        name = "p7a-ticket"
        run_deadline = 1_900_000_000.0

        @staticmethod
        def result():
            return {"name": "p7a-ticket"}

    class Manager:
        @staticmethod
        def release(_ticket):
            events.append("release")

    def admit(*_args, **_kwargs):
        events.append("admit")
        ticket = Ticket()
        return Manager(), ticket, ticket.result(), 1.0, 1.0, True

    monkeypatch.setattr(run_gate, "_admit_lane", admit)
    return events


def _executing_docker(tmp_path: Path, monkeypatch,
                      *, target_container: str = "persistent-runner"
                      ) -> tuple[Path, Path, Path]:
    """A stateful fake Docker that executes ``bash -c`` child bodies.

    Its container base environment deliberately contains BAKED_SENTINEL, not
    the test process environment. Therefore omission of the explicit ``-e``
    would expose the baked value, while name-only gets the client value and
    ``NAME=`` gets exactly empty.
    """
    directory = shim_dir_of(monkeypatch)
    trace = tmp_path / "docker-trace.jsonl"
    state = tmp_path / "docker-state"
    state.mkdir(exist_ok=True)
    shim = directory / "docker"
    shim.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys

        CAP = {CAP!r}
        TRACE = Path({str(trace)!r})
        STATE = Path({str(state)!r})
        TARGET = {target_container!r}
        BAKED = {BAKED_SENTINEL!r}
        OBSERVER = {list(OBSERVER_CONTROL_NAMES)!r}
        VALUE_FLAGS = {{
            "--name", "--cgroup-parent", "--network", "--label", "--user",
            "--memory", "--memory-swap", "--pids-limit", "--cpus", "-v",
            "--mount", "--workdir", "--format", "--env",
        }}

        def trace(kind, arguments, client, child=None, code=None):
            payload = {{
                "kind": kind,
                "argv": list(arguments),
                "client_cap_present": CAP in client,
                "client_cap": client.get(CAP),
                "observer_names_in_client": [
                    name for name in OBSERVER if name in client],
                "child_cap_present": None,
                "child_cap": None,
                "observer_names_in_child": [],
                "exit_code": code,
            }}
            if child is not None:
                payload.update({{
                    "child_cap_present": CAP in child,
                    "child_cap": child.get(CAP),
                    "observer_names_in_child": [
                        name for name in OBSERVER if name in child],
                }})
            with TRACE.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, sort_keys=True) + "\\n")

        def take_assignment(assignment, client, child):
            if "=" in assignment:
                name, value = assignment.split("=", 1)
                child[name] = value
            elif assignment in client:
                child[assignment] = client[assignment]
            else:
                child.pop(assignment, None)

        def run_parser(rest):
            index = 0
            name = None
            detached = False
            assignments = []
            while index < len(rest):
                token = rest[index]
                if token == "-d":
                    detached = True
                    index += 1
                    continue
                if token == "--name":
                    name = rest[index + 1]
                    index += 2
                    continue
                if token == "-e":
                    assignments.append(rest[index + 1])
                    index += 2
                    continue
                if token.startswith("-"):
                    if token in VALUE_FLAGS:
                        index += 2
                    else:
                        index += 1
                    continue
                return name, detached, assignments, index
            raise SystemExit("fake docker: run has no image")

        def execution_child(client, assignments, container):
            child = {{
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", str(STATE)),
                "USER": os.environ.get("USER", "unit"),
            }}
            if container:
                child[CAP] = BAKED
            for assignment in assignments:
                take_assignment(assignment, client, child)
            return child

        def execute(tokens, client, assignments, *, container):
            child = execution_child(client, assignments, container)
            trace("execute", tokens, client, child)
            try:
                result = subprocess.run(
                    tokens, cwd=None, env=child, capture_output=True,
                    text=True)
            except OSError as exc:
                trace("spawn-error", tokens, client, child, 127)
                print(str(exc), file=sys.stderr)
                return 127, "", str(exc)
            trace("executed", tokens, client, child, result.returncode)
            return result.returncode, result.stdout, result.stderr

        def state_id(name):
            return "sha256:fakeid-" + name

        def write_state(name, status, code):
            (STATE / name).write_text(
                json.dumps({{
                    "status": status,
                    "exit_code": code,
                    "finished_at": "2026-11-23T00:00:01Z",
                    "started_at": "2026-11-23T00:00:00Z",
                    "id": state_id(name),
                }}, sort_keys=True))

        arguments = sys.argv[1:]
        client = dict(os.environ)
        if not arguments:
            trace("empty", arguments, client)
            raise SystemExit(0)
        command, rest = arguments[0], arguments[1:]
        trace("call", arguments, client)

        if command == "ps":
            print(TARGET)
            raise SystemExit(0)
        if command == "image":
            raise SystemExit(0)
        if command == "run":
            name, detached, assignments, image_at = run_parser(rest)
            tokens = rest[image_at + 1:]
            if detached:
                if not name:
                    raise SystemExit("fake docker: detached run has no --name")
                write_state(name, "running", 0)
                code, stdout, stderr = execute(
                    tokens, client, assignments, container=True)
                write_state(name, "exited", code)
                (STATE / (name + ".stream")).write_text(stdout + stderr)
                if code != 0:
                    print(stderr, file=sys.stderr)
                print(state_id(name))
                raise SystemExit(0)
            code, stdout, stderr = execute(
                tokens, client, assignments, container=True)
            sys.stdout.write(stdout)
            sys.stderr.write(stderr)
            raise SystemExit(code)
        if command == "exec":
            index = 0
            assignments = []
            container = None
            while index < len(rest):
                token = rest[index]
                if token == "-e":
                    assignments.append(rest[index + 1])
                    index += 2
                    continue
                if token in VALUE_FLAGS:
                    index += 2
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                container = token
                index += 1
                break
            if not container:
                raise SystemExit("fake docker: exec has no container")
            code, stdout, stderr = execute(
                rest[index:], client, assignments, container=True)
            sys.stdout.write(stdout)
            sys.stderr.write(stderr)
            raise SystemExit(code)
        if command == "inspect":
            name = rest[-1]
            state_path = STATE / name
            if not state_path.is_file():
                print(f"Error: No such object: {{name}}", file=sys.stderr)
                raise SystemExit(1)
            document = json.loads(state_path.read_text())
            format_text = rest[rest.index("--format") + 1] \\
                if "--format" in rest else (
                    rest[rest.index("-f") + 1] if "-f" in rest else "")
            if "{{{{.Id}}}}" == format_text:
                print("a" * 64)
            elif "{{{{.Image}}}}" == format_text:
                print("sha256:" + "b" * 64)
            elif "{{{{.State.Status}}}}" in format_text:
                print("|".join([
                    document["status"], str(document["exit_code"]),
                    document["finished_at"], document["started_at"],
                    document["id"],
                ]))
            else:
                print(document["id"])
            raise SystemExit(0)
        if command == "logs":
            name = rest[-1]
            stream_path = STATE / (name + ".stream")
            if stream_path.is_file():
                sys.stdout.write(stream_path.read_text())
            raise SystemExit(0)
        if command == "wait":
            name = rest[-1]
            state_path = STATE / name
            if not state_path.is_file():
                print("Error: No such container", file=sys.stderr)
                raise SystemExit(1)
            print(json.loads(state_path.read_text())["exit_code"])
            raise SystemExit(0)
        if command == "rm":
            name = rest[-1]
            (STATE / name).unlink(missing_ok=True)
            (STATE / (name + ".stream")).unlink(missing_ok=True)
            raise SystemExit(0)
        # Read-only probes outside this change return a benign answer.
        raise SystemExit(0)
        """))
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{directory}:{os.environ['PATH']}")
    return trace, state, shim


def _trace_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _json_calls(path: Path) -> list[list[str]]:
    return [row["argv"] for row in _trace_rows(path)
            if row["kind"] == "call"]


def _json_lane_runs(path: Path) -> list[list[str]]:
    return [argv for argv in _json_calls(path)
            if argv and argv[0] == "run" and "-d" in argv]


def _json_docker_execs(path: Path) -> list[list[str]]:
    return [argv for argv in _json_calls(path)
            if argv and argv[0] == "exec"]


class TestLaneCapsFact:
    def test_exact_serialization_order_ascii_and_no_newline(self):
        value = run_gate.lane_caps_v1(
            "tester-unified", "p7a-lane-caps-smoke", "ephemeral", "2g", 8192)
        assert value == (
            '{"environment":"tester-unified","lane":"p7a-lane-caps-smoke",'
            '"memory_max_bytes":2147483648,"pids_max":8192}')
        assert "\n" not in value and value.encode("utf-8") == value.encode("ascii")
        assert list(json.loads(value)) == [
            "environment", "lane", "memory_max_bytes", "pids_max"]

    def test_unicode_names_are_ascii_escaped_wire_bytes(self):
        value = run_gate.lane_caps_v1("tést", "café", "ephemeral", "2g", 8192)
        assert value == (
            '{"environment":"t\\u00e9st","lane":"caf\\u00e9",'
            '"memory_max_bytes":2147483648,"pids_max":8192}')

    def test_effective_memory_precedence_and_no_non_docker_source(self):
        lane = {
            "memory": "512m",
            "resources": {
                "memory": "2g",
                "memory_swap": "16g",
                "memory_max": "9g",
                "pids": 8193,
            },
        }
        assert run_gate.expected_lane_caps_v1(
            "caps", lane, "env", "ephemeral") == _expected(
                "caps", environment="env", pids=8193)
        del lane["resources"]["memory"]
        assert json.loads(run_gate.expected_lane_caps_v1(
            "caps", lane, "env", "ephemeral"))["memory_max_bytes"] \
            == 536870912
        assert run_gate.expected_lane_caps_v1(
            "caps", {"resources": {"memory_swap": "16g", "pids": 8192}},
            "env", "ephemeral") is None

    @pytest.mark.parametrize("mode", ["exec", "host", None, "EPHEMERAL"])
    def test_non_ephemeral_mode_has_no_fact(self, mode):
        assert run_gate.lane_caps_v1("env", "caps", mode, "2g", 8192) is None

    @pytest.mark.parametrize("memory", [None, "", "0", 0, 2048, True])
    def test_absent_or_non_string_or_zero_memory_has_no_fact(self, memory):
        assert run_gate.lane_caps_v1(
            "env", "caps", "ephemeral", memory, 8192) is None

    @pytest.mark.parametrize("pids", [None, 0, -1, True, 8192.0, "8192"])
    def test_non_positive_or_non_integer_pid_cap_has_no_fact(self, pids):
        assert run_gate.lane_caps_v1(
            "env", "caps", "ephemeral", "2g", pids) is None

    def test_malformed_memory_retains_existing_size_parser_refusal(self):
        with pytest.raises(ValueError):
            run_gate.lane_caps_v1(
                "env", "caps", "ephemeral", "not-a-size", 8192)

    def test_parsed_unicode_lane_and_environment_use_the_same_builder(
            self, tmp_path):
        repo = make_repo(tmp_path)
        config = """\
            schema_version = 1

            [environments."tést"]
            mode = "ephemeral"
            image = "tester-unified:local"

            [lanes."café"]
            kind = "command"
            environment = "tést"
            argv = ["true"]

            [lanes."café".resources]
            memory = "2g"
            pids = 8192
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "unicode lane caps")
        cfg, cfg_path, central, central_path, *_rest = \
            run_gate.load_config_snapshot(project)
        lane = cfg["lanes"]["café"]
        env, _source = run_gate.resolve_environment(
            lane, "café", cfg, central, cfg_path, central_path)
        assert run_gate.expected_lane_caps_v1(
            "café", lane, "tést", env["mode"]) == (
                '{"environment":"t\\u00e9st","lane":"caf\\u00e9",'
                '"memory_max_bytes":2147483648,"pids_max":8192}')

    def test_selected_unicode_lane_reaches_early_record_preflight(
            self, tmp_path, monkeypatch):
        """The old ASCII-only early-preflight skip must not bypass recovery."""
        monkeypatch.setenv("RUN_GATE_TEST_IMAGE", "   ")
        repo = make_repo(tmp_path)
        config = """\
            schema_version = 1

            [environments."tést"]
            mode = "ephemeral"
            image = "tester-unified:local"
            image_env = "RUN_GATE_TEST_IMAGE"

            [lanes."café"]
            kind = "command"
            environment = "tést"
            argv = ["true"]
            clean_tree = false

            [lanes."café".resources]
            memory = "2g"
            pids = 8192
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "unicode early preflight")
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(project, repo, state, lane="café", schema=99)
        before = record.read_bytes()
        result = _dispatch(monkeypatch, project, "café")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "declares schema 99" in result.reason
        assert "image override" not in result.reason
        assert _docker_calls(log) == []
        assert record.read_bytes() == before
        assert state_containers(state) == ["run-gate-planted"]
        assert _history_lane_names(project) == set()


class TestDockerAssignment:
    def test_eligible_is_name_only_and_value_stays_out_of_argv(self):
        carrier: dict[str, str] = {}
        value = _expected("caps")
        args = run_gate.lane_caps_docker_args(
            carrier, value, allow_fact=True)
        assert args == ["-e", CAP]
        assert carrier[CAP] == value
        assert value not in args

    def test_ineligible_and_exec_use_explicit_empty(self):
        carrier = {CAP: AMBIENT_SENTINEL}
        assert run_gate.lane_caps_docker_args(
            carrier, None, allow_fact=True) == ["-e", f"{CAP}="]
        assert CAP not in carrier
        assert run_gate.lane_caps_docker_args(
            {CAP: AMBIENT_SENTINEL}, _expected("caps"), allow_fact=False
        ) == ["-e", f"{CAP}="]

    def test_private_environment_removes_only_its_own_copy(self):
        source = {CAP: AMBIENT_SENTINEL, "KEEP": "yes"}
        private = run_gate.lane_caps_private_environ(source)
        assert private == {"KEEP": "yes"}
        assert source == {CAP: AMBIENT_SENTINEL, "KEEP": "yes"}

    @pytest.mark.parametrize("mode,expected", [
        ("ephemeral", ["-e", f"{CAP}="]),
        ("exec", ["-e", f"{CAP}="]),
        ("host", []),
    ])
    def test_probe_flags_never_claim_a_lane_fact(self, mode, expected):
        assert run_gate.lane_caps_probe_flags({"mode": mode}) == expected


class TestCollisionBoundary:
    @pytest.mark.parametrize("ambient", [None, AMBIENT_SENTINEL])
    @pytest.mark.parametrize("field,value,source_kind", [
        ("forward_env", f'["{CAP}"]', "environment"),
        ("image_env", f'"{CAP}"', "environment"),
        ("cgroup_slice_env", f'"{CAP}"', "environment"),
        ("required_env", f'["{CAP}"]', "lane"),
    ])
    def test_each_selected_caller_field_refuses_before_records_or_docker(
            self, tmp_path, monkeypatch, ambient, field, value, source_kind):
        if ambient is None:
            monkeypatch.delenv(CAP, raising=False)
        else:
            monkeypatch.setenv(CAP, ambient)
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "never.json"
        env_field = f"{field} = {value}" if source_kind == "environment" else ""
        lane_field = f"{field} = {value}" if source_kind == "lane" else ""
        config = f"""\
            schema_version = 1
            [environments.tester-unified]
            mode = "ephemeral"
            image = "tester-unified:local"
            {env_field}
            [lanes.caps]
            kind = "command"
            environment = "tester-unified"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            {lane_field}
            [lanes.caps.resources]
            memory = "2g"
            pids = 8192
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "collision")
        log = fake_docker_stateful(tmp_path, monkeypatch)[0]

        def must_not_read(*_args, **_kwargs):
            raise AssertionError("collision read inflight state")

        def must_not_inventory(*_args, **_kwargs):
            raise AssertionError("collision ran Assay inventory")

        monkeypatch.setattr(run_gate, "load_inflight_record", must_not_read)
        monkeypatch.setattr(run_gate, "discover_imported_assay_lanes",
                            must_not_inventory)
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "ERROR"
        assert result.exit_code == 2
        assert CAP in result.reason
        assert "caps" in result.reason and field in result.reason
        assert docker_runs(log) == [] and lane_runs(log) == []
        assert not artifact.exists()
        assert _history_lane_names(project) == set()
        assert os.environ.get(CAP) == ambient

    def test_collision_wins_over_foreign_record_and_blank_override(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        monkeypatch.setenv("RUN_GATE_TEST_IMAGE", "   ")
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "never.json"
        config = f"""\
            schema_version = 1
            [environments.caps]
            mode = "ephemeral"
            image = "tester-unified:local"
            image_env = "RUN_GATE_TEST_IMAGE"
            [lanes.first]
            kind = "command"
            environment = "caps"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            [lanes.second]
            kind = "command"
            environment = "caps"
            argv = ["true"]
            required_env = ["{CAP}"]
            clean_tree = false
            [lanes.gate]
            kind = "sequence"
            lanes = ["first", "second"]
            stop_on = "FAIL"
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "combined collision")
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        foreign = plant_inflight(project, repo, state, lane="first",
                                 runner="exec")
        before = foreign.read_bytes()

        def must_not_read(*_args, **_kwargs):
            raise AssertionError("combined collision read a foreign record")

        monkeypatch.setattr(run_gate, "load_inflight_record", must_not_read)
        result = _dispatch(monkeypatch, project, "gate")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "Run-Gate-owned" in result.reason and "second" in result.reason
        assert _docker_calls(log) == []
        assert foreign.read_bytes() == before
        assert state_containers(state) == ["run-gate-planted"]
        assert not artifact.exists()

    def test_shadowed_central_collision_does_not_refuse(self, tmp_path,
                                                       monkeypatch):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "caps.json"
        (repo / "run-gate.root.toml").write_text(textwrap.dedent(f"""\
            schema_version = 1
            [environments.caps]
            mode = "ephemeral"
            image = "tester-unified:central"
            forward_env = ["{CAP}"]
            """))
        config = f"""\
            schema_version = 1
            [environments.caps]
            mode = "ephemeral"
            image = "tester-unified:local"
            [lanes.caps]
            kind = "command"
            environment = "caps"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            [lanes.caps.resources]
            memory = "2g"
            pids = 8192
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "shadowed collision")
        cfg, cfg_path, central, central_path, *_ = \
            run_gate.load_config_snapshot(project)
        lanes = run_gate.merge_lanes(
            cfg.get("lanes", {}), central, project, cfg_path, central_path, {})
        lane = lanes["caps"]
        env, source = run_gate.resolve_environment(
            lane, "caps", cfg, central, cfg_path, central_path)
        declaration = run_gate.declared_environment(lane, cfg, central)
        assert declaration["image"] == "tester-unified:local"
        assert "central" not in source
        assert CAP not in declaration.get("forward_env", [])
        run_gate.check_lane_caps_caller_collisions(
            "caps", lane, declaration, source)
        assert run_gate.expected_lane_caps_v1(
            "caps", lane, "caps", env["mode"]) == _expected(
                "caps", environment="caps")

    def test_imported_environment_collision_refuses_before_inventory(
            self, tmp_path, monkeypatch):
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        repo = make_repo(tmp_path)
        config = """\
            schema_version = 1
            [environments.imported]
            mode = "ephemeral"
            image = "tester-unified:local"
            forward_env = ["RUN_GATE_LANE_CAPS_V1"]
            [assay]
            command = ["assay"]
            environment = "imported"
            [assay.import]
            environment = "imported"
            lanes = ["imported-lane"]
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "import collision")
        log = fake_docker_stateful(tmp_path, monkeypatch)[0]

        def no_inventory(*_args, **_kwargs):
            raise AssertionError("import collision queried the judge")

        monkeypatch.setattr(run_gate, "discover_imported_assay_lanes",
                            no_inventory)
        result = _dispatch(monkeypatch, project, "imported-lane")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert CAP in result.reason and "imported" in result.reason
        assert _docker_calls(log) == []


class TestInflightProvenance:
    @pytest.mark.parametrize("stored", [
        {"wrong": "value"},
        17,
        True,
        ["not", "null"],
        "null",
        '{"environment":"tester-unified","lane":"caps",'
        '"memory_max_bytes":2147483648,"pids_max":8192}\n',
        '{"lane":"caps","environment":"tester-unified",'
        '"memory_max_bytes":2147483648,"pids_max":8192}',
        '{"environment": "tester-unified", "lane": "caps", '
        '"memory_max_bytes": 2147483648, "pids_max": 8192}',
        "not json",
    ])
    def test_hostile_eligible_values_refuse_without_docker_or_mutation(
            self, tmp_path, monkeypatch, stored):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(
            project, repo, state, lane="caps", container="old-container",
            **{RECORD: stored})
        before = record.read_bytes()
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert RECORD in result.reason
        assert _docker_calls(log) == []
        assert record.read_bytes() == before
        assert (state / "old-container").exists()
        assert not artifact.exists()

    @pytest.mark.parametrize("stored", [
        "present",
        0,
        False,
        {},
        [],
        "null",
    ])
    def test_ineligible_requires_actual_json_null(
            self, tmp_path, monkeypatch, stored):
        repo, project, _script, artifact = _make_single_project(
            tmp_path, resources='memory = "2g"')
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(
            project, repo, state, lane="caps", container="old-container",
            **{RECORD: stored})
        before = record.read_bytes()
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert RECORD in result.reason and "JSON null" in result.reason
        assert _docker_calls(log) == []
        assert record.read_bytes() == before
        assert not artifact.exists()

    def test_missing_schema_two_field_is_not_null_and_refuses(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(project, repo, state, lane="caps",
                                container="old-container")
        document = json.loads(record.read_text())
        del document[RECORD]
        record.write_text(json.dumps(document))
        before = record.read_bytes()
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "missing lane_caps_v1" in result.reason
        assert _docker_calls(log) == []
        assert record.read_bytes() == before
        assert not artifact.exists()

    def test_direct_resolver_omitting_the_explicit_fact_is_a_programming_error(
            self, tmp_path):
        with pytest.raises(run_gate.GateError, match="explicit selected"):
            run_gate.resolve_inflight(
                "docker", {"kind": "command", "environment": "caps"},
                "caps", tmp_path, tmp_path, tmp_path, False, False, None)

    def test_direct_resolver_refuses_planned_present_record_that_is_absent(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, _state = fake_docker_stateful(tmp_path, monkeypatch)
        record = run_gate.inflight_path(project, "caps")
        record.parent.mkdir(parents=True, exist_ok=True)
        frozen = b'{"schema":2,"container":"planned"}\n'
        record.write_bytes(frozen)
        assert record.is_file()
        record.unlink()
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            run_gate.resolve_inflight(
                "docker", {"kind": "command", "environment": "caps"},
                "caps", project, repo, repo, False, False, None,
                expected_lane_caps_v1_value=_expected("caps"),
                expected_record_snapshot=frozen)
        assert _docker_calls(log) == []
        assert not record.exists()
        assert not artifact.exists()

    @pytest.mark.parametrize("fresh,dry_run", [
        (False, False),
        (True, False),
        (False, True),
        (True, True),
    ])
    def test_unknown_schema_always_refuses_before_docker(
            self, tmp_path, monkeypatch, fresh, dry_run):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(project, repo, state, lane="caps",
                                container="old-container", schema=99)
        before = record.read_bytes()
        argv = ["caps"]
        if fresh:
            argv.append("--fresh")
        if dry_run:
            argv.append("--dry-run")
        result = _dispatch(monkeypatch, project, *argv)
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "declares schema 99" in result.reason
        assert "does not prescribe --fresh" in result.reason
        assert _docker_calls(log) == []
        assert record.read_bytes() == before
        assert (state / "old-container").exists()
        assert not artifact.exists()

    @pytest.mark.parametrize("state_name,expected", [
        ("alive", "alive"),
        ("dead", "dead"),
        ("unknown", "unknown"),
    ])
    def test_owner_classification_is_three_state_and_fail_closed(
            self, tmp_path, monkeypatch, state_name, expected):
        pending = {
            "schema": run_gate.INFLIGHT_SCHEMA,
            "runner": "container",
            "container": "old-container",
            "container_id": "sha256:fakeid-old-container",
            RECORD: _expected("caps"),
        }
        if state_name == "alive":
            pending.update(live_owner_fields())
        elif state_name == "dead":
            owner = live_owner_fields()
            owner["owner_pid"] = 2 ** 22 + 1
            pending.update(owner)
        else:
            owner = live_owner_fields()
            owner["pid_ns"] = owner["pid_ns"] + 1
            pending.update(owner)
        assert run_gate.inflight_owner_state(pending) == expected

    def test_direct_resolver_final_reread_refuses_deleted_record(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        value = _expected("caps")
        run_gate.write_inflight_record(project, repo, "caps", {
            "schema": run_gate.INFLIGHT_SCHEMA,
            "lane": "caps",
            "runner": "container",
            "container": "old-container",
            "container_id": "sha256:fakeid-old-container",
            **live_owner_fields(),
            RECORD: value,
        })
        record = run_gate.inflight_path(project, "caps")
        real_snapshot = run_gate.inflight_record_snapshot
        reads = []

        def snapshot_then_delete(path):
            result = real_snapshot(path)
            reads.append(result)
            if len(reads) == 2:
                path.unlink()
            return result

        monkeypatch.setattr(run_gate, "inflight_record_snapshot",
                            snapshot_then_delete)
        with pytest.raises(run_gate.GateError,
                           match="disappeared at the final resolver boundary"):
            run_gate.resolve_inflight(
                "docker", {"kind": "command", "environment": "caps"},
                "caps", project, repo, repo, False, False, None,
                expected_lane_caps_v1_value=value)
        assert not record.exists()
        assert not artifact.exists()

    def test_direct_resolver_final_reread_refuses_replaced_caps(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        value = _expected("caps")
        run_gate.write_inflight_record(project, repo, "caps", {
            "schema": run_gate.INFLIGHT_SCHEMA,
            "lane": "caps",
            "runner": "container",
            "container": "old-container",
            "container_id": "sha256:fakeid-old-container",
            **live_owner_fields(),
            RECORD: value,
        })
        record = run_gate.inflight_path(project, "caps")
        real_snapshot = run_gate.inflight_record_snapshot
        reads = []

        def snapshot_then_replace(path):
            result = real_snapshot(path)
            reads.append(result)
            if len(reads) == 2:
                document = json.loads(path.read_text())
                document[RECORD] = _expected("caps", pids=7)
                path.write_text(json.dumps(document))
            return result

        monkeypatch.setattr(run_gate, "inflight_record_snapshot",
                            snapshot_then_replace)
        with pytest.raises(run_gate.GateError,
                           match="changed between preflight"):
            run_gate.resolve_inflight(
                "docker", {"kind": "command", "environment": "caps"},
                "caps", project, repo, repo, False, False, None,
                expected_lane_caps_v1_value=value)
        assert not artifact.exists()


class TestChildEnvironmentDelivery:
    def test_eligible_child_and_record_receive_one_exact_value(
            self, tmp_path, monkeypatch, capsys):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        trace, _state, _shim = _executing_docker(tmp_path, monkeypatch)
        writes = _capture_inflight_writes(monkeypatch)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        _set_observer_controls(monkeypatch)
        before_parent = os.environ[CAP]
        result = _dispatch(monkeypatch, project, "caps")
        observed = json.loads(artifact.read_text())
        expected = _expected("caps")
        assert result.verdict == "PASS" and result.exit_code == 0
        assert observed == {
            "present": True, "value": expected, "observer_controls": []}
        assert writes and writes[-1][RECORD] == expected
        run = _json_lane_runs(trace)[0]
        assert run[run.index("-e") + 1] == CAP
        assert expected not in run
        assert run[run.index("--memory") + 1] == "2g"
        assert run[run.index("--pids-limit") + 1] == "8192"
        rows = _trace_rows(trace)
        executed = [row for row in rows
                    if row["kind"] == "executed" and CAP in row["argv"][-1]]
        assert executed
        assert executed[-1]["child_cap_present"] is True
        assert executed[-1]["child_cap"] == expected
        assert executed[-1]["observer_names_in_child"] == []
        assert BAKED_SENTINEL not in observed.values()
        assert os.environ[CAP] == before_parent == AMBIENT_SENTINEL
        assert "docker argv:" in capsys.readouterr().out

    def test_ineligible_child_is_present_and_empty_and_record_is_null(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(
            tmp_path, resources='memory = "2g"')
        trace, _state, _shim = _executing_docker(tmp_path, monkeypatch)
        writes = _capture_inflight_writes(monkeypatch)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        result = _dispatch(monkeypatch, project, "caps")
        observed = json.loads(artifact.read_text())
        assert result.verdict == "PASS"
        assert observed == {
            "present": True, "value": "", "observer_controls": []}
        assert writes and writes[-1][RECORD] is None
        run = _json_lane_runs(trace)[0]
        assert f"{CAP}=" in run
        assert run[run.index("--memory") + 1] == "2g"
        assert "--pids-limit" not in run
        rows = _trace_rows(trace)
        executed = [row for row in rows
                    if row["kind"] == "executed" and "observe_lane_caps" in row["argv"][-1]]
        assert executed[-1]["child_cap_present"] is True
        assert executed[-1]["child_cap"] == ""
        assert os.environ[CAP] == AMBIENT_SENTINEL

    def test_legacy_lane_memory_is_the_same_docker_and_fact_source(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(
            tmp_path, extra_lane_fields='memory = "512m"',
            resources='pids = 8192')
        trace, _state, _shim = _executing_docker(tmp_path, monkeypatch)
        writes = _capture_inflight_writes(monkeypatch)
        result = _dispatch(monkeypatch, project, "caps")
        expected = _expected("caps", memory=536870912)
        assert result.verdict == "PASS"
        assert json.loads(artifact.read_text())["value"] == expected
        assert writes and writes[-1][RECORD] == expected
        run = _json_lane_runs(trace)[0]
        assert run[run.index("--memory") + 1] == "512m"
        assert run[run.index("--pids-limit") + 1] == "8192"

    def test_exec_child_is_present_empty_and_record_is_null(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "exec-observed.json"
        config = f"""\
            schema_version = 1
            [environments.runner]
            mode = "exec"
            image = "tester-unified:local"
            container_name = "persistent-runner"
            [lanes.caps]
            kind = "command"
            environment = "runner"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            [lanes.caps.resources]
            memory = "2g"
            pids = 8192
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "exec caps")
        trace, _state, _shim = _executing_docker(
            tmp_path, monkeypatch, target_container="persistent-runner")
        writes = _capture_inflight_writes(monkeypatch)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        _set_observer_controls(monkeypatch)
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "PASS"
        assert json.loads(artifact.read_text()) == {
            "present": True, "value": "", "observer_controls": []}
        assert writes and writes[-1][RECORD] is None
        exec_call = _json_docker_execs(trace)[-1]
        assert f"{CAP}=" in exec_call
        rows = [row for row in _trace_rows(trace)
                if row["kind"] == "executed" and "observe_lane_caps" in row["argv"][-1]]
        assert rows[-1]["child_cap_present"] is True
        assert rows[-1]["child_cap"] == ""
        assert rows[-1]["observer_names_in_child"] == []
        assert os.environ[CAP] == AMBIENT_SENTINEL

    def test_bare_host_child_has_the_name_removed(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "host-observed.json"
        config = f"""\
            schema_version = 1
            [environments.bare-host]
            mode = "host"
            [lanes.caps]
            kind = "command"
            environment = "bare-host"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "host caps")
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "PASS"
        assert json.loads(artifact.read_text()) == {
            "present": False, "value": None, "observer_controls": []}
        assert os.environ[CAP] == AMBIENT_SENTINEL

    @pytest.mark.parametrize("mode", ["exec", "bare-host"])
    def test_planned_absent_record_appearing_before_child_launch_refuses(
            self, tmp_path, monkeypatch, mode):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "late-record-child.json"
        environment = """\
            [environments.runner]
            mode = "exec"
            image = "tester-unified:local"
            container_name = "persistent-runner"
            """ if mode == "exec" else ""
        lane_environment = "runner" if mode == "exec" else "bare-host"
        config = f"""\
            schema_version = 1
            {textwrap.indent(environment, "            ").lstrip()}
            [lanes.caps]
            kind = "command"
            environment = "{lane_environment}"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "late record launch")
        log, _state = (fake_docker_stateful(tmp_path, monkeypatch)
                       if mode == "exec" else (None, None))
        record = run_gate.inflight_path(project, "caps")
        late = json.dumps({
            "schema": run_gate.INFLIGHT_SCHEMA,
            "lane": "caps",
            "runner": "exec",
            "container": "late-container",
        }).encode("utf-8")
        runner_name = ("run_exec_lane" if mode == "exec"
                       else "run_bare_host_lane")
        real_runner = getattr(run_gate, runner_name)

        def plant_then_run(*args, **kwargs):
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_bytes(late)
            return real_runner(*args, **kwargs)

        monkeypatch.setattr(run_gate, runner_name, plant_then_run)
        result = _dispatch(monkeypatch, project, "caps")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "immutable launch plan" in result.reason
        assert record.read_bytes() == late
        assert not artifact.exists()
        if log is not None:
            assert _docker_calls(log) == []

    def test_record_write_failure_keeps_lane_outcome_and_has_no_record(
            self, tmp_path, monkeypatch, capsys):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        _executing_docker(tmp_path, monkeypatch)
        real_write = run_gate._write_json_atomic

        def fail_record(*_args, **_kwargs):
            raise OSError("planted inflight write failure")

        monkeypatch.setattr(run_gate, "_write_json_atomic", fail_record)
        result = _dispatch(monkeypatch, project, "caps")
        outerr = capsys.readouterr()
        assert result.verdict == "PASS"
        assert artifact.is_file()
        assert "WARNING: re-attach record not written" in outerr.err
        assert "planted inflight write failure" in outerr.err
        assert not run_gate.inflight_path(project, "caps").exists()
        monkeypatch.setattr(run_gate, "_write_json_atomic", real_write)


class TestFinalInflightActionBoundary:
    def test_direct_container_entry_snapshot_refuses_during_preparation(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        document = tomllib.loads(
            (project / "run-gate.toml").read_text(encoding="utf-8"))
        lane = document["lanes"]["caps"]
        environment = document["environments"]["tester-unified"]
        record = run_gate.inflight_path(project, "caps")
        real_disclose = run_gate.disclose_r2_jobs

        def plant_then_disclose(*args, **kwargs):
            if not record.exists():
                plant_inflight(
                    project, repo, None, lane="caps",
                    container="direct-late-container")
            return real_disclose(*args, **kwargs)

        monkeypatch.setattr(run_gate, "disclose_r2_jobs",
                            plant_then_disclose)
        monkeypatch.setattr(run_gate.shutil, "which",
                            lambda _name: "/usr/bin/docker")
        monkeypatch.setattr(run_gate, "head_commit",
                            lambda *_args, **_kwargs: "a" * 40)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda _repo, **_kwargs: Path("/phys"))
        monkeypatch.setattr(run_gate, "container_mount_flags",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(run_gate, "verify_slice_loaded",
                            lambda *_args: None)
        monkeypatch.setattr(run_gate, "subprocess",
                            SimpleNamespace(
                                run=lambda *_args, **_kwargs: pytest.fail(
                                    "a changed record must refuse before "
                                    "docker run")))
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            run_gate.run_container_lane(
                lane, "caps", project, repo, repo, environment, "fixture",
                "dev-gates.slice", "fixture",
                profile_plan={"enabled": False,
                              "disabled_reason": "direct boundary test"})
        late = record.read_bytes()
        assert json.loads(late)["container"] == "direct-late-container"
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    def test_direct_exec_entry_snapshot_refuses_late_record(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "direct-exec-boundary.json"
        config = f"""\
            schema_version = 1
            [environments.runner]
            mode = "exec"
            image = "tester-unified:local"
            container_name = "persistent-runner"
            [lanes.caps]
            kind = "command"
            environment = "runner"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "direct exec final-record boundary")
        record = run_gate.inflight_path(project, "caps")
        real_snapshot = run_gate.inflight_record_snapshot
        planted = False

        def snapshot_then_plant(path):
            nonlocal planted
            snapshot = real_snapshot(path)
            if not planted and path == record:
                plant_inflight(
                    project, repo, None, lane="caps",
                    container="direct-exec-late-container")
                planted = True
            return snapshot

        monkeypatch.setattr(run_gate, "inflight_record_snapshot",
                            snapshot_then_plant)
        monkeypatch.setattr(run_gate.shutil, "which",
                            lambda _name: "/usr/bin/docker")
        monkeypatch.setattr(
            run_gate, "subprocess",
            SimpleNamespace(
                run=lambda *_args, **_kwargs: pytest.fail(
                    "a changed record must refuse before docker ps")))
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            run_gate.run_exec_lane(
                {"kind": "command", "argv": ["true"]}, "caps", project,
                repo, repo, {"mode": "exec",
                             "image": "tester-unified:local"}, "fixture",
                "persistent-runner", "fixture", "fixture", None, "no-slice",
                run_record={}, profile_plan={
                    "enabled": False,
                    "disabled_reason": "direct boundary test"})
        late = record.read_bytes()
        assert json.loads(late)["container"] == "direct-exec-late-container"
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    @pytest.mark.parametrize(("runner", "mode"), [
        ("container", "ephemeral"),
        ("exec", "exec"),
        ("bare-host", "host"),
    ])
    def test_assay_verdict_cleanup_requires_unchanged_record(
            self, tmp_path, monkeypatch, runner, mode):
        repo = make_repo(tmp_path)
        image = ('image = "tester-unified:local"\n'
                 if mode != "host" else "")
        container_name = ('container_name = "persistent-runner"\n'
                          if runner == "exec" else "")
        config = f"""\
            schema_version = 1
            [environments.runner]
            mode = "{mode}"
            {image}
            {container_name}
            [lanes.caps]
            kind = "assay"
            assay_lane = "caps"
            assay_command = ["measure-assay"]
            environment = "runner"
            clean_tree = false
            artifacts = [".run-gate/verdict-cleanup-child.observed"]
            [lanes.caps.resources]
            memory = "2g"
            pids = 8192
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, f"assay verdict cleanup {runner}")
        child_artifact = (project / ".run-gate"
                          / "verdict-cleanup-child.observed")
        document = tomllib.loads(textwrap.dedent(config))
        lane = document["lanes"]["caps"]
        environment = document["environments"]["runner"]
        record = run_gate.inflight_path(project, "caps")
        observed = {"seam": False, "external_record": None,
                    "external_verdict": None}

        def plant_before_cleanup(*_args, **_kwargs):
            external_record = plant_inflight(
                project, repo, None, lane="caps", code=0)
            external_verdict = Path(
                json.loads(external_record.read_text(encoding="utf-8"))[
                    "verdict"])
            external_verdict.write_bytes(
                b'{"foreign_verdict":"P7A_VERDICT_MUST_SURVIVE"}\n')
            observed.update(
                seam=True,
                external_record=external_record.read_bytes(),
                external_verdict=external_verdict.read_bytes())

        monkeypatch.setattr(run_gate, "assure_assay_state_root",
                            plant_before_cleanup)
        monkeypatch.setattr(run_gate, "physical_path",
                            lambda _repo, **_kwargs: Path("/phys"))
        monkeypatch.setattr(run_gate, "container_mount_flags",
                            lambda *_args, **_kwargs: [])
        monkeypatch.setattr(run_gate, "verify_slice_loaded",
                            lambda *_args: None)
        common_profile = {"enabled": False,
                          "disabled_reason": "verdict cleanup boundary test"}
        if runner == "container":
            log, _state = fake_docker_stateful(tmp_path, monkeypatch)
        elif runner == "exec":
            trace, _state, _shim = _executing_docker(
                tmp_path, monkeypatch, target_container="persistent-runner")
        bare_host_popen_attempts: list[tuple[tuple[object, ...],
                                             dict[str, object]]] = []
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            if runner == "container":
                run_gate.run_container_lane(
                    lane, "caps", project, repo, repo, environment, "fixture",
                    "dev-gates.slice", "fixture", run_record={},
                    profile_plan=common_profile)
            elif runner == "exec":
                run_gate.run_exec_lane(
                    lane, "caps", project, repo, repo, environment, "fixture",
                    "persistent-runner", "fixture", "fixture", None,
                    "no-slice", run_record={}, profile_plan=common_profile)
            else:
                def forbid_bare_host_popen(*args, **kwargs):
                    bare_host_popen_attempts.append((args, kwargs))
                    pytest.fail(
                        "bare-host Assay child launch was attempted after "
                        "the inflight record changed")

                monkeypatch.setattr(
                    run_gate.subprocess, "Popen", forbid_bare_host_popen)
                run_gate.run_bare_host_lane(
                    lane, "caps", project, repo, repo, run_record={},
                    profile_plan=common_profile, env_source="fixture")
        assert observed["seam"] is True
        assert record.read_bytes() == observed["external_record"]
        verdict = Path(json.loads(record.read_text(encoding="utf-8"))["verdict"])
        assert verdict.read_bytes() == observed["external_verdict"]
        assert not child_artifact.exists()
        assert _history_lane_names(project) == set()
        if runner == "container":
            assert lane_runs(log) == []
        elif runner == "exec":
            assert docker_execs(trace) == []
        else:
            assert bare_host_popen_attempts == []

    def test_record_created_during_container_launch_preparation_refuses(
            self, tmp_path, monkeypatch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, _state = fake_docker_stateful(tmp_path, monkeypatch)
        record = run_gate.inflight_path(project, "caps")
        real_clear = run_gate.clear_previous_assay_verdict

        def plant_then_prepare(*args, **kwargs):
            if not record.exists():
                plant_inflight(
                    project, repo, None, lane="caps",
                    container="run-gate-late-external")
            return real_clear(*args, **kwargs)

        monkeypatch.setattr(
            run_gate, "clear_previous_assay_verdict", plant_then_prepare)
        result = _dispatch(monkeypatch, project, "caps")
        late = record.read_bytes()
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "immutable launch plan" in result.reason
        assert record.read_bytes() == late
        assert lane_runs(log) == []
        assert _docker_calls(log) == []
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    def test_exec_record_replaced_after_record_write_refuses_before_exec(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "exec-boundary.json"
        config = f"""\
            schema_version = 1
            [environments.runner]
            mode = "exec"
            image = "tester-unified:local"
            container_name = "persistent-runner"
            [lanes.caps]
            kind = "command"
            environment = "runner"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "exec final-record boundary")
        trace, _state, _shim = _executing_docker(
            tmp_path, monkeypatch, target_container="persistent-runner")
        record = run_gate.inflight_path(project, "caps")
        real_write = run_gate.write_inflight_record

        def replace_after_write(*args, **kwargs):
            written = real_write(*args, **kwargs)
            document = json.loads(record.read_text(encoding="utf-8"))
            document["owner_pid"] = 2 ** 22 + 1
            document["owner_start"] = 987654321
            record.write_text(json.dumps(document), encoding="utf-8")
            return written

        monkeypatch.setattr(run_gate, "write_inflight_record",
                            replace_after_write)
        result = _dispatch(monkeypatch, project, "caps")
        replaced = record.read_bytes()
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "immutable launch plan" in result.reason
        assert record.read_bytes() == replaced
        assert docker_execs(trace) == []
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    def test_record_created_during_bare_host_launch_preparation_refuses(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        script = _observer_script(repo)
        artifact = repo / ".run-gate" / "host-boundary.json"
        config = f"""\
            schema_version = 1
            [environments.bare-host]
            mode = "host"
            [lanes.caps]
            kind = "command"
            environment = "bare-host"
            argv = [{json.dumps(sys.executable)}, {json.dumps(str(script))}, {json.dumps(str(artifact))}]
            clean_tree = false
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "bare-host final-record boundary")
        record = run_gate.inflight_path(project, "caps")
        real_watch = run_gate.make_progress_watch
        hook_ran = False

        def plant_then_watch(*args, **kwargs):
            nonlocal hook_ran
            if not record.exists():
                plant_inflight(
                    project, repo, None, lane="caps",
                    container="late-host-authority")
                hook_ran = True
            return real_watch(*args, **kwargs)

        monkeypatch.setattr(run_gate, "make_progress_watch", plant_then_watch)
        result = _dispatch(monkeypatch, project, "caps")
        late = record.read_bytes()
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "immutable launch plan" in result.reason
        assert hook_ran
        assert record.read_bytes() == late
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    @pytest.mark.parametrize("mutation", [
        "container_id", "owner", "caps",
    ])
    def test_fresh_removal_rereads_after_real_inspection(
            self, tmp_path, monkeypatch, mutation):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(
            project, repo, state, lane="caps", status="running",
            **dead_owner_fields())
        before = record.read_bytes()
        real_state = run_gate.container_state

        def inspect_then_mutate(docker, name):
            inspected = real_state(docker, name)
            document = json.loads(record.read_text(encoding="utf-8"))
            if mutation == "container_id":
                document["container_id"] = "sha256:replacement"
            elif mutation == "owner":
                document.update(live_owner_fields())
            else:
                document[RECORD] = _expected("caps", pids=7)
            record.write_text(json.dumps(document), encoding="utf-8")
            return inspected

        monkeypatch.setattr(run_gate, "container_state",
                            inspect_then_mutate)
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            run_gate.resolve_inflight(
                "docker",
                {"kind": "command", "environment": "caps"},
                "caps", project, repo, repo, True, False, {},
                expected_lane_caps_v1_value=_expected("caps"),
                expected_record_snapshot=before)
        mutated = record.read_bytes()
        assert mutated != before
        assert not any(call[1:3] == ["rm", "-f"] for call in _docker_calls(log))
        assert (state / "run-gate-planted").exists()
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    @pytest.mark.parametrize("branch", ["follow", "adopt", "collect"])
    def test_record_change_after_inspection_refuses_before_result_action(
            self, tmp_path, monkeypatch, branch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        owner = live_owner_fields() if branch == "follow" else dead_owner_fields()
        status = "exited" if branch == "collect" else "running"
        record = plant_inflight(
            project, repo, state, lane="caps", status=status, code=7,
            **owner)
        before = record.read_bytes()
        real_state = run_gate.container_state

        def inspect_then_mutate(docker, name):
            inspected = real_state(docker, name)
            document = json.loads(record.read_text(encoding="utf-8"))
            document["container_id"] = "sha256:replacement"
            record.write_text(json.dumps(document), encoding="utf-8")
            return inspected

        monkeypatch.setattr(run_gate, "container_state",
                            inspect_then_mutate)
        monkeypatch.setattr(
            run_gate, "follow_container",
            lambda *_args, **_kwargs: pytest.fail(
                "a changed record must refuse before follow"))
        monkeypatch.setattr(
            run_gate, "await_container",
            lambda *_args, **_kwargs: pytest.fail(
                "a changed record must refuse before result access"))
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            run_gate.resolve_inflight(
                "docker",
                {"kind": "command", "environment": "caps"},
                "caps", project, repo, repo, False, False, {},
                expected_lane_caps_v1_value=_expected("caps"),
                expected_record_snapshot=before)
        mutated = record.read_bytes()
        assert mutated != before
        assert not any(call[1:3] == ["rm", "-f"] for call in _docker_calls(log))
        assert not any(call[1:3] in (["wait", "run-gate-planted"],
                                     ["logs", "-f", "run-gate-planted"])
                       for call in _docker_calls(log))
        assert (state / "run-gate-planted").exists()
        assert not artifact.exists()
        assert _history_lane_names(project) == set()

    @pytest.mark.parametrize("branch", ["adopt", "collect"])
    def test_record_change_after_adoption_refuses_before_result_access(
            self, tmp_path, monkeypatch, branch):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        status = "running" if branch == "adopt" else "exited"
        record = plant_inflight(
            project, repo, state, lane="caps", status=status, code=7,
            **dead_owner_fields())
        before = record.read_bytes()
        real_adopt = run_gate.adopt_inflight_start

        def adopt_then_mutate(run_record, pending):
            real_adopt(run_record, pending)
            document = json.loads(record.read_text(encoding="utf-8"))
            document["container_id"] = "sha256:replacement"
            record.write_text(json.dumps(document), encoding="utf-8")

        monkeypatch.setattr(run_gate, "adopt_inflight_start",
                            adopt_then_mutate)
        monkeypatch.setattr(
            run_gate, "await_container",
            lambda *_args, **_kwargs: pytest.fail(
                "a changed record must refuse before result access"))
        with pytest.raises(run_gate.GateError,
                           match="immutable launch plan"):
            run_gate.resolve_inflight(
                "docker",
                {"kind": "command", "environment": "caps"},
                "caps", project, repo, repo, False, False, {},
                expected_lane_caps_v1_value=_expected("caps"),
                expected_record_snapshot=before)
        mutated = record.read_bytes()
        assert mutated != before
        assert not any(call[1:3] == ["rm", "-f"] for call in _docker_calls(log))
        assert not any(call[1:3] in (["wait", "run-gate-planted"],
                                     ["logs", "-f", "run-gate-planted"])
                       for call in _docker_calls(log))
        assert (state / "run-gate-planted").exists()
        assert not artifact.exists()
        assert _history_lane_names(project) == set()


class TestSequencePrelaunchBarrier:
    def test_nested_repeated_names_keep_distinct_occurrence_paths(
            self, tmp_path):
        repo = make_repo(tmp_path)
        config = """\
            schema_version = 1
            [environments.caps]
            mode = "ephemeral"
            image = "tester-unified:local"
            [lanes.first]
            kind = "command"
            environment = "caps"
            argv = ["true"]
            clean_tree = false
            [lanes.first.resources]
            memory = "1g"
            pids = 111
            [lanes.bad]
            kind = "command"
            environment = "caps"
            argv = ["true"]
            clean_tree = false
            [lanes.bad.resources]
            memory = "2g"
            pids = 8192
            [lanes.nested]
            kind = "sequence"
            lanes = ["bad"]
            [lanes.gate]
            kind = "sequence"
            lanes = ["first", "nested", "first"]
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "repeated sequence names")
        (cfg, cfg_path, central, central_path,
         cfg_sha256, central_cfg_sha256, *_rest) = \
            run_gate.load_config_snapshot(project)
        lanes = run_gate.merge_lanes(
            cfg.get("lanes", {}), central, project, cfg_path, central_path)
        plan = run_gate.build_root_selection_plan(
            "gate", lanes, cfg, central, cfg_path, central_path,
            cfg_sha256, central_cfg_sha256, project,
            SimpleNamespace(worktree=None, fresh=False, dry_run=False))
        assert plan is not None
        assert [(node.path, node.lane_name) for node in plan.sequence_nodes] == [
            ((), "gate"), ((1,), "nested")]
        assert [(leaf.path, leaf.lane_name) for leaf in plan.leaves] == [
            ((0,), "first"), ((1, 0), "bad"), ((2,), "first")]
        assert plan.leaf_at((0,)).lane_name == "first"
        assert plan.leaf_at((2,)).lane_name == "first"
        assert plan.leaf_at((0,)) is not plan.leaf_at((2,))

    def test_live_fresh_sequence_keeps_non_ephemeral_members_out_of_fresh(
            self, tmp_path):
        repo = make_repo(tmp_path)
        config = """\
            schema_version = 1
            [environments.caps]
            mode = "ephemeral"
            image = "tester-unified:local"
            [environments.local]
            mode = "host"
            [lanes.first]
            kind = "command"
            environment = "caps"
            argv = ["true"]
            clean_tree = false
            [lanes.first.resources]
            memory = "1g"
            pids = 111
            [lanes.host]
            kind = "command"
            environment = "local"
            argv = ["true"]
            clean_tree = false
            [lanes.gate]
            kind = "sequence"
            lanes = ["first", "host"]
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "mixed fresh sequence")
        (cfg, cfg_path, central, central_path,
         cfg_sha256, central_cfg_sha256, *_rest) = \
            run_gate.load_config_snapshot(project)
        lanes = run_gate.merge_lanes(
            cfg.get("lanes", {}), central, project, cfg_path, central_path)
        plan = run_gate.build_root_selection_plan(
            "gate", lanes, cfg, central, cfg_path, central_path,
            cfg_sha256, central_cfg_sha256, project,
            SimpleNamespace(worktree=None, fresh=True, dry_run=False))
        assert plan is not None
        assert [(leaf.lane_name, leaf.environment_mode)
                for leaf in plan.leaves] == [
                    ("first", "ephemeral"), ("host", "host")]
        assert plan.fresh is True

    @pytest.mark.parametrize("failure", [
        "collision",
        "blank_image",
        "required_absent",
        "required_empty",
        "required_unreachable",
        "foreign_record",
        "unknown_schema",
        "missing_caps",
        "unequal_caps",
        "container_id_mismatch",
    ])
    def test_later_nested_failure_prevents_every_first_member_effect(
            self, tmp_path, monkeypatch, capsys, failure):
        if failure == "blank_image":
            monkeypatch.setenv("RUN_GATE_TEST_IMAGE", "   ")
        elif failure == "required_empty":
            monkeypatch.setenv("REQUIRED_P7A", "")
        elif failure == "required_unreachable":
            monkeypatch.setenv("REQUIRED_P7A", "nonempty")

        bad_fields = ""
        bad_environment = "caps"
        extra_environments = ""
        environment_fields = ""
        if failure == "collision":
            bad_fields = f'required_env = ["{CAP}"]'
        elif failure == "blank_image":
            extra_environments = (
                '[environments.override]\n'
                'mode = "ephemeral"\n'
                'image = "tester-unified:local"\n'
                'image_env = "RUN_GATE_TEST_IMAGE"\n')
            bad_environment = "override"
        elif failure in ("required_absent", "required_empty"):
            environment_fields = 'forward_env = ["REQUIRED_P7A"]'
            bad_fields = 'required_env = ["REQUIRED_P7A"]'
        elif failure == "required_unreachable":
            bad_fields = 'required_env = ["REQUIRED_P7A"]'
        repo, project, first_artifact, bad_artifact = _make_sequence_project(
            tmp_path, bad_fields, bad_environment=bad_environment,
            extra_environments=extra_environments,
            environment_fields=environment_fields)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = None
        if failure == "foreign_record":
            record = plant_inflight(project, repo, state, lane="bad",
                                    runner="exec")
        elif failure == "unknown_schema":
            record = plant_inflight(project, repo, state, lane="bad",
                                    schema=99)
        elif failure == "missing_caps":
            record = plant_inflight(project, repo, state, lane="bad")
            document = json.loads(record.read_text())
            del document[RECORD]
            record.write_text(json.dumps(document))
        elif failure == "unequal_caps":
            record = plant_inflight(
                project, repo, state, lane="bad",
                **{RECORD: _expected("bad", pids=7)})
        elif failure == "container_id_mismatch":
            record = plant_inflight(
                project, repo, state, lane="bad",
                state_id="sha256:different-container")
        before = record.read_bytes() if record is not None else None
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)

        reconciled = _fake_admission(monkeypatch)
        result = _dispatch(monkeypatch, project, "gate")
        output = capsys.readouterr()
        if failure in ("required_absent", "required_empty"):
            assert result.verdict == "NOT_RUN" and result.exit_code == 3
            assert result.reason == "env-missing"
            assert "REQUIRED_P7A" in (output.out + output.err)
        else:
            assert result.verdict == "ERROR" and result.exit_code == 2
        if failure == "collision":
            assert CAP in result.reason and "bad" in result.reason
        if failure == "blank_image":
            assert "image override" in result.reason and "empty" in result.reason
        if failure == "required_unreachable":
            assert "forward_env" in result.reason
        if failure == "foreign_record":
            assert "foreign record" in result.reason
        if failure == "unknown_schema":
            assert "schema 99" in result.reason
        if failure in ("missing_caps", "unequal_caps"):
            assert RECORD in result.reason
        if failure == "container_id_mismatch":
            assert "recorded container id" in result.reason
        assert lane_runs(log) == []
        assert state_containers(state) in ([], ["run-gate-planted"])
        if failure == "container_id_mismatch":
            assert state_containers(state) == ["run-gate-planted"]
        if record is not None:
            assert record.read_bytes() == before
        assert not first_artifact.exists()
        assert not bad_artifact.exists()
        assert _history_lane_names(project) == set()
        assert not run_gate.inflight_path(project, "first").exists()
        if failure not in {
                "collision", "blank_image", "required_absent",
                "required_empty", "required_unreachable"}:
            assert reconciled == ["admit", "release"] \
                if failure == "container_id_mismatch" else reconciled == []
        assert os.environ[CAP] == AMBIENT_SENTINEL

    @pytest.mark.parametrize("required_state", ["absent", "empty"])
    @pytest.mark.parametrize("dry_run", [False, True])
    def test_required_input_env_missing_terminal_live_and_dry_run(
            self, tmp_path, monkeypatch, capsys, required_state, dry_run):
        if required_state == "empty":
            monkeypatch.setenv("REQUIRED_P7A", "")
        repo, project, first_artifact, bad_artifact = _make_sequence_project(
            tmp_path, 'required_env = ["REQUIRED_P7A"]',
            environment_fields='forward_env = ["REQUIRED_P7A"]')
        log, _state = fake_docker_stateful(tmp_path, monkeypatch)
        argv = ["gate", "--dry-run"] if dry_run else ["gate"]
        result = _dispatch(monkeypatch, project, *argv)
        output = capsys.readouterr()
        assert result.verdict == "NOT_RUN" and result.exit_code == 3
        assert result.reason == "env-missing"
        assert "REQUIRED_P7A" in (output.out + output.err)
        assert lane_runs(log) == []
        assert not first_artifact.exists()
        assert not bad_artifact.exists()
        assert _history_lane_names(project) == set()
        assert not run_gate.inflight_path(project, "first").exists()
        assert not run_gate.inflight_path(project, "bad").exists()

    def test_combined_later_collision_wins_over_earlier_record_and_override(
            self, tmp_path, monkeypatch):
        repo, project, first_artifact, _bad_artifact = _make_sequence_project(
            tmp_path, f'required_env = ["{CAP}"]',
            environment_fields='image_env = "RUN_GATE_TEST_IMAGE"')
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        monkeypatch.setenv("RUN_GATE_TEST_IMAGE", "   ")
        plant_inflight(project, repo, state, lane="first",
                       container="first-old", runner="exec")
        reconciled = _fake_admission(monkeypatch)
        result = _dispatch(monkeypatch, project, "gate")
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "Run-Gate-owned" in result.reason and "bad" in result.reason
        assert reconciled == []
        assert _docker_calls(log) == []
        assert not first_artifact.exists()

    @pytest.mark.parametrize("mutation", [
        "delete", "container_id", "caps", "absent_to_present",
    ])
    def test_final_all_member_reread_refuses_record_seam_mutations(
            self, tmp_path, monkeypatch, capsys, mutation):
        repo, project, first_artifact, bad_artifact = _make_sequence_project(
            tmp_path, "")
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = None
        if mutation != "absent_to_present":
            record = plant_inflight(project, repo, state, lane="bad",
                                    container="bad-old")
        events = _fake_admission(monkeypatch)
        real = run_gate._planned_record_matches
        calls: list[int] = []

        def mutate_then_compare(plan):
            calls.append(1)
            if len(calls) == 2:
                if mutation == "delete":
                    record.unlink()
                elif mutation == "container_id":
                    document = json.loads(record.read_text())
                    document["container_id"] = "sha256:replacement"
                    record.write_text(json.dumps(document))
                elif mutation == "caps":
                    document = json.loads(record.read_text())
                    document[RECORD] = _expected("bad", pids=7)
                    record.write_text(json.dumps(document))
                else:
                    plant_inflight(project, repo, state, lane="bad",
                                   container="new-bad")
            return real(plan)

        monkeypatch.setattr(run_gate, "_planned_record_matches",
                            mutate_then_compare)
        result = _dispatch(monkeypatch, project, "gate")
        capsys.readouterr()
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert events == ["admit", "release"]
        assert lane_runs(log) == []
        assert not first_artifact.exists()
        assert not bad_artifact.exists()
        assert _history_lane_names(project) == set()

    @pytest.mark.parametrize("terminal,fresh,dry_run,expected_verdict,"
                             "expected_exit", [
        ("recognized", False, False, "ERROR", 2),
        ("recognized", True, False, "ERROR", 2),
        ("recognized", False, True, "ERROR", 2),
        ("recognized", True, True, "ERROR", 2),
        ("recognized_no_container", False, False, "ERROR", 2),
        ("recognized_no_container", True, False, "ERROR", 2),
        ("recognized_no_container", False, True, "ERROR", 2),
        ("recognized_no_container", True, True, "ERROR", 2),
        ("invalid_identity", False, False, "ERROR", 2),
        ("invalid_identity", True, False, "ERROR", 2),
        ("invalid_identity", False, True, "ERROR", 2),
        ("invalid_identity", True, True, "ERROR", 2),
        ("foreign", False, False, "ERROR", 2),
        ("foreign", True, False, "ERROR", 2),
        ("foreign", True, True, "PASS", 0),
        ("unknown", False, False, "ERROR", 2),
        ("unknown", True, False, "ERROR", 2),
        ("unknown", False, True, "ERROR", 2),
        ("unknown", True, True, "ERROR", 2),
        ("foreign", False, True, "PASS", 0),
    ])
    def test_selected_sequence_node_record_refuses_changed_kind(
            self, tmp_path, monkeypatch, capsys, terminal, fresh, dry_run,
            expected_verdict, expected_exit):
        repo = make_repo(tmp_path)
        config = """\
            schema_version = 1
            [environments.caps]
            mode = "ephemeral"
            image = "tester-unified:local"
            [lanes.first]
            kind = "command"
            environment = "caps"
            argv = ["true"]
            clean_tree = false
            [lanes.first.resources]
            memory = "1g"
            pids = 1
            [lanes.node]
            kind = "sequence"
            lanes = ["first"]
            stop_on = "FAIL"
            [lanes.gate]
            kind = "sequence"
            lanes = ["first", "node"]
            stop_on = "FAIL"
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, "node record")
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        node_record = project / ".run-gate" / "inflight" / "node.json"
        node_record.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": run_gate.INFLIGHT_SCHEMA,
            "lane": "node",
            "runner": "container",
            "container": "node-old",
            "container_id": "sha256:fakeid-node-old",
            "assay_identity_mode": (
                "invalid-mode" if terminal == "invalid_identity" else None),
        }
        if terminal == "foreign":
            payload["runner"] = "exec"
        if terminal == "recognized_no_container":
            payload.pop("container")
            payload.pop("container_id")
        if terminal == "unknown":
            payload["schema"] = 99
        node_record.write_text(json.dumps(payload))
        (state / "node-old").write_text("running 0\n")
        before = node_record.read_bytes()
        argv = ["gate"]
        if fresh:
            argv.append("--fresh")
        if dry_run:
            argv.append("--dry-run")
        result = _dispatch(monkeypatch, project, *argv)
        output = capsys.readouterr()
        assert result.verdict == expected_verdict
        assert result.exit_code == expected_exit
        if terminal == "unknown":
            assert "schema 99" in result.reason
        elif terminal == "foreign":
            assert "foreign record" in (result.reason or output.out)
        elif terminal == "invalid_identity":
            assert "assay_identity_mode" in result.reason
        else:
            assert "current kind is 'sequence'" in result.reason
        assert node_record.read_bytes() == before
        assert (state / "node-old").exists()
        assert _docker_calls(log) == []
        assert _history_lane_names(project) == set()

    def test_all_member_snapshot_reread_covers_an_absent_sequence_node(
            self, tmp_path, monkeypatch, capsys):
        repo, project, first_artifact, bad_artifact = _make_sequence_project(
            tmp_path, "")
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        events = _fake_admission(monkeypatch)
        real = run_gate._planned_record_matches
        calls: list[int] = []

        def create_node_then_compare(plan):
            calls.append(1)
            if len(calls) == 2:
                plant_inflight(
                    project, repo, state, lane="nested",
                    container="nested-new")
            return real(plan)

        monkeypatch.setattr(run_gate, "_planned_record_matches",
                            create_node_then_compare)
        result = _dispatch(monkeypatch, project, "gate")
        capsys.readouterr()
        assert result.verdict == "ERROR" and result.exit_code == 2
        assert "selected sequence node 'nested' changed" in result.reason
        assert events == ["admit", "release"]
        assert lane_runs(log) == []
        assert not first_artifact.exists()
        assert not bad_artifact.exists()
        assert _history_lane_names(project) == set()
        assert not run_gate.inflight_path(project, "first").exists()

    def test_successful_nested_sequence_delivers_each_members_own_fact(
            self, tmp_path, monkeypatch):
        repo, project, first_artifact, bad_artifact = _make_sequence_project(
            tmp_path, "")
        trace, _state, _shim = _executing_docker(tmp_path, monkeypatch)
        writes = _capture_inflight_writes(monkeypatch)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        result = _dispatch(monkeypatch, project, "gate")
        assert result.verdict == "PASS"
        assert json.loads(first_artifact.read_text())["value"] == _expected(
            "first", memory=1073741824, pids=111)
        assert json.loads(bad_artifact.read_text())["value"] == _expected("bad")
        assert [payload[RECORD] for payload in writes] == [
            _expected("first", memory=1073741824, pids=111),
            _expected("bad"),
        ]
        runs = _json_lane_runs(trace)
        assert len(runs) == 2
        assert all(run[run.index("-e") + 1] == CAP for run in runs)
        assert _history_lane_names(project) == {"first", "bad"}
        assert os.environ[CAP] == AMBIENT_SENTINEL


class TestProbeAndAuxiliaryBoundaries:
    @staticmethod
    def _assay_probe_project(tmp_path: Path, mode: str
                             ) -> tuple[Path, Path, Path]:
        repo = make_repo(tmp_path)
        container = ('container_name = "persistent-runner"\n'
                     if mode == "exec" else "")
        config = f"""\
            schema_version = 1
            [environments.runner]
            mode = "{mode}"
            image = "tester-unified:local"
            {container}
            [assay]
            command = ["measure-assay"]
            environment = "runner"
            [lanes.caps]
            kind = "assay"
            assay_lane = "caps"
            clean_tree = false
            budget = "10m"
            """
        project = repo / "proj"
        project.mkdir()
        (project / "run-gate.toml").write_text(textwrap.dedent(config))
        commit_all(repo, f"assay {mode} probe")
        return repo, project, repo / "run-gate.toml"

    def test_assay_inventory_probe_masks_every_execution_mode(
            self, tmp_path, monkeypatch):
        artifact = tmp_path / "inventory-child.json"
        directory = shim_dir_of(monkeypatch)
        judge = directory / "inventory-judge"
        judge.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json
            import os
            from pathlib import Path
            cap = {CAP!r}
            Path({str(artifact)!r}).write_text(json.dumps({{
                "present": cap in os.environ,
                "value": os.environ.get(cap) if cap in os.environ else None,
            }}, sort_keys=True))
            print(json.dumps({{
                "inventory_schema": 1,
                "lanes": [{{"name": "caps"}}],
            }}))
            """))
        judge.chmod(judge.stat().st_mode | stat.S_IEXEC)
        repo = make_repo(tmp_path)
        lane = {
            "kind": "assay",
            "assay_lane": "caps",
            "assay_command": ["inventory-judge"],
        }
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        for mode in ("host", "ephemeral", "exec"):
            artifact.unlink(missing_ok=True)
            docker: str | None = None
            env: dict
            if mode == "host":
                env = {}
            else:
                trace, _state, shim = _executing_docker(
                    tmp_path, monkeypatch, target_container="persistent-runner")
                docker = str(shim)
                env = {
                    "mode": mode,
                    "image": "tester-unified:local",
                    "container_name": "persistent-runner",
                }
            doc, reason = run_gate.assay_inventory(
                docker, lane, env, mode, repo, repo, "fixture", repo)
            assert reason is None and doc["inventory_schema"] == 1
            observed = json.loads(artifact.read_text())
            if mode == "host":
                assert observed == {"present": False, "value": None}
            else:
                assert observed == {"present": True, "value": ""}
                rows = [row for row in _trace_rows(trace)
                        if row["kind"] == "executed"
                        and "inventory-judge lanes --json" in row["argv"][-1]]
                assert rows[-1]["client_cap_present"] is False
                assert rows[-1]["child_cap"] == ""
        assert os.environ[CAP] == AMBIENT_SENTINEL

    @pytest.mark.parametrize("mode", ["ephemeral", "exec"])
    def test_assay_state_probe_observes_explicit_empty_child(
            self, tmp_path, monkeypatch, mode):
        repo = make_repo(tmp_path)
        trace, _state, shim = _executing_docker(
            tmp_path, monkeypatch, target_container="persistent-runner")
        env = {
            "mode": mode,
            "image": "tester-unified:local",
            "container_name": "persistent-runner",
        }
        state_root = repo / ".run-gate"
        state_dir = state_root / "assay-state"
        state_root.mkdir(exist_ok=True)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        ready, reason = run_gate.probe_assay_state_root(
            str(shim), env, "runner", repo, repo, "fixture", "dev-gates.slice",
            state_root, state_dir,
            resolved_container_name=(
                "persistent-runner" if mode == "exec" else None))
        assert ready is True and reason is None
        rows = [row for row in _trace_rows(trace)
                if row["kind"] == "executed"
                and "RUN_GATE_STATE_ROOT_READY" in row["argv"][-1]]
        assert rows[-1]["client_cap_present"] is False
        assert rows[-1]["child_cap_present"] is True
        assert rows[-1]["child_cap"] == ""
        assert os.environ[CAP] == AMBIENT_SENTINEL

    @pytest.mark.parametrize("verb", ["doctor", "check-env"])
    @pytest.mark.parametrize("mode", ["ephemeral", "exec"])
    def test_public_preflight_probes_mask_the_reserved_name(
            self, tmp_path, monkeypatch, verb, mode):
        repo, project, cfg_path = self._assay_probe_project(tmp_path, mode)
        trace, _state, shim = _executing_docker(
            tmp_path, monkeypatch, target_container="persistent-runner")
        tool = shim.parent / "probe-tool"
        tool.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
        inventory = {
            "inventory_schema": 1,
            "lanes": [{
                "name": "caps",
                "argv0": "probe-tool",
                "external_tools": [],
                "language": "",
            }],
        }
        monkeypatch.setattr(
            run_gate, "assay_inventory",
            lambda *_args, **_kwargs: (inventory, None))
        monkeypatch.setattr(
            run_gate, "verify_slice_loaded", lambda _slice_name: None)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        argv = ["doctor"] if verb == "doctor" else ["--check-env"]
        result = _dispatch(monkeypatch, project, *argv)
        assert result.verdict in {"PASS", "ERROR"}
        rows = [row for row in _trace_rows(trace)
                if row["kind"] == "executed"
                and "command -v" in row["argv"][-1]]
        assert rows
        assert rows[-1]["client_cap_present"] is False
        assert rows[-1]["child_cap_present"] is True
        assert rows[-1]["child_cap"] == ""
        assert BAKED_SENTINEL not in json.dumps(rows[-1])
        assert os.environ[CAP] == AMBIENT_SENTINEL

    def test_generic_probe_builder_reaches_ephemeral_and_exec_children_empty(
            self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        trace, _state, _shim = _executing_docker(
            tmp_path, monkeypatch, target_container="persistent-runner")
        script = repo / "probe_child.py"
        artifact = tmp_path / "probe-child.json"
        script.write_text(textwrap.dedent(f"""\
            import json
            from pathlib import Path
            import os
            cap = {CAP!r}
            Path({str(artifact)!r}).write_text(json.dumps({{
                "present": cap in os.environ,
                "value": os.environ.get(cap),
            }}))
            """))
        base = {CAP: AMBIENT_SENTINEL, "PATH": os.environ["PATH"]}
        command = f"{shlex_quote_python()} {shlex_quote_path(script)}"
        for mode in ("ephemeral", "exec"):
            artifact.unlink(missing_ok=True)
            env = {
                "mode": mode,
                "image": "tester-unified:local",
                "container_name": "persistent-runner",
            }
            argv = run_gate.build_env_probe_argv(
                str(_shim), env, "caps-env", repo, repo, "fixture", "dev-gates.slice",
                command, resolved_container_name="persistent-runner")
            assert f"{CAP}=" in argv
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                env=run_gate.lane_caps_private_environ(base))
            assert proc.returncode == 0, proc.stderr
            assert json.loads(artifact.read_text()) == {
                "present": True, "value": ""}
        rows = _trace_rows(trace)
        assert rows[-1]["client_cap_present"] is False
        assert rows[-1]["child_cap_present"] is True
        assert rows[-1]["child_cap"] == ""

    def test_basic_sampler_exec_and_self_probe_use_real_empty_boundaries(
            self, tmp_path, monkeypatch):
        trace, _state, shim = _executing_docker(tmp_path, monkeypatch)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        sampler = run_gate.BasicSampler(str(shim), "sampled-container")
        sampler._read_container()
        rows = _trace_rows(trace)
        sampler_rows = [row for row in rows if row["kind"] == "executed"]
        assert sampler_rows
        assert sampler_rows[-1]["client_cap_present"] is False
        assert sampler_rows[-1]["child_cap_present"] is True
        assert sampler_rows[-1]["child_cap"] == ""
        assert _json_docker_execs(trace)[-1][2:4] == ["-e", f"{CAP}="]

        container_id, reason = run_gate.resolve_self_container_id(str(shim))
        assert reason is None
        assert container_id == "a" * 64
        self_rows = [row for row in _trace_rows(trace)
                     if row["kind"] == "executed"
                     and "/usr/bin/readlink" in row["argv"][-1]]
        assert self_rows[-1]["client_cap_present"] is False
        assert self_rows[-1]["child_cap_present"] is True
        assert self_rows[-1]["child_cap"] == ""

    def test_admission_and_systemd_children_strip_the_reserved_name(
            self, tmp_path, monkeypatch):
        directory = shim_dir_of(monkeypatch)
        trace = tmp_path / "aux-trace.jsonl"
        recorder = tmp_path / "record-env.py"
        recorder.write_text(textwrap.dedent(f"""\
            import json
            import os
            from pathlib import Path
            import sys
            payload = {{
                "argv": sys.argv[1:],
                "present": {CAP!r} in os.environ,
                "value": os.environ.get({CAP!r}),
            }}
            with Path({str(trace)!r}).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload) + "\\n")
            """))
        executable = directory / "record-env"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import runpy\n"
            f"runpy.run_path({str(recorder)!r}, run_name='__main__')\n")
        executable.chmod(executable.stat().st_mode | stat.S_IEXEC)
        proc = run_gate.lane_caps_admission_run(
            [str(executable), "docker", "info"],
            capture_output=True, text=True,
            env={CAP: AMBIENT_SENTINEL})
        assert proc.returncode == 0
        row = _trace_rows(trace)[-1]
        assert row["present"] is False and row["value"] is None

        systemctl = directory / "systemctl"
        systemctl.write_text(
            textwrap.dedent(f"""\
                #!{sys.executable}
                import json
                import os
                from pathlib import Path
                import sys
                payload = {{
                    "argv": sys.argv[1:],
                    "present": {CAP!r} in os.environ,
                    "value": os.environ.get({CAP!r}),
                }}
                with Path({str(trace)!r}).open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(payload) + "\\n")
                print("LoadState=loaded")
                print("FragmentPath=/etc/systemd/system/dev-gates.slice")
                """))
        systemctl.chmod(systemctl.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setattr(run_gate.os.path, "isdir", lambda path: True)
        monkeypatch.setenv(CAP, AMBIENT_SENTINEL)
        real_run = subprocess.run

        def recording_systemctl(argv, **kwargs):
            if argv[0] == "systemctl":
                return real_run(
                    [str(systemctl), *argv[1:]],
                    capture_output=True, text=True, env=kwargs.get("env"))
            return subprocess.CompletedProcess(argv, 1, "", "unexpected")

        # Directly prove the production call site, not only the helper it uses.
        monkeypatch.setattr(run_gate.subprocess, "run", recording_systemctl)
        run_gate.verify_slice_loaded("dev-gates.slice")
        row = _trace_rows(trace)[-1]
        assert row["argv"][:2] == ["show", "dev-gates.slice"]
        assert row["present"] is False and row["value"] is None


class TestReceiverFixture:
    FIXTURE = RUN_GATE_DIR / "tests" / "fixtures" / "lane_caps_probe.py"
    ELIGIBLE_LANE = "p7a-lane-caps-outer-smoke"
    EMPTY_LANE = "p7a-lane-caps-ineligible-smoke"
    EXPECTED_ELIGIBLE = (
        b'{"environment":"tester-unified","lane":"p7a-lane-caps-outer-smoke",'
        b'"memory_max_bytes":2147483648,"pids_max":8192}'
    )
    HANG_FAILSAFE_SECONDS = 60.0

    @staticmethod
    def _env(lane: str, mode: str) -> dict[str, str]:
        env = dict(os.environ)
        for name in OBSERVER_CONTROL_NAMES:
            env.pop(name, None)
        if mode == "eligible":
            env[CAP] = _expected(lane)
        else:
            env[CAP] = ""
        return env

    def _start_direct(self, socket_path: Path, artifact: Path, *,
                      lane: str | None = None, mode: str = "eligible",
                      env: dict[str, str] | None = None):
        lane = lane or self.ELIGIBLE_LANE
        process = subprocess.Popen(
            [sys.executable, str(self.FIXTURE), mode, lane,
             str(artifact), str(socket_path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env or self._env(lane, mode))
        readable, _, _ = select.select(
            [process.stdout], [], [], self.HANG_FAILSAFE_SECONDS)
        if not readable:
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(
                f"receiver readiness pipe timed out as a hang failsafe: "
                f"stdout={stdout!r} stderr={stderr!r}")
        ready = process.stdout.readline()
        match = re.fullmatch(rb"P7A_CAPS_READY ([0-9a-f]{32}) (.+)\n", ready)
        if match is None or match.group(2).decode() != str(socket_path):
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(
                f"receiver did not emit its exact READY line: "
                f"ready={ready!r} stdout={stdout!r} stderr={stderr!r}")
        return process, match.group(1).decode("ascii")

    def _finish(self, process: subprocess.Popen) -> tuple[bytes, bytes]:
        try:
            return process.communicate(timeout=self.HANG_FAILSAFE_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            pytest.fail(
                f"receiver did not finish before the test hang failsafe: "
                f"stdout={stdout!r} stderr={stderr!r}")

    def _send(self, socket_path: Path, chunks: tuple[bytes, ...]) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(self.HANG_FAILSAFE_SECONDS)
            client.connect(str(socket_path))
            for chunk in chunks:
                if chunk:
                    client.sendall(chunk)
            client.shutdown(socket.SHUT_WR)

    @pytest.mark.parametrize("state", ["absent", "wrong", "empty"])
    def test_eligible_environment_rejections_create_no_artifact(
            self, tmp_path, state):
        artifact = tmp_path / f"{state}.observed"
        env = self._env(self.ELIGIBLE_LANE, "eligible")
        if state == "absent":
            env.pop(CAP)
        elif state == "wrong":
            env[CAP] = "WRONG_RECEIVER_SENTINEL"
        else:
            env[CAP] = ""
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible",
             self.ELIGIBLE_LANE, str(artifact), "--posthoc"],
            capture_output=True, env=env)
        assert proc.returncode != 0
        assert b"refused" in proc.stderr
        assert not os.path.lexists(artifact)

    @pytest.mark.skipif(not hasattr(os, "environb"),
                        reason="byte-valued environments require POSIX")
    def test_invalid_utf8_cap_bytes_refuse_cleanly_without_artifact(
            self, tmp_path):
        artifact = tmp_path / "invalid-utf8.observed"
        env = os.environb.copy()
        for name in OBSERVER_CONTROL_NAMES:
            env.pop(name.encode("ascii"), None)
        env[CAP.encode("ascii")] = b"\xff\xfe"
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible",
             self.ELIGIBLE_LANE, str(artifact), "--posthoc"],
            capture_output=True, env=env)
        assert proc.returncode != 0
        assert b"refused" in proc.stderr
        assert not os.path.lexists(artifact)

    @pytest.mark.parametrize("observer_name", OBSERVER_CONTROL_NAMES)
    def test_every_observer_control_name_refuses_even_when_empty(
            self, tmp_path, observer_name):
        artifact = tmp_path / "leak.observed"
        env = self._env(self.ELIGIBLE_LANE, "eligible")
        env[observer_name] = ""
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible",
             self.ELIGIBLE_LANE, str(artifact), "--posthoc"],
            capture_output=True, env=env)
        assert proc.returncode != 0
        assert b"observer-control variables" in proc.stderr
        assert not os.path.lexists(artifact)

    def test_successful_eligible_command_uses_split_pass_and_eof(
            self, short_socket_root, tmp_path):
        socket_path = short_socket_root / "i.sock"
        artifact = tmp_path / "eligible.observed"
        process, nonce = self._start_direct(socket_path, artifact)
        assert re.fullmatch(r"[0-9a-f]{32}", nonce)
        assert not os.path.lexists(artifact)
        encoded = f"PASS {nonce}\n".encode()
        self._send(socket_path, (encoded[:13], encoded[13:]))
        stdout, stderr = self._finish(process)
        assert process.returncode == 0, stderr
        assert stdout == f"P7A_CAPS_OK eligible {self.ELIGIBLE_LANE}\n".encode()
        assert artifact.read_bytes() == self.EXPECTED_ELIGIBLE
        assert not os.path.lexists(socket_path)

    def test_successful_empty_command_writes_exactly_zero_bytes(
            self, short_socket_root, tmp_path):
        socket_path = short_socket_root / "i.sock"
        artifact = tmp_path / "empty.observed"
        process, nonce = self._start_direct(
            socket_path, artifact, lane=self.EMPTY_LANE, mode="empty")
        self._send(socket_path, (f"PASS {nonce}\n".encode(),))
        stdout, stderr = self._finish(process)
        assert process.returncode == 0, stderr
        assert stdout == f"P7A_CAPS_OK empty {self.EMPTY_LANE}\n".encode()
        assert artifact.read_bytes() == b""
        assert not os.path.lexists(socket_path)

    def test_assay_posthoc_is_explicit_and_needs_no_socket_or_ready(
            self, tmp_path):
        lane = "p7a-lane-caps-smoke"
        artifact = tmp_path / "assay.observed"
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible", lane,
             str(artifact), "--posthoc"], capture_output=True,
            env=self._env(lane, "eligible"))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == f"P7A_CAPS_OK eligible {lane}\n".encode()
        assert b"P7A_CAPS_READY" not in proc.stdout
        assert artifact.read_bytes() == (
            b'{"environment":"tester-unified","lane":"p7a-lane-caps-smoke",'
            b'"memory_max_bytes":2147483648,"pids_max":8192}')

    def test_legacy_three_argument_call_is_validation_only(self, tmp_path):
        artifact = tmp_path / "implicit-posthoc.observed"
        lane = self.ELIGIBLE_LANE
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible", lane,
             str(artifact)], capture_output=True,
            env=self._env(lane, "eligible"))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout == (
            f"P7A_CAPS_VALIDATION_ONLY eligible {lane}\n".encode())
        assert b"P7A_CAPS_READY" not in proc.stdout
        assert not os.path.lexists(artifact)

    @pytest.mark.parametrize("proof", ["--posthocX", "--unknown", "relative.sock"])
    def test_unknown_or_relative_fourth_argument_refuses_before_receiving(
            self, tmp_path, proof):
        artifact = tmp_path / "invalid-proof.observed"
        lane = self.ELIGIBLE_LANE
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible", lane,
             str(artifact), proof], capture_output=True,
            env=self._env(lane, "eligible"))
        assert proc.returncode != 0
        assert b"fourth argument" in proc.stderr
        assert not os.path.lexists(artifact)

    @pytest.mark.parametrize("case", [
        "literal-pass", "wrong-nonce", "missing-newline", "malformed",
        "truncated", "early-eof", "extra-after-valid", "oversized",
    ])
    def test_invalid_or_incomplete_decisions_refuse_without_artifact(
            self, short_socket_root, tmp_path, case):
        socket_path = short_socket_root / "i.sock"
        artifact = tmp_path / f"{case}.observed"
        process, nonce = self._start_direct(socket_path, artifact)
        valid = f"PASS {nonce}\n".encode()
        wrong_nonce = "1" * 32 if nonce != "1" * 32 else "2" * 32
        chunks = {
            "literal-pass": (b"PASS",),
            "wrong-nonce": (f"PASS {wrong_nonce}\n".encode(),),
            "missing-newline": (f"PASS {nonce}".encode(),),
            "malformed": (b"REJECT\n",),
            "truncated": (f"PASS {nonce[:11]}".encode(),),
            "early-eof": (),
            "extra-after-valid": (valid, b"X"),
            "oversized": (b"X" * 65,),
        }[case]
        self._send(socket_path, chunks)
        stdout, _stderr = self._finish(process)
        assert process.returncode != 0
        assert b"P7A_CAPS_OK" not in stdout
        assert not os.path.lexists(artifact)
        assert not os.path.lexists(socket_path)

    def test_previous_attempt_nonce_is_not_replayable(
            self, short_socket_root, tmp_path):
        socket_path = short_socket_root / "i.sock"
        first_artifact = tmp_path / "first.observed"
        first, previous_nonce = self._start_direct(socket_path, first_artifact)
        wrong_nonce = "1" * 32 if previous_nonce != "1" * 32 else "2" * 32
        self._send(socket_path, (f"PASS {wrong_nonce}\n".encode(),))
        first_stdout, _ = self._finish(first)
        assert first.returncode != 0
        assert not os.path.lexists(first_artifact)

        second_artifact = tmp_path / "second.observed"
        second, current_nonce = self._start_direct(socket_path, second_artifact)
        assert current_nonce != previous_nonce
        self._send(socket_path, (f"PASS {previous_nonce}\n".encode(),))
        second_stdout, _ = self._finish(second)
        assert second.returncode != 0
        assert b"P7A_CAPS_OK" not in first_stdout + second_stdout
        assert not os.path.lexists(second_artifact)

    def test_replaced_socket_and_stale_nonce_with_extra_bytes_fail_together(
            self, short_socket_root, tmp_path):
        socket_path = short_socket_root / "i.sock"
        old_artifact = tmp_path / "old.observed"
        old_process, stale_nonce = self._start_direct(socket_path, old_artifact)
        self._send(socket_path, (b"not a decision",))
        self._finish(old_process)
        assert old_process.returncode != 0

        artifact = tmp_path / "combined.observed"
        process, nonce = self._start_direct(socket_path, artifact)
        assert nonce != stale_nonce
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(self.HANG_FAILSAFE_SECONDS)
            client.connect(str(socket_path))
            os.unlink(socket_path)
            replacement.bind(str(socket_path))
            replacement.listen(1)
            replacement_stat = os.lstat(socket_path)
            assert stat.S_ISSOCK(replacement_stat.st_mode)
            client.sendall(f"PASS {stale_nonce}\n".encode())
            client.sendall(b"EXTRA")
            client.shutdown(socket.SHUT_WR)
            stdout, _stderr = self._finish(process)
        finally:
            client.close()
        assert process.returncode != 0
        assert b"P7A_CAPS_OK" not in stdout
        assert not os.path.lexists(artifact)
        current = os.lstat(socket_path)
        assert (current.st_dev, current.st_ino) == (
            replacement_stat.st_dev, replacement_stat.st_ino)
        replacement.close()
        os.unlink(socket_path)

    def test_preexisting_socket_path_is_refused_and_preserved(
            self, short_socket_root, tmp_path):
        socket_path = short_socket_root / "i.sock"
        owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        owner.bind(str(socket_path))
        owner.listen(1)
        before = os.lstat(socket_path)
        artifact = tmp_path / "preexisting-socket.observed"
        proc = subprocess.run(
            [sys.executable, str(self.FIXTURE), "eligible",
             self.ELIGIBLE_LANE, str(artifact), str(socket_path)],
            capture_output=True, env=self._env(self.ELIGIBLE_LANE, "eligible"))
        after = os.lstat(socket_path)
        owner.close()
        assert proc.returncode != 0
        assert b"already exists" in proc.stderr
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert not os.path.lexists(artifact)
        os.unlink(socket_path)

    @pytest.mark.parametrize("proof", ["command", "posthoc"])
    def test_preexisting_artifact_is_refused_and_left_unchanged(
            self, short_socket_root, tmp_path, proof):
        lane = self.ELIGIBLE_LANE if proof == "command" else "p7a-lane-caps-smoke"
        artifact = tmp_path / f"existing-{proof}.observed"
        original = b"another owner's artifact\x00\xff"
        artifact.write_bytes(original)
        if proof == "posthoc":
            proc = subprocess.run(
                [sys.executable, str(self.FIXTURE), "eligible", lane,
                 str(artifact), "--posthoc"], capture_output=True,
                env=self._env(lane, "eligible"))
        else:
            socket_path = short_socket_root / "i.sock"
            process, nonce = self._start_direct(socket_path, artifact)
            self._send(socket_path, (f"PASS {nonce}\n".encode(),))
            stdout, stderr = self._finish(process)
            proc = SimpleNamespace(returncode=process.returncode,
                                   stdout=stdout, stderr=stderr)
        assert proc.returncode != 0
        assert artifact.read_bytes() == original

    @pytest.mark.parametrize("proof", ["command", "posthoc"])
    def test_preexisting_artifact_symlink_is_refused_without_following(
            self, short_socket_root, tmp_path, proof):
        lane = self.ELIGIBLE_LANE if proof == "command" else "p7a-lane-caps-smoke"
        artifact = tmp_path / f"existing-link-{proof}.observed"
        target = tmp_path / f"target-{proof}.observed"
        original = b"symlink target remains byte-for-byte unchanged\x00\xff"
        target.write_bytes(original)
        artifact.symlink_to(target)
        if proof == "posthoc":
            proc = subprocess.run(
                [sys.executable, str(self.FIXTURE), "eligible", lane,
                 str(artifact), "--posthoc"], capture_output=True,
                env=self._env(lane, "eligible"))
        else:
            socket_path = short_socket_root / "i.sock"
            process, nonce = self._start_direct(socket_path, artifact)
            self._send(socket_path, (f"PASS {nonce}\n".encode(),))
            stdout, stderr = self._finish(process)
            proc = SimpleNamespace(returncode=process.returncode,
                                   stdout=stdout, stderr=stderr)
        assert proc.returncode != 0
        assert artifact.is_symlink()
        assert target.read_bytes() == original

    def test_registered_product_lanes_and_assay_receiver_match_contract(self):
        run_gate_config = tomllib.loads(
            (RUN_GATE_DIR / "run-gate.toml").read_text())
        assay_config = tomllib.loads(
            (RUN_GATE_DIR / "assay.toml").read_text())
        eligible = run_gate_config["lanes"]["p7a-lane-caps-smoke"]
        outer = run_gate_config["lanes"]["p7a-lane-caps-outer-smoke"]
        ineligible = run_gate_config["lanes"]["p7a-lane-caps-ineligible-smoke"]
        receiver = assay_config["lanes"]["p7a-lane-caps-smoke"]
        assert eligible["assay_lane"] == "p7a-lane-caps-smoke"
        assert eligible["resources"] == {"memory": "2g", "pids": 8192}
        assert outer["resources"] == {"memory": "2g", "pids": 8192}
        assert ineligible["resources"] == {"memory": "2g"}
        for lane in (outer, ineligible):
            assert lane["kind"] == "command"
            assert "tests/fixtures/lane_caps_probe.py" in lane["argv"][1]
            assert lane["argv"][-1] == \
                "{worktree}/run-gate-project/.run-gate/i.sock"
        assert receiver["env"] == {}
        assert receiver["env_required"] == [CAP]
        assert receiver["env_passthrough"] == [CAP]
        assert receiver["argv"][:5] == [
            "/opt/tester-venv/bin/python", "tests/fixtures/lane_caps_probe.py",
            "eligible", "p7a-lane-caps-smoke",
            ".run-gate/p7a-lane-caps-smoke.observed",
        ]
        assert receiver["argv"][-1] == "--posthoc"

        comments = (RUN_GATE_DIR / "run-gate.toml").read_text()
        assay_comments = (RUN_GATE_DIR / "assay.toml").read_text()
        assert "Assay captures child stdout until completion" in comments
        assert "direct command lanes use live READY/PASS" in comments
        assert "Assay captures child stdout until completion" in assay_comments

    def test_fixture_literal_bytes_are_independent_of_the_builder(self):
        sys.path.insert(0, str(RUN_GATE_DIR / "tests" / "fixtures"))
        try:
            import lane_caps_probe
        finally:
            sys.path.pop(0)
        assert lane_caps_probe.expected_eligible_bytes(
            "p7a-lane-caps-outer-smoke") == self.EXPECTED_ELIGIBLE


class TestDocumentationAndLedger:
    DOCS = (
        RUN_GATE_DIR / "README.md",
        RUN_GATE_DIR / "CONSUMERS.md",
        RUN_GATE_DIR / "docs" / "DESIGN-GUIDE.md",
    )

    @staticmethod
    def _fences(path: Path, marker: str) -> list[tuple[int, str]]:
        lines = path.read_text().splitlines()
        found = []
        for index, line in enumerate(lines):
            if line.strip() != "```toml":
                continue
            end = next((candidate for candidate in range(index + 1, len(lines))
                        if lines[candidate].strip() == "```"), None)
            assert end is not None
            tagged = index > 0 and lines[index - 1].strip() == marker
            if tagged:
                found.append((index + 1,
                              "\n".join(lines[index + 1:end])))
        return found

    def test_run_gate_examples_load_with_the_shipped_schema(self, tmp_path):
        parsed = 0
        for document in self.DOCS:
            for line, tagged, source in \
                    TestUserDocumentationOracles._toml_fences(document):
                assert tagged, (
                    f"{document.name}:{line}: Run-Gate example needs "
                    "<!-- run-gate-config --> immediately before its fence")
                config = tomllib.loads(source)
                assert config.get("schema_version") == run_gate.SCHEMA_VERSION
                run_gate._validate_config(
                    config,
                    tmp_path / f"{document.stem}-{line}.toml",
                    central="run-gate.root.toml" in source)
                parsed += 1
        assert parsed >= 3

    def test_assay_only_example_loads_with_the_current_assay_loader(
            self, tmp_path):
        examples = []
        for document in self.DOCS:
            examples.extend(
                (line, source)
                for line, source in self._fences(
                    document, "<!-- assay-config -->")
                if "schema_version = 2" in source)
        assert len(examples) == 1
        source_dir = RUN_GATE_DIR.parent / "assay" / "src"
        assert source_dir.is_dir()
        for line, source in examples:
            path = tmp_path / f"assay-{line}.toml"
            path.write_text(source)
            code = textwrap.dedent(f"""\
                import sys
                from pathlib import Path
                sys.path.insert(0, {str(source_dir)!r})
                from assay.config import LANE_SCHEMA_VERSION, load_lane_file
                lane_file = load_lane_file(Path({str(path)!r}))
                assert lane_file.schema_version == LANE_SCHEMA_VERSION == 2
                """)
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True)
            assert proc.returncode == 0, proc.stderr

    def test_documented_assay_consumer_forwards_present_value_including_empty(
            self, tmp_path):
        examples = [
            source
            for document in self.DOCS
            for _line, source in self._fences(
                document, "<!-- assay-config -->")
            if "schema_version = 2" in source
            and "env_required = [\"RUN_GATE_LANE_CAPS_V1\"]" in source]
        assert len(examples) == 1
        path = tmp_path / "documented-assay.toml"
        path.write_text(examples[0])
        source_dir = RUN_GATE_DIR.parent / "assay" / "src"
        value = _expected("caps-consumer")
        code = textwrap.dedent(f"""\
            import sys
            from pathlib import Path
            sys.path.insert(0, {str(source_dir)!r})
            from assay.config import load_lane_file
            from assay.runner import resolve_command_plan

            cap = {CAP!r}
            lane = load_lane_file(Path({str(path)!r})).lane("caps-consumer")
            assert lane.env_required == (cap,)
            assert cap in lane.effective_env_passthrough
            for expected in ({value!r}, ""):
                source = {{cap: expected}}
                plan = resolve_command_plan(
                    lane, passthrough_source=source)
                assert plan.env_effective[cap] == expected
            assert cap not in resolve_command_plan(
                lane, passthrough_source={{}}).env_effective
            """)
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr

    def test_docs_vocabulary_and_links_cover_the_lane_caps_contract(self):
        corpus = "\n".join(path.read_text() for path in self.DOCS)
        for value in (
                CAP, RECORD, "ephemeral", "exec", "host", "resources.memory",
                "resources.pids", "exact value through a name-only",
                "present and exactly empty", "Unknown-schema"):
            assert value in corpus, value
        for source in self.DOCS:
            prose = TestUserDocumentationOracles._markdown_without_fences(source)
            for match in re.finditer(r"\]\(([^)\n]+)\)", prose):
                destination = match.group(1).split(maxsplit=1)[0].strip("<>")
                if destination.startswith(("http://", "https://", "mailto:")):
                    continue
                target_text, separator, fragment = destination.partition("#")
                if not separator:
                    continue
                target = ((source.parent / target_text).resolve()
                          if target_text else source.resolve())
                assert target.is_file()
                if target.suffix.lower() == ".md":
                    assert unquote(fragment) in \
                        TestUserDocumentationOracles._heading_slugs(target)

    def test_d001_and_release_records_do_not_claim_public_release(self):
        ledger = (RUN_GATE_DIR / "nyxloom-trove" / "decisions.md").read_text()
        exact = ledger[ledger.index("## D-001 "):].rstrip()
        assert exact == D001_EXACT
        assert ledger.count("D-001 — Selected lane caps as an exact launch "
                            "and reattachment fact") == 1
        for statement in (
                "No host, image, swap, or ambient fallback is permitted.",
                "Missing is distinct from null.",
                "Every unknown-schema inflight record refuses, including "
                "under --fresh",
                "Public wheel release requires separate operator approval"):
            assert statement in ledger
        changes = (RUN_GATE_DIR / "CHANGES.md").read_text()
        assert "P7.0a candidate only, not released" in changes
        backlog = (RUN_GATE_DIR / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text()
        assert "RG-293" in backlog
        assert "not publicly released" in backlog
        spec = (RUN_GATE_DIR / "SPEC.md").read_text()
        assert "**`--fresh`** → remove the named container (by name" \
            not in spec
        assert ("Host lanes and exec lanes start no container of run-gate's "
                "own, so they have no record") not in spec
        assert "exec lanes write a schema-2 recovery record" in spec
        assert "remove only the inspected ID" in spec


class TestStatusAndWriterObservability:
    def test_legacy_schema_two_record_stays_visible_without_cap_claims(
            self, tmp_path, monkeypatch, capsys):
        repo, project, _script, _artifact = _make_single_project(tmp_path)
        log, state = fake_docker_stateful(tmp_path, monkeypatch)
        record = plant_inflight(project, repo, state, lane="caps",
                                container="legacy-container")
        document = json.loads(record.read_text())
        del document[RECORD]
        record.write_text(json.dumps(document))
        before = record.read_bytes()
        monkeypatch.setattr(run_gate.shutil, "which", lambda _name: None)
        code = run_gate.cmd_status(
            project, run_gate.load_config_snapshot(project)[0],
            run_gate.load_config_snapshot(project)[1], json_mode=True)
        output = json.loads(capsys.readouterr().out)
        assert code == 2  # Docker absence is reported, the record is not hidden.
        shown = next(item for item in output["inflight"]
                     if item["lane"] == "caps")
        assert shown["container"] == "legacy-container"
        assert RECORD not in shown
        assert "reattach" not in json.dumps(shown).lower()
        assert record.read_bytes() == before
        assert state_containers(state) == ["legacy-container"]

    def test_lane_caps_record_write_is_best_effort_no_durable_claim(
            self, tmp_path, monkeypatch, capsys):
        repo, project, _script, artifact = _make_single_project(tmp_path)
        _executing_docker(tmp_path, monkeypatch)

        def fail(*_args, **_kwargs):
            raise OSError("record disk unavailable")

        monkeypatch.setattr(run_gate, "_write_json_atomic", fail)
        result = _dispatch(monkeypatch, project, "caps")
        output = capsys.readouterr()
        assert result.verdict == "PASS"
        assert artifact.is_file()
        assert "record disk unavailable" in output.err
        assert not run_gate.inflight_path(project, "caps").exists()

    def test_best_effort_writer_helper_removes_reserved_name_for_children(
            self, tmp_path):
        source = {CAP: AMBIENT_SENTINEL, "PATH": os.environ["PATH"]}
        private = run_gate.lane_caps_private_environ(source)
        assert CAP not in private
        assert source[CAP] == AMBIENT_SENTINEL


def shlex_quote_python() -> str:
    import shlex
    return shlex.quote(sys.executable)


def shlex_quote_path(path: Path) -> str:
    import shlex
    return shlex.quote(str(path))
