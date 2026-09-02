"""S13.7 / S13.4d / S3.4a — `ciu migration-check` and ciu-P46's three new stages.

Three subjects, deliberately in one file because they ship as one contract:

* the ``ciu migration-check`` rule REGISTRY and its two entry points (the verb
  and ``ciu check``'s ``migration`` stage) — including that neither
  reimplements the other's detection;
* the ``vault-presence`` static stage (F7);
* the ``state-secrets`` static stage (S3.4a) and the secret-shape heuristic
  underneath it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, deploy, migration_check  # noqa: E402
from ciu.config_constants import (  # noqa: E402
    GLOBAL_CONFIG_WORKTREE_OVERRIDES,
    WORKSPACE_ENV,
)
from ciu.deploy_pkg.profiles import Profile  # noqa: E402
from ciu.scaffold import _GITIGNORE_ENTRIES  # noqa: E402
from ciu.workspace_env import GENERATED_FACTS_HEADER, GENERATED_FACTS_KEYS  # noqa: E402


def _complete_gitignore(root: Path) -> None:
    root.joinpath(".gitignore").write_text(
        "\n".join(entry for entry, _why in _GITIGNORE_ENTRIES) + "\n",
        encoding="utf-8",
    )


def _complete_identity(root: Path) -> None:
    lines = [GENERATED_FACTS_HEADER]
    lines += [f'{key} = "x"' for key in GENERATED_FACTS_KEYS]
    root.joinpath(GLOBAL_CONFIG_WORKTREE_OVERRIDES).write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _clean_repo(tmp_path: Path) -> Path:
    """A checkout every v1 rule is satisfied by."""
    root = tmp_path / "repo"
    root.mkdir()
    _complete_gitignore(root)
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")
    _complete_identity(root)
    return root


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------

def test_the_registry_is_the_documented_v1_rule_set() -> None:
    """Exactly three rules; a secret-shaped-[state] rule is deliberately NOT one.

    That fourth candidate was dropped during design as 100% redundant with the
    always-on `state-secrets` stage, which fires on that condition regardless
    of the artifact's age. Pinned by name so re-adding it is a deliberate act.
    """
    assert [rule.name for rule in migration_check.RULES] == [
        "retired-overlay-file",
        "stale-identity-facts",
        "gitignore-gaps",
    ]


def test_every_rule_carries_a_detector_and_prose() -> None:
    for rule in migration_check.RULES:
        assert callable(rule.detector)
        assert rule.description


def test_no_detector_reads_an_installed_version(tmp_path: Path) -> None:
    """Every rule is pattern-based: it answers from the checkout alone.

    Proven behaviourally rather than by inspection — the SAME checkout must
    produce the SAME findings no matter what CIU reports as its own version,
    which is only possible if no detector consults it.
    """
    root = _clean_repo(tmp_path)
    root.joinpath(".gitignore").write_text("nothing-ciu-owned\n", encoding="utf-8")

    before = migration_check.run_migration_check(root)
    import ciu.cli_utils as cli_utils

    original = cli_utils.get_cli_version
    try:
        cli_utils.get_cli_version = lambda: "0.0.1-ancient"  # type: ignore[assignment]
        after = migration_check.run_migration_check(root)
    finally:
        cli_utils.get_cli_version = original  # type: ignore[assignment]

    assert [f.as_dict() for f in before] == [f.as_dict() for f in after]


def test_a_clean_checkout_has_no_findings(tmp_path: Path) -> None:
    assert migration_check.run_migration_check(_clean_repo(tmp_path)) == []


def test_every_finding_carries_a_remediation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")
    root.joinpath(".gitignore").write_text("unrelated\n", encoding="utf-8")

    findings = migration_check.run_migration_check(root)

    assert findings
    for finding in findings:
        assert finding.severity in migration_check.SEVERITIES
        assert finding.message and finding.remediation


# ---------------------------------------------------------------------------
# Rule 1 — retired overlay filename
# ---------------------------------------------------------------------------

def test_retired_overlay_rule_is_silent_while_the_name_is_still_current(
    tmp_path: Path,
) -> None:
    """As of ciu-P46 the rename has not happened, so this rule finds nothing.

    The detector filters its history list against the LIVE constant, which is
    what makes it correct both today (the name is current → silent) and after
    ciu-P47 flips that constant (the name is retired → live), with no edit to
    the detector or the registry.
    """
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(GLOBAL_CONFIG_WORKTREE_OVERRIDES).write_text("x = 1\n")

    assert GLOBAL_CONFIG_WORKTREE_OVERRIDES in migration_check.RETIRED_OVERLAY_NAMES
    assert migration_check.detect_retired_overlay(root) == []


def test_retired_overlay_rule_fires_once_the_name_is_no_longer_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ciu-P47 shape, proven now: rename the constant, the rule goes live."""
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath("ciu.global.worktree.toml.j2").write_text("x = 1\n")
    monkeypatch.setattr(
        migration_check, "GLOBAL_CONFIG_WORKTREE_OVERRIDES",
        "ciu.global.instance.toml.j2",
    )

    findings = migration_check.detect_retired_overlay(root)

    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert "ciu.global.worktree.toml.j2" in findings[0].message
    assert "ciu.global.instance.toml.j2" in findings[0].remediation


