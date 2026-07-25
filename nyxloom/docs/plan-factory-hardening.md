# Plan — factory-hardening workstream (safer + more-correct + cost-aware)

Accepted 2026-07-24. These sharpen the correctness pipeline the manual-hardening
phase validated. Guiding principle (canonical `reference/LESSONS.md` **L1**):
**always the clean structural fix, never a band-aid.** Sequenced small→large;
each is a normal carved package through the controller loop (or a dogfood
candidate once the frozen core is done). Cost theme: spend correctness where the
blast radius is largest, cheapen it everywhere else — drive the switch off the
carver's complexity band.

## A — De-duplicate the schemas (structural, retires a band-aid) · SMALL · ✅ DONE
**What.** Make `src/nyxloom/schemas/` the single source of truth (it is what
pyproject packages). Classify each top-level `schemas/*` file: a true duplicate of
the packaged copy → remove it (or generate/symlink at build); a genuine
example/reference (e.g. `routes.example.toml`) → keep, and document why it lives at
repo root. Then delete `tests/test_schema_sync.py` (the byte-identity guard) — once
there is one source, the guard has nothing to guard.
**Why.** Two hand-maintained copies diverged twice; the guard is L1's textbook
band-aid. **Investigate first** (`event`/`handoff-frontmatter`/`statefile`/`routes.example`
each differ in status) — do not blanket-delete.
**Scope.** `schemas/` (top-level), `tests/test_schema_sync.py`, maybe a build/gen
step. Additive-safe; leaf. **Do this first — it's the flagship structural fix.**
**Done.** Classification confirmed the three top-level `*.schema.json` copies
(`event`/`handoff-frontmatter`/`statefile`) had **no readers** (every loader uses
`importlib.resources.files("nyxloom.schemas")`) and `handoff-frontmatter` had already
drifted stale unguarded → removed all three plus the guard test; `routes.example.toml`
kept with a `schemas/README.md` documenting the reference-only purpose; the three live
doc-links repointed to `src/nyxloom/schemas/`. See `nyxloom-trove/LESSONS.md` PL1.

