# ciu-P27 — CIU-25 completion: `ciu worktree reap`

**Handoff:** `nyxloom-trove/handoffs/ciu-P27-ciu25-reap-verb.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `32f3a0f9` (CIU-59
filing on top of ciu-P26), confirmed with `git status --porcelain && git log
--oneline -3` before any edit — tree was clean.

**Status: COMPLETE.** All four oracles met; **3206 tests pass; coverage is
100.00% line+branch** under the real gate and under a serial run. Two
BLOCKERs were raised BEFORE any code was written and ruled on by the
controller; both rulings are recorded in the handoff's own Amendment section
and implemented as ruled. No `scope.forbid` file touched. One further scope
question (a third file pinning the same capability literal, and a `docs/`
vs repo-root `README.md` mis-citation) is recorded in §3 — both are the same
class as the ruling already given.

**The single most important fact for review, stated once, up front:**

> With `-y`, this verb destroys resource groups in exactly four categories —
> `lease-expired`, `checkout-missing`, `orphaned`, `partial-cleanup` — and
> nothing else. `owned`, `unattributable` and `ambiguous` are not merely
> off-by-default: `--category` REFUSES their names (exit 2) instead of
> selecting them, so no flag combination in the CLI reaches them. §5 gives
> the proof required for each of the four.

---

## 1. Reading, before any code

The handoff in full, then, in the order it names them:
`KNOWN_ISSUES_TODO_BACKLOG.md#CIU-25` (the five demanded states, the "must
not destroy on age / basename / missing process" constraint, and the ciu-P28
hotfix lesson that binds any successor);
`nyxloom-trove/reports/ciu-P26-ciu25-lease-schema-and-labels-LOG.md` **and
the live shipped code it describes**, re-verified rather than assumed —
`WorktreeLease`/`_lease_from_dict`/`_parse_utc_timestamp` (204–264),
`_record_from_dict`'s schema-dependent key set (267), `to_dict`'s conditional
`lease` (190), `acquire_lease`/`make_lease_perpetual`/`release_lease`
(406–468), `apply_lease` (1505), and on the engine side
`OWNERSHIP_LABEL_INSTANCE`/`OWNERSHIP_LABEL_REPO_ROOT` +
`workspace_ownership_labels` + `_labelable_top_level` +
`write_ownership_overlay` (`engine.py` 1155–1253);
`branch_hygiene`/`BRANCH_CATEGORIES`/`prune_branches` in full (1569–1933) as
the structural template; `list_instance_records` (1288) and its four raising
cross-checks; `_runtime_identity` (2356); `_clean_in` (1208) and `remove()`
(2782) for how it is actually called; `_network_container_ids` (2844) and
`connect_shared_infra_after_up` (2908); `WORKTREE_CAPABILITIES` (1354);
`engine.identity_compose_project_name` (928) and `compose_project_name` (906)
for the two project-naming schemes; `workspace_env._compute_network_name`
(619) for the identity-network convention; `cli._worktree`'s inline-argparse
dispatch; and the fake-Docker fixture style in
`test_ciu_worktree_shared_infra.py` (`ScriptedDocker`, ~112).

Three of the handoff's own citations of P26 were checked and found **wrong**,
exactly as the handoff warned they might be — see §2 and §3.1.

## 2. The two BLOCKERs raised before writing code

Both were reported to the controller before a line of implementation, and
both were approved as proposed. The handoff's frontmatter, category table and
a new Amendment section carry the rulings (commit 1).

### 2.1 O4's capability identifiers vs. a pinned out-of-scope test — APPROVED (widen scope)

O4 mandates `worktree.reap.v1` and `worktree.lease.v1` in the allowlist. The
handoff hedges "`worktree.lease.v1` … only if ciu-P26 didn't already add it —
check". **It did not add either**: P26's LOG §4.9 records the deliberate
deferral, and `WORKTREE_CAPABILITIES` is untouched since before P26. Two
assertions in `tests/tests/test_ciu_worktree.py` (919, 933) pin the tuple as
a bare literal. Ruled: widen `scope.touch`, because P26's deferral was
justified only by shipping no new machine contract and this package ships
two.

