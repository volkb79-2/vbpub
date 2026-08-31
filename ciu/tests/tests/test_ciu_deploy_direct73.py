"""Contracts for inspection actions with partially rendered selections."""

from __future__ import annotations

from pathlib import Path

from ciu import deploy, provisioning
from ciu.deploy_pkg.profiles import Profile
from ciu.provisioning import ProbeResult


def test_check_ignores_unrendered_selection_and_passes_no_refs(monkeypatch, tmp_path: Path, capsys):
    """`ciu check` must not lint or probe a stack absent from rendered inputs."""

    monkeypatch.setattr(
        provisioning,
        "lint_graph",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not lint no stacks")),
    )
    monkeypatch.setattr(
        provisioning,
        "probe_ref",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not probe no stacks")),
    )

    assert deploy.action_check(
        tmp_path, Profile(), [{"path": "apps/not-rendered"}], {}, live=True
    ) == 0
    output = capsys.readouterr().out
    assert "No stacks with requires/provides" in output
    assert "check passed" in output


def test_check_live_probes_only_rendered_stack_requirements(monkeypatch, tmp_path: Path):
    """A live check skips unrendered entries while probing real required refs."""

    probed: list[str] = []
    monkeypatch.setattr(provisioning, "lint_graph", lambda _stacks: [])
    monkeypatch.setattr(
        provisioning,
        "probe_ref",
        lambda ref, *_args, **_kw: (probed.append(ref) or ProbeResult(ref, True, "ready")),
    )
    rendered = {"apps/api": {"api": {"requires": ["pg:db/main"], "provides": []}}}

    assert deploy.action_check(
        tmp_path,
        Profile(),
        [{"path": "apps/not-rendered"}, {"path": "apps/api"}],
        rendered,
        live=True,
    ) == 0
    assert probed == ["pg:db/main"]


def test_graph_ignores_unrendered_selection_and_does_not_render(monkeypatch, tmp_path: Path, capsys):
    """`ciu graph` has no topology to emit when every selected stack is absent."""

    monkeypatch.setattr(
        provisioning,
        "render_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not render no graph")),
    )

    assert deploy.action_graph(
        tmp_path, Profile(), [{"path": "apps/not-rendered"}], {}, fmt="json"
    ) == 0
    assert "No stacks with requires/provides — nothing to graph" in capsys.readouterr().out
