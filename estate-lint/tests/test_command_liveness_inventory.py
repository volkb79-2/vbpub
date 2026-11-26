"""Estate-wide guard for RG-320/RG-326 command-lane silence bounds."""
from __future__ import annotations

import re
import shlex
import subprocess
import tomllib
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = "estate-lint/stall-progress-inventory.toml"
CMRU_GATE_SOURCE = "cmru/tools/run_release_gate.py"
DEFAULT_COMMAND_BOUND = 300
DURATION = re.compile(r"^(\d+)([smh])$")
# Named exclusion rule: configs below any tests/fixtures/ directory are
# synthetic inputs, not live estate lane declarations.
FIXTURE_RULE = "exclude tracked run-gate.toml files below any tests/fixtures/ directory"
VALUE_OPTIONS = {
    "--admission-wait", "--base", "--carry-over", "--carry-over-reason",
    "--exclude-candidates", "--jobs", "--merge-shards", "--merge-state",
    "--only-candidates", "--rejudge", "--rejudge-outcome", "--shard",
    "--worktree", "--workers",
}
RUN_GATE = {"run-gate", "./run-gate", "run-gate.py", "./run-gate.py"}
RUN_GATE_VERBS = {
    "admission", "doctor", "footprint", "history", "lanes", "status",
    "validate-pointers",
}


def _seconds(value: object) -> int | None:
    match = DURATION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


