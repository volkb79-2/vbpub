# ciu implementation brief — worktree-automation + config-wave — 2026-08-19 (rev 4)

**Where you are:** the worktree `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`,
branch `docs/ciu-worktree-automation-backlog`, base `3639b18c` (= the branch's worktree-identity
checkpoint `71f5ec79` + its P04–P07 carves + a merge of main bringing the dstdns config-wave:
asks CIU-34..38, carves `ciu-P08..P11`, and the provenance ask renumbered CIU-28→CIU-39).
**All eight open packages live in `nyxloom-trove/handoffs/` of THIS worktree and are implemented
HERE, serially, on THIS branch.** Prior context: `ciu/_last-summary.txt` (the branch's own
checkpoint summary), `nyxloom-trove/roadmap.md`, `nyxloom-trove/decisions.md`.

Rev 1 of this brief assumed P08–P11 would run from main in parallel with this branch; that is
superseded — one lane, one branch, serial order below.

## Order of work → three merge-to-main checkpoints (= releases)

| checkpoint | packages (serial) | why this order |
|---|---|---|
| **A — first** | `ciu-P08-landscape-identity` → `ciu-P09-configfile-schema-validation` | Small; what dstdns's config-cutover consumes (P09 = render-time fail-fast for app configs; P08 = landscape_id validation). They touch `config_model.py`/`composefile.py`/docs only — **no overlap with P04–P07's `worktree.py`/`cli.py` surface**, so doing them first cannot disturb the carved anchors of the worktree packages. |
| **B** | `ciu-P04` → `ciu-P05` → `ciu-P06` → `ciu-P07` | The branch's own worktree-automation milestone, in its carved dependency order. P07 (assay qualification) closes this branch's CIU-28/CIU-29 (worktree identity/control — NOT the renumbered CIU-39 provenance ask). |
| **C** | `ciu-P10-deploy-layouts` (depends P08) → `ciu-P11-host-scoped-secrets` | Both touch `cli.py` (`_USAGE`, `_VERB_HELP`, verb chain) — the surface P04–P06 rewrite, so they go AFTER B. P10 carries `[deployment] environment` per layout (dstdns D-105 Q2's endgame). |

**Rev 4 correction (2026-08-19, after the checkpoint-A review):** checkpoint A is
**review-only** — ✅ DONE, see `nyxloom-trove/reports/checkpoint-A-review-2026-08-19.md`
(P08+P09 approved on-branch; hermetic gate 2092/0, 100.00%). Merging at A would have carried
the branch's not-yet-P07-qualified worktree code to main. **The first merge-to-main + release
happens after checkpoint B**, the second after C. The B merge must also clear the trove gate's
changed-line finding recorded in that review: 6 `pragma: no cover` lines in
`src/ciu/worktree.py` (`:225-226`, `:414-415`, `:668-669`, from `71f5ec79`) — test those arcs
inside B (worktree.py is B's scope) or make `--allow-excluded` an explicit reviewed argv
change. At each merge: review, `--no-ff`, update `KNOWN_ISSUES_TODO_BACKLOG.md` rows (FIXED
with evidence) + `CHANGES.md`, cut a release via the cmru flow — dstdns pins released
artifacts, not branches.

## Branch-specific deltas an implementer must know (measured at `3639b18c`)

- **P08 anchor drift:** this branch's worktree-overlay work moved `config_model.py` anchors:
  `_make_render_context` is now `:317` (handoff says :325-338), `render_global_chain` `:392`
  (handoff says :401-478); the reserved-roots frozenset is still at `:55`. The handoff's
  instruction stands: validate on the FINAL merged config — which on this branch **includes the
  `ciu.global.worktree.toml.j2` overlay**, so the landscape_id validation naturally covers
  overlay-set values too. Re-verify every anchor before editing; the handoffs command this.
- **P09:** `composefile.py` is untouched by this branch — anchors hold as written.
- **Numbering:** on THIS branch, CIU-28/CIU-29 mean worktree identity/control (P04–P07's
  subjects). The provenance defect assay filed is **CIU-39** (renumbered at the `3639b18c`
  merge). The dstdns asks are CIU-34..38. Do not "fix" these numbers back.
- `DERIVE` and `ASK_VAULT_ONCE` are withdrawn directives; six live kinds
  (`secrets/directives.py:25-32`). P11 accepts exactly two at host scope.
- The gate is **100% line AND branch** (`run-ciu-tests.py`, `--cov-fail-under=100`): no
  `# pragma: no cover` on changed code, no hollow assert-no-exception tests, no live
  Docker/SSH/Vault — fake seams only (precedent `tests/tests/test_ciu_deploy_actions.py:1348-1379`).
- ciu has **no typed global-config model**; the `Profile` dataclass
  (`src/ciu/deploy_pkg/profiles.py`) is the precedent P10's `Layout` mirrors. `[deploy]` is
  already reserved — new keys under it need no frozenset change.
- The configfile-context `instance_id` (`composefile.py`, render_configfiles context) is a
  **per-service replica index**, not the workspace `INSTANCE_ID` — P08's docs must
  disambiguate (its O3).

## Environment / gate — the evidence ladder (rev 3 clarification)

Two distinct levels; never conflate them in a LOG or REPORT:

1. **Iteration signal (implementer runs this, freely):**
   `cd /workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog/ciu && python -m venv .venv
   && .venv/bin/pip install -e '.[test,ssh]' && .venv/bin/python run-ciu-tests.py`
   — the same suite and the same 100% line+branch fail-under, but in a LOCAL venv whose
   dependency closure is NOT the gate's. A green here is a working signal only. Record it as
   "venv run", never as "the gate".
2. **Checkpoint evidence (run at review time, NOT by the implementer):** the trove
   `[gates.tester-unified]` argv (`nyxloom-trove/nyxloom.toml`) inside `tester-unified:local` —
   `run-ciu-tests.py` PLUS `nyxloom.coverage_gate` (changed-line floor). The operator/controller
   runs it once per checkpoint at merge review. Implementers do NOT hand-roll the docker
   invocation (detached-form + physical-path + cgroup subtleties; LESSONS L18) and do NOT
   start their own tester-unified container.
   **Scheduling:** tester-unified runs land in the shared `nyxloom-gates.slice`; while a cmru
   release/mutation campaign occupies it (CPU-heavy, hours), serialize — run the checkpoint
   gate after the campaign finishes rather than alongside it.
3. **Assay is not part of ciu's gate yet.** Moving ciu's gate to the released assay artifact
   IS package `ciu-P07` (checkpoint B). Until P07 merges, checkpoints use (2) as-is; after it,
   the assay-based gate replaces it.
- One package per commit series; write `nyxloom-trove/reports/ciu-P0N-…-LOG.md` per its
  handoff; commit on THIS branch; do NOT merge to main — the operator/controller merges at
  the checkpoints.
- BLOCKED is mechanical: per each handoff's BLOCKED rule — write `BLOCKED: <reason>` to the
  LOG, commit, stop. Scope.touch/forbid and oracles in each handoff are binding; product
  calls are controller decisions, never improvisations.
