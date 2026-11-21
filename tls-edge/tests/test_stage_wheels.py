from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stage-wheels.py"
SPEC = importlib.util.spec_from_file_location("tls_edge_stage_wheels", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
stage_wheels = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage_wheels)


def test_download_command_applies_estate_age_cutoff_by_default(tmp_path: Path) -> None:
    command = stage_wheels.download_command(["PyYAML>=6.0.1"], "cp313", tmp_path)

    cutoff_index = command.index("--uploaded-prior-to")
    assert command[cutoff_index + 1] == f"P{stage_wheels.MIN_AGE_DAYS}D"


def test_allow_fresh_omits_resolver_cutoff(tmp_path: Path) -> None:
    command = stage_wheels.download_command(
        ["PyYAML>=6.0.1"], "cp313", tmp_path, allow_fresh=True
    )

    assert "--uploaded-prior-to" not in command


def test_old_pip_is_rejected_by_cutoff_preflight(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        stage_wheels.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="pip download help without upload cutoff", stderr=""
        ),
    )

    with pytest.raises(SystemExit) as error:
        stage_wheels.require_upload_cutoff_support()

    assert error.value.code == 1
    assert "selected pip must support" in capsys.readouterr().err
