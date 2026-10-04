"""CIU-115/119: migrate old generated identity only after resource checks."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, procutil, workspace_env  # noqa: E402


OLD_FACTS = {
    "repo_name": "vbpub",
    "instance_id": "ab12cd",
    "network": "vbpub-ab12cd-network",
    "physical_repo_root": "/host/checkouts/vbpub",
    "repo_root": "/workspaces/vbpub",
    "public_fqdn": "example.test",
}


def _write_old_facts(root: Path, facts: dict | None = None) -> Path:
    path = workspace_env.generated_facts_path(root)
    lines = workspace_env.render_generated_facts_block(facts or OLD_FACTS)
    path.write_text("\n".join(line for line in lines if not line.startswith("schema_version = ")) + "\n",
                    encoding="utf-8")
    return path


def _docker_empty(*_args, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_outdated_identity_repairs_only_when_old_network_and_labels_are_absent(
    tmp_path, monkeypatch, capsys,
):
    path = _write_old_facts(tmp_path)
    new_facts = {**OLD_FACTS, "instance_id": "cd34ef", "network": "vbpub-cd34ef-network"}
    monkeypatch.setattr(procutil, "docker", _docker_empty)

    def regenerate(root, *, notice_stream=None):
        workspace_env.write_generated_facts(root, new_facts)
        return root / "ciu.env"

    monkeypatch.setattr(workspace_env, "generate_ciu_env", regenerate)
    assert workspace_env.read_generated_facts(tmp_path) == new_facts
    assert "ab12cd -> cd34ef" in capsys.readouterr().err
    assert "schema_version = 2" in path.read_text(encoding="utf-8")


def test_outdated_identity_with_old_id_resource_refuses_and_names_cleanup(
    tmp_path, monkeypatch,
):
    path = _write_old_facts(tmp_path)
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        if args[:2] == ["network", "ls"] and any(
            arg.startswith("name=") for arg in args
        ):
            return _docker_empty()
        if "label=ciu.instance=ab12cd" in args:
            return SimpleNamespace(
                returncode=0,
                stdout="cid-old\t/host/checkouts/vbpub\tcustom-project\n"
                if args[0] == "ps" else "",
                stderr="",
            )
        return _docker_empty()

    monkeypatch.setattr(procutil, "docker", docker)
    monkeypatch.setattr(
        workspace_env,
        "generate_ciu_env",
        lambda *_a, **_kw: pytest.fail("must refuse before rewriting old identity"),
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match=r"ciu clean --identity ab12cd"):
        workspace_env.read_generated_facts(tmp_path)
    assert "schema_version = 2" not in path.read_text(encoding="utf-8")
    assert any("label=ciu.instance=ab12cd" in call for call in calls)


def test_matching_id_label_for_another_checkout_blocks_migration(tmp_path, monkeypatch):
    _write_old_facts(tmp_path)

    def docker(args, **kwargs):
        if "label=ciu.instance=ab12cd" in args and args[0] == "ps":
            return SimpleNamespace(
                returncode=0,
                stdout="cid-other\t/host/other/vbpub\tcustom-project\n",
                stderr="",
            )
        return _docker_empty()

    monkeypatch.setattr(procutil, "docker", docker)
    with pytest.raises(workspace_env.WorkspaceEnvError, match="not '/host/checkouts/vbpub'"):
        workspace_env.read_generated_facts(tmp_path)


def test_current_schema_corruption_is_not_rewritten(tmp_path, monkeypatch):
    path = workspace_env.write_generated_facts(tmp_path, OLD_FACTS)
    path.write_text(
        path.read_text(encoding="utf-8").replace('instance_id = "ab12cd"', "instance_id = 12"),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workspace_env, "generate_ciu_env",
        lambda *_a, **_kw: pytest.fail("current-schema corruption must never be repaired"),
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match="not a string"):
        workspace_env.read_generated_facts(tmp_path)


def test_read_only_and_unidentifiable_old_records_refuse_without_repair(tmp_path, monkeypatch):
    _write_old_facts(tmp_path)
    monkeypatch.setattr(
        workspace_env, "generate_ciu_env",
        lambda *_a, **_kw: pytest.fail("read-only identity lookup must not repair"),
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match="read-only operation will not repair"):
        workspace_env.read_generated_facts(tmp_path, allow_repair=False)
    assert workspace_env.outdated_generated_identity(tmp_path) == OLD_FACTS

    _write_old_facts(tmp_path, {**OLD_FACTS, "instance_id": "", "network": ""})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="missing instance_id or network"):
        workspace_env.read_generated_facts(tmp_path)


@pytest.mark.parametrize("missing_key", ["instance_id", "network"])
def test_outdated_identity_refuses_when_either_identity_fact_is_missing(
    tmp_path, missing_key,
):
    path = _write_old_facts(tmp_path, {**OLD_FACTS, missing_key: ""})
    before = path.read_bytes()

    with pytest.raises(
        workspace_env.WorkspaceEnvError,
        match="missing instance_id or network",
    ):
        workspace_env.outdated_generated_identity(tmp_path)

    assert path.read_bytes() == before


def test_float_current_schema_version_is_not_accepted_as_integer(tmp_path):
    path = _write_old_facts(tmp_path)
    body = path.read_text(encoding="utf-8")
    path.write_text(
        body.replace(
            workspace_env.GENERATED_FACTS_HEADER + "\n",
            workspace_env.GENERATED_FACTS_HEADER + "\nschema_version = 2.0\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match="schema_version"):
        workspace_env.read_generated_facts(tmp_path)


def test_outdated_identity_inspector_refuses_malformed_and_unidentifiable_shapes(tmp_path):
    path = workspace_env.generated_facts_path(tmp_path)
    path.write_text("ciu = 1\n", encoding="utf-8")
    with pytest.raises(workspace_env.WorkspaceEnvError, match="nests 'instance'"):
        workspace_env.outdated_generated_identity(tmp_path)

    path.write_text("[ciu.instance]\ngenerated = 1\n", encoding="utf-8")
    with pytest.raises(workspace_env.WorkspaceEnvError, match="generated.*not a table"):
        workspace_env.outdated_generated_identity(tmp_path)

    path = _write_old_facts(tmp_path)
    body = path.read_text(encoding="utf-8")
    path.write_text(
        body.replace(
            workspace_env.GENERATED_FACTS_HEADER + "\n",
            workspace_env.GENERATED_FACTS_HEADER + "\nschema_version = 2.0\n",
        ),
        encoding="utf-8",
    )
    with pytest.raises(workspace_env.WorkspaceEnvError, match="schema_version"):
        workspace_env.outdated_generated_identity(tmp_path)

    _write_old_facts(tmp_path, {**OLD_FACTS, "instance_id": "", "network": ""})
    with pytest.raises(workspace_env.WorkspaceEnvError, match="missing instance_id or network"):
        workspace_env.outdated_generated_identity(tmp_path)


@pytest.mark.parametrize(
    ("machine_table", "message"),
    [
        ("[ciu.instance]\nmachine = 1\n", "is not a table"),
        ("[ciu.instance.machine]\n", "outdated machine facts invalid"),
        (
            "[ciu.instance.machine]\nschema_version = 3\n",
            "schema_version",
        ),
    ],
)
def test_old_machine_fact_table_must_be_known_and_well_formed(
    tmp_path, machine_table, message,
):
    path = _write_old_facts(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + machine_table)
    with pytest.raises(workspace_env.WorkspaceEnvError, match=message):
        workspace_env.read_generated_facts(tmp_path, allow_repair=False)


def test_old_machine_facts_reject_non_string_and_accept_current_machine_version(tmp_path):
    path = _write_old_facts(tmp_path)
    machine = {key: "value" for key in workspace_env.MACHINE_FACTS_KEYS}
    machine["user_uid"] = 12
    lines = ["[ciu.instance.machine]", "schema_version = 1"]
    lines.extend(f'{key} = "{value}"' if isinstance(value, str) else f"{key} = {value}" for key, value in machine.items())
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "\n".join(lines) + "\n")
    with pytest.raises(workspace_env.WorkspaceEnvError, match="not a string"):
        workspace_env.read_generated_facts(tmp_path, allow_repair=False)

    machine["user_uid"] = "1000"
    lines = ["[ciu.instance.machine]", f"schema_version = {workspace_env.GENERATED_FACTS_SCHEMA_VERSION}"]
    lines.extend(f'{key} = "{value}"' for key, value in machine.items())
    path.write_text(
        "\n".join(line for line in workspace_env.render_generated_facts_block(OLD_FACTS)
                   if not line.startswith("schema_version = "))
        + "\n\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    assert workspace_env.outdated_generated_identity(tmp_path) == OLD_FACTS


def test_machine_fact_reader_rejects_non_table_parent_and_unknown_keys(tmp_path):
    path = _write_old_facts(tmp_path)
    with pytest.raises(workspace_env.WorkspaceEnvError, match="malformed \\[ciu.instance.machine\\]"):
        workspace_env._validate_outdated_machine_table(path, {"ciu": 1})

    machine = {key: "value" for key in workspace_env.MACHINE_FACTS_KEYS}
    lines = ["[ciu.instance.machine]", "schema_version = 1"]
    lines.extend(f'{key} = "{value}"' for key, value in machine.items())
    lines.append('unknown = "value"')
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "\n".join(lines) + "\n")
    with pytest.raises(workspace_env.WorkspaceEnvError, match="unknown=.*unknown"):
        workspace_env.read_generated_facts(tmp_path, allow_repair=False)


def test_identity_label_scan_validates_checkout_and_collects_projects(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append((args, kwargs))
        kind = args[0] if args[0] != "volume" else "volume"
        return SimpleNamespace(
            returncode=0,
            stdout={
                "ps": "cid\t/physical/repo\tproject-a\n\n",
                "volume": "vol\t/physical/repo\tproject-b\n",
                "network": "net\t/physical/repo\t<no value>\n",
            }[kind] if kwargs.get("capture") else None,
            stderr="",
        )

    monkeypatch.setattr(procutil, "docker", docker)
    resources = workspace_env._identity_labeled_resources(
        "ab12cd", expected_checkout="/physical/repo"
    )
    assert resources == {
        "container": ["cid"], "volume": ["vol"], "network": ["net"],
        "project": ["project-a", "project-b"],
    }
    assert len(calls) == 3
    assert all(kwargs == {"capture": True, "check": False} for _, kwargs in calls)


@pytest.mark.parametrize(
    "docker",
    [
        lambda *_a, **_kw: (_ for _ in ()).throw(FileNotFoundError("docker")),
        lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout="", stderr="denied"),
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="cid\t<no value>\t\n", stderr=""),
    ],
)
def test_identity_label_scan_refuses_unreadable_or_unowned_resources(monkeypatch, docker):
    monkeypatch.setattr(procutil, "docker", docker)
    with pytest.raises(workspace_env.WorkspaceEnvError):
        workspace_env._identity_labeled_resources("ab12cd")


def test_identity_project_labels_include_exact_old_prefix_and_owned_labels(monkeypatch):
    calls = []

    def docker(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(
            returncode=0,
            stdout="repo-ab12cd-one\nother-project\n" if len(calls) == 1 else "",
            stderr="",
        )

    monkeypatch.setattr(procutil, "docker", docker)
    monkeypatch.setattr(
        workspace_env, "_identity_labeled_resources",
        lambda *_a, **_kw: {"project": ["repo-ab12cd-labelled"]},
    )
    assert workspace_env._identity_project_labels("ab12cd", "repo") == {
        "repo-ab12cd-one", "repo-ab12cd-labelled",
    }


def test_identity_project_label_scan_captures_without_checking(monkeypatch):
    calls = []

    def docker(args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(procutil, "docker", docker)
    monkeypatch.setattr(
        workspace_env, "_identity_labeled_resources",
        lambda *_a, **_kw: {"project": []},
    )
    assert workspace_env._identity_project_labels("ab12cd", "repo") == set()
    assert len(calls) == 3
    assert all(kwargs == {"capture": True, "check": False} for _, kwargs in calls)


@pytest.mark.parametrize(
    "docker",
    [
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("daemon")),
        lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout="", stderr="denied"),
    ],
)
def test_identity_project_label_scan_refuses_docker_errors(monkeypatch, docker):
    monkeypatch.setattr(procutil, "docker", docker)
    with pytest.raises(workspace_env.WorkspaceEnvError, match="cannot check Docker resources"):
        workspace_env._identity_project_labels("ab12cd", "repo")


def test_identity_project_label_scan_keeps_stdout_error_detail(monkeypatch):
    monkeypatch.setattr(
        procutil, "docker",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=1, stdout="daemon query detail", stderr=""
        ),
    )
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc_info:
        workspace_env._identity_project_labels("ab12cd", "repo")
    assert "daemon query detail" in str(exc_info.value)


def test_old_identity_resource_check_covers_network_labels_projects_and_errors(monkeypatch):
    facts = {**OLD_FACTS}
    monkeypatch.setattr(
        procutil, "docker",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="vbpub-ab12cd-network\n", stderr=""),
    )
    assert workspace_env._old_identity_resources_exist(facts)

    monkeypatch.setattr(
        procutil, "docker",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        workspace_env, "_identity_labeled_resources",
        lambda *_a, **_kw: {"container": [], "volume": [], "network": [], "project": []},
    )
    monkeypatch.setattr(workspace_env, "_identity_project_labels", lambda *_a, **_kw: set())
    assert not workspace_env._old_identity_resources_exist(facts)
    monkeypatch.setattr(workspace_env, "_identity_project_labels", lambda *_a, **_kw: {"repo-ab12cd-old"})
    assert workspace_env._old_identity_resources_exist(facts)

    monkeypatch.setattr(procutil, "docker", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("daemon")))
    with pytest.raises(workspace_env.WorkspaceEnvError, match="cannot check Docker network"):
        workspace_env._old_identity_resources_exist(facts)


def test_old_identity_network_scan_keeps_stdout_error_detail(monkeypatch):
    facts = {**OLD_FACTS}
    monkeypatch.setattr(
        procutil, "docker",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=1, stdout="network query detail", stderr=""
        ),
    )
    with pytest.raises(workspace_env.WorkspaceEnvError) as exc_info:
        workspace_env._old_identity_resources_exist(facts)
    assert "network query detail" in str(exc_info.value)


def test_old_identity_network_check_requires_captured_docker_output(monkeypatch):
    facts = {**OLD_FACTS}
    network = facts["network"]
    calls = []

    def docker(args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        captured = kwargs.get("capture") is True
        output = f"{network}\n" if captured else ""
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(procutil, "docker", docker)
    assert workspace_env._old_identity_resources_exist(facts)
    assert calls == [(
        ["network", "ls", "--filter", f"name={network}", "--format", "{{.Name}}"],
        {"capture": True, "check": False},
    )]


def test_old_identity_network_filter_handles_docker_substring_matching_exactly(
    monkeypatch,
):
    facts = {**OLD_FACTS}
    network = facts["network"]
    calls = []

    def docker(args, **_kwargs):
        calls.append(args)
        if args[:2] == ["network", "ls"] and "--filter" in args:
            filter_value = args[args.index("--filter") + 1]
            if filter_value.startswith("name="):
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"{network}\n{network}-suffix\n",
                    stderr="",
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(procutil, "docker", docker)
    assert workspace_env._old_identity_resources_exist(facts)
    name_query = next(
        args for args in calls
        if args[:2] == ["network", "ls"]
        and "--filter" in args
        and args[args.index("--filter") + 1].startswith("name=")
    )
    assert name_query == [
        "network", "ls", "--filter", f"name={network}",
        "--format", "{{.Name}}",
    ]

    calls.clear()

    def prefix_only(args, **_kwargs):
        calls.append(args)
        if args[:2] == ["network", "ls"] and "--filter" in args:
            filter_value = args[args.index("--filter") + 1]
            if filter_value.startswith("name="):
                return SimpleNamespace(
                    returncode=0,
                    stdout=f"{network}-suffix\n",
                    stderr="",
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(procutil, "docker", prefix_only)
    monkeypatch.setattr(
        workspace_env, "_identity_labeled_resources",
        lambda *_a, **_kw: {"container": [], "volume": [], "network": [], "project": []},
    )
    monkeypatch.setattr(workspace_env, "_identity_project_labels", lambda *_a, **_kw: set())
    assert not workspace_env._old_identity_resources_exist(facts)
    monkeypatch.setattr(procutil, "docker", lambda *_a, **_kw: SimpleNamespace(returncode=1, stdout="", stderr="denied"))
    with pytest.raises(workspace_env.WorkspaceEnvError, match="cannot check Docker network"):
        workspace_env._old_identity_resources_exist(facts)


@pytest.mark.parametrize("document", ["", "[ciu]\ninstance = 1\n", "[ciu.instance]\ngenerated = 2\n"])
def test_current_generated_identity_reader_returns_empty_for_absent_or_non_table(tmp_path, document):
    path = workspace_env.generated_facts_path(tmp_path)
    path.write_text(document, encoding="utf-8")
    assert workspace_env._read_current_generated_identity(tmp_path) == {}


def test_clean_identity_removes_only_exact_checkout_ownership_labels(tmp_path, monkeypatch):
    _write_old_facts(tmp_path)
    calls = []

    def docker(args, **kwargs):
        calls.append(args)
        if "label=ciu.instance=ab12cd" in args and args[0] == "ps":
            return SimpleNamespace(
                returncode=0,
                stdout="cid-old\t/host/checkouts/vbpub\tcustom-project\n",
                stderr="",
            )
        if "label=ciu.instance=ab12cd" in args:
            return _docker_empty()
        if args[:2] == ["network", "ls"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return _docker_empty()

    monkeypatch.setattr(deploy.procutil, "docker", docker)
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda _root: Path("/host/checkouts/vbpub"))
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 0
    removals = [call for call in calls if call[:2] == ["rm", "-f"]]
    assert removals == [["rm", "-f", "cid-old"]]
    assert not any(
        call[:2] == ["volume", "rm"] or call[:2] == ["network", "rm"]
        for call in calls
    )
    assert not any(
        call[:2] == ["ps", "-aq"] and any("custom-project" in value for value in call)
        for call in calls
    )


def _clean_identity_helpers(monkeypatch, *, outdated=None, current=None, projects=(), labelled=None):
    monkeypatch.setattr(workspace_env, "outdated_generated_identity", lambda *_a: outdated)
    if current is None:
        current = {}
    if isinstance(current, BaseException):
        monkeypatch.setattr(
            workspace_env, "read_generated_facts",
            lambda *_a, **_kw: (_ for _ in ()).throw(current),
        )
    else:
        monkeypatch.setattr(workspace_env, "read_generated_facts", lambda *_a, **_kw: current)
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root",
        lambda *_a: (_ for _ in ()).throw(OSError("no mount fact")),
    )
    if labelled is None:
        labelled = {"container": [], "volume": [], "network": []}
    monkeypatch.setattr(
        workspace_env, "_identity_labeled_resources", lambda *_a, **_kw: labelled,
    )
    monkeypatch.setattr(
        workspace_env, "_identity_project_labels", lambda *_a, **_kw: set(projects),
    )
    monkeypatch.setattr(procutil, "docker", _docker_empty)


def test_clean_identity_removes_labelled_and_legacy_project_resources(tmp_path, monkeypatch):
    repo_name = tmp_path.name.lower()
    _clean_identity_helpers(
        monkeypatch,
        outdated={**OLD_FACTS},
        labelled={
            "container": ["cid"], "volume": ["vol"], "network": ["label-net"],
        },
        projects=[f"{repo_name}-ab12cd-legacy"],
    )
    calls = []

    def docker(args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        if args[0:2] == ["ps", "-aq"]:
            return SimpleNamespace(returncode=0, stdout="legacy-cid\n", stderr="")
        if args[0:2] == ["volume", "ls"]:
            return SimpleNamespace(returncode=0, stdout="legacy-vol\n", stderr="")
        if args[0:2] == ["network", "ls"] and "--filter" in args:
            return SimpleNamespace(returncode=0, stdout="legacy-net\n", stderr="")
        if args == ["network", "ls", "--format", "{{.Name}}"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"vbpub-ab12cd-network\n{repo_name}-ab12cd-network\n",
                stderr="",
            )
        return _docker_empty()

    monkeypatch.setattr(procutil, "docker", docker)
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 0
    argv_calls = [args for args, _kwargs in calls]
    assert [call for call in argv_calls if call[:2] == ["rm", "-f"]] == [
        ["rm", "-f", "cid"], ["rm", "-f", "legacy-cid"],
    ]
    assert [call for call in argv_calls if call[:2] == ["volume", "rm"]] == [
        ["volume", "rm", "vol"], ["volume", "rm", "legacy-vol"],
    ]
    assert [call for call in argv_calls if call[:2] == ["network", "rm"]] == [
        ["network", "rm", "label-net"], ["network", "rm", "legacy-net"],
        ["network", "rm", f"{repo_name}-ab12cd-network"],
        ["network", "rm", "vbpub-ab12cd-network"],
    ]
    assert all(kwargs == {"capture": True, "check": False} for _, kwargs in calls)


def test_clean_identity_uses_read_only_current_checkout_fact(tmp_path, monkeypatch):
    observed = {}
    current = {"physical_repo_root": "/physical/selected"}
    monkeypatch.setattr(workspace_env, "outdated_generated_identity", lambda *_a: None)

    def read_facts(_root, *, allow_repair=True):
        observed["allow_repair"] = allow_repair
        return current

    def labeled(_instance_id, *, expected_checkout=None):
        observed["expected_checkout"] = expected_checkout
        return {"container": [], "volume": [], "network": []}

    monkeypatch.setattr(workspace_env, "read_generated_facts", read_facts)
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root",
        lambda *_a: pytest.fail("current checkout fact must avoid host-root re-derivation"),
    )
    monkeypatch.setattr(workspace_env, "_identity_labeled_resources", labeled)
    monkeypatch.setattr(workspace_env, "_identity_project_labels", lambda *_a, **_kw: set())
    monkeypatch.setattr(procutil, "docker", _docker_empty)

    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 0
    assert observed == {
        "allow_repair": False,
        "expected_checkout": "/physical/selected",
    }


def test_clean_identity_validation_cancel_and_indeterminate_helpers(tmp_path, monkeypatch, capsys):
    assert deploy.action_clean_identity(tmp_path, "BADID", yes=True) == 2
    bad_repo = tmp_path / "bad!"
    assert deploy.action_clean_identity(bad_repo, "ab12cd", yes=True) == 2

    monkeypatch.setattr("builtins.input", lambda _prompt: "no")
    assert deploy.action_clean_identity(tmp_path, "ab12cd") == 0
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))
    assert deploy.action_clean_identity(tmp_path, "ab12cd") == 0

    _clean_identity_helpers(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    assert deploy.action_clean_identity(tmp_path, "ab12cd") == 0
    monkeypatch.setattr(
        workspace_env, "outdated_generated_identity",
        lambda *_a: (_ for _ in ()).throw(workspace_env.WorkspaceEnvError("corrupt")),
    )
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 0
    assert "could not read" in capsys.readouterr().out

    monkeypatch.setattr(
        workspace_env, "_identity_labeled_resources",
        lambda *_a, **_kw: (_ for _ in ()).throw(workspace_env.WorkspaceEnvError("ownership unknown")),
    )
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 1


def test_clean_identity_records_failures_without_touching_unlabelled_resources(
    tmp_path, monkeypatch, capsys,
):
    repo_name = tmp_path.name.lower()
    _clean_identity_helpers(
        monkeypatch,
        outdated={**OLD_FACTS, "instance_id": "other"},
        current=workspace_env.WorkspaceEnvError("corrupt current"),
        labelled={"container": ["bad-cid"], "volume": ["bad-vol"], "network": []},
        projects=[
            f"{repo_name}-ab12cd-old", f"{repo_name}-ab12cd-remove",
            f"{repo_name}-ab12cd-$invalid",
        ],
    )

    def docker(args, **_kwargs):
        if args == ["rm", "-f", "bad-cid"]:
            raise OSError("socket")
        if args == ["volume", "rm", "bad-vol"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="busy")
        if args[0:2] == ["ps", "-aq"]:
            if f"{repo_name}-ab12cd-old" in args[-1]:
                return SimpleNamespace(returncode=1, stdout="", stderr="cannot list")
            return SimpleNamespace(returncode=0, stdout="legacy-cid\n", stderr="")
        if args[0:2] == ["volume", "ls"]:
            if f"{repo_name}-ab12cd-old" in args[-1]:
                raise OSError("daemon stopped")
            return SimpleNamespace(returncode=0, stdout="legacy-vol\n", stderr="")
        if args[0:2] == ["network", "ls"] and "--filter" in args:
            if f"{repo_name}-ab12cd-old" in args[-1]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="legacy-net\n", stderr="")
        if args == ["rm", "-f", "legacy-cid"]:
            raise OSError("remove socket")
        if args == ["volume", "rm", "legacy-vol"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="volume busy")
        if args == ["network", "ls", "--format", "{{.Name}}"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="daemon stopped")
        return _docker_empty()

    monkeypatch.setattr(procutil, "docker", docker)
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "busy" in output
    assert "cannot list" in output
    assert "daemon stopped" in output


def test_clean_identity_uses_stdout_when_docker_failure_has_no_stderr(
    tmp_path, monkeypatch, capsys,
):
    _clean_identity_helpers(
        monkeypatch,
        labelled={"container": ["bad-cid"], "volume": [], "network": []},
    )

    def docker(args, **_kwargs):
        if args == ["rm", "-f", "bad-cid"]:
            return SimpleNamespace(
                returncode=17, stdout="failure reported on stdout", stderr="",
            )
        return _docker_empty()

    monkeypatch.setattr(procutil, "docker", docker)
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 1
    captured = capsys.readouterr()
    assert "failure reported on stdout" in captured.out + captured.err


def test_clean_identity_network_removal_failure_is_reported(tmp_path, monkeypatch, capsys):
    _clean_identity_helpers(monkeypatch)
    network = f"{tmp_path.name.lower()}-ab12cd-network"

    def docker(args, **_kwargs):
        if args == ["network", "ls", "--format", "{{.Name}}"]:
            return SimpleNamespace(returncode=0, stdout=f"{network}\n", stderr="")
        if args == ["network", "rm", network]:
            return SimpleNamespace(returncode=1, stdout="", stderr="in use")
        return _docker_empty()

    monkeypatch.setattr(procutil, "docker", docker)
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "in use" in output


def test_clean_identity_reports_docker_network_list_exception(tmp_path, monkeypatch, capsys):
    _clean_identity_helpers(monkeypatch)
    monkeypatch.setattr(
        procutil, "docker",
        lambda args, **_kw: (
            (_ for _ in ()).throw(OSError("daemon gone"))
            if args == ["network", "ls", "--format", "{{.Name}}"]
            else _docker_empty()
        ),
    )
    assert deploy.action_clean_identity(tmp_path, "ab12cd", yes=True) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "daemon gone" in output
