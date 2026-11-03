from __future__ import annotations

import re
from pathlib import Path

DOCS = ("README.md", "DESIGN-GUIDE.md", "CONSUMERS.md")
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def test_public_vocabulary_is_documented():
    docs = "\n".join(Path(name).read_text(encoding="utf-8") for name in DOCS).lower()
    for value in (
        "cowtree_mode",
        "auto",
        "off",
        "btrfs",
        "reflink",
        "copy",
        "flock",
        "external",
        "zstd",
        "zstd:n",
        "lzo",
        "none",
        "zfs",
        "no native zfs dataset backend",
        "detect",
        "survey",
        "gc",
        "make-shared",
        "api_version = 1",
        "cowtree gc --scan",
        "64 kib",
        "root-invalid",
        "lease",
    ):
        assert value in docs


def test_cross_document_links_resolve():
    root = Path(__file__).parents[1]
    for document in DOCS:
        path = root / document
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            file_part, separator, anchor = target.partition("#")
            linked = (path.parent / file_part).resolve() if file_part else path
            assert linked.is_file(), f"{document} links to missing {target!r}"
            if separator and anchor:
                headings = {
                    re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower())
                    .replace(" ", "-")
                    for line in linked.read_text(encoding="utf-8").splitlines()
                    if line.startswith("#")
                }
                assert anchor.lower() in headings, f"{document} links to missing anchor {target!r}"


def test_readme_features_link_to_design_guide_sections():
    links = LINK.findall(Path("README.md").read_text(encoding="utf-8"))
    design_sections = {
        target.partition("#")[2]
        for target in links
        if target.startswith("DESIGN-GUIDE.md#")
    }
    assert {
        "one-adapter-for-three-storage-paths",
        "ownership-and-identity",
        "input-derived-space-admission",
        "cli-exit-statuses",
    } <= design_sections


def test_readme_cli_exit_code_table_matches_error_code_mapping():
    from cowtree.cli import _ERROR_EXIT_CODES

    readme = Path("README.md").read_text(encoding="utf-8")
    for code, status in _ERROR_EXIT_CODES.items():
        assert f"| {status} | `{code}` |" in readme
    assert "| 2 | `usage` |" in readme


def test_lane_configs_use_current_schemas_and_closed_coverage_gate():
    import tomllib

    with (Path(__file__).parents[1] / "run-gate.toml").open("rb") as handle:
        run_gate = tomllib.load(handle)
    with (Path(__file__).parents[1] / "assay.toml").open("rb") as handle:
        assay = tomllib.load(handle)

    assert run_gate["schema_version"] == 1
    assert run_gate["lanes"]["cowtree"]["kind"] == "assay"
    lane = assay["lanes"]["cowtree"]
    assert assay["schema_version"] == 2
    assert lane["rigor"] == ["R0", "R1"]
    assert lane["judge"]["fail_under"] == 100.0
    assert lane["judge"]["require_branch"] is True
