"""Reference hook template: a generic database readiness + secret check.

Hook point: post_compose (S9.1) — copy this file with `ciu init --hooks
post_compose_db` into `<stack_dir>/hooks/post_compose_db.py` (stamped with
this module's CURRENT `template_revision` at copy time; see docs/SPEC.md
S19.1 for the exact stamp format) and declare it under
`[<your-root-key>.hooks].post_compose` in your stack's `ciu.defaults.toml.j2`
the same way any other hook is declared (S9.1).

`template_revision` RULE (read this before editing the body below): bump the
integer by exactly 1 every time this module's `run`/`validate_config`
BEHAVIOR changes — the value a future `ciu config check` revision-comparison
feature (tracked as a CIU-QOL-13 follow-up) would compare a consumer's
stamped copy against. Comment-only or docstring-only edits do not need a
bump.

WHAT THIS TEMPLATE IS — and is NOT (read before copying):
This demonstrates exactly two things a real `post_compose` hook commonly
needs from `ctx` (S9.3): a readiness probe (`ctx.wait_healthy`) and a
secret's store-file path (`ctx.secret_file`). It does **not** create
databases, users, or schemas, and it makes no assumption about which
database engine you run — `deploy.db_service_name` and the `db_password`
secret name are both starting-point placeholders. Copy this file and extend
it with your own provisioning logic; do not deploy it unmodified and expect
a real database bootstrap.
"""
from __future__ import annotations

template_revision: int = 1


def run(config: dict, ctx) -> dict:
    """Wait for the configured database service, then record what happened.

    Reads `deploy.db_service_name` (falls back to `"db"` when a consumer
    hasn't declared one yet — see `validate_config` below, which flags that
    as a finding rather than letting it pass silently). Persists two small,
    non-secret facts under `[state]` so later `ciu check`/operator review can
    see this hook actually ran and what it observed — it does not touch the
    database itself.
    """
    service = config.get("deploy", {}).get("db_service_name", "db")

    ready = True
    if ctx.wait_healthy is not None:
        ready = ctx.wait_healthy(service)

    password_materialized = False
    try:
        password_materialized = ctx.secret_file("db_password").exists()
    except KeyError:
        # No 'db_password' secret declared for this stack — not an error,
        # this template does not require one to exist.
        password_materialized = False

    return {
        "hook_state.db_service_healthy": {
            "value": ready,
            "persist": "state",
        },
        "hook_state.db_password_materialized": {
            "value": password_materialized,
            "persist": "state",
        },
    }


def validate_config(config: dict, ctx) -> list[str]:
    """Preflight (S9.5): confirm what `run()` needs is actually DECLARED.

    Only checks declared config shape — never a materialized secret value.
    `ctx.secret_file` raises `KeyError` for every name during `ciu check`
    (S9.5), so this deliberately does not call it.
    """
    errors: list[str] = []
    deploy = config.get("deploy", {})
    if "db_service_name" not in deploy:
        errors.append(
            "deploy.db_service_name is not declared; run() would default "
            "to 'db', which may not match your compose service name"
        )
    return errors
