# nyxloom-P39-ntfy-url-single-source — REVIEW

- **Reviewer:** independent frontier reviewer (merge gate)
- **Date:** 2026-07-16
- **Branch:** `feat/nyxloom-P39-ntfy-url-single-source`
- **Implementer commit reviewed:** `778b552` — *carve(nyxloom): P39 ntfy URL single-source*
- **Verdict:** **APPROVED** (1 substantive defect found and fixed by reviewer)

## Git state verified (not trusted from receipt)

`git log main..feat/nyxloom-P39-ntfy-url-single-source` → exactly one commit, `778b552`.
`git status` in the worktree → clean, no uncommitted implementer work. The packet's
diffstat (3 files, +74/-1) matches the real diff. Scope respected: `notify.py` and
`daemon.py` (both `scope.forbid`) untouched; ntfy auth (admin user / tokens) untouched.

## Gate re-run (not trusted from report)

Declared gate `tester-unified`, run by me against the branch worktree:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P39-ntfy-url-single-source/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests'
```

- At `778b552` (as delivered): **530 passed**, exit 0.
- After my fixes: **533 passed**, exit 0.

Note: `pytest -q` in this repo's config does **not** emit the summary line; the run
must be read via exit code or without `-q`. A pasted `-q` tail is not evidence of a
pass — worth knowing for future reviews of this project.

## Finding 1 (MAJOR, fixed) — env override applied at dataclass construction, not at config load

`778b552` resolved `NTFY_URL` inside `NotifyConfig.__post_init__`. That runs on
**every** `NotifyConfig(...)` construction, not just the TOML-loading path, so an
ambient `NTFY_URL` silently overrode arguments a caller passed **explicitly**. Two
concrete consequences:

1. **"Notifications disabled" became inexpressible.** `NotifyConfig(ntfy_url=None,
   webhook_url=None)` — the explicit disabled config — silently acquired the env URL,
   so `notify.py`'s `both_unconfigured` guard (notify.py:277) stopped firing.
2. **Callers aimed at a specific endpoint got silently retargeted at the production
   server.** Every test constructing a local stub (`NotifyConfig(ntfy_url=
   f"http://127.0.0.1:{port}")`) or a deliberately closed port would instead have
   issued real requests — including a bearer token — at the real ntfy deployment.

This is the exact environment the handoff *mandates* (`NTFY_URL` set alongside
`NTFY_TOKEN` in the nyxloomd deployment), so it is not a hypothetical env.

**Proof (at `778b552`):** the suite is green on a clean env but breaks the moment the
deployment's own variable is present —

```
$ pytest tests                                     → 530 passed
$ NTFY_URL=https://nyxloom.gstammtisch.dchive.de pytest tests
  → 4 failed, 526 passed
    FAILED tests/test_notify.py::test_send_ntfy_success
    FAILED tests/test_notify.py::test_notify_event_both_unconfigured
    FAILED tests/test_notify.py::TestTokenAuth::test_bearer_token_header_from_env
    FAILED tests/test_commands.py::test_transport_reply_and_reconnect_carries_since