def test_retired_overlay_rule_is_silent_when_the_leftover_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(
        migration_check, "GLOBAL_CONFIG_WORKTREE_OVERRIDES",
        "ciu.global.instance.toml.j2",
    )
    assert migration_check.detect_retired_overlay(root) == []


# ---------------------------------------------------------------------------
# Rule 2 — stale identity facts (CIU-60/CIU-75)
# ---------------------------------------------------------------------------

def test_identity_rule_is_silent_without_ciu_env(tmp_path: Path) -> None:
    """No `ciu.env` at all is a first-run state, not a migration."""
    root = tmp_path / "repo"
    root.mkdir()
    assert migration_check.detect_stale_identity_facts(root) == []


def test_identity_rule_fires_when_the_generated_table_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")

    findings = migration_check.detect_stale_identity_facts(root)

    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert "no such table at all" in findings[0].message
    assert "ciu env generate" in findings[0].remediation


def test_identity_rule_fires_when_the_generated_table_is_incomplete(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")
    root.joinpath(GLOBAL_CONFIG_WORKTREE_OVERRIDES).write_text(
        f'{GENERATED_FACTS_HEADER}\nrepo_name = "x"\n', encoding="utf-8"
    )

    findings = migration_check.detect_stale_identity_facts(root)

    assert len(findings) == 1
    # Names exactly what is missing, so the reader can see it is not cosmetic.
    for key in GENERATED_FACTS_KEYS:
        if key != "repo_name":
            assert key in findings[0].message


def test_identity_rule_reports_an_unreadable_table_rather_than_raising(
    tmp_path: Path,
) -> None:
    """Indeterminacy is a finding, never a crash inside `ciu check`."""
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")
    root.joinpath(GLOBAL_CONFIG_WORKTREE_OVERRIDES).write_text(
        f"{GENERATED_FACTS_HEADER}\nrepo_name = \n", encoding="utf-8"
    )

    findings = migration_check.detect_stale_identity_facts(root)

    assert len(findings) == 1
    assert "could not be read" in findings[0].message


def test_identity_rule_is_silent_on_a_complete_table(tmp_path: Path) -> None:
    root = _clean_repo(tmp_path)
    assert migration_check.detect_stale_identity_facts(root) == []


# ---------------------------------------------------------------------------
# Rule 3 — CIU-61 gitignore gaps
# ---------------------------------------------------------------------------

def test_gitignore_rule_names_every_missing_entry(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(".gitignore").write_text("ciu.env\n", encoding="utf-8")

    findings = migration_check.detect_gitignore_gaps(root)

    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    listed = findings[0].message.split(": ", 1)[1].split(", ")
    assert set(listed) == {
        entry for entry, _why in _GITIGNORE_ENTRIES if entry != "ciu.env"
    }


def test_gitignore_rule_reuses_ciu61_comment_normalization(tmp_path: Path) -> None:
    """A prior `ciu init`'s trailing `  # why` must not be read as the pattern."""
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(".gitignore").write_text(
        "\n".join(f"{entry}  # {why}" for entry, why in _GITIGNORE_ENTRIES) + "\n",
        encoding="utf-8",
    )

    assert migration_check.detect_gitignore_gaps(root) == []


def test_gitignore_rule_is_silent_without_a_gitignore(tmp_path: Path) -> None:
    """A repo that never ran `ciu init` is a first-run state, not a migration."""
    root = tmp_path / "repo"
    root.mkdir()
    assert migration_check.detect_gitignore_gaps(root) == []


def test_gitignore_rule_reports_an_unreadable_gitignore(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(".gitignore").mkdir()

    findings = migration_check.detect_gitignore_gaps(root)

    assert len(findings) == 1
    assert "could not be read" in findings[0].message


# ---------------------------------------------------------------------------
# Entry point 1 — the standalone verb
# ---------------------------------------------------------------------------

def test_verb_exits_zero_and_says_so_when_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clean_repo(tmp_path)
    assert migration_check.main(["--define-root", str(root)]) == 0
    assert "no stale artifacts" in capsys.readouterr().out


def test_verb_exits_non_zero_on_a_WARN_only_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """S13.7's deliberate divergence from `ciu check`: ANY finding is non-zero.

    Every v1 rule is WARN-severity, so a severity-gated exit would make this
    verb silently useless to the scripts most likely to call it.
    """
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")

    assert migration_check.main(["--define-root", str(root)]) != 0
    err = capsys.readouterr().err
    assert "[WARN] stale-identity-facts" in err
    assert "fix:" in err


def test_verb_json_is_a_versioned_envelope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")

    code = migration_check.main(["--define-root", str(root), "--json"])
    document = json.loads(capsys.readouterr().out)

    assert code != 0
    assert document["schema_version"] == migration_check.MIGRATION_CHECK_SCHEMA_VERSION
    assert document["operation"] == "migration-check"
    assert document["status"] == "fail"
    assert document["rules"] == [rule.name for rule in migration_check.RULES]
    assert document["findings"][0]["rule"] == "stale-identity-facts"


def test_verb_json_passes_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clean_repo(tmp_path)
    assert migration_check.main(["--define-root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_verb_refuses_a_define_root_that_disagrees_with_REPO_ROOT(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CIU-54's established root-resolution convention, not a bespoke one."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path / "elsewhere"))
    with pytest.raises(SystemExit) as excinfo:
        migration_check.main(["--define-root", str(_clean_repo(tmp_path))])
    assert excinfo.value.code == 2


def test_cli_routes_the_verb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ciu import cli

    root = _clean_repo(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ciu", "migration-check", "--define-root", str(root)])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0


def test_cli_help_documents_the_verb() -> None:
    from ciu import cli

    assert "migration-check" in cli._USAGE
    assert "S13.7" in cli._VERB_HELP["migration-check"]


# ---------------------------------------------------------------------------
# Entry point 2 — the `ciu check` stage
# ---------------------------------------------------------------------------

def test_the_three_new_stages_are_appended_last() -> None:
    """Appended, never interleaved — stages 1-13 keep their pinned positions."""
    assert deploy.CHECK_STAGES[-3:] == ("vault-presence", "state-secrets", "migration")


def test_check_stage_notes_a_WARN_without_failing_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A WARN weighs what a WARN weighs everywhere else in this report.

    `ciu up`'s automatic preflight (S13.4c) runs this stage, so a WARN that
    failed the stage would start hard-blocking deploys on an advisory.
    """
    root = tmp_path / "repo"
    root.mkdir()
    root.joinpath(WORKSPACE_ENV).write_text("REPO_ROOT=/x\n", encoding="utf-8")

    assert deploy.action_check(root, Profile(), [], {}, json_output=True) == 0
    document = json.loads(capsys.readouterr().out)
    stage = next(s for s in document["stages"] if s["stage"] == "migration")
    assert stage["status"] == "pass"
    assert stage["findings"] == []
    assert "stale-identity-facts" in stage["notes"][0]["message"]
    assert "fix:" in stage["notes"][0]["message"]


def test_check_stage_fails_on_an_ERROR_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The severity split is real in both directions, not WARN-only by accident."""
    root = _clean_repo(tmp_path)
    monkeypatch.setattr(
        migration_check, "run_migration_check",
        lambda _root: [
            migration_check.Finding("made-up", "ERROR", "boom", "do the thing")
        ],
    )

    assert deploy.action_check(root, Profile(), [], {}, json_output=True) == 2
    document = json.loads(capsys.readouterr().out)
    stage = next(s for s in document["stages"] if s["stage"] == "migration")
    assert stage["status"] == "fail"
    assert "boom" in stage["findings"][0]["message"]


def test_check_stage_walks_the_same_registry_as_the_verb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One registry, two entry points — the stage must not reimplement detection."""
    root = _clean_repo(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(
        migration_check, "run_migration_check",
        lambda repo_root: (calls.append(repo_root) or []),
    )

    deploy.action_check(root, Profile(), [], {}, json_output=True)
    capsys.readouterr()

    assert calls == [root]


# ---------------------------------------------------------------------------
# A3 — the vault-presence stage (F7)
# ---------------------------------------------------------------------------

def _vault_stack(directive: str = "ASK_VAULT:demo/pw") -> dict:
    return {"app": {"stack_name": "app", "secrets": {"pw": directive}}}


def _check(tmp_path: Path, rendered: dict, profile_config: dict | None = None):
    root = _clean_repo(tmp_path)
    for rel in rendered:
        (root / rel).mkdir(parents=True, exist_ok=True)
    profile = Profile(config=profile_config or {})
    code = deploy.action_check(
        root, profile, [{"path": rel} for rel in rendered], rendered, json_output=True
    )
    return code


def _stage(capsys: pytest.CaptureFixture[str], name: str) -> dict:
    document = json.loads(capsys.readouterr().out)
    return next(s for s in document["stages"] if s["stage"] == name)


@pytest.mark.parametrize("directive", ["ASK_VAULT:demo/pw", "GEN_TO_VAULT:demo/pw"])
def test_vault_presence_fails_without_a_declared_vault_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], directive: str
) -> None:
    """F7 — this used to surface only at S4.16 runtime, mid-`ciu up`."""
    assert _check(tmp_path, {"apps/api": _vault_stack(directive)}) == 2
    stage = _stage(capsys, "vault-presence")
    assert stage["status"] == "fail"
    message = stage["findings"][0]["message"]
    assert "[S13.4d]" in message
    assert "topology.services.vault is not declared" in message
    assert "apps/api" in message
    assert directive.split(":")[0] in message


def test_vault_presence_passes_with_a_declared_vault_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    topology = {
        "topology": {
            "services": {"vault": {"internal_host": "vault", "internal_port": 8200}}
        }
    }
    _check(tmp_path, {"apps/api": _vault_stack()}, profile_config=topology)
    assert _stage(capsys, "vault-presence")["status"] == "pass"


def test_vault_presence_is_silent_for_a_stack_with_no_vault_directive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rendered = {"apps/api": {"app": {"stack_name": "a", "secrets": {"pw": "GEN_EPHEMERAL"}}}}
    _check(tmp_path, rendered)
    assert _stage(capsys, "vault-presence")["status"] == "pass"


def test_vault_presence_stays_silent_when_secret_discovery_already_failed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One root cause, one finding — no second complaint off unreliable specs."""
    rendered = {"apps/api": {"app": {"stack_name": "a", "secrets": {"pw": "NOT_A_DIRECTIVE"}}}}
    assert _check(tmp_path, rendered) == 2
    document = json.loads(capsys.readouterr().out)
    stages = {s["stage"]: s for s in document["stages"]}
    assert stages["secrets"]["status"] == "fail"
    assert stages["vault-presence"]["status"] == "pass"


# ---------------------------------------------------------------------------
# A4 — the state-secrets stage (S3.4a) and its heuristic
# ---------------------------------------------------------------------------

def test_state_secrets_refuses_a_secret_shaped_state_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rendered = {
        "infra/vault": {
            "vault_core": {"stack_name": "v"},
            "state": {"initialized": True, "root_token": "s.a-real-looking-token"},
        }
    }
    assert _check(tmp_path, rendered) == 2
    stage = _stage(capsys, "state-secrets")
    assert stage["status"] == "fail"
    message = stage["findings"][0]["message"]
    assert "[S3.4a]" in message
    assert "[state].root_token" in message
    assert "persist:'secret'" in message
    # S4.23 — the finding names the KEY, never the VALUE.
    assert "s.a-real-looking-token" not in message


def test_state_secrets_walks_nested_state_tables(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`persist:'state'` accepts a dotted path, so `[state]` can nest."""
    rendered = {
        "infra/vault": {
            "vault_core": {"stack_name": "v"},
            "state": {"vault": {"root_token": "s.a-real-looking-token"}},
        }
    }
    assert _check(tmp_path, rendered) == 2
    assert "[state].vault.root_token" in _stage(capsys, "state-secrets")[
        "findings"
    ][0]["message"]


def test_state_secrets_passes_on_a_non_secret_state_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rendered = {
        "infra/vault": {
            "vault_core": {"stack_name": "v"},
            "state": {"initialized": True, "token_count": 4, "mode": "dev"},
        }
    }
    _check(tmp_path, rendered)
    assert _stage(capsys, "state-secrets")["status"] == "pass"


def test_state_secrets_passes_with_no_state_table_at_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _check(tmp_path, {"apps/api": {"app": {"stack_name": "a"}}})
    assert _stage(capsys, "state-secrets")["status"] == "pass"


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("root_token", "s.a-real-looking-token", True),
        ("password", "hunter2000", True),
        ("api_key", "AKIA-something-long", True),
        ("private_key", "MIIEvQIBADANBg", True),
        ("db_passphrase", "correct-horse", True),
        ("client_secret", "abcdefghijkl", True),
        ("service_credential", "abcdefghijkl", True),
        # last component is not sensitive
        ("token_count", "abcdefghijkl", False),
        ("keyboard_layout", "abcdefghijkl", False),
        # not a literal string / too short / a pointer rather than the value
        ("root_token", True, False),
        ("root_token", 12345678, False),
        ("root_token", "short", False),
        ("root_token", "secret/demo/vault_root_token", False),
        ("root_token", "/run/secrets/root_token", False),
        ("root_token", "{{ env.VAULT_TOKEN }}", False),
        ("root_token", "$VAULT_ROOT_TOKEN", False),
        ("root_token", "${VAULT_ROOT_TOKEN}", False),
    ],
)
def test_secret_shape_heuristic(key: str, value: object, expected: bool) -> None:
    """S3.4a's key test is S2.4.1's; its value test is S3.1a's, not a third copy."""
    assert config_model.is_secret_shaped(key, value) is expected


def test_find_secret_shaped_keys_ignores_a_non_table() -> None:
    assert config_model.find_secret_shaped_keys(None) == []
    assert config_model.find_secret_shaped_keys("not-a-table") == []
