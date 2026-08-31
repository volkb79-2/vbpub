"""CIU provisioning tests — requires/provides declarative dependency graph.

Covers:
  - parse_ref: valid and invalid ref strings
  - lint_graph: missing provider detection and cycle detection
  - probe_ref: injected docker_exec_fn and vault_client for unit testing
  - config_model integration: validate_provisioning_ref, validate_stack_provisioning
  - deploy integration: provisioning_preflight, action_check
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import provisioning  # noqa: E402
from ciu.provisioning import (  # noqa: E402
    ProvisioningRef,
    ProbeResult,
    parse_ref,
    lint_graph,
    probe_ref,
)
from ciu import config_model  # noqa: E402
from ciu.config_model import (  # noqa: E402
    validate_provisioning_ref,
    validate_stack_provisioning,
)


# ---------------------------------------------------------------------------
# parse_ref — valid cases
# ---------------------------------------------------------------------------


def test_parse_ref_vault_simple_path():
    ref = parse_ref("vault:secret/db/password")
    assert ref.kind == "vault"
    assert ref.subkind == "secret"
    assert ref.selector == "db/password"


def test_parse_ref_vault_single_segment():
    ref = parse_ref("vault:secret/mykey")
    assert ref.kind == "vault"
    assert ref.selector == "mykey"


def test_parse_ref_pg_role():
    ref = parse_ref("pg:role/myuser")
    assert ref.kind == "pg"
    assert ref.subkind == "role"
    assert ref.selector == "myuser"


def test_parse_ref_pg_db():
    ref = parse_ref("pg:db/mydb")
    assert ref.kind == "pg"
    assert ref.subkind == "db"
    assert ref.selector == "mydb"


def test_parse_ref_minio_user():
    ref = parse_ref("minio:user/worker")
    assert ref.kind == "minio"
    assert ref.subkind == "user"
    assert ref.selector == "worker"


def test_parse_ref_consul_token():
    ref = parse_ref("consul:token/myapp")
    assert ref.kind == "consul"
    assert ref.subkind == "token"
    assert ref.selector == "myapp"


def test_parse_ref_stack_healthy():
    ref = parse_ref("stack:db-core:healthy")
    assert ref.kind == "stack"
    assert ref.subkind == ""
    assert ref.selector == "db-core"


def test_parse_ref_stack_with_slash():
    ref = parse_ref("stack:infra/postgres:healthy")
    assert ref.kind == "stack"
    assert ref.selector == "infra/postgres"


# ---------------------------------------------------------------------------
# parse_ref — :completed terminal (O1, V8-PREP-5)
# ---------------------------------------------------------------------------


def test_parse_ref_stack_completed():
    ref = parse_ref("stack:db-init:completed")
    assert ref.kind == "stack"
    assert ref.subkind == "completed"
    assert ref.selector == "db-init"


def test_parse_ref_stack_completed_with_slash():
    ref = parse_ref("stack:infra/db-init:completed")
    assert ref.kind == "stack"
    assert ref.subkind == "completed"
    assert ref.selector == "infra/db-init"


def test_parse_ref_stack_healthy_subkind_unchanged():
    # O1's additive contract: an existing ':healthy' ref's parsed subkind is
    # UNCHANGED by this package (still '', never 'healthy' or anything new).
    ref = parse_ref("stack:db-core:healthy")
    assert ref.subkind == ""


def test_parse_ref_stack_bad_terminal_raises():
    import pytest
    with pytest.raises(ValueError, match="does not match any valid pattern"):
        parse_ref("stack:db-core:done")


# ---------------------------------------------------------------------------
# parse_ref — error cases
# ---------------------------------------------------------------------------


def test_parse_ref_missing_colon_raises():
    import pytest
    with pytest.raises(ValueError, match="missing kind prefix"):
        parse_ref("vaultsecret/path")


def test_parse_ref_unknown_kind_raises():
    import pytest
    with pytest.raises(ValueError, match="Unknown ref kind"):
        parse_ref("s3:bucket/mybucket")


def test_parse_ref_known_kind_bad_format_raises():
    import pytest
    with pytest.raises(ValueError, match="does not match any valid pattern"):
        parse_ref("pg:badsubkind/name")


def test_parse_ref_stack_missing_healthy_suffix_raises():
    import pytest
    with pytest.raises(ValueError, match="does not match any valid pattern"):
        parse_ref("stack:db-core")


# ---------------------------------------------------------------------------
# lint_graph — missing provider
# ---------------------------------------------------------------------------


def test_lint_graph_passes_when_all_satisfied():
    stacks = {
        "infra/pg": {
            "provides": ["pg:db/mydb", "pg:role/myuser"],
            "requires": [],
        },
        "apps/backend": {
            "provides": [],
            "requires": ["pg:db/mydb", "pg:role/myuser"],
        },
    }
    errors = lint_graph(stacks)
    assert errors == []


def test_lint_graph_detects_missing_provider():
    stacks = {
        "apps/backend": {
            "provides": [],
            "requires": ["pg:db/mydb"],
        },
    }
    errors = lint_graph(stacks)
    assert len(errors) == 1
    assert "pg:db/mydb" in errors[0]
    assert "nobody provides it" in errors[0]


def test_lint_graph_detects_multiple_missing():
    stacks = {
        "apps/backend": {
            "provides": [],
            "requires": ["pg:db/mydb", "vault:secret/db/pass"],
        },
    }
    errors = lint_graph(stacks)
    assert len(errors) == 2


def test_lint_graph_passes_when_no_stacks():
    errors = lint_graph({})
    assert errors == []


def test_lint_graph_passes_when_stacks_have_no_requires_provides():
    stacks = {
        "infra/pg": {"requires": [], "provides": []},
        "apps/backend": {"requires": [], "provides": []},
    }
    errors = lint_graph(stacks)
    assert errors == []


# ---------------------------------------------------------------------------
# lint_graph — CIU-63: `stack:<path>:healthy|completed` refs are satisfied
# by the referenced stack resolving via `_resolve_declared_stack_path`, not
# by any `provides` declaration -- `_probe_stack` (the live probe) never
# reads one, and the cycle-detection pass below already resolves refs this
# same way, so the "every required ref is provided" pass must not demand a
# redundant self-declaration for this ref kind alone.
# ---------------------------------------------------------------------------


def test_lint_graph_stack_ref_satisfied_without_self_declared_provides():
    # infra/vault is a REAL declared stack but deliberately does NOT
    # self-declare provides = ["stack:infra/vault:healthy"] -- before the
    # CIU-63 fix this required entry would have errored "but nobody provides
    # it" even though _probe_stack would satisfy it live at deploy time.
    stacks = {
        "infra/vault": {
            "requires": [],
            "provides": [],
        },
        "apps/backend": {
            "requires": ["stack:infra/vault:healthy"],
            "provides": [],
        },
    }
    errors = lint_graph(stacks)
    assert errors == []


def test_lint_graph_stack_ref_completed_satisfied_without_self_declared_provides():
    # Same contract, :completed terminal (one-shot stacks, V8-PREP-5).
    stacks = {
        "infra/db-init": {
            "requires": [],
            "provides": [],
        },
        "apps/backend": {
            "requires": ["stack:infra/db-init:completed"],
            "provides": [],
        },
    }
    errors = lint_graph(stacks)
    assert errors == []


def test_lint_graph_stack_ref_bare_selector_satisfied_without_self_declared_provides():
    # The selector may also be a bare basename resolving to a full declared
    # path (same resolution rule the cycle-detection pass already uses) --
    # still satisfied with no self-declared provides.
    stacks = {
        "infra/vault": {
            "requires": [],
            "provides": [],
        },
        "apps/backend": {
            "requires": ["stack:vault:healthy"],
            "provides": [],
        },
    }
    errors = lint_graph(stacks)
    assert errors == []


def test_lint_graph_stack_ref_to_bogus_stack_still_errors():
    # Negative control (CIU-63): a stack:<path>:healthy ref that does NOT
    # resolve to any real declared stack must still error, unchanged.
    stacks = {
        "apps/backend": {
            "requires": ["stack:infra/does-not-exist:healthy"],
            "provides": [],
        },
    }
    errors = lint_graph(stacks)
    assert len(errors) == 1
    assert "stack:infra/does-not-exist:healthy" in errors[0]
    assert "nobody provides it" in errors[0]


def test_lint_graph_stack_ref_other_kinds_still_require_provides():
    # Regression bar: every OTHER ref kind keeps today's exact
    # provides-union check -- a real declared stack existing must NOT
    # satisfy a non-stack ref that nobody provides.
    stacks = {
        "infra/pg": {
            "requires": [],
            "provides": [],
        },
    }
    errors = lint_graph({**stacks, "apps/backend": {
        "requires": ["pg:db/mydb"],
        "provides": [],
    }})
    assert len(errors) == 1
    assert "pg:db/mydb" in errors[0]
    assert "nobody provides it" in errors[0]


# ---------------------------------------------------------------------------
# lint_graph — cycle detection
# ---------------------------------------------------------------------------


def test_lint_graph_detects_simple_cycle():
    stacks = {
        "infra/a": {
            "requires": ["stack:infra/b:healthy"],
            "provides": ["stack:infra/a:healthy"],
        },
        "infra/b": {
            "requires": ["stack:infra/a:healthy"],
            "provides": ["stack:infra/b:healthy"],
        },
    }
    errors = lint_graph(stacks)
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1


def test_lint_graph_no_cycle_linear():
    stacks = {
        "infra/a": {
            "requires": [],
            "provides": ["stack:infra/a:healthy"],
        },
        "infra/b": {
            "requires": ["stack:infra/a:healthy"],
            "provides": ["stack:infra/b:healthy"],
        },
        "apps/c": {
            "requires": ["stack:infra/b:healthy"],
            "provides": [],
        },
    }
    errors = lint_graph(stacks)
    assert errors == []


def test_lint_graph_three_node_cycle():
    stacks = {
        "a": {"requires": ["stack:b:healthy"], "provides": ["stack:a:healthy"]},
        "b": {"requires": ["stack:c:healthy"], "provides": ["stack:b:healthy"]},
        "c": {"requires": ["stack:a:healthy"], "provides": ["stack:c:healthy"]},
    }
    errors = lint_graph(stacks)
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1


def test_lint_graph_cycle_with_inbound_edge_does_not_crash():
    # Regression: a cycle (A<->B) PLUS a third stack C with an edge INTO the
    # cycle. The DFS used to leave A/B GRAY after early-returning the A<->B
    # cycle; C's later DFS then hit the stale-GRAY A and called
    # path.index(A) with A not on C's path -> ValueError. Must instead report
    # the cycle cleanly and never raise.
    stacks = {
        "A": {"requires": ["stack:B:healthy"], "provides": ["stack:A:healthy"]},
        "B": {"requires": ["stack:A:healthy"], "provides": ["stack:B:healthy"]},
        "C": {"requires": ["stack:A:healthy"], "provides": ["stack:C:healthy"]},
    }
    errors = lint_graph(stacks)  # must not raise
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1
    # C requires stack:A which IS provided, so no missing-provider error for C
    assert not any("nobody provides" in e and "'C'" in e for e in errors)


def test_lint_graph_two_disjoint_cycles_via_shared_root_no_crash():
    # A branches to two independent cycles (B<->D and C<->E). Regardless of the
    # (set-ordered) branch the DFS explores first, the other branch's nodes must
    # not be left stale-GRAY in a way that crashes a later DFS. At least one
    # cycle is always reported; the graph is correctly flagged cyclic.
    stacks = {
        "A": {"requires": ["stack:B:healthy", "stack:C:healthy"], "provides": ["stack:A:healthy"]},
        "B": {"requires": ["stack:D:healthy"], "provides": ["stack:B:healthy"]},
        "D": {"requires": ["stack:B:healthy"], "provides": ["stack:D:healthy"]},
        "C": {"requires": ["stack:E:healthy"], "provides": ["stack:C:healthy"]},
        "E": {"requires": ["stack:C:healthy"], "provides": ["stack:E:healthy"]},
    }
    errors = lint_graph(stacks)  # must not raise
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1


# ---------------------------------------------------------------------------
# lint_graph — O5 fix: bare-selector refs resolve to full stack-path keys
# ---------------------------------------------------------------------------


def test_lint_graph_detects_cycle_via_bare_selector_matching_full_path_keys():
    # Regression (O5, V8-PREP-5): `stacks` is keyed by full repo-relative
    # path (exactly how deploy.py's real callers key it), but the ONLY
    # selector form that has ever resolved to a real container is a bare
    # basename (a slash-bearing selector was, until O4, guaranteed-broken).
    # Before this fix, a bare-name ref never matched a full-path key, so a
    # genuine cross-stack cycle silently passed lint.
    stacks = {
        "infra/a": {
            "requires": ["stack:b:healthy"],  # bare -- must resolve to infra/b
            "provides": ["stack:a:healthy"],
        },
        "infra/b": {
            "requires": ["stack:a:healthy"],  # bare -- must resolve to infra/a
            "provides": ["stack:b:healthy"],
        },
    }
    errors = lint_graph(stacks)
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1


def test_lint_graph_bare_selector_via_completed_also_resolves():
    # The fix applies uniformly to :completed refs too (O1 extends _STACK_RE).
    stacks = {
        "infra/a": {
            "requires": ["stack:b:completed"],
            "provides": ["stack:a:completed"],
        },
        "infra/b": {
            "requires": ["stack:a:completed"],
            "provides": ["stack:b:completed"],
        },
    }
    errors = lint_graph(stacks)
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1


def test_lint_graph_ambiguous_bare_selector_stays_unresolved_no_false_cycle():
    # Two declared stacks share the same basename ('db-init') -- an
    # ambiguous bare selector must NOT be guessed at; the ref stays
    # unresolved (today's exact behavior: silently not walked), so no
    # phantom cycle is manufactured out of ambiguous data.
    stacks = {
        "infra/db-init": {"requires": [], "provides": ["stack:db-init:healthy"]},
        "apps/db-init": {"requires": ["stack:db-init:healthy"], "provides": []},
    }
    errors = lint_graph(stacks)
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert cycle_errors == []


def test_lint_graph_full_path_selector_still_works_unchanged():
    # A ref written with the full path (matches a `stacks` key EXACTLY) must
    # keep working exactly as it did before this package (regression bar).
    stacks = {
        "infra/a": {
            "requires": ["stack:infra/b:healthy"],
            "provides": ["stack:infra/a:healthy"],
        },
        "infra/b": {
            "requires": ["stack:infra/a:healthy"],
            "provides": ["stack:infra/b:healthy"],
        },
    }
    errors = lint_graph(stacks)
    cycle_errors = [e for e in errors if "cycle" in e.lower()]
    assert len(cycle_errors) >= 1


# ---------------------------------------------------------------------------
# _resolve_declared_stack_path (O4/O5 shared helper)
# ---------------------------------------------------------------------------


def test_resolve_declared_stack_path_exact_match():
    assert provisioning._resolve_declared_stack_path(
        "infra/db-init", {"infra/db-init", "infra/vault"}
    ) == "infra/db-init"


def test_resolve_declared_stack_path_unique_basename_match():
    assert provisioning._resolve_declared_stack_path(
        "db-init", {"infra/db-init", "infra/vault"}
    ) == "infra/db-init"


def test_resolve_declared_stack_path_ambiguous_basename_returns_none():
    assert provisioning._resolve_declared_stack_path(
        "db-init", {"infra/db-init", "apps/db-init"}
    ) is None


def test_resolve_declared_stack_path_unknown_returns_none():
    assert provisioning._resolve_declared_stack_path(
        "nope", {"infra/db-init"}
    ) is None


# ---------------------------------------------------------------------------
# probe_ref — injected docker_exec_fn
# ---------------------------------------------------------------------------


def test_probe_ref_pg_role_found():
    def docker_exec_fn(container, cmd):
        return (0, "1\n")

    result = probe_ref(
        "pg:role/myuser",
        config={},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"pgstack": {"provides": ["pg:role/myuser"]}},
    )
    assert result.satisfied is True
    assert "myuser" in result.reason


def test_probe_ref_pg_role_not_found():
    def docker_exec_fn(container, cmd):
        return (0, "\n")  # empty output = not found

    result = probe_ref(
        "pg:role/myuser",
        config={},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"pgstack": {"provides": ["pg:role/myuser"]}},
    )
    assert result.satisfied is False
    # CIU-70: rc==0 is the ONLY status from which "genuinely absent" follows,
    # so this reason says so instead of the old ambiguous "not found (rc=…)".
    assert "does not exist" in result.reason


def test_probe_ref_pg_db_found():
    def docker_exec_fn(container, cmd):
        return (0, "1\n")

    result = probe_ref(
        "pg:db/mydb",
        config={},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"pgstack": {"provides": ["pg:db/mydb"]}},
    )
    assert result.satisfied is True
    assert "mydb" in result.reason


def test_probe_ref_minio_user_found():
    def docker_exec_fn(container, cmd):
        return (0, "some output")

    result = probe_ref(
        "minio:user/worker",
        config={},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"objstore": {"provides": ["minio:user/worker"]}},
    )
    assert result.satisfied is True
    assert "worker" in result.reason


def test_probe_ref_minio_user_not_found():
    def docker_exec_fn(container, cmd):
        return (1, "")

    result = probe_ref(
        "minio:user/worker",
        config={},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"objstore": {"provides": ["minio:user/worker"]}},
    )
    assert result.satisfied is False
    assert "MinIO user 'worker' not found (rc=1)" == result.reason


def test_probe_ref_stack_healthy_via_exec():
    def docker_exec_fn(container, cmd):
        return (0, "healthy")

    result = probe_ref(
        "stack:db-core:healthy",
        config={"deploy": {"project_name": "myproj", "environment_tag": "dev"}},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
    )
    assert result.satisfied is True


def test_probe_ref_stack_not_healthy_via_exec():
    def docker_exec_fn(container, cmd):
        return (1, "")

    result = probe_ref(
        "stack:db-core:healthy",
        config={"deploy": {"project_name": "myproj", "environment_tag": "dev"}},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
    )
    assert result.satisfied is False


def test_probe_ref_invalid_ref_returns_unsatisfied():
    result = probe_ref(
        "badref",
        config={},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is False
    assert "missing kind prefix" in result.reason


# ---------------------------------------------------------------------------
# probe_ref — vault_client injection
# ---------------------------------------------------------------------------


class _FakeVaultClient:
    def __init__(self, data: dict):
        self._data = data

    def read(self, path, field=None):
        return self._data.get(path)


def test_probe_ref_vault_found():
    client = _FakeVaultClient({"db/password": "s3cr3t"})
    result = probe_ref(
        "vault:secret/db/password",
        config={},
        repo_root=Path("/tmp"),
        vault_client=client,
    )
    assert result.satisfied is True
    assert "db/password" in result.reason


def test_probe_ref_vault_not_found():
    client = _FakeVaultClient({})
    result = probe_ref(
        "vault:secret/db/password",
        config={},
        repo_root=Path("/tmp"),
        vault_client=client,
    )
    assert result.satisfied is False
    assert "not found" in result.reason


def test_probe_ref_consul_uses_vault_path():
    # consul:token/myapp should look up consul/acl/tokens/myapp in vault
    seen_paths = []

    class TrackingVault:
        def read(self, path, field=None):
            seen_paths.append(path)
            return "sometoken"

    result = probe_ref(
        "consul:token/myapp",
        config={},
        repo_root=Path("/tmp"),
        vault_client=TrackingVault(),
    )
    assert result.satisfied is True
    assert seen_paths == ["consul/acl/tokens/myapp"]


# ---------------------------------------------------------------------------
# config_model.validate_provisioning_ref
# ---------------------------------------------------------------------------


def test_validate_provisioning_ref_accepts_valid_refs():
    valid = [
        "vault:secret/db/pass",
        "pg:role/myuser",
        "pg:db/mydb",
        "minio:user/worker",
        "consul:token/myapp",
        "stack:db-core:healthy",
        "stack:infra/postgres:healthy",
    ]
    for ref in valid:
        validate_provisioning_ref(ref)  # should not raise


def test_validate_provisioning_ref_rejects_no_colon():
    import pytest
    with pytest.raises(ValueError, match="missing kind prefix"):
        validate_provisioning_ref("vaultsecretpath")


def test_validate_provisioning_ref_rejects_unknown_kind():
    import pytest
    with pytest.raises(ValueError, match="Unknown ref kind"):
        validate_provisioning_ref("redis:key/foo")


def test_validate_provisioning_ref_rejects_bad_pg_format():
    import pytest
    with pytest.raises(ValueError, match="does not match any valid pattern"):
        validate_provisioning_ref("pg:table/foo")


# ---------------------------------------------------------------------------
# config_model.validate_stack_provisioning
# ---------------------------------------------------------------------------


def test_validate_stack_provisioning_passes_empty():
    # No requires/provides — should pass silently
    validate_stack_provisioning({"mystack": {"image": "nginx"}}, source="test")


def test_validate_stack_provisioning_passes_valid_refs_in_root_key():
    config = {
        "mystack": {
            "requires": ["pg:db/mydb", "vault:secret/db/pass"],
            "provides": ["pg:role/myuser"],
        }
    }
    validate_stack_provisioning(config, source="test")


def test_validate_stack_provisioning_fails_invalid_ref_in_requires():
    import pytest
    config = {
        "mystack": {
            "requires": ["bad-ref"],
            "provides": [],
        }
    }
    with pytest.raises(ValueError, match="provisioning validation failed"):
        validate_stack_provisioning(config, source="test")


def test_validate_stack_provisioning_fails_requires_not_a_list():
    import pytest
    config = {
        "mystack": {
            "requires": "pg:db/mydb",  # string, not list
        }
    }
    with pytest.raises(ValueError, match="must be a list"):
        validate_stack_provisioning(config, source="test")


def test_validate_stack_provisioning_fails_item_not_a_string():
    import pytest
    config = {
        "mystack": {
            "requires": [123],
        }
    }
    with pytest.raises(ValueError, match="must be a string"):
        validate_stack_provisioning(config, source="test")


def test_validate_stack_provisioning_collects_all_violations():
    import pytest
    config = {
        "mystack": {
            "requires": ["bad1", "bad2"],
            "provides": ["bad3"],
        }
    }
    with pytest.raises(ValueError) as exc_info:
        validate_stack_provisioning(config, source="test")
    msg = str(exc_info.value)
    # All three violations should appear in the single error
    assert "bad1" in msg
    assert "bad2" in msg
    assert "bad3" in msg


# ---------------------------------------------------------------------------
# deploy.provisioning_preflight — with stubs
# ---------------------------------------------------------------------------


def test_provisioning_preflight_skips_when_no_requires_provides():
    """When no stacks have requires/provides, preflight should silently pass."""
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}}]
    # rendered config has no requires/provides
    rendered = {
        "infra/pg": {"pg_stack": {"image": "postgres"}}
    }

    # Should not raise
    deploy.provisioning_preflight(Path("/tmp"), profile, selection, rendered)


def test_provisioning_preflight_skips_when_no_preflight_flag():
    """--no-preflight (break-glass) skips the entire preflight."""
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}]
    rendered = {
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],
                "provides": [],
            }
        }
    }

    # Even though pg:db/mydb is not provided, no_preflight=True skips it
    deploy.provisioning_preflight(
        Path("/tmp"), profile, selection, rendered,
        no_preflight=True,
    )


def test_provisioning_preflight_raises_on_graph_error():
    """Missing provider should cause provisioning_preflight to raise ValueError."""
    import pytest
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}]
    rendered = {
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],  # nobody provides this
                "provides": [],
            }
        }
    }

    with pytest.raises(ValueError, match="Provisioning graph lint failed"):
        deploy.provisioning_preflight(Path("/tmp"), profile, selection, rendered)


def test_provisioning_preflight_rejects_malformed_ref():
    """A malformed typed ref in provides/requires fails preflight (spec §2 grammar)."""
    import pytest
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}}]
    rendered = {
        "infra/pg": {
            "pg_stack": {
                "requires": [],
                "provides": ["pg:role/ok", "bogus:thing/x"],  # second is malformed
            }
        }
    }

    with pytest.raises(ValueError, match="provisioning validation failed|Unknown ref kind"):
        deploy.provisioning_preflight(Path("/tmp"), profile, selection, rendered)


# ---------------------------------------------------------------------------
# deploy.action_check — with stubs
# ---------------------------------------------------------------------------


def test_action_check_rejects_malformed_ref():
    """`ciu check` exits 2 on a malformed typed ref before linting."""
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}}]
    rendered = {
        "infra/pg": {
            "pg_stack": {
                "requires": ["not-a-valid-ref"],
                "provides": [],
            }
        }
    }

    rc = deploy.action_check(Path("/tmp"), profile, selection, rendered)
    assert rc == 2


def test_action_check_passes_when_no_stacks_with_refs():
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}}]
    rendered = {
        "infra/pg": {"pg_stack": {"image": "postgres"}}
    }

    rc = deploy.action_check(Path("/tmp"), profile, selection, rendered)
    assert rc == 0


def test_action_check_fails_on_graph_error():
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [{"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}]
    rendered = {
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],  # nobody provides
                "provides": [],
            }
        }
    }

    rc = deploy.action_check(Path("/tmp"), profile, selection, rendered)
    assert rc == 2


def test_action_check_passes_with_valid_graph():
    from ciu import deploy
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}},
        {"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}},
    ]
    rendered = {
        "infra/pg": {
            "pg_stack": {
                "provides": ["pg:db/mydb"],
                "requires": [],
            }
        },
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],
                "provides": [],
            }
        },
    }

    rc = deploy.action_check(Path("/tmp"), profile, selection, rendered)
    assert rc == 0


def test_action_check_live_mode_uses_probe(monkeypatch):
    """With live=True, action_check calls probe_ref for each requires."""
    from ciu import deploy, provisioning as prov_mod
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}},
        {"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}},
    ]
    rendered = {
        "infra/pg": {
            "pg_stack": {
                "provides": ["pg:db/mydb"],
                "requires": [],
            }
        },
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],
                "provides": [],
            }
        },
    }

    probed_refs = []

    def fake_probe_ref(ref, config, repo_root, **kwargs):
        probed_refs.append(ref)
        return ProbeResult(ref=ref, satisfied=True, reason="ok")

    monkeypatch.setattr(prov_mod, "probe_ref", fake_probe_ref)

    rc = deploy.action_check(Path("/tmp"), profile, selection, rendered, live=True)
    assert rc == 0
    assert "pg:db/mydb" in probed_refs


def test_action_check_live_mode_fails_on_unsatisfied(monkeypatch):
    from ciu import deploy, provisioning as prov_mod
    from ciu.deploy_pkg.profiles import Profile

    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = Profile(name=None, phase_keys=None, config=config)
    selection = [
        {"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}},
        {"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}},
    ]
    rendered = {
        "infra/pg": {
            "pg_stack": {
                "provides": ["pg:db/mydb"],
                "requires": [],
            }
        },
        "apps/backend": {
            "backend": {
                "requires": ["pg:db/mydb"],
                "provides": [],
            }
        },
    }

    def fake_probe_ref(ref, config, repo_root, **kwargs):
        return ProbeResult(ref=ref, satisfied=False, reason="not found")

    monkeypatch.setattr(prov_mod, "probe_ref", fake_probe_ref)

    rc = deploy.action_check(Path("/tmp"), profile, selection, rendered, live=True)
    assert rc == 1


# ---------------------------------------------------------------------------
# build_action_sequence — includes --check
# ---------------------------------------------------------------------------


def test_build_action_sequence_check():
    from ciu.deploy import build_action_sequence
    actions = build_action_sequence(["--check"])
    assert actions == ["check"]


def test_build_action_sequence_check_with_other_flags():
    from ciu.deploy import build_action_sequence
    actions = build_action_sequence(["--check", "--profile", "core"])
    assert "check" in actions


# ---------------------------------------------------------------------------
# parse_args — new flags present
# ---------------------------------------------------------------------------


def test_parse_args_check_flag():
    from ciu.deploy import parse_args
    args = parse_args(["--check"])
    assert args.check is True


def test_parse_args_no_preflight_flag():
    from ciu.deploy import parse_args
    args = parse_args(["--no-preflight"])
    assert args.no_preflight is True


def test_parse_args_live_flag():
    from ciu.deploy import parse_args
    args = parse_args(["--check", "--live"])
    assert args.live is True


def test_parse_args_defaults():
    from ciu.deploy import parse_args
    args = parse_args([])
    assert args.check is False
    assert args.no_preflight is False
    assert args.live is False


# ---------------------------------------------------------------------------
# 4.2: pg:schema ref kind
# ---------------------------------------------------------------------------


def test_parse_ref_pg_schema():
    ref = parse_ref("pg:schema/authentik")
    assert ref.kind == "pg"
    assert ref.subkind == "schema"
    assert ref.selector == "authentik"


def test_validate_provisioning_ref_accepts_pg_schema():
    validate_provisioning_ref("pg:schema/authentik")  # should not raise


def test_probe_ref_pg_schema_found_targets_app_db():
    """A pg:schema probe must query the APP database (-d), not the default 'postgres'
    db, because information_schema.schemata is per-database."""
    seen = {}

    def docker_exec_fn(container, cmd):
        seen["cmd"] = cmd
        return (0, "1\n")

    result = probe_ref(
        "pg:schema/authentik",
        config={"registry": {"postgresql": {"database": "dstdns"}}},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"pgstack": {"provides": ["pg:schema/authentik"]}},
    )
    assert result.satisfied is True
    assert "authentik" in result.reason
    assert "-d" in seen["cmd"] and "dstdns" in seen["cmd"]
    assert "information_schema.schemata" in " ".join(seen["cmd"])


def test_probe_ref_pg_schema_not_found():
    def docker_exec_fn(container, cmd):
        return (0, "\n")

    result = probe_ref(
        "pg:schema/missing",
        config={"registry": {"postgresql": {"database": "dstdns"}}},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
        stacks={"pgstack": {"provides": ["pg:schema/missing"]}},
    )
    assert result.satisfied is False
    assert "does not exist" in result.reason


# ---------------------------------------------------------------------------
# 4.2: consul:token configurable Vault path
# ---------------------------------------------------------------------------


def test_consul_token_path_is_configurable():
    seen = []

    class TrackingVault:
        def read(self, path, field=None):
            seen.append(path)
            return "tok"

    result = probe_ref(
        "consul:token/controller",
        config={"registry": {"consul": {"token_vault_path": "consul/{svc}/token"}}},
        repo_root=Path("/tmp"),
        vault_client=TrackingVault(),
    )
    assert result.satisfied is True
    assert seen == ["consul/controller/token"]


def test_consul_token_path_defaults_when_unset():
    seen = []

    class TrackingVault:
        def read(self, path, field=None):
            seen.append(path)
            return "tok"

    probe_ref(
        "consul:token/myapp",
        config={},
        repo_root=Path("/tmp"),
        vault_client=TrackingVault(),
    )
    assert seen == ["consul/acl/tokens/myapp"]


# ---------------------------------------------------------------------------
# 4.2: stack:<name>:healthy treats an exited-0 one-shot as satisfied
# ---------------------------------------------------------------------------


def test_probe_stack_oneshot_exited_zero_is_satisfied(monkeypatch):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:healthy",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is True
    assert "exited 0" in result.reason or "one-shot" in result.reason


def test_probe_stack_exited_nonzero_is_unsatisfied(monkeypatch):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 1, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:healthy",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is False


# ---------------------------------------------------------------------------
# O2: the exit-0-no-healthcheck fallback now WARNS (behavior unchanged)
# ---------------------------------------------------------------------------


def test_probe_stack_healthy_oneshot_fallback_warns_deprecated(monkeypatch, capsys):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:healthy",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    # Behavior is UNCHANGED (O2's negative: this must not become an abort).
    assert result.satisfied is True
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "stack:db-init:healthy" in out
    assert "stack:db-init:completed" in out


def test_probe_stack_healthy_running_no_healthcheck_does_not_warn(monkeypatch, capsys):
    # The OTHER no-healthcheck branch (a long-running container) is a
    # different code path from the exit-0 fallback and must NOT warn.
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": None, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:healthy",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is True
    assert "[WARN]" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# O1: :completed terminal — exit-0-based, NEVER reads Health
# ---------------------------------------------------------------------------


def test_probe_stack_completed_exited_zero_is_satisfied(monkeypatch):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:completed",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is True
    assert "exited 0" in result.reason or "completed" in result.reason


def test_probe_stack_completed_nonzero_exit_is_not_satisfied(monkeypatch):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 1, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:completed",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is False


def test_probe_stack_completed_still_running_is_not_satisfied(monkeypatch):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": None, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:completed",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is False


def test_probe_stack_completed_never_reads_health_even_when_healthy(monkeypatch):
    # The exact false-positive gap O1 closes: a healthcheck that reports
    # 'healthy' while the container is STILL RUNNING (not yet exited) must
    # NOT satisfy :completed -- unlike :healthy, which is satisfied by
    # Health.Status alone regardless of Running/ExitCode (proven below by
    # probing the identical state via :healthy on the same fixture).
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps(
            {"Running": True, "ExitCode": None, "Health": {"Status": "healthy"}}
        )

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())

    completed_result = probe_ref(
        "stack:db-init:completed",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert completed_result.satisfied is False

    healthy_result = probe_ref(
        "stack:db-init:healthy",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert healthy_result.satisfied is True


def test_probe_stack_completed_container_not_found(monkeypatch):
    from ciu import procutil

    class _R:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    result = probe_ref(
        "stack:db-init:completed",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    assert result.satisfied is False
    assert "not found" in result.reason


# ---------------------------------------------------------------------------
# O4: slash-bearing selector resolution (+ slash-free regression bar)
# ---------------------------------------------------------------------------


def test_probe_stack_slash_free_selector_container_name_byte_identical(monkeypatch):
    import json as _json
    from ciu import procutil

    captured = {}

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    def fake_docker(args, check=False):
        captured["args"] = list(args)
        return _R()

    monkeypatch.setattr(procutil, "docker", fake_docker)
    probe_ref(
        "stack:db-init:healthy",
        config={"deploy": {"project_name": "p", "environment_tag": "t"}},
        repo_root=Path("/tmp"),
    )
    # container_name(config, "db-init") == "p-t-db-init" -- exactly the same
    # call pre-package code made (selector passed straight through, unchanged).
    assert captured["args"][-1] == "p-t-db-init"


def test_probe_stack_slash_bearing_selector_resolves_known_declared_path(monkeypatch):
    import json as _json
    from ciu import procutil

    captured = {}

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 0, "Health": {}})

    def fake_docker(args, check=False):
        captured["args"] = list(args)
        return _R()

    monkeypatch.setattr(procutil, "docker", fake_docker)
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": {
                "phase_1": {"services": [{"path": "infra/db-init"}]},
            },
        }
    }
    result = probe_ref("stack:infra/db-init:completed", config=config, repo_root=Path("/tmp"))
    # Previously guaranteed-broken (container_name(config, "infra/db-init")
    # -> "p-t-infra/db-init", matching no real container). Now resolves to
    # the declared path's basename, exactly like a bare selector always has.
    assert captured["args"][-1] == "p-t-db-init"
    assert result.satisfied is True


def test_probe_stack_slash_bearing_selector_via_profile_stacks_list(monkeypatch):
    import json as _json
    from ciu import procutil

    captured = {}

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    def fake_docker(args, check=False):
        captured["args"] = list(args)
        return _R()

    monkeypatch.setattr(procutil, "docker", fake_docker)
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "profiles": {"core": {"stacks": ["infra/db-init"]}},
        }
    }
    probe_ref("stack:infra/db-init:healthy", config=config, repo_root=Path("/tmp"))
    assert captured["args"][-1] == "p-t-db-init"


def test_probe_stack_slash_bearing_selector_unknown_path_stays_broken(monkeypatch):
    # No declared stack matches -- the selector is passed through UNCHANGED,
    # exactly as today's (guaranteed-broken) behavior, so a genuine typo
    # still surfaces as "container not found" rather than being silently
    # reinterpreted.
    import json as _json
    from ciu import procutil

    captured = {}

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    def fake_docker(args, check=False):
        captured["args"] = list(args)
        return _R()

    monkeypatch.setattr(procutil, "docker", fake_docker)
    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    probe_ref("stack:some/unknown:healthy", config=config, repo_root=Path("/tmp"))
    assert captured["args"][-1] == "p-t-some/unknown"


# ---------------------------------------------------------------------------
# _declared_stack_paths / _stack_container_name / _one_shot_stack_service
# (private helpers, direct unit coverage of defensive branches)
# ---------------------------------------------------------------------------


def test_declared_stack_paths_empty_config():
    assert provisioning._declared_stack_paths({}) == set()


def test_declared_stack_paths_deploy_not_a_dict():
    assert provisioning._declared_stack_paths({"deploy": "nope"}) == set()


def test_declared_stack_paths_skips_non_dict_phase_and_service():
    config = {
        "deploy": {
            "phases": {
                "phase_1": "not-a-dict",
                "phase_2": {
                    "services": [
                        "not-a-dict-either",
                        {"path": ""},
                        {"path": 123},
                        {},
                        {"path": "infra/a"},
                    ]
                },
            },
        }
    }
    assert provisioning._declared_stack_paths(config) == {"infra/a"}


def test_declared_stack_paths_phases_not_a_dict_still_reads_profiles():
    config = {
        "deploy": {
            "phases": "not-a-dict",
            "profiles": {"core": {"stacks": ["infra/z"]}},
        }
    }
    assert provisioning._declared_stack_paths(config) == {"infra/z"}


def test_declared_stack_paths_from_profiles_skips_non_dict_entries():
    config = {
        "deploy": {
            "profiles": {
                "core": "not-a-dict",
                "edge": {"stacks": [123, "", "infra/b"]},
            },
        }
    }
    assert provisioning._declared_stack_paths(config) == {"infra/b"}


def test_declared_stack_paths_profiles_not_a_dict():
    config = {"deploy": {"profiles": "nope"}}
    assert provisioning._declared_stack_paths(config) == set()


def test_stack_container_name_no_config_no_match_falls_back_to_selector():
    # A missing deploy.project_name/environment_tag raises inside
    # container_name; the KeyError/ValueError fallback returns the selector
    # unchanged -- same fallback contract _probe_stack always had.
    assert provisioning._stack_container_name({}, "db-init") == "db-init"


def test_one_shot_stack_service_no_deploy_table():
    assert provisioning._one_shot_stack_service({}, "db-init") is None


def test_one_shot_stack_service_deploy_not_a_dict():
    assert provisioning._one_shot_stack_service({"deploy": "nope"}, "db-init") is None


def test_one_shot_stack_service_phases_not_a_dict():
    assert provisioning._one_shot_stack_service({"deploy": {"phases": "nope"}}, "db-init") is None


def test_one_shot_stack_service_skips_non_dict_phase_and_service():
    config = {
        "deploy": {
            "phases": {
                "phase_1": "not-a-dict",
                "phase_2": {
                    "services": [
                        "not-a-dict-either",
                        {"path": ""},
                        {"path": 123},
                        {},
                        {"path": "infra/a"},
                    ]
                },
            },
        }
    }
    assert provisioning._one_shot_stack_service(config, "nomatch") is None
    matched = provisioning._one_shot_stack_service(config, "infra/a")
    assert matched == {"path": "infra/a"}


# ---------------------------------------------------------------------------
# O3: one_shot ciu-check cross-reference warning
# ---------------------------------------------------------------------------


def test_probe_stack_healthy_warns_when_target_declares_one_shot(monkeypatch, capsys):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": {
                "phase_1": {"services": [{"path": "infra/db-init", "one_shot": True}]},
            },
        }
    }
    result = probe_ref("stack:infra/db-init:healthy", config=config, repo_root=Path("/tmp"))
    assert result.satisfied is True
    out = capsys.readouterr().out
    assert "[WARN]" in out
    assert "one_shot" in out
    assert "stack:infra/db-init:completed" in out


def test_probe_stack_healthy_warns_via_bare_selector_matching_one_shot_stack(monkeypatch, capsys):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": {
                "phase_1": {"services": [{"path": "infra/db-init", "one_shot": True}]},
            },
        }
    }
    result = probe_ref("stack:db-init:healthy", config=config, repo_root=Path("/tmp"))
    assert result.satisfied is True
    assert "[WARN]" in capsys.readouterr().out


def test_probe_stack_healthy_no_warning_when_target_not_one_shot(monkeypatch, capsys):
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": {
                "phase_1": {"services": [{"path": "infra/db-init"}]},
            },
        }
    }
    result = probe_ref("stack:infra/db-init:healthy", config=config, repo_root=Path("/tmp"))
    assert result.satisfied is True
    assert "[WARN]" not in capsys.readouterr().out


def test_probe_stack_completed_never_emits_one_shot_cross_reference_warning(monkeypatch, capsys):
    # A :completed ref is already the CORRECT form -- no cross-reference
    # warning should fire even when the target declares one_shot = true.
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": False, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": {
                "phase_1": {"services": [{"path": "infra/db-init", "one_shot": True}]},
            },
        }
    }
    result = probe_ref("stack:infra/db-init:completed", config=config, repo_root=Path("/tmp"))
    assert result.satisfied is True
    assert "[WARN]" not in capsys.readouterr().out


def test_probe_stack_healthy_one_shot_malformed_does_not_raise(monkeypatch, capsys):
    # A malformed one_shot on the MATCHED entry must never make a probe
    # raise (module contract: probe_ref/​_probe_stack always return a
    # ProbeResult) -- treated as "not declared one_shot", no warning.
    import json as _json
    from ciu import procutil

    class _R:
        returncode = 0
        stdout = _json.dumps({"Running": True, "ExitCode": 0, "Health": {}})

    monkeypatch.setattr(procutil, "docker", lambda *a, **k: _R())
    config = {
        "deploy": {
            "project_name": "p",
            "environment_tag": "t",
            "phases": {
                "phase_1": {"services": [{"path": "infra/db-init", "one_shot": "yes"}]},
            },
        }
    }
    result = probe_ref("stack:infra/db-init:healthy", config=config, repo_root=Path("/tmp"))
    assert result.satisfied is True
    assert "[WARN]" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 4.2: per-phase preflight — lint/probe toggles
# ---------------------------------------------------------------------------


def _profile_for(config):
    from ciu.deploy_pkg.profiles import Profile
    return Profile(name=None, phase_keys=None, config=config)


def test_preflight_lint_only_does_not_probe():
    """probe=False must lint the graph but never attempt a live probe (no docker/vault)."""
    from ciu import deploy
    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = _profile_for(config)
    selection = [
        {"path": "infra/pg", "service": {"path": "infra/pg", "enabled": True}},
        {"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}},
    ]
    rendered = {
        "infra/pg": {"pg": {"provides": ["pg:db/mydb"], "requires": []}},
        "apps/backend": {"backend": {"requires": ["pg:db/mydb"], "provides": []}},
    }
    # lint passes (pg:db/mydb is provided); probe=False → no live probing, no raise.
    deploy.provisioning_preflight(Path("/tmp"), profile, selection, rendered, probe=False)


def test_preflight_probe_only_skips_lint(monkeypatch):
    """lint=False skips the static graph check (used for per-phase probing where a
    require may be satisfied by an EARLIER phase not in this call's selection)."""
    from ciu import deploy, provisioning as prov_mod
    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = _profile_for(config)
    selection = [{"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}]
    # apps/backend requires pg:db/mydb but no provider in THIS selection — would fail
    # a lint, but lint=False skips it; probe is mocked satisfied → no raise.
    rendered = {"apps/backend": {"backend": {"requires": ["pg:db/mydb"], "provides": []}}}
    monkeypatch.setattr(
        prov_mod, "probe_ref",
        lambda ref, config, repo_root, **k: ProbeResult(ref=ref, satisfied=True, reason="ok"),
    )
    deploy.provisioning_preflight(Path("/tmp"), profile, selection, rendered, lint=False)


def test_preflight_probe_failure_raises(monkeypatch):
    import pytest
    from ciu import deploy, provisioning as prov_mod
    config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
    profile = _profile_for(config)
    selection = [{"path": "apps/backend", "service": {"path": "apps/backend", "enabled": True}}]
    rendered = {"apps/backend": {"backend": {"requires": ["pg:db/mydb"], "provides": []}}}
    monkeypatch.setattr(
        prov_mod, "probe_ref",
        lambda ref, config, repo_root, **k: ProbeResult(ref=ref, satisfied=False, reason="not found"),
    )
    with pytest.raises(ValueError, match="unsatisfied requirements"):
        deploy.provisioning_preflight(Path("/tmp"), profile, selection, rendered, lint=False)


# ===========================================================================
# S13.4b — `[registry.*]` schema validation (ciu check stage 7, ciu-P19)
# ===========================================================================
#
# SCOPE NOTE (ciu-P19 O1). CIU reads exactly TWO values out of `[registry.*]`:
# `[registry.postgresql].database` (_probe_pg's `psql -d` target) and
# `[registry.consul].token_vault_path` (_probe_consul's `.format(svc=…)`
# template). Those are the only two these tests pin. The V8 proposal's other
# three "built-in kinds" (Redis/MinIO/Vault registry tables) have NO shape
# anywhere in this repo to validate against, so no model exists to test and a
# consumer table carrying them must pass untouched — which
# `test_registry_extra_keys_are_never_rejected` asserts directly.

import textwrap  # noqa: E402

import pytest  # noqa: E402


def _validator_file(tmp_path: Path, body: str, name: str = "registry_validate.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _svc_template_problem — every constraint grounded in _probe_consul
# ---------------------------------------------------------------------------


def test_svc_template_accepts_the_documented_default():
    assert provisioning._svc_template_problem(
        provisioning.CONSUL_TOKEN_VAULT_PATH_DEFAULT
    ) is None


def test_svc_template_accepts_a_template_with_no_placeholder_at_all():
    """A constant path substitutes cleanly, so it is NOT rejected.

    Deliberate non-constraint (O4's negative): `{svc}` presence is not
    required, because `_probe_consul` requires nothing of the sort — a
    template without it is degenerate for most deployments but legitimate for
    one shared ACL token.
    """
    assert provisioning._svc_template_problem("consul/shared/token") is None


def test_svc_template_rejects_unbalanced_braces():
    problem = provisioning._svc_template_problem("consul/{svc/token")
    assert problem is not None
    assert "aborts the whole probe run" in problem


def test_svc_template_rejects_an_unknown_named_placeholder():
    problem = provisioning._svc_template_problem("consul/{service}/token")
    assert problem is not None
    assert "{service}" in problem
    assert "SILENTLY" in problem
    assert provisioning.CONSUL_TOKEN_VAULT_PATH_DEFAULT in problem


def test_svc_template_rejects_positional_placeholders():
    assert "{}" in (provisioning._svc_template_problem("consul/{}/token") or "")
    assert "{0}" in (provisioning._svc_template_problem("consul/{0}/token") or "")


def test_svc_template_accepts_attribute_and_index_access_on_svc():
    """The root of the field name is what matters — `{svc.x}` still substitutes."""
    assert provisioning._svc_template_problem("consul/{svc:>4}/token") is None


def test_svc_template_rejection_matches_the_probe_it_protects(tmp_path):
    """The validator's grounding, proved against the real probe.

    `_probe_consul` catches KeyError and silently falls back to the default
    path — so an unknown placeholder produces a WRONG Vault read with no
    warning. That is the behaviour this constraint exists to surface at check
    time, and it is asserted here rather than merely asserted about.
    """
    seen = []

    class Vault:
        def read(self, path):
            seen.append(path)
            return None

    provisioning.probe_ref(
        "consul:token/api",
        {"registry": {"consul": {"token_vault_path": "consul/{service}/token"}}},
        tmp_path,
        vault_client=Vault(),
    )
    assert seen == ["consul/acl/tokens/api"]  # NOT consul/api/token
    assert provisioning._svc_template_problem("consul/{service}/token") is not None


# ---------------------------------------------------------------------------
# The two models — exactly the two fields CIU consumes
# ---------------------------------------------------------------------------


def test_registry_models_accept_the_documented_config(tmp_path):
    config = {
        "registry": {
            "postgresql": {"database": "dstdns"},
            "consul": {"token_vault_path": "consul/{svc}/token"},
        }
    }
    assert provisioning.validate_registries(config, tmp_path) == []


@pytest.mark.parametrize("bad", [123, True, ["dstdns"], {"name": "dstdns"}])
def test_registry_postgresql_database_must_be_a_string(bad, tmp_path):
    findings = provisioning.validate_registries(
        {"registry": {"postgresql": {"database": bad}}}, tmp_path
    )
    assert len(findings) == 1
    assert findings[0].startswith("[S13.4b] [registry.postgresql].database: ")
    assert "valid string" in findings[0]


def test_registry_postgresql_database_must_not_be_empty(tmp_path):
    """An empty string is falsy, so `_probe_pg` skips it and quietly targets
    the default `postgres` database instead of the app one."""
    findings = provisioning.validate_registries(
        {"registry": {"postgresql": {"database": ""}}}, tmp_path
    )
    assert len(findings) == 1
    assert "at least 1 character" in findings[0]


def test_registry_consul_token_vault_path_must_be_a_valid_svc_template(tmp_path):
    findings = provisioning.validate_registries(
        {"registry": {"consul": {"token_vault_path": "consul/{service}/token"}}},
        tmp_path,
    )
    assert len(findings) == 1
    assert findings[0].startswith("[S13.4b] [registry.consul].token_vault_path: ")
    assert "{service}" in findings[0]


def test_registry_consul_token_vault_path_must_be_a_string(tmp_path):
    findings = provisioning.validate_registries(
        {"registry": {"consul": {"token_vault_path": 42}}}, tmp_path
    )
    assert len(findings) == 1
    assert "valid string" in findings[0]


def test_registry_sub_table_must_be_a_table(tmp_path):
    """`registry.postgresql = "x"` crashes `_probe_pg` with AttributeError;
    the finding names the table with no key path appended."""
    findings = provisioning.validate_registries(
        {"registry": {"postgresql": "dstdns"}}, tmp_path
    )
    assert findings == [
        "[S13.4b] [registry.postgresql]: Input should be a valid dictionary "
        "or instance of RegistryPostgresql"
    ]


def test_registry_itself_must_be_a_table(tmp_path):
    findings = provisioning.validate_registries({"registry": "nope"}, tmp_path)
    assert findings == ["[S13.4b] [registry] must be a table, got str"]


def test_registry_extra_keys_are_never_rejected(tmp_path):
    """O1's negative, pinned: `[registry.*]` is free-form consumer metadata.

    CIU constrains the two keys it reads and NOTHING else — not the
    PostgreSQL users table, not Redis ACLs, not a MinIO/Vault table it has
    never had a shape for. A model that rejected these would break every real
    consumer config.
    """
    config = {
        "registry": {
            "postgresql": {
                "database": "dstdns",
                "users": {"controller": {"role": "rw", "schema": "controller"}},
            },
            "consul": {"token_vault_path": "consul/{svc}/token", "datacenter": "dc1"},
            "redis": {"users": {"worker": {"acl": "+@read"}}},
            "minio": {"buckets": ["artifacts"]},
            "vault": {"mounts": {"kv": {"type": "kv-v2"}}},
        }
    }
    assert provisioning.validate_registries(config, tmp_path) == []


def test_registry_models_report_every_finding_not_just_the_first(tmp_path):
    findings = provisioning.validate_registries(
        {
            "registry": {
                "postgresql": {"database": 1},
                "consul": {"token_vault_path": "consul/{}/token"},
            }
        },
        tmp_path,
    )
    assert len(findings) == 2
    assert any("[registry.postgresql].database" in f for f in findings)
    assert any("[registry.consul].token_vault_path" in f for f in findings)


def test_registry_consul_model_tolerates_an_explicit_none(tmp_path):
    """The field validator's None guard, exercised through the model API.

    TOML cannot express null, so this is reachable only by a direct
    `model_validate` — but the guard is what keeps the validator from calling
    `_svc_template_problem(None)` if it ever is.
    """
    models = provisioning._build_registry_models()
    assert models["consul"].model_validate({"token_vault_path": None}).token_vault_path is None


# ---------------------------------------------------------------------------
# pydantic is OPTIONAL — absent ⇒ loud, never a silent skip (O2)
# ---------------------------------------------------------------------------


def test_absent_pydantic_fails_loud_with_install_hint(tmp_path, monkeypatch):
    """With pydantic unimportable and a validated table declared, the finding
    names the extra and how to install it (mirrors CIU-37/S5.7's
    `test_absent_jsonschema_fails_loud_with_install_hint`; `sys.modules[…] =
    None` makes the import raise ImportError)."""
    monkeypatch.setitem(sys.modules, "pydantic", None)
    findings = provisioning.validate_registries(
        {"registry": {"postgresql": {"database": "dstdns"}}}, tmp_path
    )
    assert len(findings) == 1
    assert "[S13.4b]" in findings[0]
    assert "ciu[registry]" in findings[0]
    assert "pip install" in findings[0]


def test_absent_pydantic_never_silently_skips_a_malformed_table(tmp_path, monkeypatch):
    """The anti-pattern this exists to avoid: a config that IS wrong must not
    come back clean just because the extra is missing."""
    monkeypatch.setitem(sys.modules, "pydantic", None)
    findings = provisioning.validate_registries(
        {"registry": {"consul": {"token_vault_path": "consul/{service}/token"}}},
        tmp_path,
    )
    assert findings != []


def test_no_registry_table_never_imports_pydantic(tmp_path, monkeypatch):
    """With pydantic forced unimportable, a config declaring no validated
    sub-table still validates clean — any import attempt would raise."""
    monkeypatch.setitem(sys.modules, "pydantic", None)
    assert provisioning.validate_registries({}, tmp_path) == []
    assert provisioning.validate_registries(
        {"registry": {"redis": {"users": {}}}}, tmp_path
    ) == []


# ---------------------------------------------------------------------------
# The consumer-declared validate_registry extension point (Option C)
# ---------------------------------------------------------------------------


def test_consumer_validator_findings_are_reported(tmp_path):
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            entries = config["registry"]["redis"]["users"]
            return [f"redis user {n} has no acl" for n, u in entries.items()
                    if "acl" not in u]
        """,
    )
    findings = provisioning.validate_registries(
        {
            "ciu": {"registry_validator": "registry_validate.py"},
            "registry": {"redis": {"users": {"worker": {}, "reader": {"acl": "+@read"}}}},
        },
        tmp_path,
    )
    assert findings == ["[S13.4b] redis user worker has no acl"]


def test_consumer_validator_accepts_an_absolute_path(tmp_path):
    path = _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            return []
        """,
        name="abs_validate.py",
    )
    assert provisioning.validate_registries(
        {"ciu": {"registry_validator": str(path)}}, tmp_path
    ) == []


def test_consumer_validator_receives_the_whole_global_config(tmp_path):
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            if config["deploy"]["project_name"] != "demo":
                return ["did not see the whole global config"]
            return []
        """,
    )
    assert provisioning.validate_registries(
        {
            "ciu": {"registry_validator": "registry_validate.py"},
            "deploy": {"project_name": "demo"},
        },
        tmp_path,
    ) == []


def test_consumer_validator_returning_none_is_no_findings(tmp_path):
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            return None
        """,
    )
    assert provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    ) == []


@pytest.mark.parametrize("returned, shown", [("'one big string'", "str"), ("7", "int")])
def test_consumer_validator_malformed_return_is_one_finding(returned, shown, tmp_path):
    """A bare string is ONE malformed return, not one finding per character —
    the same `str`-is-iterable trap S9.5's `validate_config` handling avoids."""
    _validator_file(
        tmp_path,
        f"""
        def validate_registry(config):
            return {returned}
        """,
    )
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    )
    assert len(findings) == 1
    assert f"returned {shown}" in findings[0]