def _fixture(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(parts[i:i + 2] == ("tests", "fixtures")
               for i in range(len(parts) - 1))


def _configs(texts: dict[str, str]) -> tuple[dict[str, dict], list[str]]:
    parsed: dict[str, dict] = {}
    errors: list[str] = []
    for path, text in texts.items():
        try:
            value = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            errors.append(f"{path}: invalid TOML: {exc}")
            continue
        if not isinstance(value.get("lanes"), dict):
            errors.append(f"{path}: missing [lanes] table")
            continue
        parsed[path] = value["lanes"]
    return parsed, errors


def _inventory(text: str) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return {}, {}, [f"{INVENTORY}: invalid TOML: {exc}"]
    if value.get("schema_version") != 1:
        return {}, {}, [f"{INVENTORY}: schema_version must be 1"]
    entries = value.get("lanes", [])
    if not isinstance(entries, list):
        return {}, {}, [f"{INVENTORY}: lanes must be an array of tables"]
    wrappers = value.get("wrappers", [])
    if not isinstance(wrappers, list):
        return {}, {}, [f"{INVENTORY}: wrappers must be an array of tables"]
    result: dict[str, dict] = {}
    wrapper_result: dict[str, dict] = {}
    errors: list[str] = []
    for table, destination, label in (
        (entries, result, "lanes"), (wrappers, wrapper_result, "wrappers"),
    ):
        for entry in table:
            if not isinstance(entry, dict):
                errors.append(f"{INVENTORY}: each {label} entry must be a table")
                continue
            config, lane = entry.get("config"), entry.get("lane")
            if not isinstance(config, str) or not config.strip() \
                    or not isinstance(lane, str) or not lane.strip():
                errors.append(f"{INVENTORY}: each {label} entry needs config and lane")
                continue
            ref = f"{config}#{lane}"
            if ref in destination:
                errors.append(f"{INVENTORY}: duplicate {label} entry for {ref}")
            destination[ref] = entry
    return result, wrapper_result, errors


def _cmru_members(source: str, argv: list[str]) -> list[str] | None:
    """Resolve the CMRU wrapper from run_release_gate.py's member loop."""
    if not any("run_release_gate.py" in item for item in argv):
        return None
    constants = dict(re.findall(
        r"(?m)^([A-Z][A-Z0-9_]+)\s*=\s*['\"]([^'\"]+)['\"]", source,
    ))
    loop = re.search(r"for\s+lane\s+in\s*\((.*?)\)\s*:", source, re.DOTALL)
    assay = re.search(r"['\"]assay_lane['\"]\s*:\s*['\"]([^'\"]+)['\"]", source)
    if loop is None or assay is None:
        return None
    names = re.findall(r"\b[A-Za-z_]\w*\b", loop[1])
    members: list[str] = []
    for name in names:
        if name == "assay_lane":
            members.append(assay[1])
        elif name in constants:
            members.append(constants[name])
        else:
            return None
    if "--postpone-mutation" not in argv:
        members.append("mutation")
    return [f"cmru/run-gate.toml#{name}" for name in members]


def _nested_members(config_path: str, lane: dict,
                    configs: dict[str, dict]) -> tuple[list[str], bool]:
    """Resolve literal `run-gate LANE` calls; flag dynamic or unknown names."""
    argv = lane.get("argv")
    lines = [item for item in argv if isinstance(item, str)] \
        if isinstance(argv, list) else []
    if lines and lines[0] in RUN_GATE:
        lines = [" ".join(lines)]
    members: list[str] = []
    unresolved = False
    prefix = ""
    own_config = (PurePosixPath(config_path).parent / "run-gate.toml").as_posix()
    if own_config == "./run-gate.toml":
        own_config = "run-gate.toml"
    for line in "\n".join(lines).splitlines():
        try:
            tokens = shlex.split(line, comments=True)
        except ValueError:
            unresolved |= "run-gate" in line
            prefix += line + "\n"
            continue
        for index, token in enumerate(tokens):
            if token not in RUN_GATE:
                continue
            cursor = index + 1
            while cursor < len(tokens) and tokens[cursor].startswith("-"):
                cursor += 2 if tokens[cursor] in VALUE_OPTIONS else 1
            if cursor >= len(tokens) or tokens[cursor] in {"&&", "||", ";"}:
                unresolved = True
                continue
            name = tokens[cursor]
            if name in RUN_GATE_VERBS:
                continue
            target = own_config
            changed_dir = re.search(
                r"\bcd\s+\{worktree\}/([A-Za-z0-9_./-]+)", prefix + line[:line.find(token)],
            )
            if changed_dir is not None:
                candidate = f"{changed_dir[1]}/run-gate.toml"
                if candidate in configs:
                    target = candidate
            target_lanes = configs.get(target, {})
            if "$" in name or "{" in name or name not in target_lanes:
                unresolved = True
            else:
                members.append(f"{target}#{name}")
        prefix += line + "\n"
    return members, unresolved


def _progress_errors(ref: str, entry: dict, tracked: set[str]) -> list[str]:
    progress = entry.get("progress")
    if not isinstance(progress, dict):
        return [f"{ref}: progress must name a producer, emitter, and cadence"]
    producer, emitter, guarantee = (
        progress.get("producer"), progress.get("emitter"), progress.get("guarantee")
    )
    errors: list[str] = []
    if not isinstance(producer, str) or not producer.strip():
        errors.append(f"{ref}: progress producer file must be non-empty")
    elif producer not in tracked:
        errors.append(f"{ref}: progress producer {producer!r} is not tracked")
    if not isinstance(emitter, str) or not emitter.strip():
        errors.append(f"{ref}: progress emitter symbol or command must be non-empty")
    if not isinstance(guarantee, str) or not guarantee.strip() \
            or re.search(r"300\s*(?:s|seconds?)", guarantee, re.I) is None:
        errors.append(f"{ref}: progress must state what emits output at least every 300s")
    return errors


def audit_liveness(config_texts: dict[str, str], inventory_text: str,
                   tracked: set[str], *, cmru_source: str = "") -> list[str]:
    """Return actionable estate errors for unbounded lanes and wrappers."""
    configs, errors = _configs(config_texts)
    progress_entries, wrapper_entries, inventory_errors = _inventory(inventory_text)
    errors.extend(inventory_errors)
    lanes = {
        f"{path}#{name}": lane
        for path, entries in configs.items()
        for name, lane in entries.items() if isinstance(lane, dict)
    }

    for ref, entry in progress_entries.items():
        lane = lanes.get(ref)
        if lane is None:
            errors.append(f"stale progress entry for {ref}: lane is gone or renamed; remove or update it")
        elif lane.get("kind") != "command" or "stall_timeout" in lane:
            errors.append(f"stale progress entry for {ref}: lane is now bounded or is not a command; remove or update it")
        else:
            errors.extend(_progress_errors(ref, entry, tracked))

    for ref, lane in lanes.items():
        if lane.get("kind") == "command" and "stall_timeout" not in lane:
            entry = progress_entries.get(ref)
            if entry is None:
                errors.append(
                    f"{ref}: command lane has no stall_timeout; declare a stall_timeout "
                    "or add verified progress evidence (producer, emitter, and a healthy "
                    "output guarantee at least every 300s) in " + INVENTORY
                )
    wrapper_members: dict[str, tuple[list[str], bool]] = {}
    for ref, lane in lanes.items():
        if lane.get("kind") != "command":
            continue
        config_path = ref.split("#", 1)[0]
        argv = [item for item in lane.get("argv", []) if isinstance(item, str)] \
            if isinstance(lane.get("argv"), list) else []
        cmru_members = _cmru_members(cmru_source, argv)
        if cmru_members is not None:
            wrapper_members[ref] = (cmru_members, False)
        else:
            members, unresolved = _nested_members(config_path, lane, configs)
            if members or unresolved:
                wrapper_members[ref] = (members, unresolved)

    for ref, entry in wrapper_entries.items():
        if ref not in wrapper_members or not wrapper_members[ref][1]:
            errors.append(f"stale wrapper member entry for {ref}: argv now resolves the member set; remove or update it")

    for ref, (static_members, unresolved) in wrapper_members.items():
        if unresolved:
            entry = wrapper_entries.get(ref)
            listed = entry.get("members") if entry is not None else None
            if not isinstance(listed, list) or not listed \
                    or not all(isinstance(item, str) and item for item in listed):
                errors.append(
                    f"{ref}: nested lane members cannot be derived statically; name them in "
                    f"the {INVENTORY} wrappers entry"
                )
                continue
            members = listed
        else:
            members = static_members
        wrapper = lanes[ref]
        wrapper_bound = _seconds(wrapper.get("stall_timeout")) \
            if "stall_timeout" in wrapper else DEFAULT_COMMAND_BOUND
        if wrapper_bound is None:
            errors.append(f"{ref}: wrapper stall_timeout is not a supported duration")
            continue
        for member_ref in members:
            member = lanes.get(member_ref)
            if member is None:
                errors.append(f"{ref}: member {member_ref!r} is not a tracked lane")
                continue
            if "stall_timeout" not in member:
                continue
            member_bound = _seconds(member.get("stall_timeout"))
            if member_bound is None:
                errors.append(f"{member_ref}: declared stall_timeout is not a supported duration")
            elif member_bound > wrapper_bound:
                errors.append(
                    f"{ref}: wrapper effective bound {wrapper_bound}s is shorter than "
                    f"member {member_ref}'s declared stall_timeout {member_bound}s; raise the "
                    "wrapper bound or reduce the member bound only with progress evidence"
                )
    return errors


def _read_estate() -> tuple[dict[str, str], str, set[str], str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True,
        capture_output=True, text=True,
    )
    tracked = set(result.stdout.splitlines())
    configs = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in sorted(tracked)
        if PurePosixPath(path).name == "run-gate.toml" and not _fixture(path)
    }
    inventory = (ROOT / INVENTORY).read_text(encoding="utf-8")
    source_path = ROOT / CMRU_GATE_SOURCE
    source = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    return configs, inventory, tracked, source


