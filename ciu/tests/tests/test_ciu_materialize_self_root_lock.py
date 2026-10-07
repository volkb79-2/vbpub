"""Regression: materialize() must not self-deadlock when a stack is its own ciu root.

With a GEN_LOCAL spec, materialize takes the stack lock then the project lock
(S4.26). For a stack that IS the ciu root (pwmcp/mattermost layout) both are
<root>/.ciu/lock. flock locks an open file description, so the second
os.open()+LOCK_EX blocked forever on the process's own first lock (`ciu up`
hung at STEP 10/17 "Resolving and materializing secrets").

The deadlock cases run in a REAL subprocess with a hard timeout so an unfixed
tree fails fast instead of hanging the suite.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")
TIMEOUT_S = 8

_CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, {src!r})
from ciu.secrets.directives import parse_value
from ciu.secrets.materialize import materialize
specs = [parse_value("token", "GEN_LOCAL:shared/token", "mystack.secrets")]
res = materialize(
    specs,
    stack_dir=Path({stack!r}),
    repo_root=Path({root!r}),
    vault=None,
    assume_yes=True,
    env={{}},
    chown_fn=lambda p, u, g: None,
)
print("OK", bool(res["token"].value))
"""


def _run_real_flock(stack: Path, root: Path) -> subprocess.CompletedProcess:
    code = _CHILD.format(src=SRC, stack=str(stack), root=str(root))
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )


def test_stack_is_own_root_does_not_deadlock_gen_local(tmp_path):
    """stack_dir == repo_root + GEN_LOCAL completes (real flock, no self-block)."""
    root = tmp_path / "root"
    root.mkdir()
    try:
        proc = _run_real_flock(root, root)
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"materialize() hung >{TIMEOUT_S}s: same-file stack/project lock "
            "self-deadlock (stack_dir == repo_root)"
        )
    assert proc.returncode == 0, proc.stderr
    assert "OK True" in proc.stdout


def test_stack_reached_via_symlink_to_root_does_not_deadlock(tmp_path):
    """A stack dir that is a symlink to the root resolves equal: no deadlock."""
    root = tmp_path / "root"
    root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    try:
        proc = _run_real_flock(link, root)
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"materialize() hung >{TIMEOUT_S}s: symlinked stack_dir resolving "
            "to repo_root self-deadlocked on the shared lock file"
        )
    assert proc.returncode == 0, proc.stderr
    assert "OK True" in proc.stdout


def test_distinct_stack_and_root_take_both_locks_stack_first(tmp_path, monkeypatch):
    """Different lock files + GEN_LOCAL: stack lock then project lock (S4.26)."""
    sys.path.insert(0, SRC)
    from ciu.secrets import materialize as mat
    from ciu.secrets.directives import parse_value

    root = tmp_path / "root"
    stack = root / "stacks" / "app"
    stack.mkdir(parents=True)

    taken: list[Path] = []
    real_flock = mat._flock

    def spy(lock_path):
        taken.append(Path(lock_path))
        return real_flock(lock_path)

    monkeypatch.setattr(mat, "_flock", spy)
    specs = [parse_value("token", "GEN_LOCAL:shared/token", "mystack.secrets")]
    mat.materialize(
        specs,
        stack_dir=stack,
        repo_root=root,
        vault=None,
        assume_yes=True,
        env={},
        chown_fn=lambda p, u, g: None,
    )

    assert taken == [
        stack / mat.MACHINE_DIR / mat.LOCK_NAME,
        root / mat.MACHINE_DIR / mat.LOCK_NAME,
    ]
