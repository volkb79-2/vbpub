"""Behavioral contracts for explicit supply-chain age-window resolution."""
from __future__ import annotations

import io
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
import types
import urllib.error
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from typing import Mapping

import pytest

from cmru import version_registry as registry
from cmru import versions
from cmru import config as config_module
from cmru.version_config import deep_merge_versions, parse_versions_section


def _candidate(version: str, released_at: datetime, age_source: str = "fixture") -> registry.Candidate:
    return registry.Candidate(version, released_at, age_source)


def _result(
    target_id: str,
    family: str,
    version: str,
    released_at: datetime,
    *,
    age_cutoff: datetime | None = None,
    owner: str = "demo",
    tag: str | None = None,
) -> versions.TargetResult:
    resolved_at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    candidate = registry.Candidate(version, released_at, "fixture", tag)
    return versions.TargetResult(
        target_id=target_id,
        version=version,
        resolved_at=resolved_at,
        age_cutoff=age_cutoff or resolved_at - timedelta(days=14),
        sources={family: candidate},
        override=False,
        reason=None,
        expires=None,
        owner=owner,
    )


def _pypi_target(**extra):
    return {
        "mode": "single",
        "constraint": ">=2.31.0,<3.0.0",
        "pypi": {"name": "requests", "registry": "https://pypi.org"},
        **extra,
    }


def _project_config(name: str, versions_text: str = "") -> str:
    return f'''schema_version = 1

[runtime]
kind = "none"

[project]
id = "{name}"
description = "Test project"
prefix = "{name}-v"
artifacts = ["wheel"]
template_revision = 4

[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"
artifact_dirs = ["dist"]

{versions_text}

[steps.run-tests]
quiet = true
commands = [{{ label = "test", argv = ["true"], cwd = "." }}]

[steps.build]
quiet = true
commands = [{{ label = "build", argv = ["true"], cwd = "." }}]

[steps.push]
quiet = true
commands = [{{ label = "push", argv = ["true"], cwd = "." }}]
'''


def _root_config(name: str, root_versions: str = "") -> str:
    return f'''schema_version = 1

[github]
owner = "example"
repo = "example"
owner_type = "user"

[targets]
host = "github"
registry = ["ghcr.io"]

[orchestration]
project_order = ["{name}"]
default_projects = ["{name}"]
default_steps = ["run-tests", "build", "push"]
execution_mode = "project-first"

[orchestration.project.{name}]
config = "{name}/cmru.toml"
depends_on = []

[cleanup]
release_tag_prefixes = ["*"]
keep_release_tags = ["{name}-latest"]
ghcr_packages = ["*"]
ghcr_delete_packages = []

{root_versions}
'''


def _versions_table(age_days: int, target_id: str = "pypi.requests") -> str:
    return f'''[versions]
age_window_days = {age_days}

[versions.targets."{target_id}"]
mode = "single"
constraint = ">=2.31.0,<3.0.0"

[versions.targets."{target_id}".pypi]
name = "requests"
registry = "https://pypi.org"
'''


def _estate(tmp_path: Path, *, project_versions: str = "", root_versions: str = "") -> tuple[Path, Path]:
    project_root = tmp_path / "demo"
    project_root.mkdir(parents=True, exist_ok=True)
    project_path = project_root / "cmru.toml"
    project_path.write_text(_project_config("demo", project_versions), encoding="utf-8")
    root_path = tmp_path / "cmru.orchestration.toml"
    root_path.write_text(_root_config("demo", root_versions), encoding="utf-8")
    return root_path, project_root


def test_constraint_subset_and_semver_ordering():
    assert registry.version_satisfies("1.4.0", "^1.2.3")
    assert not registry.version_satisfies("2.0.0", "^1.2.3")
    assert registry.version_satisfies("0.4.2", "^0.4.0")
    assert not registry.version_satisfies("0.5.0", "^0.4.0")
    assert registry.version_satisfies("2.2.9", "~2.2.0")
    assert not registry.version_satisfies("2.3.0", "~2.2.0")
    assert registry.version_satisfies("1.9.0", "~=1.4")
    assert not registry.version_satisfies("2.0.0", "~=1.4")
    assert registry.version_satisfies("1.4.9", "~=1.4.5")
    assert not registry.version_satisfies("1.5.0", "~=1.4.5")
    assert registry.version_satisfies("1.2.3", ">=1.2.3,!=1.2.4,<2")
    assert not registry.version_satisfies("1.2.4", ">=1.2.3,!=1.2.4,<2")
    assert registry.version_satisfies("1.2.3-rc.2", ">=1.2.3-rc.1,<1.2.3")
    assert registry.version_satisfies("1.2.3", "*")
    assert registry.version_satisfies("1.2.3", "X")
    assert not registry.version_satisfies("not-semver", "*")
    assert registry._semver_key("v1.2.3+build.5") == registry._semver_key("1.2.3")
    assert registry._semver_key("1.2.3rc1") is None
    assert registry.normalized_version("v1.2.3") == "1.2.3"
    with pytest.raises(registry.RegistryError, match="unsupported version constraint"):
        registry.validate_constraint("^1.2.3 || ^2.0.0")


