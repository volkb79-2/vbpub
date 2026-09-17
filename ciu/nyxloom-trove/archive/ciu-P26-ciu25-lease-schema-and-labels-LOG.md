# ciu-P26 — CIU-25 foundation: lease schema + ownership labels

**Handoff:** `nyxloom-trove/handoffs/ciu-P26-ciu25-lease-schema-and-labels.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `6f80e2cf` (ciu-P25 LOG,
confirmed with `git status --porcelain && git log --oneline -3` before any edit
— tree was clean).

**Status: COMPLETE, with one gate caveat that is NOT this package's code.**
All five oracles met; 3127 tests pass; coverage is 100.00% line+branch under
`run-ciu-tests.py --dist loadfile` and under a serial run. Under the gate's
bare default (`-n auto`, xdist `--dist load`) it reports 99.85%, for a
**pre-existing** coverage-measurement defect in an unrelated file that this
package's +122 tests merely tip into failing — **reproduced on the clean
`6f80e2cf` baseline with zero source changes**. Filed as **CIU-56** with the
reproducer and a one-line fix that lives outside this package's `scope.touch`.
See §7. No `escalate_if` fired. No `scope.forbid` file touched. No scope
widened.

---

## 1. Reading, before any code

Handoff in full, then, in order: `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-25` (the
five states, the ciu-P28 hotfix lesson, the "must not destroy on age /
basename / missing process" constraint); `src/ciu/worktree.py` —
`WORKTREE_INSTANCE_SCHEMA_VERSION` (line 56), the record dataclass +
`_record_from_dict` with its exact key-set comparison (142–200), `_utc_now`
(231), `resolve_max_concurrent_instances` + the closed `[ciu.worktree]` table
(the handoff cited ~2355–2400; the live function is at **2824**, and the
unknown-key refusal at **2843** — confirmed against the real file per the
handoff's own instruction), `remove()` (2418), `list_instance_records` /
`build_instance_document` / `inspect_instance` / `list_instances` /
`remove_document` (993–1186), `_runtime_identity` (1993);
`src/ciu/engine.py` — the label trace (§2), `main_execution`'s 17 steps,
`reset_service`; `src/ciu/deploy.py` — `action_clean`,
`_is_worktree_instance`, `_workspace_identity_network`, `_seconds`;
`src/ciu/workspace_env.py` (read-only) — `_compute_network_name`'s CIU-41
discipline, `_ensure_network_exists`, the two `DEVCONTAINER_NAME or HOSTNAME`
sites; `src/ciu/composefile.py` (read-only, FORBIDDEN) —
`generate_overlay`/`compose_file_args`; `src/ciu/cli.py`'s `_worktree`
inline-argparse dispatch; and the existing test fixtures in
`test_ciu_worktree.py` / `test_ciu_engine_worktree_budget.py` /
`test_ciu_deploy_actions.py`.

## 2. `escalate_if` #1 — the O4 injection point: investigated, did NOT fire,
   but the handoff's premise was materially wrong

O4 assumed "engine.py's overlay/label construction that composefile.py's
`generate_overlay`-style mechanism consumes". **That mechanism does not
exist.** Traced honestly, before writing anything:

- `generate_overlay(stack_dir, materialized, configfile_mounts, *, repo_root,
  physical_root, compose_yaml_text, governance, image_revisions)` has **no
  labels parameter of any kind**. Adding one means editing `composefile.py` —
  forbidden.
- CIU has never injected RUNTIME labels at all. The only label machinery in
  the codebase is (a) `engine._image_revision_bake_args` → `--set
  *.labels.<L>=<rev>`, a BAKE-time IMAGE label, and (b) `deploy.labels.prefix`
  / `<prefix>.component=<service>`, which is authored by the CONSUMER in their
  own compose template (`src/ciu/templates/stack.compose.yml.j2:25`) and only
  ever READ by CIU (orphan-sweep filters in `engine.reset_service` step 4).
- The workspace network is not created by `ciu up` at all — it is created by
  `workspace_env._ensure_network_exists` during `ciu env generate`, and every
  stack declares it `external: true`, so compose could not label it even in
  principle.

The `escalate_if` trigger is worded precisely: BLOCKED only if *no clean
injection point exists without editing composefile.py*. One does, entirely on
the engine side: **a second compose fragment**,
`<stack>/.ciu/ciu.compose.ownership.yml`, written by
`engine.write_ownership_overlay` from the already-rendered compose text and
appended to `composefile.compose_file_args(...)`'s returned list as an extra
`-f`. `composefile.py` is not touched (`git diff --stat` confirms empty).

Choosing a SEPARATE file over "more keys in the existing overlay" is not just
a forbid-list workaround, it is the better design here: `generate_overlay`
legitimately returns `None` when a stack has no secrets, no configfiles, no
governance and no image revisions, and the plainest possible stack's
containers still have to be attributable. Riding on a file that may not exist
would make the labels silently conditional on unrelated features.

`reset_service` was extended to pass the fragment to `docker compose down`
(same file set `up` composed with) and to remove it with every other generated
artifact.

## 3. `escalate_if` #2 — hostname/instance-id resolution: did NOT fire

- **INSTANCE_ID**: fully reusable and already the codebase's discipline —
  `record.instance_id` (itself derived from that workspace's own `ciu.env` at
  allocation), with `worktree._runtime_identity(ciu_root)` (exact-path
  `parse_workspace_env`) as the fallback for an `allocating` record that has
  no runtime identity yet. Nothing invented.
- **hostname**: there is no `socket.gethostname()` anywhere in `src/ciu/`,
  and no factored helper. There IS an existing, twice-repeated expression:
  `os.environ.get("DEVCONTAINER_NAME") or os.environ.get("HOSTNAME", "")`
  (`workspace_env.py:598` in `_detect_host_mdt_tmp`, `:742` in
  `_connect_devcontainer_to_network`), whose result `env generate` records
  into `ciu.env` as `DEVCONTAINER_NAME` (`:964`).

I judged this "already exists, reusably" — it is the codebase's one answer to
"what is this machine called", just not extracted into a function — and reused
the expression verbatim in `worktree._host_identity()` rather than introducing
`socket.gethostname()`. It could not be factored into a shared helper because
`workspace_env.py` is not in `scope.touch`; the duplication is deliberate and
its docstring names both existing sites. **This is a judgement call the
reviewer should check**: the alternative reading ("no reusable helper exists")
would have made this a BLOCKED escalation, and I chose not to escalate on what
is a missing `def`, not a missing mechanism.

Unlike the INSTANCE_ID half, the hostname half is deliberately AMBIENT: it
names the machine holding the lease right now, not the workspace being leased,
so reading it from a workspace's generated record would be wrong.

## 4. Design decisions worth reviewing

### 4.1 `schema_version` is STICKY, and a fresh `create` still writes v1

O1 says the constant bumps to 2 and that "only an operation that legitimately
mutates the record (acquire/renew/release) upgrades it on write". Implemented
exactly:

- `WORKTREE_INSTANCE_SCHEMA_VERSION = 2` (the current/max version),
  `WORKTREE_INSTANCE_BASE_SCHEMA_VERSION = 1`,
  `WORKTREE_INSTANCE_SCHEMA_VERSIONS = {1, 2}` (supported set);
- the dataclass field DEFAULTS to 1, so `create`/`adopt`/`ensure` keep writing
  a byte-identical v1 record;
- `to_dict()` emits the `lease` key **only** when `schema_version >= 2`.

That last point makes "a read never rewrites a v1 record" a *structural*
property rather than a promise: there is no v2 shape to emit by accident until
a lease operation sets the version. `test_v1_record_round_trips_with_no_lease_key_at_all`
and `test_a_plain_read_never_rewrites_a_v1_record_on_disk` pin it.

It also meant **no out-of-scope test needed altering** —
`test_ciu_worktree.py:205`'s `assert stored["schema_version"] == 1` after
`create` still holds, as does every raw-v1 fixture in
`test_ciu_cli_worktree.py`/`test_ciu_worktree_branches.py`. Had the dataclass
default become 2, that assertion would have had to change and I would have had
to stop and escalate.

`release` deliberately keeps the record at v2 with `lease: null`: "participates
in leasing, claims nothing" is a materially different fact for a future reap
than "predates leasing entirely", and a reader must be able to tell them apart.

### 4.2 The key-set check is schema-dependent, and its message names the version

`_record_from_dict` computes `required` from `raw.get("schema_version")`: the
v1 set, plus `lease` when the declared version is 2. A v2 record without
`lease`, and a v1 record WITH one, are both refused. The refusal message now
carries `(schema_version <n>)`, which keeps the existing out-of-scope
parametrized case `({**raw, "schema_version": 2}, "schema_version")` in
`test_ciu_worktree.py:524` passing on its own terms — the message it matches
still names the reason.

An unknown version (3, `None`, a string) is checked against the v1 key set and
then rejected by the unchanged "unsupported ... schema_version" refusal.

### 4.3 The lease is claimed BEFORE `docker compose up`

A run that crashes halfway has still created containers. Claiming slightly too
early is recoverable (`ciu clean` clears it, and a subsequent refusal — the
S16.3 budget slot, `guard_legacy_compose_project` — leaves an over-claim, which
reads as "still owned" and makes a future reap refuse). Claiming too late is
the orphaned-resource hole CIU-25 exists to close. Over-claiming is always the
safe direction; under-claiming is the bug.
`test_up_claims_the_lease_before_compose_runs` proves the order with a shared
ordering list, not by inspecting internals.

### 4.4 A malformed record WARNS during `ciu clean`; it does not fail the clean

`deploy.action_clean`'s lease clear is wrapped: a `WorktreeError` becomes
`warn(...)`, not `rc = 1`. Reasoning, in the code and in SPEC S16.9: what
`clean` certifies is that the project's containers/volumes/networks are gone,
and they are; a record too malformed to parse is a pre-existing S16 defect this
teardown neither caused nor can repair, and failing here would let one broken
record block every future teardown. It is also the SAFE direction — an
uncleared lease reads as "still owned", so a future reap refuses rather than
destroys.

This surfaced from an out-of-scope test, `test_action_clean_preserves_worktree_durable_inputs`,
which drops a deliberately minimal `{"schema_version": 1}` stub as a
"durable input clean must not delete". A strict failure there would have
required changing that test; the warn is both the correct behavior and the one
that leaves it untouched. `test_an_unreadable_record_warns_without_failing_the_clean`
pins the new behavior directly.

### 4.5 `worktree rm` clears the lease after a successful CLEAN, not after the
   `git worktree remove`

By the time `git worktree remove` succeeds the record is gone with the
checkout, so "clear on rm success" is only observable at one point: right after
`_clean_in` returns 0. That is also the semantically right point — a successful
clean is what removed the resources; a later `git worktree remove` failure does
not resurrect containers. `rc != 0` (including `--force` over a failed clean)
clears nothing.
`test_worktree_rm_clears_the_lease_after_a_successful_clean` snapshots the
record at the exact instant of the removal call;
`test_force_over_a_failed_clean_still_does_not_clear_the_lease` is the negative.

### 4.6 ONE duration grammar, not two

`--extend` needed a REFUSING parser; `deploy._seconds` is deliberately lenient
(falls back with a warning) because its callers are config values with sane
defaults. Rather than write a second regex, `deploy.parse_duration_seconds` was
added as the strict form and `_seconds`'s string branch now delegates to it, so
`24h`/`90m`/`3600` mean exactly one thing everywhere. `worktree.py` imports it
lazily (`deploy` imports `worktree`), the same cycle-avoidance the existing
`_candidate_project` → `engine` import already uses.

### 4.7 Shipped mode: lease YES, labels NO — a named, deliberate gap

`ciu up --shipped` (S8.5) creates containers too, so it acquires/renews the
lease — a pure record write, one line, no artifact. It is deliberately NOT
label-stamped: the labels are a GENERATED compose fragment, and `action_clean`
skips `reset_service` for a shipped stack (its compose file is maintainer-
owned), so a fragment written under vendored content would survive every
clean. Wiring it would have introduced a leak rather than closed a gap.
Documented as open in SPEC S16.9, CHANGES.md and the CIU-25 backlog row, and
pinned as deliberate by
`TestShippedModeParity::test_shipped_up_writes_no_label_fragment_into_a_vendored_stack`.

### 4.8 What is NOT labeled, and why

`external: true` volumes and networks are skipped: `ciu up` did not create
them, the workspace network is exactly such an entry (created by
`ciu env generate`, S2.6), and compose rejects extra keys on an external
declaration. A PRIMARY/unmanaged checkout is skipped per O4's own negative.

### 4.9 No new capability identifier

`WORKTREE_CAPABILITIES` was deliberately left alone. The handoff does not ask
for one, and `test_ciu_worktree.py:934` pins that tuple exactly — adding an
identifier would have forced an out-of-scope test change for no oracle. The
`lease` operation WAS added to `WORKTREE_JSON_OPERATIONS` (nothing pins that
set) so `--json` emits a proper S16.4 document.

## 5. Files changed

| File | What |
|---|---|
| `src/ciu/worktree.py` | O1: schema-version constants (2 / base 1 / supported {1,2}), `WORKTREE_LEASE_MODES`, `WORKTREE_LEASE_KEYS`, frozen `WorktreeLease` dataclass, `_parse_utc_timestamp` (explicit-offset-or-refuse), `_lease_from_dict` (closed keys, mode↔expiry pairing), schema-dependent key set + version-naming refusal in `_record_from_dict`, conditional `lease` in `to_dict`, `_utc_stamp`. O3: `_host_identity`, `lease_holder`, `acquire_lease`/`make_lease_perpetual`/`release_lease` (pure), `instance_record_path`/`read_own_instance_record`/`acquire_own_lease`/`release_own_lease` (disk, record-gated), `_lease_duration_hours`, `apply_lease`, `"lease"` in `WORKTREE_JSON_OPERATIONS`, the on-success clear in `remove()`. O2: `WORKTREE_TABLE_KEYS`, extracted `_validate_worktree_table` + `_primary_worktree_table` (shared by the cap and the TTL so the two policies can never disagree about which checkout is authoritative), `resolve_lease_ttl_hours`, `resolve_worktree_lease_ttl` |
| `src/ciu/engine.py` | O4: `OWNERSHIP_OVERLAY_NAME`, `OWNERSHIP_LABEL_INSTANCE`/`_REPO_ROOT`, `workspace_ownership_labels` (record-gated; exact-path `ciu.env` read; refuses on an identity-less env), `_labelable_top_level` (external skip), `write_ownership_overlay` (atomic write, list-form labels), plus step-15 wiring + leak scan and the extra `-f` in step 16. O3: `acquire_instance_lease` and its two call sites (`main_execution` step 16, `run_shipped`). `reset_service`: fragment in the `down` `-f` set and in the removal targets |
| `src/ciu/deploy.py` | `parse_duration_seconds` (strict form; `_seconds` delegates); `action_clean`'s on-success-only lease clear with the malformed-record warn |
| `src/ciu/cli.py` | `worktree lease` subparser (mutually-exclusive, required mode group), dispatch arm with human + `--json` output, `--define-root` registration, `_USAGE` + `_VERB_HELP["worktree"]` lines |
| `tests/tests/test_ciu_worktree_lease.py` | NEW, 95 items — schema v1/v2 reading, lease validation (shape/vocabulary/expiry pairing/naive timestamps/non-Zulu offsets), pure transitions, on-disk own-record operations, the TTL config key, the duration grammar, `apply_lease`, the CLI verb, and teardown-clears-on-success-only |
| `tests/tests/test_ciu_worktree_lifecycle.py` | NEW, 27 items — label resolution (incl. the armed CIU-41 ambient-mismatch fixture), fragment shape, `ciu up` wiring for managed vs unmanaged, `reset_service`, lease acquisition/ordering/renewal/dry-run/PRIMARY, shipped-mode parity, and `ciu clean`'s success/failure/unmanaged/malformed arms |
| `docs/SPEC.md` | New `S16.9` (lease field + v1/v2 coexistence, the mode↔expiry table, `lease_ttl_hours`, lifecycle, the verb, ownership labels, and a "Still open" naming both the shipped-mode label gap and the untouched three of CIU-25's five states); S16's "schema-v1" wording corrected; S16.4's operation vocabulary gains `lease` |
| `docs/CONFIG.md` | `[ciu.worktree]` table gains the `lease_ttl_hours` row + a paragraph on why absent ≠ default, with an S16.9 cross-link |
| `CHANGES.md` | Unreleased `feat(ciu):` entry |
| `KNOWN_ISSUES_TODO_BACKLOG.md` | CIU-25 row + detail updated to PARTIAL naming this package as the substrate and ciu-P27 as the successor (explicitly NOT FIXED); **new CIU-56** (§7) |

`scope.forbid` verified empty before writing code and again before commit:

```
$ git diff --stat -- src/ciu/composefile.py src/ciu/config_model.py \
    src/ciu/provisioning.py nyxloom-trove/backlog.md \
    nyxloom-trove/decisions.md nyxloom-trove/roadmap.md
