"""Focused provisioning probe contracts for CIU Luna-medium package 39."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402


def test_probe_ref_unknown_kind_is_an_unsatisfied_probe_result(tmp_path):
    result = provisioning.probe_ref("bogus:thing", {}, tmp_path)

    assert isinstance(result, provisioning.ProbeResult)
    assert result.satisfied is False
    assert "Unknown ref kind 'bogus'" in result.reason


def test_probe_ref_stack_accepts_clean_one_shot_without_healthcheck(monkeypatch, tmp_path):
    from ciu import procutil

    monkeypatch.setattr(
        procutil,
        "docker",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"Running": false, "ExitCode": 0, "Health": {}}',
        ),
    )

    result = provisioning.probe_ref("stack:db:healthy", {}, tmp_path)

    assert isinstance(result, provisioning.ProbeResult)
    assert result.satisfied is True
    assert "one-shot" in result.reason


def test_probe_ref_stack_malformed_inspect_json_is_typed_unsatisfied(monkeypatch, tmp_path):
    from ciu import procutil

    monkeypatch.setattr(
        procutil,
        "docker",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="{not-json"),
    )

    result = provisioning.probe_ref("stack:db:healthy", {}, tmp_path)

    assert isinstance(result, provisioning.ProbeResult)
    assert result.satisfied is False
    assert result.reason == "Could not parse container state for 'db'"
