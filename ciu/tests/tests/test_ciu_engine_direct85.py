"""DooD no-context and reset whitespace-list contracts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine


def _write_identity_facts(root):
    """CIU-75: reset's config-less down path derives its compose project from
    the checkout's generated `[ciu.instance.generated]` overlay table, not its
    legacy `ciu.env` export."""
    from ciu.workspace_env import GENERATED_FACTS_KEYS, upsert_generated_facts

    facts = {key: "" for key in GENERATED_FACTS_KEYS}
    facts["repo_name"] = "dstdns"
    facts["instance_id"] = "abc123"
    upsert_generated_facts(root, facts)



def test_dood_preflight_without_repository_context_is_a_noop(monkeypatch, tmp_path: Path):
    """Native/unknown execution contexts must not issue an invented Docker probe."""

    monkeypatch.delenv("CIU_SKIP_DOOD_PREFLIGHT", raising=False)
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    monkeypatch.setattr(
        engine.procutil,
        "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("no context must not probe Docker")),
    )

    engine._dood_preflight(tmp_path / "stack")


def test_reset_treats_whitespace_only_orphan_listing_as_empty(monkeypatch, tmp_path: Path, capsys):
    """Docker output with no actual names must not trigger a meaningless remove call."""

    _write_identity_facts(tmp_path)
    results = iter([
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout=" \n\t\n", stderr=""),
    ])
    monkeypatch.setattr(engine.procutil, "run_cmd", lambda *_args, **_kwargs: next(results))

    engine.reset_service(
        {"deploy": {"project_name": "project", "labels": {"prefix": "ciu"}}},
        tmp_path,
        assume_yes=True,
        repo_root=tmp_path,
    )

    assert "No orphaned containers found" in capsys.readouterr().out
