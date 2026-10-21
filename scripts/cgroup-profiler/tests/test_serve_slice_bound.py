"""Sol B4 -- `cgprofile serve` refuses to run unless the slice the daemon is
ACTUALLY IN (derived from its own cgroup, not looked up by name) is bounded.
Kept apart from test_cgprofile.py, which needs the report tier's pandas/plotly
stack; this check is collector-tier only."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cgprofile as cg  # noqa: E402
from lib import access  # noqa: E402
from lib import serve as serve_lib  # noqa: E402
from lib import targets  # noqa: E402


def _tree(tmp_path: Path, *, own: str, memory_max: Optional[str], slice_name="cgprofile.slice"):
    """A fake cgroup root with `slice_name` carrying `memory_max`, and the
    daemon reported (through the cgroup_of_pid seam) at `own`."""
    root = tmp_path / "cgroup"
    slice_dir = root / slice_name
    slice_dir.mkdir(parents=True)
    if memory_max is not None:
        (slice_dir / "memory.max").write_text(memory_max)
    return root


@pytest.fixture
def own_cgroup(monkeypatch):
    def set_own(path: Optional[str], pid: Optional[int] = 4242):
        monkeypatch.setattr(serve_lib.placement_mod, "_host_pid_of_self", lambda _p: pid)
        monkeypatch.setattr(targets, "cgroup_of_pid", lambda *_a, **_k: path)
    return set_own


class TestDaemonSliceProblem:
    def test_bounded_own_slice_passes(self, tmp_path, own_cgroup):
        root = _tree(tmp_path, own="", memory_max="1073741824\n")
        own_cgroup("/cgprofile.slice/docker-abc.scope")
        assert serve_lib.daemon_slice_problem(str(root), "/proc") is None
        assert serve_lib.daemon_slice_bounded(str(root), "/proc") is True

    @pytest.mark.parametrize("memory_max", ["max\n", "0\n", None, "garbage\n", "\n"])
    def test_unbounded_unreadable_zero_or_garbage_memory_max_refuses(
        self, tmp_path, own_cgroup, memory_max,
    ):
        root = _tree(tmp_path, own="", memory_max=memory_max)
        own_cgroup("/cgprofile.slice/docker-abc.scope")
        problem = serve_lib.daemon_slice_problem(str(root), "/proc")
        assert problem is not None and "zero" in problem and "not a finite positive" in problem

    def test_a_bounded_cgprofile_slice_does_not_vouch_for_another_parent(
        self, tmp_path, own_cgroup,
    ):
        # B-1: the named slice exists and is bounded, but THIS daemon's
        # container was created under a different parent.
        root = _tree(tmp_path, own="", memory_max="1073741824\n")
        own_cgroup("/system.slice/docker-abc.scope")
        problem = serve_lib.daemon_slice_problem(str(root), "/proc")
        assert problem is not None and "not under cgprofile.slice" in problem
        assert serve_lib.daemon_slice_bounded(str(root), "/proc") is False

    def test_a_nested_cgprofile_slice_is_not_the_top_level_slice(self, tmp_path, own_cgroup):
        root = _tree(tmp_path, own="", memory_max="1073741824\n")
        own_cgroup("/dev.slice/cgprofile.slice/docker-abc.scope")
        assert "not under cgprofile.slice" in serve_lib.daemon_slice_problem(str(root), "/proc")

    def test_underivable_own_cgroup_or_pid_refuses(self, tmp_path, own_cgroup):
        root = _tree(tmp_path, own="", memory_max="1073741824\n")
        own_cgroup(None)
        assert "cannot derive" in serve_lib.daemon_slice_problem(str(root), "/proc")
        own_cgroup("/cgprofile.slice/x.scope", pid=None)
        assert "host PID" in serve_lib.daemon_slice_problem(str(root), "/proc")


class TestServeRefusesAnUnboundedDaemonSlice:
    def _prep(self, monkeypatch, tmp_path):
        monkeypatch.setattr(access, "CGROUP_ROOT", str(tmp_path / "cgroup"))
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda r: True)
        monkeypatch.setenv(access.PID_NAMESPACE_MODE_ENV, "host")
        monkeypatch.setattr(access, "have_host_proc_view", lambda proc: True)
        monkeypatch.setattr(
            serve_lib, "SessionServer",
            lambda **kwargs: pytest.fail("must not construct a server"),
        )

    def test_refusal_names_zero_and_why_and_the_install_step(
        self, monkeypatch, tmp_path, capsys, own_cgroup,
    ):
        _tree(tmp_path, own="", memory_max="0\n")
        own_cgroup("/cgprofile.slice/docker-abc.scope")
        self._prep(monkeypatch, tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            cg.cmd_serve(cg.build_parser().parse_args(["serve"]))
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "zero is refused because" in err
        assert "misconfiguration, not containment" in err
        assert "infra/cgprofile.slice" in err and "CIU-150" in err

    def test_wrong_parent_refuses_even_with_a_bounded_named_slice(
        self, monkeypatch, tmp_path, capsys, own_cgroup,
    ):
        _tree(tmp_path, own="", memory_max="1073741824\n")
        own_cgroup("/system.slice/docker-abc.scope")
        self._prep(monkeypatch, tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            cg.cmd_serve(cg.build_parser().parse_args(["serve"]))
        assert exc_info.value.code == 2
        assert "not under cgprofile.slice" in capsys.readouterr().err
