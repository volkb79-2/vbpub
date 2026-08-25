from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assay.config import LaneConfigError, load_lane_file
from assay.errors import AssayError, Outcome, ReasonCode
from assay.runner import resolve_command_plan, run_lane
from conftest import make_lane


def _write_lane(tmp_path: Path, infrastructure: str) -> Path:
    path = tmp_path / "assay.toml"
    path.write_text(
        f"""
schema_version = 2

[lanes.package]
scope = "S1"
    rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {{ }}
env_passthrough = []
budget = "5m"
allow_argv_append = false

[lanes.package.infrastructure]
{infrastructure}
""",
        encoding="utf-8",
    )
    return path


def test_loader_parses_and_round_trips_declared_facts(tmp_path):
    path = _write_lane(
        tmp_path,
        'network = "required-env:NETWORK"\nimage = "derived:deploy.image"\n',
    )
    lane = load_lane_file(path).lane("package")
    assert lane.as_declared()["infrastructure"] == {
        "network": "required-env:NETWORK",
        "image": "derived:deploy.image",
    }


@pytest.mark.parametrize("declaration", ["", "env:X", "required-env:", "derived:..", "derived:a..b"])
def test_invalid_declarations_refuse_at_load_with_named_key(tmp_path, declaration):
    path = _write_lane(tmp_path, f'network = "{declaration}"')
    with pytest.raises(LaneConfigError) as caught:
        load_lane_file(path)
    assert "'infrastructure.network'" in str(caught.value)


def test_collision_with_env_or_passthrough_is_refused(tmp_path):
    path = tmp_path / "assay.toml"
    path.write_text(
        """
schema_version = 2

[lanes.package]
scope = "S1"
    rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = { NETWORK = "fixed" }
env_passthrough = []
budget = "5m"
allow_argv_append = false

[lanes.package.infrastructure]
NETWORK = "required-env:SOURCE"
""",
        encoding="utf-8",
    )
    with pytest.raises(LaneConfigError, match="infrastructure.*own its name"):
        load_lane_file(path)


def test_env_passthrough_refuses_rather_than_silently_overwriting_infrastructure(tmp_path):
    """(B022) `config.py`'s loader already refuses this exact collision at
    load time (the test above) -- but the RUNTIME had no defence of its
    own: `resolve_command_plan`'s `env_passthrough` loop ran AFTER the
    infrastructure loop and unconditionally overwrote, so if the loader
    check were ever weakened, a passthrough value would silently win over
    an infrastructure-injected one, the opposite of A-293's "every injected
    fact has exactly one owner". Constructing the `Lane` directly (bypassing
    the loader, which is the whole point of `make_lane`) reaches the runtime
    path on its own."""
    lane = make_lane(
        infrastructure={"NETWORK": "required-env:SOURCE_VAR"},
        env_passthrough=("NETWORK",),
    )
    with pytest.raises(AssayError, match="env_passthrough.*NETWORK.*collides"):
        resolve_command_plan(
            lane,
            passthrough_source={"NETWORK": "from-passthrough"},
            infrastructure_source=None,
            infrastructure_environment={"SOURCE_VAR": "from-infrastructure"},
        )


def test_an_oversized_resolved_value_refuses_rather_than_failing_late_at_exec(tmp_path):
    """(B022 item 4) A `derived:` value landing on something far larger than
    any real infrastructure fact -- a whole file's content instead of one
    field, say -- used to reach `env_effective` unbounded and fail only much
    later, opaquely, at `E2BIG` on exec. `resolve_command_plan` now refuses
    it directly, by name, with the byte count that tripped the bound."""
    facts = tmp_path / "ciu.global.toml"
    oversized = "x" * (65536 + 1)
    facts.write_text(f"[deploy]\nimage = '{oversized}'\n", encoding="utf-8")
    lane = make_lane(infrastructure={"image": "derived:deploy.image"})
    with pytest.raises(AssayError, match=r"image.*65537 bytes"):
        resolve_command_plan(lane, passthrough_source={}, infrastructure_source=facts)


def test_required_env_resolves_and_missing_or_empty_refuses_named_key(tmp_path):
    lane = make_lane(infrastructure={"network": "required-env:NETWORK_SOURCE"})
    plan = resolve_command_plan(
        lane,
        passthrough_source={},
        infrastructure_source=tmp_path,
        infrastructure_environment={"NETWORK_SOURCE": "infra-network"},
    )
    assert plan.env_effective["network"] == "infra-network"

    from assay.errors import AssayError
    with pytest.raises(Exception) as missing:
        resolve_command_plan(lane, passthrough_source={}, infrastructure_source=tmp_path)
    assert "NETWORK_SOURCE" in str(missing.value)


