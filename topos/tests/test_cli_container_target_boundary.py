"""Behavioural contracts for action/squeeze container target resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import topos.cli as cli


def test_container_target_resolution_collects_enriched_entities_before_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    entity_a = object()
    entity_b = object()
    frame = SimpleNamespace(entities={"/cg/a": SimpleNamespace(entity=entity_a), "/cg/b": SimpleNamespace(entity=entity_b)})
    calls: list[object] = []

    class Collector:
        def __init__(self, *, config: object) -> None:
            calls.append(("collector", config))

        def collect_once(self) -> object:
            calls.append("collect")
            return frame

    monkeypatch.setattr(cli, "load", lambda value: calls.append(("load", value)) or config)
    monkeypatch.setattr(cli, "Collector", Collector)
    monkeypatch.setattr(
        cli,
        "resolve_container_key",
        lambda name, entities: calls.append(("resolve", name, entities)) or "/cg/b",
    )

    assert cli._resolve_container_target("api-prefix") == "/cg/b"
    assert calls == [
        ("load", None),
        ("collector", config),
        "collect",
        ("resolve", "api-prefix", {"/cg/a": entity_a, "/cg/b": entity_b}),
    ]


def test_target_container_choice_preserves_direct_target_and_rejects_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cli._resolve_mutual_exclusive_target("/cg/direct", None, "action") == "/cg/direct"

    monkeypatch.setattr(cli, "_resolve_container_target", lambda value: f"/resolved/{value}")
    assert cli._resolve_mutual_exclusive_target(None, "api", "action") == "/resolved/api"

    with pytest.raises(ValueError, match="choose either --target or --container for action"):
        cli._resolve_mutual_exclusive_target("/cg/direct", "api", "action")
    with pytest.raises(ValueError, match="action requires either --target or --container"):
        cli._resolve_mutual_exclusive_target(None, None, "action")
