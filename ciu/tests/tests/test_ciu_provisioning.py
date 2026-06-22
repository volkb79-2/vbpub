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
    )
    assert result.satisfied is False
    assert "not found" in result.reason


def test_probe_ref_pg_db_found():
    def docker_exec_fn(container, cmd):
        return (0, "1\n")

    result = probe_ref(
        "pg:db/mydb",
        config={},
        repo_root=Path("/tmp"),
        docker_exec_fn=docker_exec_fn,
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
    )
    assert result.satisfied is False


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
