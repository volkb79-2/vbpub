---
schema_version: 1
id: nyxloom-P39-ntfy-url-single-source
project: nyxloom
title: "ntfy URL is server-authoritative (one NTFY_URL env source, not per-project)"
tier: sonnet5-high
input_revision: "613d15f"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/config.py"
    - "nyxloom-trove/nyxloom.toml"
    - "tests/test_config.py"
  forbid:
    - "src/nyxloom/notify.py"
    - "src/nyxloom/daemon.py"
oracles:
  - id: O1
    observable: "`NotifyConfig` resolves `ntfy_url` from the `NTFY_URL` environment variable FIRST (deployment/server-authoritative — the ntfy stack owns its FQDN via tls-edge/PUBLIC_FQDN), overriding any `[notify] ntfy_url` in a project's nyxloom.toml. A test sets `NTFY_URL` + a different toml value and asserts the env value wins."
    negative: "the URL comes only from per-project toml, so it is hardcoded + duplicated across every project (nyxloom + dstdns both carry the same literal today) and silently breaks when the server's FQDN changes."
    gate: tester-unified
  - id: O2
    observable: "Backward-compatible fallback: when `NTFY_URL` is unset, `NotifyConfig` still uses `[notify] ntfy_url` if present, else None (notifications simply disabled, existing behavior). A test asserts the fallback chain env -> toml -> None."
    negative: "removing the toml key with no env set crashes or mis-sends — the migration must be safe."
    gate: tester-unified
  - id: O3
    observable: "nyxloom's OWN `nyxloom-trove/nyxloom.toml` no longer hardcodes `ntfy_url` (the `[notify]` key is deleted); config still loads and, with `NTFY_URL` set, notifications resolve correctly. A test loads the repo's own config and asserts no toml-level ntfy_url is required."
    negative: "the per-project hardcode remains, perpetuating the duplication this package removes."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "resolving NTFY_URL from env requires touching notify.py's send path or daemon.py (both forbidden) rather than NotifyConfig alone — then BLOCKED"
  - "changing the URL source would require touching ntfy AUTH (the admin user / tokens) — it must NOT; auth is out of scope and unchanged"
---

# P39 — ntfy URL is server-authoritative (one NTFY_URL env source)

The ntfy **server** owns its URL (the ntfy stack's FQDN, derived from
tls-edge/`PUBLIC_FQDN`) — but every project's `nyxloom.toml [notify]` currently
**re-hardcodes** it (`ntfy_url = "https://nyxloom.gstammtisch.dchive.de"`,
duplicated in nyxloom + dstdns), so it drifts/breaks if the server moves. Make
the URL come from a single deployment source (`NTFY_URL` env, set alongside
`NTFY_TOKEN` in the nyxloomd deployment) and strip the per-project hardcodes.
Channels stay the two deployment-global names; **auth (the admin user + the two
tokens) is untouched** — the operator relies on that admin user.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P39-ntfy-url-single-source` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/config.py` — `class NotifyConfig` (~69-79): `ntfy_url` field +
  the loader that reads `[notify]` (the `notifications_topic`/`feedback_topic`
  mapping at ~155-162 is the pattern). Add env-first resolution for `ntfy_url`.
- `src/nyxloom/notify.py` (READ only, forbidden) — confirms the send path uses
  `nc.ntfy_url` (notify.py:184-186); resolving it in NotifyConfig means notify.py
  needs no change.
- `nyxloom-trove/nyxloom.toml` (~71-74 `[notify]`) — delete the `ntfy_url` line;
  keep `notifications_topic`/`feedback_topic`.
- `docs/runtime-process-model.md` §5 area (notification authority) if present, or
  the general rule: server owns URL + channel existence + ACLs; client declares
  only channel intent.

## Work

1. `config.py` `NotifyConfig`: resolve `ntfy_url` as `env NTFY_URL` -> `[notify]
   ntfy_url` -> None (env wins). Keep it a plain attribute after resolution.
2. `nyxloom-trove/nyxloom.toml`: remove the `ntfy_url` key from `[notify]`.
3. `tests/test_config.py`: prove O1 (env overrides toml), O2 (fallback chain),
   O3 (repo config loads with no toml ntfy_url + NTFY_URL set).
4. REPORT: note the deployment step (set `NTFY_URL` in `nyxloomd/.env`/compose
   env like `NTFY_TOKEN`) and that the other projects' toml `ntfy_url` lines
   (e.g. dstdns) are deleted at deploy time (cross-repo, out of this worktree).

## Scope / forbid

Touch ONLY the three files in `scope.touch`. Do NOT touch `notify.py`,
`daemon.py`, or anything under ntfy AUTH (admin user / tokens stay as-is).

## BLOCKED rule

If env-first resolution can't be done in NotifyConfig alone (needs notify.py or
daemon.py), STOP — write `BLOCKED: <reason>` to the LOG, commit, exit.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