### 2.2 `partial-cleanup` as carved was undecidable AND unsafe — APPROVED (narrow)

Carved as *"record in `recovery-required`, OR a resource group with some (not
all) of its containers/volumes/network still present, OR a previously-failed
reap"*, and placed in the **default `-y` set**. The middle clause cannot be
implemented from provable facts and would destroy live data:

1. **Nothing anywhere records what "all" would be** for a group. A stack that
   legitimately declares no volumes has containers+network and no volumes; an
   instance that ran `ciu env generate` but never `ciu up` has only the
   network. Both read as "some, not all". The criterion decides by guessing
   at an expectation that was never written down — the exact forbidden-
   workaround shape the handoff's own BLOCKED rule names.
2. **`ciu down` preserves volumes on purpose** (`cli.py`'s `down` help:
   "Stop project containers; volumes are preserved (use `ciu clean` to remove
   them)"). Any owned, valid-leased instance left volumes-only by a hand-run
   `docker compose down`, a `docker system prune`, or a reboot over `--rm`
   containers would have classified `partial-cleanup`, sat in the default
   `-y` set, and lost its data.
3. **"a previously-failed reap" is not persisted anywhere**, so it is not
   observable at all.

Shipped criterion: **the attributed record DECLARES
`state: "recovery-required"`** — CIU itself wrote that down when a lifecycle
step failed. Declared, not inferred, which is the same discipline that makes
the lease rather than an age heuristic the authority on staleness everywhere
else in S16.9. Everything the withdrawn clauses meant to catch is already
covered by `checkout-missing`/`lease-expired`/`orphaned`.

`test_a_volumes_only_owned_instance_is_never_partial_cleanup` is the direct
negative: containers gone, two volumes standing, lease valid → `owned`,
survives `-y` with both volumes intact.

## 3. Three further findings the reviewer should check

### 3.1 `checkout-missing` as specified was UNREACHABLE — redefined from the second label

The handoff (and my own first implementation) defines it as *"a readable
record whose checkout directory is gone"*. **That state cannot exist.** The
instance record lives at `ciu_root/ciu.worktree-instance.json` — INSIDE the
checkout — and `survey_instance_records` reads records only from directories
`git worktree list` registers. A vanished checkout takes its record with it,
so `record.ciu_root.is_dir()` is False for no record we can ever hold. I
found this because the branch was dead under the 100%-branch gate, not by
inspection; it would otherwise have shipped as a category that silently never
fires.

Redefined from the one durable, checkout-EXTERNAL piece of evidence P26
ships: **`ciu.repo-root`** (`PHYSICAL_REPO_ROOT`, stamped on every managed
resource). A group is `checkout-missing` when it is unclaimed (as `orphaned`)
**and** its own `ciu.repo-root` label names a directory that no longer
exists.

This is **safety-neutral by construction**, which is why I resolved it rather
than escalating a third time: a group reaches either test only after no
record and no registered checkout claims its id — that is what licenses
removal — so the label only decides which of two already-destructible
messages the operator reads. It also means the known DooD caveat (in a
devcontainer, `ciu.repo-root` is a HOST path this container may not see, so
`is_dir()` can be False for a path that exists on the host) cannot cause a
wrong destruction. Stated plainly in SPEC S16.10.

