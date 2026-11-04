"""The in-cockpit half of runner/setup-cockpit-run-gate.sh, run in real bash against fake python3/pip: idempotent, never leaves a half-built venv."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runner" / "setup-cockpit-run-gate.sh"
INNER = re.search(r"<<'INNER'\n(.*?)\nINNER\n", SCRIPT.read_text(), re.S).group(1)
WHEEL = b"fake wheel bytes"
SHA = hashlib.sha256(WHEEL).hexdigest()

FAKE_PYTHON = r'''#!/usr/bin/env bash
if [[ $1 == */fetch-run-gate-wheel.py ]]; then
  [[ -z ${FAKE_FETCH_FAIL:-} ]] || exit 1
  mkdir -p "$HOME/wheels"; printf 'fake wheel bytes' > "$HOME/wheels/run_gate-$FAKE_VER-py3-none-any.whl"; echo fetch >> "$HOME/calls"; exit 0
fi
if [[ $1 == -m && $2 == venv ]]; then
  mkdir -p "$3/bin"; echo "venv $3" >> "$HOME/calls"
  printf '#!/usr/bin/env bash\n[[ -z ${FAKE_PIP_FAIL:-} ]] || exit 1\necho "pip $*" >> "$HOME/calls"\nprintf "#!/usr/bin/env bash\\necho run-gate ${FAKE_BUILT:-$FAKE_VER}\\n" > "$(dirname "$0")/run-gate"; chmod +x "$(dirname "$0")/run-gate"\n' > "$3/bin/pip"
  chmod +x "$3/bin/pip"; exit 0
fi
exit 1
'''


@pytest.fixture
def env(tmp_path):
    (tmp_path / "bin").mkdir()
    py = tmp_path / "bin" / "python3"
    py.write_text(FAKE_PYTHON)
    py.chmod(py.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    src = tmp_path / "ws" / "abc123" / "tester-unified"
    src.mkdir(parents=True)
    return {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "HOME": str(home), "COMMIT": "abc123", "RG_VERSION": "25.0.2", "RG_SHA": SHA,
            "RG_WORKSPACES": str(tmp_path / "ws"), "FAKE_VER": "25.0.2"}


def _run(env, **extra):
    return subprocess.run(["bash", "-s"], input=INNER, env={**env, **extra}, capture_output=True, text=True)


def _calls(env):
    p = Path(env["HOME"]) / "calls"
    return p.read_text().splitlines() if p.exists() else []


def test_first_run_builds_the_venv_and_a_second_run_is_a_noop(env):
    r = _run(env)
    assert r.returncode == 0 and r.stdout.strip() == "run-gate 25.0.2", r.stderr
    venv = Path(env["HOME"]) / ".venvs" / "gate-tools2502"
    assert (venv / "bin" / "run-gate").exists()
    assert any(c.startswith("pip install -q --no-deps --no-index ") for c in _calls(env))  # nothing is fetched from an index
    before = _calls(env)
    r = _run(env)
    assert r.returncode == 0 and "nothing to do" in r.stdout and _calls(env) == before


def test_a_failed_install_leaves_no_venv_and_the_rerun_rebuilds_clean(env):
    r = _run(env, FAKE_PIP_FAIL="1")
    assert r.returncode != 0 and "no venv left" in r.stderr
    venvs = Path(env["HOME"]) / ".venvs"
    assert not (venvs / "gate-tools2502").exists() and len(list(venvs.glob("gate-tools2502.failed.*"))) == 1
    r = _run(env)
    assert r.returncode == 0 and (venvs / "gate-tools2502" / "bin" / "run-gate").exists()


def test_a_venv_that_reports_the_wrong_version_is_rejected_and_moved_aside(env):
    r = _run(env, FAKE_BUILT="25.0.1rc1")
    assert r.returncode != 0
    assert not (Path(env["HOME"]) / ".venvs" / "gate-tools2502").exists()


def test_a_legacy_half_built_venv_is_moved_aside_not_trusted(env):
    legacy = Path(env["HOME"]) / ".venvs" / "gate-tools2502" / "bin"
    legacy.mkdir(parents=True)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    assert "moved the unusable" in r.stderr and len(list((Path(env["HOME"]) / ".venvs").glob("gate-tools2502.failed.*"))) == 1


def test_wheel_digest_mismatch_and_missing_checkout_refuse_before_any_venv(env):
    r = _run(env, RG_SHA="0" * 64)
    assert r.returncode != 0 and not (Path(env["HOME"]) / ".venvs").exists()
    r = _run(env, RG_WORKSPACES=str(Path(env["HOME"]) / "nowhere"))
    assert r.returncode == 4 and "recent sync" in r.stderr
