"""CIU v2 SPEC CONTRACT TEST SUITE — end-to-end integration contracts (P12).

Every test here is keyed to a normative spec requirement ID (docs/SPEC.md) and
exercises the REAL pipeline (``engine.main_execution`` / ``deploy.*`` / the
``ciu`` CLI) against a hermetic per-test copy of the rebuilt ``test-repo`` demo.
These are the long-term guarantees that must survive refactors; the per-packet
unit tests cover module internals — this file pins the INTEGRATION behaviour.

Design rules (all enforced below):

* **No docker daemon.** Only ``--dry-run`` / ``--render-toml`` paths run; the
  DooD preflight and dependency check are skipped via an autouse fixture
  (``CIU_SKIP_DOOD_PREFLIGHT=1`` + ``SKIP_DEPENDENCY_CHECK=1``), and the network
  attach step is monkeypatched to a no-op. Vault-backed flows monkeypatch
  ``engine.VaultKV2`` with an in-memory fake.
* **Hermetic + parallel-safe.** Each test fabricates a minimal repo root under
  ``tmp_path`` (``ciu.global.defaults.toml.j2`` copied from the demo, ``ciu.env``
  generated with ``REPO_ROOT == PHYSICAL_REPO_ROOT == tmp``) and copytree's only
  the stack(s) it needs. The real ``test-repo`` is NEVER mutated.
* **Inherited-env hazard.** The devcontainer exports a *foreign* dstdns
  ``REPO_ROOT`` / ``PHYSICAL_REPO_ROOT``; the autouse fixture defensively
  ``delenv``s them (and the demo's secret env) before each test sets its own.

Helpers (``build_repo`` / ``add_stack`` / ``run_engine`` / ``doctor_*``) are the
P12 analogue of ``test_ciu_test_repo.py``'s fixture helpers, adapted for
tmp-copy hermeticity.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import composefile  # noqa: E402
from ciu import deploy  # noqa: E402
from ciu import engine  # noqa: E402
from ciu.deploy_pkg import health as health_pkg  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402
from ciu.secrets import providers as providers_pkg  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_REPO = REPO_ROOT / "test-repo"

GLOBAL_DEFAULTS = "ciu.global.defaults.toml.j2"

# Demo stack source dirs (copied per-test into tmp; never mutated in place).
SRC_VAULT = TEST_REPO / "infra" / "vault"
SRC_REDIS = TEST_REPO / "infra" / "redis-core"
SRC_DB = TEST_REPO / "infra" / "db-core"
SRC_APP = TEST_REPO / "applications" / "app-config"


# ===========================================================================
# Autouse environment fixture (the inherited-env hazard + no-docker knobs)
# ===========================================================================


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Neutralize the foreign devcontainer env and force no-docker dry-run paths.

    S2.7 — a pre-set env value always wins, so the devcontainer's foreign
    ``REPO_ROOT`` / ``PHYSICAL_REPO_ROOT`` (pointing at the dstdns workspace)
    would otherwise poison every ``to_physical_path`` / bootstrap call. We
    ``delenv`` them (and the demo secret env) up front; each test sets its own
    via :func:`build_repo`.
    """
    # Foreign repo-root pair — MUST be cleared before any test sets its own.
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    # Demo secret env that could mask ASK_EXTERNAL fail-fast / caching tests.
    monkeypatch.delenv("CIU_SECRET_LICENSE", raising=False)
    monkeypatch.delenv("CIUDEMO_LICENSE", raising=False)
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    # Cert env so require_certs paths and the TLS probe stay no-ops.
    monkeypatch.delenv("PUBLIC_TLS_CRT_PEM", raising=False)
    monkeypatch.delenv("PUBLIC_TLS_KEY_PEM", raising=False)

    # No docker anywhere (S1.5 preflight + dependency check off).
    monkeypatch.setenv("CIU_SKIP_DOOD_PREFLIGHT", "1")
    monkeypatch.setenv("SKIP_DEPENDENCY_CHECK", "1")

    # Machine-identity facts the demo global config expands from ciu.env.
    # Use the REAL process uid/gid for the ownership knobs so the hostdir chown
    # (S6.3/S4.10) is a genuine no-op success: created vol-* dirs then naturally
    # carry matching ownership, and a SECOND run passes the S6.3 compatibility
    # check on its own leftovers (without privilege the configured 1000:999 of
    # the demo would fail to apply, then re-trip the check — the inherited-env
    # hazard the existing test-repo fixture cleans around).
    uid = str(os.getuid())
    gid = str(os.getgid())
    monkeypatch.setenv("DOCKER_GID", gid)
    monkeypatch.setenv("CONTAINER_UID", uid)
    monkeypatch.setenv("CONTAINER_GID", gid)
    monkeypatch.setenv("USER_UID", uid)
    monkeypatch.setenv("USER_GID", gid)
    monkeypatch.setenv("USER_NAME", "tester")
    monkeypatch.setenv("DOCKER_UID", uid)
    monkeypatch.setenv("PUBLIC_FQDN", "example.test")
    monkeypatch.setenv("PUBLIC_IP", "127.0.0.1")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "ciu-spec-network")

    # The network attach step touches docker — stub it everywhere it is called:
    #   * workspace_env owns the implementation (bootstrap calls it directly);
    #   * engine + deploy each imported the name into their own namespace.
    import ciu.workspace_env as _we

    monkeypatch.setattr(_we, "ensure_workspace_network", lambda *a, **k: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda *a, **k: None)
    monkeypatch.setattr(deploy, "ensure_workspace_network", lambda *a, **k: None)
    yield