## D — `review_focus` on handoffs + tier review depth by band · SMALL-MEDIUM
**What.** Add an optional `review_focus: [..]` list to the handoff frontmatter
(carver-authored "adversarially check these"), injected into the
`REVIEW_INDEPENDENT` prompt (`adapters.py`). Select review depth/route by the
carver's complexity band: additive/leaf → one cheap review; frontier/frozen-core →
the strongest review.
**Why.** The package-specific reviewer hints that caught real bugs this session
came from *controller judgment*, not the factory. Move that judgment into the carve
so it is mechanical. Also *saves* cost (cheap review for leaf work).
**Scope.** `src/nyxloom/schemas/handoff-frontmatter.schema.json`, `adapters.py` (review prompt),
routing selection, lint (accept the new field), tests. Frozen-core-adjacent
(adapters/routing) — carve carefully.
**Gate-rigor as a second review-depth input (folded in 2026-07-25).** Review depth
should key off *two* signals, not one: the carver's complexity band AND how much
correctness the project's own gate already guarantees. Today nyxloom trusts a
project's declared gate blindly — dstdns even ships a `[gates.gate-probe]` whose
`argv` is `true`, and a project could declare `argv=["true"]` and watch every merge
sail through, orchestration perfectly hardened (F) but verdict meaningless. Add an
optional declared **rigor contract** to `[gates.*]` in the trove config — e.g.
`asserts = ["tests-pass", "changed-line-coverage", "mutation"]` — that nyxloom
records + surfaces and that D's routing reads: a weak-gate project makes the
reviewer layer carry more (deeper/stronger review); a strong-gate project earns a
cheaper review. This makes the three-layer model (gate ⊕ reviewer ⊕ controller,
canonical **L2**) *configurable per project* instead of hardcoded — the same axis D
already mechanizes. Optional adversarial extension: a **meta-gate** that requires the
declared gate to *reject* a known-bad canary commit before nyxloom trusts it (turning
`gate-probe`'s "does it run" into "does it actually fail"). See `nyxloom-trove/LESSONS.md` PL2.
**argv_max caveat (from scoping).** The `REVIEW_INDEPENDENT` prompt is already at
argv_max — inject `review_focus` (and any rigor-derived hint) with the SAME
bounded-embed pattern as the existing `prior_verdict`/scope-amendment appends
(guarded by `test_review_independent_prompt_stays_under_argv_max_with_real_paths`);
tier/band changes have a ~19-test blast radius (frontier-review fixtures).

## F — Auto-revert on post-merge-gate failure + gate the manual merge path · SMALL-MEDIUM · ✅ DONE
**What.** (1) When post-merge validation fails on the *published* tree, auto-revert
(CAS `update-ref` back to the pre-merge commit) instead of only transitioning
BLOCKED; emit an audit event. (2) Make `cli.cmd_merge` run the gate before
recording, unless `--force`.
**Why.** The daemon's `_execute_auto_merge` already gates pre-publish (D-CORRECT-1),
so the factory cannot make the "merge before verdict" mistake — but the *manual*
`cmd_merge` path is ungated and post-merge failure currently only *detects* a bad
merge, it does not *heal* it. Structural closure of the operator escape hatch
(canonical **L4**).
**Scope.** `daemon.py` (post-merge validation → revert), `cli.py` (gate cmd_merge),
tests. **Frozen-core** (the publish/validate path) — full stack + diverse review;
mirror D-CORRECT-1's structure exactly; the revert must itself be CAS-safe.
**Done (merge `564cadf4`).** `daemon._run_post_merge_gate` now CAS-reverts the branch to
`{merge_commit}^1` + emits `MERGE_REVERTED` (opt-out `policy.auto_revert_failed_merge`);
`cli.cmd_merge` gates the commit before recording (`--force` bypass); a new shared
`gate_runner.py` single-sources gate selection + run-at-commit (L1). SOLO gate 60/60
diff-cov + full suite; deepseek-pro-max review APPROVE / no findings (CAS-safety, None-
safety, gate-before-record, test behavioral-ness all verified).

## G — Parallelize the gate (pytest-xdist) + mutation across mutants · MEDIUM
**What.** Add `pytest-xdist` and run the gate suite with `-n auto`, composed with
`coverage` (pytest-cov + xdist data-merge) and with any serial-state tests marked
`-p no:xdist`/serial. Parallelize the changed-lines mutation gate across mutants
(each mutant is independent).
**Why.** The 57-file suite runs single-process today (`addopts = "-q"`); the gate
is the loop's wall-clock bottleneck. Straight cost/latency win; also the enabler
for H (broad mutation becomes affordable). NOTE: this is orthogonal to the
SOLO/serial-*across-gates* rule (that is an OOM/resource constraint, not a
within-gate one).
**Scope.** `pyproject.toml` (dep + addopts), conftest markers for order-dependent
tests, `mutation_gate.py` (mutant fan-out). Verify coverage numbers are identical
under xdist before trusting.

## C — System→system lessons channel (the promotion plumbing) · EPIC
**What.** A `LESSON_DISCOVERED` typed record (reuse findings-channel plumbing): a
gate-failure / review-rejection / operator note distils into an entry appended to
`nyxloom-trove/LESSONS.md` (`scope: project|product`); `scope: product` additionally
emits an upstream proposal for maintainer integration into `reference/LESSONS.md`
(never auto-mutated). Optionally: the carver consults recent lessons when authoring
oracles, closing "we hit this bug" → "the next carve writes an oracle for it."
**Why.** The factory has no memory today — doctrine is static/human-curated and it
does not learn from its own rejections. This is the loop that makes it improve.
Implements the promotion model in `reference/LESSONS.md`.
**Scope.** New `lessons.py` (like `findings.py`), events, trove writer, upstream-
proposal artifact, carve-prompt injection, dashboard surface. Design first.

## H — Strategic mutation audit of the pre-existing frozen core · EPIC (needs G)
**What.** A one-time (then periodic) mutation sweep of `reconcile.py`, `daemon.py`,
`storage.py`, `types.py` — the whole module, not just diffs — surfacing hollow
tests in code that predates the mutation gate. Prioritize via the strategic
test-health trigger (D-065). Parallelize across mutants (needs G).
**Why.** Canonical **L5**: changed-lines gates never examine the baseline. Frozen
core is where hollow tests are catastrophic. Depends on G for affordability.
**Scope.** A mutation-audit runner (whole-module mode of `mutation_gate.py`),
report artifact, backlog items per surviving mutant. Design + budget first.

---

## Sequencing
1. **A** (schema de-dup) — ✅ DONE. Flagship structural fix; paid down the dual-schema debt (single source = `src/nyxloom/schemas/`).
2. **F** (auto-revert + gate cmd_merge) — ✅ DONE (merge `564cadf4`). Frozen-core safety; closed the L4 operator escape hatch.
3. **D** (review_focus + band-tiered review) — mechanizes reviewer targeting, saves cost.
4. **G** (xdist + mutation fan-out) — wall-clock win, enables H.
5. **C** (lessons channel) — the learning loop; design doc → build.
6. **H** (frozen-core mutation audit) — after G; design + budget → build.

A/D/F/G are good **dogfood candidates once P2b lands** (the frozen core is stable);
C/H are epics that each want a design doc first. All must honor **L1** — structural
over band-aid — as their own acceptance bar.