def test_registry_candidates_and_resolved_results_are_immutable():
    candidate = _candidate("1.2.3", datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(FrozenInstanceError):
        candidate.version = "2.0.0"
    result = _result("pypi.demo", "pypi", "1.2.3", candidate.released_at)
    with pytest.raises(FrozenInstanceError):
        result.version = "2.0.0"


def test_versions_config_is_strict_and_deep_merges_project_tables():
    root = parse_versions_section(
        {"age_window_days": 14, "targets": {"root": _pypi_target()}}, "root"
    )
    overlay = parse_versions_section(
        {"age_window_days": 21, "targets": {"root": {"pypi": {"registry": "https://packages.example/simple"}}}},
        "project",
        allow_partial=True,
    )
    effective = parse_versions_section(deep_merge_versions(root, overlay), "effective")
    assert effective["age_window_days"] == 21
    assert effective["targets"]["root"]["pypi"] == {
        "name": "requests", "registry": "https://packages.example/simple"
    }

    with pytest.raises(ValueError, match="positive integer"):
        parse_versions_section({"age_window_days": 0}, "root")
    for invalid_days in (True, False, -1, 1.5, "14"):
        with pytest.raises(ValueError, match="positive integer"):
            parse_versions_section({"age_window_days": invalid_days}, "root")
    with pytest.raises(ValueError, match="unknown keys"):
        parse_versions_section({"window_days": 14}, "root")
    with pytest.raises(ValueError, match="requires mode and constraint"):
        parse_versions_section({"targets": {"broken": {"pypi": {"name": "x", "registry": "https://pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="at least two"):
        parse_versions_section({"targets": {"broken": {"mode": "aligned", "constraint": "*", "pypi": {"name": "x", "registry": "https://pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="exactly one"):
        parse_versions_section({"targets": {"broken": {"mode": "single", "constraint": "*", "npm": {"name": "x", "registry": "https://registry.npmjs.org"}, "pypi": {"name": "x", "registry": "https://pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="constraint"):
        parse_versions_section({"targets": {"broken": {"mode": "single", "constraint": "latest", "pypi": {"name": "x", "registry": "https://pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="reason is required"):
        parse_versions_section({"targets": {"pin": _pypi_target(version="2.32.0")}}, "root")
    with pytest.raises(ValueError, match="reason requires"):
        parse_versions_section({"targets": {"pin": _pypi_target(reason="hold")}}, "root")
    with pytest.raises(ValueError, match="must be an ISO date"):
        parse_versions_section({"targets": {"pin": _pypi_target(version="2.32.0", reason="hold", expires="tomorrow")}}, "root")


def test_versions_config_loader_maps_bad_policy_and_overlay_to_config_errors(tmp_path):
    root_config, _project_root = _estate(
        tmp_path / "bad-root", root_versions="[versions]\nage_window_days = 0",
    )
    with pytest.raises(SystemExit) as caught:
        config_module.load_forge_config(root_config)
    assert caught.value.code == 2

    with pytest.raises(SystemExit) as caught:
        config_module._parse_versions(
            {"targets": {"incomplete": {"mode": "single"}}},
            "cmru.orchestration.toml [versions]",
        )
    assert caught.value.code == 2

    root_config, _project_root = _estate(
        tmp_path / "overlay",
        root_versions=_versions_table(14),
        project_versions='''[versions.targets."pypi.requests"]
mode = "aligned"''',
    )
    with pytest.raises(SystemExit) as caught:
        config_module.load_forge_config(root_config)
    assert caught.value.code == 2


def test_effective_versions_selection_rejects_unknown_or_invalid_project():
    forge = types.SimpleNamespace(versions={}, projects={})
    with pytest.raises(SystemExit) as caught:
        config_module.effective_versions_for_project(forge, "missing")
    assert caught.value.code == 2

    forge.projects["demo"] = types.SimpleNamespace(versions={"age_window_days": 0})
    with pytest.raises(SystemExit) as caught:
        config_module.effective_versions_for_project(forge, "demo")
    assert caught.value.code == 2


def test_top_level_cli_dispatches_versions_verb(monkeypatch):
    from cmru import cli as cli_module

    calls = []
    monkeypatch.setattr(versions, "main", lambda argv: calls.append(argv) or 7)
    assert cli_module.main(["versions", "check", "--json"]) == 7
    assert calls == [["check", "--json"]]


def test_auth_schema_requires_safe_urls_and_one_credential_shape():
    base = {"mode": "single", "constraint": "*"}
    with pytest.raises(ValueError, match="HTTPS URL"):
        parse_versions_section({"targets": {"bad": {**base, "pypi": {"name": "x", "registry": "http://pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="set together"):
        parse_versions_section({"targets": {"bad": {**base, "pypi": {"name": "x", "registry": "https://pypi.org", "username_env": "USER"}}}}, "root")
    with pytest.raises(ValueError, match="not both"):
        parse_versions_section({"targets": {"bad": {**base, "pypi": {"name": "x", "registry": "https://pypi.org", "token_env": "TOKEN", "username_env": "USER", "password_env": "PASS"}}}}, "root")
    with pytest.raises(ValueError, match="placeholder"):
        parse_versions_section({"targets": {"bad": {"mode": "single", "constraint": "*", "oci": {"image": "ghcr.io/acme/app", "tag": "latest"}}}}, "root")


def test_versions_config_validates_recorded_state_and_custom_output_paths():
    target = _pypi_target()
    target["resolved"] = {
        "version": "2.32.0",
        "resolved_at": "2026-09-24T00:00:00Z",
        "age_cutoff": "2026-09-10T00:00:00Z",
        "sources": {
            "pypi": {
                "version": "2.32.0",
                "released_at": "2026-09-01T00:00:00Z",
                "age_source": "pypi-upload-time",
            },
        },
    }
    output = {
        "template": "templates/constraints.txt.j2",
        "path": "generated/constraints.txt",
        "dated_path": "generated/constraints-{date}.txt",
    }
    parsed = parse_versions_section({"targets": {"requests": target}, "outputs": {"requirements": output}}, "root")
    assert parsed["outputs"]["requirements"]["dated_path"].endswith("{date}.txt")
    assert parsed["targets"]["requests"]["resolved"]["version"] == "2.32.0"

    bad_records = (
        {"resolved_at": "2026-09-24", "age_cutoff": "2026-09-10T00:00:00Z"},
        {"resolved_at": "2026-09-24T00:00:00Z", "age_cutoff": "bad"},
        {
            "resolved_at": "2026-09-24T00:00:00Z",
            "age_cutoff": "2026-09-10T00:00:00Z",
            "sources": {},
        },
    )
    for record in bad_records:
        with pytest.raises(ValueError):
            parse_versions_section({"targets": {"requests": {**_pypi_target(), "resolved": record}}}, "root")
    with pytest.raises(ValueError, match="dated_path must contain"):
        parse_versions_section({"outputs": {"bad": {"template": "a", "path": "b", "dated_path": "c"}}}, "root")
    with pytest.raises(ValueError, match="must stay within"):
        parse_versions_section({"outputs": {"bad": {"template": "../a", "path": "b", "dated_path": "c/{date}"}}}, "root")
    with pytest.raises(ValueError, match="unknown keys"):
        parse_versions_section({"outputs": {"bad": {"template": "a", "path": "b", "dated_path": "c/{date}", "unknown": 1}}}, "root")


def test_versions_config_rejects_malformed_sources_state_and_partial_overlays():
    base = {"mode": "single", "constraint": "*"}
    for source in (None, [], "pypi"):
        with pytest.raises(ValueError, match="must be a table"):
            parse_versions_section({"targets": {"x": {**base, "pypi": source}}}, "root")
    with pytest.raises(ValueError, match="valid registry package name"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {"name": "../x", "registry": "https://pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="without embedded credentials"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {"name": "x", "registry": "https://user:pass@pypi.org"}}}}, "root")
    with pytest.raises(ValueError, match="valid Go module path"):
        parse_versions_section({"targets": {"x": {**base, "go": {"module": "example.com/../bad", "proxy": "https://proxy.example"}}}}, "root")
    with pytest.raises(ValueError, match="fully-qualified registry"):
        parse_versions_section({"targets": {"x": {**base, "oci": {"image": "app", "tag": "v{version}"}}}}, "root")
    with pytest.raises(ValueError, match=r"only the \{version\} placeholder"):
        parse_versions_section({"targets": {"x": {**base, "oci": {"image": "ghcr.io/a/app", "tag": "v{version}-{unknown}"}}}}, "root")
    with pytest.raises(ValueError, match="malformed placeholders"):
        parse_versions_section({"targets": {"x": {**base, "oci": {"image": "ghcr.io/a/app", "tag": "v{{version}}"}}}}, "root")
    with pytest.raises(ValueError, match="environment variable"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {"name": "x", "registry": "https://pypi.org", "token_env": "9TOKEN"}}}}, "root")
    with pytest.raises(ValueError, match="unknown keys"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {"name": "x", "registry": "https://pypi.org", "unexpected": True}}}}, "root")
    with pytest.raises(ValueError, match="must be a table"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {"name": "x", "registry": "https://pypi.org"}, "resolved": []}}}, "root")
    with pytest.raises(ValueError, match="ISO timestamp"):
        parse_versions_section({"targets": {"x": {
            **base, "pypi": {"name": "x", "registry": "https://pypi.org"}, "resolved": {
                "version": "1.0.0", "resolved_at": "never", "age_cutoff": "2026-01-01T00:00:00Z",
                "sources": {"pypi": {"version": "1.0.0", "released_at": "2026-01-01T00:00:00Z", "age_source": "pypi"}},
            },
        }}}, "root")
    with pytest.raises(ValueError, match="timezone"):
        parse_versions_section({"targets": {"x": {
            **base, "pypi": {"name": "x", "registry": "https://pypi.org"}, "resolved": {
                "version": "1.0.0", "resolved_at": "2026-01-01T00:00:00Z", "age_cutoff": "2026-01-01",
                "sources": {"pypi": {"version": "1.0.0", "released_at": "2026-01-01T00:00:00Z", "age_source": "pypi"}},
            },
        }}}, "root")
    with pytest.raises(ValueError, match="valid ISO date"):
        parse_versions_section({"targets": {"pin": _pypi_target(version="2.32.0", reason="hold", expires="2026-02-30")}}, "root")
    with pytest.raises(ValueError, match="expires requires"):
        parse_versions_section({"targets": {"pin": _pypi_target(expires="2026-10-01")}}, "root")
    with pytest.raises(ValueError, match="fully-qualified registry"):
        parse_versions_section(
            {"targets": {"oci.partial": {"oci": {"image": "ghcr.io/../bad"}}}},
            "project", allow_partial=True,
        )

    partial = parse_versions_section(
        {"targets": {"pypi.requests": {"pypi": {"registry": "https://packages.example/simple"}}},
         "outputs": {"custom": {"path": "out.txt"}}},
        "project", allow_partial=True,
    )
    assert partial["targets"]["pypi.requests"]["pypi"]["registry"].endswith("simple")
    assert partial["outputs"]["custom"]["path"] == "out.txt"


def test_versions_config_parser_covers_source_override_state_and_shape_edges():
    assert parse_versions_section(None, "root") == {}
    for raw in ([], "versions"):
        with pytest.raises(ValueError, match="must be a table"):
            parse_versions_section(raw, "root")

    # Partial overlays can supply any source family and the two independently
    # optional OCI fields; the complete merged target is validated later.
    partial = parse_versions_section(
        {"targets": {
            "go.mod": {"go": {"proxy": "https://proxy.example"}},
            "oci.app": {"oci": {"tag": "v{version}-alpine"}},
            "oci.image": {"oci": {"image": "ghcr.io/acme/app"}},
        }},
        "project", allow_partial=True,
    )
    assert partial["targets"]["go.mod"]["go"] == {"proxy": "https://proxy.example"}
    assert partial["targets"]["oci.app"]["oci"]["tag"] == "v{version}-alpine"
    assert partial["targets"]["oci.image"]["oci"]["image"] == "ghcr.io/acme/app"

    # A complete target must supply each required top-level field independently.
    for target in (
        {"mode": "single", "pypi": {"name": "x", "registry": "https://pypi.org"}},
        {"constraint": "*", "pypi": {"name": "x", "registry": "https://pypi.org"}},
    ):
        with pytest.raises(ValueError, match="requires mode and constraint"):
            parse_versions_section({"targets": {"incomplete": target}}, "root")
    aligned = parse_versions_section({"targets": {"matched": {
        "mode": "aligned", "constraint": "*",
        "pypi": {"name": "x", "registry": "https://pypi.org"},
        "npm": {"name": "x", "registry": "https://registry.npmjs.org"},
    }}}, "root")
    assert set(aligned["targets"]["matched"]) == {"mode", "constraint", "pypi", "npm"}

    invalid_targets = (
        ([], "targets must be a table"),
        ({"bad/name": _pypi_target()}, "simple target identifiers"),
        ({"bad": []}, "must be a table"),
        ({"bad": {"mode": "many", "constraint": "*", "pypi": {"name": "x", "registry": "https://pypi.org"}}}, "mode must be one of"),
        ({"bad": {"unexpected": 1}}, "unknown keys"),
        ({"bad": {"mode": "single", "constraint": "*"}}, "at least one source table"),
        ({"bad": {"mode": "single", "constraint": "*", "version": "2.0.0", "reason": "  ", "pypi": {"name": "x", "registry": "https://pypi.org"}}}, "reason must be a non-empty string"),
        ({"bad": {"mode": "single", "constraint": "<2.0.0", "version": "2.0.0", "reason": "hold", "pypi": {"name": "x", "registry": "https://pypi.org"}}}, "does not satisfy constraint"),
    )
    for targets, message in invalid_targets:
        with pytest.raises(ValueError, match=message):
            parse_versions_section({"targets": targets}, "root")

    resolved = {
        "version": "v1.2.3", "resolved_at": "2026-09-24T00:00:00Z",
        "age_cutoff": "2026-09-10T00:00:00+00:00", "provenance": "reviewed override",
        "sources": {"oci": {
            "version": "v1.2.3", "released_at": "2026-09-01T00:00:00Z",
            "age_source": "oci-image-created-fallback", "tag": "v1.2.3-alpine",
        }},
    }
    clean = parse_versions_section({"targets": {"oci.app": {
        "mode": "single", "constraint": "*", "oci": {
            "image": "ghcr.io/acme/app", "tag": "v{version}-alpine",
        }, "resolved": resolved,
    }}}, "root")
    assert clean["targets"]["oci.app"]["resolved"]["provenance"] == "reviewed override"

    bad_states = (
        ({**resolved, "resolved_at": "2026-09-24T00:00:00"}, "resolved_at must include a timezone"),
        ({**resolved, "sources": {}}, "must be a non-empty table"),
        ({**resolved, "sources": {"unknown": {}}}, "must be a supported source table"),
        ({**resolved, "sources": {"oci": {"version": "1.2.3", "released_at": "bad", "age_source": "created"}}}, "released_at must be an ISO timestamp"),
        ({**resolved, "sources": {"oci": {"version": "1.2.3", "released_at": "2026-09-01", "age_source": "created"}}}, "released_at must include a timezone"),
        ({**resolved, "sources": {"oci": {"version": "1.2.3", "released_at": "2026-09-01T00:00:00Z", "age_source": " "}}}, "age_source must be a non-empty string"),
        ({**resolved, "provenance": []}, "provenance must be a string"),
        ({**resolved, "age_cutoff": "bad"}, "age_cutoff must be an ISO timestamp"),
    )
    for record, message in bad_states:
        with pytest.raises(ValueError, match=message):
            parse_versions_section({"targets": {"oci.app": {
                "mode": "single", "constraint": "*", "oci": {
                    "image": "ghcr.io/acme/app", "tag": "v{version}",
                }, "resolved": record,
            }}}, "root")

    invalid_outputs = (
        ([], "outputs must be a table"),
        ({"bad/name": {"template": "a", "path": "b", "dated_path": "c/{date}"}}, "simple output identifiers"),
        ({"bad": []}, "must be a table"),
    )
    for outputs, message in invalid_outputs:
        with pytest.raises(ValueError, match=message):
            parse_versions_section({"outputs": outputs}, "root")


def test_versions_config_auth_fields_and_output_partial_rules():
    base = {"mode": "single", "constraint": "*"}
    with pytest.raises(ValueError, match="must name an environment variable"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {
            "name": "x", "registry": "https://pypi.org", "token_env": "BAD-NAME",
        }}}}, "root")
    with pytest.raises(ValueError, match="use token_env or username_env"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {
            "name": "x", "registry": "https://pypi.org", "token_env": "TOKEN",
            "username_env": "USER", "password_env": "PASS",
        }}}}, "root")
    with pytest.raises(ValueError, match="exactly one"):
        parse_versions_section({"targets": {"x": {**base, "pypi": {
            "name": "x", "registry": "https://pypi.org",
        }, "go": {"module": "example.com/x", "proxy": "https://proxy.example"}}}}, "root")

    partial = parse_versions_section({"outputs": {"out": {}}}, "project", allow_partial=True)
    assert partial["outputs"]["out"] == {}
    partial = parse_versions_section({"outputs": {
        "template-only": {"template": "templates/constraints.j2"},
        "dated-only": {"dated_path": "out/{date}.txt"},
    }}, "project", allow_partial=True)
    assert partial["outputs"]["template-only"] == {"template": "templates/constraints.j2"}
    assert partial["outputs"]["dated-only"] == {"dated_path": "out/{date}.txt"}
    reason_partial = parse_versions_section({"targets": {"pin": {"reason": "under review"}}}, "project", allow_partial=True)
    assert reason_partial["targets"]["pin"]["reason"] == "under review"
    with pytest.raises(ValueError, match="must be a non-empty string"):
        parse_versions_section({"outputs": {"out": {"path": " "}}}, "project", allow_partial=True)
    with pytest.raises(ValueError, match="must contain a valid ISO date"):
        parse_versions_section({"targets": {"pin": {
            **_pypi_target(version="2.32.0", reason="hold"), "expires": "2026-02-30",
        }}}, "root")
    exact = parse_versions_section({"targets": {"pin": {
        **_pypi_target(version="2.32.0", reason="temporary reviewed pin", expires="2026-10-01"),
    }}}, "root")
    assert exact["targets"]["pin"]["version"] == "2.32.0"
    assert exact["targets"]["pin"]["expires"] == "2026-10-01"


@pytest.mark.parametrize("port", [1, 65535])
def test_versions_registry_url_accepts_valid_port_boundaries(port):
    cleaned = parse_versions_section({"targets": {"demo": {
        "mode": "single", "constraint": "*",
        "pypi": {"name": "demo", "registry": f"https://packages.example:{port}"},
    }}}, "root")
    assert cleaned["targets"]["demo"]["pypi"]["registry"] == f"https://packages.example:{port}"


@pytest.mark.parametrize("url", [
    "http://packages.example/simple",
    "https:///simple",
    "https://user@packages.example/simple",
    "https://:password@packages.example/simple",
    "https://packages.example/simple?token=x",
    "https://packages.example/simple?",
    "https://packages.example/simple#fragment",
    "https://packages.example/simple#",
    "https://packages.example:invalid/simple",
    "https://packages.example:/simple",
    "https://packages.example:0/simple",
    "https://packages.example:65536/simple",
    "https://[broken/simple",
])
def test_versions_registry_urls_reject_unsafe_or_malformed_authorities(url):
    target = {
        "mode": "single", "constraint": "*",
        "pypi": {"name": "demo", "registry": url},
    }
    with pytest.raises(
        ValueError, match="HTTPS URL|credentials|query or fragment|valid port|include a host",
    ):
        parse_versions_section({"targets": {"demo": target}}, "root")


def test_versions_auth_pair_is_atomic_but_project_overlays_can_be_partial():
    base = {"mode": "single", "constraint": "*"}
    pypi = {"name": "demo", "registry": "https://packages.example/simple"}
    valid_token = {**base, "pypi": {**pypi, "token_env": "TOKEN"}}
    valid_pair = {**base, "pypi": {**pypi, "username_env": "USER", "password_env": "PASS"}}
    parse_versions_section({"targets": {"token": valid_token, "basic": valid_pair}}, "root")

    for field in ("username_env", "password_env"):
        source = {**pypi, field: "CREDENTIAL"}
        with pytest.raises(ValueError, match="must be set together"):
            parse_versions_section({"targets": {"demo": {**base, "pypi": source}}}, "root")
        overlay = parse_versions_section(
            {"targets": {"demo": {"pypi": {field: "CREDENTIAL"}}}},
            "project", allow_partial=True,
        )
        assert overlay["targets"]["demo"]["pypi"][field] == "CREDENTIAL"

    conflict = {**pypi, "token_env": "TOKEN", "password_env": "PASS"}
    with pytest.raises(ValueError, match="use token_env or"):
        parse_versions_section(
            {"targets": {"demo": {"pypi": conflict}}}, "project", allow_partial=True,
        )


@pytest.mark.parametrize(("family", "source"), [
    ("npm", {"registry": "https://registry.npmjs.org"}),
    ("pypi", {"name": "demo"}),
    ("go", {"module": "example.com/demo"}),
    ("oci", {"image": "ghcr.io/acme/app"}),
])
def test_complete_target_sources_require_all_family_fields(family, source):
    target = {"mode": "single", "constraint": "*", family: source}
    with pytest.raises(ValueError, match="must be a non-empty string"):
        parse_versions_section({"targets": {"demo": target}}, "root")


@pytest.mark.parametrize("tag", ["{{version}", "{version}}", "{other}", "{version}/{version}"])
def test_oci_source_tag_template_requires_one_well_formed_version_placeholder(tag):
    target = {
        "mode": "single", "constraint": "*",
        "oci": {"image": "ghcr.io/acme/app", "tag": tag},
    }
    with pytest.raises(ValueError, match="placeholder"):
        parse_versions_section({"targets": {"image": target}}, "root")


def test_resolve_target_uses_cutoff_alignment_and_expiring_overrides(monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    released = now - timedelta(days=40)
    recent = now - timedelta(days=2)
    table = _pypi_target()
    monkeypatch.setattr(versions, "candidates", lambda *_: {
        "2.31.0": _candidate("2.31.0", released),
        "2.33.0": _candidate("2.33.0", recent),
    })
    selected = versions._resolve_target("requests", table, age_window_days=14, resolved_at=now, owner="root")
    assert selected.version == "2.31.0"
    assert selected.sources["pypi"].released_at == released
    assert selected.override is False

    cutoff = now - timedelta(days=14)
    monkeypatch.setattr(versions, "candidates", lambda *_: {
        "2.32.0": _candidate("2.32.0", cutoff),
    })
    exact_cutoff = versions._resolve_target(
        "requests", table, age_window_days=14, resolved_at=now, owner="root",
    )
    assert exact_cutoff.version == "2.32.0"
    assert exact_cutoff.sources["pypi"].released_at == cutoff

    aligned = {
        "mode": "aligned", "constraint": ">=2.31.0,<3.0.0",
        "pypi": {"name": "requests", "registry": "https://pypi.org"},
        "npm": {"name": "requests", "registry": "https://registry.npmjs.org"},
    }
    pools = {
        "pypi": {"2.31.0": _candidate("2.31.0", released), "2.32.0": _candidate("2.32.0", now - timedelta(days=20))},
        "npm": {"2.31.0": _candidate("2.31.0", released), "2.32.0": _candidate("2.32.0", recent)},
    }
    monkeypatch.setattr(versions, "candidates", lambda family, *_: pools[family])
    aligned_result = versions._resolve_target("aligned", aligned, age_window_days=14, resolved_at=now, owner="root")
    assert aligned_result.version == "2.31.0"
    assert set(aligned_result.sources) == {"pypi", "npm"}

    fresh_override = _pypi_target(version="2.33.0", reason="urgent fix")
    monkeypatch.setattr(versions, "candidate_for", lambda *_: _candidate("2.33.0", recent))
    with pytest.raises(versions.VersionsError, match="set a future expires date"):
        versions._resolve_target("requests", fresh_override, age_window_days=14, resolved_at=now, owner="root")
    fresh_override["expires"] = "2026-09-25"
    pinned = versions._resolve_target("requests", fresh_override, age_window_days=14, resolved_at=now, owner="root")
    assert pinned.override and pinned.reason == "urgent fix"
    fresh_override["expires"] = "2026-09-24"
    with pytest.raises(versions.VersionsError, match="expired"):
        versions._resolve_target("requests", fresh_override, age_window_days=14, resolved_at=now, owner="root")
    fresh_override.pop("expires")
    monkeypatch.setattr(versions, "candidate_for", lambda *_: _candidate("2.33.0", now - timedelta(days=14)))
    boundary_override = versions._resolve_target(
        "requests", fresh_override, age_window_days=14, resolved_at=now, owner="root",
    )
    assert boundary_override.override is True and boundary_override.expires is None


def test_resolve_target_refuses_no_eligible_or_no_aligned_version(monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    fresh = _candidate("2.33.0", now - timedelta(days=1))
    monkeypatch.setattr(versions, "candidates", lambda *_: {"2.33.0": fresh})
    with pytest.raises(versions.VersionsError, match="no age-eligible version"):
        versions._resolve_target("requests", _pypi_target(), age_window_days=14, resolved_at=now, owner="root")
    aligned = {
        "mode": "aligned", "constraint": "*",
        "pypi": {"name": "x", "registry": "https://pypi.org"},
        "npm": {"name": "x", "registry": "https://registry.npmjs.org"},
    }
    pools = {"pypi": {"1.0.0": _candidate("1.0.0", now - timedelta(days=30))}, "npm": {"2.0.0": _candidate("2.0.0", now - timedelta(days=30))}}
    monkeypatch.setattr(versions, "candidates", lambda family, *_: pools[family])
    with pytest.raises(versions.VersionsError, match="no age-eligible version shared"):
        versions._resolve_target("aligned", aligned, age_window_days=14, resolved_at=now, owner="root")

    compatible_time = now - timedelta(days=2)
    incompatible_time = now - timedelta(days=1)
    monkeypatch.setattr(versions, "candidates", lambda *_: {
        "compatible": _candidate("2.32.0", compatible_time),
        "newer-but-incompatible": _candidate("3.0.0", incompatible_time),
    })
    with pytest.raises(versions.VersionsError) as caught:
        versions._resolve_target(
            "requests", _pypi_target(), age_window_days=14, resolved_at=now, owner="root",
        )
    assert versions._timestamp(compatible_time) in str(caught.value)
    assert versions._timestamp(incompatible_time) not in str(caught.value)


def test_target_source_extraction_ignores_unrecognized_or_malformed_tables():
    source = {"name": "demo", "registry": "https://pypi.org"}
    assert versions._target_sources({
        "pypi": source, "extension": {"endpoint": "https://elsewhere"}, "npm": "not a table",
    }) == {"pypi": source}
    with pytest.raises(versions.VersionsError, match="no configured registry sources"):
        versions._resolve_target(
            "empty", {"mode": "single", "constraint": "*", "extension": source},
            age_window_days=14, resolved_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            owner="root",
        )


def test_resolver_refuses_invalid_overrides_registry_states_and_target_shapes(monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    target = _pypi_target(version="2.33.0", reason="fix")
    with pytest.raises(versions.VersionsError, match="violates"):
        versions._resolve_target("requests", {**target, "constraint": "<2.0.0"}, age_window_days=14, resolved_at=now, owner="root")
    monkeypatch.setattr(versions, "candidate_for", lambda *_: (_ for _ in ()).throw(
        registry.RegistryError("registry does not publish exact version '2.33.0'"),
    ))
    with pytest.raises(versions.VersionsError, match="does not publish exact"):
        versions._resolve_target("requests", target, age_window_days=14, resolved_at=now, owner="root")
    monkeypatch.setattr(versions, "candidate_for", lambda *_: _candidate("2.32.9", now - timedelta(days=30)))
    with pytest.raises(versions.VersionsError, match="does not publish exact version"):
        versions._resolve_target("requests", target, age_window_days=14, resolved_at=now, owner="root")
    with pytest.raises(versions.VersionsError, match="no configured registry sources"):
        versions._resolve_target("empty", {"mode": "single", "constraint": "*"}, age_window_days=14, resolved_at=now, owner="root")
    with pytest.raises(versions.VersionsPrerequisiteError, match="offline"):
        monkeypatch.setattr(versions, "candidates", lambda *_: (_ for _ in ()).throw(registry.RegistryError("offline")))
        versions._resolve_target("requests", _pypi_target(), age_window_days=14, resolved_at=now, owner="root")

    monkeypatch.setattr(versions, "candidates", lambda *_: {
        "old": _candidate("3.0.0", now - timedelta(days=30)),
    })
    with pytest.raises(versions.VersionsError, match="registry returned no compatible candidates"):
        versions._resolve_target("requests", _pypi_target(), age_window_days=14, resolved_at=now, owner="root")
    with pytest.raises(versions.VersionsError, match="is not a table"):
        versions._resolve_targets({"bad": "value"}, age_window_days=14, resolved_at=now, owner="root")


def test_registry_metadata_clients_use_registry_timestamps_and_auth(monkeypatch):
    seen = []
    payload = {
        "releases": {
            "1.0.0": [{"upload-time": "2026-01-01T00:00:00Z"}],
            "1.1.0": [{"upload_time_iso_8601": "2026-02-01T00:00:00Z"}],
            "1.2.0": [{"upload_time": "2026-02-15T00:00:00Z"}],
            "2.0.0": [{"upload-time": "2026-03-01T00:00:00Z"}],
        }
    }
    monkeypatch.setattr(registry, "_json", lambda url, headers=None: (seen.append((url, headers)) or (payload, {})))
    pypi = registry.pypi_candidates({"name": "demo_pkg", "registry": "https://packages.example/simple"})
    assert seen[0][0] == "https://packages.example/pypi/demo_pkg/json"
    assert set(pypi) == {"1.0.0", "1.1.0", "1.2.0", "2.0.0"}
    assert pypi["1.1.0"].released_at.month == 2
    assert pypi["1.2.0"].released_at.day == 15
    assert pypi["2.0.0"].age_source == "pypi-upload-time"

    npm_payload = {
        "versions": {"1.0.0": {}, "1.1.0": {"deprecated": "use newer"}},
        "time": {"1.0.0": "2026-01-01T00:00:00Z", "1.1.0": "2026-02-01T00:00:00Z"},
    }
    monkeypatch.setattr(registry, "_json", lambda *_: (npm_payload, {}))
    npm = registry.npm_candidates({"name": "@acme/lib", "registry": "https://registry.npmjs.org"})
    assert list(npm) == ["1.0.0"]
    assert npm["1.0.0"].age_source == "npm-registry-time"

    monkeypatch.setenv("CMRU_TEST_TOKEN", "secret")
    assert registry._auth_headers({"token_env": "CMRU_TEST_TOKEN"}) == {"Authorization": "Bearer secret"}
    assert registry._auth_headers({}) == {}
    monkeypatch.setenv("CMRU_TEST_USER", "alice")
    monkeypatch.setenv("CMRU_TEST_PASS", "pw")
    assert registry._auth_headers({"username_env": "CMRU_TEST_USER", "password_env": "CMRU_TEST_PASS"})["Authorization"].startswith("Basic ")
    monkeypatch.delenv("CMRU_TEST_PASS")
    with pytest.raises(registry.RegistryError, match="CMRU_TEST_PASS"):
        registry._auth_headers({"username_env": "CMRU_TEST_USER", "password_env": "CMRU_TEST_PASS"})
    monkeypatch.setenv("CMRU_TEST_PASS", "pw")
    monkeypatch.delenv("CMRU_TEST_USER")
    with pytest.raises(registry.RegistryError, match="CMRU_TEST_USER"):
        registry._auth_headers({"username_env": "CMRU_TEST_USER", "password_env": "CMRU_TEST_PASS"})
    monkeypatch.delenv("CMRU_TEST_TOKEN")
    with pytest.raises(registry.RegistryError, match="token environment"):
        registry._auth_headers({"token_env": "CMRU_TEST_TOKEN"})


def test_registry_time_parsing_and_http_failures(monkeypatch):
    assert registry._iso_datetime("Thu, 01 Jan 2026 00:00:00 GMT", "fixture").year == 2026
    with pytest.raises(registry.RegistryError, match="without a timezone"):
        registry._iso_datetime("2026-01-01T00:00:00", "fixture")
    with pytest.raises(registry.RegistryError, match="invalid release timestamp"):
        registry._iso_datetime("tomorrow", "fixture")

    class Response:
        headers = {"Last-Modified": "Thu, 01 Jan 2026 00:00:00 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return b"{}"

    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: Response())
    body, headers = registry._request("https://registry.example/meta")
    assert body == b"{}" and headers["last-modified"].endswith("GMT")

    def http_error(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://registry.example", 403, "forbidden", Message(), io.BytesIO(b"no"))

    monkeypatch.setattr(registry, "_urlopen", http_error)
    with pytest.raises(registry.RegistryError, match="HTTP 403"):
        registry._request("https://registry.example/meta")

    def not_found(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://registry.example", 404, "missing", Message(), io.BytesIO(b"no"))

    monkeypatch.setattr(registry, "_urlopen", not_found)
    with pytest.raises(registry.RegistryNotFoundError, match="HTTP 404"):
        registry._request("https://registry.example/meta")
    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    with pytest.raises(registry.RegistryError, match="could not reach registry"):
        registry._request("https://registry.example/meta")

    monkeypatch.setattr(registry, "MAX_METADATA_BYTES", 4)

    class BoundaryResponse(Response):
        def read(self, _limit):
            return b"1234"

    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: BoundaryResponse())
    assert registry._request("https://registry.example/boundary")[0] == b"1234"

    class OversizeResponse(Response):
        def read(self, limit):
            return b"x" * (limit + 1)

    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: OversizeResponse())
    with pytest.raises(registry.RegistryError, match="exceeds"):
        registry._request("https://registry.example/large")
    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("socket")))
    with pytest.raises(registry.RegistryError, match="request failed"):
        registry._request("https://registry.example/oserror")


def test_registry_redirect_policy_keeps_secure_redirects_and_scopes_authorization(monkeypatch):
    handler = registry._RegistryRedirectHandler()
    headers = Message()
    original = registry.urllib.request.Request(
        "https://registry.example:443/v2/blob",
        headers={"Authorization": "Bearer secret", "Accept": "application/octet-stream"},
    )
    original.add_unredirected_header("Authorization", "Bearer unredirected-secret")

    same_origin = handler.redirect_request(
        original, None, 307, "Temporary Redirect", headers,
        "https://REGISTRY.example/blob?signature=abc",
    )
    assert same_origin is not None
    assert same_origin.headers["Authorization"] == "Bearer secret"
    assert "Authorization" not in same_origin.unredirected_hdrs

    cross_origin = handler.redirect_request(
        original, None, 307, "Temporary Redirect", headers,
        "https://cdn.example/blob?signature=abc",
    )
    assert cross_origin is not None
    assert "Authorization" not in cross_origin.headers
    assert "Authorization" not in cross_origin.unredirected_hdrs
    assert cross_origin.get_header("Accept") == "application/octet-stream"
    cross_port = handler.redirect_request(
        original, None, 307, "Temporary Redirect", headers,
        "https://registry.example:444/blob?signature=abc",
    )
    assert cross_port is not None and "Authorization" not in cross_port.headers

    with pytest.raises(registry.RegistryError, match="non-HTTPS"):
        handler.redirect_request(
            original, None, 302, "Found", headers,
            "http://cdn.example/blob",
        )
    with pytest.raises(registry.RegistryError, match="embedded credentials"):
        handler.redirect_request(
            original, None, 302, "Found", headers,
            "https://user:pass@cdn.example/blob",
        )
    with pytest.raises(registry.RegistryError, match="fragment"):
        handler.redirect_request(
            original, None, 302, "Found", headers,
            "https://cdn.example/blob#",
        )
    with pytest.raises(registry.RegistryError, match="invalid port"):
        handler.redirect_request(
            original, None, 302, "Found", headers,
            "https://cdn.example:0/blob",
        )
    for url in ("https://cdn.example:not-a-port/blob", "https://cdn.example:/blob"):
        with pytest.raises(registry.RegistryError, match="invalid port"):
            handler.redirect_request(original, None, 302, "Found", headers, url)
    with pytest.raises(registry.RegistryError, match="invalid URL"):
        handler.redirect_request(
            original, None, 302, "Found", headers,
            "https://[broken/blob",
        )

    assert handler._origin("https://registry.example:1/v2") == ("https", "registry.example", 1)
    assert handler._origin("https://registry.example:65535/v2") == ("https", "registry.example", 65535)

    class FakeOpener:
        def __init__(self, redirect_handler):
            self.redirect_handler = redirect_handler

        def open(self, request, *, timeout):
            assert request is original
            assert timeout == 7
            return "response"

    opened_handlers = []
    def build_opener(redirect_handler):
        opened_handlers.append(redirect_handler)
        return FakeOpener(redirect_handler)

    monkeypatch.setattr(
        registry.urllib.request, "build_opener",
        build_opener,
    )
    assert registry._urlopen(original, timeout=7) == "response"
    assert len(opened_handlers) == 1
    assert isinstance(opened_handlers[0], registry._RegistryRedirectHandler)


def test_oci_response_body_accepts_exact_limit_and_refuses_one_byte_more(monkeypatch):
    monkeypatch.setattr(registry, "MAX_METADATA_BYTES", 4)
    client = registry._OCIClient("https://registry.example", "acme/app", {})

    class Response:
        headers = {}

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: Response(b"1234"))
    assert client._authorized_request("https://registry.example/manifest")[0] == b"1234"
    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: Response(b"12345"))
    with pytest.raises(registry.RegistryError, match="exceeds 4 bytes"):
        client._authorized_request("https://registry.example/manifest")


def test_go_proxy_uses_info_time_and_reports_bad_protocol(monkeypatch):
    def request(url, _headers=None):
        assert url == "https://proxy.golang.org/example.com/!a!c!m!e/mod/@v/list"
        return b"v1.0.0\nv1.1.0\n", {}

    monkeypatch.setattr(registry, "_request", request)

    def info(url, _headers=None):
        version = re.search(r"/@v/([^/]+)\.info$", url).group(1)
        version = version.replace("!", "")
        return {"Version": version, "Time": "2026-01-01T00:00:00Z"}, {}

    monkeypatch.setattr(registry, "_json", info)
    found = registry.go_candidates({"module": "example.com/ACME/mod", "proxy": "https://proxy.golang.org"}, ">=1.0.0")
    assert set(found) == {"1.0.0", "1.1.0"}
    assert found["1.0.0"].age_source == "go-proxy-info-vcs-commit-time"

    monkeypatch.setattr(registry, "_request", lambda *_: (b"\xff", {}))
    with pytest.raises(registry.RegistryError, match="invalid UTF-8"):
        registry.go_candidates({"module": "example.com/mod", "proxy": "https://proxy.golang.org"}, "*")


def test_go_proxy_uses_latest_and_constraint_pseudo_version(monkeypatch):
    seed = "v0.0.0-20250101000000-abcdefabcdef"
    latest = "v0.0.0-20260301000000-bcdefabcdefa"
    source = {"module": "example.com/mod", "proxy": "https://proxy.golang.org"}
    requests = []

    def request(url, _headers=None):
        requests.append(url)
        assert url == "https://proxy.golang.org/example.com/mod/@v/list"
        return b"", {}

    def info(url, _headers=None):
        requests.append(url)
        if url.endswith("/@latest"):
            return {"Version": latest, "Time": "2026-03-01T00:00:00Z"}, {}
        assert url.endswith(f"/@v/{seed}.info")
        return {"Version": seed, "Time": "2025-01-01T00:00:00Z"}, {}

    monkeypatch.setattr(registry, "_request", request)
    monkeypatch.setattr(registry, "_json", info)
    found = registry.go_candidates(source, f">={seed}")
    assert found[registry.normalized_version(seed)].released_at.year == 2025
    assert found[registry.normalized_version(latest)].released_at.year == 2026
    assert "https://proxy.golang.org/example.com/mod/@latest" in requests
    assert registry.candidate_for("go", source, seed).version == seed
    assert registry._go_pseudo_versions_in_constraint(f">={seed},<{latest},!={latest},=={seed}") == {seed}
    assert registry._go_pseudo_versions_in_constraint(f">={seed},") == {seed}

    def missing_seed(url, _headers=None):
        if url.endswith("/@latest"):
            return {"Version": latest, "Time": "2026-03-01T00:00:00Z"}, {}
        raise registry.RegistryNotFoundError("pseudo-version unavailable")

    monkeypatch.setattr(registry, "_json", missing_seed)
    assert set(registry.go_candidates(source, f">={seed}")) == {registry.normalized_version(latest)}

    def no_latest(url, _headers=None):
        if url.endswith("/@latest"):
            raise registry.RegistryNotFoundError("optional latest endpoint unavailable")
        return {"Version": seed, "Time": "2025-01-01T00:00:00Z"}, {}

    monkeypatch.setattr(registry, "_json", no_latest)
    assert set(registry.go_candidates(source, f">={seed}")) == {registry.normalized_version(seed)}

    def same_latest(url, _headers=None):
        return {"Version": seed, "Time": "2025-01-01T00:00:00Z"}, {}

    monkeypatch.setattr(registry, "_json", same_latest)
    selected = registry.go_candidates(source, f">={seed}")
    assert selected[registry.normalized_version(seed)].released_at.year == 2025

    def conflicting_latest(url, _headers=None):
        stamp = "2024-01-01T00:00:00Z" if url.endswith("/@latest") else "2025-01-01T00:00:00Z"
        return {"Version": seed, "Time": stamp}, {}

    monkeypatch.setattr(registry, "_json", conflicting_latest)
    with pytest.raises(registry.RegistryError, match="inconsistent timestamps"):
        registry.go_candidates(source, f">={seed}")

    monkeypatch.setattr(registry, "_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        registry.RegistryNotFoundError("optional latest endpoint unavailable"),
    ))
    monkeypatch.setattr(registry, "_request", lambda *_args, **_kwargs: (b"", {}))
    with pytest.raises(registry.RegistryError, match="does not provide @latest"):
        registry.go_candidates(source, "*")

    monkeypatch.setattr(registry, "_request", lambda *_args, **_kwargs: (b"v1.0.0\n", {}))
    monkeypatch.setattr(registry, "_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        registry.RegistryNotFoundError("listed version metadata missing"),
    ))
    with pytest.raises(registry.RegistryNotFoundError, match="listed version metadata missing"):
        registry.go_candidates(source, ">=1.0.0")


def test_go_proxy_strict_pseudo_version_bound_excludes_seed(monkeypatch):
    seed = "v0.0.0-20250101000000-abcdefabcdef"
    latest = "v0.0.0-20260301000000-bcdefabcdefa"
    source = {"module": "example.com/mod", "proxy": "https://proxy.golang.org"}
    monkeypatch.setattr(registry, "_request", lambda *_args, **_kwargs: (b"", {}))

    def info(url, _headers=None):
        version = latest if url.endswith("/@latest") else seed
        stamp = "2026-03-01T00:00:00Z" if version == latest else "2025-01-01T00:00:00Z"
        return {"Version": version, "Time": stamp}, {}

    monkeypatch.setattr(registry, "_json", info)
    found = registry.go_candidates(source, f">{seed}")
    assert set(found) == {registry.normalized_version(latest)}


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"Version": "1.2.3", "Time": "2026-01-01T00:00:00Z"},
     {"Version": "vnot-semver", "Time": "2026-01-01T00:00:00Z"}],
)
def test_go_latest_refuses_invalid_proxy_metadata(monkeypatch, payload):
    monkeypatch.setattr(registry, "_json", lambda *_args, **_kwargs: (payload, {}))
    with pytest.raises(registry.RegistryError, match="invalid latest-version metadata"):
        registry._go_latest("https://proxy.example", "example.com/mod", {})

    monkeypatch.setattr(registry, "_json", lambda *_args, **_kwargs: ({"Version": "v1.2.3"}, {}))
    with pytest.raises(registry.RegistryError, match="no release timestamp"):
        registry._go_latest("https://proxy.example", "example.com/mod", {})


def test_go_proxy_refuses_invalid_version_metadata_and_candidate_overflow(monkeypatch):
    monkeypatch.setattr(registry, "_request", lambda *_: (b"v1.0.0\n", {}))
    monkeypatch.setattr(registry, "_json", lambda *_: ({"Version": "v2.0.0", "Time": "2026-01-01T00:00:00Z"}, {}))
    with pytest.raises(registry.RegistryError, match="invalid version metadata"):
        registry.go_candidates({"module": "example.com/mod", "proxy": "https://proxy.golang.org"}, "*")

    many = ("\n".join(f"v1.0.{index}" for index in range(registry.MAX_REGISTRY_ITEMS + 1))).encode()
    monkeypatch.setattr(registry, "_request", lambda *_: (many, {}))
    with pytest.raises(registry.RegistryError, match="more than"):
        registry.go_candidates({"module": "example.com/mod", "proxy": "https://proxy.golang.org"}, "*")


def test_go_proxy_allows_candidate_limit_boundary(monkeypatch):
    monkeypatch.setattr(registry, "MAX_REGISTRY_ITEMS", 1)
    monkeypatch.setattr(registry, "_request", lambda *_args, **_kwargs: (b"v1.0.0\n", {}))
    monkeypatch.setattr(registry, "_go_info", lambda *_args, **_kwargs: registry.Candidate(
        "v1.0.0", datetime(2026, 1, 1, tzinfo=timezone.utc), "go-proxy-info-vcs-commit-time",
    ))
    found = registry.go_candidates(
        {"module": "example.com/mod", "proxy": "https://proxy.golang.org"}, "*",
    )
    assert set(found) == {"1.0.0"}


def test_oci_tag_list_rejects_external_pagination_and_created_time_fallback(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    calls = []
    pages = [
        ({"tags": ["v1.0.0"]}, {"link": '</v2/acme/app/tags/list?n=1000&last=v1.0.0>; rel="next"'}),
        ({"tags": ["v1.1.0"]}, {}),
    ]

    def page(url, _headers=None):
        calls.append(url)
        return pages.pop(0)

    monkeypatch.setattr(client, "_json", page)
    assert client.tag_list() == ["v1.0.0", "v1.1.0"]
    assert len(calls) == 2

    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": ["v1.0.0"]}, {"link": '<https://attacker.example/x>; rel="next"'}))
    with pytest.raises(registry.RegistryError, match="unsafe pagination"):
        client.tag_list()

    for link in (
        '</v2/acme/app/manifests/latest>; rel="next"',
        '<https://attacker.example/v2/acme/app/tags/list?n=1000&last=v1.0.0>; rel="next"',
    ):
        client = registry._OCIClient("https://registry.example", "acme/app", {})
        monkeypatch.setattr(client, "_json", lambda *_args, _link=link: (
            {"tags": ["v1.0.0"]}, {"link": _link},
        ))
        with pytest.raises(registry.RegistryError, match="unsafe pagination"):
            client.tag_list()

    client = registry._OCIClient("https://registry.example", "acme/app", {})
    monkeypatch.setattr(client, "_json", lambda *_: (
        {"schemaVersion": 2, "annotations": {"org.opencontainers.image.created": "2026-01-02T00:00:00Z"}},
        {},
    ))
    stamp, source = client.tag_timestamp("v1.0.0")
    assert stamp.day == 2 and source == "oci-image-created-fallback"


def test_oci_tag_listing_accepts_exact_page_and_item_limits(monkeypatch):
    monkeypatch.setattr(registry, "MAX_REGISTRY_ITEMS", 100)
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    calls = 0

    def page(_url, _headers=None):
        nonlocal calls
        calls += 1
        headers = (
            {"link": f'</v2/acme/app/tags/list?n=1000&last=v{calls}>; rel="next"'}
            if calls < 100 else {}
        )
        return {"tags": [f"v{calls}"]}, headers

    monkeypatch.setattr(client, "_json", page)
    tags = client.tag_list()
    assert calls == 100
    assert len(tags) == 100


def test_oci_credentials_do_not_follow_external_bearer_realm(monkeypatch):
    client = registry._OCIClient(
        "https://registry.example", "acme/app",
        {"username_env": "OCI_USER", "password_env": "OCI_PASSWORD"},
    )
    headers = Message()
    headers["WWW-Authenticate"] = 'Bearer realm="https://attacker.example/token",service="registry"'

    def unauthorized(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://registry.example/v2/acme/app/tags/list", 401, "auth", headers, io.BytesIO(b""),
        )

    monkeypatch.setattr(registry, "_urlopen", unauthorized)
    with pytest.raises(registry.RegistryError, match="credential realm.*outside"):
        client._authorized_request("https://registry.example/v2/acme/app/tags/list")


def test_oci_credentials_accept_same_origin_bearer_realm_with_explicit_default_port(monkeypatch):
    client = registry._OCIClient(
        "https://registry.example:443", "acme/app",
        {"username_env": "OCI_USER", "password_env": "OCI_PASSWORD"},
    )
    monkeypatch.setenv("OCI_USER", "user")
    monkeypatch.setenv("OCI_PASSWORD", "password")
    headers = Message()
    headers["WWW-Authenticate"] = 'Bearer realm="https://registry.example/token",service="registry"'
    unauthorized = urllib.error.HTTPError(
        "https://registry.example:443/tags", 401, "auth", headers, io.BytesIO(b""),
    )
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(unauthorized))
    monkeypatch.setattr(registry, "_json", lambda *_a, **_k: ({"token": "token"}, {}))
    monkeypatch.setattr(registry, "_request", lambda *_a, **_k: (b"ok", {}))
    assert client._authorized_request("https://registry.example:443/tags")[0] == b"ok"


def test_oci_bearer_challenge_authenticates_and_validates_token(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    calls = []

    class Unauthorized(urllib.error.HTTPError):
        def __init__(self):
            headers = Message()
            headers["WWW-Authenticate"] = 'Bearer realm="https://registry.example/token",service="registry",scope="repository:acme/app:pull"'
            super().__init__("https://registry.example/tags", 401, "unauthorized", headers, io.BytesIO(b""))

    class Body:
        headers = {"content-type": "application/json"}

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    def urlopen(request, timeout):
        calls.append((request.full_url, request.headers, timeout))
        if request.full_url.endswith("/tags"):
            if len(calls) == 1:
                raise Unauthorized()
            return Body(b'{"tags": ["v1.0.0"]}')
        return Body(b'{"token": "registry-token"}')

    monkeypatch.setattr(registry, "_urlopen", urlopen)
    body, _ = client._authorized_request("https://registry.example/tags")
    assert body == b'{"tags": ["v1.0.0"]}'
    assert calls[1][0].startswith("https://registry.example/token?")
    assert "registry-token" in str(calls[2][1])
    assert registry._parse_bearer_challenge('Basic realm="x"') is None
    assert registry._parse_bearer_challenge('Bearer realm="https://x"')["realm"] == "https://x"


def test_oci_candidates_extract_template_versions_and_reject_empty_tags(monkeypatch):
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda self: ["v1.0.0", "v2.0.0", "latest"])
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda self, tag: (
        datetime(2026, 1, 1, tzinfo=timezone.utc), "oci-registry-last-modified",
    ))
    found = registry.oci_candidates(
        {"image": "ghcr.io/acme/app", "tag": "v{version}"}, ">=1.0.0,<3.0.0",
    )
    assert set(found) == {"1.0.0", "2.0.0"}
    assert registry._oci_identity("docker.io/library/alpine") == ("https://registry-1.docker.io", "library/alpine")
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["latest"])
    with pytest.raises(registry.RegistryError, match="no tags matching"):
        registry.oci_candidates({"image": "ghcr.io/acme/app", "tag": "v{version}"}, "*")


def test_registry_clients_reject_missing_metadata_and_support_registry_suffixes(monkeypatch):
    seen = []
    monkeypatch.setattr(registry, "_json", lambda url, headers=None: (
        seen.append((url, headers)) or ({"releases": {"bad": [], "1.0.0": [{"yanked": True}]}}, {})
    ))
    assert registry.pypi_candidates({"name": "demo", "registry": "https://packages.example/pypi"}) == {}
    assert seen[-1][0] == "https://packages.example/pypi/demo/json"
    assert registry.pypi_candidates({"name": "demo", "registry": "https://packages.example/simple"}) == {}
    assert seen[-1][0] == "https://packages.example/pypi/demo/json"

    monkeypatch.setattr(registry, "_json", lambda *_: ({"releases": {"1.0.0": [{"yanked": False}]}}, {}))
    with pytest.raises(registry.RegistryError, match="no upload timestamp"):
        registry.pypi_candidates({"name": "demo", "registry": "https://pypi.org"})
    monkeypatch.setattr(registry, "_json", lambda *_: ({"projects": {}}, {}))
    with pytest.raises(registry.RegistryError, match="no releases table"):
        registry.pypi_candidates({"name": "demo", "registry": "https://pypi.org"})

    monkeypatch.setattr(registry, "_json", lambda *_: ({"versions": {}, "time": []}, {}))
    with pytest.raises(registry.RegistryError, match="no version timestamps"):
        registry.npm_candidates({"name": "demo", "registry": "https://registry.npmjs.org"})
    monkeypatch.setattr(registry, "_json", lambda *_: ({"versions": [], "time": {}}, {}))
    with pytest.raises(registry.RegistryError, match="no versions table"):
        registry.npm_candidates({"name": "demo", "registry": "https://registry.npmjs.org"})
    monkeypatch.setattr(registry, "_json", lambda *_: ({"versions": {"1.0.0": {}}, "time": {}}, {}))
    with pytest.raises(registry.RegistryError, match="no publication timestamp"):
        registry.npm_candidates({"name": "demo", "registry": "https://registry.npmjs.org"})


def test_registry_http_json_auth_and_dispatch_errors(monkeypatch):
    class Body:
        headers = {"X-Test": "yes"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"{invalid"

    monkeypatch.setattr(registry, "_request", lambda *_: (b"{invalid", {}))
    with pytest.raises(registry.RegistryError, match="invalid JSON"):
        registry._json("https://registry.example/bad")
    monkeypatch.setattr(registry, "_auth_headers", registry._auth_headers)
    with pytest.raises(registry.RegistryError, match="credential environment"):
        registry._auth_headers({"username_env": "MISSING_USER", "password_env": "MISSING_PASS"})
    with pytest.raises(registry.RegistryError, match="unsupported version source"):
        registry.candidates("other", {}, "*")
    monkeypatch.setattr(registry, "candidates", lambda *_: {})
    with pytest.raises(registry.RegistryError, match="does not publish exact"):
        registry.candidate_for("pypi", {}, "1.0.0")
    with pytest.raises(registry.RegistryError, match="must include registry host"):
        registry._oci_identity("missing-host")
    with pytest.raises(registry.RegistryError, match="include registry host and repository"):
        registry._oci_identity("ghcr.io/")
    assert registry._go_escape("ACME") == "!a!c!m!e"


def test_registry_constraints_cover_comparison_and_precision_edges():
    with pytest.raises(registry.RegistryError, match="non-empty string"):
        registry.validate_constraint(" ")
    assert registry.version_satisfies("2.0.0", ">=2.0.0")
    assert registry.version_satisfies("2.0.0", "<=2.0.0")
    assert registry.version_satisfies("2.0.0", ">1.9.9")
    assert registry.version_satisfies("2.0.0", "!=1.0.0")
    assert not registry.version_satisfies("1.0.0", ">=2.0.0")
    assert not registry.version_satisfies("1.0.0", ">2.0.0")
    assert not registry.version_satisfies("3.0.0", "<=2.0.0")
    assert not registry.version_satisfies("1.0.0", "<1.0.0")
    assert registry.version_satisfies("1.9.0", "~1")
    assert not registry.version_satisfies("2.0.0", "~1")
    assert registry.version_satisfies("1.4.9", "~=1.4.5")
    assert registry.version_satisfies("0.0.5", "^0.0.4") is False
    assert registry.version_satisfies("0.0.4", "^0.0.4")
    assert not registry.version_satisfies("1.0.0", "!=1.0.0")
    assert not registry.version_satisfies("1.0.0", "==2.0.0")
    with pytest.raises(registry.RegistryError, match="unsupported version constraint"):
        registry.validate_constraint(">>1.0.0")
    with pytest.raises(registry.RegistryError, match="unsupported version constraint"):
        registry.validate_constraint(">=1.0.0,")
    with pytest.raises(registry.RegistryError, match="expected SemVer"):
        registry.validate_constraint(">=stable")
    with pytest.raises(registry.RegistryError, match="no release timestamp"):
        registry._iso_datetime(" ", "test")


def test_registry_request_timeout_http_excerpt_and_auth_failure(monkeypatch):
    def timed_out(*_args, **_kwargs):
        raise TimeoutError("slow")

    monkeypatch.setattr(registry, "_urlopen", timed_out)
    with pytest.raises(registry.RegistryError, match="timed out"):
        registry._request("https://registry.example/slow")

    headers = Message()
    error = urllib.error.HTTPError(
        "https://registry.example", 500, "bad", headers, io.BytesIO(b"x" * 700),
    )
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(error))
    with pytest.raises(registry.RegistryError, match="HTTP 500") as caught:
        registry._request("https://registry.example/error")
    assert len(str(caught.value).split(": ", 1)[1]) <= 500
    with pytest.raises(registry.RegistryError, match="token environment"):
        registry._auth_headers({"token_env": "CMRU_MISSING_TOKEN"})
    monkeypatch.delenv("CMRU_PYPI_TOKEN", raising=False)
    monkeypatch.setattr(registry, "_auth_headers", lambda _source: {})
    with pytest.raises(registry.RegistryError, match="token environment"):
        registry.pypi_candidates({
            "name": "demo", "registry": "https://pypi.org", "token_env": "CMRU_PYPI_TOKEN",
        })


def test_pypi_and_npm_metadata_skip_invalid_and_yanked_records(monkeypatch):
    monkeypatch.setenv("CMRU_PYPI_TOKEN", "pypi-secret")
    seen = []
    monkeypatch.setattr(registry, "_json", lambda url, headers=None: (
        seen.append((url, headers)) or ({"releases": {
        "bad": [{"upload-time": "2026-01-01T00:00:00Z"}],
        "99.0.0": [{"upload-time": "not a timestamp"}],
        "1.0.1": [],
            "1.0.0": ["not a file", {"yanked": True}],
            "1.1.0": [{"upload_time": "2026-01-01T00:00:00Z"}, {"upload-time": "2026-02-01T00:00:00Z"}],
        }}, {})
    ))
    found = registry.pypi_candidates({
        "name": "demo", "registry": "https://packages.example", "token_env": "CMRU_PYPI_TOKEN",
    }, "<2.0.0")
    assert set(found) == {"1.1.0"}
    assert found["1.1.0"].released_at.month == 2
    assert seen[0][1]["Authorization"].startswith("Basic ")

    monkeypatch.setattr(registry, "_json", lambda *_: ({
        "versions": {
            "bad": {}, "1.0.0": None, "1.1.0": {"deprecated": "gone"},
            "1.2.0": {}, "99.0.0": {},
        },
        "time": {
            "1.0.0": "2026-01-01T00:00:00Z",
            "1.2.0": "2026-02-01T00:00:00Z",
        },
    }, {}))
    found = registry.npm_candidates(
        {"name": "demo", "registry": "https://registry.example"}, "<2.0.0",
    )
    assert set(found) == {"1.0.0", "1.2.0"}


def test_oci_authorized_request_handles_cached_tokens_challenges_and_transport_errors(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})

    class Body:
        headers = {"X-Result": "yes"}

        def __init__(self, data=b"ok"):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.data

    seen = []
    monkeypatch.setattr(registry, "_urlopen", lambda request, timeout: (
        seen.append(request.headers) or Body()
    ))
    assert client._authorized_request("https://registry.example/x")[0] == b"ok"
    assert seen[-1]["User-agent"] == registry.USER_AGENT
    client.token = "cached"
    client._authorized_request("https://registry.example/x")
    assert seen[-1]["Authorization"] == "Bearer cached"

    token_client = registry._OCIClient(
        "https://registry.example", "acme/app", {"token_env": "CMRU_OCI_TOKEN"},
    )
    monkeypatch.setenv("CMRU_OCI_TOKEN", "direct")
    token_client._authorized_request("https://registry.example/x")
    assert seen[-1]["Authorization"] == "Bearer direct"

    oversize = type("Oversize", (Body,), {"read": lambda self, limit: b"x" * (limit + 1)})()
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: oversize)
    with pytest.raises(registry.RegistryError, match="response exceeds"):
        registry._OCIClient("https://registry.example", "acme/app", {})._authorized_request(
            "https://registry.example/large",
        )

    for exception, expected in (
        (urllib.error.URLError("offline"), "could not reach OCI"),
        (TimeoutError("slow"), "timed out"),
        (OSError("socket"), "request failed"),
    ):
        monkeypatch.setattr(registry, "_urlopen", lambda *_a, _e=exception, **_k: (_ for _ in ()).throw(_e))
        with pytest.raises(registry.RegistryError, match=expected):
            registry._OCIClient("https://registry.example", "acme/app", {})._authorized_request(
                "https://registry.example/x",
            )

    challenge = Message()
    challenge["WWW-Authenticate"] = 'Bearer realm="http://registry.example/token"'
    bad_realm = urllib.error.HTTPError(
        "https://registry.example/x", 401, "auth", challenge, io.BytesIO(b""),
    )
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(bad_realm))
    with pytest.raises(registry.RegistryError, match="unsafe bearer-token realm"):
        registry._OCIClient("https://registry.example", "acme/app", {})._authorized_request(
            "https://registry.example/x",
        )


@pytest.mark.parametrize("realm", [
    "https:///token",
    "https://user@registry.example/token",
    "https://:password@registry.example/token",
    "https://registry.example/token#fragment",
    "https://registry.example/token#",
])
def test_oci_bearer_realms_reject_missing_hosts_credentials_and_fragments(monkeypatch, realm):
    headers = Message()
    headers["WWW-Authenticate"] = f'Bearer realm="{realm}"'
    error = urllib.error.HTTPError(
        "https://registry.example/x", 401, "auth", headers, io.BytesIO(b""),
    )
    monkeypatch.setattr(registry, "_urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    with pytest.raises(registry.RegistryError, match="unsafe bearer-token realm"):
        client._authorized_request("https://registry.example/x")


def test_oci_bearer_token_response_failures_and_docker_hub_external_realm(monkeypatch):
    headers = Message()
    headers["WWW-Authenticate"] = 'Bearer realm="https://auth.docker.io/token",service="registry"'
    unauthorized = urllib.error.HTTPError(
        "https://registry-1.docker.io/v2/library/app/tags/list", 401, "auth", headers,
        io.BytesIO(b""),
    )
    seen = []

    class Body:
        headers = {}

        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.data

    def malformed_token(request, timeout):
        seen.append(request.full_url)
        if "tags/list" in request.full_url:
            raise unauthorized
        return Body(b"[]")

    monkeypatch.setattr(registry, "_urlopen", malformed_token)
    docker = registry._OCIClient(
        "https://registry-1.docker.io", "library/app",
        {"username_env": "DOCKER_USER", "password_env": "DOCKER_PASS"},
    )
    monkeypatch.setenv("DOCKER_USER", "user")
    monkeypatch.setenv("DOCKER_PASS", "password")
    with pytest.raises(registry.RegistryError, match="invalid bearer token response"):
        docker._authorized_request("https://registry-1.docker.io/v2/library/app/tags/list")
    assert any("auth.docker.io" in url for url in seen)

    basic_headers = Message()
    basic_headers["WWW-Authenticate"] = 'Basic realm="https://auth.docker.io/token"'
    basic_error = urllib.error.HTTPError(
        "https://registry-1.docker.io/x", 403, "denied", basic_headers, io.BytesIO(b"denied"),
    )
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(basic_error))
    with pytest.raises(registry.RegistryError, match="HTTP 403.*denied"):
        registry._OCIClient("https://registry-1.docker.io", "library/app", {})._authorized_request(
            "https://registry-1.docker.io/x",
        )

    no_body_error = urllib.error.HTTPError(
        "https://registry.example/x", 401, "unauthorized", Message(), None,
    )
    monkeypatch.setattr(registry, "_urlopen", lambda *_a, **_k: (_ for _ in ()).throw(no_body_error))
    with pytest.raises(registry.RegistryError, match="HTTP 401"):
        registry._OCIClient("https://registry.example", "acme/app", {})._authorized_request(
            "https://registry.example/x",
        )

    for payload, message in ((b'{"other":"x"}', "omitted a token"), (b'{"token": 5}', "omitted a token")):
        count = 0

        def token_response(request, timeout):
            nonlocal count
            count += 1
            if count == 1:
                raise unauthorized
            return Body(payload)

        monkeypatch.setattr(registry, "_urlopen", token_response)
        with pytest.raises(registry.RegistryError, match=message):
            registry._OCIClient("https://registry-1.docker.io", "library/app", {})._authorized_request(
                "https://registry-1.docker.io/v2/library/app/tags/list",
            )


def test_oci_json_pagination_and_timestamp_error_edges(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b"[]", {}))
    with pytest.raises(registry.RegistryError, match="non-object"):
        client._json("https://registry.example/data")
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b"{", {}))
    with pytest.raises(registry.RegistryError, match="invalid JSON"):
        client._json("https://registry.example/data")
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b'{"tags": ["v1"]}', {"X-Test": "yes"}))
    assert client._json("https://registry.example/data") == ({"tags": ["v1"]}, {"X-Test": "yes"})

    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": []}, {"link": '<https://registry.example/elsewhere?n=1>; rel="next"'}))
    with pytest.raises(registry.RegistryError, match="unsafe pagination"):
        client.tag_list()
    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": ["v1"]}, {"link": '<http://registry.example/v2/acme/app/tags/list?n=1000>; rel="next"'}))
    with pytest.raises(registry.RegistryError, match="unsafe pagination"):
        client.tag_list()

    for location in (
        "https://user@registry.example/v2/acme/app/tags/list?n=1000",
        "https://registry.example/v2/acme/app/tags/list?n=1000#fragment",
    ):
        monkeypatch.setattr(client, "_json", lambda *_: (
            {"tags": ["v1"]}, {"link": f'<{location}>; rel="next"'},
        ))
        with pytest.raises(registry.RegistryError, match="unsafe pagination"):
            client.tag_list()

    pages = iter([
        ({"tags": ["v1"]}, {"link": '<https://registry.example:443/v2/acme/app/tags/list?n=1000&last=v1>; rel="next"'}),
        ({"tags": ["v2"]}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(pages))
    assert client.tag_list() == ["v1", "v2"]

    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": ["v1"]}, {"link": '</v2/acme/app/tags/list?n=1000&last=v1>; rel="next"'}))
    with pytest.raises(registry.RegistryError, match="exceeded 100 pages"):
        client.tag_list()

    monkeypatch.setattr(client, "_json", lambda *_: ({"manifests": [{}]}, {}))
    with pytest.raises(registry.RegistryError, match="invalid platform descriptor"):
        client.tag_timestamp("v1")
    monkeypatch.setattr(client, "_json", lambda *_: (
        {"manifests": [{"digest": f"sha256:{i}"} for i in range(129)]}, {},
    ))
    with pytest.raises(registry.RegistryError, match="more than 128"):
        client.tag_timestamp("v1")

    manifest = {"manifests": [{"digest": f"sha256:{i}"} for i in range(128)]}
    child_calls = 0
    def child_manifest(_url, _headers=None):
        nonlocal child_calls
        child_calls += 1
        if child_calls == 1:
            return manifest, {}
        return {}, {"last-modified": "Tue, 01 Sep 2026 00:00:00 GMT"}

    monkeypatch.setattr(client, "_json", child_manifest)
    assert client.tag_timestamp("v1")[0] == datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_oci_timestamp_uses_config_blob_and_safest_index_age(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    manifest = {"manifests": [{"digest": "sha256:one", "platform": {"os": "linux"}}]}
    responses = iter([
        (manifest, {}), ({"config": {"digest": "sha256:config"}}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b'{"created":"2026-09-01T00:00:00Z"}', {}))
    stamp, source = client.tag_timestamp("v1")
    assert stamp == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert source == "oci-image-created-fallback"

    # An index annotation is a conservative upper bound when one platform has
    # no timestamp evidence.
    two_platforms = {"manifests": [{"digest": "sha256:one"}, {"digest": "sha256:two"}],
                     "annotations": {"org.opencontainers.image.created": "2026-09-03T00:00:00Z"}}
    responses = iter([
        (two_platforms, {}),
        ({"annotations": {"org.opencontainers.image.created": "2026-09-01T00:00:00Z"}}, {}),
        ({}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    stamp, source = client.tag_timestamp("v1")
    assert stamp.day == 3 and source == "oci-image-created-fallback"

    responses = iter([({"config": {"digest": "sha256:bad"}}, {})])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b"{", {}))
    with pytest.raises(registry.RegistryError, match="config.*invalid JSON"):
        client.tag_timestamp("v1")

    responses = iter([
        ({"manifests": [{"digest": "sha256:one"}]}, {}),
        ({"config": {"digest": "sha256:bad"}}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    with pytest.raises(registry.RegistryError, match="config.*invalid JSON"):
        client.tag_timestamp("v1")

    responses = iter([
        ({"manifests": [{"digest": "sha256:one"}]}, {}),
        ({}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    with pytest.raises(registry.RegistryError, match="neither a registry Last-Modified"):
        client.tag_timestamp("v1")

    for blob, expected in ((b"[]", "neither a registry Last-Modified"), (b"{}", "neither a registry Last-Modified")):
        monkeypatch.setattr(client, "_json", lambda *_: ({"config": {"digest": "sha256:config"}}, {}))
        monkeypatch.setattr(client, "_authorized_request", lambda *_, _blob=blob, **__: (_blob, {}))
        with pytest.raises(registry.RegistryError, match=expected):
            client.tag_timestamp("v1")

    responses = iter([
        ({"manifests": [{"digest": "sha256:one"}, {"digest": "sha256:two"}]}, {}),
        ({"annotations": {"org.opencontainers.image.created": "2026-09-01T00:00:00Z"}}, {}),
        ({"config": {"digest": "sha256:config"}}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b"[]", {}))
    with pytest.raises(registry.RegistryError, match="platform manifests without"):
        client.tag_timestamp("v1")

    responses = iter([
        ({"manifests": [{"digest": "sha256:one"}, {"digest": "sha256:two"}]}, {}),
        ({"annotations": {"org.opencontainers.image.created": "2026-09-01T00:00:00Z"}}, {}),
        ({"config": {"digest": "sha256:config"}}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b"{}", {}))
    with pytest.raises(registry.RegistryError, match="platform manifests without"):
        client.tag_timestamp("v1")

    assert client._created_time({"annotations": {}}) is None


def test_registry_exact_candidate_and_redundant_oci_tags(monkeypatch):
    monkeypatch.setattr(registry, "candidates", lambda *_: {"1.2.3": _candidate(
        "1.2.3", datetime(2026, 1, 1, tzinfo=timezone.utc),
    )})
    assert registry.candidate_for("pypi", {}, "1.2.3").version == "1.2.3"

    original_oci_candidates = registry.oci_candidates
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["1.0.0", "v1.0.0"])
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda _self, tag: (
        datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=tag.startswith("v")),
        "oci-registry-last-modified",
    ))
    found = original_oci_candidates(
        {"image": "ghcr.io/acme/app", "tag": "{version}"}, "*",
    )
    assert found["1.0.0"].tag == "v1.0.0"

    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["v1.0.0", "1.0.0"])
    stamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda _self, _tag: (
        stamp, "oci-registry-last-modified",
    ))
    tied = original_oci_candidates({"image": "ghcr.io/acme/app", "tag": "{version}"}, "*")
    assert tied["1.0.0"].tag == "v1.0.0"


def test_registry_dispatch_and_oci_candidate_limit(monkeypatch):
    original_oci_candidates = registry.oci_candidates
    for family, function in (
        ("pypi", "pypi_candidates"), ("npm", "npm_candidates"),
        ("go", "go_candidates"), ("oci", "oci_candidates"),
    ):
        monkeypatch.setattr(registry, function, lambda *args, _family=family: {_family: args})
        assert registry.candidates(family, {"x": "y"}, "*")
    monkeypatch.setattr(registry, "MAX_REGISTRY_ITEMS", 1)
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["v1.0.0"])
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda *_: (
        datetime(2026, 9, 1, tzinfo=timezone.utc), "oci-registry-last-modified",
    ))
    assert set(original_oci_candidates({"image": "ghcr.io/acme/app", "tag": "v{version}"}, "*")) == {"1.0.0"}
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["v1.0.0", "v2.0.0"])
    with pytest.raises(registry.RegistryError, match="too many matching tags"):
        original_oci_candidates({"image": "ghcr.io/acme/app", "tag": "v{version}"}, "*")


def test_oci_timestamp_paths_cover_registry_platform_and_config_fallbacks(monkeypatch):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    monkeypatch.setattr(client, "_json", lambda *_: ({"schemaVersion": 2}, {"last-modified": "Tue, 01 Sep 2026 00:00:00 GMT"}))
    assert client.tag_timestamp("v1.0.0") == (now, "oci-registry-last-modified")

    monkeypatch.setattr(client, "_json", lambda *_: ({"annotations": {"org.opencontainers.image.created": "2026-09-01T00:00:00Z"}}, {}))
    stamp, source = client.tag_timestamp("v1.0.0")
    assert stamp == now and source == "oci-image-created-fallback"

    monkeypatch.setattr(client, "_json", lambda *_: ({"config": {"digest": "sha256:abc"}}, {}))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b'{"created":"2026-09-01T00:00:00Z"}', {}))
    assert client.tag_timestamp("v1.0.0") == (now, "oci-image-created-fallback")

    manifest = {"manifests": [{"digest": "sha256:one"}, {"digest": "sha256:two"}]}
    responses = iter([
        (manifest, {}),
        ({"annotations": {"org.opencontainers.image.created": "2026-08-01T00:00:00Z"}}, {}),
        ({}, {"last-modified": "Tue, 01 Sep 2026 00:00:00 GMT"}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(responses))
    stamp, source = client.tag_timestamp("v1.0.0")
    assert stamp == now and source == "oci-image-created-fallback"

    incomplete = iter([
        ({"manifests": [{"digest": "sha256:one"}, {"digest": "sha256:two"}]}, {}),
        ({"annotations": {"org.opencontainers.image.created": "2026-08-01T00:00:00Z"}}, {}),
        ({}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(incomplete))
    with pytest.raises(registry.RegistryError, match="platform manifests without"):
        client.tag_timestamp("v1.0.0")

    monkeypatch.setattr(client, "_json", lambda *_: ({"config": {}}, {}))
    with pytest.raises(registry.RegistryError, match="neither a registry Last-Modified"):
        client.tag_timestamp("v1.0.0")

    monkeypatch.setattr(client, "_json", lambda *_: ({
        "annotations": {"org.opencontainers.image.created": ""},
        "config": {"digest": "sha256:config"},
    }, {}))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (
        b'{"created":"2026-09-01T00:00:00Z"}', {},
    ))
    assert client.tag_timestamp("v1.0.0") == (now, "oci-image-created-fallback")

    child_without_created = iter([
        ({
            "manifests": [{"digest": "sha256:child"}],
            "annotations": {"org.opencontainers.image.created": "2026-09-01T00:00:00Z"},
        }, {}),
        ({"config": {"digest": "sha256:config"}}, {}),
    ])
    monkeypatch.setattr(client, "_json", lambda *_: next(child_without_created))
    monkeypatch.setattr(client, "_authorized_request", lambda *_: (b'{"created":""}', {}))
    assert client.tag_timestamp("v1.0.0") == (now, "oci-image-created-fallback")
    assert client._created_time({"annotations": {"org.opencontainers.image.created": ""}}) is None


def test_oci_index_metadata_rejects_malformed_manifests_field(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    monkeypatch.setattr(client, "_json", lambda *_: ({"manifests": "not-an-array"}, {}))
    with pytest.raises(registry.RegistryError, match="invalid platform manifest list"):
        client.tag_timestamp("v1.0.0")


def test_oci_tag_listing_rejects_bad_pages_and_created_metadata(monkeypatch):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": "bad"}, {}))
    with pytest.raises(registry.RegistryError, match="invalid tag list"):
        client.tag_list()
    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": []}, {}))
    with pytest.raises(registry.RegistryError, match="no tags"):
        client.tag_list()
    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": ["v1"] * (registry.MAX_REGISTRY_ITEMS + 1)}, {}))
    with pytest.raises(registry.RegistryError, match="more than"):
        client.tag_list()
    monkeypatch.setattr(client, "_json", lambda *_: ({"tags": ["v1"]}, {"link": '</elsewhere?n=1>; rel="next"'}))
    with pytest.raises(registry.RegistryError, match="unsafe pagination"):
        client.tag_list()
    assert client._created_time({}) is None
    assert client._created_time({"annotations": {"org.opencontainers.image.created": "2026-09-01T00:00:00Z"}}) is not None


@pytest.mark.parametrize("payload", [{}, {"tags": None}, {"tags": "bad"}])
def test_oci_tag_listing_refuses_pages_without_a_string_array(monkeypatch, payload):
    client = registry._OCIClient("https://registry.example", "acme/app", {})
    monkeypatch.setattr(client, "_json", lambda *_: (payload, {}))
    with pytest.raises(registry.RegistryError, match="invalid tag list"):
        client.tag_list()


def test_oci_candidates_handle_version_spelling_duplicates(monkeypatch):
    monkeypatch.setattr(registry, "as_completed", lambda futures: list(futures))
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["1.0.0", "v1.0.0", "bad"])
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda _self, tag: (
        datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=1 if tag.startswith("v") else 0),
        "oci-registry-last-modified",
    ))
    candidates = registry.oci_candidates(
        {"image": "ghcr.io/acme/app", "tag": "{version}"}, "*",
    )
    assert candidates["1.0.0"].tag == "v1.0.0"

    timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(registry, "as_completed", lambda futures: list(futures))
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["v1.0.0", "1.0.0"])
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda *_: (
        timestamp, "oci-registry-last-modified",
    ))
    candidates = registry.oci_candidates(
        {"image": "ghcr.io/acme/app", "tag": "{version}"}, "*",
    )
    assert candidates["1.0.0"].tag == "v1.0.0"
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["v1.0.0", "1.0.0"])
    monkeypatch.setattr(registry._OCIClient, "tag_timestamp", lambda _self, tag: (
        datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=tag == "v1.0.0"),
        "oci-registry-last-modified",
    ))
    candidates = registry.oci_candidates(
        {"image": "ghcr.io/acme/app", "tag": "{version}"}, "*",
    )
    assert candidates["1.0.0"].tag == "v1.0.0"
    monkeypatch.setattr(registry._OCIClient, "tag_list", lambda _self: ["vlatest"])
    with pytest.raises(registry.RegistryError, match="no tags matching"):
        registry.oci_candidates({"image": "ghcr.io/acme/app", "tag": "v{version}"}, "*")