# ===========================================================================
# Fixture builders — minimal repo root + per-test stack copies under tmp_path
# ===========================================================================


def build_repo(tmp_path: Path, monkeypatch) -> Path:
    """Fabricate a minimal CIU repo root under *tmp_path* and set its env.

    Copies the demo ``ciu.global.defaults.toml.j2`` verbatim (it is
    self-contained — every ``$VAR`` resolves from ``ciu.env``) and generates
    ``ciu.env`` with ``REPO_ROOT == PHYSICAL_REPO_ROOT == repo_root`` (S2.7:
    the pre-set env wins, so the generated file carries the tmp paths). The repo
    lives under ``/tmp`` which is NOT a git work tree, so the S1.7 gitignore
    probe no-ops cleanly.

    Returns the repo-root path (already exported as ``REPO_ROOT``).
    """
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TEST_REPO / GLOBAL_DEFAULTS, repo_root / GLOBAL_DEFAULTS)

    # S2.7: pre-set the repo-root pair so generation + reload carry the tmp paths.
    monkeypatch.setenv("REPO_ROOT", str(repo_root))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(repo_root))

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


def add_stack(repo_root: Path, src: Path, rel: str) -> Path:
    """Copytree the demo stack *src* into ``repo_root/rel`` (machine artifacts dropped).

    Drops any pre-existing ``.ciu/`` / ``vol-*`` / rendered outputs / pycache from
    the source so each copy starts from the committed templates only — the test
    is then deterministic regardless of prior demo runs.
    """
    dst = repo_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)

    def _ignore(_dir, names):
        drop = set()
        for n in names:
            if n in (".ciu", "__pycache__", "ciu.toml", "ciu.compose.yml") or n.startswith("vol-"):
                drop.add(n)
        return drop

    shutil.copytree(src, dst, ignore=_ignore)
    # A stale ciu.toml.j2 override (auto-created by a prior render) would pin
    # [state]; drop it so each copy renders from defaults only.
    (dst / "ciu.toml.j2").unlink(missing_ok=True)
    return dst


def run_engine(stack_dir: Path, monkeypatch, **kwargs):
    """Run ``engine.main_execution`` for *stack_dir* with sane test defaults.

    ``dry_run=True`` and ``generate_env=False`` by default (the repo's
    ``ciu.env`` already exists from :func:`build_repo`). Extra kwargs override.
    """
    params = dict(working_dir=stack_dir, dry_run=True, generate_env=False)
    params.update(kwargs)
    return engine.main_execution(**params)


def clear_render_artifacts(stack_dir: Path) -> None:
    """Drop a stack's RENDERED artifacts between two runs, KEEPING secret stores.

    The idempotency contracts (S4.11/S3.4) re-run a full dry-run twice and assert
    that the SECRET STORE files persist/refresh correctly. CIU renders the
    configfile at mode ``0440`` (S5.2), and ``render_configfiles`` overwrites it
    with a plain (non-atomic) ``write_text`` — which a non-root owner cannot do
    over a read-only file. We therefore clear ``.ciu/rendered`` (and the rendered
    compose output) before the second run; crucially we DO NOT touch
    ``.ciu/secrets`` (stack store) or the project store, so the very persistence
    the test asserts is left intact. (The non-atomic 0440 re-render is noted as a
    finding — it is an engine concern, out of scope for this test packet.)
    """
    shutil.rmtree(stack_dir / ".ciu" / "rendered", ignore_errors=True)
    (stack_dir / "ciu.compose.yml").unlink(missing_ok=True)


def read_overlay(stack_dir: Path) -> str:
    return (stack_dir / ".ciu" / "ciu.compose.overlay.yml").read_text()


def read_compose(stack_dir: Path) -> str:
    return (stack_dir / "ciu.compose.yml").read_text()


def rendered_config(stack_dir: Path) -> str:
    return (stack_dir / ".ciu" / "rendered" / "app" / "main").read_text()


def store_value(repo_root: Path, rel: str) -> bytes:
    return (repo_root / ".ciu" / "secrets" / rel).read_bytes()


# ===========================================================================
# Doctoring helpers — fabricate spec-violating stack copies in tmp
# ===========================================================================


def doctor_compose(stack_dir: Path, new_text: str) -> None:
    """Overwrite the COPY's compose template (only ever the tmp copy)."""
    (stack_dir / "ciu.compose.yml.j2").write_text(new_text, encoding="utf-8")


def doctor_config_template(stack_dir: Path, new_text: str) -> None:
    (stack_dir / "config.toml.j2").write_text(new_text, encoding="utf-8")


# A minimal valid app-config compose body, parametrised on the `secrets:` list and
# an optional extra volume line — used to build leak/consumption doctored copies.
def _app_compose(secrets_list: str, extra: str = "") -> str:
    return (
        "services:\n"
        "  app:\n"
        "    image: {{ app_config.app.image_name }}:{{ app_config.app.image_tag }}\n"
        "    container_name: {{ deploy.project_name }}-{{ deploy.environment_tag }}-{{ app_config.app.name }}\n"
        f"    secrets: {secrets_list}\n"
        "    command: [\"python\", \"-m\", \"http.server\", \"{{ app_config.app.internal_port }}\"]\n"
        "    environment:\n"
        "      - APP_CONFIG={{ app_config.app.configfile.main.target }}\n"
        f"{extra}"
        "    volumes:\n"
        "      - {{ app_config.app.hostdir.logs }}:/var/log/app\n"
        "    networks:\n"
        "      - workspace\n"
        "networks:\n"
        "  workspace:\n"
        "    external: true\n"
        "    name: {{ deploy.network_name }}\n"
    )


