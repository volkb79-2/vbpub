"""Behavioral command ownership and parser-to-handler contracts."""

from __future__ import annotations

import importlib
import re
import runpy
import shlex
import sys
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from cli_extended import CliFailure

from nyxloom.cli_registry import _extract_guard, _opt, harness_cli, operator_cli, primary_cli


def _stub_handlers(registered, calls, prefix=()):
    handlers = {}
    for name in registered.handlers:
        path = (*prefix, name)

        def handler(args, runtime, command_path=path):
            calls.append((command_path, args))
            return 0

        handlers[name] = handler
    registered.handlers = handlers
    for name, child in registered.delegates.items():
        _stub_handlers(child, calls, (*prefix, name))


def _run(factory: Callable, argv: list[str]):
    registered = factory()
    calls = []
    _stub_handlers(registered, calls)
    status = registered.run(argv=argv, interactive_extra="nyxloom[interactive]")
    return status, calls


def _run_with_domain_guards(factory: Callable, argv: list[str]) -> int:
    return factory().run(argv=argv, interactive_extra="nyxloom[interactive]")


def _leaf_parsers(registered, prefix=()):
    for name, parser in registered.command_parsers.items():
        path = (*prefix, name)
        if name in registered.delegates:
            yield from _leaf_parsers(registered.delegates[name], path)
        else:
            yield path, parser


def _documentation_cli_lines(path: Path) -> list[str]:
    source_lines = path.read_text(encoding="utf-8").splitlines()
    lines = []
    in_fence = False
    shell_fence = False
    for line in source_lines:
        if line.startswith("```"):
            if not in_fence:
                shell_fence = line[3:].strip() in {"bash", "sh", "shell"}
                in_fence = True
            else:
                in_fence = False
                shell_fence = False
            continue
        if in_fence and shell_fence:
            lines.append(line)
        elif not in_fence and line.startswith("    "):
            lines.append(line[4:])
    logical_lines = []
    pending = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            pending += line.rstrip()[:-1] + " "
            continue
        logical_lines.append(pending + line)
        pending = ""
    if pending:
        logical_lines.append(pending)
    return logical_lines


def _github_anchor(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text.strip().lower())
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text)


@pytest.mark.parametrize(
    ("factory", "argv", "expected"),
    [
        (primary_cli, ["lint"], ("lint",)),
        (primary_cli, ["init", "/tmp/project"], ("init",)),
        (primary_cli, ["onboard", "/tmp/project"], ("onboard",)),
        (primary_cli, ["backlog", "new", "feature request"], ("backlog", "new")),
        (primary_cli, ["backlog", "edit", "NYX-4"], ("backlog", "edit")),
        (primary_cli, ["backlog", "promote", "INBOX-1"], ("backlog", "promote")),
        (primary_cli, ["backlog", "note", "NYX-4", "update"], ("backlog", "note")),
        (primary_cli, ["backlog", "set-status", "NYX-4", "open"], ("backlog", "set-status")),
        (primary_cli, ["backlog", "list", "--status", "merged"], ("backlog", "list")),
        (primary_cli, ["backlog", "show", "NYX-4"], ("backlog", "show")),
        (primary_cli, ["backlog", "index"], ("backlog", "index")),
        (harness_cli, ["extract", "session.jsonl"], ("extract",)),
        (harness_cli, ["extract-lossless", "session.jsonl"], ("extract-lossless",)),
        (harness_cli, ["extract-debug", "session.jsonl"], ("extract-debug",)),
        (harness_cli, ["extract-report", "session.jsonl"], ("extract-report",)),
        (harness_cli, ["extract-sessions", "codex"], ("extract-sessions",)),
        (operator_cli, ["project", "add", "demo", "/tmp/demo"], ("project", "add")),
        (operator_cli, ["project", "list"], ("project", "list")),
        (operator_cli, ["lint"], ("lint",)),
        (operator_cli, ["doctor"], ("doctor",)),
        (operator_cli, ["status"], ("status",)),
        (operator_cli, ["resync", "demo"], ("resync",)),
        (operator_cli, ["render"], ("render",)),
        (operator_cli, ["migrate-store", "demo"], ("migrate-store",)),
        (operator_cli, ["daemon"], ("daemon",)),
        (operator_cli, ["auth", "show"], ("auth", "show")),
        (operator_cli, ["auth", "bootstrap"], ("auth", "bootstrap")),
        (operator_cli, ["auth", "rotate"], ("auth", "rotate")),
        (operator_cli, ["tick"], ("tick",)),
        (operator_cli, ["decide", "demo", "D-1", "--choose", "a"], ("decide",)),
        (operator_cli, ["discuss", "demo", "D-1"], ("discuss",)),
        (operator_cli, ["intake", "demo", "I-1", "message"], ("intake",)),
        (operator_cli, ["intake-bridge", "poll", "demo"], ("intake-bridge", "poll")),
        (operator_cli, ["reject", "demo", "TASK-1"], ("reject",)),
        (operator_cli, ["merge", "demo", "TASK-1"], ("merge",)),
        (operator_cli, ["pause", "demo"], ("pause",)),
        (operator_cli, ["resume", "demo"], ("resume",)),
        (operator_cli, ["leases"], ("leases",)),
        (operator_cli, ["digest", "demo"], ("digest",)),
        (operator_cli, ["digest", "demo", "--since", "3"], ("digest",)),
        (operator_cli, ["events", "demo"], ("events",)),
        (operator_cli, ["events", "demo", "--since", "3"], ("events",)),
        (operator_cli, ["free-models", "list"], ("free-models", "list")),
        (operator_cli, ["free-models", "refresh"], ("free-models", "refresh")),
        (operator_cli, ["capability-map", "refresh"], ("capability-map", "refresh")),
        (operator_cli, ["route", "doctor"], ("route", "doctor")),
        (operator_cli, ["finding", "record", "--project-id", "demo", "--kind", "generic", "--title", "title"], ("finding", "record")),
        (operator_cli, ["finding", "record", "--project-id", "demo", "--kind", "cost_crossover", "--title", "title", "--severity", "important"], ("finding", "record")),
        (operator_cli, ["finding", "list"], ("finding", "list")),
    ],
)
def test_every_advertised_leaf_dispatches_once_to_its_declared_handler(
    factory, argv, expected
):
    status, calls = _run(factory, argv)
    assert status == 0
    assert [path for path, _args in calls] == [expected]