def test_versions_toml_writer_preserves_other_tables_and_transaction_rolls_back(tmp_path):
    text = "schema_version = 1\n\n[project]\nid = \"demo\"\n\n[versions]\nage_window_days = 14\n\n[versions.targets.old]\nmode = \"single\"\n\n[steps.test]\nquiet = true\n"
    new = {"age_window_days": 21, "targets": {"pypi.requests": _pypi_target()}}
    updated = versions._replace_versions_region(text, new)
    parsed = tomllib.loads(updated)
    assert parsed["project"]["id"] == "demo"
    assert parsed["steps"]["test"]["quiet"] is True
    assert parsed["versions"]["targets"]["pypi.requests"]["pypi"]["name"] == "requests"
    assert versions._replace_versions_region("schema_version = 1\n", new).endswith("[versions.targets.\"pypi.requests\".pypi]\nname = \"requests\"\nregistry = \"https://pypi.org\"\n")
    assert versions._replace_versions_region(updated, {}) == "schema_version = 1\n\n[project]\nid = \"demo\"\n\n[steps.test]\nquiet = true\n"

    existing = tmp_path / "existing.txt"
    created = tmp_path / "created.txt"
    existing.write_text("before", encoding="utf-8")
    transaction = versions._FileTransaction()
    transaction.track(existing)
    transaction.track(created)
    existing.write_text("after", encoding="utf-8")
    created.write_text("partial", encoding="utf-8")
    transaction.rollback()
    assert existing.read_text(encoding="utf-8") == "before"
    assert not created.exists()