# ===========================================================================
# In-memory fake Vault (S4.11 / S4.12 / S4.16) — records writes
# ===========================================================================


class FakeVaultKV2:
    """Drop-in for ``engine.VaultKV2``: in-memory KV with a write counter.

    Mirrors the real ``VaultKV2(addr, token)`` constructor and ``read``/``write``
    surface (S4.15). ``writes`` records every ``write`` call so a test can assert
    GEN_TO_VAULT generated exactly once across re-runs (S4.11).
    """

    # Class-level store so a fresh instance per engine run shares state across
    # the two re-runs of one test (the engine constructs VaultKV2 each call).
    store: dict[str, str] = {}
    writes: list[tuple[str, str]] = []

    def __init__(self, addr: str, token: str, timeout: float = 10) -> None:
        self.addr = addr
        self.token = token

    @classmethod
    def reset(cls) -> None:
        cls.store = {}
        cls.writes = []

    def read(self, path: str, field: str | None = None):
        return type(self).store.get(path)

    def write(self, path: str, value: str) -> None:
        type(self).store[path] = value
        type(self).writes.append((path, value))


# ===========================================================================
# 1. Idempotent re-run (S4.11 / S3.4 / S4.24)
# ===========================================================================


class TestIdempotentRerun:
    def test_gen_local_byte_identical_across_runs(self, tmp_path, monkeypatch):
        """S4.11 — GEN_LOCAL api_key store file is byte-identical on re-run.

        The project store file IS the persistence (S4.9): a second full dry-run
        must reuse it unchanged, never regenerate.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        r1 = run_engine(stack, monkeypatch)
        assert r1["status"] == "success"
        first = store_value(repo, "demo/app_api_key")

        clear_render_artifacts(stack)
        r2 = run_engine(stack, monkeypatch)
        assert r2["status"] == "success"
        second = store_value(repo, "demo/app_api_key")

        assert first == second and len(first) >= 8

    def test_ask_external_cached_no_prompt_on_rerun(self, tmp_path, monkeypatch):
        """S4.13 — ASK_EXTERNAL license cached: run 2 works with the env unset.

        Run 1 supplies CIU_SECRET_LICENSE; run 2 unsets it and runs
        non-interactively (no TTY) — the cached stack store file must satisfy it
        with no prompt and no abort.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")

        monkeypatch.setenv("CIU_SECRET_LICENSE", "cached-license-value")
        r1 = run_engine(stack, monkeypatch, yes=True)
        assert r1["status"] == "success"
        license_file = stack / ".ciu" / "secrets" / "license"
        assert license_file.read_text() == "cached-license-value"

        # Run 2: env gone, non-interactive — must reuse the cached store file.
        monkeypatch.delenv("CIU_SECRET_LICENSE", raising=False)
        clear_render_artifacts(stack)
        r2 = run_engine(stack, monkeypatch, yes=True)
        assert r2["status"] == "success"
        assert license_file.read_text() == "cached-license-value"

    def test_gen_ephemeral_differs_across_runs(self, tmp_path, monkeypatch):
        """S4.11 — GEN_EPHEMERAL run_nonce is a fresh value every run."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        run_engine(stack, monkeypatch)
        first = (stack / ".ciu" / "secrets" / "run_nonce").read_bytes()
        clear_render_artifacts(stack)
        run_engine(stack, monkeypatch)
        second = (stack / ".ciu" / "secrets" / "run_nonce").read_bytes()

        assert first != second

    def test_hook_state_persists_across_rerender(self, tmp_path, monkeypatch):
        """S3.4 — a [state] table written by a hook survives re-render.

        The app-config pre_compose hook applies a *config* value (not state), so
        to pin S3.4 we plant a post_compose hook that persists into [state] and
        assert the rendered ciu.toml [state] carries it after a SECOND render
        (which re-runs steps 1-15, preserving only [state]).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        # Plant a post_compose hook that writes [state].marker via persist:state.
        hook = stack / "persist_state_hook.py"
        hook.write_text(
            "def run(config, ctx):\n"
            "    return {'marker': {'value': 'kept', 'persist': 'state'}}\n",
            encoding="utf-8",
        )
        # Append the hook to the stack's existing pre_compose-bearing hooks table.
        defaults = stack / "ciu.defaults.toml.j2"
        defaults.write_text(
            defaults.read_text().replace(
                'pre_compose = ["./pre_compose_app.py"]',
                'pre_compose = ["./pre_compose_app.py"]\n'
                'post_compose = ["./persist_state_hook.py"]',
            ),
            encoding="utf-8",
        )

        # Run 1 writes [state].marker (post_compose runs in dry-run, S8.3 note).
        run_engine(stack, monkeypatch)
        doc1 = tomllib.loads((stack / "ciu.toml").read_text())
        assert doc1.get("state", {}).get("marker") == "kept"

        # Run 2 re-renders; only [state] survives — marker must still be there.
        clear_render_artifacts(stack)
        run_engine(stack, monkeypatch)
        doc2 = tomllib.loads((stack / "ciu.toml").read_text())
        assert doc2.get("state", {}).get("marker") == "kept"

    def test_secrets_never_in_ciu_toml(self, tmp_path, monkeypatch):
        """S4.24 — plaintext secrets MUST NOT appear in the rendered ciu.toml.

        Neither a ``[secrets]`` section (v1's withdrawn ``[secrets.local]``) nor
        the resolved api_key/license values may be present.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "a-very-secret-license")
        run_engine(stack, monkeypatch)

        doc = tomllib.loads((stack / "ciu.toml").read_text())
        # No top-level [secrets] table persisted into ciu.toml (S4.24).
        assert "secrets" not in doc
        text = (stack / "ciu.toml").read_text()
        api_key = store_value(repo, "demo/app_api_key").decode()
        assert api_key not in text
        assert "a-very-secret-license" not in text


# ===========================================================================
# 2. Leak containment (S4.21 / S4.22 / S4.23)
# ===========================================================================


class TestLeakContainment:
    def test_compose_and_overlay_contain_no_store_value(self, tmp_path, monkeypatch):
        """S4.22 — no resolved store-file value appears in compose or overlay.

        After a clean dry-run the rendered ciu.compose.yml and the overlay
        carry only names/paths; every materialized secret VALUE is absent.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        # A long, unique license that cannot collide with rendered substrings
        # (the 4-char "demo" would match "ciudemo" in the container name).
        monkeypatch.setenv("CIU_SECRET_LICENSE", "license-secret-9z8x7c")
        run_engine(stack, monkeypatch)

        compose = read_compose(stack)
        overlay = read_overlay(stack)
        for rel, name in (("demo/app_api_key", "api_key"),):
            value = store_value(repo, rel).decode()
            assert value not in compose
            assert value not in overlay
        for sec in ("license", "run_nonce"):
            value = (stack / ".ciu" / "secrets" / sec).read_text()
            assert value not in compose
            assert value not in overlay

    def test_overlay_yaml_contains_paths_only(self, tmp_path, monkeypatch):
        """S4.17 — the overlay declares secrets as ``file: <path>`` (paths only).

        Every secret entry must resolve to a filesystem path under the stack/
        project store (or the in-place ASK_FILE), never an inline value.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        run_engine(stack, monkeypatch)

        import yaml

        overlay = yaml.safe_load(read_overlay(stack))
        secrets = overlay["secrets"]
        assert set(secrets) == {"api_key", "license", "run_nonce", "ca_bundle"}
        for name, body in secrets.items():
            assert set(body) == {"file"}
            assert body["file"].startswith(str(repo))

    def test_print_context_redacts_values(self, tmp_path, monkeypatch, capsys):
        """S4.23 — --print-context renders secrets as ``<secret:name>``, no values."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "super-secret-license")
        run_engine(stack, monkeypatch, print_context=True)

        out = capsys.readouterr().out
        assert "<secret:api_key>" in out
        assert "<secret:license>" in out
        # No plaintext value of any secret appears in the redacted context.
        assert "super-secret-license" not in out
        assert store_value(repo, "demo/app_api_key").decode() not in out

    def test_compose_template_referencing_secret_value_fails(self, tmp_path, monkeypatch):
        """S4.21 — a compose template that stringifies a secret aborts (SecretLeakError).

        A doctored compose template that interpolates
        ``{{ app_config.secrets.api_key }}`` hits the guard's ``__str__`` and
        the run fails naming ``api_key`` — values can never enter the template.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        doctor_compose(
            stack,
            _app_compose(
                "[api_key, run_nonce]",
                extra="      - LEAK={{ app_config.secrets.api_key }}\n",
            ),
        )

        with pytest.raises(composefile.SecretLeakError) as exc:
            run_engine(stack, monkeypatch)
        assert "api_key" in str(exc.value)


# ===========================================================================
# 3. Consumption validation (S4.20)
# ===========================================================================


class TestConsumptionValidation:
    def test_undeclared_secret_reference_aborts(self, tmp_path, monkeypatch):
        """S4.20 — a service referencing an undeclared secret name aborts, naming it."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        doctor_compose(stack, _app_compose("[api_key, undeclared_name]"))

        with pytest.raises(ValueError) as exc:
            run_engine(stack, monkeypatch)
        msg = str(exc.value)
        assert "[S4.20]" in msg and "undeclared_name" in msg

    def test_declared_but_unconsumed_warns_and_succeeds(self, tmp_path, monkeypatch, capsys):
        """S4.20 — declared-but-unconsumed secrets warn, the run still succeeds.

        The demo declares ``license`` and ``ca_bundle`` but consumes neither in
        the compose ``secrets:`` list — CIU must warn (naming each) and finish.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        result = run_engine(stack, monkeypatch)
        assert result["status"] == "success"

        out = capsys.readouterr().out
        assert "consumed by no service" in out
        assert "license" in out and "ca_bundle" in out


# ===========================================================================
# 4. ASK_EXTERNAL fail-fast (S4.13)
# ===========================================================================


class TestAskExternalFailFast:
    def test_no_env_no_store_non_interactive_aborts(self, tmp_path, monkeypatch):
        """S4.13 — ASK_EXTERNAL with no env, no store, non-interactive → abort.

        The error carries [S4.13] and NO license store file is created (the
        abort happens during materialization before any persist).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        # CIU_SECRET_LICENSE / CIUDEMO_LICENSE already delenv'd by the autouse
        # fixture; ensure stdin is treated as non-interactive.
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

        with pytest.raises(ValueError) as exc:
            run_engine(stack, monkeypatch, yes=True)
        assert "[S4.13]" in str(exc.value)
        assert not (stack / ".ciu" / "secrets" / "license").exists()

    def test_exit_code_via_cli_is_2(self, tmp_path, monkeypatch):
        """S4.13 / S10.3 — the ASK_EXTERNAL abort maps to a config exit code (2).

        ASK_EXTERNAL no-value raises ValueError, which the S10.3 mapper classes
        as a configuration error → exit 2 (per the taxonomy in engine._exit_code_for).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

        rc = engine.main(["-d", str(stack), "--dry-run", "-y"])
        assert rc == 2


# ===========================================================================
# 5. ASK_FILE (S4.14)
# ===========================================================================


class TestAskFile:
    def test_missing_file_aborts_with_s4_14(self, tmp_path, monkeypatch):
        """S4.14 — a missing ASK_FILE target aborts with [S4.14]."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        # Remove the pre-provisioned file the ASK_FILE directive points at.
        (stack / "files" / "demo-ca.pem").unlink()

        with pytest.raises(ValueError) as exc:
            run_engine(stack, monkeypatch)
        assert "[S4.14]" in str(exc.value)

    def test_present_file_referenced_in_place(self, tmp_path, monkeypatch):
        """S4.14 — a present ASK_FILE is referenced in place (no copy under .ciu).

        The overlay's ``ca_bundle`` source must be the ORIGINAL stack file
        (remapped to its physical path), and no copy is written under
        ``.ciu/secrets``.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        run_engine(stack, monkeypatch)

        import yaml

        overlay = yaml.safe_load(read_overlay(stack))
        ca_src = overlay["secrets"]["ca_bundle"]["file"]
        # Physical path of the in-place files/demo-ca.pem (REPO==PHYSICAL here).
        assert ca_src == str(stack / "files" / "demo-ca.pem")
        assert not (stack / ".ciu" / "secrets" / "ca_bundle").exists()


# ===========================================================================
# 6. Configfile (S5.4 / S5.5)
# ===========================================================================


class TestConfigfile:
    def test_rendered_config_has_values_and_mode_0440(self, tmp_path, monkeypatch):
        """S5.4 — the rendered config.toml carries real api_key + license values, mode 0440.

        The configfile template is the ONLY place a secret value may appear
        (S5.4); the rendered file must be mode 0440 (S5.2/S4.22 exemption).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo-license")
        run_engine(stack, monkeypatch)

        cfg = rendered_config(stack)
        api_key = store_value(repo, "demo/app_api_key").decode()
        assert f'api_key = "{api_key}"' in cfg
        assert 'license = "demo-license"' in cfg

        mode = stat.S_IMODE((stack / ".ciu" / "rendered" / "app" / "main").stat().st_mode)
        assert mode == 0o440

    def test_unknown_secret_in_template_aborts_s5_4(self, tmp_path, monkeypatch):
        """S5.4 — secret('nope') for an undeclared name aborts with [S5.4]."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        doctor_config_template(
            stack,
            "[auth]\napi_key = \"{{ secret('nope') }}\"\n",
        )

        with pytest.raises(ValueError) as exc:
            run_engine(stack, monkeypatch)
        assert "[S5.4]" in str(exc.value) and "nope" in str(exc.value)

    def test_overlay_mounts_configfile_readonly_at_physical_path(self, tmp_path, monkeypatch):
        """S5.3 — the configfile mounts read_only at the target via a PHYSICAL path.

        A dedicated DooD split: PHYSICAL_REPO_ROOT differs from REPO_ROOT. The
        ``ciu.env`` is generated with both equal (build_repo), then we OVERRIDE
        PHYSICAL_REPO_ROOT in the env for the run, so the overlay's mount source
        must start with the physical prefix (S1.4). DooD preflight is skipped.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        physical_prefix = tmp_path / "host-view"
        monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical_prefix))
        # (CIU_SKIP_DOOD_PREFLIGHT=1 from the autouse fixture.)
        run_engine(stack, monkeypatch)

        import yaml

        overlay = yaml.safe_load(read_overlay(stack))
        app_volumes = overlay["services"]["app"]["volumes"]
        mount = next(v for v in app_volumes if v["target"] == "/etc/app/config.toml")
        assert mount["read_only"] is True
        assert mount["source"].startswith(str(physical_prefix))
        # The secret-file sources were remapped to the physical prefix too.
        assert overlay["secrets"]["api_key"]["file"].startswith(str(physical_prefix))


