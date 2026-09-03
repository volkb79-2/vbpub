# nyxloom-P100 — code review (diff vs. frozen handoff)

**Reviewed:** `git diff 95472822..2f38a5f5 -- .` (branch `feat/nyxloom-P100-tier-routes-toml-validation`,
tip `2f38a5f5`). **Handoff:** last touched at `45732f58` (the round-5 freeze), unmodified by the
implementer afterward. **Method:** blind diff pass + independent oracle re-derivation + two live
evasion probes, before reading LOG/REPORT; reconciled after.

## Verdict: ACCEPT

No blockers.

## 1. Diff review

Nine files changed: `reference/AUTHORING.md`, `src/nyxloom/lint.py`, `tests/test_lint.py`, the
ownership-inventory doc, LOG.md, REPORT.md, the handoff itself (freeze/repair commits only), plus my
own two FIX-VERIFICATION reports. Every edit maps to an authorized `scope.touch` entry; nothing
outside it was touched.

- **`AUTHORING.md`**: the pinned paragraph is present byte-for-byte, including the
  "and a frontier-capable route" clause that took three repair rounds to restore — verified via a
  direct Python substring check (`pinned in text` → `True`), not a visual diff read. The worked
  example's `tier:` line is now `tier: <a live key from routes.toml>`.
- **`lint.py`**: `_check_l14(findings, path, fm)` matches the Implementation packet's signature
  exactly (no `cfg`/`body`), calls `Routes.load()` fresh, WARNs on any exception, ERRORs + up to 3
  `difflib.get_close_matches` suggestions on an unresolved tier, wired immediately after `_check_l13`.
  The broad `except Exception` carries `# census: advisory-degradation (nyxloom-P100)` — a real,
  independently-found reverse dependency (`test_exception_census.py`'s per-module legacy-handler
  budget), fixed in scope, correctly classified against the same tag `doctor.py` uses for an
  identically-shaped check.
- **`test_lint.py`**: `TestAuthoringDocTierGuidance` (3 cases) and `TestL14TierRoutesToml` (10 cases,
  4 parametrized) — every fixture is a real on-disk `routes.toml` via `paths.routes_path().write_text`
  inside `tmp_state`/`sample_project`, never a bare in-memory `Routes(...)`, matching Context item 5's
  explicit requirement. Both malformed-routes.toml variants (bad syntax, missing `routes` key) are
  real constructed files, run through the actual CLI, exactly as O4 requires.
- **Ownership-inventory doc**: `lint.py`'s row updated `1,112 → 1,262`, matching `wc -l` exactly; a
  consolidated "Re-measured 2026-09-03 (nyxloom-P100)" note added following the document's own
  convention; no other row touched.

## 2. Consumer-dimension sweep, independently re-verified

- `src/nyxloom/adapters.py`'s `_TIER_BAND = {"implement-1": 1, "implement-2": 2, "implement-3": 3}`
  — confirmed zero-diff (`git diff ... -- src/nyxloom/adapters.py` empty), NL-7 correctly filed and
  left untouched.
- `src/nyxloom/config.py`'s `next_implement_tier` (~845-880) — a mechanism I had not previously
  checked in any prior carve-review round; the implementer's own sweep surfaced it and I
  independently read it: it iterates `routes.tiers` **live** via `_IMPLEMENT_TIER_RE`, matching
  whatever `implement-N`-shaped keys actually exist today (currently none), returning `None` when no
  higher band is declared — genuinely different from `_TIER_BAND`'s hardcoded dict, not a second
  instance of NL-2's bug, and correctly out of L14's concern (a different job: post-reject tier
  escalation). `config.py` itself confirmed zero-diff.
- `reference/AUTHORING.md`'s ladder table (lines 80-86) and 2a-2e headers: unchanged, and correctly
  describe a planned mapping rather than a live-key claim (re-confirmed by direct reading).

## 3. Oracle re-verification (independent, against the actual tree)