def test_versions_file_read_render_and_atomic_failure_edges(monkeypatch, tmp_path):
    with pytest.raises(versions.VersionsError, match="could not read"):
        versions._load_document(tmp_path / "missing.toml")
    malformed = tmp_path / "malformed.toml"
    malformed.write_text("[broken\n", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not read"):
        versions._load_document(malformed)

    assert versions._toml_key("simple_key-1") == "simple_key-1"
    assert versions._toml_key("not a key") == '"not a key"'
    assert versions._toml_value(False) == "false"
    assert versions._toml_value(3) == "3"
    assert versions._toml_value("quoted\"value") == '"quoted\\\"value"'
    unicode_text = "résolu par l’équipe"
    assert versions._toml_value(unicode_text) == '"résolu par l’équipe"'
    assert tomllib.loads("value = " + versions._toml_value(unicode_text))["value"] == unicode_text
    assert versions._toml_value([True, "x"]) == '[true, "x"]'
    with pytest.raises(versions.VersionsError, match="cannot render TOML"):
        versions._toml_value({"nested": True})
    assert not versions._render_versions_toml({})
    assert versions._render_versions_toml({"empty": {}}).startswith("[versions]\n")
    assert "[versions.targets.\"pypi.requests\"]" in versions._render_versions_toml(
        {"targets": {"pypi.requests": {"mode": "single"}}},
    )
    assert not versions._header_is_versions("[[array.table]]")
    assert not versions._header_is_versions("[invalid")
    assert versions._header_is_versions('["versions".targets]')
    assert versions._replace_versions_region("", {"age_window_days": 14}).startswith("[versions]")
    source = "schema_version = 1\n\n[steps.run]\ncommands = []\n"
    replaced = versions._replace_versions_region(source, {"age_window_days": 14})
    assert tomllib.loads(replaced)["versions"]["age_window_days"] == 14
    assert versions._replace_versions_region(source, {}) == source
    valid = tmp_path / "valid.toml"
    valid.write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="did not return a table"):
        versions._update_versions_file(valid, lambda _versions: None)
    no_op = tmp_path / "no-op.toml"
    no_op.write_text("schema_version = 1\n", encoding="utf-8")
    versions._update_versions_file(no_op, lambda existing: existing)

    target = tmp_path / "atomic.txt"
    target.write_text("before", encoding="utf-8")
    real_replace = versions.os.replace

    def failed_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(versions.os, "replace", failed_replace)
    with pytest.raises(OSError, match="replace failed"):
        versions._write_text_atomic(target, "after")
    assert target.read_text(encoding="utf-8") == "before"
    monkeypatch.setattr(versions.os, "replace", real_replace)
    versions._write_text_atomic(target, "after")
    assert target.read_text(encoding="utf-8") == "after"
    nested_target = tmp_path / "created" / "nested" / "output.txt"
    versions._write_text_atomic(nested_target, "nested")
    assert nested_target.read_text(encoding="utf-8") == "nested"

    partial_config = tmp_path / "partial.toml"
    partial_config.write_text("schema_version = 1\n", encoding="utf-8")
    versions._update_versions_file(partial_config, lambda _old: {
        "targets": {"pypi.demo": {"pypi": {"registry": "https://packages.example/simple"}}},
    })
    assert tomllib.loads(partial_config.read_text(encoding="utf-8"))["versions"]["targets"]["pypi.demo"]["pypi"] == {
        "registry": "https://packages.example/simple",
    }


