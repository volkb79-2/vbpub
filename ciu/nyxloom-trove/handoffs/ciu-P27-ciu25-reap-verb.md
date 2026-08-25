---
schema_version: 1
id: ciu-P27-ciu25-reap-verb
project: ciu
component: worktree
title: "CIU-25 completion: ciu worktree reap [--json] [-y] [--category C] [--dry-run] — a closed seven-category survey of Docker-resource groups (owned/lease-expired/checkout-missing/orphaned/partial-cleanup/unattributable/ambiguous) built on ciu-P26's lease+label substrate, destroying exactly the categories that are Git-and-lease-provable, mirroring worktree branches' survey-then-prune shape"
tier: implement-4
input_revision: "13c039ac"
source: {kind: research, ref: "CIU-25 Docker-resource reap design, controller session 2026-08-25, grounded in KNOWN_ISSUES_TODO_BACKLOG.md#CIU-25 and worktree.py's shipped worktree branches (S16.8) as the structural precedent"}
stack: none
depends_on: [P26]
session: fresh
scope:
  touch:
    - "src/ciu/worktree.py"
    - "src/ciu/cli.py"
    - "tests/tests/test_ciu_worktree_reap.py"
    # Widened by the controller amendment below (Blocker 1): O4 mandates two
    # NEW capability identifiers, and this file pins WORKTREE_CAPABILITIES
    # with two literal-list assertions.
    - "tests/tests/test_ciu_worktree.py"
    - "tests/tests/test_ciu_cli_worktree.py"
    - "docs/SPEC.md"
    - "docs/README.md"
    - "docs/CONSUMERS.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P27-ciu25-reap-verb-LOG.md"
  forbid:
    - "src/ciu/composefile.py"
    - "src/ciu/config_model.py"
    - "src/ciu/provisioning.py"
    - "src/ciu/engine.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-closed-partition
    observable: "survey_reap_groups classifies EVERY resource group into exactly one of the seven closed categories (owned/lease-expired/checkout-missing/orphaned/partial-cleanup/unattributable/ambiguous) — never zero, never more than one. The 16 numbered test scenarios in 'Work' item 5 (#1,2,3,6,7,11,13,16) prove this: age/missing-process/basename-similarity never reclassify `owned` into something destructible; unlabeled resources land in `unattributable`; colliding project names land in `ambiguous`; a v1 record (no lease) still classifies cleanly; an inconsistent record surfaces as a finding, never an unhandled exception."
    negative: "a heuristic (age, basename, missing process) ever changing a classification; an inconsistent record aborting the whole survey instead of surfacing as its own finding; a group matching two categories' criteria simultaneously with no documented precedence rule"
    gate: "tester-unified"
  - id: O2-destructive-safety
    observable: "`-y` only acts on lease-expired/checkout-missing/orphaned/partial-cleanup by default; `unattributable`/`ambiguous` are NEVER acted on even when explicitly named via `--category` (that combination is a refusal, exit 2). Scenarios #4,5,6,7,8,9 in Work item 5 prove this per-category, including the shared-network-membership check (#9: reaping one instance never removes a network another owned instance still depends on)."
    negative: "any code path that removes an unattributable or ambiguous group's resources under any flag combination; removing a network still joined by another live, owned instance"
    gate: "tester-unified"
  - id: O3-transactional-isolation
    observable: "One group's destruction failure (e.g. a `volume rm` error) lands that group in `failed` with the real error text, every OTHER targeted group still gets processed, and the overall result is `status: \"partial\"` with a non-zero (1) CLI exit — scenario #10. A full-success pass returns `status: \"reaped\"`, exit 0. The post-pass document reflects a RE-SURVEY (post-state truth), not the pre-pass plan — scenario #12's byte-identical-modulo-timestamps property depends on this."
    negative: "one group's failure aborting the entire sweep; returning the pre-pass plan instead of re-surveying after destruction; exit 0 on a partial result"
    gate: "tester-unified"
  - id: O4-envelope-and-docs
    observable: "`REAP_SCHEMA_VERSION = 1`, `counts` keyed by all seven categories including zero-valued ones (scenario #11). `worktree.reap.v1` (and `worktree.lease.v1` if not already added by ciu-P26) appear in the capabilities allowlist. README/CONSUMERS/SPEC/CHANGES/KNOWN_ISSUES_TODO_BACKLOG.md all updated per the 'Work' item 6 docs list, with CIU-25 marked FIXED naming both this package and ciu-P26."
    negative: "an undocumented or unversioned JSON shape; CIU-25 marked FIXED without naming ciu-P26 as the co-requisite (a reader following only this package's evidence couldn't reconstruct the lease/label substrate it depends on)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "a resource group cannot be cleanly classified into exactly one of the seven categories by the facts available (record state, lease, label, checkout presence) — BLOCKED naming the ambiguous case; do not invent an eighth category or force a bad classification"
  - "_clean_in cannot be reused for the checkout-exists case without a change to a forbidden file — BLOCKED naming the exact incompatibility"
