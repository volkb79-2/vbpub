# assay — B068 + quick-wins wave, implementer LOG (2026-09-08)

Branch: `fix/assay-b068-quickwins-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b068-quickwins`
Wave prompt: `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-b068-quickwins.md`
Base: `a78d0280` (the wave prompt + controller log commit)

Five items, five commits, in the prompt's order. No verdict-schema change:
`VERDICT_SCHEMA_VERSION` stays 10, `assay.toml`'s `schema_version` stays 2,
`assay lanes --json`'s `inventory_schema` stays 1. `assay verify` is
untouched by every item.

| # | item | commit | shape |
|---|---|---|---|
| 1 | B068 | `96973575` | fix (b) — diagnostic message; the entry's own premise refuted |
| 2 | B072 | `9cd5ef28` | one-line fix + red-first tests; sweep found a third instance, filed B074 |
| 3 | B062 | `c2d89888` | 31-finding sweep + gate lint-phase widening |
| 4 | B063 | `e426c29f` | skip-with-a-named-reason + a pre-existing gate-blocking red repaired |
| 5 | B071 | `b12ec9f2` | `crashed`-only diagnostic tails in the mutation-state record |

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

**Sweep — NOT clean.** Widened past the four named modules to all 7
`json.loads`/`json.load` sites in `src/assay`. `verify.py:2562`
(`verify_text`, the parser behind `assay verify`, whose whole job is reading a
document assay did not write) has the identical narrow guard and crashes live
the same way. **Filed as B074, not fixed** — this wave's binding constraints
state "`assay verify` is unaffected by every item above", and B072's own
acceptance asks for the sweep result to be *named here*, which filing does.
The entry carries the full 7-site table and the recorded judgment that
`provenance.py:137` (pip-written `direct_url.json`, in an already-best-effort
function) is a preference call rather than a third defect.

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
monorepo's tagged history, and skipping the ~60 that do not would hide real
coverage of assay behind an unrelated property of the checkout.

**Measured**, from a `cp -r` copy of `assay/` into a scratch directory outside
any git repository:

```
2 failed, 4143 passed, 77 skipped, 1 warning in 446.07s (0:07:26)
```

against R-1's `11 failed, 3956 passed, 18 skipped, 13 errors in 821.95s`.
**Zero errors; zero failures attributable to the missing parent repository.**

**Both remaining failures are a pre-existing red on `main`**, unrelated to
B063 and unrelated to this wave: `assert lane["environment"] == "host"` at
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

## Gate

`./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-b068-quickwins
tester-unified`, at `b12ec9f2`: **`tester-unified: PASS (exit 0)`**,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, all 12 `ASSAY_GATE_PHASE` markers, zero
`ASSAY_GATE_DIAGNOSTIC` lines. Verdict read from the log's own markers in a
separate step, never a piped exit code (LESSONS L4). Full detail, including
the aborted first attempt I caused by writing these two documents into the
tree mid-run, is in the REPORT's Gate section.

## Bookkeeping notes for the reviewer

- **One blurred commit boundary.** B071's resolution note in
  `nyxloom-trove/4-backlog.md` was written before the B063 commit and rode
  along inside `e426c29f`. Content is correct; only the boundary is off by one
  file.
- **B074 filed** (`verify.py`'s `verify_text` `RecursionError` gap), from
  B072's own required sweep. Filed, not fixed, on the wave's own
  "`assay verify` is unaffected" constraint.
- **Two pre-existing reds repaired** (`host` → `bare-host`), outside the five
  items but inside the gate's blast radius.
- **Host-load discipline slip, disclosed.** When capping the gate container I
  first ran `docker update --cpus=3` against two of a peer agent's dstdns
  containers before identifying my own (`boring_swirles`). Both were already
  at exactly `3000000000` NanoCpus — the estate-standard cap — so the write
  was a no-op in effect, but it was still a container I did not launch.