@pytest.mark.parametrize(
    ("factory", "argv"),
    [
        (primary_cli, ["init"]),
        (primary_cli, ["backlog", "note", "NYX-1"]),
        (primary_cli, ["backlog", "set-status", "NYX-1", "unknown"]),
        (primary_cli, ["backlog", "list", "--status", "not-a-status"]),
        (harness_cli, ["extract"]),
        (harness_cli, ["extract", "file", "--format", "unknown"]),
        (harness_cli, ["extract-sessions", "file", "--recurse", "sometimes"]),
        (operator_cli, ["project", "add", "demo"]),
        (operator_cli, ["intake-bridge", "poll", "demo", "--transport", "unknown"]),
        (operator_cli, ["decide", "demo", "D-1"]),
        (operator_cli, ["finding", "record", "--project-id", "demo"]),
        (operator_cli, ["finding", "list", "--kind", "unknown-kind"]),
        (operator_cli, ["finding", "record", "--project-id", "demo", "--kind", "unknown-kind", "--title", "title"]),
        (operator_cli, ["finding", "record", "--project-id", "demo", "--kind", "generic", "--title", "title", "--severity", "critical"]),
        (operator_cli, ["finding", "record", "--project-id", "demo", "--kind", "generic", "--title", "title", "--field", "noequals"]),
        (operator_cli, ["finding", "record", "--project-id", "demo", "--kind", "generic", "--title", "title", "--field", "=novaluekey"]),
        (operator_cli, ["digest", "demo", "--since", "not-a-number"]),
        (operator_cli, ["events", "demo", "--since", "not-a-number"]),
        (operator_cli, ["status", "demo", "extra"]),
    ],
)
def test_invalid_arities_choices_and_missing_inputs_never_dispatch(factory, argv):
    status, calls = _run(factory, argv)
    assert status == 2
    assert calls == []


