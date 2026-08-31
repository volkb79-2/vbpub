# assay Wave C (Go, the P27 re-carve) — controller log

Running audit trail of every autonomous controller decision for Wave C,
maintained the same way as `assay-WAVE-B-producer-CONTROLLER-LOG.md`.
Operating rule: same active `/goal` as Wave B — report-and-continue on
routine events (checkpoint/successor cycles, REJECT→fix-round,
ACCEPT→merge/release), log every one here plus a brief chat note; actually
pause only for the reviewer's 3-round cap hit without ACCEPT, or a genuine
product/design fork the wave plan / decisions.md / A-334/A-335-style
principles don't already resolve.

- **2026-08-31 (dispatch)** — Wave B's real release (assay-v4.0.0) is
  confirmed published, deployed, and dstdns-notified (see the Wave B
  controller log's final entries). Per the operator's standing ruling
  (`WAVE-PROMPT-2026-08-30-js-consumer-producer.md` line ~20: "Wave C's
  dispatch waits for Wave B's own real RELEASE... not merely its merge"),
  Wave C is now clear to start.

  Before writing the dispatch prompt, ran a research-only fork to settle
  whether the Go wave's own planning ground (`STATE.md`'s P27 history) was
  current or stale — see the Wave B log's last entry for why this was
  needed rather than assumed. Findings, independently re-verified by the
  controller directly against `decisions.md`/`2-product-definition.md`/
  `carve-assets/P27/`:
  - **A-217 (2026-08-11) already resolved A-O19** — ruled option 2 (a
    real source-side statement-position oracle) over blocks-as-truth or a
    v5 schema narrowing. `STATE.md`'s "P27 is still NOT dispatchable"
    language describes the pre-ruling state and is stale AS A BLOCKER, but
    was correct that P27 needed re-carving around the oracle rather than
    dispatch as originally scoped — this wave IS that re-carve, not a
    contradiction of the old note.
  - `3-roadmap.md` (current as of 2026-08-24) lists M6 (Go) as next,
    after M4 (ship) and M5 (SQL/P34), both done — clear to proceed.
  - **Current authoritative scope is narrower than the old STATE.md
    P27→P32 sequence.** `2-product-definition.md`'s F008 has 5 acceptance
    criteria; F008-A1/A2 already `proven`. M6 delivers only the three
    `absent` ones (F008-A3 statement-granular R1, F008-A4 real fixture
    regen, F008-A5 srdm covergate qualification) plus B047's smaller
    items (helper distribution, `external_tools` declaration, `go-cover`
    producer key, gate-envelope check). **No later roadmap or backlog
    entry re-authorizes the older STATE.md language about "P30 real Go
    R2" / "P31 real Go R3"** — Go R2/R3 stay explicitly out of scope for
    this wave; `generate_mutants` stays `UNSUPPORTED` for Go.
  - `carve-assets/P27/` (README, BLOCKED-grammar.md, probe-results.json,
    pinned-environment.json, fixture/, expected/, manifest/, witness/ — 14
    real witness files) is fully present and real: real `gofmt`-clean Go
    source, real `tester-unified-go:local`-produced coverage profiles,
    including the collision-pair impossibility proof itself
    (byte-identical profiles, provably different correct statement
    positions) and four other named discriminator/caveat fixtures. This
    is carver-owned, frozen evidence — cited in the dispatch prompt, not
    to be edited by the implementer.
  - Code is NOT starting from zero: `adapters/go.py` and
    `coverage_parsers/go_cover.py` already exist, already carry
    "STALE PREMISE, TRACKED (A-234/A-217)" banners naming exactly what
    this wave must fix, and were both touched as recently as Wave A's
    B039 work (the shared classified-line ceiling).
  - A genuine design fork exists (A-239's concrete `blocks` field shape
    and the new protocol hook's exact signature) but is correctly left
    undecided in the docs for the re-carve itself to design, per A-084 —
    not a controller-level pause point; this is ordinary implementer
    design work bounded by an already-accepted general shape, the same
    kind of call Wave B's implementer made for its own schema fields.

  **Controller decision**: proceed with dispatch, scoped to F008-A3/A4/A5
  + B047 items 1/2/3/5/6 (item 4, the shared bound, already landed in
  Wave A — nothing to do). Recorded the full delta as a new "## Wave C —
  dispatched 2026-08-31" section in
  `WAVE-PROMPT-2026-08-30-js-consumer-producer.md` (commit `25b1f7fb`),
  following the same shared-skeleton-plus-delta pattern the doc already
  used for Wave A→B, so the authoritative scope, NOT-IN-SCOPE list, rules,
  and reviewer emphasis live in one committed place before the implementer
  starts — not only in this log or the dispatch prompt text.

  Dispatched a fresh Opus session (never a fork, per the estate's
  implementer-role doctrine — this wave carries real design judgment:
  A-239's concrete carve) as the Wave C implementer, worktree
  `.worktrees/assay-wave-c-go`, branch `feature/assay-wave-c-go` off
  `main`. Expect a MINOR bump (no `!` commit) — this wave is designed not
  to touch the verdict wire schema (the new `blocks` field lives in the
  internal `coverage_parsers/model.py` representation, not `verdict.py`);
  told the implementer to stop and write a decision ask rather than cut a
  v10 if that assumption turns out wrong.

  Next: wait for the implementer's first checkpoint or completion signal,
  per the same external-compaction / fresh-successor pattern used
  throughout Wave B.

- **2026-08-31 (checkpoint 1)** — Implementer's first checkpoint arrived
  ~21 min in, at commit `4408622b` on `feature/assay-wave-c-go`. Real
  progress: built `tester-unified-go:local` (not on this host — from the
  committed Dockerfile), then proved the statement-position oracle
  (`assay/helpers/go/stmtpos/`, transcribed from `cmd/cover`'s own
  instrumenter per A-217's implementation note) against **all eight**
  frozen `carve-assets/P27/witness/` fixtures, including the impossibility
  proof itself — `collision-colA`→`{4,6}`, `collision-colB`→`{4,5}`,
  derived from byte-identical profile bytes. New evidence filed under
  `carve-assets/P27-recarve/` (the frozen `P27/` originals untouched, per
  instruction). The `blocks`/model half of item 1 (A-239) is also done:
  `CoverageBlock`, `FileCoverage.blocks`, `CoverageProfile.
  statement_attributed`, `go_cover.parse` keeping columns, the pure join
  in the new `statement_attribution.py` — 14 tests, all green. Correctly
  did NOT claim `lit.go`'s laundering is fixed (it structurally cannot be,
  at line granularity, per the caveat's own nature) — documented,
  asserted by a test, recorded as `A-393`, filed as `B053`.
  Self-caught, twice, the exact wrapper-exit-code trap AGENTS.md warns
  about (a reported "exit 0" while the real pytest/gate result was
  nonzero) by reading the job's own status in a separate step rather than
  trusting the wrapper's own summary — no controller action needed, this
  is the discipline working as designed.
  Left the gate running detached (`gate2.log`, PID confirmed alive via
  `ps`/`docker ps` on the controller side) and ended its turn per the
  E-008 checkpoint clause with two decision asks open rather than
  improvised, and a very thorough continuation brief (`BRIEF-1.md`) naming
  every remaining seam by file:line.

  **Decision ask 1 — register `GoAdapter` in `cli.py`'s built-in registry
  (currently python/sql/javascript only)?** F008-A5 (srdm covergate
  qualification) needs a runnable Go lane through the real CLI, so
  registration is implied by an acceptance criterion already ruled this
  wave, not new scope. **Controller decision: yes, but strictly as the
  LAST step of item 1's wiring, after `requires_statement_attribution`,
  the new `statement_blocks` hook, the `evaluate` refusal (`A-392`), and
  `external_tools = ("go",)` (item 4) all land — never registered with
  only the oracle standing alone.** Registering earlier would let a Go
  lane run today still reading block-extent-as-truth (`requires_
  statement_attribution` unset), reintroducing exactly the wrong-verdict
  problem A-217 exists to fix — an A-334/A-335-style honesty violation
  (a Go R1 claim going out as if audited when it structurally is not).
  Once the guard is wired, an unavailable Go toolchain (true in this
  devcontainer) correctly refuses `MISSING_EXTERNAL_TOOL` rather than
  crashing (A-253's mechanism, already built) — registration is then
  safe in every environment, audited or cleanly refused, never silently
  wrong.

  **Decision ask 2 — `tests/test_cli_run.py:406` asserts `"helpers" not
  in document`; this wave becomes the first real producer.** Controller
  decision: this is item 6 (B047 item 5, gate-envelope check) — already
  in scope, not new work, exactly as the implementer's own brief already
  concluded ("item 6 is therefore more than a doc check"). Do not weaken
  the existing assertion for adapters that still produce none; add a
  parallel test asserting `helpers` IS present with the right shape
  (`role="statement-positions"`, identity naming `go version …`) for a Go
  lane specifically, once decision 1's full chain make that lane
  actually runnable — real end-to-end proof still needs
  `tester-unified-go:local` (A-334), matching Wave A/B's own real-tool
  qualification-harness pattern rather than a mock standing in for the
  toolchain.

  Both relayed to the SAME agent via `SendMessage` (resume, not a fresh
  `Agent` call — per the standing memory on that mistake), confirming the
  natural next step is finishing item 1's "STILL TO WIRE" list from
  `BRIEF-1.md` §3 (the new hook, the evaluate refusal, updating every
  other adapter's two new members, the runner wiring, THEN
  external_tools + registration), plus the packaging fix the implementer
  already flagged as a real shipping blocker on its own
  (`pyproject.toml`'s package-data omits the helper's `.go`/`.mod`
  files — would silently vanish from the wheel) — no controller
  disagreement there, just confirming it's correctly on the list.
  Gate on the checkpoint's own tip (`4408622b`) confirmed still genuinely
  running (docker container live, log advancing through the normal phase
  sequence) — not yet complete; will be read on the next contact.

- **2026-08-31 (gate PASS on `4408622b`)** — Confirmed directly from
  `gate2.log` as a separate step (not a pipe tail):
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, full B006(a) WI-5
  receipt PASS on all four claims. Relayed the verdict to the implementer
  via `SendMessage` (resume) and confirmed via `ListAgents` it resumed
  (not a duplicate spawn) rather than trusting the send call alone.

- **2026-08-31 (self-correction — CORRECTING MY OWN PRIOR ENTRY ABOVE)** —
  The checkpoint-1 entry above states the packaging gap as "a real
  shipping blocker" with "no controller disagreement." **That was wrong,
  and the implementer caught it, not me.** Before writing the fix, it
  built the wheel with `[tool.setuptools.package-data]` deleted entirely
  and read the zip's own namelist: `assay/helpers/go/stmtpos/*.go` and
  `go.mod` shipped anyway — `setuptools_scm` installs a git file finder
  and `include_package_data` defaults true under pyproject metadata, so
  every git-tracked file under the package dir already ships regardless
  of that stanza. The stanza naming the schema, and the schema shipping,
  was a correlation both the implementer and I read as the mechanism —
  exactly A-334's pattern, one layer up: not a test double this time, but
  an untested causal claim about packaging, stated confidently by both
  of us before either ran a build. One `python -m build` +
  `zipfile.namelist()` refuted it in under a minute. Recorded as `A-396`.
  The implementer retracted the claim at every site it had already
  reached (`pyproject.toml`'s comment, the new test's docstring,
  `BRIEF-1.md` in place, the REPORT) and rescoped the declaration
  honestly — kept, but justified as explicitness for the
  git-metadata-absent build `[tool.setuptools_scm]`'s own
  `fallback_version` exists to serve, not as "the fix that makes the
  helper ship." The new test asserts the OUTCOME (in the wheel, resolves
  from a scratch venv) rather than the retracted mechanism, so it still
  goes red if the helper ever stops shipping for a real reason.
  **Filed as B054, not decided**: this same unverified-mechanism pattern
  already exists in `test_verdict_schema_is_packaged.py`'s own docstring
  (states it as "measured"; a re-run refutes it) — the test still passes
  and what it asserts is still worth asserting, but its stated negative
  is currently unreachable. Three options recorded, none chosen — a real
  call, correctly left for later rather than patched in passing.
  **Controller note for the record**: no action needed on B054 now (same
  file-don't-build disposition as B050-B053); logging the correction here
  precisely because auto-memory and future readers of this log should see
  the checkpoint-1 entry's "no controller disagreement" line was itself
  wrong, not silently let it stand. The implementer also correctly held
  off on the `evaluate.py:625-676` refactor I endorsed last entry —
  exposing that join with no caller yet would be speculative; it now
  belongs in the wiring cluster (BRIEF-2 §5 step 4) where the new hook
  actually consumes it. Two more live wrapper-vs-job exit-code traps
  self-caught, documented in REPORT §7. Gate now running on the new tip
  `428f69e2`; implementer holding all writes until it returns, same
  discipline as checkpoint 1.
