"""PWMCP-71: the release-scoped R2 candidate selection is an algorithm with tests, not prose.

A release-to-release campaign runs a SUBSET of the whole-target plan: the candidates whose
mutation sites fall inside the lines that release changed. `docs/CONSUMERS.md` documents the
recipe; `tools/r2/select_changed_candidates.py` is the step that is easy to get wrong by hand
(reading `git diff -U0` hunk headers, stripping the `a/`/`b/` prefixes, skipping pure-deletion
hunks) and this module tests it on synthetic fixtures, plus the documentation contract:

* the documented flags exist in the shipped tools (`run-gate --only-candidates`,
  `assay plan --json`/`--list-pending`),
* the recipe states the result is PARTIAL and never a whole-target R2 PASS,
* `assay plan --file` is named as the trap it is (a TOML config, not a source filter).
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PWMCP = Path(__file__).resolve().parents[1]
TOOL = PWMCP / "tools" / "r2" / "select_changed_candidates.py"
CONSUMERS = PWMCP / "docs" / "CONSUMERS.md"

sys.path.insert(0, str(TOOL.parent))
import select_changed_candidates as selector  # noqa: E402  (the tool under test)


def _plan(*entries: tuple[str, int]) -> dict:
    return {
        "lane": "r2",
        "candidate_count": len(entries),
        "candidates": [
            {"id": f"id-{path.rsplit('/', 1)[-1]}-{lineno}", "path": path, "lineno": lineno, "operator": "python:bool-const-flip"}
            for path, lineno in entries
        ],
    }


DIFF = """diff --git a/pwmcp/client/src/pwmcp_client/blocked.py b/pwmcp/client/src/pwmcp_client/blocked.py
index 1111111..2222222 100644
--- a/pwmcp/client/src/pwmcp_client/blocked.py
+++ b/pwmcp/client/src/pwmcp_client/blocked.py
@@ -40,0 +41,3 @@ def detect_block(html, status):
+    if html is None:
+        return None
+    if status is not None:
@@ -80 +83 @@ def detect_block(html, status):
-    return None
+    return verdict
diff --git a/pwmcp/client/src/pwmcp_client/cli.py b/pwmcp/client/src/pwmcp_client/cli.py
--- a/pwmcp/client/src/pwmcp_client/cli.py
+++ b/pwmcp/client/src/pwmcp_client/cli.py
@@ -900 +900,0 @@
-    unreachable = 1
"""


def test_the_new_file_line_ranges_come_from_the_hunk_headers() -> None:
    ranges = selector.changed_line_ranges(DIFF)
    assert ranges == {
        "pwmcp/client/src/pwmcp_client/blocked.py": [(41, 43), (83, 83)],
    }, "a pure-deletion hunk (+900,0) must contribute no range, and `+83` means exactly one line"


def test_selection_is_by_path_and_line_inside_the_changed_ranges() -> None:
    plan = _plan(
        ("pwmcp/client/src/pwmcp_client/blocked.py", 40),  # before the hunk
        ("pwmcp/client/src/pwmcp_client/blocked.py", 41),  # first changed line
        ("pwmcp/client/src/pwmcp_client/blocked.py", 43),  # last changed line
        ("pwmcp/client/src/pwmcp_client/blocked.py", 44),  # after the hunk
        ("pwmcp/client/src/pwmcp_client/blocked.py", 83),  # the single-line hunk
        ("pwmcp/client/src/pwmcp_client/cli.py", 900),  # a file with deletions only
        ("pwmcp/build-push.py", 46),  # a file the diff does not mention
    )
    selected = selector.select_candidates(plan, selector.changed_line_ranges(DIFF))
    assert selected == [
        "id-blocked.py-41",
        "id-blocked.py-43",
        "id-blocked.py-83",
    ]


def test_the_documented_command_writes_one_id_per_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(_plan(("pwmcp/client/src/pwmcp_client/blocked.py", 42))), encoding="utf-8")
    diff_file = tmp_path / "release.diff"
    diff_file.write_text(DIFF, encoding="utf-8")
    out = tmp_path / ".run-gate" / "only-candidates-r10.txt"
    assert selector.main(["--plan", str(plan_file), "--diff", str(diff_file), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == "id-blocked.py-42\n"
    captured = capsys.readouterr()
    assert "selected 1 of 1 candidates" in captured.err


def test_an_empty_selection_fails_loudly_instead_of_writing_an_empty_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(_plan(("pwmcp/client/src/pwmcp_client/cli.py", 12))), encoding="utf-8")
    diff_file = tmp_path / "release.diff"
    diff_file.write_text(DIFF, encoding="utf-8")
    out = tmp_path / "only.txt"
    assert selector.main(["--plan", str(plan_file), "--diff", str(diff_file), "--out", str(out)]) == 1
    assert not out.exists(), "an empty candidate list must not be written: run-gate would run nothing"
    assert "no candidate falls inside the changed lines" in capsys.readouterr().err


def test_a_malformed_plan_is_refused_rather_than_partially_selected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no 'candidates' list"):
        selector.select_candidates({"lane": "r2"}, {})
    with pytest.raises(ValueError, match="malformed candidate"):
        selector.select_candidates({"candidates": [{"path": "a.py"}]}, {})


@pytest.mark.parametrize("header", ["@@ nonsense @@", "@@ -1 +x @@"])
def test_an_unparseable_hunk_header_is_refused(header: str) -> None:
    with pytest.raises(ValueError, match="unparseable hunk header"):
        selector.changed_line_ranges(f"+++ b/a.py\n{header}\n")


def test_the_recipe_names_the_shipped_flags() -> None:
    assert TOOL.is_file(), "docs/CONSUMERS.md names a selection tool that does not exist"
    text = " ".join(CONSUMERS.read_text(encoding="utf-8").split())
    assert "Release-scoped R2 campaigns" in text
    assert "assay plan r2 --json" in text
    assert "git diff -U0" in text
    assert ".run-gate/only-candidates-" in text
    assert "run-gate r2 --only-candidates" in text
    assert ".assay/progress-r2.jsonl" in text  # the resume/progress handling stays run-gate's
    assert "tools/r2/select_changed_candidates.py" in text
    assert (PWMCP / "tools" / "r2" / "README.md").is_file()
    # the claim is PARTIAL by construction, and the item's own trap is named
    assert "PARTIAL by construction" in text
    assert "never a whole-target R2 PASS" in text
    assert "selective" in text
    assert "assay plan --file" in text and "not a source-file filter" in text


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    duplicates: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match is None:
            continue
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", match.group(1))
        title = re.sub(r"`([^`]+)`", r"\1", title)
        slug = re.sub(r"\s", "-", re.sub(r"[^\w -]", "", title.lower()).strip())
        count = duplicates.get(slug, 0)
        duplicates[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def test_every_cross_document_link_in_the_recipe_resolves() -> None:
    """README, CONSUMERS, the design guide and the tool's README link each other."""

    links = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    documents = [
        PWMCP / "README.md",
        CONSUMERS,
        PWMCP / "docs" / "DESIGN-GUIDE.md",
        PWMCP / "tools" / "r2" / "README.md",
    ]
    for source in documents:
        for raw in links.findall(source.read_text(encoding="utf-8")):
            target, _, fragment = raw.strip().split(maxsplit=1)[0].partition("#")
            if not fragment or "://" in raw:
                continue
            resolved = source if not target else (source.parent / target)
            assert resolved.is_file(), f"{source.name} links to missing {target}"
            assert fragment in _anchors(resolved), (
                f"{source.name} links to missing anchor {fragment!r} in {resolved.name}"
            )


