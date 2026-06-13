# CIU v2 — Architecture Reference

For contributors. Normative contract: [SPEC.md](SPEC.md). Work-packet history:
[docs/plans/V2-PACKETS.md](plans/V2-PACKETS.md).

---

## Module Map

| Module | Responsibility | Key public functions | Spec sections | Test file |
|---|---|---|---|---|
| `engine.py` | Single-stack CLI entry point; implements the 17-step pipeline [S8.3]; hostdir creation; DooD preflight; `ciu secrets` subcommand; `--shipped` passthrough [S8.6] | `main_execution`, `run_shipped`, `create_hostdirs`, `reset_service`, `privileged_fs_op`, `secrets_command`, `main` | S1–S6, S8–S11 | `test_ciu_test_repo.py`, `test_ciu_reset_service.py`, `test_ciu_hostdir_creation.py`, `test_ciu_shipped.py` |
| `config_model.py` | Template rendering pipeline (Jinja2 → env-expand → TOML parse → deep-merge); global chain; stack render; static validation | `render_jinja2_text`, `expand_env_vars_or_fail`, `render_global_chain`, `render_stack`, `deep_merge`, `validate_stack_shape` | S3.1–S3.10, S11 | `test_ciu_config_model.py` |
| `composefile.py` | Compose template render; secret guard injection; leak scan; configfile render; overlay generation; compose process env | `render_compose`, `guard_config`, `leak_scan`, `render_configfiles`, `generate_overlay`, `compose_process_env`, `validate_consumption` | S4.17–S4.23, S5.1–S5.5, S8.1–S8.2 | `test_ciu_composefile.py` |
| `hooks_runner.py` | Hook loading; `HOOK_POINTS` tuple; `HookExecutionError`; structured-return dispatch; `apply_to_config` + `persist:"state"` | `run_hooks`, `load_hook`, `HookContext`, `HookExecutionError` | S9.1–S9.4 | `test_ciu_hooks_runner.py`, `test_hook_interfaces.py` |
| `secrets/directives.py` | **Single** directive parser; `SecretSpec` dataclass; `discover`; `find_misplaced` | `parse_value`, `discover`, `find_misplaced`, `SecretSpec` | S4.1–S4.7 | `test_ciu_secret_directives.py` |
| `secrets/providers.py` | Vault KV2 I/O; token source order; `VaultKV2` client | `vault_addr_from_config`, `resolve_vault_token`, `VaultKV2` | S4.15–S4.16 | `test_ciu_secret_directives.py` |
| `secrets/materialize.py` | Secret resolution and file writing; `GEN_LOCAL` project store; `ASK_EXTERNAL` prompt + cache; lock; `list_secrets`; `reset_secrets` | `materialize`, `list_secrets`, `reset_secrets`, `stack_store`, `project_store` | S4.8–S4.14, S4.25–S4.26 | `test_ciu_secrets_materialize.py` |
| `workspace_env.py` | `ciu.env` generation and loading; DooD detection; network creation/attach; TLS probe | `load_workspace_env`, `ensure_workspace_env`, `resolve_env_root`, `validate_required_certs` | S1.1–S1.5, S2.1–S2.8 | `test_ciu_workspace_env_v2.py` |
| `paths.py` | Logical→physical path mapping (DooD) | `to_physical_path`, `is_under` | S1.3–S1.4 | `test_ciu_paths_procutil.py` |
| `procutil.py` | **All** subprocess calls behind `run_cmd` / `docker`; no `shell=True` | `run_cmd`, `docker` | S7.3, S7.8 | `test_ciu_paths_procutil.py` |
| `config_constants.py` | **Single source of truth** for every CIU file/dir name (config, compose, `ciu.env`, `.ciu/` overlay/secrets/rendered/lock, `SHIPPED_COMPOSE`); all modules import from here so a rename is one edit | `get_rendered_config_name`, `get_defaults_template_name`, `is_config_file` | S1.6–S1.8, S3.1, S8.5 | — |
| `deploy.py` | Multi-stack CLI entry point; action dispatch; Vault/registry preflights; phase execution; health gate | `action_deploy`, `action_stop`, `action_clean`, `action_build`, `vault_preflight`, `registry_preflight`, `run_health_gate` | S7.1–S7.9, S8.2 | `test_ciu_deploy_actions.py`, `test_ciu_deploy_pkg.py` |
| `deploy_pkg/phases.py` | Phase ordering (numeric); `service_enabled`; `service_shipped` (S8.6); `iter_enabled_services` | `ordered_phases`, `service_enabled`, `service_shipped`, `iter_enabled_services` | S7.1–S7.3, S8.6 | `test_ciu_deploy_pkg.py` |
| `deploy_pkg/profiles.py` | Profile resolution; `[deploy.groups]` rejection; `topology_overrides` merge | `resolve_profile`, `reject_groups`, `Profile` | S7.4–S7.5a | `test_ciu_deploy_pkg.py` |
| `deploy_pkg/health.py` | Health gate; `starting`/pending classification; anchored filter | `classify`, `evaluate_gate`, `wait_for_gate`, `anchored_name_filter` | S7.7–S7.8 | `test_ciu_deploy_pkg.py` |
| `deploy_pkg/registry.py` | Docker registry credential verification | `check_registry_auth` | S7.9 | `test_ciu_deploy_pkg.py` |
| `deploy_pkg/http_util.py` | HTTP helpers for health/selftest endpoints | — | — | — |
| `cli.py` / `cli_utils.py` | Argument parsers for `ciu` and `ciu-deploy`; exit-code mapping | `parse_arguments` (engine), `main` | S10.1–S10.3 | `test_ciu_cli_parser.py` |