def test_file_transaction_refuses_symlinks_and_restores_missing_and_existing_paths(tmp_path):
    existing = tmp_path / "existing"
    existing.write_text("before", encoding="utf-8")
    existing.chmod(0o640)
    absent = tmp_path / "absent"
    transaction = versions._FileTransaction()
    transaction.track(existing)
    transaction.track(existing)
    transaction.track(absent)
    existing.write_text("after", encoding="utf-8")
    absent.write_text("created", encoding="utf-8")
    transaction.rollback()
    assert existing.read_text(encoding="utf-8") == "before"
    assert existing.stat().st_mode & 0o777 == 0o640
    assert not absent.exists()
    transaction_missing = versions._FileTransaction()
    transaction_missing.track(absent)
    transaction_missing.rollback()
    link = tmp_path / "link"
    link.symlink_to(existing)
    with pytest.raises(versions.VersionsError, match="symlink output"):
        versions._FileTransaction().track(link)
    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(versions.VersionsError, match="non-file output"):
        versions._FileTransaction().track(directory)
    nested_parent = tmp_path / "nested" / "output"
    nested_parent.mkdir(parents=True)
    nested_file = nested_parent / "generated.txt"
    nested_file.write_text("before", encoding="utf-8")
    nested_transaction = versions._FileTransaction()
    nested_transaction.track(nested_file)
    nested_file.write_text("after", encoding="utf-8")
    nested_file.unlink()
    nested_parent.rmdir()
    nested_parent.parent.rmdir()
    nested_transaction.rollback()
    assert nested_file.read_text(encoding="utf-8") == "before"
    bad_result = _candidate("not-semver", datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(versions.VersionsError, match="unsupported version spelling"):
        versions._version_key(bad_result)


def test_version_file_cleanup_errors_and_section_replacement_edges(monkeypatch, tmp_path):
    assert not versions._header_is_versions("[bad = value]")
    path = tmp_path / "config.toml"
    path.write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="unknown keys"):
        versions._update_versions_file(path, lambda _raw: {"not_a_key": True})

    real_replace = versions.os.replace
    real_unlink = versions.os.unlink
    temporary_paths = []
    real_mkstemp = versions.tempfile.mkstemp

    def remember_temp(*args, **kwargs):
        fd, name = real_mkstemp(*args, **kwargs)
        temporary_paths.append(name)
        return fd, name

    monkeypatch.setattr(versions.tempfile, "mkstemp", remember_temp)
    monkeypatch.setattr(versions.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace")))
    monkeypatch.setattr(versions.os, "unlink", lambda *_args: (_ for _ in ()).throw(OSError("unlink")))
    with pytest.raises(OSError, match="replace"):
        versions._write_text_atomic(path, "updated")
    monkeypatch.setattr(versions.os, "replace", real_replace)
    monkeypatch.setattr(versions.os, "unlink", real_unlink)
    for temporary in temporary_paths:
        if Path(temporary).exists():
            real_unlink(temporary)

    existing = tmp_path / "existing"
    existing.write_text("before", encoding="utf-8")
    transaction = versions._FileTransaction()
    transaction.track(existing)
    existing.write_text("after", encoding="utf-8")
    monkeypatch.setattr(versions.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("rollback replace")))
    monkeypatch.setattr(versions.os, "unlink", lambda *_args: (_ for _ in ()).throw(OSError("rollback unlink")))
    with pytest.raises(OSError, match="rollback replace"):
        transaction.rollback()
    monkeypatch.setattr(versions.os, "replace", real_replace)
    monkeypatch.setattr(versions.os, "unlink", real_unlink)
    for temporary in tmp_path.glob(".existing.rollback.*"):
        real_unlink(temporary)


def test_project_selection_declarations_and_recorded_fallbacks(monkeypatch, tmp_path):
    p1 = types.SimpleNamespace(versions={"targets": {"x": {"pypi": {"registry": "https://pypi.org"}}}}, project_root=tmp_path / "p1")
    p2 = types.SimpleNamespace(versions={"targets": {"x": {"pypi": {"name": "x"}}, "y": {"mode": "single"}}}, project_root=None)
    root_target = {"mode": "single", "constraint": "*", "pypi": {"name": "x", "registry": "https://root.example"}}
    forge = types.SimpleNamespace(
        projects={"p1": p1, "p2": p2},
        versions={"targets": {"x": root_target}},
        orchestration=types.SimpleNamespace(project_order=["p2", "p1"], project_configs={"p2": tmp_path / "p2" / "cmru.toml"}),
        repo_root=tmp_path,
    )
    assert versions._project_root(forge, "p1") == (tmp_path / "p1").resolve()
    assert versions._project_root(forge, "p2") == (tmp_path / "p2").resolve()
    assert versions._project_root(types.SimpleNamespace(
        projects={"p": types.SimpleNamespace(project_root=None)}, orchestration=None, repo_root=tmp_path,
    ), "p") == tmp_path.resolve()
    assert versions._project_root(types.SimpleNamespace(
        projects={"p": types.SimpleNamespace(project_root=None)},
        orchestration=types.SimpleNamespace(project_configs={}), repo_root=tmp_path,
    ), "p") == tmp_path.resolve()
    assert versions._local_target_overlays(forge, "p1")["x"]["pypi"]["name"] == "x"
    assert versions._target_declarations_for_project(forge, "p1")["x"]["pypi"]["registry"] == "https://pypi.org"
    p2.versions = {"targets": "bad"}
    assert versions._local_target_overlays(forge, "p2") == {}
    assert versions._target_declarations_for_project(forge, "p2")["x"] == root_target
    p1.versions = {"targets": {"ignored": "not a table"}}
    assert versions._local_target_overlays(forge, "p1") == {}
    forge.versions["targets"] = "bad"
    assert versions._target_declarations_for_project(forge, "p1") == {}

    ctx_project = types.SimpleNamespace(config_kind="project", project_name="p1", scope="project")
    ctx_estate = types.SimpleNamespace(config_kind="orchestration", project_name=None, scope="estate")
    selected_calls = []
    monkeypatch.setattr(versions, "select_target_names", lambda *args, **kwargs: (
        selected_calls.append((args, kwargs)) or list(args[2])
    ))
    assert versions._selected_projects(forge, ctx_project, None) == ["p1", "p2"]
    assert selected_calls[-1][0][2] == ["p1", "p2"]
    assert selected_calls[-1][1]["estate_scope"] is False
    assert versions._selected_projects(forge, ctx_estate, None) == ["p2", "p1"]
    assert selected_calls[-1][0][2] == ["p2", "p1"]
    assert selected_calls[-1][1]["estate_scope"] is True
    forge.orchestration = None
    standalone_context = types.SimpleNamespace(config_kind="standalone", project_name="p1", scope="project")
    assert versions._selected_projects(forge, standalone_context, None) == ["p1", "p2"]
    monkeypatch.setattr(versions, "select_target_names", lambda *_a, **_k: (_ for _ in ()).throw(versions.TargetSelectionError("bad target")))
    with pytest.raises(versions.VersionsError, match="bad target"):
        versions._selected_projects(forge, ctx_estate, "missing")

    forge.versions["targets"] = {"x": root_target}
    assert versions._recorded_target(forge, "p1", "x", "pypi") is None
    forge.versions["targets"]["x"]["resolved"] = {"sources": {"pypi": {"version": "2.0.0"}}}
    assert versions._recorded_target(forge, "p1", "x", "pypi") == "2.0.0"
    forge.versions["targets"]["x"]["resolved"] = {"sources": {"pypi": "bad source record"}}
    assert versions._recorded_target(forge, "p1", "x", "pypi") is None
    forge.versions["targets"]["x"]["resolved"] = {"sources": {"pypi": {"version": 7}}}
    assert versions._recorded_target(forge, "p1", "x", "pypi") is None
    forge.versions["targets"]["x"]["resolved"] = {"sources": {"pypi": {"version": "2.0.0"}}}
    p1.versions = {"targets": {"x": {"resolved": {"sources": {"pypi": {"version": "2.1.0"}}}}}}
    assert versions._recorded_target(forge, "p1", "x", "pypi") == "2.1.0"
    forge.versions["targets"] = []
    p1.versions = {"targets": []}
    recorded = versions._recorded_versions(
        forge, {}, {"p1": {"x": _result("x", "pypi", "2.0.0", datetime(2026, 9, 1, tzinfo=timezone.utc))}},
        {"p1": {"x": {"mode": "single", "constraint": "*"}}},
    )
    assert recorded[0]["recorded_version"] is None


def test_versions_for_project_and_report_helpers(monkeypatch):
    forge = types.SimpleNamespace()
    monkeypatch.setattr(versions, "effective_versions_for_project", lambda *_: {"age_window_days": 5})
    assert versions._versions_for_project(forge, "demo") == {"age_window_days": 5}
    monkeypatch.setattr(versions, "effective_versions_for_project", lambda *_: (_ for _ in ()).throw(SystemExit(2)))
    with pytest.raises(versions.VersionsError, match="could not construct effective version config"):
        versions._versions_for_project(forge, "demo")

    assert versions._render_report([]).startswith("No version targets")
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    row = versions._report_record(_result("requests", "pypi", "2.32.0", now), {"mode": "single", "constraint": "*"})
    assert row["status"] == "unresolved" and row["recorded_version"] is None
    row["sources"]["pypi"]["tag"] = "v2.32.0"
    report = versions._render_report([row])
    assert "AGE SOURCE" in report
    assert "—" in report


def test_apply_state_age_policy_and_native_output_collision_edges(tmp_path, monkeypatch):
    config = tmp_path / "cmru.toml"
    config.write_text("schema_version = 1\n", encoding="utf-8")
    result = _result("requests", "pypi", "2.32.0", datetime(2026, 9, 1, tzinfo=timezone.utc))
    with pytest.raises(versions.VersionsError, match="not declared"):
        versions._apply_resolved_state(config, {"requests": result})
    with pytest.raises(versions.VersionsError, match="positive integer"):
        versions._age_window({"age_window_days": True})
    with pytest.raises(versions.VersionsError, match="positive integer"):
        versions._age_window({"age_window_days": 0})
    malformed = tmp_path / "malformed-targets.toml"
    malformed.write_text('schema_version = 1\n[versions]\ntargets = "bad"\n', encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="targets is not a table"):
        versions._apply_resolved_state(malformed, {"requests": result})
    with pytest.raises(versions.VersionsError, match="multiple version outputs"):
        versions._assert_unique_output_paths([(tmp_path / "out", "a"), (tmp_path / "sub" / ".." / "out", "b")])

    template = tmp_path / "missing.j2"
    with pytest.raises(versions.VersionsError, match="template does not exist"):
        versions._template_artifacts(
            tmp_path, {"x": {"template": "missing.j2", "path": "out", "dated_path": "out-{date}"}},
            {}, "20260924",
        )

    declaration = {"oci.app": {"oci": {"image": "ghcr.io/acme/app"}}}
    result = _result("oci.app", "oci", "1.0.0", datetime(2026, 9, 1, tzinfo=timezone.utc), tag="v1.0.0")
    files = versions._native_output_files(
        tmp_path, {"oci.app": result}, declaration, now=result.resolved_at,
        outputs={}, oci_results={"oci.app": result},
    )
    stable = tmp_path / "versions" / "oci-images.json"
    stable.parent.mkdir()
    stable.write_text("not json", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="unrecognized OCI output"):
        versions._native_output_files(
            tmp_path, {"oci.app": result}, declaration, now=result.resolved_at,
            outputs={}, oci_results={"oci.app": result},
        )

    stable.write_text('{"generated_by": "cmru versions resolve"}\n', encoding="utf-8")
    assert versions._native_output_files(
        tmp_path, {"oci.app": result}, declaration, now=result.resolved_at, outputs={},
    )
    pypi_result = versions.TargetResult(
        target_id="multi", version="1.0.0", resolved_at=result.resolved_at,
        age_cutoff=result.age_cutoff,
        sources={
            "pypi": _candidate("1.0.0", result.resolved_at),
            "oci": registry.Candidate("1.0.0", result.resolved_at, "fixture", "v1.0.0"),
        }, override=False, reason=None, expires=None, owner="demo",
    )
    pypi_only = versions.TargetResult(
        target_id="pypi-only", version="1.0.0", resolved_at=result.resolved_at,
        age_cutoff=result.age_cutoff,
        sources={"pypi": _candidate("1.0.0", result.resolved_at)},
        override=False, reason=None, expires=None, owner="demo",
    )
    multi_files = versions._native_output_files(
        tmp_path, {"multi": pypi_result},
        {
            "multi": {"pypi": "malformed", "oci": {"image": "ghcr.io/acme/multi"}},
            "pypi-only": {"pypi": {"name": "example"}},
        },
        now=result.resolved_at, outputs={},
        oci_results={"multi": pypi_result, "pypi-only": pypi_only},
    )
    assert {path.name for path, _ in multi_files} == {"oci-images-20260924.json", "oci-images.json"}


def test_resolve_context_and_manifest_compatibility_paths(monkeypatch, tmp_path):
    context = types.SimpleNamespace(config_path=tmp_path / "cmru.toml")
    forge = types.SimpleNamespace()
    calls = []
    monkeypatch.setattr(versions, "resolve_invocation_context", lambda path: calls.append(path) or context)
    monkeypatch.setattr(versions, "load_forge_config", lambda path: calls.append(path) or forge)
    found_context, found_forge = versions._resolve_context("~/cmru.toml")
    assert found_context is context and found_forge is forge
    assert calls[0] == Path("~/cmru.toml").expanduser()

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "requirements.in").write_text("requests>=2.0.0,<3.0.0\n", encoding="utf-8")
    target_result = _result("pypi.requests", "pypi", "1.0.0", datetime(2026, 9, 1, tzinfo=timezone.utc))
    declaration = {"pypi.requests": {"pypi": {"name": "requests", "registry": "https://pypi.org"}}}
    with pytest.raises(versions.VersionsError, match="manifest requires"):
        versions._validate_manifest_target_compatibility(
            project_root, {"pypi.requests": target_result}, declaration,
        )
    versions._validate_manifest_target_compatibility(project_root, {"pypi.requests": target_result}, {})
    versions._validate_manifest_target_compatibility(
        project_root, {"pypi.requests": target_result},
        {"pypi.requests": {"pypi": {"name": "different", "registry": "https://pypi.org"}}},
    )
    versions._validate_manifest_target_compatibility(
        project_root,
        {"pypi.requests": _result("pypi.requests", "npm", "1.0.0", target_result.resolved_at)},
        declaration,
    )
    versions._validate_manifest_target_compatibility(
        project_root, {"pypi.requests": target_result},
        {"pypi.requests": {"pypi": {"registry": "https://pypi.org"}}},
    )

    go_project = tmp_path / "go-project"
    go_project.mkdir()
    (go_project / "go.mod").write_text(
        "module example.com/demo\nrequire example.com/lib v1.2.0\n", encoding="utf-8",
    )
    go_result = _result(
        "go.example.com.lib", "go", "v1.0.0", datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(versions.VersionsError, match="manifest requires"):
        versions._validate_manifest_target_compatibility(
            go_project, {"go.example.com.lib": go_result},
            {"go.example.com.lib": {"go": {
                "module": "example.com/lib", "proxy": "https://proxy.golang.org",
            }}},
        )


def test_native_results_choose_manifest_matched_and_project_local_sources(tmp_path):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "requirements.in").write_text("python-pkg>=1.0.0\n", encoding="utf-8")
    (project_root / "package.json").write_text(json.dumps({"dependencies": {"js-pkg": "^1"}}), encoding="utf-8")
    (project_root / "go.mod").write_text("module example.com/demo\nrequire example.com/lib v1.0.0\n", encoding="utf-8")
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    targets = {
        "py": {"mode": "single", "constraint": "*", "pypi": {"name": "python-pkg", "registry": "https://pypi.org"}},
        "js": {"mode": "single", "constraint": "*", "npm": {"name": "js-pkg", "registry": "https://registry.npmjs.org"}},
        "go": {"mode": "single", "constraint": "*", "go": {"module": "example.com/lib", "proxy": "https://proxy.golang.org"}},
        "oci": {"mode": "single", "constraint": "*", "oci": {"image": "ghcr.io/acme/app", "tag": "v{version}"}},
        "local": {"mode": "single", "constraint": "*", "oci": {"image": "ghcr.io/acme/local", "tag": "v{version}"}},
    }
    project = types.SimpleNamespace(
        artifacts=["oci-image"], versions={"targets": {"local": targets["local"]}},
    )
    forge = types.SimpleNamespace(
        projects={"demo": project}, versions={"targets": targets, "outputs": {}}, orchestration=None,
    )
    results = {
        "py": _result("py", "pypi", "1.2.0", now - timedelta(days=30)),
        "js": _result("js", "npm", "1.2.0", now - timedelta(days=30)),
        "go": _result("go", "go", "v1.2.0", now - timedelta(days=30)),
        "oci": _result("oci", "oci", "1.2.0", now - timedelta(days=30), tag="v1.2.0"),
        "local": _result("local", "oci", "1.2.0", now - timedelta(days=30), tag="v1.2.0"),
    }
    native, oci = versions._native_results_for_project(forge, "demo", project_root, results, targets)
    assert set(native) == {"pypi", "npm", "go"}
    assert set(native["pypi"]) == {"py"}
    assert set(oci) == {"oci", "local"}

    project.artifacts = []
    project.versions = {"targets": {}}
    (project_root / "requirements.in").unlink()
    native, oci = versions._native_results_for_project(forge, "demo", project_root, results, targets)
    assert set(oci) == set()
    assert "py" not in native.get("pypi", {})


def test_resolve_all_normalizes_non_table_root_target_map(monkeypatch):
    forge = types.SimpleNamespace(versions={"targets": "invalid"}, projects={})
    resolved = versions._resolve_all_for_command(forge, [], resolved_at=datetime(2026, 9, 24, tzinfo=timezone.utc))
    assert resolved == ({}, {}, {})


def test_resolve_command_dry_run_and_native_npm_go_writers(tmp_path, monkeypatch):
    local_versions = '''[versions]
age_window_days = 14
[versions.targets."npm.pkg"]
mode = "single"
constraint = "*"
[versions.targets."npm.pkg".npm]
name = "pkg"
registry = "https://registry.npmjs.org"
[versions.targets."go.example.com.lib"]
mode = "single"
constraint = "*"
[versions.targets."go.example.com.lib".go]
module = "example.com/lib"
proxy = "https://proxy.golang.org"
'''
    root_config, project_root = _estate(tmp_path, project_versions=local_versions)
    package = project_root / "package.json"
    package.write_text(json.dumps({"dependencies": {"pkg": "^1.0.0"}, "optionalDependencies": ["ignored"]}), encoding="utf-8")
    (project_root / "go.mod").write_text("module example.com/demo\nrequire example.com/lib v1.0.0\n", encoding="utf-8")
    forge = versions.load_forge_config(root_config)
    declarations = forge.projects["demo"].versions["targets"]
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    project_results = {
        "npm.pkg": _result("npm.pkg", "npm", "1.2.0", now - timedelta(days=30)),
        "go.example.com.lib": _result("go.example.com.lib", "go", "v1.2.0", now - timedelta(days=30)),
    }
    monkeypatch.setattr(versions, "_resolve_all_for_command", lambda *_args, **_kwargs: (
        {}, {"demo": project_results}, {"demo": declarations},
    ))
    monkeypatch.setattr(versions, "_compile_python_constraints", lambda *_args: [])
    before = (project_root / "cmru.toml").read_bytes()
    dry = versions._run_resolve(
        forge, types.SimpleNamespace(config_kind="project", config_path=project_root / "cmru.toml"),
        ["demo"], dry_run=True,
    )
    assert dry[0] == "Dry run: no files written."
    assert (project_root / "cmru.toml").read_bytes() == before

    calls = []
    monkeypatch.setattr(versions, "_run_npm", lambda *args: calls.append(("npm", args)))
    monkeypatch.setattr(versions, "_run_go", lambda *args: calls.append(("go", args)))
    resolved = versions._run_resolve(
        forge, types.SimpleNamespace(config_kind="project", config_path=project_root / "cmru.toml"),
        ["demo"], dry_run=False,
    )
    assert resolved[0].startswith("Resolved version targets")
    assert {name for name, _args in calls} == {"npm", "go"}
    recorded = tomllib.loads((project_root / "cmru.toml").read_text(encoding="utf-8"))
    assert recorded["versions"]["targets"]["npm.pkg"]["resolved"]["version"] == "1.2.0"
    assert recorded["versions"]["targets"]["go.example.com.lib"]["resolved"]["version"] == "v1.2.0"


