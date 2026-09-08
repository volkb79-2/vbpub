# assay — B068 + quick-wins wave, implementer LOG (2026-09-08)

Branch: `fix/assay-b068-quickwins-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b068-quickwins`
Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b068-quickwins.md`
Base: `a78d0280` (the wave prompt + controller log commit)

Five items from the wave prompt, plus a sixth added by controller ruling
after the first hand-back. No verdict-schema change: `VERDICT_SCHEMA_VERSION`
stays 10, `assay.toml`'s `schema_version` stays 2, `assay lanes --json`'s
`inventory_schema` stays 1.

| # | item | commit | shape |
|---|---|---|---|
| 1 | B068 | `96973575` | fix (b) — diagnostic message; the entry's own premise refuted |
| 2 | B072 | `9cd5ef28` | one-line fix + red-first tests; sweep found a third instance, filed B074 |
| 3 | B062 | `c2d89888` | 31-finding sweep + gate lint-phase widening |
| 4 | B063 | `e426c29f` | skip-with-a-named-reason + a pre-existing gate-blocking red repaired |
| 5 | B071 | `b12ec9f2` | `crashed`-only diagnostic tails in the mutation-state record |
| — | LOG/REPORT | `95177803` | first hand-back, gate green at `b12ec9f2` |
| 6 | B074 | `767393d1` | a third site of the `RecursionError` gap, **on controller ruling** |
| 7 | review blocker | `93e6f7fc` | B072's sweep redone — it had missed 4 sites, 3 of them crashing |

(The item-6 commit message calls B074 "the third and **last** site of one
gap". That was wrong, and item 7 is why: there were five more untrusted
sites than the sweep behind it had looked at. The message is left as
committed and corrected here rather than rewritten.)

**Scope note on the wave's "`assay verify` is unaffected" constraint.** It
held for items 1-5, which is what it was written for. Item 6 changes
`assay verify` deliberately: the controller ruled that sentence was the scope
guard for the five original items' own changes, not a blanket prohibition on
fixing a live crash bug found inside `assay verify` by item 2's own required
sweep.

---

## 1. B068 — `96973575`

**Investigated first, as the prompt required, and the entry's own
"discriminator" is REFUTED.**

Traced to source, then reproduced end to end. `cli._cmd_run` →
`cli._run_reserved` calls `git.head_rev(lane_file.project_root)` **once,
unconditionally** (`cli.py:728`), before the reservation → HEAD → adapter →
command order reaches anything tier-specific. There is no caller R0/R1 reaches
that R2 does not. Measured in a real severed linked worktree (a genuine
`git worktree add`, then the main repository's `.git` moved away): a
`rigor = ["R0"]` lane and a `rigor = ["R0","R1","R2"]` lane in the SAME
worktree produce the byte-identical refusal. The dstdns field observation (an
R2 lane running 9+ hours while `mock` died) was a difference in the two
containers' **mounts**, not in assay's code.

That closed the (a)/(b) fork: **(a) was never available** — nothing tolerant
exists to copy — and it is also not desirable, because git resolution is
load-bearing for R0/R1's own semantics (the commit label every verdict
carries, the dirty set behind `clean_tree`, the comparison base).

Fix (b): `git.py`'s `_resolve_repo` bootstrap-failure path consults a new
diagnostic-only `_linked_worktree_gap()`. It asks the FILESYSTEM (git has
already declined to answer) whether `repo_top/.git` is a gitfile whose
`gitdir:` target is absent, and prefixes the refusal with a sentence naming
the worktree, the missing git directory, the usual cause, the fact that no
lane setting — `clean_tree` explicitly — routes around it, and two remedies.
The raw git `fatal:` is **kept, not replaced**: in the measured case it reads
`not a git repository: (null)`, which is precisely why it needed a sentence in
front of it rather than a rewrite.

Tests: `tests/test_git_linked_worktree_gap.py` (14). The tier-independence is
pinned as a parametrized CLI test, so a future per-rigor git bypass turns it
red; the rest cover the message contract and every helper branch (healthy
worktree, plain `.git` directory, relative `gitdir:`, non-gitfile marker,
empty target, oversized marker, non-UTF-8 marker, unreadable marker → says
nothing rather than raising on top of the failure it explains). One test
asserts the real `git` binary still writes the `gitdir: <path>` shape the
helper parses.

## 2. B072 — `9cd5ef28`

`attestation.py:240`'s `except` tuple gains `RecursionError`. Red-first, on
today's `main`: the 200,000-byte / 100,000-deep array (well inside
`MAX_ATTESTATION_BYTES`) raised `RecursionError: Stack overflow (used 8148
kB)`.

Tests: `tests/test_attestation_recursion_depth.py` (6) — the depth fixture, a
guard that the fixture is genuinely inside the size bound (so it proves the
PARSER and not the bound), an injected-`RecursionError` test pinning *which*
exception is caught (a future CPython raising something else would otherwise
leave the fix silently inert), both real consumer hops
(`load_attestation_file` → `UNREADABLE_ARTIFACT`; `load_attested_evidence`
staging it as that one evidence item's own refusal instead of escaping the
loader), and a legible-document control.

**Sweep — NOT clean, and the sweep itself was wrong the first time.** It
found `verify.py`'s `verify_text` carrying the identical narrow guard and
crashing live, which became B074 (item 6). But it reported **7** sites in
`src/assay` when there are **11**: its grep was `src/assay/*.py`, a glob that
never descends into `adapters/`, `coverage_parsers/` or `mutation_parsers/`.
Review caught that; three of the four unexamined sites were also crashing.
See item 7 — the corrected sweep and the complete 11-site table live in
B074's Resolution.

## 3. B062 — `c2d89888`

All 31 findings deleted (25 unused imports, 5 dead locals, 1 redefinition,
across 19 modules). `python -m pyflakes tests` now reports exactly one line:
`tests/fixtures/mutation/python/broken.py:8:12: invalid syntax`, the
deliberate fixture.

**Both classes the backlog flagged as needing judgement turned out to be
EMPTY, verified rather than assumed:**

- the `pytest.importorskip`-shaped availability probe **does not exist** —
  `grep -rn importorskip tests/` returns nothing, so all 25 imports were
  genuinely dead;
- the assigned-never-read local that is "the point of the assertion above it"
  **does not exist either**. One (`lane` in `test_mutation_executor_bound.py`)
  was simply dead — `run_mutation` takes no lane — and the other four are
  `head_rev = git_repo.commit_all(...)`, where the CALL is load-bearing (it
  creates the commit) and only the BINDING is dead, so the honest fix is
  dropping the binding and keeping the call.

**Nothing was suppressed anywhere**: no `_ = value`, no `# noqa`, no
file-level exclusion. (For the record, the suite's only pre-existing `# noqa`
is one `E402` in `test_distribution_build_release.py`.)

`run_lint_phase` now lints `src/assay` **and** `tests/`. pyflakes has no
exclude flag, so `tests/fixtures/` is pruned by an explicit
`find -H … -prune` file list, and the phase **refuses an empty expansion** —
a renamed or absent `tests/` would otherwise shrink the scope back to B024's
while still emitting `ASSAY_GATE_PHASE=pyflakes-clean`. `gate/` stays out:
measured clean, but adding a third tree on the way past would be the
unevidenced drift the original deferral existed to prevent. The scope comment
above the function and `docs/DESIGN-GUIDE.md` §14's scope sentence were both
rewritten rather than left to rot.

Tests (`tests/test_distribution_gate.py`): a planted unused import in a TEST
module reddens the phase (before B062 this passed, because the phase never
looked); an unparseable fixture AND a plain unused import under
`tests/fixtures/` both stay green (the prune is a scope decision, not a
syntax-error exemption); a clone with no `tests/` refuses;
`test_the_shipped_source_tree_is_pyflakes_clean` now symlinks `tests/` in
alongside `src/assay`, so a new finding reddens `pytest tests` in seconds
instead of only after a nine-minute container run.

## 4. B063 — `e426c29f`

Skip-with-a-named-reason, per the controller's ruling. `conftest.py` gains one
shared `REPO_ROOT` (replacing three independent `PROJECT_ROOT.parent` hops)
and a `requires_parent_repository` skip mark that asks **git** — a linked
worktree's marker is a gitfile and a submodule's is a redirect — and compares
the reported **toplevel** against `REPO_ROOT` rather than merely testing for
existence, so assay copied into an unrelated repository's subdirectory skips
honestly instead of failing confusingly.

The rejected alternative (resolve-from-context) is stated at the seam with its
reason.

Applied as a module-level `pytestmark` to `test_python_qualification.py` and
`test_distribution_build_release.py`, but **per-test** in
`test_runner_snapshot_selection.py`: only its two embargo tests read the
monorepo's tagged history, and skipping the other 11 collected items would hide real
coverage of assay behind an unrelated property of the checkout.

**Measured**, from a `cp -r` copy of `assay/` into a scratch directory outside
any git repository:

```
4178 passed, 77 skipped, 1 warning in 366.71s (0:06:06)
```

against R-1's `11 failed, 3956 passed, 18 skipped, 13 errors in 821.95s`.
**Zero failures, zero errors.**

Re-measured at the final tip, because the number first recorded here
(`2 failed, 4143 passed, 77 skipped in 446.07s`) went stale twice over: it
was taken BEFORE the two `host`→`bare-host` repairs in this same commit, and
before the B074 and second-sweep tests were added. Quoting a pre-repair
figure next to a claim of "zero failures" was a real inconsistency, caught in
review.

**Those two failures were a pre-existing red on `main`**, unrelated to
B063 and unrelated to this wave, and are repaired in this commit:
`assert lane["environment"] == "host"` at
`test_cgroup_parent.py:110` and `test_self_hosting.py:461`. run-gate rev 36's
RG-43 estate sweep (`f62642c6`) moved this lane to `bare-host` and neither
copy of the assertion followed. Confirmed failing IN PLACE on `main` too.
Repaired in this commit with the reason recorded at both sites — a red gate
blocks the wave, and the wave prompt forbids cutting on one.

In place, the three modules are unchanged: 68 passed, 9 skipped, all 9 skips
the pre-existing `/opt/tester-venv` ones, none from B063.

## 5. B071 — `b12ec9f2`

`_write_mutation_state_record`'s payload gains
`result_stdout_tail`/`result_stderr_tail` — the names `verdict.py:3778-3779`
already defines and `verify.py:1853-1859` already round-trips for other claim
types — through a new `_crash_diagnostic_tails()` that returns `{}` for every
bucket but `crashed`.

Scope held narrow per the ruling: `killed`/`survived`/`budget_exceeded` are
NOT wired, and `write_progress`'s payload is untouched. No schema or wire
change: the file is diagnostic state, not a verified artifact, and
`_load_validated_state_record` validates named keys while tolerating extra
ones, so an older record resumes exactly as before.

An empty tail is written as `""` rather than omitted ("the stream was empty"
is itself a diagnosis); a `None` tail is omitted rather than written as
`null`.

Tests: `tests/test_mutation_state_crash_tails.py` (8). The headline test
reproduces the entry's own `uq_work_units_id_operation` shape rather than
asserting synthetically — an `equivalence_artifact` lane whose mutated DDL
fails to apply and therefore writes no artifact, which is exactly what lands
the candidate in `crashed` instead of `killed` — and asserts the real Postgres
sentence is in the record. Then: `killed` and `survived` records carry neither
field; a crashed record still resumes without re-execution; and the size
argument is PINNED rather than left as arithmetic — two maximal 64 KiB tails
of the most expensive-to-escape character `ensure_ascii=True` has still
serialize under `MUTATION_STATE_RECORD_LIMIT` (1 MiB).

`docs/CONSUMERS.md`'s mutation-state paragraph documents the fields, names
`crashed` as the only bucket that populates them and why, and states these
files are diagnostic state whose content is untrusted subprocess output.

---

## 6. B074 — `767393d1` (added by controller ruling, after the first hand-back)

`verify.py`'s `verify_text` `except` tuple becomes
`except (json.JSONDecodeError, ValueError, RecursionError)` — the **same three
names** the other two sites carry, deliberately, so a reader comparing the
three finds one shape rather than three variants. (`ValueError` is the
superclass `JSONDecodeError` already belongs to; spelled out for that
symmetry, not because it adds reach here.)

Red-first, confirmed by stashing the fix and re-running against the tip: both
`verify_text` and `cmd_verify` raised `RecursionError` on the 200,000-byte
document.

Tests: `tests/test_verify_recursion_depth.py` (8) — the bare function returns
a failure list instead of raising; an injected `RecursionError` pins *which*
exception is caught; two controls (an ordinary syntax error unchanged, and a
legible-but-wrong-shaped document still reaching `verify_document`, asserted
equal to calling it directly — the widening is a CATCH, never a validation
change); and the real consumer path **three ways**, because they are three
different ways in — `cmd_verify`'s stdin arm, its file-path arm (through
`_read_file`), and `cli.main(["verify", …])` — each exiting 1 with
`assay verify: not valid JSON` on stderr. Plus a source-level guard asserting
all three untrusted-JSON sites still carry the identical clause, so a fourth
variant cannot appear unnoticed.

`provenance.py:137`'s judgment is **affirmed, not reversed**, and B074's
resolution says so: its input is the installed distribution's own pip-written
`direct_url.json`, the enclosing function is already best-effort, and a
`RecursionError` there would mean a broken install rather than a bad
artifact.

## 7. Review round 1's blocker — `93e6f7fc`

**B072's required sweep was recorded as complete and was not.** Its grep was
`src/assay/*.py`, which does not descend into subpackages: it reported 7
`json.loads`/`json.load` sites; there are **11**. Four were never examined.
Three of those four were genuinely untrusted and reproducibly crashing —
`coverage_parsers/coverage_py_json.py` and
`coverage_parsers/coverage_istanbul_json.py` (a target project's own coverage
tool output, both named by the reviewer) and `adapters/go_stmtpos.py` (a real
external `go` subprocess' raw stdout, named by neither the reviewer's list
nor my table — found by the controller's spot-check, confirmed crashing
here, fixed). All three now carry `RecursionError`; `go_stmtpos` keeps
`UnicodeDecodeError` first because the decode can fail before `json.loads` is
reached, with a control test pinning that arm.

The fourth, `mutation_parsers/mutation_report_json.py`, was already guarded —
and is the proof the old guard test was unfit a **second**, independent way:
it spells the same three names in a different ORDER, which an exact-string
match reports as missing. A "fourth variant" already existed, undetected, by
the very test whose docstring claimed one could not appear unnoticed.

So the guard now derives its subject:
`tests/test_untrusted_json_parse_sweep.py` walks the AST of every module
under `src/assay` (`rglob` — the recursive glob whose absence caused the
first fault), finds every `json.loads`/`json.load` call, and asks whether an
enclosing `try` **names** `RecursionError`, never how the clause is spelled.
Every site is guarded or carries a written reason in `TRUSTED_SITES`; there
is no third disposition. Four guards on the guard: the walk must find a
plausible population and at least one site in each of the three subpackages
the old glob missed (so it cannot pass vacuously); no allowlist entry may be
stale; the eight known-untrusted sites are pinned by name so a silent
deletion shows up; and order-independence is proven against the real module
that differs. Verified red by reverting one guard — two independent tests
fail, naming file and function.

`TRUSTED_SITES` keys on `(module, enclosing function)`, never line number —
which is also why the old table had already gone stale
(`verify.py:2562`→2583, `mutation.py:838`→890). The bar for entry is B074's
own `provenance.py` reasoning, now written down and applied uniformly: assay's
own bytes or its own installation, never a consumer's project or an external
tool, AND a failure means a broken build rather than a bad artifact. Three
sites qualify; the other eight do not and all eight are guarded.

Behavioural red-first tests for each newly-fixed parser live in that parser's
own module, driven through the real entry point (`load_coverage_profile` for
both coverage parsers, `_read_document` for the Go oracle), not only through
the sweep.

Also in this commit, the reviewer's four should-fix nits: the `~60` comment
now says the real number (11 collected items), the drifted line numbers are
corrected and the table now says why they will drift again, the B063 numbers
are re-measured at the final tip (below), and the duplicated `---` separator
in `4-backlog.md` is gone.

## Gate

`./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-b068-quickwins
tester-unified`, **re-run from scratch at `001a1f24`** — a new commit is a
new judged tip, so neither the `b12ec9f2` nor the `767393d1` green was
carried over:

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All 12 `ASSAY_GATE_PHASE` markers, zero `ASSAY_GATE_DIAGNOSTIC` lines.
Verdict read from the log's own markers in a separate step, never a piped
exit code (LESSONS L4). Four runs in total; the REPORT's Gate section
accounts for all of them, including the one I aborted myself.

## Bookkeeping notes for the reviewer

- **One blurred commit boundary.** B071's resolution note in
  `nyxloom-trove/4-backlog.md` was written before the B063 commit and rode
  along inside `e426c29f`. Content is correct; only the boundary is off by one
  file.
- **B074 filed, then fixed** (`verify.py`'s `verify_text` `RecursionError`
  gap), from B072's own required sweep. Filed-not-fixed at the first
  hand-back on the wave's "`assay verify` is unaffected" constraint; the
  controller overrode that reading and it landed as item 6 (`767393d1`).
- **Two pre-existing reds repaired** (`host` → `bare-host`), outside the five
  items but inside the gate's blast radius.
- **Host-load discipline slip, disclosed.** When capping the gate container I
  first ran `docker update --cpus=3` against two of a peer agent's dstdns
  containers before identifying my own (`boring_swirles`). Both were already
  at exactly `3000000000` NanoCpus — the estate-standard cap — so the write
  was a no-op in effect, but it was still a container I did not launch.