def test_derived_fact_requires_rendered_toml_value(tmp_path):
    facts = tmp_path / "ciu.global.toml"
    facts.write_text("[deploy]\nimage = 'postgres:18'\n", encoding="utf-8")
    lane = make_lane(infrastructure={"image": "derived:deploy.image"})
    plan = resolve_command_plan(lane, passthrough_source={}, infrastructure_source=facts)
    assert plan.env_effective["image"] == "postgres:18"

    facts.write_text("", encoding="utf-8")
    from assay.errors import AssayError
    with pytest.raises(AssayError) as caught:
        resolve_command_plan(lane, passthrough_source={}, infrastructure_source=facts)
    assert "deploy.image" in str(caught.value)


def test_lane_without_infrastructure_ignores_the_source(tmp_path):
    lane = make_lane()
    plan = resolve_command_plan(lane, passthrough_source={}, infrastructure_source=None)
    assert plan.env_effective == {}


def test_a_derived_fact_with_no_infrastructure_source_refuses(tmp_path):
    """(B013 remediation) A lane declaring `derived:` must be paired with a
    source to resolve it against. Refused as an `AssayError`/`BAD_LANE_
    CONFIG`, not the bare `ValueError` this used to be -- every other
    refusal in `resolve_command_plan` is already a typed `AssayError`, and
    an untyped one is invisible to any caller catching the documented
    contract, `cli.py`'s own `main()` included."""
    lane = make_lane(infrastructure={"image": "derived:deploy.image"})
    from assay.errors import AssayError

    with pytest.raises(AssayError) as caught:
        resolve_command_plan(lane, passthrough_source={}, infrastructure_source=None)
    assert "infrastructure_source" in str(caught.value)


def _run_r0(repo: Path, *, infrastructure_environment={"NETWORK_SOURCE": "network"}) -> tuple[int, str]:
    (repo / "assay.toml").write_text(
        """
schema_version = 2

[lanes.package]
scope = "S1"
    rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "[ \\"$INFRA_NETWORK\\" = network ] && printf committed > out.txt || exit 9"]
env = { }
env_passthrough = []
budget = "5m"
allow_argv_append = false

[lanes.package.infrastructure]
INFRA_NETWORK = "required-env:NETWORK_SOURCE"
""",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(".assay/\nout.txt\n", encoding="utf-8")
    (repo / "left.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    proc = subprocess.run(["git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True) == ""
    from assay.config import load_lane_file

    lane_file = load_lane_file(repo / "assay.toml")
    verdict = run_lane(
        lane_file.lane("package"),
        commit=subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"], text=True).strip(),
        repo=repo,
        project_root=repo,
        adapter=None,
        assay_version="test",
        passthrough_source={},
        process_runner=subprocess.run,
        clock=lambda: datetime.now(timezone.utc),
        infrastructure_environment=infrastructure_environment,
    )
    return verdict.outcome, verdict.reason_code.value if verdict.reason_code else ""


def test_direct_r0_lane_resolves_declared_facts_in_invoking_context(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
    outcome, reason = _run_r0(repo)
    assert (outcome, reason) == (Outcome.PASS, ""), f"expected PASS; got {reason}"
    assert (repo / "out.txt").read_text(encoding="utf-8") == "committed"
    assert subprocess.check_output(["git", "-C", repo, "status", "--porcelain"], text=True) == ""


def test_missing_fact_refuses_before_command_execution(tmp_path):
    """(B025) `run_lane`'s direct R0-only path used to let this raise
    uncaught -- the third of three sites an unresolvable infrastructure
    declaration could crash a refusal from (see `runner.py`'s own comment at
    this exact call site). It now refuses cleanly instead, matching every
    other post-HEAD-resolution refusal: `ERROR`/`BAD_LANE_CONFIG`, no
    traceback. The specific missing variable name is not recoverable from
    `(outcome, reason_code)` alone -- the same reason-code-only diagnosis
    every `run` refusal gives (B026 N-4's own accepted asymmetry), not new
    information loss this fix introduced."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", repo, "init", "-q"], check=True)
    outcome, reason = _run_r0(repo, infrastructure_environment={})
    assert (outcome, reason) == (Outcome.ERROR, "BAD_LANE_CONFIG")