Consequence, also documented: the **identity network** of an instance whose
checkout AND record are both gone carries no label at all (it is created by
`ciu env generate`, outside compose, and declared `external: true`, so
neither compose nor P26's fragment labels it) and is therefore out of reap's
reach by design. Remove it by hand.

### 3.2 A third file pinned the same capability literal

`tests/tests/test_ciu_cli_worktree.py::TestCapabilitiesDispatch::test_capabilities_json_dispatch`
(~353) hardcodes the same list **without referencing
`WORKTREE_CAPABILITIES`**, so the pre-implementation grep for the symbol did
not find it. It is the identical edit under the identical, already-given
ruling, so I applied it and recorded it in the handoff Amendment rather than
stopping a second time for the same decision. **The authoritative sweep for
this class of pin is `grep -rn "worktree.branches.v1"`, not a grep for the
constant's name** — worth carrying forward.

### 3.3 `scope.touch` names `docs/README.md`; the verb list is in the repo-root `README.md`

`docs/README.md` is a pure document index and contains the string "worktree"
zero times. Work item 6's "README.md's worktree bullet gains `reap` to its
verb list" can only mean the repo-root `README.md:11`, which is where that
bullet lives. I edited the repo-root file and left `docs/README.md`
untouched (adding a verb mention to an index would be noise). Flagging it as
a scope-citation correction rather than a silent substitution.

## 4. Design decisions worth reviewing

### 4.1 The partition is ONE ordered chain, deliberately

`_classify_reap_group` is a single function of early returns rather than a
table of predicates, so the precedence is readable top-to-bottom and a group
structurally cannot land in two buckets. The order encodes two policies:

1. **Every un-provable attribution resolves to a never-destroyed category
   BEFORE any destructible one is considered** — `ambiguous` (several
   identities, several records, or a Git-contradicted record) and
   `unattributable` (no evidence at all) are tested first.
2. Among the destructible ones the most operationally specific fact wins.

Full chain: `len(ids) > 1` → ambiguous; `not ids` → unattributable;
duplicated record id → ambiguous; distrusted (Git-contradicted) id →
ambiguous; no record but a registered checkout's own `ciu.env` declares the
id → **owned**; no record, no checkout, repo-root label gone →
checkout-missing; no record, no checkout → orphaned; record declares
`recovery-required` → partial-cleanup; record's held lease expired →
lease-expired; else → owned.

`test_every_group_lands_in_exactly_one_of_the_seven` builds one group per
category simultaneously and asserts the mapping is a bijection plus
`sum(counts.values()) == len(groups)`.

### 4.2 "owned via a registered checkout's own ciu.env" — the rule that keeps the partition closed

The handoff defines `orphaned` as "no known record AND no known worktree",
which forces the "labelled id matching a registered checkout that has no
record" case to land somewhere else; the vocabulary offers only never-
destroyed homes for it. `owned` is the right one, and the attribution is a
FACT, not a heuristic: `git worktree list` registers the checkout and that
checkout's own `ciu.env` — read by exact path, never ambient (CIU-41) —
declares the `INSTANCE_ID`.

This is load-bearing in two places beyond its own test: it is why a
**corrupted instance record on a live instance** yields `owned` rather than
`orphaned` (`test_unreadable_record_is_a_finding_and_the_checkout_still_owns`
— the single worst false positive this module could produce), and it is why
the **PRIMARY checkout's** config-less identity-form projects are `owned`
rather than reapable, even though P26 deliberately never labels them.

### 4.3 The identity-completeness interlock

`_classify_reap_group` stays pure; the interlock lives in the DESTRUCTIVE
pass, mirroring `_prune_base_sanity`'s shape. A registered checkout carrying
a record FILE from which no identity can be derived — neither from the record
nor from `ciu.env` — sets `identity_complete: false`, and while that holds
`orphaned` targets are refused by name. An id that looks unclaimed may simply
be the one that could not be read.

Two deliberate details: the refusal is **loud** (that group lands in
`failed`, status `partial`, exit 1) rather than a silent skip, because not
doing something the operator asked for must be visible; and it disarms
**only** `orphaned`, because the record-backed categories never depended on
the "nothing claims this id" premise. `TestIdentityInterlock` pins all three
properties including `test_the_interlock_only_disarms_orphaned`.

The interlock is scoped narrowly on purpose: a checkout with no record file
at all cannot have stamped ownership labels (`workspace_ownership_labels`
returns `None` without one), so it can never be the hidden owner of a
labelled group and does not trip it.

### 4.4 `--category` REFUSES rather than filters

The safety property is stated as code: `resolve_reap_categories` accepts only
members of `REAP_DESTRUCTIBLE_CATEGORIES` and refuses everything else,
including the three real-but-protected categories, with a message that says
*why* ("`ambiguous` means exactly that no such proof exists"). So
`--category ambiguous` fails the command rather than selecting a category —
there is no code path anywhere that can reach a protected group.

It is also validated **before** the survey runs, so a refusal cannot be
preempted by an unrelated Docker error producing a different exit code.
`test_ambiguous_is_never_reaped_and_cannot_be_selected` includes the
adversarial `--category orphaned,ambiguous` and `ambiguous,orphaned`: mixing
a legal name with an illegal one does not smuggle the illegal one through.

### 4.5 Labels are read with explicit `{{.Label "k"}}` lookups, never the `{{.Labels}}` blob

`docker ps --format '{{.Labels}}'` returns comma-joined `k=v` pairs; a label
VALUE containing a comma splits that blob wrong, and a mis-parsed
`ciu.instance` is a mis-attribution — the one error class this whole module
exists to prevent. Each field is pulled individually, tab-separated, and a
row with the wrong field count is a refusal rather than a best-effort parse.

`--no-trunc` is on the `ps` enumeration and the recorded estate lesson behind
it is honored end to end: the container IDs the survey captures are full
64-char IDs, and they are compared against `_network_container_ids`'
`docker network inspect` keys (also full) in the network guard. Removal is
by those exact IDs, which is strictly narrower than re-running a filter at
destroy time and immune to name reuse.

### 4.6 docker ABSENT is empty; docker PRESENT-and-FAILING is a refusal

A CIU workspace can legitimately be local-only, so `FileNotFoundError`/
`OSError` from an enumeration yields no resources — an empty survey destroys
nothing, which is the honest answer. A non-zero return from a daemon that IS
there is a `WorktreeError`: a survey that silently under-reports is the input
to a destructive pass, and the group it failed to see is exactly the one
whose absence would make a shared network look unused. During a REMOVAL the
distinction disappears — both become a reported failure with the real error
text (`test_the_daemon_dying_mid_pass_is_a_failure_not_a_crash`).

### 4.7 Network removal is last, and doubly guarded

Guard 1 is the S16.1 case the handoff names: any container still joined that
this pass did not just remove. I made it **stricter** than "belongs to a
different still-owned instance" — ANY remaining member blocks — because it
subsumes the required property and is provable without a second attribution
pass over the joiners.

Guard 2 is mine: one workspace's several stacks share ONE identity network
(S2.6), so a group is a blocker for that network until it has itself been
disposed of, and an UNTARGETED group never leaves the pending map at all.
The last one out turns off the light
(`test_a_sibling_stack_of_the_same_identity_blocks_the_shared_network`).

A network a concurrent operator already removed is a no-op, not a failure —
checked with the existing `_docker_network_exists` rather than by letting
`_network_container_ids` raise on a missing network.

### 4.8 `_clean_in` is authoritative and is never second-guessed

Reuse per the handoff's item 5, gated on `_reap_uses_clean` (the checkout
exists and its `ciu.env` is readable). A clean that returns non-zero puts the
group in `failed` and **stops**; CIU does not fall back to a bare docker
removal, because `clean` knows about `vol-*` hostdirs and the privileged
removal helper and a direct `docker rm` does not.
`test_a_failing_clean_never_falls_back_to_a_bare_docker_removal` asserts the
negative directly (`not any(c[:2] == ["rm", "-f"] for c in docker.calls)`).