(empty)
```

`git status --porcelain` lists exactly the `scope.touch` set and nothing else.

## 6. Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** schema v2 + lease | **MET** | Constants: `TestSchemaVersionConstants` (2 ×). Reading: `TestReadingV1AndV2Records` ×9 — v1→`lease=None` at version 1, v1 round-trips with NO `lease` key, v2-with-null and v2-with-held/perpetual round-trip, v2-missing-`lease` refused naming the version, v1-carrying-`lease` refused, version 3 refused, and `test_a_plain_read_never_rewrites_a_v1_record_on_disk` (byte comparison after `read_instance_record`). Validation: `TestLeaseValidation` — 5-way shape/vocabulary parametrize, held-without-expiry, perpetual-with-expiry, 3-way naive-timestamp parametrize, explicit `+02:00` accepted, 4-way unparseable parametrize, `_utc_stamp` format |
| **O2** `lease_ttl_hours` | **MET** | `TestLeaseTtlConfig` ×11 — closed two-key table; `None` table and absent key both yield `None` (**the negative: `{"max_concurrent_instances": 3}` alone must NOT produce a TTL**); int and fractional hours; 6-way refusal parametrize (`0`, `-1`, `"24h"`, `True`, `None`, `[24]`); unknown-key and non-table refusals through the new reader; the capacity reader still accepts the new sibling key; primary-root resolution and the outside-git `None` |
| **O3** lifecycle + verb | **MET** | Transitions: `TestLeaseTransitions` ×12 (holder composition ×4, now+ttl, fractional ttl, **renewal preserves `acquired_at_utc`**, 3-way non-positive-ttl refusal, perpetual, release-stays-v2, and a round-trip-through-the-reader sweep). Disk: `TestOwnRecordOperations` ×7 — **acquire and release on an unmanaged checkout write NOTHING (directory listing compared)**, the `allocating`-record exact-path `ciu.env` fallback, and release never dragging a v1 record to v2. `up`: `TestUpAcquiresTheLease` ×5 — no-TTL is a total no-op, TTL passed verbatim, **lease strictly before compose (ordering list)**, renewal across two ups, **a PRIMARY checkout invents no record**, dry-run changes nothing. Verb: `TestApplyLease` ×10 incl. `test_works_on_a_stopped_instance_without_touching_docker` (O3's negative — `procutil.docker` replaced with a raiser) and the exactly-one-mode 4-way parametrize; `TestLeaseCli` ×7. Teardown: `TestTeardownClearsTheLease` ×3 + `TestCleanClearsTheLease` ×4 — **failed clean and `--force`-over-failed-clean both leave the record byte-identical** |
| **O4** ownership labels | **MET** | `TestOwnershipLabelResolution` ×4 — unmanaged → `None`; **managed reads its own `ciu.env` while the AMBIENT `INSTANCE_ID` is deliberately set to `"ambient-wrong"`** (the review_focus fixture, asserted explicitly in the test body); identity-less `ciu.env` refuses. `TestOwnershipFragment` ×7 — services+volumes+networks all labeled, **`external: true` entries never labeled**, path/location, nothing-labelable → no file, non-mapping doc → no file. `TestUpStampsOwnershipLabels` ×3 — end-to-end `main_execution` proving the extra `-f` is last in the real compose argv and the on-disk fragment carries the workspace's own identity; an unmanaged checkout composes with no fragment and writes no file; `reset_service` downs with it and deletes it. Injection point: `git diff --stat -- src/ciu/composefile.py` is empty |
| **O5** docs | **MET** | SPEC S16.9 (new section under S16, extending CIU-28's normative area) + the two S16/S16.4 corrections; CONFIG.md's `[ciu.worktree]` row and rationale; CHANGES.md Unreleased entry; CIU-25 row and detail updated to a PARTIAL that names this package as the lease/label foundation and ciu-P27 as the follow-up — **explicitly not FIXED**, and the entry now also names the shipped-mode label gap |

## 7. Gate output (verbatim, read in a separate step from the run itself)

The real gate command, with xdist keeping each test file in one worker:

```
$ .venv/bin/python run-ciu-tests.py --dist loadfile
...
src/ciu/worktree.py                               1380      0    540      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             9221      0   3742      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
====================== 3127 passed, 6 warnings in 22.36s =======================
```

Serial agrees:

```
$ .venv/bin/python -m pytest tests --cov=ciu --cov-branch --cov-report=term-missing -q -n 0
TOTAL                                             9221      0   3742      0   100%
3127 passed, 20 warnings in 42.74s
```

The bare default does not:

```
$ .venv/bin/python run-ciu-tests.py
src/ciu/hook_templates/post_compose_db.py           19     15      4      0    17%   42-56, 75-82
FAIL Required test coverage of 100% not reached. Total coverage: 99.85%
====================== 3127 passed, 14 warnings in 20.45s ======================
```

**Every test passes in all three.** The 15 uncovered statements are the two
function bodies of `src/ciu/hook_templates/post_compose_db.py`, which this
package does not touch, import, or reference.

**This is not caused by this package's code.** `hooks_runner._load_hook_module`
loads a hook by FILE PATH under a synthetic non-`ciu` module name, and
`--cov=ciu` does not measure such a module unless the same file was ALSO
imported normally earlier in that worker process:

```
$ ... ::test_shipped_template_run_defaults_ready_and_reports_missing_secret --cov=ciu -q -n 0
src/ciu/hook_templates/post_compose_db.py   19  19   4  0    0%   27-82     # 1 passed
$ ... ::test_shipped_template_module_shape ::test_shipped_template_run_... --cov=ciu -q -n 0
src/ciu/hook_templates/post_compose_db.py   19   6   4  1   61%   46, 75-82
```

`run-ciu-tests.py` uses `-n auto` with xdist's default `--dist load`, which
splits `test_ciu_scaffold_hooks.py`'s tests across workers, so whether that
co-location happens is scheduling luck — and the luck turns on the suite's
test COUNT. Proven independent of this package by **checking out the clean
baseline (`git stash -u`) and adding one file of 122 trivial `assert True`
tests**: 2 of 3 `-n auto` runs then reported the identical 17% / 99.85%;
removing the pad file restored 6/6 green; this package's own +122 real tests
reproduce it 6/6.

Filed as **CIU-56** (High) with the full reproducer. The fix is one line in
`tests/tests/test_ciu_scaffold_hooks.py` (a module-level
`import ciu.hook_templates.post_compose_db  # noqa: F401`) and/or `--dist
loadfile` in `run-ciu-tests.py` — **both outside this package's `scope.touch`,
so neither was applied here**. The finding is worth more than the fix: those
15 statements have never genuinely been measured; they only ever LOOKED
covered.

Baseline for the test-count delta: `6f80e2cf` collects **3005**, matching
ciu-P25's LOG exactly. 3127 − 3005 = **+122** new items = 95 (`..._lease.py`)
+ 27 (`..._lifecycle.py`).

**Targeted regression check** (every file this package could plausibly
affect):

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_worktree.py \
    tests/tests/test_ciu_cli_worktree.py tests/tests/test_ciu_worktree_branches.py \
    tests/tests/test_ciu_worktree_budget.py tests/tests/test_ciu_engine_worktree_budget.py \
    tests/tests/test_ciu_deploy_actions.py tests/tests/test_ciu_clean_identity_networks.py \
    tests/tests/test_ciu_documentation_contract.py tests/tests/test_spec_contracts.py -q
539 passed in 23.07s
```

No out-of-scope test required a change, in this package or anywhere else.

## 8. Commits

1. Implementation + tests + docs + backlog (`src/ciu/worktree.py`,
   `src/ciu/engine.py`, `src/ciu/deploy.py`, `src/ciu/cli.py`,
   `tests/tests/test_ciu_worktree_lease.py`,
   `tests/tests/test_ciu_worktree_lifecycle.py`, `docs/SPEC.md`,
   `docs/CONFIG.md`, `CHANGES.md`, `KNOWN_ISSUES_TODO_BACKLOG.md`) — one
   commit, via `git commit --only -F - -- <paths>`.
2. This LOG file — a separate commit.

Exact hashes are in this package's final report (read back via
`git log --format=%H`, never predicted ahead of the actual commit).