def _command_output(argv: list[str]) -> tuple[int, str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    return result.returncode, result.stdout + result.stderr


def test_the_documented_flags_exist_in_the_shipped_tools() -> None:
    """Pin the recipe to the tools that actually accept the flags, not to the prose."""

    # run-gate is installed in the gate image at a pinned version; --only-candidates must exist
    # there, otherwise the documented step 4 cannot be run at all.
    assert shutil.which("run-gate") is not None, (
        "run-gate is not on PATH, so the documented `run-gate r2 --only-candidates` step cannot be "
        "checked; run this suite inside the registered tester-unified gate image"
    )
    code, run_gate = _command_output(["run-gate", "--help"])
    assert code == 0, run_gate[-500:]
    assert "--only-candidates FILE" in run_gate, "run-gate no longer accepts the documented --only-candidates"

    # The plan verbs the recipe uses must be present in THIS interpreter's assay. An
    # interpreter without assay is a MISSING DEPENDENCY, not a docs defect: fail with the
    # remedy instead of letting the assertion read as "the recipe is wrong".
    assert importlib.util.find_spec("assay") is not None, (
        "assay is not installed in this interpreter, so the documented `assay plan` step cannot be "
        "checked; install the judge the lane uses (run-gate installs it from the selected worktree)"
    )
    code, plan_help = _command_output([sys.executable, "-m", "assay.cli", "plan", "--help"])
    assert code == 0, plan_help[-500:]
    assert "--json" in plan_help
    assert "--file" in plan_help

    # the selection tool documents the exact options the recipe passes
    code, tool_help = _command_output([sys.executable, str(TOOL), "--help"])
    assert code == 0, tool_help[-500:]
    for flag in ("--plan", "--diff", "--out"):
        assert flag in tool_help, flag