# ===========================================================================
# 7. Hostdir physical rewrite (S6.2)
# ===========================================================================


class TestHostdirPhysicalRewrite:
    def test_logs_hostdir_is_absolute_physical_path(self, tmp_path, monkeypatch):
        """S6.2 — the merged/rendered compose hostdir is an absolute physical path.

        The auto-named ``vol-app-config-logs`` dir is created under the stack
        copy, and the rendered compose emits its ABSOLUTE physical path as the
        bind source (the v1 relative ``./vol-*`` emission is withdrawn).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        run_engine(stack, monkeypatch)

        vol = stack / "vol-app-config-logs"
        assert vol.is_dir()

        compose = read_compose(stack)
        # The bind line carries the absolute physical path (REPO==PHYSICAL here).
        assert f"{vol}:/var/log/app" in compose
        assert "./vol-" not in compose


# ===========================================================================
# 8. Vault-backed flows with fake Vault (S4.11 / S4.12 / S4.16)
# ===========================================================================


class TestVaultBackedFlows:
    def test_gen_to_vault_generates_once_across_reruns(self, tmp_path, monkeypatch):
        """S4.11 — GEN_TO_VAULT generates exactly once; re-run reads the same value.

        Two dry-runs of redis-core against a fake Vault: the value is written on
        run 1 only (writes == 1), and run 2 reads back the identical value.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_REDIS, "infra/redis-core")

        FakeVaultKV2.reset()
        monkeypatch.setattr(engine, "VaultKV2", FakeVaultKV2)
        monkeypatch.setenv("VAULT_TOKEN", "test-token")

        run_engine(stack, monkeypatch)
        store = stack / ".ciu" / "secrets" / "redis_password"
        v1 = store.read_bytes()
        assert len(FakeVaultKV2.writes) == 1

        run_engine(stack, monkeypatch)
        v2 = store.read_bytes()
        assert len(FakeVaultKV2.writes) == 1  # no second write
        assert v1 == v2

    def test_gen_to_vault_refreshes_store_when_vault_changes(self, tmp_path, monkeypatch):
        """S4.12 — the materialized store file is refreshed from Vault every run.

        Changing the fake's stored value between runs must refresh the local
        store file (materialized files track the provider, S4.12).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_REDIS, "infra/redis-core")

        FakeVaultKV2.reset()
        monkeypatch.setattr(engine, "VaultKV2", FakeVaultKV2)
        monkeypatch.setenv("VAULT_TOKEN", "test-token")

        run_engine(stack, monkeypatch)
        store = stack / ".ciu" / "secrets" / "redis_password"
        assert store.read_text() != "rotated-out-of-band"

        # Rotate in the provider (the only sanctioned rotation, S4.12).
        path = next(iter(FakeVaultKV2.store))
        FakeVaultKV2.store[path] = "rotated-out-of-band"

        run_engine(stack, monkeypatch)
        assert store.read_text() == "rotated-out-of-band"

    def test_token_source_3_vault_stack_state(self, tmp_path, monkeypatch):
        """S4.16 — token source #3: the vault stack's ciu.toml [state].root_token.

        With no VAULT_TOKEN env and no token_file, a planted vault-stack
        ciu.toml carrying ``[state].root_token`` at ``vault.stack_path`` must
        resolve. Tested directly against ``providers.resolve_vault_token``.
        """
        repo = build_repo(tmp_path, monkeypatch)
        # Plant the vault stack's rendered ciu.toml with a [state].root_token.
        vault_dir = repo / "infra" / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "ciu.toml").write_text(
            "[vault_core]\nstack_name = \"vault\"\n\n"
            "[state]\ninitialized = true\nroot_token = \"s.from-state\"\n",
            encoding="utf-8",
        )

        config = {"vault": {"stack_path": "infra/vault"}}
        # No VAULT_TOKEN (autouse delenv'd it), no token_file → falls to source 3.
        assert providers_pkg.resolve_vault_token(config, repo) == "s.from-state"

    def test_token_env_precedes_state(self, tmp_path, monkeypatch):
        """S4.16 — source #1 (VAULT_TOKEN env) wins over the vault stack [state]."""
        repo = build_repo(tmp_path, monkeypatch)
        vault_dir = repo / "infra" / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        (vault_dir / "ciu.toml").write_text(
            "[vault_core]\nstack_name = \"vault\"\n\n"
            "[state]\nroot_token = \"s.from-state\"\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("VAULT_TOKEN", "s.from-env")
        config = {"vault": {"stack_path": "infra/vault"}}
        assert providers_pkg.resolve_vault_token(config, repo) == "s.from-env"


# ===========================================================================
# 9. Deploy orchestration (S7.1-S7.3, S7.5-S7.7)
# ===========================================================================


class _RecordingEngine:
    """Records main_execution calls; returns success unless the name fails."""

    def __init__(self, fail_for: set[str] | None = None):
        self.fail_for = fail_for or set()
        self.calls: list[dict] = []

    def main_execution(self, *, working_dir, dry_run, yes, update_cert_permission, compose_profiles, **kw):
        name = Path(working_dir).name
        self.calls.append({"name": name, "path_parent": Path(working_dir).parent.name})
        return {"status": "error" if name in self.fail_for else "success"}


def _patch_recording_engine(monkeypatch, stub: _RecordingEngine):
    monkeypatch.setattr(deploy.engine, "main_execution", stub.main_execution)
    monkeypatch.setattr(deploy.Path, "is_dir", lambda self: True)


def _phases_config(phases: dict, control: dict | None = None) -> dict:
    cfg = {"deploy": {"project_name": "p", "environment_tag": "t", "phases": phases}}
    if control is not None:
        cfg["deploy"]["control"] = control
    return cfg


class TestDeployOrchestration:
    def test_core_infra_selects_phase1_phase2_in_numeric_order(self, monkeypatch):
        """S7.1 — the 'core_infra' profile selects phase_1+phase_2 services in numeric order.

        Drives the REAL demo global config through build_selection: the profile
        restricts to [phase_1, phase_2]; the selection order is vault → redis →
        postgres (numeric phase order, declaration order within a phase).
        """
        cfg = _load_demo_global(monkeypatch)
        profile = deploy.resolve_profile(cfg, "core_infra")
        selection = deploy.build_selection(profile)

        assert [e["phase_key"] for e in selection] == ["phase_1", "phase_2", "phase_2"]
        assert [e["path"] for e in selection] == [
            "infra/vault",
            "infra/redis-core",
            "infra/db-core",
        ]

    def test_synthetic_phase_2_before_phase_10_end_to_end(self, monkeypatch, tmp_path):
        """S7.1 — phase_2 orders before phase_10 numerically through build_selection.

        Kills the v1 lexicographic bug end-to-end: a synthetic phases table runs
        phase_2 strictly before phase_10 in the produced selection.
        """
        cfg = _phases_config(
            {
                "phase_10": {"services": [{"path": "applications/late", "name": "late", "enabled": True}]},
                "phase_2": {"services": [{"path": "infra/early", "name": "early", "enabled": True}]},
            }
        )
        profile = Profile(name=None, phase_keys=None, config=cfg)
        selection = deploy.build_selection(profile)
        assert [e["name"] for e in selection] == ["early", "late"]
        assert [e["phase_key"] for e in selection] == ["phase_2", "phase_10"]

    def test_phase_2_failure_skips_phase_3_exit_1(self, monkeypatch, tmp_path):
        """S7.3 — a failure in the first phase_2 service skips phase_3; exit 1."""
        cfg = _phases_config(
            {
                "phase_1": {"services": [{"path": "infra/a", "name": "a", "enabled": True}]},
                "phase_2": {"services": [{"path": "infra/b", "name": "b", "enabled": True}]},
                "phase_3": {"services": [{"path": "applications/c", "name": "c", "enabled": True}]},
            }
        )
        profile = Profile(name=None, phase_keys=None, config=cfg)
        stub = _RecordingEngine(fail_for={"b"})
        _patch_recording_engine(monkeypatch, stub)

        rc = deploy.action_deploy(
            tmp_path, profile, deploy.build_selection(profile),
            dry_run=False, ignore_errors=False, health_after_phase=False,
            update_cert_permission=False,
        )
        assert rc == 1
        assert [c["name"] for c in stub.calls] == ["a", "b"]  # c never called

    def test_ignore_errors_runs_all_exit_1(self, monkeypatch, tmp_path):
        """S7.3 — --ignore-errors runs every stack but the final exit is still 1."""
        cfg = _phases_config(
            {
                "phase_1": {"services": [{"path": "infra/a", "name": "a", "enabled": True}]},
                "phase_2": {"services": [{"path": "infra/b", "name": "b", "enabled": True}]},
                "phase_3": {"services": [{"path": "applications/c", "name": "c", "enabled": True}]},
            }
        )
        profile = Profile(name=None, phase_keys=None, config=cfg)
        stub = _RecordingEngine(fail_for={"b"})
        _patch_recording_engine(monkeypatch, stub)

        rc = deploy.action_deploy(
            tmp_path, profile, deploy.build_selection(profile),
            dry_run=False, ignore_errors=True, health_after_phase=False,
            update_cert_permission=False,
        )
        assert rc == 1
        assert [c["name"] for c in stub.calls] == ["a", "b", "c"]

    def test_enabled_flag_honored_false_drops_service(self, monkeypatch):
        """S7.2 — an ``enabled = "<flag>"`` control flag set false drops the service."""
        phases = {
            "phase_1": {
                "services": [
                    {"path": "infra/a", "name": "a", "enabled": True},
                    {"path": "applications/app", "name": "app", "enabled": "enable_app"},
                ]
            }
        }
        on = Profile(name=None, phase_keys=None, config=_phases_config(phases, control={"enable_app": True}))
        off = Profile(name=None, phase_keys=None, config=_phases_config(phases, control={"enable_app": False}))

        assert [e["name"] for e in deploy.build_selection(on)] == ["a", "app"]
        assert [e["name"] for e in deploy.build_selection(off)] == ["a"]

    def test_unknown_enabled_flag_exit_2(self, monkeypatch, tmp_path):
        """S7.2 / S10.3 — an unknown control-flag name aborts with exit 2.

        build_selection raises ValueError [S7.2]; through the CLI single exit
        point that maps to a configuration exit code (2).
        """
        phases = {
            "phase_1": {"services": [{"path": "infra/a", "name": "a", "enabled": "nope"}]}
        }
        cfg = _phases_config(phases, control={"enable_app": True})
        profile = Profile(name=None, phase_keys=None, config=cfg)
        with pytest.raises(ValueError) as exc:
            deploy.build_selection(profile)
        assert "[S7.2]" in str(exc.value)
        # Same error through the deploy main() exit-code mapper → 2.
        assert engine._exit_code_for(exc.value) == 2

    def test_topology_overrides_visible_in_profile_config(self, monkeypatch):
        """S7.4 — a profile's topology_overrides are visible in profile.config.

        The 'workers' profile points topology.services.vault at host A; that
        override must be deep-merged into profile.config (what the vault
        preflight / vault_addr_from_config reads).
        """
        cfg = _load_demo_global(monkeypatch)
        profile = deploy.resolve_profile(cfg, "workers")
        vault = profile.config["topology"]["services"]["vault"]
        assert vault["internal_host"] == "ciudemo-dev-vault"
        # vault_addr_from_config consumes exactly this overridden topology.
        addr = providers_pkg.vault_addr_from_config(profile.config)
        assert addr == "http://ciudemo-dev-vault:8200"

    def test_deploy_groups_rejected_exit_2(self, monkeypatch, tmp_path):
        """S7.5 — a planted [deploy.groups] table is rejected (config error → exit 2)."""
        monkeypatch.setattr(
            deploy.config_model,
            "render_global_chain",
            lambda working_dir, repo_root: {"deploy": {"groups": {"infra": ["phase_1"]}}},
        )
        with pytest.raises(ValueError) as exc:
            deploy.load_global_config(tmp_path)
        assert "[S7.5]" in str(exc.value)
        assert engine._exit_code_for(exc.value) == 2

    def test_health_gate_starting_not_passed(self):
        """S7.7 — a 'starting' status is NOT passed (pending bucket fails the gate)."""
        passed, summary = health_pkg.evaluate_gate({"svc-a": "starting", "svc-b": "healthy"})
        assert passed is False
        assert summary["pending"] == ["svc-a"]
        assert summary["healthy"] == ["svc-b"]

    def test_health_gate_wired_through_deploy_with_stubbed_inspect(self, monkeypatch):
        """S7.7 — deploy's health action gates 'starting' as failed via the inspect seam.

        Wires deploy.run_health_gate with a stubbed _inspect_state returning a
        'starting' Health.Status; the gate must fail (return passed=False) and
        bucket the container as pending — exercising deploy's classify→evaluate
        path, not just the pure call.
        """
        config = {"deploy": {"project_name": "p", "environment_tag": "t"}}
        monkeypatch.setattr(deploy, "_inspect_state", lambda name: {"Health": {"Status": "starting"}})
        passed, summary = deploy.run_health_gate(
            config, ["svc"], timeout_s=0.0, interval_s=0.0
        )
        assert passed is False
        assert summary["pending"] == ["p-t-svc"]


def _load_demo_global(monkeypatch) -> dict:
    """Render the REAL demo ciu.global.defaults.toml.j2 (no tmp copy needed).

    Sets the env keys the template expands, then renders the global chain with
    working_dir == repo_root == test-repo. Used by the deploy-selection tests
    that pin the demo's authored phases/profiles (S7.1/S7.4).
    """
    monkeypatch.setenv("REPO_ROOT", str(TEST_REPO))
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(TEST_REPO))
    return deploy.config_model.render_global_chain(TEST_REPO, TEST_REPO)