The direct path is reached only when there is no checkout left to run a clean
in — which is the ciu-P28 lesson satisfied structurally rather than by
convention.

### 4.9 `operation` stays `"reap"` across all four statuses

`branches` uses `branches` → `branches-prune`; this document keeps
`operation: "reap"` because the handoff's item 1 specifies it literally and
the four-member `status` vocabulary
(`survey`/`dry-run`/`reaped`/`partial`) already carries the distinction. The
CLI therefore branches on `status`, and the exit-code decision is hoisted
above the output-format branch exactly as ciu-P28 hoisted the `branches` one
— duplicating it into both arms is what let `--json` disagree with the human
output last time.

### 4.10 The document carries no timestamp of its own

Which turns scenario #12's "byte-identical modulo timestamps" into plain
byte-identical, and makes the purity assertion strictly stronger.

## 5. What `-y` destroys, and the proof for each

| Category | The proof required | How it is destroyed |
|---|---|---|
| `lease-expired` | a **readable instance record** whose `held` lease carries an `expires_at_utc` that has passed, evaluated against an INJECTED tz-aware UTC clock. `perpetual` never lapses; `lease: null` and a schema-v1 record with no lease are NOT expired; an unparseable stored expiry is NOT expired | `ciu clean -y` inside the checkout when it can still run there; direct removal otherwise |
| `checkout-missing` | the group is unclaimed by every record and every registered checkout, **and** its own `ciu.repo-root` label names a directory that does not exist | direct removal (there is no checkout to clean in) |
| `orphaned` | `ciu.instance=<id>` matching **no** instance record and **no** registered checkout's own `ciu.env` identity — and the survey is `identity_complete` (§4.3) | direct removal |
| `partial-cleanup` | the attributed record's own **declared** `state: "recovery-required"` | `ciu clean -y` when possible; direct removal otherwise |