@pytest.mark.parametrize(
    "argv",
    [
        ["extract", "missing.jsonl", "--follow", "--strip-stale-wakeups"],
        ["extract", "missing.jsonl", "--json", "--show-timestamps", "pre"],
        ["extract-report", "missing.jsonl", "--type", "csv", "--json"],
    ],
)
def test_conflicting_extraction_options_fail_before_source_dispatch(
    argv, capsys, monkeypatch
):
    from nyxloom import cli

    calls = []

    def handler(name):
        return lambda _args: calls.append(name) or 0

    monkeypatch.setattr(cli, "cmd_extract", handler("extract"))
    monkeypatch.setattr(cli, "cmd_extract_report", handler("extract-report"))
    status = harness_cli().run(
        argv=argv, interactive_extra="nyxloom[interactive]"
    )
    error = capsys.readouterr().err
    assert status == 2
    assert calls == []
    assert "usage:" in error.lower()


def test_global_options_work_before_and_after_nested_commands():
    status, calls = _run(operator_cli, ["--traceback", "project", "list"])
    assert status == 0
    assert calls[0][1].traceback is True

    status, calls = _run(operator_cli, ["project", "list", "--log-level", "debug"])
    assert status == 0
    assert calls[0][1].log_level == "debug"

    status, calls = _run(harness_cli, ["extract", "session.jsonl", "-f"])
    assert status == 0
    assert calls[0][1].follow is True


@pytest.mark.parametrize(
    ("module_name", "factory_name", "program"),
    [
        ("nyxloom.cli_ctl", "operator_cli", "nyxloomctl"),
        ("nyxloom.cli_harness", "harness_cli", "nyxloom-harness"),
    ],
)
def test_installed_cli_module_entrypoints_call_their_registry(
    monkeypatch, module_name, factory_name, program
):
    from nyxloom import cli_registry

    calls = []

    def run(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        cli_registry,
        factory_name,
        lambda: SimpleNamespace(run=run),
    )
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.setattr(sys, "argv", [program])

    with pytest.raises(SystemExit) as raised:
        runpy.run_module(module_name, run_name="__main__")

    assert raised.value.code == 0
    assert calls == [{"argv": None}]


def test_daemon_module_entrypoint_runs_the_current_daemon(monkeypatch):
    from nyxloom import config, daemon

    registry = {"fixture": Path("/tmp/fixture-project")}
    calls = []

    class StubDaemon:
        def __init__(self, projects):
            calls.append(("init", projects))

        def run(self):
            calls.append(("run",))

    monkeypatch.setattr(config, "load_registry", lambda: registry)
    monkeypatch.setattr(daemon, "Daemon", StubDaemon)
    monkeypatch.setattr(sys, "argv", ["nyxloomd"])
    importlib.import_module("nyxloom.daemon_entrypoint")
    monkeypatch.delitem(sys.modules, "nyxloom.daemon_entrypoint", raising=False)

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("nyxloom.daemon_entrypoint", run_name="__main__")

    assert raised.value.code == 0
    assert calls == [("init", registry), ("run",)]


def test_store_false_option_default_and_extract_json_guard():
    option = _opt("--no-color", "disable color", action="store_false")
    assert option.parser_kwargs["default"] is True

    _extract_guard(SimpleNamespace(json=True))
    with pytest.raises(CliFailure) as raised:
        _extract_guard(SimpleNamespace(json=True, show_timestamps="pre"))
    assert raised.value.exit_code == 2
    assert raised.value.show_help is True


def test_repeatable_options_preserve_each_value_and_unknown_options_fail():
    status, calls = _run(
        harness_cli,
        ["extract", "session.jsonl", "--redact-pattern", "one", "--redact-pattern", "two"],
    )
    assert status == 0
    assert calls[0][1].redact_pattern == ["one", "two"]

    status, calls = _run(primary_cli, ["backlog", "list", "--yes"])
    assert status == 2
    assert calls == []

    status, calls = _run(primary_cli, ["backlog", "new", "title", "--yes"])
    assert status == 2
    assert calls == []

    status, calls = _run(operator_cli, ["project", "add", "demo", "/tmp/demo", "--yes"])
    assert status == 2
    assert calls == []


def test_leaf_options_work_before_or_after_leaf_positionals_only():
    for argv in (
        ["backlog", "new", "--priority", "4", "feature request"],
        ["backlog", "new", "feature request", "--priority", "4"],
    ):
        status, calls = _run(primary_cli, argv)
        assert status == 0
        assert calls[0][1].priority == 4

    status, calls = _run(primary_cli, ["--priority", "4", "backlog", "new", "feature request"])
    assert status == 2
    assert calls == []


