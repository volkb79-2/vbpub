# Session prompt — refine `assay` before implementation

> Paste the section below into a fresh session. It is a **design discussion**
> prompt, not an implementation handoff: the goal is to leave with decisions
> recorded, then carve packages. Do not write production code in that session.

---

I want to design **`assay`** — a standalone testing/rigor library for our
estate — before any implementation. Interview me; do not start building.

## Read first (in this order, then stop and think)

1. `/workspaces/vbpub/nyxloom/reference/TESTING-METHODOLOGY.md` §"Scope, rigor,
   and lanes" — the S0–S4 / R0–R3 model and the lane definition this library
   serves. Also skim its evidence-model catalogue: that table is the menu of
   methods assay may eventually cover.
2. `/workspaces/vbpub/ciu/docs/DESIGN-NOTES.md` **D7** — the where/what/how
   split, and the placement test ("would a project using only this tool still
   get value from it?").
3. `/workspaces/vbpub/nyxloom/nyxloom-trove/handoffs/nyxloom-P90-extract-testing-library.md`
   — the extraction spec, its oracles, and the consumer-migration table.
4. The four diverged implementations. **Diff them before proposing anything:**
   - `/workspaces/vbpub/nyxloom/src/nyxloom/coverage_gate.py` (455 lines)
   - `/workspaces/dstdns/scripts/coverage_gate.py` (804 — the most elaborated)
   - `/workspaces/vbpub/topos/tools/coverage_gate.py` (299 — the thinnest)
   - `/workspaces/vbpub/shared-ramdisk-depot-manager/tools/covergate/` (Go)
   Also read `gate_runner.py`, `gate_canary.py`, `mutation_gate.py` in nyxloom.
5. `/workspaces/dstdns/AGENTS.md` §4.2a — the defaults doctrine. It applies to
   assay's own config resolution, and it is why "sensible default" is not an
   acceptable answer to a config question below.

## Settled — do not reopen

- **Build it now, as a NEW standalone project.** Adapt logic from nyxloom where
  useful; **leave nyxloom untouched.** nyxloom's own migration happens later
  (P90c, after its core redesign CR-14). Building fresh is what removes the
  sequencing conflict that deferred this.
- **Tool independence is the hard constraint.** assay must not import ciu or
  nyxloom, and must be usable by a project that has adopted neither — the way
  `ciu` is consumed today. Synergy by design, no hard dependencies.
- **The lane declaration is tool-neutral data**, in a file both assay and ciu
  can read (e.g. `pyproject.toml [tool.assay.lanes]` or `assay.toml`). Neither
  tool owns it.
- **The union, not the intersection.** dstdns's copy carries behaviour the
  others lack — `--allow-excluded`, the NO-MEASUREMENT guard that refuses a
  vacuous verdict when base resolves to HEAD, and multi-line-statement
  attribution that stops a changed line vanishing from both numerator and
  denominator. Dropping any of it to "align the copies" loses a real feature.
- **`LanguageAdapter` from day one.** srdm is Go; dstdns has a React/TS app.
  A design that cannot take them is Python with extra steps.
- **Division of labour:** ciu answers WHERE (which container — a stack's own
  test-runner, or the shared `tester-unified` for library projects like ciu and
  cmru). assay answers HOW TO JUDGE. The lane file answers WHAT.

## Interview me on these — one at a time, with a recommendation each

1. **Name.** `assay` is a placeholder chosen for register (`ciu`, `cmru`,
   `topos`, `nyxloom`). Check PyPI availability first; propose alternatives if
   taken. Locking the name early avoids a rename across five repos.

2. **Package boundary.** Which of these are IN v1: changed-line coverage,
   gate-runner, canary ("does the gate actually reject?"), mutation, lane
   config, result/verdict schema? What is deliberately out, and why? Argue for
   the smallest v1 that still proves the boundary is real.

3. **The `LanguageAdapter` protocol.** Propose the concrete method set
   (`source_glob`, `parse_coverage`, `inject_uncovered_line`,
   `inject_import_break`, `mutation_operators`, …) and show how a Go adapter
   and a TypeScript adapter would each satisfy it. Where does coverage-format
   parsing live — in the adapter, or a separate parser registry (coverage.py
   JSON vs lcov vs cobertura vs `go cover`)?

4. **The lane schema.** Concrete TOML. It must express: scope (S0–S4), rigor
   (R0–R3), the argv, environment, budget, and which lanes are gates vs
   advisory. Show what dstdns's five lanes and ciu's own single lane look like
   in it. Remember §4.2a: every field is read or required, not defaulted into
   plausibility.

5. **Verdict contract.** What does assay return, such that ciu (or a CI system,
   or nyxloom) can consume it without linking against assay? Exit codes plus a
   machine-readable artifact? Nail the third outcome explicitly: NO
   MEASUREMENT is neither pass nor fail, and conflating it with pass is the
   specific bug dstdns's copy already guards.

6. **Consumers and sequencing.** Adoption order across dstdns, topos,
   netcup-api-filter (no gate today — the honest standalone-adoption test),
   ciu, cmru, mdt, srdm (Go). Which one is first, and what does it prove?
   Note ciu/cmru/mdt each need MORE tests generally, so adoption is also an
   opportunity to raise their floor — say where that is in scope and where it
   is a separate job.

7. **Self-hosting.** assay must gate itself. Say how, without circularity, and
   what the bootstrap looks like on a clean checkout.

8. **What assay must NOT become.** Name the scope creep you expect (a test
   runner? a CI system? a reporting dashboard?) and where the line is.

## Deliverables from that session

- A decisions record (one line per decision, with the reason) — propose where
  it lives.
- A carved package series with oracles, in the `AUTHORING.md` frontmatter
  format, ready to lint.
- An explicit list of what was NOT decided and what blocks each.

## How to run it

Interview style: one question at a time, your recommendation first, then my
answer. Do not batch. Do not write code. If you find that a settled item above
is actually wrong, say so immediately with the evidence rather than working
around it.
