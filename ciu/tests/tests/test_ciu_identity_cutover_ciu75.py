"""CIU-75 — the v8 F2 identity cutover, proven rather than asserted.

Since ciu 7.7.0 the ``[ciu.instance.generated]`` table in a checkout's
``ciu.global.worktree.toml.j2`` is the SOLE source of instance identity that
CIU itself reads. ``ciu.env`` keeps being WRITTEN, byte-identical key set, but
is a legacy write-only export: no CIU internal reads it back.

Two oracles, and the second is the load-bearing one:

1. the reader (:func:`workspace_env.read_generated_facts`) answers each of its
   defined outcomes — absent, table-absent, and the three distinct ways a
   PRESENT record can be unreadable, which it normalizes into one
   ``WorkspaceEnvError`` at the seam so no call site has to re-derive that
   ``UnicodeDecodeError`` and ``WorkspaceEnvError`` are sibling ``ValueError``
   subclasses and that neither covers ``OSError`` (the CIU-62 lesson);

2. **every fact-reading call site keeps working with ``ciu.env`` deleted, and
   again with it replaced by garbage.** That is the cutover itself. A test
   that only asserted "the code now reads the overlay" would pass just as
   happily against a code path that reads BOTH and prefers one — which is
   exactly the half-cutover CIU-60 left behind and this entry exists to
   finish.

The parametrization over ``mangle`` runs the whole site sweep twice: once with
``ciu.env`` unlinked, once with it overwritten by bytes no shell and no parser
would accept. Neither may change a single answer.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, deploy, engine, workspace_env, worktree  # noqa: E402
from ciu.config_constants import GLOBAL_CONFIG_WORKTREE_OVERRIDES  # noqa: E402

OVERLAY = GLOBAL_CONFIG_WORKTREE_OVERRIDES
TEST_REPO = Path(__file__).resolve().parents[2] / "test-repo"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _hermetic_generate(monkeypatch, repo_root: Path) -> Path:
    """The REAL ``generate_ciu_env`` with only its host-fact detectors pinned.

    Deliberately not a fake: the whole question this file asks is what the
    SHIPPED generate writes and what the shipped readers then find, so the
    write paths must be the real ones.
    """
    monkeypatch.setattr(
        workspace_env, "_detect_physical_repo_root", lambda root: repo_root
    )
    monkeypatch.setattr(
        workspace_env,
        "_detect_public_fqdn",
        lambda root, require_fqdn: {
            "PUBLIC_IP": "203.0.113.7",
            "PUBLIC_FQDN": "example.test",
            "PUBLIC_TLS_CRT_PEM": "",
            "PUBLIC_TLS_KEY_PEM": "",
        },
    )
    monkeypatch.setattr(workspace_env, "_detect_docker_gid", lambda: "999")
    monkeypatch.setattr(workspace_env, "_detect_host_mdt_tmp", lambda: "")
    monkeypatch.setattr(
        workspace_env, "_detect_governance_read_iops", lambda: ("200", "fallback")
    )
    return workspace_env.generate_ciu_env(repo_root)


_MANGLERS = {
    # The consumer-visible catastrophe the entry names: something removed the
    # legacy export (a `ciu clean --vanilla`, a stale checkout, an operator).
    "deleted": lambda p: p.unlink(),
    # And the subtler one: the file is still there and says nothing true.
    "corrupt": lambda p: p.write_bytes(b'\xff\xfe not = shell "at all\n'),
}


@pytest.fixture(params=sorted(_MANGLERS))
def mangle(request):
    """Break the legacy `ciu.env` export in one of its two realistic ways."""
    def _mangle(repo_root: Path) -> None:
        env_path = Path(repo_root) / "ciu.env"
        assert env_path.is_file(), "generate must have written the legacy export"
        _MANGLERS[request.param](env_path)
    return _mangle


# ---------------------------------------------------------------------------
# O1 — the reader's defined outcomes
# ---------------------------------------------------------------------------


class TestGeneratedFactsReader:
    def test_absent_overlay_reads_as_no_facts(self, tmp_path):
        assert workspace_env.read_generated_facts(tmp_path) == {}
        assert workspace_env.read_instance_identity_env(tmp_path) == {}
        assert workspace_env.has_generated_facts(tmp_path) is False

    def test_overlay_without_the_table_reads_as_no_facts(self, tmp_path):
        """An operator's own sparse override is not an identity — and saying
        it were would make `ciu clean` claim a checkout it cannot name."""
        (tmp_path / OVERLAY).write_text(
            '[ciu.instance]\nservice_profiles = ["core"]\n', encoding="utf-8"
        )
        assert workspace_env.read_generated_facts(tmp_path) == {}
        assert workspace_env.has_generated_facts(tmp_path) is False

    def test_present_table_round_trips_the_writer(self, tmp_path):
        facts = {
            "repo_name": "repo",
            "instance_id": "abc123",
            "network": "repo-abc123-network",
            "physical_repo_root": "/host/repo",
            "repo_root": str(tmp_path),
            "public_fqdn": "",
        }
        workspace_env.upsert_generated_facts(tmp_path, facts)
        assert workspace_env.read_generated_facts(tmp_path) == facts
        assert workspace_env.has_generated_facts(tmp_path) is True
        assert workspace_env.read_instance_identity_env(tmp_path) == {
            "REPO_NAME": "repo",
            "INSTANCE_ID": "abc123",
            "DOCKER_NETWORK_INTERNAL": "repo-abc123-network",
            "PHYSICAL_REPO_ROOT": "/host/repo",
            "REPO_ROOT": str(tmp_path),
            "PUBLIC_FQDN": "",
        }

    def test_operator_added_key_is_dropped_from_the_env_view(self, tmp_path):
        """A fact with no legacy env name is not exported under a guessed
        one — the mapping is closed, and inventing a name for an unknown key
        is the shadowing-default anti-pattern."""
        # Written by hand: the shipped writer emits exactly
        # GENERATED_FACTS_KEYS, so only a hand-edit (or a future CIU) can put
        # an unknown key here — which is precisely the case under test.
        (tmp_path / OVERLAY).write_text(
            "[ciu.instance.generated]\n"
            'instance_id = "abc123"\n'
            'extra = "x"\n',
            encoding="utf-8",
        )
        facts = workspace_env.read_generated_facts(tmp_path)
        assert facts["extra"] == "x"
        assert "extra" not in workspace_env.read_instance_identity_env(tmp_path)
        assert "EXTRA" not in workspace_env.read_instance_identity_env(tmp_path)

    def test_non_utf8_record_is_indeterminate_not_empty(self, tmp_path):
        """CIU-62's byte-level half, at the new source. `UnicodeDecodeError`
        is a `ValueError` subclass and a SIBLING of `WorkspaceEnvError`."""
        (tmp_path / OVERLAY).write_bytes(
            b'[ciu.instance.generated]\nnetwork = "\xff\xfe"\n'
        )
        with pytest.raises(workspace_env.WorkspaceEnvError, match="could not read"):
            workspace_env.read_generated_facts(tmp_path)
        # A presence predicate cannot report indeterminacy, so it must not
        # report ABSENCE either: it answers "present", and the read refuses.
        assert workspace_env.has_generated_facts(tmp_path) is True

    def test_unreadable_path_is_indeterminate_not_empty(self, tmp_path):
        """A DIRECTORY where the overlay belongs: `OSError`, which neither
        `ValueError` arm covers."""
        (tmp_path / OVERLAY).mkdir()
        with pytest.raises(workspace_env.WorkspaceEnvError, match="could not read"):
            workspace_env.read_generated_facts(tmp_path)
        assert workspace_env.has_generated_facts(tmp_path) is True

    def test_malformed_table_is_indeterminate_not_empty(self, tmp_path):
        (tmp_path / OVERLAY).write_text(
            "[ciu.instance.generated]\nnetwork = bare-word\n", encoding="utf-8"
        )
        with pytest.raises(workspace_env.WorkspaceEnvError, match="malformed"):
            workspace_env.read_generated_facts(tmp_path)

    def test_non_string_fact_refuses(self, tmp_path):
        """Every generated fact is a string by construction; a number here
        would flow into a compose project name or a docker label as
        ``str(int)`` and be silently wrong instead of loudly refused."""
        (tmp_path / OVERLAY).write_text(
            "[ciu.instance.generated]\ninstance_id = 7\n", encoding="utf-8"
        )
        with pytest.raises(
            workspace_env.WorkspaceEnvError, match=r"instance_id is int, not a string"
        ):
            workspace_env.read_generated_facts(tmp_path)

    def test_table_stops_at_the_next_table(self, tmp_path):
        """The reader owns exactly the writer's own region — a key under a
        LATER table is not silently adopted as a generated fact."""
        (tmp_path / OVERLAY).write_text(
            "[ciu.instance.generated]\n"
            'instance_id = "mine"\n'
            "\n"
            "[ciu.instance]\n"
            'instance_id = "theirs"\n',
            encoding="utf-8",
        )
        assert workspace_env.read_generated_facts(tmp_path) == {"instance_id": "mine"}


# ---------------------------------------------------------------------------
# O2 — `ciu env generate` keeps writing the legacy export, unchanged
# ---------------------------------------------------------------------------


def test_generate_still_writes_ciu_env_with_the_same_key_set(monkeypatch, tmp_path):
    """Contract item 2: the write side is untouched, so a consumer's
    `source ciu.env` keeps working byte-for-byte this release."""
    env_path = _hermetic_generate(monkeypatch, tmp_path)

    assert env_path == tmp_path / "ciu.env"
    keys = {
        line.split("=", 1)[0].removeprefix("export ")
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("export ")
    }
    for required in workspace_env.REQUIRED_KEYS_CORE:
        assert required in keys
    for identity in workspace_env.GENERATED_IDENTITY_KEYS:
        assert identity in keys


def test_generate_warns_once_that_ciu_env_is_now_write_only(
    monkeypatch, tmp_path, capsys
):
    """Contract item 3: a WARN, not a refusal, naming the forward path and
    what a future release does — the shape this codebase already uses for a
    deprecated-but-working path (provisioning's `:healthy` exit-0 fallback)."""
    _hermetic_generate(monkeypatch, tmp_path)

    out = capsys.readouterr().out
    assert "[WARN] [S3.1c]" in out
    assert "LEGACY WRITE-ONLY export as of ciu 7.7.0" in out
    assert 'eval "$(ciu env print)"' in out
    assert GLOBAL_CONFIG_WORKTREE_OVERRIDES in out


def test_env_show_nudges_on_stderr_and_keeps_stdout_parseable(
    monkeypatch, tmp_path, capsys
):
    """`ciu env` is the legacy READ path. It keeps working and keeps its
    stdout exactly the key=value stream a consumer may be parsing; the
    deprecation notice goes to stderr, where it cannot break that parse."""
    from ciu import cli

    _hermetic_generate(monkeypatch, tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)

    assert cli._env_show() == 0

    captured = capsys.readouterr()
    assert "LEGACY WRITE-ONLY export" in captured.err
    assert 'eval "$(ciu env print)"' in captured.err
    assert captured.out
    for line in captured.out.splitlines():
        assert line.startswith("export ") or "=" in line
        assert "[WARN]" not in line


# ---------------------------------------------------------------------------
# O3 — the cutover: every fact-reading site, with the legacy export broken
# ---------------------------------------------------------------------------


@pytest.fixture
def cutover_repo(monkeypatch, tmp_path):
    """A real git repo with a real linked worktree, both really generated.

    Returns ``(repo_root, worktree_root, facts, wt_facts)``. Nothing here is
    faked except `generate_ciu_env`'s host detectors and Docker.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git(["init", "-b", "main"], repo).returncode == 0
    assert _git(["config", "user.email", "t@example.com"], repo).returncode == 0
    assert _git(["config", "user.name", "Test"], repo).returncode == 0
    (repo / "ciu.global.defaults.toml.j2").write_text(
        '[deploy]\nproject_name = "demo"\nenvironment_tag = "$INSTANCE_ID"\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(
        f"ciu.env\n{OVERLAY}\nciu.global.toml\n", encoding="utf-8"
    )
    (repo / "stack").mkdir()
    assert _git(["add", "-A"], repo).returncode == 0
    assert _git(["commit", "-m", "init"], repo).returncode == 0

    wt = tmp_path / "wt"
    assert _git(
        ["worktree", "add", "-b", "feature", str(wt), "main"], repo
    ).returncode == 0

    _hermetic_generate(monkeypatch, repo)
    _hermetic_generate(monkeypatch, wt)

    return (
        repo,
        wt,
        workspace_env.read_generated_facts(repo),
        workspace_env.read_generated_facts(wt),
    )


def _ready_record(repo: Path, wt: Path, wt_facts: dict) -> worktree.WorktreeInstanceRecord:
    record = worktree.WorktreeInstanceRecord(
        logical_name="wt",
        display_name="wt",
        branch="feature",
        git_worktree_path=wt,
        ciu_root_offset=Path("."),
        created_at_utc="2026-08-31T00:00:00Z",
        base_ref="main",
        state="ready",
        instance_id=wt_facts["instance_id"],
        network=wt_facts["network"],
    )
    worktree._write_instance_record(record)
    return record


def test_cutover_engine_sites_ignore_a_broken_ciu_env(cutover_repo, mangle):
    """`identity_compose_project_name` (up AND clean name the project with
    it) and `workspace_ownership_labels` (the label pair a future reap
    attributes containers by)."""
    repo, _wt, facts, _wt_facts = cutover_repo
    stack = repo / "stack"

    before_project = engine.identity_compose_project_name(repo, stack)
    (repo / worktree.WORKTREE_INSTANCE_RECORD).write_text(
        '{"schema_version": 1}\n', encoding="utf-8"
    )
    before_labels = engine.workspace_ownership_labels(repo)

    mangle(repo)

    assert engine.identity_compose_project_name(repo, stack) == before_project
    assert before_project == f"{facts['repo_name']}-{facts['instance_id']}-stack"
    assert engine.workspace_ownership_labels(repo) == before_labels
    assert before_labels == {
        engine.OWNERSHIP_LABEL_INSTANCE: facts["instance_id"],
        engine.OWNERSHIP_LABEL_REPO_ROOT: facts["physical_repo_root"],
    }


def test_cutover_deploy_sites_ignore_a_broken_ciu_env(cutover_repo, mangle, capsys):
    """`_workspace_identity` (the `ciu check` HookContext twin, returning
    `(facts, identity_unreadable)` since CIU-80) and
    `_workspace_identity_network` (the identity network `ciu clean` removes
    and then certifies gone)."""
    repo, _wt, facts, _wt_facts = cutover_repo

    before_identity, before_unreadable = deploy._workspace_identity(repo)
    before_network = deploy._workspace_identity_network(repo)
    capsys.readouterr()

    mangle(repo)

    after_identity, after_unreadable = deploy._workspace_identity(repo)
    assert after_identity == before_identity
    assert before_identity["instance_id"] == facts["instance_id"]
    # CIU-80 x CIU-75: a broken LEGACY export is not an unreadable identity
    # record. The flag must stay False across the mangle, or a hook would be
    # told the workspace's identity is corrupt on the strength of a file CIU
    # no longer reads.
    assert after_unreadable is before_unreadable is False
    assert deploy._workspace_identity_network(repo) == before_network
    assert before_network == facts["network"]
    # Nothing degraded, so nothing was announced.
    assert "could not read workspace identity" not in capsys.readouterr().err


def test_cutover_worktree_identity_sites_ignore_a_broken_ciu_env(
    cutover_repo, mangle, monkeypatch
):
    """`_runtime_identity` (allocation + collision checks), `_reap_uses_clean`
    (whether a reap goes through `ciu clean`), `_clean_in` (the child
    environment a reap's clean runs under) and `_sanitized_target_env`
    (`worktree up`/`exec`)."""
    repo, wt, _facts, wt_facts = cutover_repo
    record = _ready_record(repo, wt, wt_facts)

    seen: dict = {}

    def fake_run(argv, cwd, env, check=False):
        seen["env"] = dict(env)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(worktree.subprocess, "run", fake_run)

    before_identity = worktree._runtime_identity(wt)
    before_target_env = worktree._sanitized_target_env(repo, record)
    assert worktree._reap_uses_clean({"ciu_root": str(wt)}) is True
    assert worktree._clean_in(wt, yes=True) == 0
    before_child_env = seen["env"]

    mangle(wt)

    assert worktree._runtime_identity(wt) == before_identity
    assert before_identity == (wt_facts["instance_id"], wt_facts["network"])
    assert worktree._reap_uses_clean({"ciu_root": str(wt)}) is True
    assert worktree._sanitized_target_env(repo, record) == before_target_env
    assert before_target_env["INSTANCE_ID"] == wt_facts["instance_id"]
    assert worktree._clean_in(wt, yes=True) == 0
    assert seen["env"] == before_child_env
    assert before_child_env["REPO_ROOT"] == wt_facts["repo_root"]


def test_cutover_shared_infra_sites_ignore_a_broken_ciu_env(
    cutover_repo, mangle, monkeypatch
):
    """Both shared-infra reads of the REFERENCE checkout's network: the
    add-time preflight and the post-up revalidation.

    The oracles are docker-free on purpose. The preflight hands the network it
    read to `_check_reference_network_and_projects` (recorded here); the
    post-up guard compares it against the recorded intent and names the
    current value in its refusal.
    """
    repo, wt, _facts, wt_facts = cutover_repo
    seen: list[str] = []
    monkeypatch.setattr(
        worktree,
        "_check_reference_network_and_projects",
        lambda network, ref_projects: seen.append(network),
    )

    def _preflight():
        return worktree._preflight_shared_infra_for_add(
            repo,
            shared_infra=str(wt),
            shared_infra_services="api",
            shared_infra_ref_projects="idp-dev-idp",
        )

    def _post_up_message() -> str:
        intent = worktree.SharedInfraIntent(
            ref_path=wt, network="a-different-network",
            services=("api",), ref_projects=("idp-dev-idp",),
        )
        with pytest.raises(worktree.WorktreeError) as excinfo:
            worktree.connect_shared_infra_after_up(repo, "child-project", intent)
        return str(excinfo.value)

    before_intent = _preflight()
    before_message = _post_up_message()

    mangle(wt)

    after_intent = _preflight()
    assert after_intent.network == before_intent.network == wt_facts["network"]
    assert seen == [wt_facts["network"], wt_facts["network"]]
    assert _post_up_message() == before_message
    assert wt_facts["network"] in before_message


def test_cutover_budget_candidates_ignore_a_broken_ciu_env(cutover_repo, mangle):
    """S16.3's capacity survey resolves every registered worktree's own
    identity — and renders each candidate's config against it."""
    repo, wt, facts, wt_facts = cutover_repo
    (wt / "stack").mkdir(exist_ok=True)

    before = worktree._resolve_budget_candidates(repo, Path("stack"))
    assert {c.network for c in before} == {facts["network"], wt_facts["network"]}
    assert {c.project for c in before} == {
        f"demo-{facts['instance_id']}-stack",
        f"demo-{wt_facts['instance_id']}-stack",
    }

    mangle(repo)
    mangle(wt)

    after = worktree._resolve_budget_candidates(repo, Path("stack"))
    assert [(c.worktree_path, c.network, c.project) for c in after] == [
        (c.worktree_path, c.network, c.project) for c in before
    ]


def test_cutover_leaves_the_overlay_as_the_only_load_bearing_record(
    cutover_repo, mangle
):
    """The converse of every assertion above: remove the OVERLAY instead, and
    the same sites stop answering. Without this, "it still works with ciu.env
    deleted" would also be satisfied by a site that reads neither."""
    repo, _wt, _facts, _wt_facts = cutover_repo
    mangle(repo)  # the legacy export is already irrelevant
    (repo / OVERLAY).unlink()

    # CIU-80: the overlay is genuinely ABSENT here, which is the legitimate
    # state its flag exists to keep apart from an unreadable one.
    assert deploy._workspace_identity(repo) == ({}, False)
    assert deploy._workspace_identity_network(repo) == ""
    assert worktree._reap_uses_clean({"ciu_root": str(repo)}) is False
    with pytest.raises(ValueError, match="declares no repo_name/instance_id"):
        engine.identity_compose_project_name(repo, repo / "stack")
    with pytest.raises(worktree.WorktreeError, match="lacks instance_id or network"):
        worktree._runtime_identity(repo)


# ---------------------------------------------------------------------------
# O4 — the PROCESS ENVIRONMENT, through a real verb
#
# O3 above drives the twelve migrated helpers directly, and that is exactly why
# it could not see the hole the CIU-75 review found: the helpers were cut over,
# but STEP 1 of every verb still seeded `os.environ` from `ciu.env`, and ~26
# internal sites read `REPO_ROOT` / `PHYSICAL_REPO_ROOT` /
# `DOCKER_NETWORK_INTERNAL` / `PUBLIC_FQDN` straight out of ambient — including
# the `$DOCKER_NETWORK_INTERNAL` expansion in the shipped global config. The
# seed skipped keys already present, so a shell that had sourced a SIBLING
# checkout's `ciu.env` won, and containers joined that sibling's network.
#
# These tests therefore run a REAL user-facing verb (`ciu secrets list` — the
# thinnest one that performs the full STEP 1 + render-global-chain sequence
# without a Docker daemon) against a REAL generated workspace, with hostile
# ambient values set. Nothing about identity is stubbed.
# ---------------------------------------------------------------------------


@pytest.fixture
def verb_repo(monkeypatch, tmp_path):
    """A real, really-generated workspace carrying the shipped demo config.

    Uses the repo's own `test-repo` global defaults — the file that actually
    contains ``network_name = "$DOCKER_NETWORK_INTERNAL"`` and
    ``REPO_ROOT = "$REPO_ROOT"`` — so the oracle below is about the config CIU
    ships, not one written to make a point.

    Returns ``(repo_root, stack_dir, facts)``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(TEST_REPO / "ciu.global.defaults.toml.j2", repo / "ciu.global.defaults.toml.j2")
    stack = repo / "applications" / "app-config"
    stack.parent.mkdir(parents=True)

    def _ignore(_dir, names):
        return {n for n in names
                if n in (".ciu", "__pycache__", "ciu.toml", "ciu.compose.yml")
                or n.startswith("vol-")}

    shutil.copytree(TEST_REPO / "applications" / "app-config", stack, ignore=_ignore)
    (stack / "ciu.toml.j2").unlink(missing_ok=True)

    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")
    monkeypatch.setenv("CIU_KSM", "off")
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    for key in workspace_env.GENERATED_FACT_ENV_KEYS.values():
        monkeypatch.delenv(key, raising=False)

    _hermetic_generate(monkeypatch, repo)
    return repo, stack, workspace_env.read_generated_facts(repo)


def _run_secrets_list(stack: Path, monkeypatch) -> dict:
    """Run `ciu secrets list` for real, capturing what its render actually saw.

    The spy WRAPS `render_global_chain` rather than replacing it: the verb's
    own render runs, and the test observes its inputs and its result. A test
    that re-rendered afterwards would only prove what the environment looked
    like when the verb was over, not what the verb itself used.
    """
    seen: dict = {}
    real_chain = config_model.render_global_chain

    def spy(working_dir, repo_root, *args, **kwargs):
        rendered = real_chain(working_dir, repo_root, *args, **kwargs)
        seen["repo_root"] = Path(repo_root)
        seen["rendered"] = rendered
        return rendered

    monkeypatch.setattr(engine.config_model, "render_global_chain", spy)
    args = argparse.Namespace(
        dir=stack, define_root=None, action="list", name=None, yes=True
    )
    seen["exit"] = engine.secrets_command(args)
    return seen


def test_a_stale_sibling_identity_cannot_reach_a_real_verbs_render(
    verb_repo, monkeypatch, capsys
):
    """The review's Repro A, as a test: an inherited sibling identity loses.

    Pre-fix this asserted `sibling-checkout-network` — the shell's value,
    rendered into `deploy.network_name`, i.e. the network the containers of
    THIS checkout would have joined. No file needed corrupting and nothing was
    hand-edited: sourcing another checkout's `ciu.env` was enough, which is
    what CIU-41 already called the normal hazard.
    """
    repo, stack, facts = verb_repo
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "sibling-checkout-network")
    monkeypatch.setenv("REPO_ROOT", "/somewhere/else/entirely")
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", "/host/somewhere/else")
    monkeypatch.setenv("INSTANCE_ID", "sibling-instance")

    seen = _run_secrets_list(stack, monkeypatch)

    assert seen["exit"] == 0
    assert seen["rendered"]["deploy"]["network_name"] == facts["network"]
    assert facts["network"] != "sibling-checkout-network"
    # …and the verb resolved ITS repo root from the record too: `secrets_command`
    # reads `os.environ["REPO_ROOT"]`, one of the ~26 ambient consumers.
    assert seen["repo_root"] == repo
    # `[deploy.env.shared]` is the demo's own "machine facts exposed to
    # templates" table — the repo-root pair a container is handed (S1.3/S1.4).
    shared = seen["rendered"]["deploy"]["env"]["shared"]
    assert shared["REPO_ROOT"] == str(repo)
    assert shared["PHYSICAL_REPO_ROOT"] == facts["physical_repo_root"]
    # The process environment is corrected, not merely bypassed — the other 25
    # ambient readers are downstream of exactly this.
    import os

    assert os.environ["DOCKER_NETWORK_INTERNAL"] == facts["network"]
    assert os.environ["INSTANCE_ID"] == facts["instance_id"]
    assert os.environ["PHYSICAL_REPO_ROOT"] == facts["physical_repo_root"]


def test_machine_facts_still_come_from_the_ambient_environment(verb_repo, monkeypatch):
    """The other half of the boundary, or the fix would be a bigger hammer.

    S3.1c clause 7: `ciu.env` also carries MACHINE facts — properties of the
    host, not of the instance. Those stay ambient-first, because a value read
    live from this process is fresher than one recorded at the last generate
    (a rebuilt devcontainer changes `DOCKER_GID`; the record does not know).
    """
    _repo, stack, _facts = verb_repo
    monkeypatch.setenv("DOCKER_GID", "4242")

    seen = _run_secrets_list(stack, monkeypatch)

    assert seen["exit"] == 0
    import os

    assert os.environ["DOCKER_GID"] == "4242"


def test_a_corrupt_legacy_export_no_longer_crashes_step_1(
    verb_repo, monkeypatch, capsys
):
    """The review's Repro B: `ciu up` used to die with a raw traceback.

    The four `ciu.env` reads in the bootstrap path never got CIU-62's
    three-exception treatment, so a non-UTF-8 byte in a file CIU calls
    write-only crashed the first statement of every verb. It is now a WARN on
    stderr and the machine facts fall back to ambient; identity is unaffected,
    because identity does not come from this file any more.
    """
    repo, stack, facts = verb_repo
    (repo / "ciu.env").write_bytes(b'\xff\xfe not = shell "at all\n')

    seen = _run_secrets_list(stack, monkeypatch)

    assert seen["exit"] == 0
    assert seen["rendered"]["deploy"]["network_name"] == facts["network"]
    captured = capsys.readouterr()
    assert "could not read" in captured.err
    assert "ciu env generate" in captured.err
    assert "Traceback" not in captured.err


def test_the_regenerated_legacy_export_cannot_change_identity(
    verb_repo, monkeypatch, capsys
):
    """The review's Repro C, answered honestly rather than defended.

    Deleting `ciu.env` and finding CIU still works proves little on its own,
    because STEP 1 REGENERATES the file it calls write-only. What matters is
    that the regeneration cannot move identity: the facts before and after are
    identical, and the render still names the recorded network. (The absent
    case is genuinely covered by
    `test_cutover_leaves_the_overlay_as_the_only_load_bearing_record`, which
    removes the record CIU actually reads.)
    """
    repo, stack, facts = verb_repo
    (repo / "ciu.env").unlink()

    seen = _run_secrets_list(stack, monkeypatch)

    assert seen["exit"] == 0
    assert (repo / "ciu.env").is_file(), "STEP 1 regenerates the legacy export"
    assert workspace_env.read_generated_facts(repo) == facts
    assert seen["rendered"]["deploy"]["network_name"] == facts["network"]


def test_step_1_regeneration_keeps_stdout_clean_for_json_consumers(
    verb_repo, monkeypatch, capsys
):
    """S3.1c clause 3, and the review's second blocker.

    `deploy._run` — `ciu check`'s entry point, `--json` included — calls this
    bootstrap as its FIRST statement. A consumer that followed the migration
    advice (stop maintaining `ciu.env`, let it regenerate) therefore got the
    deprecation notice printed ahead of the JSON document, and every machine
    parse of `ciu check --json` broke. The notice is a stderr line on this
    path; the verb an operator TYPES (`ciu env generate`) still says it on
    stdout, which is what
    `test_generate_warns_once_that_ciu_env_is_now_write_only` pins.
    """
    repo, stack, _facts = verb_repo
    (repo / "ciu.env").unlink()

    _run_secrets_list(stack, monkeypatch)

    captured = capsys.readouterr()
    assert "LEGACY WRITE-ONLY export" in captured.err
    assert "LEGACY WRITE-ONLY export" not in captured.out
    assert "[S3.1c]" not in captured.out


def test_a_checkout_with_no_generated_table_is_repaired_not_refused(
    verb_repo, monkeypatch, capsys
):
    """The upgrade path: the record CIU reads is the record CIU repairs.

    The overlay is gitignored, so "no generated table" is an ordinary state —
    a fresh clone, a CI runner, a pre-CIU-60 checkout. Since 7.7.0 it is also
    the state in which CIU knows nothing about the instance, so bootstrap
    regenerates it rather than refusing a verb the operator just ran. The
    notice says so, on stderr.
    """
    repo, stack, facts = verb_repo
    (repo / OVERLAY).write_text("# operator's own file, no CIU table\n", encoding="utf-8")
    assert workspace_env.read_generated_facts(repo) == {}

    seen = _run_secrets_list(stack, monkeypatch)

    assert seen["exit"] == 0
    repaired = workspace_env.read_generated_facts(repo)
    assert repaired == facts, "a repair re-derives the SAME identity, not a new one"
    assert seen["rendered"]["deploy"]["network_name"] == facts["network"]
    err = capsys.readouterr().err
    assert "carries no [ciu.instance.generated] table" in err
    # The operator's own bytes are still theirs (upsert_generated_facts owns
    # only its own block).
    assert "operator's own file" in (repo / OVERLAY).read_text(encoding="utf-8")


def test_a_reap_that_cannot_delegate_to_clean_says_what_it_skipped(
    tmp_path, monkeypatch
):
    """The one behaviour this cutover made WORSE, made loud instead of silent.

    `_reap_uses_clean` answering False does not refuse — the caller falls
    through to `docker rm -f` + volume/network removal, which disposes of the
    docker resources and leaves every `vol-*` hostdir on disk (no root-helper,
    no hostdir pass). Post-cutover a checkout with only a legacy `ciu.env`
    lands there, which an in-place upgrade can produce. The reap must therefore
    SAY that it took the blunt path and that data may remain.
    """
    checkout = tmp_path / "stale-checkout"
    checkout.mkdir()
    (checkout / "ciu.env").write_text("DOCKER_NETWORK_INTERNAL=old\n", encoding="utf-8")
    group = {
        "ciu_root": str(checkout),
        "containers": [{"id": "abc123"}],
        "volumes": [],
        "networks": [],
    }
    monkeypatch.setattr(worktree, "_docker_reap", lambda *_a, **_k: "")

    assert worktree._reap_uses_clean(group) is False
    notes, failure = worktree._reap_one_group(group, {})

    assert failure == ""
    skipped = next(n for n in notes if "did NOT run" in n)
    assert str(checkout) in skipped
    assert "[ciu.instance.generated]" in skipped
    assert "ciu env generate" in skipped and "ciu clean" in skipped


def test_a_reap_with_no_checkout_left_stays_quiet(tmp_path, monkeypatch):
    """…and the genuinely orphaned group does NOT get that note: there is no
    checkout to repair, so the bare removal is the only possible path and
    saying "run ciu clean there" would name a directory that is gone."""
    group = {
        "ciu_root": str(tmp_path / "deleted-checkout"),
        "containers": [{"id": "abc123"}],
        "volumes": [],
        "networks": [],
    }
    monkeypatch.setattr(worktree, "_docker_reap", lambda *_a, **_k: "")

    notes, failure = worktree._reap_one_group(group, {})

    assert failure == ""
    assert not any("did NOT run" in n for n in notes)