```

Before P39, `NotifyConfig` had no env sensitivity, so this fragility is introduced by
this change and is attributable to it.

**Fix applied (reviewer):** moved the resolution out of `__post_init__` and into
`ProjectConfig.load()`, where the `[notify]` TOML table is assembled — alongside the
existing `notifications_topic`/`feedback_topic` mapping. The env is now authoritative
over the **TOML source only**, which is precisely what the handoff asks for ("resolve
`ntfy_url` as `env NTFY_URL` -> `[notify] ntfy_url` -> None"). Programmatic
construction keeps the URL the caller passes. All three oracles still hold; the fix
stays inside `scope.touch` (`config.py`).

## Finding 2 (MINOR, fixed) — O1's test did not exercise the oracle's stated path

O1 requires: *"A test sets `NTFY_URL` + **a different toml value** and asserts the env
value wins."* The delivered `test_ntfy_url_env_overrides_toml` set `NTFY_URL` plus a
**constructor kwarg** — no TOML was involved at any point, so the env-over-TOML
precedence O1 names was never actually tested. Same for the O2 fallback test. They
passed only because `__post_init__` made construction and loading behave alike — the
very conflation that caused Finding 1.

**Fix applied (reviewer):** rewrote `tests/test_config.py` so O1/O2 load a real
`nyxloom-trove/nyxloom.toml` fixture through `ProjectConfig.load()`. Added coverage:

- `test_direct_construction_is_not_overridden_by_env` and
  `test_direct_construction_can_express_disabled_under_env` — regression guards for
  Finding 1.
- `test_empty_ntfy_url_env_does_not_shadow_toml` — pins the pre-existing (correct)
  `if env_url:` truthiness behaviour, so a blank `NTFY_URL` reads as unset rather than
  disabling notifications by pointing them at `""`.

## Finding 3 (MINOR, fixed) — suite hermeticity

`test_commands.py::test_transport_reply_and_reconnect_carries_since` configures ntfy
via TOML, so under P39's (intended) env-over-TOML precedence an ambient `NTFY_URL`
retargets the listener away from `_FakeNtfyServer`. `tests/conftest.py` is marked
FROZEN ("implementation agents add local fixtures in their own test files, never
here"), so rather than an autouse fixture I added a one-line
`monkeypatch.delenv("NTFY_URL", raising=False)` to that test, matching the idiom it
already uses for `NTFY_TOKEN`/`NTFY_CMD_TOKEN`.

This touches `tests/test_commands.py`, one file outside the handoff's `scope.touch`
(it is not in `scope.forbid`). Disclosed here deliberately: it is a test-hermeticity
one-liner required by the change under review, not a scope expansion.

**Post-fix, the suite is hermetic:** `pytest tests` → 533 passed and
`NTFY_URL=... pytest tests` → 533 passed, both exit 0.

## Finding 4 (NOT fixed — operator action required) — no REPORT; deployment step undocumented

The handoff's Work item 4 requires a REPORT noting the deployment step and the
cross-repo cleanup. **No LOG or REPORT for `nyxloom-P39` exists on the branch** (the
`topos/nyxloom-trove/reports/P39-*.md` files belong to an unrelated task,
`P39-release-readiness-ledger`). Per the reviewer role contract I must not write the
implementer's REPORT, so the requirement is recorded here instead.

This matters beyond paperwork, and it is O2's negative in practice:

> **Merging P39 without setting `NTFY_URL` in the nyxloomd deployment silently
> disables all nyxloom notifications.**

The `[notify] ntfy_url` hardcode that made notifications work has been deleted, so
with no env var the chain resolves to `None`. It does not crash — `notify.py` takes
the `both_unconfigured` path and records `NOTIFICATION_FAILED{detail: "unconfigured"}`
— it just goes quiet. The migration is safe (no crash, no mis-send) but **not
self-announcing**.

Required at deploy time, outside this worktree:

1. Set `NTFY_URL` in the nyxloomd deployment env (`.env`/compose), next to
   `NTFY_TOKEN` — **before or with** this merge.
2. Delete the now-dead `[notify] ntfy_url` line from other projects' toml (e.g.
   dstdns), which the env var now overrides anyway.

## Oracle verdicts

| Oracle | Verdict | Evidence |
|---|---|---|
| O1 — `NTFY_URL` overrides `[notify] ntfy_url` | **PASS** (after Finding 2 fix) | `test_ntfy_url_env_overrides_toml` now loads a TOML carrying a different URL and asserts env wins |
| O2 — fallback chain env → toml → None | **PASS** (after Finding 2 fix) | three tests over the full chain, all via `ProjectConfig.load()`; empty-env case pinned |
| O3 — repo's own toml no longer hardcodes the URL | **PASS** | `[notify] ntfy_url` removed from `nyxloom-trove/nyxloom.toml`; repo config loads, resolves from env, and is `None` without it |

## Reasoning

The design is right and matches the handoff: the ntfy server owns its URL, projects
declare only channel intent, auth untouched, `notify.py`/`daemon.py` needed no change.
The defect was one of **layer**, not intent — env-first resolution belongs at the TOML
load boundary, not on every dataclass construction. That is a contained, ~10-line
correction inside the handoff's own scope, not an architectural rework, so per the
role contract I fixed it rather than rejecting.

Approving with all three code findings fixed and the gate re-run green in both the
clean and the deployment env. **Finding 4 is an operator prerequisite, not a code
defect: set `NTFY_URL` in the nyxloomd deployment before/with this merge, or
notifications go silently dark.**
