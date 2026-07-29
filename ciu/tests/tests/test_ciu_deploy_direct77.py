"""Inspection-list and bootstrap-action outcome contracts."""

from __future__ import annotations

from pathlib import Path

from ciu import deploy


def test_list_phases_returns_configuration_error_for_invalid_phase_table(monkeypatch, capsys):
    """Invalid phase declarations stay a typed configuration failure."""

    monkeypatch.setattr(
        deploy.phases_pkg,
        "ordered_phases",
        lambda _phases: (_ for _ in ()).throw(ValueError("[S7.1] bad phase")),
    )

    assert deploy.action_list_phases({"deploy": {"phases": {}}}) == 2
    assert "[S7.1] bad phase" in capsys.readouterr().out


def test_list_phases_and_profiles_empty_tables_explain_no_configured_work(capsys):
    """Empty inspection tables are successful and give operators useful guidance."""

    assert deploy.action_list_phases({"deploy": {"phases": {}}}) == 0
    assert deploy.action_list_profiles({"deploy": {"profiles": {}}}) == 0

    output = capsys.readouterr().out
    assert "(none defined)" in output
    assert "default profile deploys all phases" in output


def test_generate_env_resolves_root_then_announces_created_path(monkeypatch, tmp_path: Path, capsys):
    """The bootstrap action wires resolver output directly into env initialization."""

    root = tmp_path / "workspace"
    env_path = root / "ciu.env"
    calls: list[tuple[Path, Path | None]] = []

    def resolve(*, start_dir, define_root, defaults_filename):
        calls.append((start_dir, define_root))
        assert defaults_filename == deploy.GLOBAL_CONFIG_DEFAULTS
        return root

    monkeypatch.setattr(deploy, "resolve_env_root", resolve)
    monkeypatch.setattr(deploy, "bootstrap_env_init", lambda value: (env_path if value == root else None))

    assert deploy.action_generate_env(tmp_path / "defined", tmp_path / "hint") == 0
    assert calls == [(tmp_path / "hint", tmp_path / "defined")]
    assert f"Generated {env_path}" in capsys.readouterr().out
