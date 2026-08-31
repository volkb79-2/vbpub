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

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy, engine, workspace_env, worktree  # noqa: E402
from ciu.config_constants import GLOBAL_CONFIG_WORKTREE_OVERRIDES  # noqa: E402

OVERLAY = GLOBAL_CONFIG_WORKTREE_OVERRIDES


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