def test_consumer_validator_exception_is_reported_not_raised(tmp_path):
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            raise RuntimeError("boom")
        """,
    )
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    )
    assert len(findings) == 1
    assert "raised RuntimeError: boom" in findings[0]


def test_consumer_validator_import_time_explosion_is_reported(tmp_path):
    _validator_file(
        tmp_path,
        """
        raise ZeroDivisionError("at import")

        def validate_registry(config):
            return []
        """,
    )
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    )
    assert len(findings) == 1
    assert "could not be loaded: ZeroDivisionError" in findings[0]


def test_consumer_validator_missing_file_is_reported(tmp_path):
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": "nope.py"}}, tmp_path
    )
    assert len(findings) == 1
    assert "could not be loaded: FileNotFoundError" in findings[0]
    assert "[S9.2]" in findings[0]  # reuses hooks_runner's loader semantics


@pytest.mark.parametrize("body", [
    "x = 1\n",
    "validate_registry = 'not callable'\n",
])
def test_consumer_validator_without_the_callable_is_reported(body, tmp_path):
    _validator_file(tmp_path, body)
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    )
    assert len(findings) == 1
    assert "defines no callable validate_registry(config)" in findings[0]


@pytest.mark.parametrize("declared", [42, ""])
def test_consumer_validator_path_must_be_a_non_empty_string(declared, tmp_path):
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": declared}}, tmp_path
    )
    assert len(findings) == 1
    assert "must be a non-empty path string" in findings[0]


@pytest.mark.parametrize("ciu_table", [{}, {"require_fqdn": True}, "not-a-table", None])
def test_no_consumer_validator_declared_is_a_no_op(ciu_table, tmp_path):
    assert provisioning.validate_registries({"ciu": ciu_table}, tmp_path) == []


def test_consumer_validator_import_writes_no_pycache(tmp_path):
    """`ciu check` is side-effect-free (S13.4a): importing the consumer's
    validator must not drop a `__pycache__/` beside it."""
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            return []
        """,
    )
    before = sorted(p.name for p in tmp_path.iterdir())
    saved = sys.dont_write_bytecode
    assert provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    ) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert sys.dont_write_bytecode is saved  # flag restored


