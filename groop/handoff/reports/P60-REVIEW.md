# P60 Frontier Review (pass #2) — APPROVED with review-fixes, MERGED

Reviewer: Opus 4.8 (frontier review + merge authority, controller-workflow-v2 §6).
Date: 2026-07-13. Branch: `feat/groop-p60-metrics-fieldlist-selector`.
Merge: `df13253` (`--no-ff`).

## Verdict

**Approved.** The package does what it says: `--metrics` accepts `full`,
`compact`, or a registry-validated comma-separated list of metric names and/or
family names, resolved in one helper, feeding the *existing* P55 prune path
rather than a second one. Requirement 3 ("reuse the existing prune step, do not
add a second prune loop") is honored exactly — `_compact_metric_names` was
generalized to `_kept_metric_names | None` in place.

The riskiest thing in the diff is invisible in the diff: P60 *adds* three
families (`net`, `damon`, `governance`) to `METRIC_GROUPS`, and `COMPACT_GROUPS`
was previously **derived** from `METRIC_GROUPS.keys()`. Left alone, that would
have silently widened `--metrics compact` to include net/damon/governance — a
real regression in a shipped flag. The implementer saw it and froze
`COMPACT_GROUPS` to a literal. I verified the literal is exactly right against
`main`: the pre-P60 `METRIC_GROUPS` had precisely the keys
`{mem_usage, psi, refault}`. Good catch by the implementer, and the REPORT's risk
table names it explicitly.

Also verified on `main`: the `--replay`/`--attach` rejection reads
`args.metrics != "full"`, so field lists are rejected there for free — no change
needed, and the tests prove it.

## Review-fixes applied (commit `ee8132c`, on the branch before merge)

1. **Oracle 6's two regression tests could not fail.** This is the one that
   mattered.
   - `test_fieldlist_compact_byte_identical_to_p55` asserted
     `set(eframe.metrics).issubset(expected_compact)`. A compact mode that
     dropped *every* metric passes an `issubset` check. Replaced with an exact
     cross-mode equality guard: for every entity, `compact_keys == full_keys &
     compact_families`. That fails if compact drops a compact metric or leaks a
     non-compact one. (Equality against a static set would be wrong — not every
     entity carries every metric — hence the intersection with the full-mode
     frame, which is the honest oracle.)
   - `test_fieldlist_full_is_byte_identical_to_p55` asserted four metric names
     were present on the root entity. It would pass if full mode pruned
     everything else. Replaced with a prunes-nothing assertion over every entity,
     plus structured-block survival.
   - Both tests were also *named* `..._byte_identical_to_p55` while asserting
     nothing about bytes. Renamed to what they check.
2. `METRIC_GROUPS`'s docstring still said "Compact-mode metric groups kept by
   `--metrics compact`" after P60 widened it into the general family index.
3. REPORT and STATUS both said "19 focused tests"; the real count is 20 (pass #1
   added the `--container` composition test and updated the LOG but did not
   propagate the count to the REPORT or STATUS).

Substantive note: the real backstop for Oracle 6 was never the P60 tests — it is
that P55's own 32-test suite still passes unchanged, which it does. The
review-fixed tests now add an independent guard rather than restating a hope.

## Validated from main (not the agent env)

```
PYTHONPATH=groop/src python -m pytest groop/tests -q -W error -p no:schemathesis
1066 passed, 2 skipped in 129.35s        # P60 branch, post-review-fix
1101 passed, 2 skipped in 144.71s        # main, after P60 + P62 merges
py_compile (registry, cli, collector, test_p60_fieldlist)  OK
git diff --check                                            OK
```

Live CLI from `main`, fixture cgroup tree:

```
--metrics ram,psi_mem_some_avg10  -> kept: ['psi_mem_some_avg10', 'ram']; network block: None
--metrics net                     -> kept: ['net_rx_bps','net_rx_pps','net_tx_bps','net_tx_pps']; network block: present
--metrics ram,bogus_metric        -> exit 2: "invalid --metrics: unknown metric token(s): bogus_metric"
```

The block-keep mapping and the never-silently-drop contract both hold in the real
CLI, not just in the collector unit tests.

## Non-blocking observations

- No `P60-SELFREVIEW.md` file: pass #1's findings were appended to `P60-LOG.md`
  instead. The content is there and was real work; the standing template asks for
  the separate file. Cosmetic.
- The `network` family token is spelled `net` only. The handoff wrote
  "`net`/`network`"; the REPORT argues (reasonably) that this was descriptive, not
  a dual-naming requirement, and records the alias as trivial future work. Accepted
  as-is — but note the *block* is called `network` while the *token* is `net`, so
  `FIELD_LIST_BLOCK_MAP` exists precisely to bridge that. Fine, just worth knowing.
- `_validate_metrics_mode` raises `SystemExit(2)` and `main()` catches it to
  `return 2`. Works, mildly indirect.

## Pass #1 overlap (trial metric)

| # | Pass-2 finding | flagged-by-pass-1 |
|---|---|---|
| 1 | Oracle-6 tests pass against a broken mechanism (issubset / 4-name spot check) | **no** — pass #1 explicitly concluded "No hollow tests identified", listing these two among the oracles it judged sound |
| 2 | Stale `METRIC_GROUPS` docstring | **no** |
| 3 | Test count 19 vs. real 20 in REPORT/STATUS | **partial** — pass #1 corrected the count in the LOG but not in the REPORT or STATUS |

**0 of 2 substantive findings flagged; 1 partial on a mechanical one.** Pass #1
did catch real mechanical issues (unused imports; a `lambda _cid: None` docker
stub that broke container resolution; a genuinely missing `--container`
composition test). Its false negative on the hollow-test question is the
interesting datum: asked directly "name any test that would still pass if the
mechanism were deleted," it answered "none" while two such tests sat in its own
diff.