def test_versions_main_reports_text_and_maps_domain_failures(monkeypatch, capsys, tmp_path):
    context = types.SimpleNamespace(config_path=tmp_path / "cmru.toml", config_kind="orchestration")
    forge = types.SimpleNamespace(versions={}, projects={"demo": types.SimpleNamespace(versions={})})
    monkeypatch.setattr(versions, "resolve_invocation_context", lambda _path: context)
    monkeypatch.setattr(versions, "load_forge_config", lambda _path: forge)
    monkeypatch.setattr(versions, "_selected_projects", lambda *_args: ["demo"])
    monkeypatch.setattr(versions, "_resolve_all_for_command", lambda *_args, **_kwargs: ({}, {"demo": {}}, {"demo": {}}))
    with pytest.raises(SystemExit) as missing_action:
        versions.main([])
    assert missing_action.value.code == 2
    assert versions.main(["check"]) == 0
    assert "No version targets" in capsys.readouterr().out
    assert versions.main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1

    cases = (
        (versions.VersionsPrerequisiteError("missing tool"), versions.exit_codes.PREREQ_MISSING),
        (versions.VersionsOperationError("writer failed"), versions.exit_codes.FAILURE),
        (registry.RegistryError("offline"), versions.exit_codes.PREREQ_MISSING),
        (versions.VersionsError("invalid policy"), versions.exit_codes.CONFIG_ERROR),
        (OSError("disk"), versions.exit_codes.FAILURE),
        (ValueError("bad value"), versions.exit_codes.CONFIG_ERROR),
        (SystemExit(7), 7),
        (SystemExit("non integer"), 2),
    )
    for error, expected in cases:
        monkeypatch.setattr(versions, "_resolve_all_for_command", lambda *_args, _error=error, **_kwargs: (_ for _ in ()).throw(_error))
        assert versions.main(["check"]) == expected
        output = capsys.readouterr()
        if isinstance(error, SystemExit):
            assert output.err == ""
        else:
            assert output.err.startswith("CMRU versions:")