def test_all_tracked_command_lanes_are_bounded_or_have_verified_progress():
    configs, inventory, tracked, cmru_source = _read_estate()
    all_configs = {
        path for path in tracked if PurePosixPath(path).name == "run-gate.toml"
    }
    assert INVENTORY in tracked, f"commit {INVENTORY} with the estate check"
    assert any(_fixture(path) for path in all_configs), FIXTURE_RULE
    errors = audit_liveness(
        configs, inventory, tracked, cmru_source=cmru_source,
    )
    assert not errors, "\n".join(errors)


def test_unbounded_command_without_evidence_fails_with_a_fix():
    config = '''
schema_version = 1
[lanes.silent]
kind = "command"
argv = ["pytest"]
budget = "10m"
'''
    errors = audit_liveness(
        {"project/run-gate.toml": config},
        'schema_version = 1\nlanes = []\n', {"producer.sh"},
    )
    assert any("declare a stall_timeout or add verified progress evidence" in error
               for error in errors)


def test_progress_entry_for_a_bounded_or_removed_lane_is_stale():
    config = '''
schema_version = 1
[lanes.finished]
kind = "command"
argv = ["run-tests"]
budget = "10m"
stall_timeout = "10m"
'''
    inventory = '''
schema_version = 1
[[lanes]]
config = "project/run-gate.toml"
lane = "finished"
[lanes.progress]
producer = "producer.sh"
emitter = "print_progress()"
guarantee = "prints a flushed line at least every 300s while healthy"
'''
    errors = audit_liveness(
        {"project/run-gate.toml": config}, inventory, {"producer.sh"},
    )
    assert any("stale progress entry" in error for error in errors)


def test_static_nested_lane_with_a_longer_bound_fails():
    config = '''
schema_version = 1
[lanes.inner]
kind = "command"
argv = ["true"]
budget = "10m"
stall_timeout = "5m"
[lanes.wrapper]
kind = "command"
argv = ["bash", "-c", "run-gate inner"]
budget = "10m"
stall_timeout = "1m"
'''
    errors = audit_liveness(
        {"project/run-gate.toml": config},
        'schema_version = 1\nlanes = []\n', set(),
    )
    assert any("wrapper effective bound 60s is shorter" in error
               and "member project/run-gate.toml#inner" in error
               for error in errors)


def test_dynamic_nested_lane_uses_inventory_member_names():
    config = '''
schema_version = 1
[lanes.inner]
kind = "command"
argv = ["true"]
budget = "10m"
stall_timeout = "5m"
[lanes.wrapper]
kind = "command"
argv = ["bash", "-c", "run-gate $MEMBER"]
budget = "10m"
stall_timeout = "1m"
'''
    inventory = '''
schema_version = 1
[[wrappers]]
config = "project/run-gate.toml"
lane = "wrapper"
members = ["project/run-gate.toml#inner"]
'''
    errors = audit_liveness(
        {"project/run-gate.toml": config}, inventory, set(),
    )
    assert any("wrapper effective bound 60s is shorter" in error
               and "member project/run-gate.toml#inner" in error
               for error in errors)


def test_unbounded_lane_with_valid_progress_evidence_passes():
    config = '''
schema_version = 1
[lanes.live]
kind = "command"
argv = ["bash", "producer.sh"]
budget = "10m"
'''
    inventory = '''
schema_version = 1
[[lanes]]
config = "project/run-gate.toml"
lane = "live"
[lanes.progress]
producer = "producer.sh"
emitter = "print_progress()"
guarantee = "prints a flushed output line at least every 300s while healthy"
'''
    errors = audit_liveness(
        {"project/run-gate.toml": config}, inventory, {"producer.sh"},
    )
    assert errors == []