# ===========================================================================
# 10. Exit taxonomy (S10.3)
# ===========================================================================


class TestExitTaxonomy:
    def test_success_is_0(self, tmp_path, monkeypatch):
        """S10.3 — a successful dry-run returns exit 0 (use app-config)."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")

        rc = engine.main(["-d", str(stack), "--dry-run", "-y"])
        assert rc == 0

    def test_validation_error_is_2(self, tmp_path, monkeypatch):
        """S10.3 — a validation/config error (undeclared secret) returns exit 2."""
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        doctor_compose(stack, _app_compose("[api_key, undeclared_name]"))

        rc = engine.main(["-d", str(stack), "--dry-run", "-y"])
        assert rc == 2

    def test_missing_stack_dir_is_runtime_exit_1(self, tmp_path, monkeypatch):
        """S10.3 — a missing stack dir maps to the runtime exit code (1).

        With a valid repo but a non-existent stack subdir, ``render_stack``
        raises ``FileNotFoundError`` (missing ``ciu.defaults.toml.j2``). That is
        NOT WorkspaceEnvError/Dependency/DooD (3) and NOT ValueError (2), so the
        S10.3 mapper's default runtime bucket applies → exit 1. Pinned exactly.
        """
        repo = build_repo(tmp_path, monkeypatch)
        missing = repo / "applications" / "does-not-exist"

        rc = engine.main(["-d", str(missing), "--dry-run", "-y"])
        assert rc == 1

    def test_workspace_env_error_is_3(self, tmp_path, monkeypatch):
        """S10.3 — a WorkspaceEnvError (bootstrap) maps to exit 3.

        We force bootstrap to raise WorkspaceEnvError; main() must return 3.
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")

        def _boom(*a, **k):
            raise engine.WorkspaceEnvError("[S2.2] simulated missing required env key")

        monkeypatch.setattr(engine, "bootstrap_workspace_env", _boom)
        rc = engine.main(["-d", str(stack), "--dry-run", "-y"])
        assert rc == 3


# ===========================================================================
# 11. cwd restore (S8.4)
# ===========================================================================


class TestCwdRestore:
    def test_cwd_unchanged_after_failing_run(self, tmp_path, monkeypatch):
        """S8.4 — on a failing (doctored) run the process cwd is restored.

        The engine chdir's into the stack and MUST restore the original cwd in
        its finally, even when the run aborts (here via an undeclared-secret
        compose that raises during step 14).
        """
        repo = build_repo(tmp_path, monkeypatch)
        stack = add_stack(repo, SRC_APP, "applications/app-config")
        monkeypatch.setenv("CIU_SECRET_LICENSE", "demo")
        doctor_compose(stack, _app_compose("[api_key, undeclared_name]"))

        before = os.getcwd()
        with pytest.raises(ValueError):
            run_engine(stack, monkeypatch)
        assert os.getcwd() == before