**Deleted modules** (no longer exist): `hooks/local_secrets_hook.py` (superseded by GEN_LOCAL directives), `hooks/examples/post_compose_minio_example.py` (too v1-specific), `tools/test_config_structure.py` (v1 file conventions), the `tools/` package (no remaining content after deletion).

**Deleted functions**: `engine.extract_service_definitions` (SPEC S3.8 amended to withdraw it — the global `[service.X.Y.Z]` extraction pattern is not used in v2).

---

## Contributor Invariants

1. **Single directive parser** — `secrets/directives.py` is the **only** place
   that parses directive strings. `engine.py` and `deploy.py` both import it.
   A new directive verb is added there first, then the provider in
   `secrets/providers.py`, then the resolver in `secrets/materialize.py` [S4.7].

2. **`procutil` for all subprocesses** — every `docker` / `git` / external-tool
   invocation goes through `procutil.run_cmd` or `procutil.docker`. No
   `subprocess.run(..., shell=True)`, no `os.system`, no `subprocess.Popen`
   outside `procutil`. This is the single point for logging, timeout, and
   dry-run short-circuit. **Exemption**: the live-streaming compose `Popen` in
   `engine.execute_docker_compose_with_logs` uses `subprocess.Popen` directly
   because it must stream stdout line-by-line while compose runs; this is the
   only justified use of `Popen` outside `procutil`.

3. **No `sys.exit` outside CLI mains** — `engine.main`, `deploy.main`, and the
   two test runners are the only callers of `sys.exit`. Every other function
   raises an exception. This is what allows `--ignore-errors` to work correctly
   at the `action_deploy` layer [S7.3].

4. **Secrets never in TOML or logs** — secret values appear only in
   `.ciu/secrets/<name>` store files and (transiently) in memory during
   materialization. `print_context` / all logs redact via `redact_config`.
   Configfile templates are the only place a secret value may be embedded
   (via `secret()`) [S4.21–S4.24].

5. **Tests reference spec IDs** — every regression test that covers a spec
   requirement names the spec ID in its docstring or a comment. Commit messages
   for spec-related fixes include the `[S-xx]` anchor. This makes it easy to
   audit which requirements have test coverage.

---

## Key Data-Flow Sketch

```
ciu.global.defaults.toml.j2  ──┐
ciu.global.toml.j2            ─┤  config_model.render_global_chain
ciu.env                       │       │
                               └───────▼
ciu.defaults.toml.j2  ────────┐     merged global config
ciu.toml.j2           ────────┤  config_model.render_stack
                               └───────▼
                                   merged stack config
                                       │
                       [S11 validate]  ▼
                                   guarded config (secrets → SecretGuard)
                                       │
                            hooks      ▼ (pre_secrets, pre_compose, post_compose)
                          secrets/materialize.materialize
                          composefile.render_compose
                          composefile.leak_scan
                          composefile.generate_overlay
                                       │
                          docker compose -f base -f overlay up -d
```
