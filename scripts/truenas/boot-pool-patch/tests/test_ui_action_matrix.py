"""Keep UI-ACTION-MATRIX.md and the patch/lane code in sync."""

from __future__ import annotations

from pathlib import Path
import re
import tomllib

PATCH_ROOT = Path(__file__).resolve().parents[1]
TRUENAS_ROOT = PATCH_ROOT.parent
DOC = (PATCH_ROOT / "UI-ACTION-MATRIX.md").read_text()
RELEASE_DIRS = ("v27", "v28-nightly-20261014")
LANE_SOURCES = {
    "live-ui": PATCH_ROOT / "acceptance" / "live_ui.py",
    "live-ui-workloads": PATCH_ROOT / "acceptance" / "live_ui_workloads.py",
}
VERDICTS = {"works", "patch-UI", "API-TUI", "n/a", "pending decision"}


def section(title: str) -> list[list[str]]:
    body = DOC.split(f"## {title}", 1)[1]
    rows = []
    for line in body.splitlines()[1:]:
        if line.startswith("## "):
            break
        if line.startswith("|") and not set(line) <= set("|- "):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.append(cells)
    return rows[1:]  # drop the header row


def names(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


ACTIONS = section("Actions")
INVENTORY = section("Patch inventory")
RUN_GATE = tomllib.loads((TRUENAS_ROOT / "run-gate.toml").read_text())["lanes"]


def test_rows_are_well_formed_and_ids_unique() -> None:
    assert ACTIONS
    ids = [row[0] for row in ACTIONS]
    assert len(ids) == len(set(ids))
    for row in ACTIONS:
        assert len(row) == 10, row
        assert row[6] in VERDICTS, row


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
        for patch in names(row[4]):
            if patch.endswith(".patch"):
                assert patch in patch_names, (row[0], patch)
        lane_cell = names(row[7])
        lane = lane_cell[0]
        assert lane in RUN_GATE, (row[0], lane)
        if len(lane_cell) > 1:
            source = LANE_SOURCES[lane].read_text()
            assert re.search(rf'report\(\s*"{re.escape(lane_cell[1])}"', source), (
                row[0], lane_cell[1])


def test_every_ui_patch_is_used_by_an_action_row() -> None:
    used = {patch for row in ACTIONS for patch in names(row[4]) if patch.endswith(".patch")}
    for row in INVENTORY:
        path = names(row[0])[0]
        if row[2] == "yes" and path.startswith("v27/"):
            assert Path(path).name in used, path


def test_every_ui_acceptance_step_that_exercises_an_action_has_a_row() -> None:
    # These live-ui steps submit or open a storage action; each needs a matrix row.
    exercising = {"path-a-root-ui", "dataset-ui", "zvol-ui", "container-ui", "vm-ui", "shares-ui",
                  "path-b-partition-ui-inventory"}
    source = LANE_SOURCES["live-ui"].read_text()
    rows_steps = {names(row[7])[1] for row in ACTIONS if len(names(row[7])) > 1}
    for step in exercising:
        assert step in source, step
        assert step in rows_steps, f"no matrix row covers live-ui step {step}"


def test_readme_links_the_matrix() -> None:
    assert "UI-ACTION-MATRIX.md" in (PATCH_ROOT / "README.md").read_text()
