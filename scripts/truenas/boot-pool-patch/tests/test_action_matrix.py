"""Keep ACTION-MATRIX.md and the patch/lane/API code in sync."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import tomllib

PATCH_ROOT = Path(__file__).resolve().parents[1]
TRUENAS_ROOT = PATCH_ROOT.parent
ACCEPTANCE = PATCH_ROOT / "acceptance"
DOC = (PATCH_ROOT / "ACTION-MATRIX.md").read_text()
RELEASE_DIRS = ("v27", "v28-nightly-20261014")
VERDICTS = {"works", "patch-UI", "API-TUI", "n/a", "pending decision"}
COLUMNS = ["id", "path", "action", "methods", "api", "ui", "ui_patched", "patch", "size",
           "verdict", "lane", "v27", "v28"]


def section(title: str) -> list[list[str]]:
    body = DOC.split(f"## {title}", 1)[1]
    rows: list[list[str]] = []
    for line in body.splitlines()[1:]:
        if line.startswith("## "):
            break
        if line.startswith("|") and not set(line) <= set("|- "):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows[1:]  # drop the header row


def names(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


ACTIONS = [dict(zip(COLUMNS, row, strict=True)) for row in section("Actions")]
INVENTORY = section("Patch inventory")
RUN_GATE = tomllib.loads((TRUENAS_ROOT / "run-gate.toml").read_text())["lanes"]
ACCEPTANCE_TEXT = "\n".join(path.read_text() for path in ACCEPTANCE.glob("*.py"))


def lane_source(lane: str) -> Path | None:
    for arg in RUN_GATE[lane].get("argv", []):
        if found := re.search(r"\b(live_[a-z0-9_]+\.py)\b", arg):
            return ACCEPTANCE / found.group(1)
    return None


def step_exists(source: str, step: str) -> bool:
    """A step is a report("step"), print("[step]"), or check("step") call site."""
    escaped = re.escape(step)
    return bool(
        re.search(rf'report\(\s*"{escaped}"', source)
        or re.search(rf'check\(\s*"{escaped}"', source)
        or re.search(rf'\[{escaped}\]', source)
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _has_call(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Call) and _call_name(child) == name for child in ast.walk(node))


def _step_calls(tree: ast.AST, marker: str, step: str) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    owners = []
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        if any(
            isinstance(call, ast.Call)
            and _call_name(call) == marker
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == step
            for call in ast.walk(function)
        ):
            owners.append(function)
    return owners


def _step_has_nearby_assertion(
    function: ast.FunctionDef | ast.AsyncFunctionDef, marker: str, step: str,
    *, max_line_distance: int = 30,
) -> bool:
    markers = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _call_name(node) == marker
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == step
    ]
    assertions = [
        node for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) == "require"
    ]
    return any(
        abs(assertion.lineno - marker_call.lineno) <= max_line_distance
        for marker_call in markers for assertion in assertions
    )


def _guarded_raise(function: ast.AST, guard: str, exception: str) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.UnaryOp)
            and isinstance(test.op, ast.Not)
            and isinstance(test.operand, ast.Name)
            and test.operand.id == guard
        ):
            continue
        if any(
            isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call)
            and isinstance(child.exc.func, ast.Name)
            and child.exc.func.id == exception
            for child in ast.walk(node)
        ):
            return True
    return False


def _api_probe_failures_reach_lane_exit(tree: ast.AST) -> bool:
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    checker = functions.get("check")
    main = functions.get("main")
    if checker is None or main is None:
        return False
    records_failure = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "FAILURES"
        for node in ast.walk(checker)
    ) and any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "ok"
        for node in ast.walk(checker)
    )
    raises_for_failures = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "FAILURES"
        and any(
            isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call)
            and isinstance(child.exc.func, ast.Name)
            and child.exc.func.id == "RemoteError"
            for child in ast.walk(node)
        )
        for node in ast.walk(main)
    )
    return records_failure and raises_for_failures


def _ui_requires_fail_the_lane(tree: ast.AST) -> bool:
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    require = functions.get("require")
    if require is None or not _guarded_raise(require, "condition", "RemoteError"):
        return False
    for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
        names = set()
        if isinstance(handler.type, ast.Name):
            names.add(handler.type.id)
        elif isinstance(handler.type, ast.Tuple):
            names = {elt.id for elt in handler.type.elts if isinstance(elt, ast.Name)}
        if "RemoteError" not in names:
            continue
        calls = [
            call for node in handler.body for call in ast.walk(node)
            if isinstance(call, ast.Call)
        ]
        exits_nonzero = any(
            call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == 1
            and (
                isinstance(call.func, ast.Name) and call.func.id == "SystemExit"
                or isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "sys" and call.func.attr == "exit"
            )
            for call in calls
        )
        raises_nonzero = any(
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "SystemExit"
            and node.exc.args
            and isinstance(node.exc.args[0], ast.Constant)
            and node.exc.args[0].value == 1
            for statement in handler.body for node in ast.walk(statement)
        )
        if exits_nonzero or raises_nonzero:
            return True
    return False


def source_step_is_asserting(lane: str, step: str, source: str | None = None) -> bool:
    """Check that live API/UI step markers belong to a real lane assertion path."""
    if source is None:
        source_path = lane_source(lane)
        if source_path is None or not source_path.is_file():
            return False
        source = source_path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    if lane == "live-api-probe":
        return (
            _api_probe_failures_reach_lane_exit(tree)
            and any(_has_call(owner, "check") for owner in _step_calls(tree, "check", step))
        )
    if lane == "live-ui":
        return (
            _ui_requires_fail_the_lane(tree)
            and any(
                _step_has_nearby_assertion(owner, "report", step)
                for owner in _step_calls(tree, "report", step)
            )
        )
    return False


def lane_step_is_asserting(lane: str, step: str) -> bool:
    return source_step_is_asserting(lane, step)


def method_has_call_site(method: str) -> bool:
    quoted = rf'["\']{re.escape(method)}["\']'
    return bool(re.search(
        rf'(?:\.call|\.query|attempt|call_job|observe_rpc_method|\.call_sync)\(\s*(?:target,|page,)?\s*{quoted}'
        rf'|\(\s*{quoted}\s*,\s*\(',
        ACCEPTANCE_TEXT,
    ))


def pairs(cell: str) -> list[tuple[str, str | None]]:
    """Parse ``lane : step`` evidence pairs; several may be separated by ``;``."""
    result = []
    for part in cell.split(";"):
        found = re.fullmatch(r"\s*`([^`]+)`\s*:\s*(?:`([^`]+)`|-)\s*", part)
        assert found, f"bad lane cell {cell!r}"
        result.append((found.group(1), found.group(2)))
    return result


def doc_methods() -> set[str]:
    return {m for row in ACTIONS for m in names(row["methods"])}


def test_rows_are_well_formed_and_ids_unique() -> None:
    assert ACTIONS
    ids = [row["id"] for row in ACTIONS]
    assert len(ids) == len(set(ids))
    for row in ACTIONS:
        assert re.fullmatch(r"[AB]-\d\d", row["id"]), row
        assert row["path"] == row["id"][0], row
        assert row["verdict"] in VERDICTS, row


def test_patch_inventory_matches_release_directories() -> None:
    listed = {names(row[0])[0] for row in INVENTORY}
    actual = {
        f"{release}/{path.name}"
        for release in RELEASE_DIRS
        for path in (PATCH_ROOT / release).glob("*.patch")
    }
    assert listed == actual


def test_action_rows_reference_existing_patches_lanes_and_steps() -> None:
    patch_names = {Path(names(row[0])[0]).name for row in INVENTORY}
    for row in ACTIONS:
        for patch in names(row["patch"]):
            assert patch in patch_names, (row["id"], patch)
        for lane, step in pairs(row["lane"]):
            assert lane in RUN_GATE, (row["id"], lane)
            if step is not None:
                source = lane_source(lane)
                assert source is not None and source.is_file(), (row["id"], lane)
                assert step_exists(source.read_text(), step), (row["id"], step)


def test_every_pass_cell_names_an_asserting_lane_and_step() -> None:
    for row in ACTIONS:
        for column in ("v27", "v28"):
            if row[column].startswith("PASS"):
                steps = [(lane, step) for lane, step in pairs(row["lane"]) if step]
                assert steps, f"{row['id']} {column}: PASS needs `lane : step` evidence"
                live_ui_api_steps = [
                    (lane, step) for lane, step in steps
                    if lane in {"live-api-probe", "live-ui"}
                ]
                if live_ui_api_steps:
                    assert any(lane_step_is_asserting(lane, step) for lane, step in live_ui_api_steps), (
                        f"{row['id']} {column}: PASS must cite a live API/UI step with an AST assertion")


def test_pass_patch_ui_rows_cite_asserting_lane_steps() -> None:
    for row in ACTIONS:
        if row["verdict"] != "patch-UI":
            continue
        if not any(row[column].startswith("PASS") for column in ("v27", "v28")):
            continue
        assert any(
            step and lane_step_is_asserting(lane, step)
            for lane, step in pairs(row["lane"])
        ), f"{row['id']}: PASS patch-UI evidence must name an asserting lane step"


def test_ast_assertion_check_rejects_a_report_only_step() -> None:
    source = '''
def run():
    report("unsafe-step", "looks good")
'''
    assert not source_step_is_asserting("live-ui", "unsafe-step", source)


def test_ast_assertion_check_rejects_a_report_with_only_a_distant_assertion() -> None:
    distant_code = "\n".join("    pass" for _ in range(40))
    source = f'''\
class RemoteError(Exception):
    pass

def report(*args):
    pass

def require(condition, message):
    if not condition:
        raise RemoteError(message)

def run():
    report("hollow-step", "looks good")
{distant_code}
    require(False, "unrelated condition")

def main():
    try:
        run()
    except RemoteError:
        sys.exit(1)
'''
    assert not source_step_is_asserting("live-ui", "hollow-step", source)


def test_live_api_probe_checks_installed_pool_create_schema_before_mutation() -> None:
    source = (ACCEPTANCE / "live_api_probe.py").read_text()
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "call"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "core.get_methods"
        for node in ast.walk(tree)
    )
    assert source_step_is_asserting("live-api-probe", "pool-create-schema-live-match", source)
    main = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "main")
    check_line = next(
        node.lineno for node in ast.walk(main)
        if isinstance(node, ast.Call) and _call_name(node) == "check"
        and node.args and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "pool-create-schema-live-match"
    )
    mutation_line = next(
        node.lineno for node in ast.walk(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.IfExp)
        and any(
            isinstance(child, ast.Name) and child.id in {"run_path_a", "run_path_b"}
            for child in ast.walk(node.func)
        )
    )
    assert check_line < mutation_line


def test_reclaim_action_is_documented_as_an_api_tui_fallback() -> None:
    row = next((row for row in ACTIONS if row["id"] == "B-08"), None)
    assert row is not None
    assert row["verdict"] == "API-TUI"
    assert {"pool.export", "disk.details"} <= set(names(row["methods"]))
    assert any(step == "cleanup-partition-offered-again" for _, step in pairs(row["lane"]))

    documents = [
        PATCH_ROOT / "README.md",
        PATCH_ROOT / "v27" / "README.md",
        PATCH_ROOT / "docs" / "DESIGN-GUIDE.md",
        PATCH_ROOT / "docs" / "CONSUMERS.md",
    ]
    for path in documents:
        text = path.read_text().lower()
        assert "destroy=false" in text, path
        assert "partuuid" in text, path
        assert "zpool labelclear" in text, path
        assert "disk.details" in text, path


def test_consumer_guide_does_not_pin_the_devcontainer_venv_path() -> None:
    consumers = (PATCH_ROOT / "docs" / "CONSUMERS.md").read_text()
    assert "/home/vscode/.venv" not in consumers


def test_every_ui_patch_is_used_by_an_action_row() -> None:
    used = {patch for row in ACTIONS for patch in names(row["patch"])}
    for row in INVENTORY:
        path = names(row[0])[0]
        if row[2] == "yes" and path.startswith("v27/"):
            assert Path(path).name in used, path


def test_every_api_method_in_the_doc_is_exercised_in_acceptance_code() -> None:
    for method in sorted(doc_methods()):
        assert method_has_call_site(method), f"no call site for {method}"


def test_every_method_the_api_probe_calls_is_in_the_doc() -> None:
    probe = (ACCEPTANCE / "live_api_probe.py").read_text()
    called = set(re.findall(r'attempt\(\s*target,\s*"([a-z_.]+)"', probe))
    assert called
    assert called <= doc_methods(), called - doc_methods()


def test_every_ui_acceptance_step_that_exercises_an_action_has_a_row() -> None:
    exercising = {"path-a-root-ui", "dataset-ui", "zvol-ui", "container-ui", "vm-ui", "shares-ui",
                  "path-b-pool-ui"}
    source = lane_source("live-ui").read_text()
    row_steps = {step for row in ACTIONS for _, step in pairs(row["lane"]) if step}
    for step in exercising:
        assert re.search(rf'"{step}"', source), step
        assert step in row_steps, f"no matrix row covers live-ui step {step}"


def test_readme_and_consumer_guide_link_the_matrix() -> None:
    assert "ACTION-MATRIX.md" in (PATCH_ROOT / "README.md").read_text()
    assert "ACTION-MATRIX" in (PATCH_ROOT / "docs" / "CONSUMERS.md").read_text()


def test_live_api_probe_requires_the_selected_disk_and_boot_partition() -> None:
    required = set(RUN_GATE["live-api-probe"].get("required_env", []))
    assert {"TRUENAS_DISK_DEVICE", "TRUENAS_BOOT_POOL_PARTITION"} <= required
