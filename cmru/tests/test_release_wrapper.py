"""The root release wrapper owns the durable outer audit log."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "cmru.release.sh"


def _run(log_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["CMRU_RELEASE_LOG"] = str(log_path)
    return subprocess.run(
        [str(WRAPPER), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_release_wrapper_overwrites_then_appends_with_divider(tmp_path):
    log = tmp_path / "cmru.release.log"
    log.write_text("old run\n", encoding="utf-8")

    first = _run(log, "--help")
    assert first.returncode == 0
    first_contents = log.read_text(encoding="utf-8")
    assert "old run" not in first_contents
    assert "--show-run-details" in first_contents

    second = _run(log, "--help", "--log-append")
    assert second.returncode == 0
    contents = log.read_text(encoding="utf-8")
    assert "\n---\n" in contents
    assert contents.count("--show-run-details") >= 2