def test_consumer_validator_bytecode_flag_is_restored_after_a_failed_import(tmp_path):
    _validator_file(tmp_path, "raise ValueError('nope')\n")
    saved = sys.dont_write_bytecode
    provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    )
    assert sys.dont_write_bytecode is saved


def test_consumer_validator_runs_even_when_pydantic_is_absent(tmp_path, monkeypatch):
    """Option C does not depend on Option B's optional extra."""
    monkeypatch.setitem(sys.modules, "pydantic", None)
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            return ["consumer finding"]
        """,
    )
    findings = provisioning.validate_registries(
        {"ciu": {"registry_validator": "registry_validate.py"}}, tmp_path
    )
    assert findings == ["[S13.4b] consumer finding"]


# ---------------------------------------------------------------------------
# Wiring into `ciu check` stage 7 — P18's machinery, P18's exit-code contract
# ---------------------------------------------------------------------------


def _check_profile(config):
    from ciu.deploy_pkg.profiles import Profile
    return Profile(name=None, phase_keys=None, config=config)


def test_check_stage7_is_between_configfile_and_hooks_load():
    from ciu import deploy
    stages = list(deploy.CHECK_STAGES)
    assert stages.index("registry") == stages.index("configfile") + 1
    assert stages.index("hooks-load") == stages.index("registry") + 1


def test_check_fails_exit_2_on_a_malformed_registry_table(tmp_path):
    from ciu import deploy
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "registry": {"postgresql": {"database": 5}},
    })
    rc = deploy.action_check(tmp_path, profile, [], {})
    assert rc == 2


def test_check_stage7_finding_lands_in_the_json_envelope(tmp_path, capsys):
    import json
    from ciu import deploy
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "registry": {"consul": {"token_vault_path": "consul/{service}/token"}},
    })
    rc = deploy.action_check(tmp_path, profile, [], {}, json_output=True)
    doc = json.loads(capsys.readouterr().out)

    assert rc == 2
    assert doc["status"] == "fail"
    stage = next(s for s in doc["stages"] if s["stage"] == "registry")
    assert stage["status"] == "fail"
    assert len(stage["findings"]) == 1
    # Global-scope stage: the one stage whose findings carry no `stack` key.
    assert "stack" not in stage["findings"][0]
    assert "{service}" in stage["findings"][0]["message"]


def test_check_stage7_failure_is_exit_2_even_with_live(tmp_path, monkeypatch):
    """P18's exit-code contract: a static stage failure is 2, never 1, and the
    live probe is never reached."""
    from ciu import deploy, provisioning as prov_mod
    monkeypatch.setattr(prov_mod, "probe_ref", lambda *a, **k: pytest.fail("probed"))
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "registry": {"postgresql": {"database": ""}},
    })
    rc = deploy.action_check(tmp_path, profile, [], {}, live=True)
    assert rc == 2


def test_check_stage7_runs_with_an_empty_selection(tmp_path):
    """A malformed GLOBAL registry table is a real defect regardless of which
    stacks this run selected — same rationale as QOL-11's eager validation."""
    from ciu import deploy
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "registry": {"postgresql": {"database": []}},
    })
    assert deploy.action_check(tmp_path, profile, [], {}) == 2


