"""S13.4c / CIU-64 — `ciu up` runs `ciu check`'s static pipeline itself.

Oracles:
- An ERROR-severity finding from a stack's own `validate_config` hook (S9.5)
  refuses `ciu up` BEFORE STEP 1: `action_deploy` is never entered, exit 2,
  the same class the `[S7.x]` provisioning-graph refusal already produces.
- A bare-string finding (the pre-CIU-65 shape) blocks identically — the old
  spelling did not lose its teeth.
- A WARN-severity finding does NOT block, and is still VISIBLE in the output.
  A two-tier vocabulary whose lower tier blocks is a one-tier vocabulary; a
  lower tier that is silently swallowed is worse than not having one.
- `--skip-check` lets an ERROR through AND announces that it did. A silently
  skipped gate is a gate that is not there.
- A clean config deploys with the preflight on, unannounced-refusal-free —
  the legitimate state, constructed, so this refusal cannot be the kind that
  cries wolf on a healthy estate and gets switched off.

The preflight itself is REAL in every test here: `check_preflight` and
`action_check` are never stubbed, so these drive the whole chain from argv
through the hook import to the refusal. Only the surrounding I/O (env
bootstrap, repo-root resolution, the sibling preflights, and the deploy
itself) is replaced.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402

_TRIVIAL_COMPOSE = "services:\n  app:\n    image: busybox\n"
_GLOBAL: dict = {"deploy": {"project_name": "p", "environment_tag": "t"}}


def _hook_stack(repo_root: Path, rel: str, hook_body: str) -> dict:
    """One on-disk stack whose single post_compose hook validates its config."""
    stack_dir = repo_root / rel
    stack_dir.mkdir(parents=True, exist_ok=True)
    (stack_dir / "ciu.compose.yml.j2").write_text(_TRIVIAL_COMPOSE, encoding="utf-8")
    (stack_dir / "h.py").write_text(
        textwrap.dedent(
            f"""\
            def run(config, ctx):
                raise AssertionError("run() must not execute during a preflight")

            def validate_config(config, ctx):
{hook_body}
            """
        ),
        encoding="utf-8",
    )
    return {"app_stack": {"hooks": {"post_compose": ["h.py"]}}}


@pytest.fixture
def up_harness(monkeypatch, tmp_path):
    """`deploy.main()` wired to a real check preflight and a recording deploy.

    Returns a callable ``run(hook_body, *argv)`` → ``(rc, deployed, output)``
    where *deployed* is the list of `action_deploy` invocations (empty means
    the refusal fired before STEP 1).
    """
    profile = Profile(name=None, phase_keys=None, config={"deploy": dict(_GLOBAL["deploy"])})
    rel = "infra/app"
    selection = [{"path": rel, "service": {"name": "app"}, "phase": 1}]
    deployed: list[dict] = []

    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kw: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda _cwd: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: profile.config)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda _cfg, _names: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda *_a: selection)
    for name in (
        "vault_preflight", "producer_preflight", "provisioning_preflight",
        "registry_preflight", "governance_slice_preflight",
        "ensure_workspace_network",
    ):
        monkeypatch.setattr(deploy, name, lambda *_a, **_kw: None)
    monkeypatch.setattr(
        deploy, "action_deploy",
        lambda *_a, **kw: deployed.append(kw) or 0,
    )

    def run(hook_body: str, *argv: str, capsys=None):
        rendered = {rel: _hook_stack(tmp_path, rel, hook_body)}
        monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_a, **_kw: rendered)
        rc = deploy.main(["--deploy", *argv])
        return rc, deployed

    run.deployed = deployed  # type: ignore[attr-defined]
    return run


def test_error_finding_refuses_up_before_step_1(up_harness, capsys):
    rc, deployed = up_harness('                return [("ERROR", "registry.database is missing")]')

    assert rc == 2
    assert deployed == [], "the deploy must not be entered at all"
    out = capsys.readouterr().out
    assert "registry.database is missing" in out
    assert "refusing to deploy before anything starts" in out


def test_bare_string_finding_refuses_up_too(up_harness, capsys):
    """The pre-CIU-65 spelling keeps its teeth under the new preflight."""
    rc, deployed = up_harness('                return ["old style finding"]')

    assert rc == 2
    assert deployed == []
    assert "old style finding" in capsys.readouterr().out


def test_warn_finding_does_not_block_but_is_visible(up_harness, capsys):
    rc, deployed = up_harness('                return [("WARN", "readonly role is absent")]')

    assert rc == 0
    assert len(deployed) == 1, "a WARN must not stop the deploy"
    out = capsys.readouterr().out
    assert "[WARN] readonly role is absent" in out, "a WARN must not be swallowed either"
    assert "refusing to deploy" not in out


def test_skip_check_lets_an_error_through_and_says_so(up_harness, capsys):
    rc, deployed = up_harness(
        '                return [("ERROR", "registry.database is missing")]',
        "--skip-check",
    )

    assert rc == 0
    assert len(deployed) == 1
    out = capsys.readouterr().out
    assert "--skip-check" in out
    assert "break-glass" in out
    # The gate did not run, so its finding is nowhere to be seen — which is
    # exactly why the skip has to announce itself.
    assert "registry.database is missing" not in out


def test_a_clean_config_deploys_with_the_preflight_on(up_harness, capsys):
    """The legitimate state. A refusal whose condition also matches an
    ordinary, healthy run is a superset refusal, and gets switched off."""
    rc, deployed = up_harness("                return []")

    assert rc == 0
    assert len(deployed) == 1
    out = capsys.readouterr().out
    assert "Preflight: running `ciu check`'s static validation" in out
    assert "refusing to deploy" not in out


def test_dry_run_gets_the_same_refusal(up_harness, capsys):
    """--dry-run exists to find exactly this class of defect, and the check
    is side-effect-free either way."""
    rc, deployed = up_harness(
        '                return ["a dry run must still refuse this"]', "--dry-run",
    )

    assert rc == 2
    assert deployed == []
    assert "a dry run must still refuse this" in capsys.readouterr().out


def test_skip_check_defaults_to_off(up_harness):
    """Controlled wrong implementation: if `--skip-check` were the default,
    the ERROR case above would deploy. It does not."""
    rc, deployed = up_harness('                return ["blocking"]')
    assert (rc, deployed) == (2, [])