@pytest.mark.parametrize(
    ("factory", "argv"),
    [
        (operator_cli, ["doctor", "--write"]),
        (operator_cli, ["doctor", "--liveness", "--rebuild"]),
        (operator_cli, ["resync", "demo", "--apply-content-merges"]),
        (operator_cli, ["capability-map", "refresh", "--dry-run", "--emit-findings", "demo"]),
    ],
)
def test_prohibited_option_combinations_fail_before_dispatch(factory, argv):
    status = _run_with_domain_guards(factory, argv)
    assert status == 2


def test_scriptable_backlog_creation_requires_title_unless_interactive():
    assert _run_with_domain_guards(primary_cli, ["backlog", "new"]) == 2


def test_user_facing_cli_examples_parse_against_the_registered_grammars():
    root = Path(__file__).resolve().parents[1]
    factories = {
        "nyxloom": primary_cli,
        "nyxloom-harness": harness_cli,
        "nyxloomctl": operator_cli,
    }
    example_count = 0
    for relative in ("README.md", "docs/DESIGN-GUIDE.md", "docs/CONSUMERS.md"):
        for line in _documentation_cli_lines(root / relative):
            tokens = shlex.split(line, comments=True)
            command_index = next(
                (index for index, token in enumerate(tokens) if token in factories),
                None,
            )
            if command_index is None:
                continue
            command = tokens[command_index]
            argv = []
            for token in tokens[command_index + 1 :]:
                if token in {";", "&&", "||", "|", ">", "<", ">>", "2>", "}"}:
                    break
                argv.append(token)
            status, _calls = _run(factories[command], argv)
            assert status == 0, f"invalid {command} example in {relative}: {line}"
            example_count += 1
    assert example_count >= 40


def test_current_reference_lists_every_leaf_and_option():
    root = Path(__file__).resolve().parents[1]
    reference = (root / "docs/CLI-REFERENCE.md").read_text(encoding="utf-8")
    current = reference.split("## Historical P111 audit", maxsplit=1)[0]
    for command, factory in (
        ("nyxloom", primary_cli),
        ("nyxloom-harness", harness_cli),
        ("nyxloomctl", operator_cli),
    ):
        for path, parser in _leaf_parsers(factory()):
            command_path = " ".join((command, *path))
            assert command_path in current, f"missing command row: {command_path}"
            for action in parser._actions:
                for flag in action.option_strings:
                    if flag not in {"--help", "--version"}:
                        assert flag in current, f"missing option {flag} for {command_path}"