def test_check_stage7_reports_one_finding_not_one_per_stack(tmp_path, capsys):
    """`[registry]` is GLOBAL, so stage 7 runs once — not once per stack.

    This is why it lives in `action_check` rather than at P18's per-stack
    insertion point: `merged` carries the same global table for every stack.
    """
    import json
    from ciu import deploy
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "registry": {"postgresql": {"database": 5}},
    })
    selection = [{"path": "a"}, {"path": "b"}, {"path": "c"}]
    deploy.action_check(tmp_path, profile, selection, {}, json_output=True)
    doc = json.loads(capsys.readouterr().out)
    stage = next(s for s in doc["stages"] if s["stage"] == "registry")
    assert len(stage["findings"]) == 1


def test_check_stage7_passes_on_a_clean_registry(tmp_path, capsys):
    import json
    from ciu import deploy
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "registry": {
            "postgresql": {"database": "dstdns", "users": {"c": {"role": "rw"}}},
            "consul": {"token_vault_path": "consul/{svc}/token"},
        },
    })
    rc = deploy.action_check(tmp_path, profile, [], {}, json_output=True)
    doc = json.loads(capsys.readouterr().out)
    assert rc == 0
    stage = next(s for s in doc["stages"] if s["stage"] == "registry")
    assert stage == {"stage": "registry", "status": "pass", "findings": [], "notes": []}


def test_check_stage7_wires_the_consumer_validator(tmp_path):
    """End-to-end: a consumer-declared validator's finding fails `ciu check`."""
    from ciu import deploy
    _validator_file(
        tmp_path,
        """
        def validate_registry(config):
            return ["redis user 'worker' has no acl"]
        """,
    )
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "ciu": {"registry_validator": "registry_validate.py"},
    })
    assert deploy.action_check(tmp_path, profile, [], {}) == 2


def test_check_stage7_resolves_the_validator_against_repo_root(tmp_path):
    """A relative `registry_validator` is resolved against the repo root the
    action was invoked with — not the process CWD."""
    from ciu import deploy
    sub = tmp_path / "infra"
    sub.mkdir()
    _validator_file(
        sub,
        """
        def validate_registry(config):
            return []
        """,
    )
    profile = _check_profile({
        "deploy": {"project_name": "p", "environment_tag": "t"},
        "ciu": {"registry_validator": "infra/registry_validate.py"},
    })
    assert deploy.action_check(tmp_path, profile, [], {}) == 0
