# Wave prompt — B070, `judgment.r2.discarded` becomes a listed, verified field (2026-09-08)

Branch: `feat/assay-b070-discarded-mutants-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b070-discarded-mutants`
Controller log: `assay/nyxloom-trove/reports/assay-WAVE-B070-CONTROLLER-LOG.md`

**This is a v11 MAJOR / BREAKING wire cut.** `VERDICT_SCHEMA_VERSION` goes
`10 -> 11`. This wave is scoped to B070 ALONE — do not fold in any other
backlog item, and do not touch anything already shipped in 5.0.0/5.1.0/5.2.0.
Exactly ONE `feat(assay)!:` commit for the schema bump itself, same
discipline Wave D used for the v10 cut (`b2fd09f3`).

## Ruling — the shape is already decided, do not re-litigate

The operator chose **Shape 1: list the discarded mutants** (an array of
positions, on `survived_uncovered`'s footing) over Shape 2 (an ingested-only
in-scope count). Read `assay/nyxloom-trove/4-backlog.md`'s full **B070**
section first (search for `## B070`) — it lays out both shapes, the trade,
and why Shape 1 was the bigger option. Do not re-open that choice; implement
Shape 1.

**What Shape 1 requires, concretely** (from the backlog entry + DA-R26 /
A-437 / DA-D4, all cited in full in the backlog section):

1. A new record shape for discarded mutants, on `judgment.r2.discarded`,
   consistency-auditable against the raw ingested document the same way
   `survived_uncovered` already is: same ascending/unique ordering rule,
   same subset-of-the-payload check. Decide and document the exact record
   shape (position-only vs. a small object) — this is a genuinely open
   design decision the backlog entry deliberately leaves to whoever builds
   it; make the call, state it in the LOG, and keep it consistent with how
   `survived_uncovered` is already shaped.
2. `mutation.ingest_mutation_report` (`mutation.py:1845`, `:1968`) currently
   `continue`s past a discarded mutant with no record of it at all
   (`mutation.py:1967-1969`) — it must now emit the new discarded record
   instead of silently dropping it.
3. **A fifth disposition in the arithmetic rule.** `Mutation._check_arithmetic`
   (`verdict.py:1684-1703`) currently forbids `candidate_count != total`
   outside the limit sentinel — that rule is written for four buckets and
   must be revised for five (bucketed dispositions + discarded) without
   opening a route back to the `9999`-on-a-109-mutant hole B070 exists to
   close. `candidate_count`/`total` semantics need an explicit, stated
   answer for what they mean now that a fifth disposition exists — do not
   leave this implicit.
4. The quantity is on the wire in **all three places** (schema, dataclass,
   `verify.py`) — this project's own standing 2.4.0 lesson, cited directly
   in the backlog entry's acceptance criteria. Fork on `producer` the way
   every other ingested-only field already does (native R2 never has a
   discarded list to report; only ingested R2 does).
5. `verify._check_ingested_r2_agrees_with_its_payload` gains a **fourth**
   real re-derivation (alongside its existing three), checking the new
   field against the payload. Its "what this function does NOT check"
   docstring/comment section shrinks to name only the genuinely un-listed
   half (a tool that drops candidates before reporting at all — B070's own
   entry is explicit this half stays declared-not-verified; do not attempt
   to close it, it is not closable from any artifact assay receives).
6. **The `9999` reproduction becomes a NAMED refusal.** A-437 recorded
   `judgment.r2.discarded = 9999` on the frozen 109-mutant fixture verifying
   clean as a deliberately accepted gap — that exact case must now refuse,
   naming what's wrong. Alongside it, commit a **control**: a truthful
   high-discard document that the new bound does NOT refuse. Without that
   control the bound could just as easily be an upper-bound clamp (route 3,
   already rejected by DA-R26) — the control is the proof it isn't.
