from __future__ import annotations

import pytest

from topos.collect.dockerjoin import ContainerResolveError, enrich_entities, resolve_container_key
from topos.model import DockerMeta, Entity

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


def _make_enriched(
    name: str, cid_hex: str = "a", key_suffix: str | None = None
) -> tuple[str, Entity]:
    """Build a docker-enriched (EntityKey, Entity) pair with the given name and cid suffix."""
    fs = key_suffix or cid_hex
    # Only use first char repeated 64 times for the key to match DOCKER_SCOPE_RE
    key_hex = fs[0] * 64
    key = f"system.slice/docker-{key_hex}.scope"
    entity = Entity(key=key, kind="scope", parent="system.slice")
    entity.docker = DockerMeta(
        cid=cid_hex[:12],
        full_id=key_hex,
        name=name,
        image="test:latest",
    )
    return key, entity


class TestResolveContainerKey:
    """Tests for resolve_container_key()."""

    def test_exact_name_match(self) -> None:
        key_a, ent_a = _make_enriched("my-container", "a")
        key_b, ent_b = _make_enriched("other-container", "b")
        entities = {key_a: ent_a, key_b: ent_b}
        assert resolve_container_key("my-container", entities) == key_a

    def test_unambiguous_prefix_match(self) -> None:
        key, ent = _make_enriched("my-container", "a")
        entities = {key: ent}
        assert resolve_container_key("my-cont", entities) == key

    def test_exact_match_beats_prefix(self) -> None:
        """Exact name wins when both exact and prefix matches exist."""
        key_exact, ent_exact = _make_enriched("game", "a")
        key_prefix, ent_prefix = _make_enriched("game-server", "b")
        entities = {key_exact: ent_exact, key_prefix: ent_prefix}
        assert resolve_container_key("game", entities) == key_exact

    def test_prefix_cid_match(self) -> None:
        """Prefix matching also works on DockerMeta.cid (short container ID)."""
        # Use a longer cid_hex so cid becomes 12 chars that can be prefix-matched
        key, ent = _make_enriched("my-container", "aabbccddeeff")
        entities = {key: ent}
        # cid = "aabbccddeeff", prefix match on first 6 chars
        assert resolve_container_key("aabbcc", entities) == key

    def test_ambiguous_prefix_raises(self) -> None:
        key_a, ent_a = _make_enriched("game-1", "a")
        key_b, ent_b = _make_enriched("game-2", "b")
        entities = {key_a: ent_a, key_b: ent_b}
        with pytest.raises(ContainerResolveError) as excinfo:
            resolve_container_key("game", entities)
        assert "ambiguous" in str(excinfo.value).lower()
        assert excinfo.value.candidates is not None
        assert "game-1" in excinfo.value.candidates
        assert "game-2" in excinfo.value.candidates

    def test_zero_match_raises(self) -> None:
        key, ent = _make_enriched("my-container", "a")
        entities = {key: ent}
        with pytest.raises(ContainerResolveError) as excinfo:
            resolve_container_key("nonexistent", entities)
        assert "no running container" in str(excinfo.value).lower()

    def test_non_docker_entity_skipped(self) -> None:
        """Entities without docker metadata or not matching DOCKER_SCOPE_RE are skipped."""
        non_docker_key = "system.slice/some.service"
        non_docker_ent = Entity(key=non_docker_key, kind="service", parent="system.slice")
        docker_key, docker_ent = _make_enriched("my-container", "a")
        entities = {non_docker_key: non_docker_ent, docker_key: docker_ent}
        assert resolve_container_key("my-container", entities) == docker_key