Never destroyed, under any flag combination: **`owned`**, **`unattributable`**,
**`ambiguous`** — and the latter two cannot even be named on the command line.

Never consulted, anywhere in the module: resource age, record `created_at_utc`,
directory basename similarity, container run state, or whether any process is
running. Proven by three dedicated negatives (#1, #2, #3).

## 6. Files changed

| File | What |
|---|---|
| `src/ciu/worktree.py` | New S16.10 section (~640 lines): `REAP_SCHEMA_VERSION`, `REAP_CATEGORIES`, `REAP_DESTRUCTIBLE_CATEGORIES`, `REAP_STATUSES`; `_ownership_label_keys` (lazy engine import — engine imports this module), `_reap_docker_rows`, `ReapIdentities`, `_reap_record_inconsistencies`, **`survey_instance_records`** (the non-raising sibling), `_lease_is_expired`, `_classify_reap_group`, `_reap_group_documents`, `_reap_hint`, **`survey_reap_groups`**, `resolve_reap_categories`, `_reap_plan`, `_reap_uses_clean`, `_docker_reap`, `_reap_network`, `_reap_one_group`, **`reap_groups`**. Plus `worktree.lease.v1`/`worktree.reap.v1` in `WORKTREE_CAPABILITIES` |
| `src/ciu/cli.py` | `reap` subparser (`-y`, `--category`, `--dry-run`, `--json`, `--define-root`), the dispatch arm with the hoisted exit-code decision and human rendering (per-category groups, findings, plan/reaped/failed, hint), `_USAGE` + `_VERB_HELP["worktree"]` |
| `tests/tests/test_ciu_worktree_reap.py` | NEW, **79 items** — `FakeDocker`, a STATEFUL host model whose removals actually mutate it (so "the other instance's volumes are still there" is a post-state assertion, not an argv one), plus real `git worktree add` fixtures |
| `tests/tests/test_ciu_worktree.py` | the two pinned capability literals (§2.1) |
| `tests/tests/test_ciu_cli_worktree.py` | the third pinned capability literal (§3.2) |
| `docs/SPEC.md` | New **S16.10** (category table + precedence, both narrowings and why, the CLI surface, the transaction and its ordering, both network guards, the interlock, document/exit codes); S16.5's shipped-identifier list; S16.9's "Still open → detection and destruction" now points at S16.10 |
| `docs/CONSUMERS.md` | New §5b-2 — a worked example (a crashed dispatcher's residue, surveyed then reaped), the guarantees stated as what CANNOT happen, the `--json` shape, and the lease verbs as the supported way to declare a long-lived instance |
| `README.md` | the worktree verb bullet (§3.3) |
| `CHANGES.md` | Unreleased `feat(ciu)!:` entry with an explicit **UPGRADE NOTE** |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-25 row → **FIXED**, naming ciu-P26 AND ciu-P27 as jointly the evidence; detail section rewritten with the five-demanded-states → seven-shipped-categories mapping, both narrowings, and the remaining named gaps |
| `nyxloom-trove/handoffs/ciu-P27-ciu25-reap-verb.md` | the controller Amendment (commit 1) |

`scope.forbid` verified empty before writing code and again before commit:

```
$ git diff --stat -- src/ciu/composefile.py src/ciu/config_model.py \
    src/ciu/provisioning.py src/ciu/engine.py nyxloom-trove/backlog.md \
    nyxloom-trove/decisions.md nyxloom-trove/roadmap.md
(empty)
```

`engine.py` is FORBIDDEN and is imported read-only for its two label-key
constants — the closed vocabulary stays owned by one module, with no second
spelling to drift.

## 7. Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** closed partition | **MET** | `TestClosedPartition` ×20. The three required negatives are explicit: `test_age_alone_never_reaps` (a year-old lease-less v1 record → `owned`, and `-y` leaves the host byte-for-byte identical), `test_missing_process_never_reaps`, `test_basename_similarity_never_cross_reaps` (two checkouts with the IDENTICAL basename and one-character-apart ids; reaping the lapsed one leaves every container, volume and network of the other intact). Vocabulary: `test_unlabelled_with_no_identity_match_is_unattributable`, `test_colliding_project_name_is_ambiguous`, `test_duplicate_instance_id_across_records_is_ambiguous`, `test_v1_record_surveys_as_owned_and_is_never_rewritten` (byte comparison of the record after the survey), `test_released_v2_lease_is_owned_not_expired`, `test_perpetual_lease_never_expires` (10 000 days on), `test_labelled_id_of_a_recordless_registered_checkout_is_owned`. Findings, not crashes: `test_inconsistent_record_is_a_finding_and_never_licenses_destruction` (asserts `list_instance_records` RAISES on the same fixture), `test_every_cross_check_list_instance_records_raises_on_is_a_finding` (all four checks, two from one record), `test_unreadable_record_is_a_finding_and_the_checkout_still_owns`, `test_an_inconsistent_record_with_no_instance_id_distrusts_nothing`. Closure: `test_every_group_lands_in_exactly_one_of_the_seven` (one group per category at once, bijection asserted), `test_envelope_is_versioned_and_counts_carry_all_seven`, plus the not-surveyed-at-all boundaries |
| **O2** destructive safety | **MET** | `TestDestructiveSafety` ×15. Per category: `test_expired_lease_delegates_to_clean_in_with_that_exact_ciu_root` (asserts `calls == [(root, True)]` AND that no bare `docker rm` was issued), `test_checkout_missing_removes_docker_only_and_leaves_git_alone` (`git branch --list` byte-identical after), `test_orphaned_is_reaped_but_only_when_nothing_claims_the_id`, `test_partial_cleanup_is_the_declared_recovery_state_only`. The protected two: `test_unattributable_is_never_reaped_and_cannot_be_selected`, `test_ambiguous_is_never_reaped_and_cannot_be_selected` (incl. the adversarial mixed selector), `test_owned_is_not_selectable_either`, `test_unknown_category_is_refused` ×3, `test_default_category_set_is_exactly_the_four_destructible` (asserts the complement is exactly `{owned, unattributable, ambiguous}`). §2.2's negative: `test_a_volumes_only_owned_instance_is_never_partial_cleanup`. Shared network: `test_shared_network_survives_reaping_one_joined_instance` builds two genuinely joined instances and asserts the network SURVIVES, B's container survives, A's is gone, and the note names why |
| **O3** transactional isolation | **MET** | `TestTransactionalIsolation` ×12. `test_one_volume_rm_failure_isolates_that_group` (real daemon error text preserved verbatim; the healthy group fully reaped; `status: "partial"`), `test_a_container_rm_failure_aborts_that_group_before_its_volumes` (asserts the volume `rm` was never even attempted), `test_a_network_rm_failure_lands_in_failed`, `test_the_daemon_dying_mid_pass_is_a_failure_not_a_crash`, `test_a_failing_clean_never_falls_back_to_a_bare_docker_removal`, `test_a_clean_that_refuses_outright_is_reported_not_raised`, `test_an_unexpected_refusal_mid_sweep_still_processes_the_rest` (the ciu-P28 no-escape-loop shape). Post-state truth: `test_a_full_success_is_reaped_and_the_document_is_a_re_survey` (pre-survey counts 2 orphaned, returned document counts 0). Purity: `test_two_consecutive_surveys_are_byte_identical_and_change_nothing` (identical JSON, host unchanged, record bytes unchanged, no destructive argv ever issued), `test_without_yes_the_survey_is_returned_verbatim`, `test_dry_run_prints_the_plan_and_touches_nothing` (`_clean_in` replaced with a `pytest.fail`). CLI exit 1 on partial in BOTH modes: `test_cli_exits_one_on_a_partial_pass_in_both_output_modes` |
| **O4** envelope + docs | **MET** | `schema_version == 1` and all-seven `counts` incl. zeroes: `test_envelope_is_versioned_and_counts_carry_all_seven`. Capabilities: `test_both_new_capability_identifiers_are_advertised` + the three updated literal pins. Docs, asserted from the shipped files: `TestSpecAndDocs` — S16.10 exists and names every one of the seven categories plus `worktree.reap.v1`; README carries the verb in its list; CONSUMERS carries the worked example, `unattributable` and the capability id; and `test_the_backlog_marks_ciu_25_fixed_naming_both_packages` parses the CIU-25 row for `FIXED` and the detail section for BOTH `ciu-P26` and `ciu-P27` — O4's negative ("FIXED without naming ciu-P26 as the co-requisite") is pinned by a test, not just by prose |

## 8. Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
src/ciu/worktree.py                               1702      0    696      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             9579      0   3920      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 3206 passed, 6 warnings in 19.78s =======================
```

Serial agrees:

```
$ .venv/bin/python -m pytest tests --cov=ciu --cov-branch --cov-report=term-missing -q -n 0
TOTAL                                             9579      0   3920      0   100%
3206 passed, 20 warnings in 39.30s
```

CIU-56 is fixed on this branch (`e83a8b44` put `--dist loadfile` into
`run-ciu-tests.py`), so unlike ciu-P26 the bare gate command is now the
green one — no caveat.

Test-count delta, measured rather than asserted:

```
$ .venv/bin/python -m pytest tests --collect-only -q --ignore=tests/tests/test_ciu_worktree_reap.py
3127 tests collected
$ .venv/bin/python -m pytest tests/tests/test_ciu_worktree_reap.py --collect-only -q
79 tests collected
```

3127 + 79 = 3206, and 3127 matches ciu-P26's LOG exactly (the two commits
since it added no test items).

Targeted regression check across every file this package could plausibly
affect:

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_worktree.py \
    tests/tests/test_ciu_cli_worktree.py tests/tests/test_ciu_worktree_branches.py \
    tests/tests/test_ciu_worktree_budget.py tests/tests/test_ciu_worktree_lease.py \
    tests/tests/test_ciu_worktree_lifecycle.py tests/tests/test_ciu_worktree_shared_infra.py \
    tests/tests/test_ciu_engine_worktree_budget.py tests/tests/test_ciu_deploy_actions.py \
    tests/tests/test_ciu_documentation_contract.py tests/tests/test_spec_contracts.py \
    tests/tests/test_ciu_cli_parser.py -q
775 passed in 23.43s
```

Beyond the three capability literals ruled on in §2.1/§3.2, **no
out-of-scope test required a change**, in this package or anywhere else.

Two branches were found dead only because the gate enforces 100% BRANCH
coverage, and both were real defects rather than test gaps: the unreachable
`checkout-missing` rule (§3.1), and — in an earlier draft — a
`_lease_is_expired` except-arm that no on-disk record could reach, which is
now exercised by constructing the malformed `WorktreeLease` in memory
(scenario #15's tail).

## 9. Commits

1. `56b711fc` — the controller Amendment to the handoff (scope widening +
   the `partial-cleanup` correction), committed separately from the
   implementation.
2. Implementation + tests + docs + backlog, one commit via
   `git commit --only -F - -- <paths>`.
3. This LOG file — a separate commit.

Exact hashes are read back with `git log --format=%H` after each commit and
reported in this package's final report; none is ever predicted ahead of the
actual commit.
