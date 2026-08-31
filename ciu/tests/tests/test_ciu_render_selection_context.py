"""CIU-44 / S3.12 — deployment-selection facts visible to templates and hooks.

Templates render with a ``ciu`` mapping (``selected_profiles``,
``deployed_stacks``) whenever the invocation has selection facts; outside a
deployment render the key is OMITTED so ``ciu.*`` references fail loudly
(Jinja UndefinedError) instead of silently seeing an empty selection. Hooks
receive the same facts (plus this workspace's identity from its own ciu.env)
on the HookContext — no ambient-environment trust, no ciu.env persistence.
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
    # identity comes from THIS workspace's own ciu.env (exact path), not ambient
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


def test_engine_identity_read_survives_unreadable_ciu_env(
    tmp_path, monkeypatch
):
    """An unreadable ciu.env degrades ctx identity to None — the deploy
    proceeds; the OSError branch of the S3.12 wiring is exercised."""
    import os

    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    repo_root = _build_repo(tmp_path, monkeypatch)
    stack = _add_stack(repo_root)

    # Skip ONLY the step-1 bootstrap (it legitimately needs the record and
    # already ran in _build_repo); make the file unreadable for the ENGINE's
    # own S3.12 identity read: stat still succeeds so is_file() passes, but
    # open() raises PermissionError(OSError).
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kw: None)
    env_path = repo_root / "ciu.env"
    env_path.chmod(0o000)
    try:
        result = engine.main_execution(
            stack, dry_run=True, yes=True, ciu_context=dict(_CTX)
        )
    finally:
        env_path.chmod(0o644)  # let tmp cleanup work regardless of runner uid
    assert result["status"] == "success"


def _identity_probe_stack(repo_root: Path, marker: Path) -> Path:
    """A stack whose pre_secrets hook writes the ctx identity it was given.

    The strong oracle for the arcs below: it proves not only that no traceback
    escaped, but WHAT the hook was told — the documented `{}` degradation, the
    same answer `deploy._workspace_identity` gives the `ciu check` preflight.
    """
    stack = _add_stack(repo_root)
    (stack / "identity_probe.py").write_text(
        "def run(config, ctx):\n"
        f"    open({str(marker)!r}, 'w').write(\n"
        "        f'{ctx.instance_id!r}|{ctx.network!r}'\n"
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
        # UnicodeDecodeError — a ValueError subclass, and a SIBLING of
        # WorkspaceEnvError rather than a subclass of it.
        ("non-UTF-8 byte", b'export INSTANCE_ID="\xff\xfe"\n'),
        # WorkspaceEnvError — the OTHER ValueError subclass.
        ("malformed entry", b'this is not = valid "shell\n'),
    ],
)
def test_engine_identity_read_survives_an_unparseable_ciu_env(
    tmp_path, monkeypatch, kind, payload
):
    """CIU-62 — the real-run twin of `deploy._workspace_identity`.

    `except (WorkspaceEnvError, OSError)` here caught neither a non-UTF-8 byte
    nor... well, it caught the malformed entry, but not the byte: a non-UTF-8
    `ciu.env` escaped this handler and crashed `ciu up` at the S3.12 identity
    read with a raw traceback, where the sibling `ciu check` path degraded
    cleanly. Both are now handled, and both degrade the SAME way — a preflight
    that saw an identity its own real run would not is exactly the divergence
    S3.12/CIU-44 exists to prevent.
    """
    monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
    repo_root = _build_repo(tmp_path, monkeypatch)
    marker = tmp_path / f"identity-{kind.split()[0]}.txt"
    stack = _identity_probe_stack(repo_root, marker)

    # Skip ONLY step 1's bootstrap (it legitimately needs a valid record and
    # already ran in _build_repo); the ENGINE's own S3.12 read is the arc
    # under test, and it reads the file below by exact path.
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kw: None)
    (repo_root / "ciu.env").write_bytes(payload)

    result = engine.main_execution(
        stack, dry_run=True, yes=True, ciu_context=dict(_CTX)
    )

    assert result["status"] == "success", f"a {kind} must not abort the run"
    assert marker.read_text(encoding="utf-8") == "None|None", (
        "the hook must be told the documented 'no identity', never a "
        "half-parsed or fabricated one"
    )


from ciu.hooks_runner import HookContext  # noqa: E402