7. **A real fixture with a non-zero `discarded`.** Produce a report with at
   least one genuinely discarded mutant (the backlog entry suggests a
   deliberately uncompilable mutant, easy to produce with Stryker if a
   JS/TS fixture project is at hand; a Python-side ingest fixture with a
   hand-authored `CompileError`/`RuntimeError` entry is equally acceptable
   if that's more tractable — your call, state which you used). Freeze it
   into the v11 `W<n>` generation the way every prior schema-affecting
   generation has been frozen (check `carve-assets/` for the `W<n>`
   convention and the current newest generation number before picking the
   next one).
8. **Migration notes, written not just implied.** `docs/CONSUMERS.md`'s
   declared-not-verified paragraph for `discarded` and `DESIGN-GUIDE.md`
   §11's matching paragraph are REWRITTEN (not deleted) to say what
   replaced the old behavior — a consumer who read A-437's old statement
   needs to be told what changed and how to read the new field.
   `CHANGES.md`'s `[Unreleased]` `### Changed` section gets a `BREAKING`
   entry in the same style as 5.2.0's `candidate_total` BREAKING entry
   (read it for the shape: what changed, why, and an explicit
   **Migration:** line).

## Context to read first

1. `assay/nyxloom-trove/4-backlog.md`'s full `## B070` section (the problem,
   why v11 not a defect fix, the two shapes and their trade, the acceptance
   checklist — this prompt sequences it, it does not repeat every word).
2. `assay/nyxloom-trove/4-backlog.md`'s `## B051` section (the field's
   origin — DA-D4 ruled "listed" semantics) and any `A-437`/`DA-R26`
   references in the controller-log/decisions trail if you want the full
   history; the backlog entry itself already quotes the load-bearing
   sentences.
3. `assay/nyxloom-trove/WAVE-PROMPT-2026-09-02-wave-d-v10-integrity.md` —
   the prior MAJOR/BREAKING wave on this project, for the shape of how a
   schema-version cut, a `feat(assay)!:` commit, and a frozen `W<n>`
   generation are actually run here. This wave is much smaller in surface
   area (one field, not a whole-document integrity redesign) but carries
   the exact same cut discipline.
4. `assay/CHANGES.md`'s `[5.2.0]` entry, specifically the `candidate_total`
   BREAKING item under `### Changed` — copy that entry's shape (what
   changed / why / **Migration:** line) for this wave's own BREAKING entry.
5. `verdict.py`'s `Mutation` dataclass and `_check_arithmetic`
   (`:1684-1703`), and `mutation.py`'s `ingest_mutation_report`
   (`:1845`-`:1997`, especially the discard `continue` at `:1967-1969` and
   the `candidate_count`/`total` assignment at `:1992-1997`) — the exact
   code this wave changes.
6. `verify.py`'s `_check_ingested_r2_agrees_with_its_payload` — the
   function gaining the fourth re-derivation.

## Binding constraints

- Exactly one `feat(assay)!:` commit for the schema-version bump itself
  (`10 -> 11`); other commits (ingest change, arithmetic-rule change,
  verify re-derivation, fixtures, docs) can be separate, ordinary commits,
  same convention Wave D used.
- `assay.toml`'s `schema_version` and `assay lanes --json`'s
  `inventory_schema` are UNRELATED counters — do not bump them unless you
  find a real reason tied to B070 itself (you should not).
- Every other producer/consumer of `judgment.r2` untouched by this item
  (native R2, R0/R1, R3/canary) must be byte-unchanged where they carry no
  discarded mutants — verify this with an existing frozen fixture that has
  zero discards.
- Read the registered gate's verdict from its own log markers after it
  finishes, never from a piped exit code (LESSONS L4).
- Host: 8 cores shared with a production game server. `docker ps` AND
  `pgrep -af tester-unified-gate.sh` before starting anything — wait for
  any existing gate container/process, never race. Serial pytest under
  `nice -n 19 ionice -c 3`. `docker update --cpus=3` right after any gate
  container you launch starts.
- **Do NOT write your LOG/REPORT into the tree before your final gate
  run** — run the gate on a clean commit; write LOG/REPORT and commit them
  only after you have a green verdict to report.
- **Checkpoint clause (E-008):** if you cross ~120k context tokens or ~60
  tool calls, cut at the next coherent boundary (green gate > commit >
  LOG/REPORT write; never on a red gate) and write a continuation brief to
  `assay/nyxloom-trove/reports/assay-WAVE-B070-BRIEF.md` plus a
  self-authored `/compact`-retention prompt, commit, and stop.
- Commit trailer:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  ```
- When B070 is fully implemented (schema/dataclass/verify wired, arithmetic
  rule revised, `9999` case refuses by name with a passing high-discard
  control, real non-zero-discard fixture frozen into a new `W<n>`
  generation, CONSUMERS.md/DESIGN-GUIDE.md/CHANGES.md all rewritten) and the
  registered gate is green, write
  `assay/nyxloom-trove/reports/assay-WAVE-B070-LOG.md` (what you did, the
  record-shape decision you made and why, commit hashes) and a REPORT
  summarizing every acceptance-box in the backlog's own checklist, then
  stop — do not dispatch a reviewer yourself, the controller does that
  next.
