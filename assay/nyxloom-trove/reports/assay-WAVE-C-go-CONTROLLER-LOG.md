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

- **2026-08-31 (checkpoint 2 — external compaction, fresh successor)** —
  Gate PASS on `428f69e2` confirmed independently by the controller
  (`GATE_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`, the log's own tail
  read directly from `gate2.log`, not taken on the implementer's word
  alone); working tree clean; the sole later commit (`335636b4`) is
  docs-only, so the green claim isn't stretched across an unverified code
  change. 5 commits total on `feature/assay-wave-c-go`
  (`271af037`..`335636b4`). 2 of 7 wave items landed
  (item 2/oracle, item 1's core-model half); F008-A3/A4/A5 correctly
  still `absent` — the implementer explicitly declined to tick any
  acceptance box since nothing calls the oracle yet, naming this exact
  discipline as avoiding "the check whose stated subject was not what
  was checked" defect its own carve-assets README already records once.

  BRIEF-2.md read in full — a clean, cumulative-delta handoff: both prior
  decision asks recorded as `A-394`/`A-395` citing `8fd9dd68` (no
  re-derivation needed), the packaging retraction as `A-396`/`B054`
  (already logged in this file's prior entry), and an ordered 6-step next
  chunk (the `requires_statement_attribution` + `statement_blocks` hook
  → the `evaluate` refusal → the other three adapters' two new members +
  their FakeAdapter/test copies → exposing the single `evaluate.py:
  625-676` key-resolution join per A-385/A-367 rather than duplicating it
  → runner wiring at `runner.py:969-1030` → `external_tools` then
  registration → item 7). This is the checkpoint boundary the E-008
  clause exists for: per the dispatch skill's own rule ("controller
  externally compacts by dispatching a FRESH successor... never a
  resume/fork for this step"), dispatching a NEW fresh Opus agent seeded
  with BRIEF-1+BRIEF-2, not resuming this one — the implementer already
  ended its own turn at the designed handoff boundary, this is not a
  mid-task continuation.

  Dispatched: fresh Opus session, same worktree/branch
  (`.worktrees/assay-wave-c-go`, `feature/assay-wave-c-go`, tip
  `335636b4`), given BRIEF-2 §5's 8-step ordered task list verbatim (the
  hook signature as `A-397`, the `evaluate` refusal, the other three
  adapters + their FakeAdapter copies, the exposed key-resolution join,
  runner wiring, `external_tools`, registration LAST, then item 7's srdm
  covergate qualification), explicitly told not to resurrect the
  retracted packaging claim and to hold acceptance boxes until a real Go
  verdict is provably statement-granular end to end. Next free ids
  restated: A-397, B055.

- **2026-08-31 (checkpoint 3 — gate PASS on `c85c703a`, big milestone)** —
  Confirmed independently: `GATE_EXIT=0`, `ASSAY_REGISTERED_GATE_COMPLETE=1`,
  11/11 phases, tree clean, sole later commit (`4a326ddc`) docs-only.
  This is the wiring commit: `requires_statement_attribution` +
  the new `statement_blocks` hook (signature recorded as `A-397`, three
  rejected alternatives documented), the `A-392` refusal in both
  `evaluate_coverage`/`evaluate_targets` (reading the attribute directly,
  no silent `getattr` default — a default there would quietly reinstate
  the masked-default bug the guard exists to remove), the key-resolution
  join exposed rather than duplicated
  (`evaluate.resolve_coverage_keys`/`_repo_path_by_raw_key`, with a new
  test that would catch a future re-derivation), the runner seam
  (`_attribute_statements_for_lane`), `external_tools = ("go",)`, and the
  Python-side oracle invoker (`adapters/go_stmtpos.py` — validates inputs
  before probing the toolchain so a stale artifact reports as staleness,
  not as a missing tool; identity comes from the helper's own
  `runtime.Version()`, a stronger fact than a second `go version`
  subprocess). Self-hosted lane, topos-qualification and CMRU b006a
  qualification all passed on this newly-wired chain — the phases most
  likely to catch a bad seam did not. Thirteen pre-existing Go tests
  correctly went red under the new guard (no toolchain available for
  them); none deleted or weakened, both repair shortcuts documented at
  the site and filed as `B055` (its own honest fix for one shortcut IS
  item 3/F008-A4). The wrapper-vs-job exit-code trap fired a THIRD
  time this wave, self-caught every time — this is the discipline
  earning its keep, not a recurring problem being tolerated.
  No acceptance box ticked — correct, registration isn't done yet so no
  Go verdict is end-to-end statement-granular.

  **Decision ask DA-3, resolved**: A-395 (the prior generation's own
  ruling) requires the parallel `helpers[]`-present test to be proven
  against a real toolchain, never a mock — but the registered gate's own
  image (`tester-unified:local`) has no Go, so that test cannot run
  inside the gate itself. **Controller ruling: this is not a novel fork —
  Wave A already established the exact pattern for this exact problem**
  (`tests/qualification/test_javascript_real_vitest.py`: skipped in the
  registered gate with a named reason citing DESIGN-GUIDE §10, enabled by
  an env var when the real toolchain is on PATH, run manually with the
  transcript pasted into the REPORT — the A-335 proof pattern for a tool
  the gate's own image cannot carry). Build the Go analogue the same way
  (a `tests/qualification/test_go_*_real.py`, gated on an env var, e.g.
  `ASSAY_GO_QUALIFICATION=1`, requiring `tester-unified-go:local` —
  already built by generation 1 and present in this devcontainer, so
  runnable now) rather than either extending the registered gate's own
  image (unnecessary infrastructure cost for one test) or settling for
  only a frozen `carve-assets/P27-recarve/` probe artifact (weaker:
  doesn't re-run against a live CLI end to end the way the qualification
  pattern does). This resolves DA-3 without inventing anything — it is
  citing an existing, already-committed precedent from this same wave
  sequence.

  Dispatching a fresh Opus successor (generation 3), same worktree/
  branch, tip `4a326ddc`, given BRIEF-1+2+3 plus this DA-3 ruling. Task
  order: register `GoAdapter` at `{"R1"}` (A-394, now unblocked) → item 7
  srdm covergate qualification (F008-A5) → item 3 fixture regen (F008-A4,
  also discharges half of B055) → item 5 go-cover producer vocabulary →
  item 6's remainder using the DA-3-resolved qualification pattern. Next
  free ids: A-398, B056.

- **2026-08-31 (operator cleared the standing `/goal`)** — The autonomous
  `/goal` Stop-hook condition that has governed this whole session
  (Wave B ship → Wave C dispatch → work the sequence through) was
  explicitly cleared by the operator, with a direct instruction instead:
  "finish the work in flight and reach a good checkpoint, write a
  detailed summary and next steps." This changes the controller's
  standing instruction from here on: **no further generation dispatch,
  no review dispatch, no merge/release** — those are now deferred to a
  future session/operator decision, not automatic. Only closing out
  generation 3's already-in-flight gate check remains in scope for this
  session.

  Generation 3 (registration + producer vocabulary, `367bbdf5`) had
  committed its BRIEF-4 handoff (`91b05186`) but the FIRST gate run on
  that state hit a real but trivial snag: `NO_MEASUREMENT/DIRTY_TREE`
  (exit 3) — `BRIEF-4.md` was still untracked at the moment the gate's
  self-hosted lane ran (a process-ordering slip, not a code defect;
  assay correctly refused to self-judge a dirty tree rather than produce
  a false verdict). By the time this was checked, `BRIEF-4.md` was
  already committed and the tree was clean — the controller re-ran the
  gate directly on the clean tip (`91b05186`) rather than dispatching a
  new generation for what is a one-line "run it again" fix. This is
  exactly the "routine BLOCKED item on one scope sub-step" class the
  goal's REPORT-AND-CONTINUE clause always covered — resolving it
  directly, no pause.

  **Re-run gate PASS on `91b05186`** — confirmed directly from the log:
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, all 11 phases
  including `self-hosted-lane-passed`, `topos-qualified`, the full
  `cmru-b006a-qualified` receipt (all four claims R0-R3 PASS), and
  `independent-self-hosting-passed`. This closes out the last in-flight
  work for this session — **checkpoint reached, gate-green, tree clean,
  nothing hanging.** Per the operator's instruction, stopping here: no
  generation 4 dispatch, no review dispatch this session. A detailed
  summary and next-steps handoff was delivered to the operator in chat.
  The 7-item Wave C task list stands at: items 1/2/4 (registration, the
  oracle, the producer vocabulary) done; items 3 (fixture regen), 6
  (helpers[] gate envelope) and 7 (srdm covergate qualification) open,
  with generation 3's BRIEF-4 already carrying the concrete unblocking
  path (the measured `go` shim host-path translation) and the covergate
  question already answered analytically (assay is the correct side per
  A-217, only the run itself is owed). Resume by dispatching a fresh
  generation 4 seeded with BRIEF-1 through BRIEF-4, same worktree/branch,
  tip `91b05186`.

- **2026-09-01 (operator assessment, then "proceed at your discretion")** —
  New session. The operator asked for the summary's next steps to be
  verified against `1-north-star.md` and CIU v8 (`ciu/docs/SPEC-V8.md`,
  `CIU-V8-TESTING-GATE-PROPOSAL.md`), for a disposition of the open
  backlog, and whether major assay work remains; then, after a second
  round of questions about the two tester images and the zipapp,
  authorized the controller to proceed. The full assessment was delivered
  in chat; the parts that bind generation 4 are recorded here so the
  implementer cites a commit, not a conversation.

  **Verified before ruling:** worktree tip `91b05186`, gate PASS per
  BRIEF-4 §7 and the previous entry; decisions at A-400, backlog at B056;
  items 1/2/4 done, 3/6/7 open — matches the code. BRIEF-4 §5's order
  (helpers wiring → fixture regen → srdm run → boxes) stands. What changes
  is the environment the remaining items run in, and F008-A5's wording.

  **DA-4 — F008-A5 is reworded before generation 4 ticks anything.**
  `2-product-definition.md` F008-A5 reads "Qualified against srdm's own Go
  covergate on the same commits, so union fidelity is mechanical rather
  than a review question." B056 (REPORT §17) shows covergate applies the
  exact `for l := start; l <= end` expansion assay removed in this wave,
  and A-217's frozen `collision-colA/colB` pair proves any profile-only
  rule is wrong on at least one of two inputs — so the two tools disagree
  BY CONSTRUCTION and "union fidelity" between them is unattainable, not
  merely unproven. A criterion that cannot be satisfied honestly must not
  stay on the books to be ticked dishonestly. **Ruling:** generation 4
  records **A-401** and replaces the text with: *"Qualified end to end on
  srdm's own tree: a real statement-granular Go R1 verdict produced by the
  shipped CLI inside `tester-unified-go` at a real srdm commit range, and
  every line on which srdm's `covergate` disagrees at the same commits
  classified as extent-expansion (assay correct, A-217/B056) or
  file-absence (covergate's `NoCode`/`Unmeasured` split), with the
  independent hand manifest as the neutral third party where one
  exists."* The acceptance evidence is the qualification transcript plus
  the classified table, not a parity number.

  **DA-5 — environment ruling: BRIEF-4 §3's `go` shim is RETIRED before
  it was built.** Measured by the controller, 2026-09-01:
  ```text
  $ docker run --rm --network=none tester-unified-go:local sh -c \
      'command -v python3; grep PRETTY_NAME /etc/os-release; go version; id -u'
  /usr/bin/python3
  PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
  go version go1.25.14 linux/amd64
  1003
  $ docker run --rm --network=none tester-unified-go:local python3 -c '...'
  3.13.5  no-pip  venv  no-ensurepip
  ```
  `assay/pyproject.toml` declares `requires-python = ">=3.11"`. So the
  shipped zipapp runs inside the Go gate image TODAY, with no image
  change: the interpreter is inherited from `golang:1.25`'s trixie base
  (the Dockerfile itself installs nothing — I had told the operator the
  opposite conclusion from reading the Dockerfile without running the
  image; corrected here, A-334's lesson one more time). There is no pip
  and no ensurepip, so the zipapp is the ONLY install path — which is
  B003's own stated reason to exist, now with its first non-Python
  consumer shape. **Ruling:** the remaining items run as the consumer
  would: assay INSIDE `tester-unified-go:local`, beside the real `go`,
  judging a bind-mounted tree. Build the in-repo zipapp at the judged
  commit with the release's own builder (`gate/distribution/
  build_release.py`), mount the worktree the way
  `tools/tester-unified-gate.sh:588-602` already mounts it into
  `tester-unified` (host root via `docker inspect "$HOSTNAME"`, the same
  derivation srdm's `tools/gate.sh:48-61` uses), run `python3 <pyz> run
  <lane> --file ... --verdict-json ...` inside the container, and read the
  verdict on the cockpit. The DA-3 qualification test
  (`tests/qualification/test_go_*_real.py`, env-var gated, modelled on
  `test_javascript_real_vitest.py`) drives exactly that and asserts on the
  verdict document. BRIEF-4 §3's two unproven seams (reservation survival
  across a shim mount; uid 1003 writing into a shim-mounted snapshot) are
  moot — the snapshot is a container-local `TemporaryDirectory`; the one
  seam left is uid 1003 reading the mounted tree and writing the verdict
  path, which the gate script solves the same way for `tester-unified`.
  Record as **A-402** with the measured facts. Two consequences: (a)
  A-O04's stated blocker ("whether Python enters srdm's toolchain/
  container") is void by inheritance — adoption stays srdm's call, and the
  controller has filed the fact in srdm's own backlog (below); (b)
  `tester-unified-go/Dockerfile` has no `apt-get` layer at all, so do NOT
  add one for a package that is already present — add a header paragraph
  recording the inherited interpreter and citing A-402, and make the
  qualification test assert `python3 --version` inside the image so a
  base-image change goes RED instead of silently removing the judge's
  interpreter (assert the outcome, not the mechanism: A-396's shape).

  **DA-6 — "the same commits" for F008-A5.** srdm is a subdirectory of
  this monorepo (`git -C shared-ramdisk-depot-manager rev-parse
  --show-toplevel` → `/workspaces/vbpub`), and its own backlog's first
  entry records the exact differential that memory calls "covergate
  silently skipped P14": `tools/gate.sh coverage` reports 254/258 at both
  `10b174a5` (before P10+P14) and `83c2ff79` (after P14's merge), while
  P14's ~1,500 new lines under `internal/power/*`, `internal/opctl/
  update.go`, `internal/publish/sizing.go` contribute to neither side.
  **Preferred run:** a monorepo lane — `cwd = "shared-ramdisk-depot-
  manager"` (B043), `source_roots = ["shared-ramdisk-depot-manager/
  internal"]`, argv = srdm's own `gate.sh:105` line (`go test ./...
  -count=1 -coverpkg=./... -covermode=atomic -coverprofile=...` —
  `-coverpkg=./...` is load-bearing per B056), `format = "go-cover"`,
  `producer = "go-test"`, base `10b174a5`, HEAD `83c2ff79`; then covergate
  on the same profile at the same commits inside the same container
  (`go run ./tools/covergate -profile ... -base 10b174a5 -source internal
  -fail-under 75`, exit 0/1/2/3). srdm's tree stays clean: the lane file
  lives outside it (or under a gitignored path) and this wave commits
  NOTHING into `shared-ramdisk-depot-manager/`. Fallback only if the range
  proves too costly inside the budget: a throwaway repository built from
  `carve-assets/P27/fixture/commit{1,2}` with `manifest/
  calc-statements.json` as the third party (BRIEF-4 §4). Either way,
  classify every difference per BRIEF-4 §4(b) BEFORE naming a side; a
  file-absence difference is the P14 mechanism, not the extent one.

  **DA-7 — cross-repo items are the controller's, not generation 4's.**
  Done this session, committed on `main`: (1) srdm's `nyxloom-trove/
  backlog.md` — a note on its P14 entry locating the mechanism (REPORT
  §17: `Evaluate`'s `fc == nil` branch, `NoCode` vs `Unmeasured` split by
  `HasExecutableCode`), a new entry for B056's over-approximation, and a
  note on its "Revisit `tools/covergate`" entry that the Go adapter now
  exists and the interpreter is already in its gate image; (2) the dstdns
  JavaScript-lane adoption brief owed since the 3.1.0 review (§7 of
  `assay-3.1-js-adapter-design-review-2026-08-30.md`), written as
  `reports/assay-dstdns-js-lane-adoption-brief-2026-09-01.md` and dropped
  into dstdns's gitignored `.assay-inbox/` beside the unacted 4.0.0
  `release.json` (dstdns pins 3.1.0, has NO `javascript` lane, and its
  controller deferred 3.2.0 until P152/P154/P161 close).

  **Corrections to the controller's own chat assessment, for the record:**
  (1) the Python-in-the-Go-image conclusion above; (2) I listed run-gate
  RG-25/RG-26 as open — both shipped in run-gate 23.1.0 on 2026-08-31
  (`run-gate-project/CHANGES.md` lines 71-72: `doctor/--check-env`
  toolchain fitness via `assay lanes --json`, and `--base REF` →
  `--request-base`), and 23.2.2 (RG-31, 2026-09-01) fixed the toolchain
  check's `--worktree` routing. B019 is therefore already usable from the
  current gate, which the dstdns brief now says.

  **The operator's image question, answered: no consolidation.**
  `tester-unified:local` is 8.94 GB (the Python 3.14 closure of four
  projects, built on the mdt devcontainer image); `tester-unified-go:local`
  is 1.01 GB (`golang:1.25` + a gate user + `go build std`). One image
  would make every Go pin bump rebuild and re-risk the Python projects'
  gate and every closure change rebuild srdm's, which is A-043's reasoning
  unchanged, and srdm's privileged e2e stage would inherit 8 GB it never
  reads. The judge needs an INTERPRETER, not a toolchain — stdlib-only
  (A-005) is the bet that makes "install into an arbitrary image" free —
  and the interpreter is already there. "assay without Python" is not a
  thing the zipapp provides: a `.pyz` removes pip/venv/package-manager,
  not the interpreter; the only Python-free shapes are a frozen binary
  (a second, platform-bound distribution channel next to the hash-bound
  wheel — B009's "second channel is a second thing to drift") or a
  rewrite, which is the srdm fork the north star exists to end. Inside
  vbpub the gate installs the IN-REPO code at the judged commit (a wheel
  built from the hash-checked five-wheel closure into a separate
  `run-venv`, `tester-unified-gate.sh:8-17`); consumers install the
  RELEASED artifact by sha256. Generation 4's in-image run uses the
  in-repo zipapp for the same reason: the judged commit judges itself.

  **Generation 4 dispatched:** fresh Opus session (never a fork), same
  worktree/branch, tip `91b05186`, seeded with BRIEF-1..4 plus this entry.
  Task order (supersedes BRIEF-4 §5 step 1; steps 2-5 keep their order):
  (0) record A-401/A-402, edit F008-A5's text; (1) the in-image harness —
  build the pyz, mount, prove `python3 --version` and `python3 <pyz>
  --version` inside `tester-unified-go:local`, then one real R1 lane —
  this REPLACES "prove the shim"; (2) `helpers[]` wiring + the DA-3
  qualification test driven through that harness; (3) F008-A4 fixture
  regeneration inside the image; (4) F008-A5 per DA-6; (5) the acceptance
  boxes, each citing real evidence. Next free ids: **A-401**, **B057**.
  Checkpoint clause unchanged (BRIEF-5 at the next coherent boundary).
  Review, merge and release follow on completion, same terms as Wave B.

- **2026-09-01 (generation 4 returned — checkpoint 5, gate PASS on
  `9714361c`, one blocker filed as DA-8; ruled here)** — Generation 4 cut
  at a green gate after ~53 minutes and 240 tool calls: tip `524dd16c`,
  tree clean, BRIEF-5 committed. **Gate verified by the controller from
  the run-7 log directly** (`gate-run7.log`: wheel
  `assay-4.0.1.dev29+g9714361c` installed, phases through
  `self-hosted-lane-passed` / `cmru-b006a-qualified` /
  `independent-self-hosting-passed`, `ASSAY_REGISTERED_GATE_COMPLETE=1`,
  `GATE_EXIT=0`, zero hits for `FAILED|DIRTY_TREE|Traceback`), and the two
  commits after it confirmed docs-only by `git diff --stat 9714361c..HEAD`
  (BRIEF-5, LOG, REPORT, and the `tester-unified-go/Dockerfile` header
  comment). Landed: A-401/A-402 recorded and F008-A5 reworded per DA-4
  (`2f0cd223`); **A-403, a real shipping defect found by building the
  harness rather than reading** (`8d7f8740`: the zipapp could not reach
  the Go oracle because `go run .` needs a real directory and a `.pyz`
  keeps package data as zip members — every Go consumer's only install
  shape was the broken one; now staged through `importlib.resources` on
  one path for every install shape, with a regression test against the
  real built artifact); `helpers[]`'s first producer (`77d9d6b9`, three
  sub-decisions with rejected alternatives in REPORT §28, `test_cli_run.
  py:406` untouched); the DA-3 qualification test `tests/qualification/
  test_go_r1_real.py` (real zipapp, real `go test`, real oracle, in-image:
  a PASS with `helpers[{role: statement-positions, identity: "go version
  go1.25.14"}]`, `judge_provenance.artifact: "zipapp"`, `executable: 1`
  where the removed rule said 3, and a paired FAIL naming `return -1`),
  which also asserts `python3 --version` inside the image; DA-5's
  Dockerfile header (`21708344`); B055's third box. No acceptance box
  ticked — correct, for the reason B057 states.

  **B057 / DA-8 — the blocker, measured in-image:** a Go cover profile
  keys records by IMPORT PATH (`<module path>/<dir>/<file>`), `git diff`
  by repo-relative path; `GoAdapter.module_path` bridges them and nothing
  sets it through the CLI (`_built_in_registry` builds `GoAdapter()` with
  `""`, `_KNOWN_JUDGE_FIELDS` has no key, `assay run` has no flag). So
  every real Go module — srdm included, whose `srdm/internal/...` keys
  would resolve under no source root — refuses `ERROR`/
  `UNREADABLE_ARTIFACT` with a message that blames staleness. The control
  half (with `module_path` supplied, the oracle returns `{6}`/`{11}` where
  naive expansion says `{5,6,7}`/`{10,11,12}`) proves the defect is only
  the missing value. Generation 4 laid out three shapes (REPORT §27) and
  correctly did not pick: a declared `judge.module_path` (A-328's
  precedent), derivation from `go.mod` (§4.2a's preference), or a
  per-lane registry. Note for the record that shape 1 is not cheaper than
  it looks: the field lives on a registry SINGLETON, so even a declared
  key needs a per-lane path into the adapter — the seam cost is shared by
  shapes 1 and 2, and only the SOURCE of the value differs.

  **Ruling (DA-8): derive from the snapshot's own `go.mod`. No declared
  key. The registry stays as it is.** This is doctrine applied, not a new
  product call: `docs/DESIGN-GUIDE.md` §5 already lists covergate's own
  `-source internal -module srdm` as defaults anti-pattern #1 — "a literal
  standing in for a fact that lives authoritatively in the project's
  layout … A library cannot ship any of them; it must read them." A
  declared `judge.module_path` is that flag under a new name. A-007's
  selection rule asks whose fact it is: the coverage FORMAT is the lane's
  (its argv chose it), so it is declared and cross-checked; the module
  path is `go.mod`'s, so it is derived. A-328 is not a counter-precedent:
  `base_source` delegates a fact that is genuinely the CALLER's (which
  base to compare against), and A-328's own reasoning — refuse precedence
  between two sources of one fact, because whichever loses is config
  nothing reads — argues AGAINST a declared key beside derivation; a
  restatement-that-must-agree would catch nothing, since `go test` read
  the same `go.mod`. Shape 3 is rejected on generation 3's own
  measurement (REPORT §20): `_built_in_registry` is read by the inventory
  (A-349), the docs gate (A-400) and every language resolution.

  **The seam, shape ruled, details the implementer's (A-084, as A-239 →
  A-397):** ONE new, narrow, language-free protocol member on
  `LanguageAdapter`, called by the core at the lane boundary where it
  already holds `repo_top` and `project_root` (`runner.
  _attribute_statements_for_lane` / the `resolve_coverage_keys` call
  site), returning the adapter to use for THIS lane: `GoAdapter` returns a
  copy with `module_path` derived; every other adapter and every
  `FakeAdapter` (BRIEF-1 §3 item 4 lists the sites) returns `self`. Name,
  exact signature and the rejected alternatives go in the decision row
  (**A-404**), citing this entry. Constraints that are part of the ruling:
  (a) derive from the nearest `go.mod` at or above `project_root` within
  `repo_top`, read from the SNAPSHOT (commit-bound), parsing only the
  `module` directive — its grammar per the Go reference (unquoted and
  quoted forms, comments), cited; no general `go.mod` parser; (b) no
  `go.mod` at or above `project_root` → a named refusal from the EXISTING
  closed vocabulary (a Go judge declared over a directory that is not a
  Go module is a lane/tree mismatch; the implementer picks the honest
  existing code and records why — a new reason code is an enum widening,
  which is a schema cut and out of scope; if no existing code fits, that
  is a decision ask, never a v10); (c) a profile key not under the derived
  module path at a `module_path + "/"` boundary → refuse with a message
  naming the key, the derived module path and the `go.mod` it came from —
  this REPLACES B057's misattributed "not the same revision" message
  (B057's second half is FIX, not keep); (d) nested modules never appear
  in `go test ./...`'s own output and a `cwd` above several modules
  (`go.work`) surfaces as (c) — document "one Go module per lane; `cwd`
  is the module root" in CONSUMERS.md, no workspace support this wave;
  (e) `_built_in_registry`, `assay lanes --json` and the docs-gate
  derivation stay untouched.

  **Proof required before any box is ticked:** the B057 control run as a
  real `python3 <pyz> run …` PASS inside the image (BRIEF-5 §4: the
  qualification's assertions move to the CLI entry point unchanged — if
  any assertion has to change, that is a finding); a planted `go.mod`
  whose `module` directive disagrees with the profile's keys → refusal
  (c) naming both; a Go lane whose `cwd` has no `go.mod` → refusal (b); a
  unit test for the directive parser (quoted form, comment line); and a
  statement of which existing assertion catches a mutant that skips
  derivation (`module_path` left `""`).

  **Also ruled:** BRIEF-5 §3's unfiled builder property
  (`build_release.py` leaves `zipapp-staging/` beside `--outdir` and never
  removes it; with `--outdir assay/dist` that is an untracked directory
  the self-hosted lane refuses as `DIRTY_TREE`) IS filed, as **B058**, one
  paragraph — an unfiled hazard that can turn the project's own gate red
  is what the backlog is for. Generation 4's judgment call was reasonable;
  the disagreement is on cost, not substance.

  **Generation 5 dispatched:** fresh Opus session (never a fork), same
  worktree/branch, tip `524dd16c` (gate-verified tip `9714361c` + two
  docs-only commits), seeded with BRIEF-1..5 plus this entry. Task order:
  (1) DA-8 per the ruling above → gate; (2) F008-A4 fixture regeneration
  through the in-image harness (B055's first box); (3) F008-A5 per DA-6,
  classification first; (4) F008-A3's tick with the CLI-form transcript;
  (5) the other boxes, each citing something run; (6) file B058. Next
  free ids: **A-404**, **B058**. Checkpoint clause unchanged (BRIEF-6).
  On completion: fresh adversarial reviewer, 3-round cap, then merge →
  `cmru release` (MINOR) → deploy → dstdns notify, same terms as Wave B.

- **2026-09-02 (backlog id collision with main; dstdns adopted the JS
  lane overnight)** — The operator flagged two upstream findings filed to
  assay's backlog as B053/B054. Confirmed: main commit `a050a467`
  (2026-09-02 00:27, provenance `dstdns/docs/plan-tooling-adoption-and-
  hygiene-2026-09-01.md` §3) filed **B053** (an `ERROR`-outcome verdict's
  detailed message is never surfaced — not stdout, not stderr, not the
  verdict JSON; only `cli.py`'s one wrapped call prints it) and **B054**
  (a never-executed file matched by `coverage.include` can make
  `@vitest/coverage-istanbul` emit a self-contradictory `branchMap`, and
  `UNREADABLE_ARTIFACT` refuses the WHOLE verdict rather than the one
  file). Both came from dstdns's first `javascript` lane — which means the
  brief written yesterday was picked up the same night: dstdns's plan doc
  cites it by path, `run-gate.toml` now pins `assay-4.0.0.pyz`, and
  `assay.toml` carries `[lanes.ui_unit]` with `language = "javascript"`
  and `link_paths`. Two of the three "consumer adoption" gaps from the
  assessment closed in one day; the third (srdm, A-O04) waits on F008-A5.

  **The collision.** The Wave C branch already carries its own B053..B058
  (lit.go laundering, the inert package-data stanza, the downgraded-adapter
  tests, covergate's over-approximation, the module-path blocker, the
  `zipapp-staging/` leftover) — ~108 references in 24 tracked files at
  generation 5's current tip `1885d64e`. **Ruling: main's ids win** (the
  estate's precedent is the CIU-55 shift at the ciu backlog wave's
  checkpoint 2); the branch renumbers B053→B055, B054→B056, B055→B057,
  B056→B058, B057→B059, B058→B060, in descending order, historical briefs
  and logs included (an id that now resolves to a different entry is worse
  than an edited record), as its own `chore(assay):` commit before the
  next gate, with a renumbering note at the top of the new B055 and a LOG
  entry carrying the map; main's two entries are NOT copied onto the
  branch — the merge brings them and the controller resolves the
  `4-backlog.md` conflict. Sent to generation 5 via `SendMessage` (resume,
  not a fresh spawn). Next free backlog id on the branch becomes B061.

  **Fold-in.** Main's B053 is a second, independent reproduction of what
  generation 4's B057 (→B059) transcript showed on the Go path the same
  day — the bare three-line summary, message recoverable only by probing
  the library. Noted on main's B053 as priority evidence. Consequence for
  DA-8's proof (c): the message is provable at the library boundary and
  only the reason code through the CLI; generation 5 told to prove both
  where each is observable and say so in the REPORT. **Neither main
  finding is folded into Wave C** (scope is Go; these are the CLI's error
  surface and the JS parser's per-file isolation): both pair with B049 as
  the **post-Wave-C consumer-diagnostics patch wave** — three findings from
  the first JS consumer's first week, all schema-free in their narrow
  form (B053's stderr option; B049's `st_nlink` check; B054's per-file
  `NO_MEASUREMENT` isolation or, failing a ruling, the documented
  `coverage.include` constraint). The srdm backlog's pointer to "B056" was
  corrected on main to name the entry by title and its post-merge id.

- **2026-09-02 (generation 5 returned — DA-8 landed, F008-A3 proven, ids
  renumbered; generation 6 dispatched for A4/A5)** — Generation 5 cut at
  a green gate after ~64 minutes and 286 tool calls: tip `86b4efae`, tree
  clean, BRIEF-6 committed. **Gate verified by the controller from the
  run-9 log directly** (`gate-run9.log`: wheel
  `assay-4.0.1.dev35+gdd1e2c46` installed, phases through
  `self-hosted-lane-passed` / `cmru-b006a-qualified` /
  `independent-self-hosting-passed`, `ASSAY_REGISTERED_GATE_COMPLETE=1`,
  `GATE_EXIT=0`, zero hits for `FAILED|DIRTY_TREE|Traceback`); the sole
  later commit confirmed docs-only by `git diff --stat dd1e2c46..HEAD`
  (three trove report files). Landed: **A-404** — the seam is
  `LanguageAdapter.for_project(*, repo_top, project_root) ->
  LanguageAdapter`, keyword-only, called ONCE from `runner.evaluate_r1`
  after `repo_top` resolves and before anything reads the profile, the
  local name rebound so the key join, the statement oracle and both
  evaluate modes see one object; every adapter but Go returns `self`
  (`SqlAdapter` keeps its `_UNREACHABLE` raise — a documented deviation I
  accept: the call site is only reached from R1 paths and SQL is R0/R2).
  `adapters/go_modfile.py` parses only the `module` directive, with the
  lexical rules read from go1.25.14's own vendored `modfile` inside the
  image rather than the Modules Reference (which caught that a backquoted
  module path is NOT valid `cmd/go` input). Refusals from the existing
  vocabulary: no `go.mod` → `BAD_LANE_CONFIG`; a key outside the derived
  module → `UNREADABLE_ARTIFACT` naming key, module path and `go.mod`,
  replacing the misattributed staleness message. 42 new tests; suite 3902
  passed / 18 skipped; the qualification now drives `python3 <pyz> run …`
  in-image (5 passed, every inherited assertion unchanged — BRIEF-5 §4's
  prediction held). **F008-A3 ticked `proven`** with four cited tests, two
  needing no toolchain. The renumbering (`e7eb5241`) landed as its own
  commit and was re-gated rather than assumed inert; the 21 remaining
  `B053|B054` hits on the branch are the map and explicit citations of
  MAIN's B053 (verified by the controller: backlog 2, BRIEF-6 3, LOG 9,
  REPORT 5, the qualification test 2). Main's B053 fold-in handled as
  told: the refusal-(c) proof split, the overclaiming test renamed
  `…_refuses_through_the_cli`, message asserted at the library boundary.
  Stale user-facing prose (CLI docstring, `run --help`, README still said
  Go was refused) fixed in passing — a class worth carrying: tests read
  the registry, not the help string. B060 filed. Next free ids: A-405,
  B061. **Process note generation 5 volunteered, recorded for the
  reviewer:** three bulk edits (the `for_project` member into the Python
  and JavaScript adapters, the appended REPORT sections, and the
  108-reference renumbering) were done with short Python rewrite scripts
  rather than per-file edits, against the operator's standing
  Edit/apply_patch directive; the renumbering warranted it (a partial
  rename across 25 files is worse than none, end state verified with
  `git grep`, reasoning in the commit and LOG), the other two did not.
  Content reviewed and gate-green; the reviewer should read those three
  diffs as diffs, not trust the method. Generation 6's prompt restates
  the directive.

  **DA-9 — BRIEF-6 §3's lane-shape correction is ACCEPTED; DA-6's
  substance stands.** Generation 5 found, from the key join's own
  arithmetic (`normalize_coverage_key` then `_to_repo_relative_key(…,
  project_prefix)` with `project_prefix` = `project_root` relative to
  `repo_top`), that for a Go lane the PROJECT ROOT must be the module root
  — true before A-404, and what A-404 (d) already says. DA-6's `cwd =
  "shared-ramdisk-depot-manager"` + `source_roots = ["shared-ramdisk-
  depot-manager/internal"]` implied a project root at the repository top,
  where there is no `go.mod` and where `srdm/internal/…` would resolve to
  a nonexistent path. Corrected shape: the lane file inside
  `shared-ramdisk-depot-manager/` so `project_root` is the module root,
  `source_roots = ["internal"]`, **no `cwd`**, srdm's own `gate.sh:105`
  argv verbatim, base `10b174a5`. Facts re-checked by the controller:
  `10b174a5` = "Merge cgroup-profiler …", `83c2ff79` = "Merge P14: srdm
  update — the ordered cluster update", ancestor relation holds, and
  their srdm-subtree diff is 32 files / 3917 insertions including
  `internal/power/*`, `internal/opctl/update.go` — i.e. the pair isolates
  exactly P14, the package covergate's floor never saw; P10 merged later
  (`3ac61813`). `go.mod` declares `module srdm`. **Harness ruling** for the
  one thing DA-6 did not say: `assay run` judges HEAD and the shared
  checkout's HEAD may not move, so generation 6 builds a synthetic
  two-commit repository INSIDE the container — `git archive 10b174a5
  shared-ramdisk-depot-manager` as commit 1, `git archive 83c2ff79 …` over
  it as commit 2, from the bind-mounted host repo read-only — and judges
  it with base = commit 1; the changed lines are then P14's own. covergate
  runs in that same checkout on the same profile (`-base <commit 1>
  -module srdm -source internal -fail-under 75`). Every difference is
  classified extent-expansion vs file-absence BEFORE a side is named,
  with `carve-assets/P27/fixture/manifest/calc-statements.json` as the
  third party where one exists. Nothing is committed under
  `shared-ramdisk-depot-manager/`.

  **B057's remaining box** (the old B055's `_PreOracleGoAdapter` canary
  shortcut): close it only if F008-A4's regeneration makes it fall out;
  otherwise leave it as accepted-and-documented with the reason (the
  registered gate's image has no Go), the B024/B026 precedent. Not a
  blocker for the wave.

  **Generation 6 dispatched:** fresh Opus session (never a fork), same
  worktree/branch, tip `86b4efae` (gate-verified `dd1e2c46` + one
  docs-only commit), seeded with BRIEF-1..6 plus this entry. Task order:
  (1) F008-A4 through the in-image harness — regenerate the fixture bytes
  from real toolchain output AND re-derive every asserted line set from
  the oracle in the same change (A-234's warning), discharging B057's
  first box; (2) F008-A5 per DA-6 as corrected by DA-9; (3) the A4/A5
  boxes, each citing something run; (4) B057's last box per above. Then
  return — on completion the fresh adversarial reviewer (3-round cap),
  merge, `cmru release` (MINOR), deploy, dstdns notify.

- **2026-09-02 (generation 6 returned — Wave C scope COMPLETE; reviewer
  dispatched)** — Generation 6 finished the whole list in ~48 minutes and
  164 tool calls: tip `d938ab8c`, tree clean. **Gate verified by the
  controller from the run-10 log directly** (`gate-run10.log`: wheel
  `assay-4.0.1.dev39+g3355d238`, phases through `self-hosted-lane-passed`
  / `cmru-b006a-qualified` / `independent-self-hosting-passed`,
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, `GATE_EXIT=0`, zero red hits); the
  sole later commit confirmed docs-only (BRIEF-7, LOG, REPORT §43);
  `git diff --stat main...HEAD -- shared-ramdisk-depot-manager` is EMPTY
  (nothing committed into srdm, as ruled); `2-product-definition.md` F008
  reads `shipped` with A1–A5 `proven`, `3-roadmap.md` M6 reads `done`;
  B061 filed. Branch: 26 commits, 71 files, +13147/−300 against `main`.

  What landed: **F008-A4** (`394c6cc2`) — a committed in-image
  regeneration script (`carve-assets/P27-recarve/regenerate-fixtures.sh`,
  go1.25.14, `--network=none`) produced `hello.out`, both canary profiles
  AND a `fixture-oracle.json`; the tests join them with the production
  `attribute_statements`, so expectations are the oracle's, not
  hand-typed (union-fidelity's sets went `{29,30}`/`{36,37}` →
  `{33}`/`{39}`, with a control test asserting the naive expansion of the
  SAME real bytes is `{32,33,34}`/`{38,39,40}` — the concrete form of
  A-234's warning); B057 closed on all three boxes, `_PreOracleGoAdapter`
  deleted (DA-9's condition was met). **B061** (`875382d2`) — **a real
  defect found by the srdm run on its first pass**: `-coverpkg=./...`
  emits one record per test binary per block (20 per block in srdm), the
  statement join keyed a dict by extent so the LAST record won, and
  covered blocks became `missing` — 255 lines reported uncovered where the
  profile's own line-level fold says 45; fixed by folding executed-wins
  before any count is read. **F008-A5** (`3355d238`) — the synthetic
  two-commit repo built in-container from `git archive 10b174a5|83c2ff79`,
  lane at the module root per DA-9, nothing declaring the module path:
  assay PASS 418/394 (94.3%), covergate PASS 684/639 (93.4%) on the
  byte-identical profile; classified BEFORE naming a side — 266 lines
  extent-expansion (each begins no statement), file-absence axis EMPTY,
  no third category; both tools' rules re-implemented and reproducing
  their own printed numbers. **Two things the controller notes:** (a) the
  B061 catch is a direct product of DA-6's classify-first rule — the
  denominator fit the extent-expansion prediction (418 < 684) while the
  covered RATIO did not (39.0% vs 93.4%), and stopping at the denominator
  would have shipped B061 as "assay is stricter, by design"; (b) memory's
  "covergate silently skipped P14's package" does NOT reproduce at this
  commit pair — the file-absence axis is empty — so srdm's own backlog
  entry needs that fact (controller's, after the merge; not a Wave C
  item). Edits were per-file via the Edit tool; two Python helpers were
  analysis-only and are noted in the LOG. `decisions.md` untouched (next
  free A-405); next backlog id B062; no open decision asks.

  **Fresh adversarial reviewer dispatched** (fresh Opus, never a fork,
  3-round cap as in Wave B), blind phase first over `main...d938ab8c`,
  then reconciliation against the seven briefs, the LOG and the REPORT.
  On ACCEPT: merge `--no-ff` to `main` resolving the `4-backlog.md`
  collision (main's B053/B054 + the branch's B055–B061), then `cmru
  release --project assay` (MINOR — no `!` commit on the branch,
  confirmed), deploy to the devcontainer venv, dstdns notify.

- **2026-09-02 (review round 1: ACCEPT-conditional — 2 blockers, 7
  should-fixes, 2 decision asks; DA-R1/DA-R2 ruled; fix generation
  dispatched)** — The reviewer (fresh Opus, blind phase first, ~36 min,
  195 tool calls) independently reproduced essentially everything: the
  oracle against all eight witnesses (collision pair `{4,6}`/`{4,5}`),
  the transcription diffed function by function against the real
  `cover.go`, `regenerate-fixtures.sh` re-run byte-identical, the srdm
  two-commit repo rebuilt and both tools re-run with ITS OWN classifier
  (684/639 vs 418/394, 266 lines all extent-expansion, zero assay-only,
  file-absence axis positively empty — all 12 changed non-test files in
  the profile), the registered gate on the tip PASS, the Go
  qualification 5/5, the full suite 3908 (the implementer's 3905+3 exact),
  wire schema untouched, the 25-file renumber provably pure, eight
  subject mutations reddening the right tests. Review file: scratchpad
  `assay-WAVE-C-go-REVIEW-round1.md` (417 lines; the fix generation
  commits it verbatim onto the branch as its first commit so the
  fix-verification round reads it from the tree). Worktree left exactly
  as found.

  **BLOCKER 1 — column 0.** The reviewer built Go's own canonical
  `//line`-directive fixture (`cmd/cover/cover_test.go`'s
  `lineDupContents`) in-image; the real toolchain emits
  `linedup.go:100.0,102.0 1 50`. The branch refuses the WHOLE artifact
  (`go_cover.py:230` "column number 0 … is not positive"; the same false
  invariant in `model.py:221` and `statement_attribution.py:108`, "a
  1-based source position is never below 1") — a guessed fact about
  `cmd/cover` that `cmd/cover` disproves (`cover.go:1053-1058`'s own
  comment, issues #27530/#30746), the exact class this wave exists to
  remove; `main` parsed those bytes but expanded them to lines 5–105 of
  a 22-line file, so "restore main" is not the ask either. Good news
  inside it: the oracle reproduces all nine extents and `num_stmts`
  exactly, which PROVES the `dedup` replication REPORT §5 item 4 had
  left unproven. Blast radius: goyacc/peg/ragel-style generated Go and
  anything hand-emitting `//line`; one generated file poisons the lane.

  **DA-R2 ruled: shape (ii), per-file, on the north star's own rule.**
  `1-north-star.md`: "pre-existing code outside the diff is invisible to
  the verdict by construction, because it is not what is under review",
  and "0/0 is never 100%". So: (1) the parser ACCEPTS column 0 as the
  true fact it is and marks the FILE as `//line`-remapped (any record
  with a zero column in that file; positions in such a file cannot be
  told physical from virtual, so the flag is per file, conservative);
  the three invariant sites state the true rule with the `go/token`
  citation; (2) at evaluation, a flagged file with NO lines in the
  judged set (changed lines after `source_roots`/`is_test_path`
  filtering, or declared targets in whole-target mode) is IGNORED —
  it is not under review and must not take the lane down; (3) a flagged
  file WITH lines in the judged set → refuse, from the existing closed
  vocabulary (the implementer picks the honest code and records why —
  no new code), with a message naming `//line` as the cause, the file,
  and the remedy (generated sources belong outside `source_roots`);
  never a silent 0/0 (shape (i) rejected: remapped virtual lines
  intersect no physical diff line and the file would measure nothing
  while looking judged). Shape (iii) — whole-lane refusal with a correct
  message — is NOT ruled: it would leave every Go project with one
  generated file unable to use R1 at all, on the eve of the first Go
  consumer, and the per-file seam is a bounded change (parser flag,
  join skip, one evaluate check). **The same principle is hereby the
  ruling for main's B054** (an istanbul record for a never-executed file
  outside the judged set must not refuse the lane) — applied to the
  istanbul parser in the post-Wave-C patch wave, not here. Witness: the
  `linedup` profile + oracle document + provenance into
  `carve-assets/P27-recarve/` (should-fix 7), tests for all three
  outcomes (ignored / refused-named / normal file unaffected), CONSUMERS'
  Go section gains the limit beside the other four.

  **BLOCKER 2** — `registry.py:41-44` still says "no real entry here
  ever exercises one" about `external_tools`; Go declares `("go",)`.
  Rewrite as `cli.py:328-347` was, citing A-394/B047 item 2; fix the
  adjacent pre-Wave-C staleness (`registry.py:29-32`, "no entry at all
  for Go", already false since SQL/JS registered) in the same edit.

  **DA-R1 ruled: REFUSE, never vacuous, for a `requires_statement_
  attribution` adapter.** The reviewer's reachable case is decisive:
  `judge.language` and `judge.coverage.format` are independent
  (`config.py:2169-2172`), so a Go lane may declare `format = "lcov"`,
  and lcov converted from a Go coverprofile carries exactly the naive
  block expansion A-392 exists to refuse — yet `runner.
  _attribute_statements_for_lane` (`runner.py:912-920`) marks a
  block-less profile `statement_attributed=True`, no oracle, no helper,
  and the guard passes. There is no honest Go profile without block
  extents. Ruling: (1) a `requires_statement_attribution` adapter with
  a format that carries no block extents is refused at CONFIG LOAD,
  `ERROR`/`BAD_LANE_CONFIG`, naming the language, the format, and the
  one format that can be attributed (`go-cover`, producers `go-test`
  and `covdata`); (2) the vacuous branch is DELETED for such adapters —
  a block-bearing format whose parsed profile has no block-bearing files
  is either the empty profile (must terminate in `NO_MEASUREMENT`/
  `EMPTY_COVERAGE`, proven by a test that also asserts no `helpers`
  entry and no PASS) or a contradiction (refused); the branch stays
  only for adapters that do not require attribution, and its test is
  rewritten to say so; (3) the docstring's "such a profile is already
  statement truth" is retracted at the site.

  **Should-fixes 1, 2, 4, 5, 6, 7: all applied**, none deferred — 6
  (`numStmts` inconsistency across repeated records) is IMPLEMENTED as
  a refusal in the fold, not filed: the fold now transcribes `x/tools/
  cover/profile.go`'s merge (should-fix 4's citation, `Count |=` for
  `set`, `+=` otherwise), and that same loop refuses `inconsistent
  NumStmt`; transcribing half of a cited rule is the pattern this wave
  keeps catching. 5 (a bare `ValueError` escaping `go_cover.parse`, no
  verdict written, reservation never closed) is a real consumer-facing
  crash and `main` accepted those bytes — fix via `_malformed` at the
  construction site. 1 re-points one of the two `test_cli_run.py`
  decoys at a genuinely unregistered language and labels the other as
  the registered-at-another-rigor control, and corrects `cli.py:325-326`.
  2 fixes the three `go.mod` divergences (trailing slash, `module /`,
  quote-in-ident) to match the real parser, with tests.

  **Fix generation (7) dispatched:** fresh Opus session, same worktree/
  branch, tip `d938ab8c`, seeded with BRIEF-1..7, the review file and
  this entry. Order: commit the review verbatim → blocker 2 → blocker 1
  per DA-R2 (with the `linedup` witness) → DA-R1 → should-fixes 1/2/4/5/
  6 → docs/CHANGES/LOG/REPORT → gate → return. Decisions: **A-405**
  (DA-R2, the `//line` rule), **A-406** (DA-R1, no vacuous
  attribution), both citing this entry. Next backlog id **B062**. Then
  the SAME reviewer is resumed for fix-verification (round 2 of 3).

- **2026-09-02 (fix generation returned — gate run 11 PASS; reviewer
  resumed for round 2)** — Generation 7 landed everything in ~50 minutes
  and 274 tool calls: six commits on `d938ab8c` (`210812f6` the review
  verbatim — controller confirmed sha256-identical to the reviewer's
  file; `4c876306` BLOCKER 2; `7cda9d11` should-fix 2; `bdbb2557`
  should-fix 1; `4c3e83f4` BLOCKER 1 as **A-405** + DA-R1 as **A-406** +
  should-fixes 4/5/6/7; `1d464fc4` docs). **Gate verified by the
  controller from the run-11 log directly** (`gate-run11.log`: wheel
  `assay-4.0.1.dev45+g4c3e83f4`, all phases through
  `independent-self-hosting-passed`, `ASSAY_REGISTERED_GATE_COMPLETE=1`,
  `GATE_EXIT=0`, zero red hits); `git diff --stat 4c3e83f4..1d464fc4` is
  two trove report files; tree clean; `verify.py`, `schemas/`,
  `carve-assets/W5/` and `shared-ramdisk-depot-manager/` unchanged
  against `main`; `verdict.py` differs by +51/−17, which the round-2
  reviewer is asked to confirm is the helpers correspondence rule and
  not a wire change. Implementer's counts: suite 3939/11, Go
  qualification 5/5 in-image. Notable in the landing: the `linedup`
  witness committed with a recipe (`probe-linedup.sh`, `linedup.out`,
  `linedup-oracle.json`) — nine records, eight zero-column, the oracle
  reproducing all nine extents and counts, REPORT §5 item 4's "unproven"
  struck; `FileCoverage.line_directive_remapped` DERIVED from the blocks
  rather than stored; the DA-R1 load-time seam is
  `vocabulary.STATEMENT_ATTRIBUTABLE_FORMATS_BY_LANGUAGE` (the
  adapter-attribute alternative rejected in A-406 because `config` must
  not import `adapters`, registry's O2 guarantee) with a drift guard
  derived from `_built_in_registry`; a third stale registry-summary copy
  found in `cli.py`'s module docstring and fixed; the `cover.go` comment
  measured at lines 1055-1060 in this image versus the review's
  1053-1058, recorded rather than adjusted. Deliberately NOT repeated:
  the srdm F008-A5 rebuild after the fold and parser changed shape
  (REPORT §48) — handed to the reviewer as a pre-adjudicated residue to
  re-run if its round-1 harness survives.

  **Round 2 sent to the SAME reviewer via `SendMessage`** (resume, never
  a fresh spawn): re-run its own probes verbatim, per-blocker checklist,
  the `verdict.py` diff question, the srdm residue; round 2 of the
  3-round cap; "on ACCEPT state it unambiguously — the controller merges
  on your word."

- **2026-09-02 (review round 2: NOT ACCEPT — one blocker, pre-existing,
  with a proven 12-line fix; DA-R3 ruled; final fix round dispatched)** —
  The reviewer (~32 min, 119 calls) re-ran every round-1 probe verbatim
  against `1d464fc4`: BLOCKER 1 (A-405), BLOCKER 2, DA-R1 (A-406) and
  should-fixes 1–7 all check out; it re-ran the gate on the tip itself
  (PASS, 12 phase markers), the suite (3939 passed, 18 skipped — the
  implementer's "11 skipped" is not reproducible, the passed count is
  exact), the Go qualification (5/5), and **rebuilt its srdm F008-A5
  harness against the tip: 418/394/94.26% holds exactly**, so REPORT
  §48's judgement call was right. It also conceded its own `cover.go`
  line citation was wrong (the implementer's 1055-1060 is right) and
  found the implementer had fixed a fourth `go.mod` divergence and a
  third stale docstring it had missed. `verdict.py`'s +51/−17 confirmed
  as the behaviour-preserving extraction of `supported_helper_roles`;
  `schema_version` still 9 in every real verdict; both drift guards
  passed in its own gate run. Review file: scratchpad
  `assay-WAVE-C-go-REVIEW-round2.md` (374 lines).

  **BLOCKER R2-1.** Any Go R1 lane whose judge refuses AFTER the oracle
  has run — a stale profile refused inside `attribute_statements`, or
  A-405's own refusal for a flagged file with judged lines — reports
  assay's `helpers[]` WIRING instead of the refusal and writes NO
  verdict artifact: `runner.py:997` records the `statement-positions`
  helper the instant the oracle returns; the judge's refusal then voids
  the R1 payload; `assemble_verdict` (`runner.py:1493`) refuses because
  no claim supports the helper role; `run_lane` never returns. Five
  in-image lanes measured: the DA-R2 ignore semantics (A) work, the
  ruled refusal (B) is correct but unreachable through the CLI, and the
  pre-existing stale-profile refusal CONSUMERS.md has documented since
  generation 5 (E) is masked the same way. **Not a regression from the
  fix round** — the reviewer nearly reported it as one and disproved it
  against `875382d2`; it is a wave defect its round-1 probes missed, and
  A-405 gave it a second, ruled path. **Prescription proven, not
  proposed**: after `claims += (r1_claim,)` (`runner.py:2769`), when
  `r1_claim.coverage is None`, drop the helpers whose role
  `supported_helper_roles((r1_claim,))` no longer supports — the same
  move `_replace_highest_higher_rigor_claim_with_git_failed` already
  makes — so `assemble_verdict`'s guard stays a wiring assertion; built
  in a scratch worktree, zipapp rebuilt, all five scenarios then behave
  as ruled with verdicts written, suite 3939/18 identical. **Controller
  ruling: apply exactly that**, plus the scenario-E regression test at
  the runner level with a `FakeAdapter` whose `statement_blocks` report
  disagrees with the profile (no toolchain needed, so the registered
  gate exercises it) AND the in-image scenario B/E through the shipped
  zipapp in the qualification module. A-405's message text stays as
  ruled; the defect was that it never reached a verdict at all.

  **Should-fixes, all applied:** SF-R2-1 — `test_go_line_directive_
  witness.py:322` is HOLLOW (its `/repo` fixture does not exist, so the
  whole-target refusal fires first and the test passes on the wrong
  refusal; mutation M-B2 removing the whole-target A-405 branch leaves
  all 69 tests green) — materialise the target on a `tmp_path` repo and
  assert on the message text; SF-R2-2 — the runner's remapped-file
  filter is a second guard no test distinguishes (mutation M-F survives)
  — assert the oracle is NOT asked about a flagged path; SF-R2-3 —
  CONSUMERS.md:1953-1968 and :1888-1897 print refusal text "as what you
  will see" when an `assay run` consumer sees `unit: ERROR/
  BAD_LANE_CONFIG (exit 2)` and nothing more (the reviewer's control:
  a judge-phase `AssayError`'s text reaches no surface) — rewrite both
  blocks as "the refusal assay raises (visible to a library caller; the
  CLI shows the reason code — see B053)".

  **DA-R3 ruled: (a) now, (b) in the patch wave.** Judge-phase refusal
  text not reaching the consumer is main's B053 (dstdns's finding of
  the same day) seen from the Go side; the controller already paired
  B053 with B049 for the post-Wave-C consumer-diagnostics patch wave.
  The reviewer's option (b) — route judge-phase refusal text to the
  existing `diagnostics` stream `environment_command` already uses
  (`runner.py:365`, no wire change) — is recorded here as the candidate
  mechanism for that wave alongside B053's stderr option; option (c),
  a v10 field, is not taken. Wave C changes only the docs (SF-R2-3).
  Added to main's B053 as a note after the merge, not now.

  **Fix generation (8) dispatched** — fresh Opus, same worktree/branch,
  tip `1d464fc4`, seeded with the round-2 file and this entry: commit
  the round-2 review verbatim → R2-1 per §3.4 + both regression tests →
  SF-R2-1 → SF-R2-2 → SF-R2-3 → LOG/REPORT/CHANGES → gate → return.
  Decisions: **A-407** for R2-1 (the orphaned-helper rule and where it
  lives), citing this entry; next backlog id **B062**. Then the SAME
  reviewer for **round 3 — the cap**: if round 3 is not ACCEPT, the
  controller pauses for the operator rather than dispatching a fourth.
