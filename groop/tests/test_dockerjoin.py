from __future__ import annotations

from groop.collect.dockerjoin import enrich_entities
from groop.model import Entity

FULL_ID = "a" * 64


def test_docker_metadata_join_with_injected_inspect() -> None:
    key = f"system.slice/docker-{FULL_ID}.scope"
    entities = {key: Entity(key=key, kind="scope", parent="system.slice")}

    def inspect(cid: str) -> list[dict]:
        assert cid == FULL_ID
        return [{"Id": FULL_ID, "Name": "/123e4567-e89b-12d3-a456-426614174000", "Config": {"Image": "ghcr.io/example/game:latest", "Labels": {"com.docker.compose.project": "wings"}}}]

    meta = enrich_entities(entities, inspect)[key].docker
    assert meta is not None
    assert meta.cid == "a" * 12
    assert meta.image == "ghcr.io/example/game:latest"
    assert meta.compose_project == "wings"
    assert meta.ptero_uuid == "123e4567-e89b-12d3-a456-426614174000"
