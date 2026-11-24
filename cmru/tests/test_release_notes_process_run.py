"""Caller contracts for release-note source reads at a recorded commit."""

from __future__ import annotations

import subprocess
from unittest.mock import Mock, call

from cmru import release_notes


def test_release_notes_process_sites_preserve_text_and_bytes_modes(
    monkeypatch, tmp_path,
):
    commit = "a" * 40
    run = Mock(side_effect=[
        subprocess.CompletedProcess(["git", "rev-parse"], 0, "nested/\n", ""),
        subprocess.CompletedProcess(["git", "show"], 0, b"# release notes\n", b""),
    ])
    monkeypatch.setattr(release_notes, "run_process", run)

    assert release_notes._read_changelog(
        tmp_path, "CHANGES.md", commit,
    ) == ("# release notes\n", f"CHANGES.md at {commit[:12]}")
    assert run.call_args_list == [
        call(
            ["git", "-C", str(tmp_path), "rev-parse", "--show-prefix"],
            capture=True, text=True, check=False,
        ),
        call(
            ["git", "-C", str(tmp_path), "show", f"{commit}:nested/CHANGES.md"],
            capture=True, check=False, text=None,
        ),
    ]
