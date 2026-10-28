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
LANE_SOURCES = {
    "live-ui": ACCEPTANCE / "live_ui.py",
    "live-ui-workloads": ACCEPTANCE / "live_ui_workloads.py",
}
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
        lane_cell = names(row["lane"])
        assert lane_cell[0] in RUN_GATE, (row["id"], lane_cell[0])
        if len(lane_cell) > 1:
            source = LANE_SOURCES[lane_cell[0]].read_text()
            assert re.search(rf'report\(\s*"{re.escape(lane_cell[1])}"', source), (
                row["id"], lane_cell[1])


def test_every_ui_patch_is_used_by_an_action_row() -> None:
    used = {patch for row in ACTIONS for patch in names(row["patch"])}
    for row in INVENTORY:
        path = names(row[0])[0]
        if row[2] == "yes" and path.startswith("v27/"):
            assert Path(path).name in used, path


def test_every_api_method_in_the_doc_is_exercised_in_acceptance_code() -> None:
    for method in sorted(doc_methods()):
        assert f'"{method}"' in ACCEPTANCE_TEXT or f"'{method}'" in ACCEPTANCE_TEXT, method


def test_every_method_the_api_probe_calls_is_in_the_doc() -> None:
    probe = (ACCEPTANCE / "live_api_probe.py").read_text()
    called = set(re.findall(r'attempt\(\s*target,\s*"([a-z_.]+)"', probe))
    assert called
    assert called <= doc_methods(), called - doc_methods()


def test_every_ui_acceptance_step_that_exercises_an_action_has_a_row() -> None:
    exercising = {"path-a-root-ui", "dataset-ui", "zvol-ui", "container-ui", "vm-ui", "shares-ui",
                  "path-b-partition-ui-inventory"}
    source = LANE_SOURCES["live-ui"].read_text()
    row_steps = {names(row["lane"])[1] for row in ACTIONS if len(names(row["lane"])) > 1}
    for step in exercising:
        assert re.search(rf'"{step}"', source), step
        assert step in row_steps, f"no matrix row covers live-ui step {step}"


def test_readme_and_consumer_guide_link_the_matrix() -> None:
    assert "ACTION-MATRIX.md" in (PATCH_ROOT / "README.md").read_text()
    assert "ACTION-MATRIX" in (PATCH_ROOT / "docs" / "CONSUMERS.md").read_text()