def test_resolve_native_writer_guards_and_transaction_rollback(tmp_path, monkeypatch):
    root_config, project_root = _estate(tmp_path)
    package = project_root / "package.json"
    package.write_text(json.dumps({
        "dependencies": {"pkg": "^1.0.0"}, "optionalDependencies": ["ignored"],
    }), encoding="utf-8")
    go_mod = project_root / "go.mod"
    go_mod.write_text("module example.com/demo\nrequire example.com/lib v1.0.0\n", encoding="utf-8")
    forge = versions.load_forge_config(root_config)
    context = types.SimpleNamespace(config_kind="project", config_path=project_root / "cmru.toml")
    native_state = ({}, {})
    monkeypatch.setattr(versions, "_resolve_all_for_command", lambda *_args, **_kwargs: (
        {}, {"demo": {}}, {"demo": declarations_state},
    ))
    monkeypatch.setattr(versions, "_native_results_for_project", lambda *_args: native_state)
    monkeypatch.setattr(versions, "_native_output_files", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(versions, "_compile_python_constraints", lambda *_args: [])
    monkeypatch.setattr(versions, "_apply_resolved_state", lambda *_args: None)
    tool_calls = []
    monkeypatch.setattr(versions, "_run_npm", lambda *args: tool_calls.append(("npm", args)))
    monkeypatch.setattr(versions, "_run_go", lambda *args: tool_calls.append(("go", args)))

    one = _result("one", "npm", "1.2.0", datetime(2026, 9, 1, tzinfo=timezone.utc))
    two = _result("two", "npm", "1.3.0", datetime(2026, 9, 1, tzinfo=timezone.utc))
    declarations_state = {"one": {"npm": "bad"}}
    native_state = ({"npm": {"one": one}}, {})
    before = package.read_bytes()
    with pytest.raises(versions.VersionsError, match="npm source declaration missing"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert package.read_bytes() == before

    declarations_state = {
        "one": {"npm": {"name": "pkg", "registry": "https://registry.npmjs.org"}},
    }
    native_state = ({"npm": {"one": types.SimpleNamespace(sources={})}}, {})
    with pytest.raises(versions.VersionsError, match="npm candidate missing"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert package.read_bytes() == before

    declarations_state = {
        "one": {"npm": {"name": "pkg", "registry": "https://registry.npmjs.org"}},
        "two": {"npm": {"name": "pkg", "registry": "https://registry.npmjs.org"}},
    }
    native_state = ({"npm": {"one": one, "two": two}}, {})
    with pytest.raises(versions.VersionsError, match="conflicting target versions"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert package.read_bytes() == before

    same = _result("two", "npm", "1.2.0", one.resolved_at)
    native_state = ({"npm": {"one": one, "two": same}}, {})
    with pytest.raises(versions.VersionsError, match="multiple npm targets"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    native_state = ({"npm": {"one": one}}, {})
    versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert tool_calls[-1][0] == "npm"

    declarations_state = {"one": {"go": "bad"}}
    native_state = ({"go": {"one": one}}, {})
    with pytest.raises(versions.VersionsError, match="Go source declaration missing"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert go_mod.read_text(encoding="utf-8").startswith("module example.com/demo")

    one_go = _result("one", "go", "v1.2.0", one.resolved_at)
    two_go = _result("two", "go", "v2.0.0", one.resolved_at)
    declarations_state = {
        "one": {"go": {"module": "example.com/lib", "proxy": "https://proxy.golang.org"}},
        "two": {"go": {"module": "example.com/lib", "proxy": "https://proxy.golang.org"}},
    }
    native_state = ({"go": {"one": one_go, "two": two_go}}, {})
    with pytest.raises(versions.VersionsError, match="conflicting target versions"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)

    two_go_same = _result("two", "go", "1.2.0", one.resolved_at)
    native_state = ({"go": {"one": one_go, "two": two_go_same}}, {})
    with pytest.raises(versions.VersionsError, match="multiple Go targets"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    native_state = ({"go": {"one": one_go}}, {})
    versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert tool_calls[-1][0] == "go"

    declarations_state = {
        "one": {"go": {"module": "example.com/lib", "proxy": "https://proxy.golang.org"}},
    }
    native_state = ({"go": {"one": types.SimpleNamespace(sources={})}}, {})
    with pytest.raises(versions.VersionsError, match="Go candidate missing"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)

    # An orchestration config can be selected with no root-owned targets. It
    # should leave the shared file untouched while resolving project targets.
    native_state = ({"go": {"one": one_go}}, {})
    root_context = types.SimpleNamespace(config_kind="orchestration", config_path=root_config)
    root_before = root_config.read_bytes()
    versions._run_resolve(forge, root_context, ["demo"], dry_run=False)
    assert root_config.read_bytes() == root_before


def test_go_workspace_outputs_are_tracked_and_rolled_back_with_native_writes(tmp_path, monkeypatch):
    project_versions = '''[versions]
age_window_days = 14
[versions.targets."go.lib"]
mode = "single"
constraint = ">=1.0.0"
[versions.targets."go.lib".go]
module = "example.com/lib"
proxy = "https://proxy.golang.org"
'''
    root_config, project_root = _estate(tmp_path, project_versions=project_versions)
    forge = versions.load_forge_config(root_config)
    project_config = project_root / "cmru.toml"
    context = types.SimpleNamespace(config_kind="project", config_path=project_config)
    go_mod = project_root / "go.mod"
    go_sum = project_root / "go.sum"
    workspace = tmp_path / "go.work"
    workspace_sum = tmp_path / "go.work.sum"
    output = project_root / "generated.out"
    original_files = {
        go_mod: b"module example.com/demo\n",
        go_sum: b"example.com/old v1.0.0 h1:old\n",
        workspace: b"go 1.24.0\nuse ./demo\n",
        workspace_sum: b"example.com/old v1.0.0/go.mod h1:old\n",
        project_config: project_config.read_bytes(),
    }
    for path, contents in original_files.items():
        path.write_bytes(contents)
    monkeypatch.setenv("GOWORK", str(workspace))

    result = _result(
        "go.lib", "go", "v1.2.0", datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    project_results = {"go.lib": result}
    declarations = {"demo": forge.projects["demo"].versions["targets"]}
    monkeypatch.setattr(versions, "_resolve_all_for_command", lambda *_args, **_kwargs: (
        {}, {"demo": project_results}, declarations,
    ))
    monkeypatch.setattr(versions, "_native_results_for_project", lambda *_args: (
        {"go": project_results}, {},
    ))
    monkeypatch.setattr(versions, "_native_output_files", lambda *_args, **_kwargs: [(output, "new artifact\n")])
    monkeypatch.setattr(versions, "_compile_python_constraints", lambda *_args: [])

    def run(command, **_kwargs):
        if command == ["go", "env", "GOWORK"]:
            return types.SimpleNamespace(returncode=0, stdout=str(workspace), stderr="")
        assert command == ["go", "get", "example.com/lib@v1.2.0"]
        go_mod.write_text("module changed\n", encoding="utf-8")
        go_sum.write_text("sum changed\n", encoding="utf-8")
        workspace.write_text("go 1.25.0\nuse ./demo\n", encoding="utf-8")
        workspace_sum.write_text("sum changed\n", encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(versions.subprocess, "run", run)
    write_text_atomic = versions._write_text_atomic

    def fail_generated_output(path, contents):
        if path == output:
            raise OSError("simulated output write failure")
        write_text_atomic(path, contents)

    monkeypatch.setattr(versions, "_write_text_atomic", fail_generated_output)
    with pytest.raises(OSError, match="simulated output write failure"):
        versions._run_resolve(forge, context, ["demo"], dry_run=False)
    assert all(path.read_bytes() == contents for path, contents in original_files.items())
    assert not output.exists()


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("", []),
        ("off", []),
        ("go.work", [Path("go.work"), Path("go.work.sum")]),
        ("/workspace/go.work", [Path("/workspace/go.work"), Path("/workspace/go.work.sum")]),
    ],
)
def test_go_workspace_file_discovery_uses_go_env(monkeypatch, tmp_path, reported, expected):
    def run(command, **kwargs):
        assert command == ["go", "env", "GOWORK"]
        assert kwargs == {
            "cwd": tmp_path,
            "text": True,
            "capture_output": True,
            "check": False,
            "timeout": 20,
            "env": {},
        }
        return types.SimpleNamespace(returncode=0, stdout=reported, stderr="")

    monkeypatch.setattr(versions.subprocess, "run", run)
    actual = versions._go_workspace_files(tmp_path, {})
    if reported == "go.work":
        assert actual == [tmp_path / path for path in expected]
    else:
        assert actual == expected


def test_go_workspace_file_discovery_reports_probe_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(
        returncode=2, stdout="", stderr="invalid GOWORK",
    ))
    with pytest.raises(versions.VersionsOperationError, match="could not determine the Go workspace.*invalid GOWORK"):
        versions._go_workspace_files(tmp_path, {})


def test_resolver_empty_provider_and_aligned_spelling_guard(monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    empty = _pypi_target()
    monkeypatch.setattr(versions, "candidates", lambda *_: {})
    with pytest.raises(versions.VersionsError, match="registry returned no compatible candidates"):
        versions._resolve_target("requests", empty, age_window_days=14, resolved_at=now, owner="root")

    target = {
        "mode": "aligned", "constraint": "*",
        "pypi": {"name": "x", "registry": "https://pypi.org"},
        "npm": {"name": "x", "registry": "https://registry.npmjs.org"},
    }
    pools = {
        "pypi": {"shared": _candidate("1.0.0", now - timedelta(days=30))},
        "npm": {"shared": _candidate("2.0.0", now - timedelta(days=30))},
    }
    monkeypatch.setattr(versions, "candidates", lambda family, *_: pools[family])
    with pytest.raises(versions.VersionsError, match="inconsistent source spellings"):
        versions._resolve_target("aligned", target, age_window_days=14, resolved_at=now, owner="root")


def test_python_constraints_registry_auth_cutoff_and_file_edges(monkeypatch, tmp_path):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    tight = _result("pypi.tight", "pypi", "1.0.0", now - timedelta(days=40), age_cutoff=now - timedelta(days=21))
    loose = _result("pypi.loose", "pypi", "2.0.0", now - timedelta(days=40), age_cutoff=now - timedelta(days=14))
    calls = []
    monkeypatch.setattr(versions.subprocess, "run", lambda command, **kwargs: (
        calls.append((command, kwargs)) or types.SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
    ))

    for registry_url, expected_index in (
        ("https://packages.example/simple", "https://packages.example/simple"),
        ("https://packages.example/pypi", "https://packages.example/simple"),
        ("https://packages.example/custom", "https://packages.example/custom/simple"),
    ):
        calls.clear()
        results = {"pypi.tight": tight}
        declarations = {"pypi.tight": {"pypi": {"name": "tight", "registry": registry_url}}}
        versions._compile_python_constraints(tmp_path / expected_index.rsplit("/", 1)[-1], results, declarations)
        command = calls[0][0]
        assert f"cmru={expected_index}" in command

    calls.clear()
    declarations = {
        "pypi.tight": {"pypi": {"name": "tight", "registry": "https://pypi.org"}},
        "pypi.loose": {"pypi": {"name": "loose", "registry": "https://pypi.org"}},
    }
    versions._compile_python_constraints(tmp_path, {"pypi.tight": tight, "pypi.loose": loose}, declarations)
    command = calls[-1][0]
    assert f"loose={versions._timestamp(loose.age_cutoff)}" in command

    with pytest.raises(versions.VersionsError, match="source declaration missing"):
        versions._compile_python_constraints(tmp_path, {"pypi.tight": tight}, {"pypi.tight": {"pypi": None}})
    no_python = _result("npm.x", "npm", "1.0.0", now - timedelta(days=40))
    assert versions._compile_python_constraints(tmp_path, {"npm.x": no_python}, {}) == []

    missing_source = {"pypi.tight": {"pypi": "bad"}}
    with pytest.raises(versions.VersionsError, match="source declaration missing"):
        versions._compile_python_constraints(tmp_path, {"pypi.tight": tight}, missing_source)

    different_auth = {
        "pypi.tight": {"pypi": {"name": "tight", "registry": "https://pypi.org", "token_env": "ONE"}},
        "pypi.loose": {"pypi": {"name": "loose", "registry": "https://pypi.org", "token_env": "TWO"}},
    }
    with pytest.raises(versions.VersionsPrerequisiteError, match="share one credential"):
        versions._compile_python_constraints(tmp_path, {"pypi.tight": tight, "pypi.loose": loose}, different_auth)

    basic = {"pypi.tight": {"pypi": {
        "name": "tight", "registry": "https://pypi.org", "username_env": "UV_USER", "password_env": "UV_PASS",
    }}}
    monkeypatch.setenv("UV_USER", "user")
    monkeypatch.setenv("UV_PASS", "pass")
    versions._compile_python_constraints(tmp_path, {"pypi.tight": tight}, basic)
    env = calls[-1][1]["env"]
    assert env["UV_INDEX_CMRU_USERNAME"] == "user" and env["UV_INDEX_CMRU_PASSWORD"] == "pass"
    monkeypatch.delenv("UV_PASS")
    with pytest.raises(versions.VersionsPrerequisiteError, match="must be set"):
        versions._compile_python_constraints(tmp_path, {"pypi.tight": tight}, basic)


def test_python_constraints_refuse_unreadable_files_and_package_exceptions(monkeypatch, tmp_path):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    result = _result("pypi.a", "pypi", "1.0.0", now - timedelta(days=30))
    decl = {"pypi.a": {"pypi": {"name": "a", "registry": "https://pypi.org"}}}
    output = tmp_path / "constraints" / "constraints.txt"
    output.parent.mkdir()
    output.write_text("# Generated by cmru versions resolve\nold\n", encoding="utf-8")
    generated_date = tmp_path / "constraints" / "constraints-20260924.txt"
    generated_date.write_text("# Generated by cmru versions resolve\nold\n", encoding="utf-8")
    real_read_text = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == output:
            raise OSError("permission denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    with pytest.raises(versions.VersionsError, match="could not inspect generated constraints"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, decl)
    monkeypatch.setattr(Path, "read_text", real_read_text)

    with pytest.raises(versions.VersionsPrerequisiteError, match="token environment"):
        versions._compile_python_constraints(
            tmp_path, {"pypi.a": result}, {"pypi.a": {"pypi": {
                "name": "a", "registry": "https://pypi.org", "token_env": "CMRU_UV_MISSING",
            }}},
        )


def test_safe_relative_path_rejects_escape_and_symlink(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(versions.VersionsError, match="relative path"):
        versions._safe_relative_path(root, "../outside", label="output")
    target = tmp_path / "outside"
    target.mkdir()
    (root / "linked").symlink_to(target, target_is_directory=True)
    with pytest.raises(versions.VersionsError, match="symlink path"):
        versions._safe_relative_path(root, "linked/out.txt", label="output")

    actual_resolve = Path.resolve

    def resolve_outside(path, *args, **kwargs):
        if path == root / "race.txt":
            return tmp_path / "escaped.txt"
        return actual_resolve(path, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(Path, "resolve", resolve_outside)
        with pytest.raises(versions.VersionsError, match="resolves outside"):
            versions._safe_relative_path(root, "race.txt", label="output")


def test_init_facts_derives_supported_manifests_and_reports_skips(tmp_path):
    (tmp_path / "requirements.in").write_text(
        "requests>=2.31.0,<3.0.0\nconditional>=1.0.0; python_version < '3.12'\n-r extra.in\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["urllib3~=2.0", "conditional-py>=1; sys_platform == \'win32\'"]\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(json.dumps({
        "dependencies": {"left-pad": "^1.3.0", "local": "file:../local"},
        "optionalDependencies": ["malformed"],
    }), encoding="utf-8")
    (tmp_path / "go.mod").write_text("module example.com/demo\nrequire example.com/lib v1.2.3\n", encoding="utf-8")
    facts, skipped = versions._init_facts(tmp_path)
    assert {(family, slug) for family, slug, *_ in facts} == {
        ("pypi", "requests"), ("pypi", "urllib3"), ("npm", "left-pad"), ("go", "example.com.lib"),
    }
    assert any("non-registry dependency" in item for item in skipped)
    assert any("includes/options/URL/path" in item for item in skipped)
    assert any("requirements.in line" in item and "conditional" in item for item in skipped)
    assert any("conditional-py" in item and "unsupported requirement syntax" in item for item in skipped)
    assert any("optionalDependencies" in item and "expected a dependency object" in item for item in skipped)


def test_init_facts_covers_all_manifest_forms_and_deduplicates_constraints(tmp_path):
    (tmp_path / "requirements.in").write_text(
        "\n# comment\nFoo_Bar>=1.0.0\n-r extras.in\nhttps://example.invalid/pkg.whl\n"
        "./local.whl\nthing @ https://example.invalid/x\nother==latest\nrequests>=2.0.0\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="demo"\ndependencies=["foo-bar<2.0.0", "requests<3.0.0", 7, "", "invalid requirement !!!", "bad>=2.0.0rc1"]\n',
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(json.dumps({
        "dependencies": {
            "@acme/lib": "^1.2.0", "local": "workspace:*", "unsupported": "latest",
            "wrong-type": 1, 3: "^1.0.0", "scope.name": "~1.0.0", "shared": "*",
            "empty-constraint": "",
        },
        "devDependencies": "not a dependency table",
        "optionalDependencies": {"peer": ">=1.0.0", "shared": "*"},
        "peerDependencies": {"bad-peer": "^1.0.0 || ^2.0.0"},
    }), encoding="utf-8")
    (tmp_path / "go.mod").write_text(
        "module example.com/demo\n"
        "retract v0.9.0\n"
        "require example.com/single v1.0.0 // indirect\n"
        "require (\n"
        "  example.com/block v1.2.0\n"
        "  example.com/invalid v1.2.0rc1\n"
        "  example.com/no-version latest\n"
        "  // ignored comment\n"
        "  malformed\n"
        ")\n"
        "retract v1.2.0\n",
        encoding="utf-8",
    )
    facts, skipped = versions._init_facts(tmp_path)
    by_key = {(family, slug): constraint for family, slug, constraint, _source in facts}
    assert by_key[("pypi", "foo-bar")] == ">=1.0.0,<2.0.0"
    assert by_key[("pypi", "requests")] == ">=2.0.0,<3.0.0"
    assert ("npm", "acme.lib") in by_key and ("npm", "peer") in by_key
    assert by_key["npm", "shared"] == "*"
    assert by_key["npm", "empty-constraint"] == "*"
    assert ("go", "example.com.single") in by_key and ("go", "example.com.block") in by_key
    assert ("go", "retract") not in by_key
    assert len([line for line in skipped if "requirements.in line" in line]) >= 4
    assert any("pyproject.toml dependency" in line for line in skipped)
    assert any("wrong-type" in line and "must be a string" in line for line in skipped)
    assert any("devDependencies" in line and "expected a dependency object" in line for line in skipped)
    assert any("example.com/no-version" in line and "unsupported module version" in line for line in skipped)
    assert any("peerDependencies" not in line and "bad-peer" in line for line in skipped)
    assert any("go.mod example.com/invalid" in line for line in skipped)


def test_init_facts_refuses_malformed_manifests_and_coordinate_collisions(tmp_path, monkeypatch):
    package = tmp_path / "package.json"
    package.write_text("{", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not read"):
        versions._init_facts(tmp_path)
    package.write_text("[]", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="must contain a JSON object"):
        versions._init_facts(tmp_path)
    package.write_text(json.dumps({"dependencies": {"@acme/lib": "^1.0.0", "acme.lib": "^1.0.0"}}), encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="disagree on registry coordinates"):
        versions._init_facts(tmp_path)

    package.unlink()
    (tmp_path / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not read"):
        versions._init_facts(tmp_path)
    (tmp_path / "pyproject.toml").write_text('project = "not a table"\n', encoding="utf-8")
    with pytest.raises(versions.VersionsError, match=r"\[project\] must be a table"):
        versions._init_facts(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    assert versions._init_facts(tmp_path) == ([], [])
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = "not a list"\n', encoding="utf-8")
    assert versions._init_facts(tmp_path) == (
        [], ["pyproject.toml project.dependencies: expected an array of requirement strings"],
    )

    (tmp_path / "requirements.in").write_text("same>=1.0.0\nsame<2.0.0\n", encoding="utf-8")
    real_validate = versions.validate_constraint
    monkeypatch.setattr(versions, "validate_constraint", lambda text: (
        (_ for _ in ()).throw(registry.RegistryError("injected merge failure"))
        if "," in text else real_validate(text)
    ))
    with pytest.raises(versions.VersionsError, match="cannot combine constraints"):
        versions._init_facts(tmp_path)


def test_manifest_identity_target_id_and_native_dependency_readers(tmp_path):
    assert versions._target_id("npm", "@scope/pkg") == "npm.scope.pkg"
    with pytest.raises(versions.VersionsError, match="cannot derive"):
        versions._target_id("pypi", "._-")
    assert versions._same_source_identity("pypi", "Foo_Bar", "foo-bar")
    assert versions._same_source_identity("npm", "@Scope/Lib", "@scope/lib")
    assert versions._same_source_identity("go", "example.com/lib", "example.com/lib")
    assert not versions._same_source_identity("go", 1, "1")
    assert not versions._same_source_identity("npm", "a", "b")

    assert versions._project_has_python_manifest(tmp_path) is False
    (tmp_path / "requirements-dev.txt").write_text("", encoding="utf-8")
    assert versions._project_has_python_manifest(tmp_path) is True

    assert versions._npm_declared_names(tmp_path) == set()
    (tmp_path / "package.json").write_text(json.dumps({
        "dependencies": {"direct": "^1", 3: "bad"}, "devDependencies": "skip",
    }), encoding="utf-8")
    (tmp_path / "npm-shrinkwrap.json").write_text(json.dumps({"packages": {
        "": {}, "node_modules/fallback": {}, "node_modules/named": {"name": "explicit"},
        1: {}, "bad": "skip",
    }}), encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {"node_modules/@scope/child": {}}}), encoding="utf-8")
    assert versions._npm_declared_names(tmp_path) == {"direct", "3", "explicit", "fallback", "@scope/child"}

    (tmp_path / "go.mod").write_text(
        "module example.com/demo\nretract v1.0.0\n"
        "require example.com/direct v1.2.3 // indirect\n"
        "require (\n example.com/block v2.0.0\n\n // comment\n bad\n example.com/no-version latest\n)\n",
        encoding="utf-8",
    )
    with (tmp_path / "go.mod").open("a", encoding="utf-8") as go_mod:
        go_mod.write("retract v2.0.0\n")
    assert versions._go_required_modules(tmp_path) == {"example.com/direct", "example.com/block"}


def test_root_and_project_resolved_records_have_their_selected_policy(tmp_path, monkeypatch, capsys):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    root_config, project_root = _estate(
        tmp_path,
        project_versions=_versions_table(21),
        root_versions=_versions_table(14),
    )
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(versions, "datetime", FixedDateTime)
    old = now - timedelta(days=40)
    middle = now - timedelta(days=17)
    fresh = now - timedelta(days=2)
    candidates = {
        "2.31.0": _candidate("2.31.0", old, "pypi-upload-time"),
        "2.32.0": _candidate("2.32.0", middle, "pypi-upload-time"),
        "2.33.0": _candidate("2.33.0", fresh, "pypi-upload-time"),
    }
    monkeypatch.setattr(versions, "candidates", lambda *_: candidates)
    calls = []

    class Process:
        returncode = 0
        stdout = "requests==2.31.0\n"
        stderr = ""

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    monkeypatch.setattr(versions.subprocess, "run", run)
    assert versions.main(["resolve", "all", "--config", str(root_config)]) == 0
    output = capsys.readouterr().out
    assert "root" in output and "demo" in output
    assert len(calls) == 1
    command = calls[0][0]
    assert "--index-strategy" in command and "first-index" in command
    assert command[command.index("--exclude-newer") + 1] == versions._timestamp(now - timedelta(days=21))
    assert "--output-file" not in command
    assert (project_root / "constraints" / "constraints.txt").read_text().endswith("requests==2.31.0\n")

    root_after = tomllib.loads(root_config.read_text(encoding="utf-8"))
    project_after = tomllib.loads((project_root / "cmru.toml").read_text(encoding="utf-8"))
    assert root_after["versions"]["targets"]["pypi.requests"]["resolved"]["version"] == "2.32.0"
    assert project_after["versions"]["targets"]["pypi.requests"]["resolved"]["version"] == "2.31.0"
    before = (root_config.read_bytes(), (project_root / "cmru.toml").read_bytes(), (project_root / "constraints" / "constraints.txt").read_bytes())
    assert versions.main(["check", "all", "--config", str(root_config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema_version"] == 1
    assert len(report["targets"]) == 2
    after = (root_config.read_bytes(), (project_root / "cmru.toml").read_bytes(), (project_root / "constraints" / "constraints.txt").read_bytes())
    assert before == after


def test_init_skips_shared_root_target_and_adds_local_manifest_target(tmp_path, monkeypatch, capsys):
    root_versions = _versions_table(14)
    root_config, project_root = _estate(tmp_path, root_versions=root_versions)
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    original = (project_root / "cmru.toml").read_bytes()
    assert versions.main(["init", "all", "--dry-run", "--config", str(root_config)]) == 0
    assert b"[versions]" not in (project_root / "cmru.toml").read_bytes()
    assert versions.main(["init", "all", "--config", str(root_config)]) == 0
    assert "shared root targets already cover" in capsys.readouterr().out
    assert (project_root / "cmru.toml").read_bytes() == original

    root_config2, project_root2 = _estate(tmp_path / "other")
    (project_root2 / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path / "other")
    assert versions.main(["init", "all", "--config", str(root_config2)]) == 0
    loaded = tomllib.loads((project_root2 / "cmru.toml").read_text(encoding="utf-8"))
    assert loaded["versions"]["targets"]["pypi.requests"]["mode"] == "single"


def test_versions_init_dry_run_existing_targets_skips_and_root_mismatch(tmp_path, monkeypatch):
    root_config, project_root = _estate(tmp_path)
    requirements = project_root / "requirements.in"
    requirements.write_text("requests>=2.31.0,<3.0.0\n-r shared.in\n", encoding="utf-8")
    original = (project_root / "cmru.toml").read_bytes()
    forge = versions.load_forge_config(root_config)
    dry_run = versions._versions_init(forge, ["demo"], dry_run=True)
    assert any("would add pypi.requests" in line for line in dry_run)
    assert any("skipped manifest dependency" in line for line in dry_run)
    assert (project_root / "cmru.toml").read_bytes() == original

    # A declaration already local to this project is left intact.
    local_table = '''[versions]
[versions.targets."pypi.requests"]
mode = "single"
constraint = ">=2.31.0,<3.0.0"
[versions.targets."pypi.requests".pypi]
name = "requests"
registry = "https://pypi.org"
'''
    config = project_root / "cmru.toml"
    config.write_text(_project_config("demo", local_table), encoding="utf-8")
    forge = versions.load_forge_config(root_config)
    summary = versions._versions_init(forge, ["demo"], dry_run=False)
    assert any("no new targets derived" in line for line in summary)

    mismatch_root = _versions_table(14).replace('name = "requests"', 'name = "other"')
    other_root, other_project = _estate(tmp_path / "mismatch", root_versions=mismatch_root)
    (other_project / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="does not match manifest source"):
        versions._versions_init(versions.load_forge_config(other_root), ["demo"], dry_run=True)

    invalid_root, invalid_project = _estate(tmp_path / "invalid-source")
    (invalid_project / "package.json").write_text(
        json.dumps({"dependencies": {"not/a-name": "^1.0.0"}}), encoding="utf-8",
    )
    with pytest.raises(versions.VersionsError, match="valid registry package name"):
        versions._versions_init(versions.load_forge_config(invalid_root), ["demo"], dry_run=True)


def test_versions_init_standalone_writes_and_validates_the_project_config(tmp_path, monkeypatch):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    config = project_root / "cmru.toml"
    config.write_text(_project_config("demo"), encoding="utf-8")
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    project = types.SimpleNamespace(project_root=project_root, versions={})
    forge = types.SimpleNamespace(
        projects={"demo": project}, versions={}, orchestration=None, repo_root=tmp_path,
    )
    validated = []
    monkeypatch.setattr(versions, "load_forge_config", lambda path: validated.append(path) or forge)

    summary = versions._versions_init(forge, ["demo"], dry_run=False)
    assert any("added pypi.requests" in line for line in summary)
    assert validated == [config]
    assert tomllib.loads(config.read_text(encoding="utf-8"))["versions"]["targets"]["pypi.requests"]["constraint"] == ">=2.31.0,<3.0.0"

    no_op = versions._versions_init(forge, ["demo"], dry_run=False)
    assert any("no new targets derived" in line for line in no_op)
    assert validated == [config]


def test_versions_init_refuses_bad_tables_and_rolls_back_failed_validation(tmp_path, monkeypatch):
    root_config, project_root = _estate(tmp_path)
    config = project_root / "cmru.toml"
    malformed_versions = _project_config("demo").replace(
        'schema_version = 1\n', 'schema_version = 1\n"versions" = "bad"\n',
    )
    config.write_text(malformed_versions, encoding="utf-8")
    fake = types.SimpleNamespace(
        projects={"demo": types.SimpleNamespace(project_root=project_root)},
        versions={}, orchestration=None, repo_root=tmp_path,
    )
    with pytest.raises(versions.VersionsError, match="must be a table"):
        versions._versions_init(fake, ["demo"], dry_run=True)
    config.write_text(_project_config("demo").replace(
        '[project]\n', '[versions]\ntargets = "bad"\n\n[project]\n',
    ), encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="targets must be a table"):
        versions._versions_init(fake, ["demo"], dry_run=True)

    root_config, project_root = _estate(tmp_path / "rollback")
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    config = project_root / "cmru.toml"
    original = config.read_bytes()
    forge = versions.load_forge_config(root_config)
    monkeypatch.setattr(versions, "load_forge_config", lambda *_: (_ for _ in ()).throw(SystemExit(2)))
    with pytest.raises(versions.VersionsError, match="failed CMRU config validation"):
        versions._versions_init(forge, ["demo"], dry_run=False)
    assert config.read_bytes() == original

    monkeypatch.setattr(versions, "load_forge_config", lambda *_: (_ for _ in ()).throw(RuntimeError("validation")))
    with pytest.raises(RuntimeError, match="validation"):
        versions._versions_init(forge, ["demo"], dry_run=False)
    assert config.read_bytes() == original


def test_standalone_project_local_target_resolves_and_records_project_side(tmp_path, monkeypatch):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    project_root = tmp_path / "standalone"
    project_root.mkdir()
    config_path = project_root / "cmru.toml"
    config_path.write_text(
        _project_config("standalone", _versions_table(14)).replace(
            "schema_version = 1\n",
            'schema_version = 1\n[github]\nowner = "example"\nrepo = "demo"\n'
            'owner_type = "user"\n[targets]\nhost = "github"\nregistry = []\n',
            1,
        ),
        encoding="utf-8",
    )
    (project_root / "requirements.in").write_text("requests>=2.31.0,<3.0.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(versions, "datetime", FixedDateTime)
    released = now - timedelta(days=40)
    monkeypatch.setattr(versions, "candidates", lambda *_: {
        "2.31.0": _candidate("2.31.0", released),
    })

    class Process:
        returncode = 0
        stdout = "requests==2.31.0\n"
        stderr = ""

    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: Process())
    assert versions.main(["resolve", "all", "--config", str(config_path)]) == 0
    document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert document["versions"]["targets"]["pypi.requests"]["resolved"]["version"] == "2.31.0"
    assert (project_root / "constraints" / "constraints.txt").is_file()


def test_go_commit_time_and_oci_created_fallback_emit_warnings(capsys):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    go_result = versions.TargetResult(
        target_id="go.example.mod", version="1.0.0", resolved_at=now,
        age_cutoff=now - timedelta(days=14),
        sources={"go": _candidate("1.0.0", now - timedelta(days=30), "go-proxy-info-vcs-commit-time")},
        override=False, reason=None, expires=None, owner="root",
    )
    oci_result = versions.TargetResult(
        target_id="oci.app", version="1.0.0", resolved_at=now,
        age_cutoff=now - timedelta(days=14),
        sources={"oci": _candidate("1.0.0", now - timedelta(days=30), "oci-image-created-fallback")},
        override=False, reason=None, expires=None, owner="demo",
    )
    versions._emit_age_evidence_warnings(
        {"go.example.mod": go_result}, {"demo": {"oci.app": oci_result}},
    )
    diagnostic = capsys.readouterr().err
    assert "Go proxy .info Time (VCS commit time)" in diagnostic
    assert "not proxy publication time" in diagnostic
    assert "publisher-supplied OCI image-created time" in diagnostic


def test_npm_native_lock_writer_pins_dependencies_and_uses_age_exceptions(tmp_path, monkeypatch):
    package_json = tmp_path / "package.json"
    package_json.write_text(json.dumps({
        "description": "bibliothèque résumé",
        "dependencies": {"@acme/lib": "^1.0.0"},
    }), encoding="utf-8")
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=21)
    result = _result("npm.acme.lib", "npm", "1.2.0", now - timedelta(days=2), age_cutoff=cutoff)
    calls = []

    class Process:
        returncode = 0
        stdout = "11.19.1"
        stderr = ""

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command == ["npm", "--version"]:
            return Process()
        complete = Process()
        complete.stdout = ""
        return complete

    monkeypatch.setattr(versions.subprocess, "run", run)
    versions._run_npm(
        tmp_path,
        {"@acme/lib": ("1.2.0", "npm.acme.lib")},
        {"npm.acme.lib": result},
        {"npm.acme.lib": "1.0.0"},
        {"@acme/lib": {"name": "@acme/lib", "registry": "https://registry.npmjs.org"}},
    )
    assert len(calls) == 2
    assert calls[0][1]["text"] is True and calls[0][1]["capture_output"] is True
    assert calls[0][1]["check"] is False
    assert calls[1][1]["text"] is True and calls[1][1]["capture_output"] is True
    assert calls[1][1]["check"] is False
    command = calls[1][0]
    assert "--package-lock-only" in command and "--ignore-scripts" in command
    assert command[command.index("--before") + 1] == versions._timestamp(cutoff)
    assert "--min-release-age-exclude" in command and "@acme/lib" in command
    assert "--@acme:registry=https://registry.npmjs.org" in command
    document = json.loads(package_json.read_text(encoding="utf-8"))
    assert document["dependencies"]["@acme/lib"] == "1.2.0"
    assert document["overrides"]["@acme/lib"] == "1.2.0"
    assert "bibliothèque résumé" in package_json.read_text(encoding="utf-8")

    calls.clear()
    exactly_at_cutoff = _result("npm.acme.lib", "npm", "1.2.0", cutoff, age_cutoff=cutoff)
    versions._run_npm(
        tmp_path,
        {"@acme/lib": ("1.2.0", "npm.acme.lib")},
        {"npm.acme.lib": exactly_at_cutoff},
        {"npm.acme.lib": "1.2.0"},
        {"@acme/lib": {"name": "@acme/lib", "registry": "https://registry.npmjs.org"}},
    )
    assert len(calls) == 1
    assert "--min-release-age-exclude" not in calls[0][0]


def test_npm_writer_refuses_unowned_overrides_and_old_npm_exception(monkeypatch, tmp_path):
    package_json = tmp_path / "package.json"
    package_json.write_text(json.dumps({"dependencies": {"left-pad": "^1.0.0"}, "overrides": {"left-pad": "1.0.0"}}), encoding="utf-8")
    result = _result("npm.left-pad", "npm", "1.2.0", datetime(2026, 9, 23, tzinfo=timezone.utc))
    source = {"name": "left-pad", "registry": "https://registry.npmjs.org"}
    with pytest.raises(versions.VersionsError, match="not owned"):
        versions._run_npm(
            tmp_path, {"left-pad": ("1.2.0", "npm.left-pad")},
            {"npm.left-pad": result}, {"npm.left-pad": None}, {"left-pad": source},
        )

    package_json.write_text(json.dumps({"dependencies": {"left-pad": "^1.0.0"}}), encoding="utf-8")
    calls = []

    class OldNpm:
        returncode = 0
        stdout = "10.0.0"
        stderr = ""

    monkeypatch.setattr(versions.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or OldNpm())
    with pytest.raises(versions.VersionsPrerequisiteError, match="11.5.0 or newer"):
        versions._run_npm(
            tmp_path, {"left-pad": ("1.2.0", "npm.left-pad")},
            {"npm.left-pad": result}, {"npm.left-pad": None}, {"left-pad": source},
        )
    assert len(calls) == 1 and calls[0][0] == ["npm", "--version"]
    assert calls[0][1]["text"] is True and calls[0][1]["capture_output"] is True
    assert calls[0][1]["check"] is False

    class FailedProbe:
        returncode = 1
        stdout = "11.19.1"
        stderr = "npm failed"

    calls.clear()
    monkeypatch.setattr(
        versions.subprocess, "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or FailedProbe(),
    )
    with pytest.raises(versions.VersionsPrerequisiteError, match="11.5.0 or newer"):
        versions._run_npm(
            tmp_path, {"left-pad": ("1.2.0", "npm.left-pad")},
            {"npm.left-pad": result}, {"npm.left-pad": None}, {"left-pad": source},
        )


def test_npm_writer_auth_overrides_and_external_command_failures(monkeypatch, tmp_path):
    package = tmp_path / "package.json"
    package.write_text(json.dumps({
        "dependencies": {"pkg": "^1.0.0"}, "devDependencies": {"pkg": "^1.0.0"},
        "optionalDependencies": {"pkg": "^1.0.0"}, "peerDependencies": {"pkg": "^1.0.0"},
    }), encoding="utf-8")
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    old = _result("npm.pkg", "npm", "1.2.0", now - timedelta(days=2))
    source = {"name": "pkg", "registry": "https://registry.example", "token_env": "NPM_TOKEN"}
    monkeypatch.setenv("NPM_TOKEN", "secret")
    calls = []

    class Process:
        returncode = 0
        stdout = "11.5.0"
        stderr = ""

    def successful(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:2] == ["--version"]:
            return Process()
        userconfig = Path(command[command.index("--userconfig") + 1])
        assert stat.S_IMODE(userconfig.stat().st_mode) == 0o600
        assert "${NPM_TOKEN}" in userconfig.read_text(encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(versions.subprocess, "run", successful)
    versions._run_npm(
        tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": old},
        {"npm.pkg": None}, {"pkg": source},
    )
    assert "--min-release-age-exclude" not in calls[0][0]  # version probe only
    assert "--min-release-age-exclude" in calls[1][0]
    document = json.loads(package.read_text(encoding="utf-8"))
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        assert document[field]["pkg"] == "1.2.0"

    package.write_text(json.dumps({"dependencies": {"pkg": "^1"}, "overrides": []}), encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="overrides must be an object"):
        versions._run_npm(
            tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": old},
            {"npm.pkg": None}, {"pkg": {"name": "pkg", "registry": "https://registry.example"}},
        )

    package.write_text(json.dumps({"dependencies": {"pkg": "^1", "other": "^1"}}), encoding="utf-8")
    mismatch = {
        "pkg": {"name": "pkg", "registry": "https://registry.example", "token_env": "ONE"},
        "other": {"name": "other", "registry": "https://registry.example", "token_env": "TWO"},
    }
    with pytest.raises(versions.VersionsError, match="share one credential"):
        versions._run_npm(
            tmp_path,
            {"pkg": ("1.2.0", "npm.pkg"), "other": ("1.2.0", "npm.other")},
            {"npm.pkg": old, "npm.other": old}, {"npm.pkg": None, "npm.other": None}, mismatch,
        )

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("npm")

    monkeypatch.setattr(versions.subprocess, "run", missing)
    with pytest.raises(versions.VersionsPrerequisiteError, match="npm on PATH"):
        versions._run_npm(
            tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": old},
            {"npm.pkg": None}, {"pkg": {"name": "pkg", "registry": "https://registry.example"}},
        )

    aged = _result("npm.pkg", "npm", "1.2.0", now - timedelta(days=40))
    with pytest.raises(versions.VersionsPrerequisiteError, match="npm on PATH"):
        versions._run_npm(
            tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": aged},
            {"npm.pkg": None}, {"pkg": {"name": "pkg", "registry": "https://registry.example"}},
        )

    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("npm", 900),
    ))
    with pytest.raises(versions.VersionsOperationError, match="did not finish"):
        versions._run_npm(
            tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": aged},
            {"npm.pkg": None}, {"pkg": {"name": "pkg", "registry": "https://registry.example"}},
        )

    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(
        returncode=1, stdout="fallback output", stderr="",
    ))
    with pytest.raises(versions.VersionsOperationError, match="fallback output"):
        versions._run_npm(
            tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": aged},
            {"npm.pkg": None}, {"pkg": {"name": "pkg", "registry": "https://registry.example"}},
        )


def test_temporary_npm_credentials_are_private_and_removed(monkeypatch):
    monkeypatch.setenv("CMRU_NPM_TOKEN", "secret-token")
    with versions._temporary_npm_config(
        "https://registry.example/npm/", "CMRU_NPM_TOKEN", None, None,
    ) as path:
        assert path is not None and path.exists()
        assert path.stat().st_mode & 0o777 == 0o600
        assert "${CMRU_NPM_TOKEN}" in path.read_text(encoding="utf-8")
        captured = path
    assert not captured.exists()
    monkeypatch.delenv("CMRU_NPM_TOKEN")
    with pytest.raises(versions.VersionsPrerequisiteError, match="token environment"):
        with versions._temporary_npm_config(
            "https://registry.example", "CMRU_NPM_TOKEN", None, None,
        ):
            pytest.fail("missing token should refuse")


def test_npm_and_go_credential_helpers_cover_anonymous_basic_and_cleanup(monkeypatch):
    with versions._temporary_npm_config("https://registry.example/npm", None, None, None) as path:
        assert path is None
    with pytest.raises(versions.VersionsError, match="invalid npm registry URL"):
        with versions._temporary_npm_config("relative", "TOKEN", None, None):
            pytest.fail("relative npm registry should refuse")
    with pytest.raises(versions.VersionsPrerequisiteError, match="credential environment"):
        with versions._temporary_npm_config("https://registry.example", None, "NPM_USER", "NPM_PASS"):
            pytest.fail("unset npm credentials should refuse")
    monkeypatch.setenv("NPM_USER", "alice")
    with pytest.raises(versions.VersionsPrerequisiteError, match="credential environment"):
        with versions._temporary_npm_config("https://registry.example", None, "NPM_USER", "NPM_PASS"):
            pytest.fail("missing npm password should refuse")
    monkeypatch.setenv("NPM_PASS", "secret")
    monkeypatch.delenv("NPM_USER")
    with pytest.raises(versions.VersionsPrerequisiteError, match="credential environment"):
        with versions._temporary_npm_config("https://registry.example", None, "NPM_USER", "NPM_PASS"):
            pytest.fail("missing npm username should refuse")
    monkeypatch.setenv("NPM_USER", "alice")
    with versions._temporary_npm_config("https://registry.example/npm", None, "NPM_USER", "NPM_PASS") as npmrc:
        assert npmrc is not None
        assert stat.S_IMODE(npmrc.stat().st_mode) == 0o600
        assert ":_auth=" in npmrc.read_text(encoding="utf-8")
        npmrc.unlink()

    with versions._temporary_netrc("https://proxy.example", None, None, None) as netrc:
        assert netrc is None
    with pytest.raises(versions.VersionsError, match="invalid Go proxy URL"):
        with versions._temporary_netrc("relative", "TOKEN", None, None):
            pytest.fail("relative Go proxy should refuse")
    with pytest.raises(versions.VersionsPrerequisiteError, match="token environment"):
        with versions._temporary_netrc("https://proxy.example", "GO_TOKEN", None, None):
            pytest.fail("unset Go token should refuse")
    monkeypatch.setenv("GO_TOKEN", "token-value")
    with versions._temporary_netrc("https://proxy.example", "GO_TOKEN", None, None) as token_netrc:
        assert token_netrc is not None
        assert "login __token__ password token-value" in token_netrc.read_text(encoding="utf-8")
    with pytest.raises(versions.VersionsPrerequisiteError, match="credential environment"):
        with versions._temporary_netrc("https://proxy.example", None, "GO_USER", "GO_PASS"):
            pytest.fail("unset Go credentials should refuse")
    monkeypatch.setenv("GO_USER", "alice")
    with pytest.raises(versions.VersionsPrerequisiteError, match="credential environment"):
        with versions._temporary_netrc("https://proxy.example", None, "GO_USER", "GO_PASS"):
            pytest.fail("missing Go password should refuse")
    monkeypatch.setenv("GO_PASS", "secret")
    monkeypatch.delenv("GO_USER")
    with pytest.raises(versions.VersionsPrerequisiteError, match="credential environment"):
        with versions._temporary_netrc("https://proxy.example", None, "GO_USER", "GO_PASS"):
            pytest.fail("missing Go username should refuse")
    monkeypatch.setenv("GO_USER", "alice")
    with versions._temporary_netrc("https://proxy.example", None, "GO_USER", "GO_PASS") as netrc:
        assert netrc is not None and "login alice password secret" in netrc.read_text(encoding="utf-8")
        netrc.unlink()


def test_npm_writer_validates_package_lock_and_registry_shapes(tmp_path, monkeypatch):
    package = tmp_path / "package.json"
    source = {"name": "pkg", "registry": "https://registry.npmjs.org"}
    base_result = _result("npm.pkg", "npm", "1.2.0", datetime(2026, 9, 1, tzinfo=timezone.utc))
    versions._run_npm(tmp_path, {}, {}, {}, {})
    with pytest.raises(versions.VersionsError, match="require .*package.json"):
        versions._run_npm(tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {}, {}, {"pkg": source})

    package.write_text("{", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not parse"):
        versions._run_npm(tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {}, {}, {"pkg": source})
    package.write_text("[]", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="JSON object"):
        versions._run_npm(tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {}, {}, {"pkg": source})

    package.write_text(json.dumps({
        "dependencies": {"pkg": "^1.0.0"}, "optionalDependencies": ["ignored"],
    }), encoding="utf-8")
    lock = tmp_path / "package-lock.json"
    lock.write_text("{", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not parse .*package-lock"):
        versions._run_npm(tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {}, {}, {"pkg": source})
    lock.write_text(json.dumps({"packages": {
        "": {}, "node_modules/pkg": {"name": "pkg"}, "node_modules/other": {},
        "node_modules/ignored": "not a table", "custom/path": {},
    }}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(versions.subprocess, "run", lambda command, **kwargs: (
        calls.append(command) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
    ))
    versions._run_npm(
        tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": base_result},
        {"npm.pkg": None}, {"pkg": source},
    )
    assert calls[-1][0] == "npm" and "--package-lock-only" in calls[-1]

    package.write_text(json.dumps({"dependencies": ["pkg"]}), encoding="utf-8")
    lock.write_text(json.dumps({"packages": {"node_modules/pkg": {"name": "pkg"}}}), encoding="utf-8")
    versions._run_npm(
        tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": base_result},
        {"npm.pkg": None}, {"pkg": source},
    )
    assert json.loads(package.read_text(encoding="utf-8"))["dependencies"] == ["pkg"]

    lock.write_text('{"packages": ["not a table"]}', encoding="utf-8")
    package.write_text(json.dumps({"dependencies": {"pkg": "^1.0.0"}}), encoding="utf-8")
    versions._run_npm(
        tmp_path, {"pkg": ("1.2.0", "npm.pkg")}, {"npm.pkg": base_result},
        {"npm.pkg": None}, {"pkg": source},
    )

    package.write_text(json.dumps({"dependencies": {"@malformed": "^1.0.0"}}), encoding="utf-8")
    calls.clear()
    versions._run_npm(
        tmp_path, {"@malformed": ("1.2.0", "npm.malformed")},
        {"npm.malformed": base_result}, {"npm.malformed": None},
        {"@malformed": {**source, "name": "@malformed"}},
    )
    assert not any(arg.startswith("--@malformed:registry=") for arg in calls[-1])

    package.write_text(json.dumps({"dependencies": {}}), encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="absent from package.json"):
        versions._run_npm(
            tmp_path, {"absent": ("1.2.0", "npm.absent")}, {"npm.absent": base_result},
            {"npm.absent": None}, {"absent": {**source, "name": "absent"}},
        )
    package.write_text(json.dumps({"dependencies": {"pkg": "^1", "other": "^1"}}), encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="same registry"):
        versions._run_npm(
            tmp_path, {"pkg": ("1.2.0", "npm.pkg"), "other": ("1.0.0", "npm.other")},
            {"npm.pkg": base_result, "npm.other": base_result}, {"npm.pkg": None},
            {"pkg": source, "other": {"name": "other", "registry": "https://elsewhere.example"}},
        )


def test_go_native_writer_uses_private_netrc_and_reports_tool_failure(monkeypatch, tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_GO_USER", "alice")
    monkeypatch.setenv("CMRU_GO_PASSWORD", "secret")
    captured = []

    class Process:
        returncode = 0
        stdout = ""
        stderr = ""

    def run(command, **kwargs):
        captured.append((command, kwargs))
        netrc = Path(kwargs["env"]["NETRC"])
        assert netrc.stat().st_mode & 0o777 == 0o600
        assert "machine proxy.example login alice password secret" in netrc.read_text(encoding="utf-8")
        return Process()

    monkeypatch.setattr(versions.subprocess, "run", run)
    versions._run_go(
        tmp_path, {"example.com/lib": ("v1.2.3", "go.example.lib")},
        {"example.com/lib": {
            "module": "example.com/lib", "proxy": "https://proxy.example",
            "username_env": "CMRU_GO_USER", "password_env": "CMRU_GO_PASSWORD",
        }},
    )
    command, kwargs = captured[0]
    assert command == ["go", "get", "example.com/lib@v1.2.3"]
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False
    assert kwargs["env"]["GOPROXY"] == "https://proxy.example"
    assert kwargs["env"]["GONOPROXY"] == "none"
    assert not Path(kwargs["env"]["NETRC"]).exists()

    class Failed:
        returncode = 1
        stdout = "useful Go diagnostic"
        stderr = ""

    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: Failed())
    with pytest.raises(versions.VersionsOperationError, match="Go module update failed.*useful Go diagnostic"):
        versions._run_go(
            tmp_path, {"example.com/lib": ("v1.2.3", "go.example.lib")},
            {"example.com/lib": {"module": "example.com/lib", "proxy": "https://proxy.example"}},
        )


def test_go_netrc_refuses_unsafe_credentials_and_multiple_proxies(monkeypatch, tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/demo\n", encoding="utf-8")
    monkeypatch.setenv("CMRU_GO_USER", "alice name")
    monkeypatch.setenv("CMRU_GO_PASSWORD", "secret")
    with pytest.raises(versions.VersionsError, match="cannot contain whitespace"):
        with versions._temporary_netrc(
            "https://proxy.example", None, "CMRU_GO_USER", "CMRU_GO_PASSWORD",
        ):
            pytest.fail("unsafe netrc credentials should refuse")
    with pytest.raises(versions.VersionsError, match="same proxy"):
        versions._run_go(
            tmp_path,
            {"example.com/a": ("v1.0.0", "go.a"), "example.com/b": ("v1.0.0", "go.b")},
            {
                "example.com/a": {"module": "example.com/a", "proxy": "https://one.example"},
                "example.com/b": {"module": "example.com/b", "proxy": "https://two.example"},
            },
        )


def test_go_writer_prerequisites_and_npm_dependency_reader_failures(monkeypatch, tmp_path):
    versions._run_go(tmp_path, {}, {})
    with pytest.raises(versions.VersionsError, match="require .*go.mod"):
        versions._run_go(
            tmp_path, {"example.com/lib": ("v1.2.3", "go.lib")},
            {"example.com/lib": {"module": "example.com/lib", "proxy": "https://proxy.example"}},
        )
    go_mod = tmp_path / "go.mod"
    go_mod.write_text("module example.com/demo\n", encoding="utf-8")
    sources = {
        "a": {"module": "example.com/a", "proxy": "https://proxy.example", "token_env": "ONE"},
        "b": {"module": "example.com/b", "proxy": "https://proxy.example", "token_env": "TWO"},
    }
    with pytest.raises(versions.VersionsError, match="share one credential"):
        versions._run_go(
            tmp_path, {"example.com/a": ("v1.0.0", "go.a"), "example.com/b": ("v1.0.0", "go.b")},
            {"example.com/a": sources["a"], "example.com/b": sources["b"]},
        )

    source = {"module": "example.com/lib", "proxy": "https://proxy.example"}
    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("go")))
    with pytest.raises(versions.VersionsPrerequisiteError, match="go on PATH"):
        versions._go_workspace_files(tmp_path, {})
    with pytest.raises(versions.VersionsPrerequisiteError, match="go on PATH"):
        versions._run_go(
            tmp_path, {"example.com/lib": ("v1.2.3", "go.lib")},
            {"example.com/lib": source},
        )
    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("go", 900),
    ))
    with pytest.raises(versions.VersionsOperationError, match="did not finish within 20 seconds"):
        versions._go_workspace_files(tmp_path, {})
    with pytest.raises(versions.VersionsOperationError, match="did not finish"):
        versions._run_go(
            tmp_path, {"example.com/lib": ("v1.2.3", "go.lib")},
            {"example.com/lib": source},
        )

    package_json = tmp_path / "package.json"
    package_json.unlink(missing_ok=True)
    assert versions._npm_declared_names(tmp_path) == set()
    package_json.write_text("{", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not parse"):
        versions._npm_declared_names(tmp_path)
    package_json.write_text("[]", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="JSON object"):
        versions._npm_declared_names(tmp_path)
    package_json.write_text("{}", encoding="utf-8")
    lock = tmp_path / "package-lock.json"
    lock.write_text("{", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="could not parse .*package-lock"):
        versions._npm_declared_names(tmp_path)
    lock.write_text("[]", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="JSON object"):
        versions._npm_declared_names(tmp_path)
    lock.write_text(json.dumps({"packages": ["not a table"]}), encoding="utf-8")
    assert versions._npm_declared_names(tmp_path) == set()


def test_npm_declared_names_tolerates_partial_lock_metadata(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"a": "*"}}), encoding="utf-8")
    (tmp_path / "npm-shrinkwrap.json").write_text(json.dumps({"packages": {
        "node_modules/nameless": {}, "node_modules/named": {"name": "explicit"},
    }}), encoding="utf-8")
    (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {
        "node_modules/not-a-map": "ignored", "": {},
    }}), encoding="utf-8")
    assert versions._npm_declared_names(tmp_path) == {"a", "nameless", "explicit"}


def test_custom_template_and_oci_outputs_are_transaction_ready(monkeypatch, tmp_path):
    template = tmp_path / "versions.j2"
    template.write_text("{{ targets['oci.app'].version }} {{ resolved_at }}", encoding="utf-8")
    environment_options = []

    class Rendered:
        def __init__(self, source):
            self.source = source

        def render(self, **context):
            return self.source.replace("{{ targets['oci.app'].version }}", context["targets"]["oci.app"]["version"]).replace(
                "{{ resolved_at }}", context["resolved_at"],
            )

    class Environment:
        def __init__(self, **kwargs):
            environment_options.append(kwargs)

        def from_string(self, source):
            return Rendered(source)

    strict_undefined = object()
    monkeypatch.setitem(sys.modules, "jinja2", types.SimpleNamespace(Environment=Environment, StrictUndefined=strict_undefined))
    result = _result(
        "oci.app", "oci", "1.2.0", datetime(2026, 9, 1, tzinfo=timezone.utc), tag="v1.2.0",
    )
    artifacts = versions._template_artifacts(
        tmp_path,
        {"custom": {"template": "versions.j2", "path": "out/stable.txt", "dated_path": "out/{date}.txt"}},
        {"oci.app": result},
        "20260924",
    )
    assert {path.relative_to(tmp_path).as_posix() for path, _ in artifacts} == {
        "out/stable.txt", "out/20260924.txt",
    }
    assert all(content.startswith("1.2.0 2026-09-24") for _, content in artifacts)
    assert len(environment_options) == 1
    assert environment_options[0]["undefined"] is strict_undefined
    assert environment_options[0]["autoescape"] is False

    declaration = {"oci.app": {"oci": {"image": "ghcr.io/acme/app", "tag": "v{version}"}}}
    files = versions._native_output_files(
        tmp_path, {"oci.app": result}, declaration, now=result.resolved_at,
        outputs={}, oci_results={"oci.app": result},
    )
    assert {path.name for path, _ in files} == {"oci-images-20260924.json", "oci-images.json"}
    payload = json.loads(files[0][1])
    assert files[0][1].index('  "generated_by"') < files[0][1].index('  "resolved_at"')
    assert files[0][1].index('  "resolved_at"') < files[0][1].index('  "schema_version"')
    assert payload["generated_by"] == "cmru versions resolve"
    assert payload["targets"]["oci.app"]["tag"] == "v1.2.0"

    stable = tmp_path / "versions" / "oci-images.json"
    stable.parent.mkdir()
    stable.write_text('{"generated_by": "someone else"}', encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="not generated by CMRU"):
        versions._native_output_files(
            tmp_path, {"oci.app": result}, declaration, now=result.resolved_at,
            outputs={}, oci_results={"oci.app": result},
        )


def test_template_dependency_and_strict_render_failures(monkeypatch, tmp_path):
    template = tmp_path / "template.j2"
    template.write_text("{{ missing }}", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "jinja2", None)
    with pytest.raises(versions.VersionsError, match="versions-templates extra"):
        versions._template_artifacts(
            tmp_path, {"out": {"template": "template.j2", "path": "out.txt", "dated_path": "out-{date}.txt"}},
            {}, "20260924",
        )

    class Environment:
        def __init__(self, **_kwargs):
            pass

        def from_string(self, _source):
            return self

        def render(self, **_context):
            raise RuntimeError("undefined value")

    monkeypatch.setitem(sys.modules, "jinja2", types.SimpleNamespace(Environment=Environment, StrictUndefined=object()))
    with pytest.raises(versions.VersionsError, match="could not render"):
        versions._template_artifacts(
            tmp_path, {"out": {"template": "template.j2", "path": "out.txt", "dated_path": "out-{date}.txt"}},
            {}, "20260924",
        )


def test_python_constraints_compile_uses_age_policy_and_markers(monkeypatch, tmp_path):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=14)
    older = _result("pypi.requests", "pypi", "2.31.0", cutoff - timedelta(days=1))
    fresh = _result("pypi.urgent", "pypi", "1.1.0", now - timedelta(days=1))
    declarations = {
        "pypi.requests": {"pypi": {"name": "requests", "registry": "https://pypi.org", "token_env": "CMRU_PYPI_TOKEN"}},
        "pypi.urgent": {"pypi": {"name": "urgent", "registry": "https://pypi.org", "token_env": "CMRU_PYPI_TOKEN"}},
    }
    monkeypatch.setenv("CMRU_PYPI_TOKEN", "secret")
    captured = []

    class Process:
        returncode = 0
        stdout = "requests==2.31.0\nurgent==1.1.0\n"
        stderr = ""

    def run(command, **kwargs):
        captured.append((command, kwargs))
        return Process()

    monkeypatch.setattr(versions.subprocess, "run", run)
    generated = versions._compile_python_constraints(
        tmp_path, {"pypi.requests": older, "pypi.urgent": fresh}, declarations,
    )
    command, kwargs = captured[0]
    assert command[:4] == ["uv", "pip", "compile", "-"]
    assert command[command.index("--exclude-newer") + 1] == versions._timestamp(cutoff)
    assert "--exclude-newer-package" in command and "urgent=false" in command
    assert kwargs["env"]["UV_INDEX_CMRU_USERNAME"] == "__token__"
    assert kwargs["env"]["UV_INDEX_CMRU_PASSWORD"] == "secret"
    assert kwargs["input"] == "requests==2.31.0\nurgent==1.1.0\n"
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False
    assert all(content.startswith("# Generated by cmru versions resolve") for _path, content in generated)
    assert [path.name for path, _ in generated] == ["constraints-20260924.txt", "constraints.txt"]
    assert versions._compile_python_constraints(tmp_path, {}, {}) == []

    exactly_at_cutoff = _result("pypi.exact", "pypi", "1.0.0", cutoff)
    versions._compile_python_constraints(
        tmp_path, {"pypi.exact": exactly_at_cutoff},
        {"pypi.exact": {"pypi": {"name": "exact", "registry": "https://pypi.org"}}},
    )
    assert "--exclude-newer-package" not in captured[-1][0]


def test_python_constraints_refuse_conflicts_markers_and_tool_errors(monkeypatch, tmp_path):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    a = _result("pypi.a", "pypi", "1.0.0", now - timedelta(days=30))
    b = _result("pypi.b", "pypi", "2.0.0", now - timedelta(days=30))
    with pytest.raises(versions.VersionsError, match="conflicting target versions"):
        versions._compile_python_constraints(
            tmp_path,
            {"pypi.a": a, "pypi.b": b},
            {
                "pypi.a": {"pypi": {"name": "requests", "registry": "https://pypi.org"}},
                "pypi.b": {"pypi": {"name": "requests", "registry": "https://pypi.org"}},
            },
        )
    same = _result("pypi.b", "pypi", "1.0.0", now - timedelta(days=30))
    with pytest.raises(versions.VersionsError, match="multiple PyPI targets"):
        versions._compile_python_constraints(
            tmp_path,
            {"pypi.a": a, "pypi.b": same},
            {
                "pypi.a": {"pypi": {"name": "Requests", "registry": "https://pypi.org"}},
                "pypi.b": {"pypi": {"name": "requests", "registry": "https://elsewhere.example"}},
            },
        )
    with pytest.raises(versions.VersionsError, match="same registry"):
        versions._compile_python_constraints(
            tmp_path, {"pypi.a": a, "pypi.b": b},
            {
                "pypi.a": {"pypi": {"name": "a", "registry": "https://pypi.org"}},
                "pypi.b": {"pypi": {"name": "b", "registry": "https://index.example"}},
            },
        )

    result = _result("pypi.a", "pypi", "1.0.0", now - timedelta(days=30))
    declaration = {"pypi.a": {"pypi": {"name": "a", "registry": "https://pypi.org"}}}
    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(
        returncode=1, stdout="", stderr="compile failed",
    ))
    with pytest.raises(versions.VersionsOperationError, match="uv pip compile failed"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, declaration)
    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(
        returncode=1, stdout="fallback output", stderr="",
    ))
    with pytest.raises(versions.VersionsOperationError, match="fallback output"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, declaration)
    monkeypatch.setattr(versions.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(
        returncode=0, stdout="", stderr="",
    ))
    with pytest.raises(versions.VersionsError, match="without producing"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, declaration)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("uv")

    monkeypatch.setattr(versions.subprocess, "run", missing)
    with pytest.raises(versions.VersionsPrerequisiteError, match="uv on PATH"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, declaration)

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("uv", 900)

    monkeypatch.setattr(versions.subprocess, "run", timeout)
    with pytest.raises(versions.VersionsOperationError, match="did not finish"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, declaration)

    generated = tmp_path / "constraints"
    generated.mkdir()
    (generated / "constraints.txt").write_text("manual\n", encoding="utf-8")
    with pytest.raises(versions.VersionsError, match="non-CMRU constraints"):
        versions._compile_python_constraints(tmp_path, {"pypi.a": result}, declaration)

    missing_auth = {"pypi.a": {"pypi": {"name": "a", "registry": "https://pypi.org", "token_env": "CMRU_ABSENT_TOKEN"}}}
    with pytest.raises(versions.VersionsPrerequisiteError, match="CMRU_ABSENT_TOKEN"):
        versions._compile_python_constraints(tmp_path / "other", {"pypi.a": result}, missing_auth)