```
O1: pinned paragraph verbatim-present (Python substring check) = True; "are deployed today" = 0 hits;
    tier: <a live key from routes.toml> present at line 395
O2-O5: TestL14TierRoutesToml — all pass in isolation (re-run directly)
O6: PYTHONPATH=src python3 -m nyxloom.cli lint nyxloom-trove/handoffs/*.md, run on the worktree
    filesystem directly — zero L14 findings anywhere; only the two pre-existing frontmatter-less
    CORE-REDESIGN notes produce (unrelated) L1 errors
O7: wc -l src/nyxloom/lint.py = 1262 (matches the row exactly);
    pytest tests/test_core_characterization.py -k test_inventory → 5/5 PASS
```

## 4. Evasion probes — planted, confirmed caught, reverted

- **O1**: inserted three words into the pinned paragraph in `AUTHORING.md`
  ("...preferably it is best to carve it down first."). `TestAuthoringDocTierGuidance::
  test_pinned_replacement_paragraph_present_verbatim` genuinely fails. Reverted; clean diff after.
- **O3's allowlist/blocklist distinction**: replaced `_check_l14`'s `fm.tier not in routes.tiers`
  check with a hardcoded blocklist of exactly the three historical bad values
  (`{"implement-2", "sonnet-xhigh", "opus-xhigh"}`). `test_bad_tier_produces_l14_error[sonnet5-hgih-True]`
  genuinely fails (`0 == 1`) — the blocklist has never seen that string and silently lets it through.
  Reverted; clean diff after.

Both probes confirm the test suite has real, load-bearing teeth against the two attack classes every
prior carve-review round flagged as the highest-risk false-PASS vectors for this package.

## 5. Hollow-test / frontmatter / forbid-list checks

- No hollow tests: every new case asserts a specific observable (finding count, severity, message
  substring, exit code, or a real behavior change across a routes.toml mutation) — none merely checks
  "doesn't raise."
- No coverage-evasion pragmas anywhere in the diff (`grep -n "pragma: no cover"` — no hits).
- Frontmatter-body agreement: the handoff file itself was last touched at the freeze commit, never
  edited by the implementer afterward.
- Forbid-list integrity: `src/nyxloom/config.py` (the `Routes` class) confirmed zero-diff; no test
  writes to the operator's real `~/.local/state/nyxloom/routes.toml` (`tmp_state`'s
  `monkeypatch.setenv("NYXLOOM_STATE", ...)` isolates every fixture); `handoff-frontmatter.schema.json`
  and `tests/conftest.py` both confirmed zero-diff, exactly as scope.touch predicted ("no edit
  needed").

## 6. Real gate run — independently reproduced

Ran `./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-nl2 tester-unified` myself from
this worktree (checked `docker ps` first — no competing `tester-unified` container), capped the
container `--cpus=3` within seconds of start, read the verdict from the JSON artifact in a separate
step:

```
outcome: PASS, exit_code: 0, commit: 2f38a5f5... (matches the reviewed tip exactly)
R0: PASS, R1: PASS
```

Container auto-torn-down. `git diff a65d4ed2..2f38a5f5` (the implementer's own real-gate commit to
the final tip) is doc-only (LOG.md/REPORT.md, zero code changes) — confirming the implementer's own
real-gate PASS at `a65d4ed2` and my independent PASS at `2f38a5f5` are the same code under test.

## 7. Reconciliation against LOG.md / REPORT.md

Read only after completing §1-6. Every substantive claim checks out:

- The BLOCKED→repair episode (Work items 1-4 tripping the ownership-inventory's size tolerance,
  exactly the class of reverse dependency nyxloom-P98 hit) is corroborated by `git log` and by the
  now-passing `test_inventory_*` suite.
- The disclosed use of a local `pytest -n auto -q` proxy run (when a concurrent package already held
  the one permitted `tester-unified` container) instead of fabricating a container run is exactly the
  right call under the shared-host one-gate-at-a-time rule, and was explicitly followed up with a
  real containerized run once asked — never presented as more than it was.
- `next_implement_tier` (§2) is a genuine, correctly-triaged finding beyond what any prior
  carve-review round named, independently re-verified rather than taken on trust.

## Verdict: ACCEPT

Merge-ready as-is. No product-level decision needed from the coordinator.
