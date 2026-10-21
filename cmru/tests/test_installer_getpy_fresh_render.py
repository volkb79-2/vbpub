"""KI-89 follow-up: every committed installer `get.py` equals a fresh render.

A change to `templates/get.py.tmpl` left `ciu/get.py`, `tls-edge/get.py` and `pwmcp/get.py`
stale, and only ciu had a byte-identity test, so the other two would have drifted silently.
For EVERY project that declares `[project.installer]` with an `asset_suffix`, this renders the
installer with THIS checkout's own cmru (`cmru get-py <project> --output-dir`, in-process, from
the repository root) and compares it with the committed `<project>/get.py`.

Like the installer-producer oracle it skips ONLY in a tree without `.git` (the isolated canary
copy has no sibling projects); in a git checkout a stale or missing file is a failure.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

import cmru
from cmru.cli import main as cmru_main
from tests.test_installer_asset_names import IS_CHECKOUT, REPO, _installer_projects

needs_checkout = pytest.mark.skipif(
    not IS_CHECKOUT, reason="not a git checkout (isolated canary tree)"
)


def staleness(rendered: str, committed: Path) -> str | None:
    """None when ``committed`` equals ``rendered``; else a short unified diff / reason."""
    if not committed.is_file():
        return f"{committed} is missing"
    text = committed.read_text(encoding="utf-8")
    if text == rendered:
        return None
    diff = difflib.unified_diff(
        text.splitlines(), rendered.splitlines(), "committed", "fresh render", lineterm="", n=0
    )
    return "\n".join(list(diff)[:12])


def render_project(name: str, out_dir: Path, monkeypatch) -> str:
    # Every comparison carries this: a render by an installed wheel's template would make the
    # byte-equality below pass or fail for the wrong reason.
    assert Path(cmru.__file__).resolve().is_relative_to((REPO / "cmru" / "src").resolve()), (
        f"cmru imported from {cmru.__file__}, not the checkout's cmru/src: the render would "
        "use another template"
    )
    monkeypatch.chdir(REPO)
    assert cmru_main(["get-py", name, "--output-dir", str(out_dir)]) in (0, None)
    return (out_dir / f"{name}-get.py").read_text(encoding="utf-8")


@needs_checkout
@pytest.mark.parametrize("project", _installer_projects(), ids=lambda p: p.name)
def test_every_committed_getpy_equals_a_fresh_render(project, tmp_path, monkeypatch):
    rendered = render_project(project.name, tmp_path, monkeypatch)
    problem = staleness(rendered, project / "get.py")
    assert problem is None, f"{project.name}/get.py is stale against the template:\n{problem}"


# ─── controlled wrong implementation: the check itself must be able to go red ───────────

@needs_checkout
def test_a_stale_committed_file_is_reported(tmp_path, monkeypatch):
    rendered = render_project("tls-edge", tmp_path, monkeypatch)
    stale = tmp_path / "get.py"
    stale.write_text(rendered.replace("def download_and_verify(", "def download_and_verify_OLD(", 1),
                     encoding="utf-8")
    report = staleness(rendered, stale)
    assert report is not None and "download_and_verify" in report


def test_a_missing_committed_file_is_reported_and_an_equal_one_passes(tmp_path):
    assert "missing" in staleness("x\n", tmp_path / "nope.py")
    good = tmp_path / "get.py"
    good.write_text("x\n", encoding="utf-8")
    assert staleness("x\n", good) is None
