# ciu-P22 — V8-PREP-3 (narrowed): declaration-only `[service.<name>]` registry + WARN-only lint

**Handoff:** `nyxloom-trove/handoffs/ciu-P22-v8prep3-service-identity-registry.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `d604a568` (ciu-P21 fix
round, confirmed with `git status --porcelain && git log --oneline -3` before
any edit — tree was clean).

---

## Files changed

| File | What |
|---|---|
| `src/ciu/config_model.py` | new `_SERVICE_ALLOWED_KEYS`/`_SERVICE_TYPE_REQUIRED_FILE`/`_SERVICE_TYPES_FORBIDDING_LOCATION`/`VALID_SERVICE_TYPES` and `validate_service_registry` (S3.14, O1), wired into `render_global_chain` right after P21's `validate_user_tables` call; `SHIPPED_COMPOSE` added to the `config_constants` import; module docstring's SPEC-section list and Public API updated; `RESERVED_GLOBAL_TABLES`'s `"service"` bullet extended with a one-line pointer to the new validator |
| `src/ciu/deploy.py` | `CHECK_STAGES` gains `"service-registry"` (appended last, after `"consumption"`); `action_check` gains the WARN-only two-directional lint (S3.15, O2), placed right after stage 7's registry validation; `action_check`'s own docstring updated |
| `tests/tests/test_ciu_config_model_service_registry.py` | new — 33 tests (O1/O3) for the shape validator, direct-call and `render_global_chain`-wired |
| `tests/tests/test_ciu_deploy_actions.py` | +9 tests (O2/O3) for the two-directional lint |
| `docs/SPEC.md` | new **S3.14** (registry shape) and **S3.15** (WARN-only lint) clauses after S3.13; S13.4a's stage table gains a `service-registry` row |
| `docs/CONFIG.md` | new `[service.<name>]` subsection (worked CIU + EXTERNAL example) under "Global Configuration Sections", after `[registry.*]` |
| `CHANGES.md` | Unreleased → one new Added entry, placed first (newest-first convention this file already uses) |
| `docs/BACKLOG-2026-08-24.md` | CIU-V8-PREP-3 row corrected — see the "eliminated" correction below — and marked FIXED-partial |

No file outside `scope.touch` was edited; `scope.forbid` fully respected —
`git diff --stat -- src/ciu/engine.py src/ciu/composefile.py
src/ciu/provisioning.py src/ciu/deploy_pkg/layouts.py nyxloom-trove/backlog.md
nyxloom-trove/decisions.md nyxloom-trove/roadmap.md` is empty.

---

## Escalate_if check (performed before writing any code)

The one named condition: *"config_constants.py does not actually name the
ciu.defaults.toml.j2 / docker-compose.yml filenames as reusable
constants."* Read `src/ciu/config_constants.py` directly: it does —
`STACK_CONFIG_DEFAULTS = 'ciu.defaults.toml.j2'` and
`SHIPPED_COMPOSE = 'docker-compose.yml'`, both already module-level
constants with the exact string values needed. **Not triggered.**
`validate_service_registry` imports and reuses both (`SHIPPED_COMPOSE` newly
added to `config_model.py`'s existing `config_constants` import block,
`STACK_CONFIG_DEFAULTS` was already imported by P21) rather than
re-hardcoding either string.

---

## Design notes

### O1 — why the shape check is per-entry, not collective

`validate_user_tables` (S3.13, P21's precedent) raises ONE collective error
naming every offending top-level key, because that check answers a single
yes/no question across the whole config ("is this key allowed at all").
`validate_service_registry` instead raises its own tagged error **per
malformed stack entry** the moment it finds one: each `[service.<name>]`
entry's shape is independent of every other entry's (unlike S3.13's single
allowlist question), so a collective error covering the whole table would
either have to defer every check to enumerate every stack first (more
complexity, no real benefit — TOML tables are small) or produce a confusing
combined message mixing unrelated stacks' unrelated defects.
`test_one_bad_stack_among_several_is_named_specifically` pins this: a good
entry earlier in iteration order does not block or get conflated with a bad
one later.

### O1 — the unhashable-`type`-value guard (found while writing the tests, not in the handoff)

`VALID_SERVICE_TYPES` is a `frozenset`, and `x not in a_frozenset` hashes
`x`. A TOML author writing an inline-table or array `type` value (e.g.
`type = ["CIU"]`, syntactically legal TOML) would make `service_type` a
`list` — unhashable — and an unguarded `service_type not in
VALID_SERVICE_TYPES` would raise a bare `TypeError` instead of this
function's own tagged `ValueError`, corrupting the whole check with an
unrelated crash instead of a clean configuration-error report. Fixed by
checking `isinstance(service_type, str)` FIRST (short-circuiting the
membership test), mirroring `validate_declared_features`'s existing
`isinstance(vendor_images, list)` guard for exactly the same class of
reason. Pinned by `test_non_string_type_value_is_a_tagged_error_not_a_bare_typeerror`
(asserts `ValueError`, not `TypeError`).

### O2 — why the lint uses `report.note(...)` tagged `[WARN]`, not the module `warn()` function

`deploy.py` already has a bare `warn()` print helper (`[WARN] {msg}`), and
`action_check`'s stage 12 (S4.20, "declared secret consumed by no channel")
is the EXACT prior art for "WARN, never a failure, inside `_CheckReport`":
it calls `report.note("consumption", "[S4.20] ...")`, never the module
`warn()` function directly. I followed that precedent rather than calling
`warn()` inline, for a concrete correctness reason: `action_check`'s own
docstring guarantees that under `--json`, "this action prints no prose of
its own" — a raw `warn()` call bypasses that gate and would leak a stray
`[WARN]` print line into JSON-mode stdout, corrupting the single-JSON-object
contract (`test_check_json_output_writes_only_the_document` already pins
this for the whole action). `report.note(...)` reaches BOTH the prose
"note: ..." line and the JSON envelope's `notes` array through the existing,
already-json-safe `_emit_check_report` path with zero extra gating code.
Each message is explicitly tagged with the literal string `[WARN]` (matching
the oracle's own wording, "named in a `[WARN]`") so both stdout modes carry
a recognizably-advisory marker without a second print call.

### O2 — data source for "what's actually deployed"

The handoff pointed at `deploy.phases.*.services[].path and profile
stacks`. Reading `deploy.build_selection` (the function that actually
combines those two sources, `deploy.py:250`) confirmed its OWN documented
output is exactly `selection` — the parameter `action_check` already
receives. No new traversal of `phases.py`/`profiles.py` was needed: `{entry["path"]
for entry in selection}` IS "every stack this run's profile/phase selection
deploys," already assembled by the caller before `action_check` runs. Using
`profile.config.get("service")` (not `rendered`, which only contains
per-stack renders keyed by path) for the registry side keeps this a pure
GLOBAL-scope check — consistent with stage 7's registry validation
immediately above it — needing no per-stack render to have succeeded.

### O4 — where the new SPEC clauses/stage went

Two new clauses, S3.14 (shape) and S3.15 (the WARN-only lint), inserted
after S3.13 (P21's `ciu.user_tables`) and before `## S4 — Secrets` — the
next sequential S3.x slot, matching how S3.13 itself was appended after
S3.12. The lint additionally became a NEW row in the existing S13.4a stage
table (`docs/SPEC.md`) and a new member of `deploy.CHECK_STAGES`
(`"service-registry"`), appended LAST rather than interleaved: the V8
proposal's own §2.7 stage table (which S13.4a's table mirrors) predates the
two-level `stack.service` hierarchy this lint cross-checks, so there is no
"correct" numbered slot for it inside that table — appending it after stage
12 (`consumption`) leaves every existing stage's position, and the
`registry`/`hooks-load` adjacency pinned by
`test_ciu_provisioning.py::test_check_stage7_is_between_configfile_and_hooks_load`,
completely undisturbed (that test file is not in this package's
`scope.touch`, so its assumptions had to survive unmodified — verified by
running it standalone, see Gate output below).