mutexes: [merge-lane]
review_focus:
  - "the seven-category partition is genuinely closed and non-overlapping under adversarial fixture construction, not just the happy-path scenarios named in the handoff"
  - "unattributable/ambiguous are provably never destroyed under any flag combination, including a deliberately adversarial --category argument"
  - "the shared-network check (scenario #9) is real — construct two genuinely joined instances and confirm the network survives reaping one"
---

# ciu-P27 — CIU-25 completion: `ciu worktree reap`

## Amendment (controller, before implementation)

The implementer stopped BEFORE writing code with two findings. Both are
ruled on here; the frontmatter and the category table above already carry
the corrections.

**Blocker 1 — O4's capability identifiers vs. an out-of-scope test.
APPROVED, scope widened.** O4 mandates `worktree.reap.v1` and
`worktree.lease.v1` in `WORKTREE_CAPABILITIES`. Verified against the shipped
code (not this handoff's citation of it): ciu-P26 added NEITHER — its LOG
§4.9 records the deliberate deferral. Two assertions in
`tests/tests/test_ciu_worktree.py`
(`test_capabilities_document_is_versioned_and_closed` ~919 and
`test_capabilities_advertise_exactly_the_shipped_contracts` ~933) pin the
tuple as a literal list. That file is now in `scope.touch`; update both
assertions. P26's deferral was justified only because P26 shipped no new
machine contract; this package ships two, and a shipped contract that is not
advertised is precisely the hole D-009's allowlist exists to close.
`test_ciu_documentation_contract.py`'s `CLOSED_PUBLIC_VALUES` is a
required-SUBSET check and needs no change.

*(Implementation note, same ruling: a THIRD file,
`tests/tests/test_ciu_cli_worktree.py`'s
`TestCapabilitiesDispatch::test_capabilities_json_dispatch` ~353, pins the
same list as a bare literal without referencing `WORKTREE_CAPABILITIES`, so
the pre-implementation grep for the symbol did not find it. It is the
identical edit under the identical ruling and joins `scope.touch` too. The
authoritative sweep for this class of pin is `grep -rn
"worktree.branches.v1"`, not a grep for the constant's name.)*

**Blocker 2 — `partial-cleanup` as carved was undecidable AND had a
catastrophic false positive. APPROVED, narrowed to the record's own declared
state.** The original clause "a resource group with some (not all) of its
containers/volumes/network still present" cannot be evaluated from provable
facts, because **nothing anywhere records what "all" should be** for a given
group: a stack that legitimately declares no volumes, and an instance that
ran `ciu env generate` but never `ciu up`, both read as "some, not all". The
criterion therefore decides by guessing at an expectation that was never
written down — the exact forbidden-workaround shape this handoff's own
BLOCKED rule names. Worse, it is destructive-by-default: `ciu down`
deliberately PRESERVES volumes (`cli.py`'s `down` help — "volumes are
preserved (use `ciu clean` to remove them)"), so any owned, valid-leased
instance left in a volumes-only state by a hand-run `docker compose down`, a
`docker system prune` or a reboot over `--rm` containers would classify
`partial-cleanup`, sit in the default `-y` set, and lose its data. The third
clause, "a previously-failed reap", is not persisted anywhere and is equally
unobservable.

Narrowed to: **`partial-cleanup` == the attributed record declares
`state: "recovery-required"`** (any of the closed
`WORKTREE_RECOVERY_STATUSES`). CIU itself wrote that state down when a
lifecycle step failed, so it is DECLARED, not inferred — the same discipline
that makes the lease, and not an age heuristic, the authority on staleness
everywhere else in S16.9. Everything the withdrawn clauses meant to catch is
already covered by `checkout-missing` / `lease-expired` / `orphaned`.
Work item 5's scenario #8 is still required; its fixture's record carries
`state: "recovery-required"`.

Documented here for the same reason ciu-P24's migration-safety refusal was:
a later reader must be able to see that the shipped category is NARROWER
than the carve on purpose, and why, rather than reading it as an
implementation shortfall.

## Context to read first

1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-25` and `nyxloom-trove/reports/ciu-P26-ciu25-lease-schema-and-labels-LOG.md` (P26's actual shipped shape — re-verify its real function/field names against the LOG and the live diff, do not assume this handoff's citations of P26 are still exactly right).
2. `src/ciu/worktree.py`: `branch_hygiene` + `BRANCH_CATEGORIES` + `prune_branches` (~854-1136) — READ IN FULL. This is your structural and stylistic template: survey function returns a closed-category document; a SEPARATE destructive pass, gated on `-y`, acts on exactly the provable-safe categories; a post-action re-survey proves the returned document is post-state truth (~1126-1131); the capability id convention (`worktree.branches.v1`, ~696).
3. `list_instance_records` (~629-661) — note it RAISES on any inconsistency (branch mismatch, duplicate logical name, offset mismatch, ~640-659). A survey that dies on one bad record is useless exactly when you need it most. You need a non-raising sibling that collects inconsistencies as findings instead — name it, document why it exists, and make `reap` use it instead of the raising version.
4. `_runtime_identity` (~1559-1573) — the exact-path `ciu.env` read per checkout (never ambient) — same discipline P26 established for labels.
5. `worktree rm`'s existing single-target teardown, specifically `_clean_in` (~549-588) — REUSE this for any group whose checkout still exists and is readable; do not hand-roll a parallel teardown path for that case.
6. `_network_container_ids` (~2018-2030, the exact `--no-trunc` discipline — the truncated-ID trap is a recorded estate lesson, do not reintroduce it) and `connect_shared_infra_after_up` (~2082) — the shared-network-membership check a reap must honor before ever removing a network.
7. `worktree.py`'s capability allowlist (~691-703) — where you add `worktree.reap.v1` and `worktree.lease.v1` (the latter only if ciu-P26 didn't already add it — check).

## Category vocabulary (closed, exactly these seven)

| Category | Meaning | Does `-y` act on it? |
|---|---|---|
| `owned` | live record, state `ready`, lease held/perpetual/not-configured | never |
| `lease-expired` | live record, `expires_at_utc` in the past at survey time | yes |
| `checkout-missing` | record/registration known but the checkout directory is gone (`ciu clean` can never run there) | yes (Docker-side removal only; git half is `worktree branches`' job, name it in the doc as the remedy) |
| `orphaned` | resources carry `ciu.instance=<id>` (P26's label) for an id matching NO known record and NO known worktree | yes |
| `partial-cleanup` | **CORRECTED (see Amendment, Blocker 2):** the attributed record's own declared `state == "recovery-required"`. The originally-carved "some (not all) resources present, OR a previously-failed reap" clauses are WITHDRAWN as undecidable and unsafe | yes |
| `unattributable` | no `ciu.instance` label AND no identity-form compose-project-name match to any known instance | **never**, even with `-y` |
| `ambiguous` | the resolved project name/prefix matches MORE THAN ONE live record (the historical CIU-19 shape — see `engine.py:813-823`, read-only) | **never** |

## Work

1. **Survey.** A new `survey_reap_groups(repo_root) -> ReapDocument` in `worktree.py`: enumerate every Docker resource GROUP (one compose project + its volumes + its per-instance network) via `docker ps -a --no-trunc --format '{{json .}}'` plus `docker volume ls --filter label=com.docker.compose.project=<p>` plus the per-instance network name convention (`{repo_name}-{instance_id}-network`), cross-referenced against the non-raising sibling of `list_instance_records` (item 3 above) and `list_worktrees`. Classify each group into EXACTLY ONE of the seven categories above — this is a closed partition, every group lands in exactly one bucket, never zero, never more than one (a review will construct a case designed to be ambiguous between two categories and check your precedence rule is documented). Emit a versioned JSON envelope: `REAP_SCHEMA_VERSION = 1`, `operation: "reap"`, `status: "survey"`, per-group category + identifying facts (compose project, instance id if known, container/volume/network names, lease state if a record exists), and `counts` keyed by ALL SEVEN categories including zero counts (mirror `worktree branches`' `counts` shape exactly, ~1045).
2. **CLI, survey-only by default.** `ciu worktree reap [--json]` with NO `-y` runs ONLY the survey (zero side effects — same contract as `worktree branches` without `-y`, ~1075-1078): human output groups by category with counts, then a `hint` naming the remedy for non-empty destructible categories. `--category C[,C...]` restricts which categories a `-y` pass may act on; the DEFAULT category set for `-y` is `orphaned,lease-expired,checkout-missing,partial-cleanup` (never unattributable/ambiguous by default). Passing `--category unattributable` or `--category ambiguous` explicitly is a REFUSAL (exit 2, naming why) — there is no way to force-reap these two, ever, from this verb. `--dry-run` combined with `-y` prints the exact remediation commands (the `ciu clean` invocation or the specific `docker ...` argv) WITHOUT executing them.
3. **Destructive pass, transactional per group.** For each group in an active category: (a) if its checkout directory exists and its `ciu.env` is readable, delegate to `_clean_in(ciu_root, yes=True)` (item 5 above) — this is authoritative and handles volumes/hostdirs/root-helper paths already; record `reaped` on success, `failed` with the reason on failure, and CONTINUE to the next group (one failure never aborts the sweep). (b) only when the checkout is missing/unreadable, remove Docker resources directly, scoped by BOTH the `ciu.instance` label AND the compose project name, in strict order containers -> volumes -> network. (c) before removing any network, check `_network_container_ids` for OTHER live containers still joined (a shared-infra join, item 6) — if any exist that belong to a DIFFERENT still-owned instance, skip the network removal and name why in the result; do not tear down a network another owned instance depends on. (d) after the full pass, RE-RUN the survey and return the post-state document (mirror `worktree branches`' `-y` proving post-state truth, ~1126-1131) — `status: "reaped"` when every targeted group succeeded, else `status: "partial"` (and the CLI exits 1, not 0, on partial).
4. **`ciu worktree lease`** — implement here ONLY IF ciu-P26 did not already ship it in full (check P26's LOG first); if P26 shipped it, this package does not touch it again.
5. **Tests** — the 16 behavioral oracles below are REQUIRED, each as an independent test using fake `docker`/subprocess seams (no live Docker, per this codebase's existing test convention — grep an existing `worktree.py` test file for the fake-docker fixture style and mirror it):
   1. Age alone never reaps (`lease: null`, old containers -> `owned`, `-y` removes nothing).
   2. Missing process never reaps (no CIU process running anywhere; valid-lease `ready` record -> `owned`).
   3. Basename similarity never cross-reaps (two worktrees, same directory basename, DIFFERENT instance ids -> reaping one leaves the other's containers/volumes/network fully intact — this is the CIU-19 regression shape, `engine.py:813-823`, read-only reference).
   4. Expired lease -> `lease-expired`; `-y` invokes `_clean_in` with THAT exact `ciu_root` when the checkout exists.
   5. Checkout missing -> `checkout-missing`; `-y` removes exactly the labeled Docker resources, leaves git registration untouched, document names the git-half remedy (`worktree branches`).
   6. Unlabeled, no identity match -> `unattributable`; `-y` removes nothing, exit 0, hint carries a copy-pasteable manual docker command; `--category unattributable` exits 2.
   7. Colliding project name across two live records -> `ambiguous`; `-y` removes nothing; hint names both colliding records.
   8. Partial cleanup (containers gone, volumes remain, record present **and in `state: "recovery-required"`** — see Amendment/Blocker 2) -> `partial-cleanup`; `-y` removes the volumes; re-survey shows the group gone.
   9. Shared network: instance A reaped while instance B is still joined to the same network -> A's containers removed, network NOT removed, result names why.
   10. Failure isolation: one `volume rm` fails -> that group lands in `failed` with the real error, every OTHER group still gets reaped, overall `status: "partial"`, exit 1.
   11. Envelope contract: `schema_version==1`; every group's category is one of the closed seven; `counts` has all seven keys including zero-valued ones.
   12. Survey purity: two consecutive `reap --json` calls with no `-y` produce byte-identical output modulo timestamps, and the underlying (faked) Docker state is unchanged.
   13. A schema-v1 worktree-instance record (no lease field, per ciu-P26) surveys as `owned` with lease treated as null, and is not rewritten by the survey.
   14. Lease lifecycle integration: `--extend`/`--perpetual`/`--release` (via ciu-P26's verb, or this package's if P26 didn't ship it) each change the SAME group's next-survey category exactly as expected (perpetual/extended -> `owned`, expired -> `lease-expired`, released with no TTL configured -> `owned`, never `lease-expired`).
   15. Clock discipline: expiry is evaluated against a timezone-aware UTC clock; inject the clock, never call the real one in a test; a naive stored timestamp is a refusal at read time (per ciu-P26's O1), not something this package needs to re-validate but should not crash on if it somehow occurs.
   16. An inconsistent record (branch mismatch, etc.) surfaces in the survey as a per-group finding/category, never raises and aborts the whole survey.
6. **Docs.** README.md's worktree bullet gains `reap` to its verb list. docs/CONSUMERS.md gets one worked example (a stale worktree from a crashed dispatcher, surveyed then reaped). docs/SPEC.md documents the full closed category vocabulary, the CLI surface, and the transactional reap-order discipline (extend S16, name the exact subsection in the LOG). `KNOWN_ISSUES_TODO_BACKLOG.md`'s CIU-25 row -> FIXED, with both this package and ciu-P26 named as the evidence. CHANGES.md Unreleased entry.

## Environment setup

```bash
cd /workspaces/vbpub/.worktrees/ciu-qol-v8prep-wave/ciu
.venv/bin/python run-ciu-tests.py
```

No live Docker anywhere in tests — fake seams only.

## BLOCKED rule

If a named contract (especially: the seven-category partition being genuinely closed and non-overlapping, or the transactional per-group failure isolation) cannot be met as specified, or scope requires a forbidden file, STOP: write `BLOCKED: <reason>` to
`nyxloom-trove/reports/ciu-P27-ciu25-reap-verb-LOG.md`, commit, exit. Forbidden workaround: inferring staleness from age, basename similarity, or absence of a running process anywhere in the classification logic — every category above must be provable from the record/label/lease facts, never inferred from a heuristic. See frontmatter `escalate_if` for the two specific triggers most likely to fire.
