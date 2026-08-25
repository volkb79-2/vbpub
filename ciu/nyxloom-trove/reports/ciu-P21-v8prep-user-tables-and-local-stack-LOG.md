# ciu-P21 — V8-PREP-1 (`ciu.user_tables`) + V8-PREP-4 (`local_stack` root key)

**Handoff:** `nyxloom-trove/handoffs/ciu-P21-v8prep-user-tables-and-local-stack.md`
**Branch:** `feat/ciu-qol-v8prep-wave` · **Base HEAD:** `0ac92992` (ciu-P20's
commit, confirmed with `git status --porcelain && git log --oneline -3`
before any edit — tree was clean).

---

## Files changed

| File | What |
|---|---|
| `src/ciu/config_model.py` | new `RESERVED_GLOBAL_TABLES` frozenset (O1); new `validate_user_tables` (O1), wired into `render_global_chain` beside `_validate_deploy_landscape_id`; `validate_stack_shape`'s docstring extended for `local_stack` recognition (O2); module docstring's Public API list updated |
| `tests/tests/test_ciu_config_model_user_tables.py` | new — 22 tests (O1/O3) |
| `tests/tests/test_ciu_config_model_local_stack.py` | new — 12 tests (O2/O3) |
| `docs/SPEC.md` | new **S3.13** clause (`ciu.user_tables`); S3.7 extended with `local_stack` prose |
| `docs/CONFIG.md` | new `[ciu].user_tables` subsection under `[ciu]`; `[<root>]` extended with a `local_stack` worked example |
| `CHANGES.md` | Unreleased → two new Added entries (one per oracle) |
| `docs/BACKLOG-2026-08-24.md` | CIU-V8-PREP-1 and CIU-V8-PREP-4 rows → FIXED-partial with the additive-subset scope stated plainly |

No file outside `scope.touch` was edited. `scope.forbid` fully respected:
`git diff --stat -- src/ciu/engine.py src/ciu/deploy.py src/ciu/composefile.py
src/ciu/provisioning.py nyxloom-trove/backlog.md nyxloom-trove/decisions.md
nyxloom-trove/roadmap.md` is empty.

---

## O1 — `RESERVED_GLOBAL_TABLES` membership: the research, stated plainly

The handoff's escalate_if explicitly anticipated this: "grepping
config_model.py/render_global_chain's actual global reads shows
RESERVED_GLOBAL_TABLES needs more members than {deploy, ciu} to be honest
— this is not a blocker, extend the set and note the discrepancy in the
LOG." It does need more members. Here is the trace.

I enumerated every `render_global_chain` call site (`engine.py` ×4,
`deploy.py`, `worktree.py` ×4, `dev.py`) and, for each, followed the
returned dict (or a still-global-scope value derived from it, e.g.
`profile.config` from `deploy_pkg/profiles.py`'s `resolve_profiles`, which
is `global_cfg` or a profile overlay over it — never a stack merge) to see
which top-level keys are actually read with `.get(name)` / `[name]`:

- **`ciu`** — `engine.py` (`auto_connect_network`, `log_level`), `deploy.py`
  (`resolve_profiles`'s `ciu.instance.service_profiles`), `worktree.py`
  (`ciu.worktree` capacity cap, S16.3), `warn_policy.py`,
  `provisioning.py` (`REGISTRY_VALIDATOR_KEY` lookup via `_run_consumer_validator`).
- **`deploy`** — `engine.py`/`deploy.py` everywhere; `config_model.py`
  itself (`_validate_deploy_landscape_id`, `validate_declared_features`'s
  `deploy.layouts`/`deploy.provenance.vendor_images`); `hosts.py`.
- **`topology`** — `worktree.py`'s `_ref_service_port` reads
  `ref_global.get("topology")` on a pure global-chain render
  (`write_rendered=False`, no stack merged in at all).
- **`vault`** — `deploy.py`'s `_is_vault_stack_path(profile.config, ...)`.
  Confirmed `profile.config` is global-scope-only by reading
  `deploy_pkg/profiles.py`'s `resolve_profiles` (`config=global_cfg` /
  a profile overlay, never a stack merge).
- **`registry`** — `provisioning.py`'s `probe_ref` (its own parameter
  docstring literally says `# merged global config`) and
  `validate_registries`, both called with `profile.config`.