### O4 — the backlog correction (not just a status flip)

Per the handoff's explicit instruction, I did not just mark the row FIXED
and leave the wrong sentence sitting above the new status line. The old row
said *"Global `[service.*]` registry eliminated; each stack owns its own
identity."* I read `docs/CIU-V8-TESTING-GATE-PROPOSAL.md` §1.15/§3.1 (the
CURRENT text, post-`4440c17e`) directly: it is the opposite claim — the
rev-1.4 two-level `stack.service` hierarchy **introduces**
`[service.<stack>]` as the global identity/location layer, with the
SEPARATE per-stack `[local_stack.<svc>]` (V8-PREP-4, §1.16) as the
deployment-wiring layer underneath it, joined by matching name (§1.16
mapping rule 1). The backlog row's "eliminated" sentence described an
earlier, since-superseded flat-model draft. I rewrote the row's body
(not only its Status field) to state the correction explicitly, name the
commit that fixed the proposal (`4440c17e`), and describe what P22
additive-shipped versus what the eventual V8 breaking step still owes
(the realness sub-table layer itself, `[local_stack]` join-key
*enforcement*, and compound-key topology/group references).

---

## Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** registry shape | **MET** | Every named positive (`CIU`, `COMPOSE`, `EXTERNAL`, `IN_PROCESS`, `description`, multiple types together) and negative (entry-not-a-table, unrecognized key, nested realness sub-table under BOTH a `CIU`/`COMPOSE` stack and an `EXTERNAL` entity, missing/unknown/non-hashable `type`, missing/non-string/empty/forbidden `location`, missing marker file, nonexistent directory, non-string `description`) case has its own test in `test_ciu_config_model_service_registry.py`. Absence/emptiness is proven a genuine no-op via a `Path.is_file` call-count spy (`test_absent_service_table_makes_zero_filesystem_checks`, `test_service_table_empty_dict_is_a_no_op`), not merely "raised nothing". The per-service realness layer's rejection (the review_focus's named priority) is pinned twice — nested under a `CIU`/`COMPOSE` stack (`test_nested_table_at_stack_scope_is_rejected_not_silently_accepted`) and nested under an `EXTERNAL` entity (`test_nested_realness_variant_under_external_is_also_rejected`), matching both worked shapes in proposal §3.1's own example. |
| **O2** two-directional WARN lint | **MET** | `test_ciu_deploy_actions.py`'s new `test_service_registry_lint_*` suite: absent/empty registry -> stage untouched (`notes == []`, `findings == []`); direction 1 alone (`test_service_registry_lint_registered_entry_not_deployed_warns`, isolated from direction 2 by a second consistent entry); direction 2 alone (`test_service_registry_lint_deployed_stack_not_registered_warns`); both directions simultaneously, asserted independently by content, not by a bare substring count (`test_service_registry_lint_both_directions_independently`); consistent registry+deployment -> zero notes; never a refusal even with two simultaneous mismatches (`test_service_registry_lint_never_fails_the_check`, `rc == 0`, `doc["status"] == "pass"`); `EXTERNAL`'s absent `location` correctly excludes it from direction 1 (`test_service_registry_lint_external_entry_without_location_never_warns`); the `[WARN]` tag reaches both JSON `notes[].message` and prose stdout (`test_service_registry_lint_prose_output_carries_the_warn_tag`). |
| **O3** tests | **MET** | 42 new tests total (33 + 9), zero of which assert only "no exception" for a negative case — every `pytest.raises` asserts `match=` against the specific offending name/value, and every WARN test asserts on `stage["findings"] == []` alongside the WARN content, proving the run's exit code / stage status is never touched. |
| **O4** docs | **MET** | `docs/SPEC.md` gains S3.14 (registry shape) and S3.15 (the lint, phrased normatively — "CIU check MUST additionally run...", matching S4.20's own normative style) plus a `service-registry` row in the S13.4a stage table. `docs/CONFIG.md` gets a worked CIU + EXTERNAL example under a new `[service.<name>]` subsection. `CHANGES.md` gets one new Added entry, placed first per this file's newest-first convention (verified against the existing P21 entries' ordering). `docs/BACKLOG-2026-08-24.md`'s CIU-V8-PREP-3 row's "eliminated" claim is corrected in its own body text, grounded against the proposal's current §1.15/§3.1/§1.16 text and the `4440c17e` fix commit — not merely re-flagged FIXED-partial. |

---

## Escalations

**None triggered.** The one named `escalate_if` condition
(`config_constants.py` not naming the two filenames as reusable constants)
was investigated directly by reading the file before writing any code — it
does name them (`STACK_CONFIG_DEFAULTS`, `SHIPPED_COMPOSE`), so the premise
held and no BLOCKED report was needed.

---

## Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
src/ciu/config_model.py                            336      0    166      0   100%
src/ciu/deploy.py                                 1592      0    696      0   100%
...
--------------------------------------------------------------------------------------------
TOTAL                                             8810      0   3532      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2888 passed in 19.87s =============================
```

2888 tests passed (up from the 2846-test baseline confirmed clean before any
edit — +42 from this package: 33 in the new
`test_ciu_config_model_service_registry.py`, 9 in
`test_ciu_deploy_actions.py`), 100% line **and** branch coverage
(`--cov-branch`, `--cov-fail-under=100`), exit code `0`.

**Adjacency regression check** (this package appends a stage to
`CHECK_STAGES` but must not disturb `test_ciu_provisioning.py`'s pinned
adjacency, which is NOT in this package's `scope.touch`):

```
$ .venv/bin/python -m pytest tests/tests/test_ciu_provisioning.py -k stage7 -q
........                                                                [100%]
8 passed in 0.3Xs
```

All 8 stage-7 tests, including
`test_check_stage7_is_between_configfile_and_hooks_load`, pass unchanged —
appending `"service-registry"` after `"consumption"` (position 13 of 13)
left every earlier stage's relative position untouched, as designed.

The six pre-existing `[S4.10] insufficient privilege to chown` `UserWarning`s
seen in the full run (from `test_ciu_host_secrets.py`, a file this package
never touches) are an unrelated, pre-existing devcontainer-non-root
environment artifact, not a regression introduced here.
