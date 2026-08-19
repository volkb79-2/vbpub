# ciu config-wave controller brief — 2026-08-19

**Mission:** implement the four carved packages `ciu-P08..P11` (`nyxloom-trove/handoffs/`),
in the release grouping below, so their consumer (dstdns's configuration/landscape program,
`dstdns/docs/spec-configuration-and-landscape.md`, D-094..D-107) gets what it needs first.
Written by the dstdns Fable controller session (`dstdns-20260818`); the shapes were decided
there — an implementer's job here is execution, not re-design.

## Release plan (needs-first)

| release | packages | why this order | conflict posture |
|---|---|---|---|
| **R1 — now** | `ciu-P08-landscape-identity` + `ciu-P09-configfile-schema-validation` | what dstdns config-cutover consumes: P09 makes a bad rendered app config fail at render (its safety net), P08 validates the landscape_id slug. Both small. | **Touches `config_model.py`/`composefile.py`/docs only — NO `cli.py`** → safe to run in parallel with the unmerged `ciu-worktree-automation-backlog` branch (their P04–P07). Docs files overlap textually; trivial merges. |
| **R2 — after the worktree-automation branch merges** | `ciu-P10-deploy-layouts` (depends_on P08) | the prod multi-host path + the durable home of `[deployment] environment` (dstdns D-105 Q2's endgame) | touches `cli.py` (`_USAGE`, `_VERB_HELP`, verb chain) — the same surface their branch rewrites. Rebase onto post-merge main; their P04 documentation-contract test then binds your docs edits. |
| **R3** | `ciu-P11-host-scoped-secrets` | enrollment (Tailscale authkey / SSH bootstrap key per host) — needed for remote adoption, not for dstdns's dev cutover | same `cli.py` posture as R2. |

Not carved: **CIU-38** (per-service AppRole) — ruled consumer-side-first (dstdns D-106); it
stays in `KNOWN_ISSUES_TODO_BACKLOG.md` as the upstreaming ask.

## Hints from the carve reconnaissance (read before implementing)

- Every handoff carries exact `file:line` anchors measured at `0b920f80`; re-verify them
  against your actual base before editing — main moves.
- ciu has **no typed global-config model**; the `Profile` dataclass
  (`src/ciu/deploy_pkg/profiles.py:26-47`) is the precedent P10's `Layout` mirrors.
- `[deploy]` is already in the reserved-roots frozenset (`config_model.py:55-68`) — new keys
  under it need **no** frozenset change.
- The configfile-context `instance_id` (`composefile.py:569-576`) is a **per-service replica
  index**, not the workspace `INSTANCE_ID` — P08's docs must disambiguate (its O3).
- `DERIVE` and `ASK_VAULT_ONCE` are withdrawn directives; only six live kinds
  (`secrets/directives.py:25-32`). P11 accepts exactly two at host scope.
- The gate is **100% line AND branch** (`run-ciu-tests.py`, `--cov-fail-under=100`): no
  `# pragma: no cover` on changed code, no hollow assert-no-exception tests, no live
  Docker/SSH/Vault — fake seams only (precedent `tests/tests/test_ciu_deploy_actions.py:1348-1379`).

## Environment / gate

- Worktree: `git -C /workspaces/vbpub worktree add -b feat/ciu-p08-p09-config-plane .worktrees/ciu-p08-p09-config-plane main`
  (ciu is the `ciu/` subdirectory of the vbpub repo — work in `<worktree>/ciu`).
- Iterate: `cd <worktree>/ciu && python -m venv .venv && .venv/bin/pip install -e '.[test,ssh]' && .venv/bin/python run-ciu-tests.py`
  — this IS the coverage gate (2,076 tests, 100/100 at base).
- Hermetic proof (if the tester-unified image is available): the trove
  `[gates.tester-unified]` argv in `nyxloom-trove/nyxloom.toml`. If not available, say so
  explicitly in the LOG — never claim the hermetic run happened.
- One package per commit series; write `nyxloom-trove/reports/ciu-P0N-…-LOG.md` per its
  handoff; do NOT merge to main — the operator/controller merges after review.
- BLOCKED is mechanical: per each handoff's BLOCKED rule — write `BLOCKED: <reason>` to the
  LOG, commit, stop.

## After each release merges

Update `KNOWN_ISSUES_TODO_BACKLOG.md` rows (FIXED with evidence), `CHANGES.md`, and cut a
release via the cmru flow (`cmru.toml`) or hand back to the operator to cut it — dstdns pins
consumed ciu versions and needs a released artifact, not a branch.
