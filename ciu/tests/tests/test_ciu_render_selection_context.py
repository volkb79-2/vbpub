"""CIU-44 / S3.12 — deployment-selection facts visible to templates and hooks.

Templates render with a ``ciu`` mapping (``selected_profiles``,
``deployed_stacks``) whenever the invocation has selection facts; outside a
deployment render the key is OMITTED so ``ciu.*`` references fail loudly
(Jinja UndefinedError) instead of silently seeing an empty selection. Hooks
receive the same facts (plus this workspace's identity from its own generated
overlay table) on the HookContext — no ambient-environment trust, no ciu.env
persistence.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import composefile  # noqa: E402
from ciu import config_model  # noqa: E402
from ciu import engine  # noqa: E402
from ciu.deploy_pkg.profiles import Profile, render_ciu_context  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"
SRC_APP = TEST_REPO / "applications" / "app-config"
GLOBAL_DEFAULTS = "ciu.global.defaults.toml.j2"

_CTX = {
    "selected_profiles": ["core", "apps"],
    "deployed_stacks": ["infra/core", "applications/app-config"],
}


# ---------------------------------------------------------------------------
# render_ciu_context
# ---------------------------------------------------------------------------


def test_render_ciu_context_parses_names_and_dedupes_stacks():
    profile = Profile(name="core, apps", config={})
    selection = [
        {"path": "infra/core"},
        {"path": "apps/api"},
        {"path": "infra/core"},
    ]
    assert render_ciu_context(profile, selection) == {
        "selected_profiles": ["core", "apps"],
        "deployed_stacks": ["infra/core", "apps/api"],
    }


def test_render_ciu_context_default_profile_is_empty_not_none():
    """The default all-phases profile is an EMPTY list — not a fabricated name."""
    assert render_ciu_context(Profile(name=None, config={}), []) == {
        "selected_profiles": [],
        "deployed_stacks": [],
    }


# ---------------------------------------------------------------------------
# template visibility + loud absence
# ---------------------------------------------------------------------------


def test_render_stack_exposes_selection_to_templates(tmp_path):
    (tmp_path / "ciu.defaults.toml.j2").write_text(
        'profiles = "{{ ciu.selected_profiles | join(\',\') }}"\n'
        '{% if \'infra/pwmcp\' in ciu.deployed_stacks %}\n'
        'pwmcp = true\n'
        '{% else %}\n'
        'pwmcp = false\n'
        '{% endif %}\n',
        encoding="utf-8",
    )

    rendered = config_model.render_stack(
        tmp_path, global_config={}, preserve_state=False, ciu_context=_CTX
    )

    assert rendered["profiles"] == "core,apps"
    assert rendered["pwmcp"] is False  # undeployed upstream correctly absent


def test_render_stack_without_context_fails_loudly_on_ciu_reference(tmp_path):
    """No deployment render → no ``ciu`` key → UndefinedError, never [] silently."""
    (tmp_path / "ciu.defaults.toml.j2").write_text(
        'x = "{{ ciu.selected_profiles }}"\n', encoding="utf-8"
    )

    with pytest.raises(Exception) as excinfo:
        config_model.render_stack(tmp_path, global_config={}, preserve_state=False)
    assert "ciu" in str(excinfo.value)


def test_render_compose_exposes_selection(tmp_path):
    template = tmp_path / "ciu.compose.yml.j2"
    template.write_text(
        "services:\n"
        "  app:\n"
        "    image: busybox\n"
        "    labels:\n"
        "      profiles: \"{{ ciu.selected_profiles | join(',') }}\"\n",
        encoding="utf-8",
    )

    rendered = composefile.render_compose(template, {}, ciu_context=_CTX)

    assert 'profiles: "core,apps"' in rendered


def test_render_compose_ciu_instances_membership_check_with_no_fanout(tmp_path):
    """CIU-74 / S7.5b: the sanctioned ``'api' in ciu.instances`` fan-out
    membership test must keep working when *nothing* declares an
    ``instances`` count — ``_CTX`` here carries no ``instances`` key at all,
    exactly the "no fan-out anywhere" case the backlog entry is about.
    Proves the always-present-empty-``{}``-mapping fix, not merely the
    StrictUndefined switch: StrictUndefined ALONE (with no context-assembly
    fix) would make this same membership test raise ``UndefinedError``
    instead of evaluating to False.
    """
    template = tmp_path / "ciu.compose.yml.j2"
    template.write_text(
        "services:\n"
        "  app:\n"
        "    image: busybox\n"
        "    labels:\n"
        "      fans_out: \"{{ 'api' in ciu.instances }}\"\n",
        encoding="utf-8",
    )

    rendered = composefile.render_compose(template, {}, ciu_context=dict(_CTX))

    assert 'fans_out: "False"' in rendered


def test_global_chain_exposes_selection_to_defaults(tmp_path):
    (tmp_path / GLOBAL_DEFAULTS).write_text(
        'flag = {{ "true" if \'infra/core\' in ciu.deployed_stacks else "false" }}\n',
        encoding="utf-8",
    )

    merged = config_model.render_global_chain(
        tmp_path, tmp_path, ciu_context=_CTX
    )

    # `flag = true` renders unquoted → tomllib parses it as a TOML boolean.
    assert merged["flag"] is True


def test_global_chain_ciu_instances_membership_check_with_no_fanout(tmp_path):
    """CIU-74 / S7.5b, TOML-layer side: the same always-present-empty-``{}``
    fix applies to ``config_model._make_render_context`` (used by
    ``render_global_chain``/``render_stack`` for TOML templates), not just
    ``composefile.render_compose`` — a global/stack TOML template checking
    ``'x' in ciu.instances`` with no fan-out declared anywhere must render,
    not raise.
    """
    (tmp_path / GLOBAL_DEFAULTS).write_text(
        "fans_out = {{ \"true\" if 'api' in ciu.instances else \"false\" }}\n",
        encoding="utf-8",
    )

    merged = config_model.render_global_chain(
        tmp_path, tmp_path, ciu_context=dict(_CTX)
    )

    assert merged["fans_out"] is False


# ---------------------------------------------------------------------------
# engine integration: templates AND hooks see the same truth
# ---------------------------------------------------------------------------


def _build_repo(tmp_path: Path, monkeypatch) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    shutil.copy2(TEST_REPO / GLOBAL_DEFAULTS, repo_root / GLOBAL_DEFAULTS)
    monkeypatch.setenv("REPO_ROOT", str(repo_root))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo_root))
    # Scrub the rest of the identity family: under xdist a worker may carry
    # generated identity from an earlier test's bootstrap; this fixture's
    # generate must see none of it (CIU-47 refined precedence would then
    # warn/ignore per key — silence beats noise here).
    for key in (
        "REPO_NAME", "INSTANCE_ID", "DOCKER_NETWORK_INTERNAL", "PUBLIC_FQDN"
    ):
        monkeypatch.delenv(key, raising=False)
    # The post-generate + engine-step-4 network steps are live-Docker side
    # effects, irrelevant to selection-fact threading — keep hermetic from
    # the daemon (patch BOTH import sites).
    from ciu import workspace_env as workspace_env_mod

    monkeypatch.setattr(
        workspace_env_mod, "ensure_workspace_network", lambda *a, **k: None
    )
    monkeypatch.setattr(
        engine, "ensure_workspace_network", lambda *a, **k: None
    )
    # Step 8 (hostdirs) does real os.chown with an S6.5 docker fallback —
    # neither belongs to this fixture's subject (selection-fact threading);
    # left live it made the test sensitive to daemon/privilege context.
    monkeypatch.setattr(engine, "create_hostdirs", lambda *a, **k: None)

    from ciu.workspace_env import bootstrap_workspace_env, REQUIRED_KEYS_CORE

    bootstrap_workspace_env(
        start_dir=repo_root,
        define_root=None,
        defaults_filename=GLOBAL_DEFAULTS,
        generate_env=True,
        update_cert_permission=False,
        required_keys=REQUIRED_KEYS_CORE,
    )
    return repo_root


def _add_stack(repo_root: Path) -> Path:
    dst = repo_root / "applications" / "app-config"
    shutil.copytree(SRC_APP, dst)
    for junk in [".ciu", "__pycache__"]:
        shutil.rmtree(dst / junk, ignore_errors=True)
    for vol in dst.glob("vol-*"):
        shutil.rmtree(vol, ignore_errors=True)
    for f in dst.glob("ciu.compose.yml"):
        f.unlink()
    return dst


def test_engine_threads_selection_into_configfiles_and_hooks(
    tmp_path, monkeypatch
):
    """End to end on the demo stack: a configfile renders ``ciu.*`` and a hook
    reads the same facts off ctx — one snapshot for the whole invocation."""
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    repo_root = _build_repo(tmp_path, monkeypatch)
    stack = _add_stack(repo_root)

    # Template consumes the selection facts (S3.12) AND the hook-injected
    # values, proving hooks and templates see one snapshot.
    cfg_tpl = stack / "config.toml.j2"
    cfg_tpl.write_text(
        cfg_tpl.read_text(encoding="utf-8")
        + "\n[ciumeta]\n"
        + 'selected_profiles = "{{ ciu.selected_profiles | join(\',\') }}"\n'
        + 'deployed_stacks = "{{ ciu.deployed_stacks | join(\';\') }}"\n'
        + 'ctx_profiles = "{{ app_config.ctx_profiles }}"\n'
        + 'ctx_network = "{{ app_config.ctx_network }}"\n',
        encoding="utf-8",
    )

    # Hook records what ctx exposed (S9.3 fields), surfaced via apply_to_config.
    hook = stack / "pre_compose_ctx_probe.py"
    hook.write_text(
        "def run(config, ctx):\n"
        "    return {\n"
        "        'app_config.ctx_profiles': {\n"
        "            'value': ','.join(ctx.selected_profiles or ()),\n"
        "            'apply_to_config': True,\n"
        "        },\n"
        "        'app_config.ctx_network': {\n"
        "            'value': ctx.network or '',\n"
        "            'apply_to_config': True,\n"
        "        },\n"
        "    }\n",
        encoding="utf-8",
    )
    defaults = stack / "ciu.defaults.toml.j2"
    defaults.write_text(
        defaults.read_text(encoding="utf-8")
        .replace(
            "[app_config.hooks]",
            "[app_config.hooks]\n"
            'pre_secrets = ["pre_compose_ctx_probe.py"]\n',
            1,
        )
        # keep the demo's own pre_compose hook intact; ours runs earlier and
        # only appends plain values.
        , encoding="utf-8")

    result = engine.main_execution(
        stack,
        dry_run=True,
        yes=True,
        ciu_context={
            "selected_profiles": ["core", "apps"],
            "deployed_stacks": ["infra/core", "applications/app-config"],
        },
    )
    assert result["status"] == "success"

    rendered_files = list((stack / ".ciu" / "rendered").rglob("config.toml"))
    assert rendered_files, "configfile was not rendered"
    body = rendered_files[0].read_text(encoding="utf-8")
    assert 'selected_profiles = "core,apps"' in body
    assert 'deployed_stacks = "infra/core;applications/app-config"' in body
    assert 'ctx_profiles = "core,apps"' in body
    # identity comes from THIS workspace's own generated overlay facts (exact
    # path), not ambient
    assert "ctx_network" in body


def test_hookcontext_identity_fields_default_none():
    """Bare/unit construction keeps the new fields None (no fabricated facts)."""
    from ciu.hooks_runner import HookContext

    ctx = HookContext(
        point="pre_compose",
        stack_dir=Path("/x"),
        repo_root=Path("/r"),
        secret_file=lambda name: Path("/x") / name,
    )
    assert ctx.selected_profiles is None
    assert ctx.deployed_stacks is None
    assert ctx.instance_id is None
    assert ctx.network is None
    # CIU-80: additive field, defaults False in bare/unit construction too —
    # a hook author who never sets it sees the same "no signal" it always had.
    assert ctx.identity_unreadable is False

# ---------------------------------------------------------------------------
# Review repairs — B1: the config's own [ciu] table survives in template scope
# ---------------------------------------------------------------------------


def test_ciu_facts_merge_into_config_ciu_table_never_replace(tmp_path):
    """Review B1: [ciu] holds workspace switches (auto_connect_network, …);
    the selection facts MERGE in — both are visible, neither clobbers the
    other, in every render surface that carries the facts."""
    (tmp_path / "ciu.defaults.toml.j2").write_text(
        "attach = {{ 'true' if ciu.auto_connect_network else 'false' }}\n"
        'profiles = "{{ ciu.selected_profiles | join(\',\') }}"\n',
        encoding="utf-8",
    )
    global_config = {"ciu": {"auto_connect_network": False, "require_fqdn": False}}

    rendered = config_model.render_stack(
        tmp_path,
        global_config=global_config,
        preserve_state=False,
        ciu_context=_CTX,
    )

    assert rendered["attach"] is False  # config's own [ciu] table intact
    assert rendered["profiles"] == "core,apps"  # facts merged alongside


def test_compose_render_also_merges_ciu_table(tmp_path):
    template = tmp_path / "ciu.compose.yml.j2"
    template.write_text(
        "services:\n"
        "  app:\n"
        "    image: busybox\n"
        "    environment:\n"
        "      ATTACH: \"{{ ciu.auto_connect_network }}\"\n"
        "      PROFILES: \"{{ ciu.selected_profiles | join(',') }}\"\n",
        encoding="utf-8",
    )
    guarded = {"ciu": {"auto_connect_network": True}}

    rendered = composefile.render_compose(
        template, guarded, ciu_context={"selected_profiles": ["core"], "deployed_stacks": ["x"]}
    )

    assert 'ATTACH: "True"' in rendered
    assert 'PROFILES: "core"' in rendered


def test_engine_identity_read_survives_unreadable_overlay(
    tmp_path, monkeypatch
):
    """An unreadable overlay degrades ctx identity to None — the deploy
    proceeds; the read-failure branch of the S3.12 wiring is exercised."""
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    repo_root = _build_repo(tmp_path, monkeypatch)
    stack = _add_stack(repo_root)

    # Skip ONLY the step-1 bootstrap (it legitimately needs the record and
    # already ran in _build_repo). Pre-CIU-75 this test made the FILE
    # unreadable (stat succeeds, open() raises PermissionError); the reader is
    # now the seam that normalizes OSError, UnicodeDecodeError and malformed
    # TOML into one WorkspaceEnvError, so the failure is injected there.
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kw: None)
    # CIU-75: the overlay is ALSO a config-chain layer, so corrupting the FILE
    # now fails the render long before step 12 — a strictly louder outcome, and
    # not the arc under test. Make the S3.12 identity READ itself fail, which
    # is exactly the condition this handler exists for.
    monkeypatch.setattr(
        engine, "read_generated_facts",
        lambda _root: (_ for _ in ()).throw(
            engine.WorkspaceEnvError("[S3.1c] could not read ...: denied")
        ),
    )
    result = engine.main_execution(
        stack, dry_run=True, yes=True, ciu_context=dict(_CTX)
    )
    assert result["status"] == "success"


def _identity_probe_stack(repo_root: Path, marker: Path) -> Path:
    """A stack whose pre_secrets hook writes the ctx identity it was given.

    The strong oracle for the arcs below: it proves not only that no traceback
    escaped, but WHAT the hook was told — the documented `{}` degradation, the
    same answer `deploy._workspace_identity` gives the `ciu check` preflight.
    The marker also carries `ctx.identity_unreadable` (CIU-80) — the field
    that lets a hook tell "genuinely unmanaged" from "record exists, CIU
    could not parse it" apart, which `instance_id`/`network` alone cannot.
    """
    stack = _add_stack(repo_root)
    (stack / "identity_probe.py").write_text(
        "def run(config, ctx):\n"
        f"    open({str(marker)!r}, 'w').write(\n"
        "        f'{ctx.instance_id!r}|{ctx.network!r}|{ctx.identity_unreadable!r}'\n"
        "    )\n"
        "    return {}\n",
        encoding="utf-8",
    )
    defaults = stack / "ciu.defaults.toml.j2"
    defaults.write_text(
        defaults.read_text(encoding="utf-8").replace(
            "[app_config.hooks]",
            '[app_config.hooks]\npre_secrets = ["identity_probe.py"]\n',
            1,
        ),
        encoding="utf-8",
    )
    return stack


@pytest.mark.parametrize(
    "kind, payload",
    [
        # The two causes `read_generated_facts` normalizes into one
        # WorkspaceEnvError at the seam (their file-level forms are proven
        # against the reader itself in test_ciu_workspace_env.py).
        ("non-UTF-8 byte", "[S3.1c] could not read ...: 'utf-8' codec"),
        ("malformed entry", "[S3.1c] ... has a malformed table"),
    ],
)
def test_engine_identity_read_survives_an_unparseable_identity_record(
    tmp_path, monkeypatch, capsys, kind, payload
):
    """CIU-62 — the real-run twin of `deploy._workspace_identity`.

    `except (WorkspaceEnvError, OSError)` here caught neither a non-UTF-8 byte
    nor... well, it caught the malformed entry, but not the byte: a non-UTF-8
    record escaped this handler and crashed `ciu up` at the S3.12 identity
    read with a raw traceback, where the sibling `ciu check` path degraded
    cleanly. Both are now handled, and both degrade the SAME way — a preflight
    that saw an identity its own real run would not is exactly the divergence
    S3.12/CIU-44 exists to prevent. CIU-75 moved the SOURCE to the overlay's
    generated table and normalized all three exception types at the reader;
    the degradation contract is unchanged.
    """
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    repo_root = _build_repo(tmp_path, monkeypatch)
    marker = tmp_path / f"identity-{kind.split()[0]}.txt"
    stack = _identity_probe_stack(repo_root, marker)

    # Skip ONLY step 1's bootstrap (it legitimately needs a valid record and
    # already ran in _build_repo); the ENGINE's own S3.12 read is the arc
    # under test, and it reads the file below by exact path.
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kw: None)
    # CIU-75: the S3.12 read is the arc under test; corrupting the overlay FILE
    # would fail the config-chain render first (a louder, different outcome).
    monkeypatch.setattr(
        engine, "read_generated_facts",
        lambda _root: (_ for _ in ()).throw(engine.WorkspaceEnvError(payload)),
    )

    result = engine.main_execution(
        stack, dry_run=True, yes=True, ciu_context=dict(_CTX)
    )

    assert result["status"] == "success", f"a {kind} must not abort the run"
    assert marker.read_text(encoding="utf-8") == "None|None|True", (
        "the hook must be told the documented 'no identity', never a "
        "half-parsed or fabricated one — and (CIU-80) that this is the "
        "PRESENT-but-unreadable state (identity_unreadable=True), not the "
        "legitimate-absent one"
    )
    # CIU-62 review ruling: the degradation stays, the SILENCE does not.
    # Without this the estate's own default test — "if this default is wrong,
    # does anything fail loudly?" — answers no, and a hook seeing
    # instance_id=None cannot distinguish "unmanaged" from "corrupt, swallowed".
    out = capsys.readouterr().out
    assert "[WARN] [S3.12] could not read workspace identity" in out
    assert "ciu.global.worktree.toml.j2" in out
    assert payload in out, "the warning must name the underlying cause"
    assert "ciu env generate" in out, "the warning must name the repair"


def test_identity_unreadable_agrees_between_check_preflight_and_real_run(
    tmp_path, monkeypatch, capsys
):
    """CIU-80's MANDATORY pairing requirement: `deploy._workspace_identity`
    (the `ciu check` preflight's HookContext) and `engine.main_execution`'s
    STEP-12 real-run read must set `identity_unreadable` IDENTICALLY on the
    SAME unreadable identity record — a hook's `validate_config` must never
    see an identity state its own `run()` would not (the exact divergence
    S3.12 / CIU-44 / CIU-62 exist to prevent).

    CIU-75 re-pointed the fixture, deliberately keeping this test END-TO-END
    rather than monkeypatching either side: the corruption must be one the
    config-chain render TOLERATES (the overlay is also a chain layer now, so
    malformed TOML would fail the run long before STEP 12) while the identity
    READER rejects it. A non-string fact is exactly that — valid TOML, merges
    fine, and `read_generated_facts` refuses it because a number flowing into
    a compose project name or a docker label as `str(int)` would be silently
    wrong rather than loudly refused. So both sides here are still driven by
    ONE real file, which is the whole point of a pairing oracle.
    """
    from ciu import deploy

    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    repo_root = _build_repo(tmp_path, monkeypatch)
    marker = tmp_path / "identity-pairing.txt"
    stack = _identity_probe_stack(repo_root, marker)

    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kw: None)
    (repo_root / "ciu.global.worktree.toml.j2").write_text(
        "[ciu.instance.generated]\ninstance_id = 7\n", encoding="utf-8"
    )

    # ---- real-run side: engine.main_execution's STEP-12 identity read ----
    result = engine.main_execution(stack, dry_run=True, yes=True, ciu_context=dict(_CTX))
    assert result["status"] == "success"
    real_run_probe = marker.read_text(encoding="utf-8")
    capsys.readouterr()  # drain the real-run's own [WARN], not under test here

    # ---- preflight side: deploy._workspace_identity, the ciu check twin ----
    identity, identity_unreadable = deploy._workspace_identity(repo_root)
    capsys.readouterr()  # drain deploy's own stderr [WARN]

    assert identity == {}
    assert real_run_probe == f"None|None|{identity_unreadable!r}", (
        "the real run's ctx.identity_unreadable must equal the preflight's "
        "own read on the identical ciu.env — a divergence here means a "
        "validate_config preflight would certify a config against an "
        "identity state its own run() would not agree with"
    )
    assert identity_unreadable is True, (
        "both sides must land on PRESENT-but-unreadable, not the legitimate-"
        "absent state, for this malformed-entry fixture"
    )


from ciu.hooks_runner import HookContext  # noqa: E402
