# Wave prompt — B068 + quick-wins (2026-09-08)

Branch: `fix/assay-b068-quickwins-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b068-quickwins`
Controller log: `assay/nyxloom-trove/reports/assay-WAVE-QUICKWINS-CONTROLLER-LOG.md`

No verdict-schema change in this wave. `VERDICT_SCHEMA_VERSION` stays 10,
`assay.toml`'s `schema_version` stays 2, `assay lanes --json`'s
`inventory_schema` stays 1. Every item below is additive or a bugfix.

## Context to read first

1. `assay/nyxloom-trove/4-backlog.md` — read each item's own full section
   (not just this prompt's summary): **B068** (line ~6566), **B072** (near
   the file's end), **B062** (~6635), **B063** (~6696), **B071** (~7027).
   Each already has a measured repro and acceptance criteria — this prompt
   does not repeat every detail, it sequences and rules the open forks.
2. `assay/CHANGES.md`'s `[5.0.0]` entry — the current shipped baseline.
3. `AGENTS.md` / `CLAUDE.md` (vbpub root) — shared-main commit discipline,
   trailer format, gate-verification-in-a-separate-step rule (LESSONS L4).

## Order and rulings

Work the items in this order. Each is its own commit (or small commit
group); do not let later items' diffs blur into earlier ones.

### 1. B068 (do this first — it's the one real investigation)

**The backlog entry proposes two possible fixes and says "source-confirm
first."** Do exactly that: reproduce the failure in a real Mode-B linked
worktree (main repo's `.git` not mounted), then trace `assay/git.py`'s
`_resolve_repo()` to find exactly which caller R0/R1's `mock`/coverage
path reaches that R2's `sql-mutation` path does not. Only after you can
name the divergent call site, choose between the backlog's two proposed
shapes:
- (a) make that resolution tolerate a linked-worktree `GIT_FAILED` the
  same way the R2 path already does (if that's genuinely what R2 does —
  confirm, don't assume), or
- (b) if git resolution is truly required for R0/R1's own semantics,
  replace the raw `fatal:` git stderr passthrough with a clear
  `ERROR/GIT_FAILED` naming the resolution gap and a remedy.

**Ruling: no preference between (a)/(b) from the controller — the
investigation itself should make the right answer obvious. If both are
genuinely viable, prefer (a)** (it restores real R0/R1 evidence in Mode-B
rather than merely explaining its absence), but do not force (a) if the
git resolution really is load-bearing for R0/R1's own correctness
elsewhere.

The backlog's own acceptance criteria apply unchanged: a regression test
pins the R0/R1-vs-R2 discriminator so this cannot silently regress back.

### 2. B072 — one-line fix, already scoped

`except (json.JSONDecodeError, ValueError, RecursionError) as exc:` at
`attestation.py:222`'s `parse_attestation`. Red-first test mirroring
`test_adjudication_provenance_parse.py`'s equivalent (a
`"["*100000 + "]"*100000` document, well under `MAX_ATTESTATION_BYTES`).
Also do the backlog's named sweep: check `adjudication.py`, `cli.py`,
`runner.py` for any OTHER `except (..., ValueError)`-without-
`RecursionError` parsing untrusted JSON — note the result (clean or not)
in your REPORT even if nothing else is found.

### 3. B062 — pyflakes sweep of `tests/`

Fix the 31 findings (25 unused imports, 5 unused locals, 1 redefinition).
**Ruling on the two judgment-requiring classes the backlog flags:** an
unused import that is actually an availability probe
(`pytest.importorskip`-shaped) — keep it, but only if it is REALLY used
that way; verify each, don't assume. An assigned-never-read local that is
the point of the assertion above it — keep the assignment but silence it
explicitly at that one site (e.g. `_ = value  # noqa: comment naming why`
or the project's own established idiom, check for precedent first), never
a blanket file-level suppression. Exclude `tests/fixtures/` from the lint
scope with a comment naming `broken.py` as the reason (already prescribed
in the backlog). Widen `tools/tester-unified-gate.sh`'s lint phase to
cover `tests/` and prove it by planting a throwaway unused import and
watching the phase go red, then removing the plant.

### 4. B063 — the three `PROJECT_ROOT.parent` test modules

**Ruling: skip-with-a-named-reason, not resolve-from-context.** The
backlog's own text calls this "cheap and honest" and the controller
agrees — these three modules are testing a property of the CHECKOUT
(that it sits inside a real repo), not a property of assay; when that
property is false, a skip is the true answer, not a lie fixed by
reaching further up the tree. Implementation shape: a fixture/helper that
checks `PROJECT_ROOT.parent` is a real git work tree (e.g. `git -C
PROJECT_ROOT.parent rev-parse --is-inside-work-tree`) and
`pytest.skip()`s with a message naming the missing parent repo when it
isn't — applied to all three named modules. Prove the backlog's own
acceptance measurement: a `cp -r` copy of `assay/` outside the vbpub
checkout runs the suite with **zero failures and zero errors** (skips are
fine) attributable to this; the three modules still run and measure the
same thing when the tree IS inside vbpub (no behavior change in place).

### 5. B071 — thread stdout/stderr tails into crashed mutation-state records

**Ruling: `crashed` bucket only, per the backlog's own priority ask — do
NOT wire `killed`/`survived`/`budget_exceeded` in this wave**, and do NOT
wire `write_progress`'s payload (leave that as a possible follow-up, not
scoped here). Thread `run.result.stdout_tail`/`.stderr_tail` into
`_write_mutation_state_record`'s payload using the exact field names
`verdict.py` already defines (`result_stdout_tail`/`result_stderr_tail`),
gated on `outcome_bucket == "crashed"`. Reproduce the backlog's own
`uq_work_units_id_operation` shape (or an equivalent minimal repro) as the
regression test, not a synthetic assertion. No schema/wire change — this
file is not a verified artifact.

## Binding constraints (every item)

- `assay verify` is unaffected by every item above — none of this is
  evidence, all of it is diagnostic or test-hygiene.
- Read the registered gate's verdict from its own log markers after it
  finishes, never from a piped exit code (LESSONS L4).
- Host: 8 cores shared with a production game server. `docker ps` AND
  `pgrep -af tester-unified-gate.sh` before starting anything — wait for
  any existing gate container/process, never race. Serial pytest under
  `nice -n 19 ionice -c 3`. `docker update --cpus=3` right after any gate
  container you launch starts.
- **Checkpoint clause (E-008):** if you cross ~120k context tokens or
  ~60 tool calls, cut at the next coherent boundary (green gate > commit >
  LOG/REPORT write; never on a red gate) and write a continuation brief to
  `assay/nyxloom-trove/reports/assay-WAVE-QUICKWINS-BRIEF.md` plus a
  self-authored `/compact`-retention prompt, commit, and stop — the
  controller dispatches a fresh successor from that brief.
- Commit trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
  (the controller's own Claude-Session line is added at merge time, not
  by you).
- When all five items are done and the registered gate is green, write
  `assay/nyxloom-trove/reports/assay-WAVE-QUICKWINS-LOG.md` (what you did,
  per item, with commit hashes) and a REPORT summarizing acceptance-box
  status per item, then stop — do not dispatch a reviewer yourself, the
  controller does that next.
