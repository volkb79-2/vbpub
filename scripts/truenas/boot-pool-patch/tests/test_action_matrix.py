"""Keep ACTION-MATRIX.md and the patch/lane/API code in sync."""

from __future__ import annotations

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


def lane_fails_on_assertion_errors(lane: str) -> bool:
    source_path = lane_source(lane)
    if source_path is None or not source_path.is_file():
        return False
    source = source_path.read_text()
    if lane == "live-api-probe":
        return (
            "if not ok:" in source
            and "FAILURES.append(key)" in source
            and "if FAILURES:" in source
            and "raise RemoteError(f\"failed checks:" in source
        )
    if lane == "live-ui":
        return (
            "def require(condition: bool, message: str) -> None:" in source
            and "raise RemoteError(message)" in source
            and "except (RemoteError, KeyError, TypeError, ValueError)" in source
        )
    return False


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
                assert any(step for _, step in pairs(row["lane"])), (
                    f"{row['id']} {column}: PASS needs `lane : step` evidence")


def test_pass_patch_ui_rows_cite_asserting_lane_steps() -> None:
    for row in ACTIONS:
        if row["verdict"] != "patch-UI":
            continue
        if not any(row[column].startswith("PASS") for column in ("v27", "v28")):
            continue
        assert any(
            step and step_exists(lane_source(lane).read_text(), step)
            and lane_fails_on_assertion_errors(lane)
            for lane, step in pairs(row["lane"])
        ), f"{row['id']}: PASS patch-UI evidence must name an asserting lane step"


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
