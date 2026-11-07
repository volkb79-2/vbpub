"""CP-45 docs sync: the README's product overview against the shipped surface.

The README tables for the verbs, the configuration keys, the sockets and the
group names are compared with the cli-extended registries of `cgprofilectl` and
`cgprofiled`, the `daemon_config` schema, the wire defaults and PROTOCOL.md. A
mutation-style self-test proves each comparison fails when the README drifts.
A grep test keeps removed 1.x names out of every live document (CP-49).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set

import pytest

from cgprofile import ctlwire, daemon_config
from cgprofile.cli import cgprofile, cgprofilectl, cgprofiled

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
README = ROOT / "README.md"
PROTOCOL = ROOT / "docs" / "PROTOCOL.md"
OBSERVER_GROUP = "cgprofile-observer"


def _section(text: str, heading: str) -> str:
    """The body under a `###` heading, up to the next heading of any level."""
    match = re.search(rf"^###? {re.escape(heading)}\n(.*?)(?=^#{{2,3}} |\Z)", text, re.S | re.M)
    assert match, f"README has no section {heading!r}"
    return match.group(1)


def _tables(body: str) -> List[List[List[str]]]:
    """Every markdown table in `body` as rows of stripped cells (header and rule dropped)."""
    tables: List[List[List[str]]] = []
    current: List[List[str]] = []
    for line in body.splitlines() + [""]:
        if line.startswith("|"):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
            current.append(cells)
        elif current:
            tables.append(current[2:])
            current = []
    return tables


def _first_code(cell: str) -> str:
    match = re.search(r"`([^`]+)`", cell)
    assert match, f"no code span in {cell!r}"
    return match.group(1)


def _registry_verbs(registry) -> Dict[str, Set[str]]:
    """verb name to the long option spellings it accepts (sub-verbs folded into the group)."""
    verbs: Dict[str, Set[str]] = {}
    for verb in registry.registered_verbs:
        options: Set[str] = set()
        for spec in (verb, *(verb.delegate.registered_verbs if verb.delegate is not None else ())):
            options |= {flag for option in spec.options for flag in option.flags if flag.startswith("--")}
            if spec.dry_run:
                options.add("--dry-run")  # the framework adds it for dry-run capable verbs
            if spec.confirmation_required:
                options.add("--yes")  # and the confirmation bypass for confirming verbs
        verbs[verb.name] = options
    return verbs


def _default_text(spec) -> str:
    if spec.kind == "onoff":
        return f'"{spec.default}"'
    if spec.kind == "socket":
        return "; ".join(f"{key}={value}" for key, value in spec.default.items())
    if spec.kind in ("slice", "string"):
        return f'"{spec.default}"' if spec.kind == "slice" else str(spec.default)
    if spec.kind in ("slices", "uids"):
        return "[]"
    return str(spec.default)


def readme_problems(text: str) -> List[str]:
    """Every way the README overview disagrees with the shipped surface."""
    problems: List[str] = []
    commands = _section(text, "2. Commands")
    ctl_table, client_table, cgprofiled_table, key_table = _tables(commands)[:4]

    for label, table, registry in (("cgprofilectl", ctl_table, cgprofilectl.build_cli()),
                                   ("cgprofile", client_table, cgprofile.build_cli())):
        verbs = _registry_verbs(registry)
        documented = {_first_code(row[0]).split()[0]: row for row in table}
        if set(documented) != set(verbs):
            problems.append(f"{label} verbs: README {sorted(documented)} vs registry {sorted(verbs)}")
        for name, row in documented.items():
            for flag in re.findall(r"`(--[a-z][a-z-]*)", row[1]):
                if flag not in verbs.get(name, set()):
                    problems.append(f"{label} {name}: README flag {flag} is not in the registry")

    cgprofiled_verbs = _registry_verbs(cgprofiled.build_cli())
    documented_cgprofiled = {_first_code(row[0]) for row in cgprofiled_table}
    if documented_cgprofiled != set(cgprofiled_verbs):
        problems.append(f"cgprofiled verbs: README {sorted(documented_cgprofiled)} vs registry {sorted(cgprofiled_verbs)}")
    cgprofiled_text = " ".join(row[1] for row in cgprofiled_table)
    for flag in re.findall(r"`(--[a-z][a-z-]*)", cgprofiled_text):
        if flag not in set().union(*cgprofiled_verbs.values()):
            problems.append(f"cgprofiled: README flag {flag} is not in the registry")

    keys = {_first_code(row[0]): row for row in key_table}
    if set(keys) != set(daemon_config.KEYS):
        problems.append(f"config keys: README {sorted(keys)} vs schema {sorted(daemon_config.KEYS)}")
    for name, spec in daemon_config.KEYS.items():
        row = keys.get(name)
        if row is None:
            continue
        if _first_code(row[1]) != _default_text(spec):
            problems.append(f"config key {name}: README default {row[1]} vs schema {_default_text(spec)}")
        expected = "reload" if spec.reloadable else "restart"
        if row[2] != expected:
            problems.append(f"config key {name}: README says {row[2]!r}, schema says {expected!r}")

    sockets = _tables(_section(text, "3. Sockets"))[0]
    socket_cells = " ".join(row[0] for row in sockets)
    for path in (daemon_config.DEFAULT_CONTROL_SOCKET, daemon_config.DEFAULT_OBSERVER_SOCKET):
        if f"`{path}`" not in socket_cells:
            problems.append(f"sockets table lacks {path}")
    if ctlwire.DEFAULT_SOCKET != daemon_config.DEFAULT_CONTROL_SOCKET:
        problems.append("ctlwire default socket differs from the sockets.control default")
    if OBSERVER_GROUP not in " ".join(" ".join(row) for row in sockets):
        problems.append(f"sockets table does not name the {OBSERVER_GROUP} group")
    return problems


def test_readme_overview_matches_the_shipped_surface():
    assert readme_problems(README.read_text(encoding="utf-8")) == []


def test_socket_paths_and_group_names_agree_across_the_documents():
    protocol = PROTOCOL.read_text(encoding="utf-8")
    native = (ROOT / "docs" / "NATIVE-DEPLOYMENT.md").read_text(encoding="utf-8")
    for path in (daemon_config.DEFAULT_CONTROL_SOCKET, daemon_config.DEFAULT_OBSERVER_SOCKET):
        assert path in protocol, path
        assert path in native, path
    for text in (protocol, native, README.read_text(encoding="utf-8")):
        assert OBSERVER_GROUP in text
    assert daemon_config.DEFAULT_CONFIG_PATH in README.read_text(encoding="utf-8")


def test_cgprofiled_run_is_explicit_in_the_spec_and_the_readme():
    spec = (ROOT / "docs" / "CGPROFILECTL-SPEC.md").read_text(encoding="utf-8")
    assert "never a default verb" in spec
    assert "The default verb" not in spec
    assert "explicit service entrypoint" in README.read_text(encoding="utf-8")


def test_socket_group_user_and_config_test_contract_stays_in_sync():
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs" / "CGPROFILECTL-SPEC.md").read_text(encoding="utf-8")
    cli_spec = (ROOT / "docs" / "CLI-SPEC-cgprofilectl.md").read_text(encoding="utf-8")
    native = (ROOT / "docs" / "NATIVE-DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "--add-user LEVEL=USER" in README.read_text(encoding="utf-8")
    assert "cgprofiled -t" in README.read_text(encoding="utf-8")
    assert "GID 0 or the docker GID" in design
    assert "host-setup owns uniqueness across services" in design
    assert "--add-user control=alice --add-user observer=runner" in consumers
    assert "gpasswd -a" in consumers and "uninstall never removes them" in consumers
    assert "--add-user LEVEL=USER" in spec and "gpasswd -a" in spec
    assert "memberships are never" in spec and "Cross-service group uniqueness belongs to host-setup" in spec
    assert "cgprofiled -t" in spec and "ExecStartPre" in spec
    assert "CGPROFILECTL-SPEC.md` section 3" in cli_spec and "config-test option" in cli_spec
    assert "No override exists" in native and "manual cleanup" in native
    assert "Do not rerun the cgprofiled binary from a version tree" in native
    assert "root-equivalent on mdt hosts" not in spec
    assert "ctl group is the Docker relay group" not in (ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")


def test_recorded_user_reconciliation_and_requested_user_validation_are_documented():
    readme = README.read_text(encoding="utf-8")
    cli_spec = (ROOT / "docs" / "CLI-SPEC-cgprofilectl.md").read_text(encoding="utf-8")
    consumers = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    spec = (ROOT / "docs" / "CGPROFILECTL-SPEC.md").read_text(encoding="utf-8")
    design = (ROOT / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")

    for text in (readme, cli_spec, consumers):
        normalized = " ".join(text.split())
        assert "recorded users whose accounts no longer exist are dropped and reported" in normalized
        assert "A user named with `--add-user` must exist" in normalized
    assert "missing recorded accounts do not block later installs" in " ".join(design.split()).lower()
    assert "dropped and reported; the install continues" in " ".join(spec.split())


def test_group_examples_and_legacy_container_files_name_the_dedicated_model():
    spec = (ROOT / "docs" / "CGPROFILECTL-SPEC.md").read_text(encoding="utf-8")
    serve = (ROOT / "src" / "cgprofile" / "serve.py").read_text(encoding="utf-8")
    compose = (ROOT / "ciu.compose.yml.j2").read_text(encoding="utf-8")
    compose_text = " ".join(
        line.lstrip().removeprefix("#").strip() for line in compose.splitlines())
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert '"group": "cgprofile-control", "gid": 999' in spec
    assert "add yourself to cgprofile-control (gid 999) and re-login" in spec
    assert "mdt-docker" not in spec and "2751" not in spec
    assert "docker-group trust" not in serve
    assert "dedicated socket group" in serve
    assert "LEGACY cgprofile 1.x CONTAINER TEMPLATE ONLY" in compose
    assert "obsolete /run/cgprofile 0770 root:docker layout" in compose_text
    assert "LEGACY cgprofile container image" in dockerfile


@pytest.mark.parametrize(
    "old,new",
    [
        ("| `sessions` | one row", "| `sesions` | one row"),
        ("| `prune` | apply retention now; `--keep`,", "| `prune` | apply retention now; `--retain`,"),
        ("| `reload` | signal", "| `restart` | signal"),
        ("| `max_watchers` | `32` | reload |", "| `max_watchers` | `33` | reload |"),
        ("| `busctl` | `busctl` | restart |", "| `busctl` | `busctl` | reload |"),
        ("| `allow_uids` | `[]` | reload |\n", ""),
        ("`/run/cgprofile/control/cgprofile.sock`", "`/run/cgprofile/control/cgprofile2.sock`"),
        ("root:cgprofile-observer` |", "root:observers` |"),
    ],
)
def test_a_drifted_readme_is_detected(old, new):
    text = README.read_text(encoding="utf-8")
    assert old in text, "the self-test anchor moved; update the mutation"
    assert readme_problems(text.replace(old, new, 1)), f"drift {old!r} -> {new!r} was not detected"


# --- CP-49: removed 1.x names stay out of the live documents -----------------

REMOVED = (
    "cgprofile ctl",
    "docker exec cgprofile-host-daemon",
    "./cgprofile ",
    "./setup.sh",
    "CGPROFILE_PID_NAMESPACE_MODE",
    "CGPROFILE_HELPER_IMAGE",
    "CGPROFILE_PLACEMENT_PROBE_IMAGE",
)

#: Live documents that must not mention a removed name.
LIVE_DOCS = (
    "cgprofile/README.md",
    "cgprofile/ATTACH-GUIDE.md",
    "cgprofile/DESIGN.md",
    "cgprofile/docs/DESIGN-GUIDE.md",
    "cgprofile/docs/PROTOCOL.md",
    "mdt-cockpit/host-setup/README.md",
    "mdt-cockpit/host-setup/scripts/check.sh",
    "mdt-cockpit/templates/devcontainer.json",
    "mdt-cockpit/templates/host-folder/devcontainer.json",
    ".devcontainer/devcontainer.json",
    "ciu/docs/SPEC-V8.md",
    "ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md",
    "scripts/debian-install-v2/testing/vm/USAGE-TESTING.md",
    "scripts/debian-install-v2/docs/DESIGN-GUIDE.md",
)


def _consumers_without_migration_table() -> str:
    text = (ROOT / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")
    head, marker, _ = text.partition("## Migration table: 1.x command to cgprofilectl verb")
    assert marker, "CONSUMERS.md lost its migration table"
    return head


def _live_texts() -> Dict[str, str]:
    texts = {}
    for relative in LIVE_DOCS:
        path = REPO / relative
        assert path.is_file(), f"live document list names a missing file: {relative}"
        texts[relative] = path.read_text(encoding="utf-8")
    texts["cgprofile/docs/CONSUMERS.md"] = _consumers_without_migration_table()
    return texts


def removed_name_hits(texts: Dict[str, str]) -> List[str]:
    return [f"{name}: {term}" for name, text in texts.items() for term in REMOVED if term in text]


def test_no_live_document_names_a_removed_1x_command():
    assert removed_name_hits(_live_texts()) == []


def test_the_removed_name_grep_detects_each_term():
    for term in REMOVED:
        assert removed_name_hits({"doc.md": f"run {term} now"}) == [f"doc.md: {term}"]
    assert removed_name_hits({"doc.md": "cgprofilectl status"}) == []