- **`governance`** — `governance.py`'s own docstring: "a bare top-level
  `[governance]` table in `ciu.global.toml`" is the BASE layer, read
  directly off the global config.
- **`service`** — no Python `.get("service")` exists (it is pass-through
  data, not interpreted), but `docs/SPEC.md` S3.8 documents it as the real,
  currently-shipped global `[service.*]` registry that stacks reference
  directly in their own TOML templates. Omitting it would make any config
  using this already-documented pattern hit a spurious "unknown table"
  error the moment it opts into `ciu.user_tables`, despite `service` not
  being a user-owned table at all — a real correctness bug, so it is
  included.
- **`auto_generated`** — S3.9: `engine.auto_generate_values` computes and
  writes `build_version`/`build_time`/`uid`/`gid`/`docker_gid` onto the
  merged config every run. A consumer's own top-level `[auto_generated]`
  table would be silently clobbered by that write, exactly the collision
  risk `RESERVED_GLOBAL_TABLES` exists to name.

**Explicitly excluded**, despite being members of `RESERVED_GLOBAL_NAMESPACES`
— grepping every call site found no code reading a literal TOP-LEVEL table
by these names off the global config:
- `consul` — only ever read nested, as `[registry.consul]`
  (`provisioning.py`'s `token_vault_path` lookup); never a top-level
  `[consul]` table.
- `env` — reserved because `env` is the Jinja context key CIU injects
  itself (`_make_render_context`); `[deploy.env.*]` is nested under
  `deploy`, not a top-level `[env]` global table.
- `state` — a STACK-scope reserved key (`_STACK_RESERVED`, S3.4/S3.5)
  preserved on re-render of `ciu.toml`; never a top-level table in
  `ciu.global.toml`.
- `secrets` — a STACK-scope concept only (`[<root>.secrets]`, S4.1); S4.1
  explicitly says global config MUST NOT contain `secrets` tables.

`build` (proposal §1.14's own example lists it as a USER table) is
deliberately absent, per the handoff's own explicit instruction.

**Result:** `RESERVED_GLOBAL_TABLES = {ciu, deploy, topology, vault,
registry, governance, service, auto_generated}` — 8 members, a genuine
proper subset of `RESERVED_GLOBAL_NAMESPACES`'s 12, not a renamed copy
(pinned by `test_reserved_global_tables_is_a_distinct_object_from_namespaces`
and `test_reserved_global_tables_is_a_proper_subset_with_different_membership`).

---

## O2 — why `validate_stack_shape` needed almost no functional change

Tracing the existing logic revealed that `local_stack` **already passes**
`validate_stack_shape` today with zero code changes: it is not in
`_STACK_RESERVED` (`{"state"}`), so it counts as the one non-reserved root
key; it is not in `RESERVED_GLOBAL_NAMESPACES`, so it never hits the S3.7
collision check. `test_validate_stack_shape_custom_root_not_in_reserved_ok`
(pre-existing, `tests/tests/test_ciu_config_model.py`) already proves this
pattern generically for any non-reserved name.

I also verified the handoff's escalate_if premise directly: `grep -n
"root_key ==\|root_key in \[\|root_key in ("` across `src/ciu/*.py` found
**zero** hardcoded root-key-string special-casing anywhere — every
downstream reader (`secret_directives.discover(stack_root_key, ...)`,
`find_misplaced(config, stack_root_key=...)`,
`composefile.render_configfiles(stack_dir, root_key, ...)`,
`governance.resolve_stack_governance`) takes the root key purely as an
opaque parameter/dict-lookup key. The escalate_if did not fire.

Given that, the "recognition" work is a documentation act, not a
functional one: `validate_stack_shape`'s docstring now states explicitly
that `local_stack` is deliberately absent from both reserved sets and
flows through unchanged, and a dedicated regression-proof test file pins
the behavior against future accidental reservation. No new symbol/constant
was invented for this — inventing one (e.g. a
`PREFERRED_STACK_ROOT_KEYS` set) would have been unused machinery beyond
what O2 actually asks for ("root-key recognition", not enforcement or
preference-tracking), and the review_focus explicitly warns against
anything beyond root-key recognition creeping in.

---

## Oracle-by-oracle evidence

| Oracle | Verdict | Evidence |
|---|---|---|
| **O1** `ciu.user_tables` | **MET** | `RESERVED_GLOBAL_TABLES` is a distinct frozenset (see research above), `validate_user_tables` is a no-op when `ciu.user_tables` is absent (`test_absent_user_tables_any_top_level_keys_pass_unchanged`, `test_no_ciu_table_at_all_is_a_no_op`), wired into `render_global_chain` beside `_validate_deploy_landscape_id` and validated on the FINAL merged config including the worktree overlay (`test_render_global_chain_validates_final_merged_worktree_overlay_value`, `test_render_global_chain_later_layer_relaxes_earlier_declaration`). An unlisted key raises one collective ValueError naming every offender (`test_multiple_unknown_top_level_keys_all_named_in_one_error`). |
| **O2** `local_stack` root key | **MET** | `validate_stack_shape({"local_stack": {...}}) == "local_stack"` (`test_local_stack_root_key_accepted_and_returned`); S3.5 invariant unchanged with `[state]` present and with a second root key present (`test_local_stack_root_plus_state_still_exactly_one_key`, `test_local_stack_alongside_another_root_key_still_raises_s3_5`); `local_stack` absent from both reserved sets (`test_local_stack_not_in_reserved_global_namespaces`, `test_local_stack_not_in_reserved_global_tables`); a conventional root key is unaffected (`test_conventional_root_key_unaffected_by_local_stack_recognition`, `test_conventional_root_key_still_hits_s3_7_collision_unchanged`). |
| **O3** tests | **MET** | 34 new tests across the two files cover every named case: regression bar, valid declaration, RESERVED_GLOBAL_TABLES collision (parametrized over all 8 members), and all four malformed-declaration shapes (non-list, non-string member, bad-charset member, duplicate) for O1; root-key acceptance, non-table rejection, and three downstream-reader fixtures (`secret_directives.discover`, `find_misplaced`, `composefile.render_configfiles`) proving opaque-parameter treatment for O2. Every negative case asserts on exact message content (`match=` with the specific offending value/tag), never bare "no exception". |
| **O4** docs | **MET** | `docs/SPEC.md` S3.13 (cited above `## S4 — Secrets`, immediately after S3.12 — the last S3.x clause — keeping the numbering sequential) documents `ciu.user_tables`; S3.7 extended in place for `local_stack` (same clause that defines the reserved-namespace collision it is the counterpoint to). `docs/CONFIG.md` gets a worked `ciu.user_tables` example under `[ciu]` and a `local_stack` worked example under `[<root>]`. `CHANGES.md` Unreleased gets two entries. `docs/BACKLOG-2026-08-24.md`'s CIU-V8-PREP-1/4 rows are marked FIXED-partial, stating plainly what shipped (additive groundwork) versus what remains deferred (the V8 breaking steps: defaulting `ciu.user_tables` to empty; making `local_stack` the only accepted root key + per-service wiring/hook relocation). |

---

## Escalations

**None triggered.** Both named `escalate_if` conditions were investigated
directly rather than assumed:

1. "RESERVED_GLOBAL_TABLES needs more members than {deploy, ciu}" — **true**,
   handled as instructed (non-blocking, extended the set, documented above
   and in the source's own inline comment block).
2. "a downstream reader of root_key special-cases a specific STRING" —
   **false**, verified by grep (`root_key ==`, `root_key in [`, `root_key in
   (` all return zero hits across `src/ciu/*.py`), so O2's "every reader is
   parameterized" premise held.

---

## Gate output (verbatim, read in a separate step from the run itself)

```
$ .venv/bin/python run-ciu-tests.py
...
src/ciu/config_model.py                            306      0    146      0   100%
...
--------------------------------------------------------------------------------------------
TOTAL                                             8770      0   3502      0   100%
Coverage JSON written to file coverage.json
Required test coverage of 100% reached. Total coverage: 100.00%
============================ 2845 passed in 20.47s =============================
```

2845 tests passed (34 new: 22 in `test_ciu_config_model_user_tables.py`,
12 in `test_ciu_config_model_local_stack.py`), 100% line **and** branch
coverage (`--cov-branch`, `--cov-fail-under=100`), exit code `0`. Full
existing suites for `config_model`, `secret_directives`, and `composefile`
were also run standalone first (294 passed) to confirm no regression
before the full-gate run.