def test_option_matrix_covers_each_local_option_with_placement_and_effect():
    root = Path(__file__).resolve().parents[1]
    reference = (root / "docs/CLI-REFERENCE.md").read_text(encoding="utf-8")
    current = reference.split("## Historical P111 audit", maxsplit=1)[0]
    matrix = current.split("### Option declaration matrix", maxsplit=1)[1].split(
        "### Shared-option effects", maxsplit=1
    )[0]
    documented = set()
    documented_rows = {}
    for line in matrix.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            continue
        command = cells[0].strip("`")
        options = cells[1].strip("`").split("/")
        placement_and_effect = cells[5]
        assert "after the complete leaf path" in placement_and_effect.lower()
        assert "Effect:" in placement_and_effect
        for option in options:
            key = (command, option)
            assert key not in documented, f"duplicate option row: {key}"
            documented.add(key)
            documented_rows[key] = cells

    common = {
        "-h", "--help", "--version", "--debug", "--debug-raw", "--log-level",
        "--quiet", "--verbose", "--color", "--no-color", "--traceback", "--yes",
    }
    assert "value-taking options may be repeated" in matrix.lower()
    assert "flags are idempotent when repeated" in matrix.lower()
    missing = []
    declared = set()
    for executable, factory in (
        ("nyxloom", primary_cli),
        ("nyxloom-harness", harness_cli),
        ("nyxloomctl", operator_cli),
    ):
        for path, parser in _leaf_parsers(factory()):
            command = " ".join((executable, *path))
            for action in parser._actions:
                for option in action.option_strings:
                    if option in common:
                        continue
                    key = (command, option)
                    if key not in documented:
                        missing.append((command, option))
                        continue
                    declared.add(key)
                    cells = documented_rows[key]
                    declaration = cells[2].lower()
                    assert cells[3].strip(), f"missing omitted behavior: {key}"
                    if action.required:
                        assert "required" in declaration, f"missing required marker: {key}"
                        assert "required; omission is a usage error" in cells[3].lower()

                    if action.nargs == 0:
                        assert "boolean flag" in declaration and "0 values" in declaration, key
                    elif action.type is int:
                        assert "integer" in declaration, key
                    elif action.type is float:
                        assert "decimal" in declaration, key
                    elif action.type is not None and getattr(action.type, "__name__", "") == "_finding_field":
                        assert "string" in declaration and "key=value" in declaration, key
                    else:
                        assert "string" in declaration, key

                    if action.choices:
                        choices = ", ".join(map(str, action.choices)).lower()
                        assert f"choices: {choices}" in declaration, key
                    if action.nargs == "?":
                        assert "0 or 1 value" in declaration, key
                    elif action.nargs != 0:
                        assert "1 value" in declaration, key
                    if action.__class__.__name__ == "_AppendAction":
                        assert "repeatable" in declaration, key
                    elif action.nargs != 0:
                        assert "repeat accepted" in declaration, key
    assert not missing, f"option matrix omits parser declarations: {missing}"
    stale = sorted(documented - declared)
    assert not stale, f"option matrix lists retired declarations: {stale}"

    assert "choices: open, carved, merged, fixed, withdrawn, obsolete" in documented_rows[
        ("nyxloom backlog list", "--status")
    ][2]
    assert "integer" in documented_rows[("nyxloomctl digest", "--since")][2]
    assert "integer" in documented_rows[("nyxloomctl events", "--since")][2]
    assert "choices: generic, model_near_equivalent, cost_crossover" in documented_rows[
        ("nyxloomctl finding record", "--kind")
    ][2]
    assert "choices: info, note, important" in documented_rows[
        ("nyxloomctl finding record", "--severity")
    ][2]
    assert "KEY=VALUE" in documented_rows[("nyxloomctl finding record", "--field")][2]

    for command, option in (
        ("nyxloomctl decide", "--choose"),
        ("nyxloomctl finding record", "--project-id"),
        ("nyxloomctl finding record", "--kind"),
        ("nyxloomctl finding record", "--title"),
    ):
        row = next(
            line for line in matrix.splitlines()
            if line.startswith(f"| `{command}` | `{option}` |")
        )
        assert "Required; omission is a usage error" in row


def test_closed_parser_choices_are_documented_for_consumers():
    root = Path(__file__).resolve().parents[1]
    documents = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in ("README.md", "docs/DESIGN-GUIDE.md", "docs/CONSUMERS.md")
    )
    choices = set()
    for factory in (primary_cli, harness_cli, operator_cli):
        for _path, parser in _leaf_parsers(factory()):
            for action in parser._actions:
                if action.choices and not isinstance(action.choices, dict):
                    choices.update(map(str, action.choices))
    missing = sorted(choice for choice in choices if choice not in documents)
    assert not missing, f"undocumented closed parser choices: {missing}"


def test_cross_document_markdown_anchors_resolve():
    root = Path(__file__).resolve().parents[1]
    sources = [
        root / "README.md",
        root / "docs/DESIGN-GUIDE.md",
        root / "docs/CONSUMERS.md",
    ]
    targets = [root / "README.md", *sorted((root / "docs").glob("*.md"))]
    anchor_sets = {}
    for target in targets:
        anchors = set()
        for line in target.read_text(encoding="utf-8").splitlines():
            heading = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
            if heading:
                base = _github_anchor(heading.group(1))
                anchor = base
                suffix = 0
                while anchor in anchors:
                    suffix += 1
                    anchor = f"{base}-{suffix}"
                anchors.add(anchor)
        anchor_sets[target.resolve()] = anchors

    missing = []
    for source in sources:
        text = source.read_text(encoding="utf-8")
        for destination in re.findall(r"(?<!!)\[[^\]]*\]\(([^)]+)\)", text):
            destination = destination.strip().split()[0].strip("<>")
            if not destination or "://" in destination or destination.startswith("mailto:"):
                continue
            target_path, _, anchor = destination.partition("#")
            target = (
                (source.parent / urllib.parse.unquote(target_path)).resolve()
                if target_path
                else source.resolve()
            )
            if not anchor:
                continue
            if target not in anchor_sets or urllib.parse.unquote(anchor) not in anchor_sets[target]:
                missing.append((source.relative_to(root), destination))
    assert not missing, f"unresolved cross-document anchors: {missing}"
