"""Sol B4 -- `cgprofile serve` refuses to run under an unbounded
`cgprofile.slice`. Kept apart from test_cgprofile.py, which needs the report
tier's pandas/plotly stack; this check is collector-tier only."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cgprofile as cg  # noqa: E402
from lib import access  # noqa: E402
from lib import serve as serve_lib  # noqa: E402


class TestServeRefusesAnUnboundedDaemonSlice:
    @pytest.mark.parametrize("memory_max", ["max\n", "0\n", None, "garbage\n"])
    def test_refuses_an_unbounded_or_unprovable_daemon_slice(
        self, monkeypatch, tmp_path, capsys, memory_max,
    ):
        # Sol B4: an implicitly auto-created cgprofile.slice reads back
        # memory.max=max. The daemon must refuse, never serve unbounded.
        root = tmp_path / "cgroup"
        slice_dir = root / "cgprofile.slice"
        slice_dir.mkdir(parents=True)
        if memory_max is not None:
            (slice_dir / "memory.max").write_text(memory_max)
        monkeypatch.setattr(access, "CGROUP_ROOT", str(root))
        monkeypatch.setattr(access, "have_host_cgroup_view", lambda r: True)
        monkeypatch.setenv(access.PID_NAMESPACE_MODE_ENV, "host")
        monkeypatch.setattr(access, "have_host_proc_view", lambda proc: True)
        monkeypatch.setattr(
            serve_lib, "SessionServer",
            lambda **kwargs: pytest.fail("must not construct a server"),
        )
        with pytest.raises(SystemExit) as exc_info:
            cg.cmd_serve(cg.build_parser().parse_args(["serve"]))
        assert exc_info.value.code == 2
        err = capsys.readouterr().err
        assert "cgprofile.slice is not provably bounded" in err
        assert "infra/cgprofile.slice" in err

    def test_serves_when_the_daemon_slice_is_bounded(self, monkeypatch, tmp_path):
        root = tmp_path / "cgroup"
        (root / "cgprofile.slice").mkdir(parents=True)
        (root / "cgprofile.slice" / "memory.max").write_text("1073741824\n")
        assert serve_lib.daemon_slice_bounded(str(root)) is True
        assert serve_lib.daemon_slice_bounded(str(tmp_path / "missing")) is False
