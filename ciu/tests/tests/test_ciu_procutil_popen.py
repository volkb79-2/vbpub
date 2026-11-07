"""CIU-141: ``procutil.popen`` starts a streaming child with exactly the given wiring.

The foreground one-shot lifecycle inspects and signals a container while the
attached ``docker compose run`` child keeps running, so ``popen`` must pass its
environment, working directory, pipes and session flag through unchanged. A
trivial Python child stands in for Compose; no Docker is involved.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import procutil  # noqa: E402

_CHILD = (
    "import os, sys\n"
    "sys.stdout.write(os.getcwd() + '|' + os.environ['CIU_POPEN_PROBE'] + '|'"
    " + str(os.getsid(0) == os.getpid()))\n"
    "sys.stderr.write('err-line')\n"
)


def test_popen_passes_env_cwd_pipes_and_session_through(tmp_path):
    env = {**os.environ, "CIU_POPEN_PROBE": "probe-value"}
    child = procutil.popen(
        [sys.executable, "-c", _CHILD],
        env=env, cwd=str(tmp_path),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    out, err = child.communicate(timeout=60)
    assert child.returncode == 0
    assert out == f"{tmp_path.resolve()}|probe-value|True"
    assert err == "err-line"


def test_popen_defaults_to_text_mode_in_the_callers_session(tmp_path):
    child = procutil.popen(
        [sys.executable, "-c", "import os; print(os.getsid(0) == os.getpid())"],
        stdout=subprocess.PIPE,
    )
    out, _ = child.communicate(timeout=60)
    assert isinstance(out, str)  # text=True by default
    assert out.strip() == "False"  # no new session unless asked for
