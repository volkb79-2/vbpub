"""CIU-70 — `pg:`/`minio:` probes resolve their container from the stack that
`provides` the ref, and report WHY a probe failed honestly.

Before ciu-P40 both probes hardcoded the literal service keys ``postgres`` /
``minio`` (``container_name(config, 'postgres')``), which nothing in SPEC S13.2
or CONFIG.md ever required a consumer to use.  A deployment whose Postgres
service is keyed anything else — ``pg``, ``db``, ``postgres_primary`` — was
therefore probed in a container that does not exist, and the answer came back
worded exactly like a genuinely missing role.

The controlled wrong implementation from the backlog entry is
:func:`test_pg_role_probe_targets_the_container_of_the_providing_stack`: a
Postgres service keyed ``pg`` whose stack ``provides`` ``pg:role/api``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, provisioning  # noqa: E402


# --- the consumer shape CIU-70 is about: a Postgres service NOT keyed 'postgres'
_CONFIG = {
    "deploy": {
        "project_name": "dstdns",
        "environment_tag": "dev",
        "profiles": {"default": {"stacks": ["infra/pg", "infra/objstore"]}},
    }
}
_GRAPH = {
    "infra/pg": {"requires": [], "provides": ["pg:role/api", "pg:db/app", "pg:schema/api"]},
    "infra/objstore": {"requires": [], "provides": ["minio:user/worker"]},
    "apps/api": {"requires": ["pg:role/api", "minio:user/worker"], "provides": []},
}


def _recording_exec(rc, stdout=""):
    seen: list[tuple] = []

    def _exec(container, cmd):
        seen.append((container, cmd))
        return (rc, stdout)

    return seen, _exec


# ---------------------------------------------------------------------------
# 1. The container comes from the PROVIDER, not from a literal service key
# ---------------------------------------------------------------------------


def test_pg_role_probe_targets_the_container_of_the_providing_stack():
    """The CIU-70 reproduction: the Postgres service is keyed `pg`, not
    `postgres`, and the role genuinely exists.

    Pre-fix this probed `dstdns-dev-postgres` (which does not exist) and
    reported `pg role 'api' not found (rc=1)` — indistinguishable in wording
    from a genuinely missing role.  Post-fix it probes `dstdns-dev-pg`, the
    container of the stack whose `provides` carries `pg:role/api`, and sees
    the role.
    """
    seen, _exec = _recording_exec(0, "1\n")

    result = provisioning.probe_ref(
        "pg:role/api", _CONFIG, Path("/tmp"),
        docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is True
    assert result.reason == "pg role 'api' exists"
    assert [c for c, _ in seen] == ["dstdns-dev-pg"]
    assert "dstdns-dev-postgres" not in [c for c, _ in seen]


def test_minio_user_probe_targets_the_container_of_the_providing_stack():
    """Same for `minio:`: the object store is keyed `objstore`, not `minio`."""
    seen, _exec = _recording_exec(0, "AccessKey: worker\n")

    result = provisioning.probe_ref(
        "minio:user/worker", _CONFIG, Path("/tmp"),
        docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is True
    assert [c for c, _ in seen] == ["dstdns-dev-objstore"]


def test_two_postgres_stacks_are_probed_in_their_own_containers():
    """The CIU-66 shape: more than one Postgres service in one deployment.

    Each ref is probed in the container of ITS OWN provider — the literal
    `postgres` key could only ever name one of them.
    """
    config = {
        "deploy": {
            "project_name": "dstdns",
            "environment_tag": "dev",
            "profiles": {"default": {"stacks": ["db-core/pg", "skywalking/oap-db"]}},
        }
    }
    graph = {
        "db-core/pg": {"provides": ["pg:role/api"]},
        "skywalking/oap-db": {"provides": ["pg:role/sw"]},
    }
    seen, _exec = _recording_exec(0, "1\n")

    provisioning.probe_ref("pg:role/api", config, Path("/tmp"),
                           docker_exec_fn=_exec, stacks=graph)
    provisioning.probe_ref("pg:role/sw", config, Path("/tmp"),
                           docker_exec_fn=_exec, stacks=graph)

    assert [c for c, _ in seen] == ["dstdns-dev-pg", "dstdns-dev-oap-db"]


# ---------------------------------------------------------------------------
# 2. Unresolvable targets fail with their OWN reason, never a literal guess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ["pg:role/nobody", "minio:user/nobody"])
def test_a_ref_no_stack_provides_says_so(ref):
    """"Nothing declares this" is its own fact, not "the role is missing"."""
    seen, _exec = _recording_exec(0, "1\n")

    result = provisioning.probe_ref(
        ref, _CONFIG, Path("/tmp"), docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == f"no stack provides '{ref}' — cannot resolve a container to probe"
    assert seen == []  # nothing was executed against a guessed container


@pytest.mark.parametrize("ref", ["pg:role/api", "minio:user/worker"])
def test_no_graph_at_all_is_reported_as_indeterminacy_not_as_absence(ref):
    """A probe handed no requires/provides graph refuses rather than falling
    back to the literal `postgres`/`minio` key CIU-70 exists to remove."""
    seen, _exec = _recording_exec(0, "1\n")

    result = provisioning.probe_ref(
        ref, _CONFIG, Path("/tmp"), docker_exec_fn=_exec,
    )

    assert result.satisfied is False
    assert result.reason == (
        f"cannot resolve a container for '{ref}': the probe was given no "
        "requires/provides graph"
    )
    assert seen == []


def test_providers_resolving_to_different_containers_are_refused_not_guessed():
    config = {
        "deploy": {
            "project_name": "dstdns",
            "environment_tag": "dev",
            "profiles": {"default": {"stacks": ["a/pg-one", "b/pg-two"]}},
        }
    }
    graph = {
        "a/pg-one": {"provides": ["pg:role/api"]},
        "b/pg-two": {"provides": ["pg:role/api"]},
    }
    seen, _exec = _recording_exec(0, "1\n")

    result = provisioning.probe_ref(
        "pg:role/api", config, Path("/tmp"), docker_exec_fn=_exec, stacks=graph,
    )

    assert result.satisfied is False
    assert result.reason == (
        "'pg:role/api' is provided by 2 stacks (a/pg-one, b/pg-two) resolving "
        "to different containers (dstdns-dev-pg-one, dstdns-dev-pg-two) — "
        "cannot choose one"
    )
    assert seen == []


def test_providers_resolving_to_one_container_are_not_ambiguous():
    """Two declared paths sharing a final segment collapse onto ONE container
    name (CIU-66's separate defect).  That is not an ambiguity *for this
    probe* — there is exactly one container to talk to — so it is probed."""
    config = {
        "deploy": {
            "project_name": "dstdns",
            "environment_tag": "dev",
            "profiles": {"default": {"stacks": ["a/pg", "b/pg"]}},
        }
    }
    graph = {"a/pg": {"provides": ["pg:db/app"]}, "b/pg": {"provides": ["pg:db/app"]}}
    seen, _exec = _recording_exec(0, "1\n")

    result = provisioning.probe_ref(
        "pg:db/app", config, Path("/tmp"), docker_exec_fn=_exec, stacks=graph,
    )

    assert result.satisfied is True
    assert [c for c, _ in seen] == ["dstdns-dev-pg"]


# ---------------------------------------------------------------------------
# 3. "container absent" is not "role absent" (CIU-70 point 4)
# ---------------------------------------------------------------------------


def test_pg_role_absent_is_worded_as_a_query_that_ran():
    """`psql -tAc` exits 0 on a query that matched nothing — the ONLY status
    from which "it genuinely does not exist" honestly follows."""
    _seen, _exec = _recording_exec(0, "\n")

    result = provisioning.probe_ref(
        "pg:role/api", _CONFIG, Path("/tmp"), docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == (
        "pg role 'api' does not exist (query ran in 'dstdns-dev-pg', "
        "no matching row)"
    )


def test_pg_container_absent_says_the_role_was_not_checked():
    _seen, _exec = _recording_exec(
        1, "Error: No such container: dstdns-dev-pg\n"
    )

    result = provisioning.probe_ref(
        "pg:role/api", _CONFIG, Path("/tmp"), docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == (
        "container 'dstdns-dev-pg' unavailable (no such container) — "
        "pg role 'api' was NOT checked"
    )
    # The two conditions must not be confusable in either direction.
    assert "does not exist" not in result.reason


def test_pg_container_stopped_says_the_role_was_not_checked():
    _seen, _exec = _recording_exec(
        1, "Error response from daemon: Container dstdns-dev-pg is not running\n"
    )

    result = provisioning.probe_ref(
        "pg:db/app", _CONFIG, Path("/tmp"), docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == (
        "container 'dstdns-dev-pg' unavailable (container is not running) — "
        "pg db 'app' was NOT checked"
    )


def test_pg_psql_failure_is_indeterminate_not_absence():
    """A non-zero psql that docker DID run (bad password, server starting,
    connection refused) is "could not check", never "does not exist"."""
    _seen, _exec = _recording_exec(2, "psql: error: connection to server failed\n")

    result = provisioning.probe_ref(
        "pg:role/api", _CONFIG, Path("/tmp"), docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == (
        "pg role 'api' could not be checked: psql in 'dstdns-dev-pg' exited rc=2"
    )


def test_minio_container_absent_says_the_user_was_not_checked():
    _seen, _exec = _recording_exec(1, "Error: No such container: dstdns-dev-objstore\n")

    result = provisioning.probe_ref(
        "minio:user/worker", _CONFIG, Path("/tmp"),
        docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == (
        "container 'dstdns-dev-objstore' unavailable (no such container) — "
        "MinIO user 'worker' was NOT checked"
    )


def test_minio_user_absent_keeps_its_own_wording():
    """`mc admin user info` answers with a non-zero status, so mc's own
    verdict — unlike psql's — is the rc, and its wording is unchanged."""
    _seen, _exec = _recording_exec(1, "mc: <ERROR> Unable to get user info.\n")

    result = provisioning.probe_ref(
        "minio:user/worker", _CONFIG, Path("/tmp"),
        docker_exec_fn=_exec, stacks=_GRAPH,
    )

    assert result.satisfied is False
    assert result.reason == "MinIO user 'worker' not found (rc=1)"


def test_the_real_docker_path_reads_stderr_for_the_container_verdict(monkeypatch):
    """`docker exec` writes `No such container` to STDERR, so the
    classification must read it — the injected seam only ever sees stdout."""
    from ciu import procutil

    monkeypatch.setattr(
        procutil, "docker",
        lambda argv, **kw: SimpleNamespace(
            returncode=1, stdout="",
            stderr="Error response from daemon: No such container: dstdns-dev-pg\n",
        ),
    )

    result = provisioning.probe_ref("pg:role/api", _CONFIG, Path("/tmp"), stacks=_GRAPH)

    assert result.satisfied is False
    assert "no such container" in result.reason
    assert "was NOT checked" in result.reason


# ---------------------------------------------------------------------------
# 4. The graph handed to the probe spans the whole run, not one phase
# ---------------------------------------------------------------------------


def test_provisioning_graph_covers_every_rendered_stack():
    graph = deploy.provisioning_graph({
        "infra/pg": {"pg": {"provides": ["pg:role/api"]}},
        "apps/api": {"api": {"requires": ["pg:role/api"]}},
        "apps/plain": {"plain": {}},                      # no requires/provides
    })

    assert graph == {
        "infra/pg": {"requires": [], "provides": ["pg:role/api"], "provides_container": {}},
        "apps/api": {"requires": ["pg:role/api"], "provides": [], "provides_container": {}},
    }


def test_provisioning_graph_skips_a_stack_whose_shape_is_invalid():
    """An unrelated malformed stack cannot contribute a provider anyway, and
    must not turn into a NEW per-phase exception (the up-front lint pass has
    already failed the run over it)."""
    graph = deploy.provisioning_graph({
        "infra/pg": {"pg": {"provides": ["pg:role/api"]}},
        "apps/two-roots": {"a": {}, "b": {}},             # S3.5 violation
    })

    assert list(graph) == ["infra/pg"]


def test_per_phase_preflight_resolves_a_provider_from_an_earlier_phase(monkeypatch):
    """The regression this design exists to avoid: live probing runs PER PHASE
    with only that phase's entries as `selection`, while the stack that
    `provides` the ref is by construction in an EARLIER phase.  A graph scoped
    to `selection` would report every cross-phase ref as "no stack provides
    it"; the graph must come from the full `rendered` map."""
    from ciu import procutil

    argvs: list[list[str]] = []

    def _docker(argv, **kw):
        argvs.append(argv)
        return SimpleNamespace(returncode=0, stdout="1\n", stderr="")

    monkeypatch.setattr(procutil, "docker", _docker)

    rendered = {
        "infra/pg": {"pg": {"provides": ["pg:role/api"]}},
        "apps/api": {"api": {"requires": ["pg:role/api"]}},
    }
    profile = SimpleNamespace(name="default", config=_CONFIG)

    # `selection` is ONE phase's entries — the provider stack is not in it.
    deploy.provisioning_preflight(
        Path("/tmp"), profile,
        [{"path": "apps/api", "service": {"enabled": True}}],
        rendered, lint=False, probe=True,
    )

    assert [argv[1] for argv in argvs] == ["dstdns-dev-pg"]


# ---------------------------------------------------------------------------
# 5. CIU-89 — multi-service stacks: `provides_container` override
#
# The gap CIU-70's OWN resolution strategy reintroduced: a stack directory
# whose basename is NOT itself a compose service key (e.g. `infra/db-core`
# running Postgres in a service keyed `postgres`, alongside five siblings).
# `_stack_container_name`'s basename guess resolves `db-core`, which is never
# a real container. `provides_container` is the escape hatch — see
# `provisioning.py::_resolve_probe_container` and `config_model.py::
# validate_stack_provisioning` (S13.2).
# ---------------------------------------------------------------------------


_CIU89_MULTI_SERVICE_CONFIG = {
    "deploy": {
        "project_name": "dstdns",
        "environment_tag": "dev",
        "profiles": {"default": {"stacks": ["infra/foo-core"]}},
    }
}


def test_ciu89_multi_service_stack_without_override_is_still_wrong():
    """The controlled wrong implementation from the CIU-89 backlog entry: a
    stack dir `infra/foo-core` provides `pg:db/bar` at the stack level, but
    its compose file keys the Postgres service `postgres`, not `foo-core`.
    Without provides_container this still resolves to the WRONG container
    (today's unchanged behavior) — pinned here as a regression guard, the
    same way the CIU-70 fixture above pins its own before-state."""
    seen, _exec = _recording_exec(0, "1\n")
    graph = {"infra/foo-core": {"provides": ["pg:db/bar"]}}

    result = provisioning.probe_ref(
        "pg:db/bar", _CIU89_MULTI_SERVICE_CONFIG, Path("/tmp"),
        docker_exec_fn=_exec, stacks=graph,
    )

    assert result.satisfied is True  # the exec fake answers 0 regardless of target
    assert [c for c, _ in seen] == ["dstdns-dev-foo-core"]  # WRONG — no such container in reality


def test_ciu89_provides_container_override_resolves_the_real_service():
    """Add `provides_container = {"pg:db/bar": "postgres"}` to the same
    fixture stack: resolution now targets the REAL service key."""
    seen, _exec = _recording_exec(0, "1\n")
    graph = {
        "infra/foo-core": {
            "provides": ["pg:db/bar"],
            "provides_container": {"pg:db/bar": "postgres"},
        }
    }

    result = provisioning.probe_ref(
        "pg:db/bar", _CIU89_MULTI_SERVICE_CONFIG, Path("/tmp"),
        docker_exec_fn=_exec, stacks=graph,
    )

    assert result.satisfied is True
    assert [c for c, _ in seen] == ["dstdns-dev-postgres"]


def test_ciu89_config_model_rejects_an_override_for_an_undeclared_ref():
    """`provides_container` for a ref the stack doesn't even `provide` is a
    config error (S13.2) — never silently accepted, never silently ignored."""
    from ciu import config_model

    stack_cfg = {
        "foo_core": {
            "provides": ["pg:db/bar"],
            "provides_container": {"pg:db/nonexistent": "postgres"},
        }
    }
    with pytest.raises(ValueError, match=r"\[S13\.2\]"):
        config_model.validate_stack_provisioning(stack_cfg, source="infra/foo-core")


def test_ciu89_real_toml_round_trip_through_validation_graph_and_resolution():
    """Permanent regression pin (adversarial ciu-P49 review, "should-fix"):
    an actual `tomllib`-parsed `ciu.toml`-shaped document, run through the
    REAL production chain end to end — `validate_stack_provisioning` ->
    `deploy.provisioning_graph` -> `_resolve_probe_container` — not the
    hand-built dict fixtures the other CIU-89 tests use for speed and to
    avoid CIU-91's own test-repo/-sharing race (deliberately NOT using
    test-repo/ here either, for the same reason).

    Mirrors the real dstdns db-core shape: `infra/db-core`'s directory
    basename ("db-core") is not itself a compose service key -- Postgres is
    keyed `postgres` -- while a SIBLING ref in the same `provides` list has
    no override and must still resolve via the basename guess, unaffected."""
    import tomllib

    from ciu import config_model

    toml_text = '''
[db_core]
provides = ["pg:db/dstdns", "pg:role/controller"]
provides_container = { "pg:db/dstdns" = "postgres" }
'''
    stack_cfg = tomllib.loads(toml_text)

    # Step 1: the real validator, unmocked -- must pass cleanly.
    config_model.validate_stack_provisioning(stack_cfg, source="infra/db-core")

    # Step 2: the real graph builder deploy.provisioning_graph consumes at
    # `ciu up`, from a `rendered` map shaped exactly like the real pipeline
    # hands it (repo-relative path -> the parsed stack TOML).
    rendered = {"infra/db-core": stack_cfg}
    graph = deploy.provisioning_graph(rendered)
    assert graph["infra/db-core"]["provides_container"] == {"pg:db/dstdns": "postgres"}

    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "profiles": {"default": {"stacks": ["infra/db-core"]}},
        }
    }

    # Step 3: the real resolver. Overridden ref -> the literal service key.
    cname, unresolved = provisioning._resolve_probe_container(
        "pg:db/dstdns", config, graph
    )
    assert unresolved is None
    assert cname == "p-t-postgres"

    # Un-overridden sibling in the SAME provides list -> unaffected, still
    # the basename guess (declared path's final segment).
    cname, unresolved = provisioning._resolve_probe_container(
        "pg:role/controller", config, graph
    )
    assert unresolved is None
    assert cname == "p-t-db-core"
