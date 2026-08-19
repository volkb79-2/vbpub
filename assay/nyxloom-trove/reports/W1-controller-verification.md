# Wave 1 — the controller's independent verification, per work item

**What this file is for.** The implementer's report is a claim, not evidence
(A-232). This is the controller re-establishing each work item's behaviour by
**driving the shipped entry points with real inputs**, never by reading the diff
and never by re-running the implementer's own tests. One section per work item,
appended as each lands.

---

## WI-1 — lane schema v2 and immutable policy (`c56a13ea`, `9b02e5e8`)

### Hard constraints, checked mechanically

| constraint | result |
|---|---|
| `cmru/assay.toml` untouched (its gate runs a pinned lane-v1 assay) | **held** — `git diff --name-only` over `cmru/` is empty |
| `nyxloom-trove/carve-assets/**` untouched (frozen evidence) | **held** — empty |
| `tests/fixtures/coverage/**` untouched (frozen) | **held** — empty |
| no `pragma: no cover` introduced | **held** — no added line matches |
| `LANE_SCHEMA_VERSION` bumped | **held** — `config.py:127: LANE_SCHEMA_VERSION = 2` |

Suite re-run by the controller, not quoted from the report:

```text
2591 passed, 11 skipped, 1 warning in 183.32s (0:03:03)
```

### The adversarial probe, and the mistake it caught in ITSELF

`scratchpad/b006a/probe_wi1.py` writes real `assay.toml` files and calls the
shipped `load_lane_file`. It deliberately includes **five cases that must
LOAD**, and that choice paid for itself immediately: the first two runs showed
21 of 27 cases "passing" while every single one refused for an unrelated reason
(`missing required field 'env'`, then `unknown judge key(s): mode`). Every one
of those passes was **vacuous** — the probe was an oracle that could not fail,
the exact defect class this wave has already paid for in O19. Only the failing
controls revealed it. Recorded because the lesson generalises: **a negative-only
probe cannot distinguish "refused for my reason" from "refused for any reason".**

A second self-inflicted error is worth recording for the same reason. The probe
appeared to show that a literal backslash was accepted. It was not: the probe
wrote `"a\b"` into TOML, and **TOML expands `\b` to U+0008 BACKSPACE**, so the
loader was being handed a backspace and was right to be judged on that instead.
Re-probed with the escaping fixed, the backslash is refused correctly. The
finding was in the probe, not the code.

Final result, 27 cases, after both probe defects were fixed: **26 behave exactly
as §3.2 specifies.** Refused with a specific message, each naming the offending
value: absolute; empty; `./x`; `x//y`; `x/`; `../x`; `a/../b`; `a/./b`;
literal backslash; NUL; `.git` as first and as last component; duplicate;
descending; 65 entries; a 4200-byte path; `repository` carrying an omission
list; omission mode with an empty list; an unknown selection value; a missing
selection key; `[isolation]` on an R0-only lane; and no `[isolation]` on an R1+
lane. Loaded, correctly: repository mode; an R0 lane with no table; omission
mode with 1, 3 and exactly 64 entries; and **`src/foo` beside `src/foo_evil`**,
which proves ancestry is decided on path components rather than string prefixes
(A-145's trap).

### One measured behaviour that is NOT a defect, but makes a later requirement load-bearing

An omission path may legally contain TAB, NEWLINE, BACKSPACE or DEL:

```text
TAB U+0009        ACCEPTED as ('a\tb',)
NEWLINE U+000A    ACCEPTED as ('a\nb',)
BACKSPACE U+0008  ACCEPTED as ('a\x08b',)
DEL U+007F        ACCEPTED as ('a\x7fb',)
```

This matches §3.2's component rules and §5.3's schema pattern, both of which
exclude only `/`, backslash and NUL — and it is **correct**, because a Git path
may contain any byte except NUL and `/`. A real repository can carry a symlink
whose name contains a newline.

The consequence lands on WI-2: **`-z` is a correctness requirement there, not a
convenience.** `git update-index --skip-worktree -z --stdin` must never become
an argv of paths, and `git ls-files -v -z` must never lose its `-z` — without
it git quotes and escapes unusual pathnames, so `_verify`'s skip-set comparison
would silently disagree with the declared set for exactly the paths this feature
exists to handle. Sent to the WI-2 implementer with a request for a
newline-in-pathname test, since nothing else in the wave covers it.

### Reported by the implementer, accepted (WI-1)

The carve's WI-1 file list was **under-inclusive**: seven further live test
modules build higher-rigor lanes through shared helpers and needed migration.
They were fixed rather than deselected, which is the right call — deselecting a
live test to go green is the failure this project's frozen-asset discipline
exists to prevent. Also documented: one of the nine frozen nodes
(`test_closed_attestation_declaration_rejects_every_inert_or_unsafe_shape`) was
a **silently vacuous pass** rather than a mechanical failure, which is exactly
the class of defect the probe above tripped over in its own harness.

---

## WI-2 — P22 unsafe-symlink omissions (`57d620d7`)

### Hard constraints

`cmru/`, `carve-assets/**` and `tests/fixtures/**` untouched; no
`pragma: no cover` added. Suite re-run by the controller: **2607 passed, 11
skipped**.

### One defect found and fixed by the controller

`_classify_symlink_target` is a NEW function in this commit, and it carried a
**structurally dead branch** forward from the code it refactors:

```python
for component in PurePosixPath(target).parts:
    if component == ".":     # <- can never be true
        continue
```

`PurePosixPath` elides `.` while parsing, measured: `"a/./b" -> ('a','b')`,
`"./a" -> ('a',)`, `"." -> ()`. Coverage confirmed it, scoped to the two
isolation modules:

```text
missing lines inside _classify_symlink_target (951-984): [976]
missing branches there: [[975, 976]]
```

The implementer judged it pre-existing because `git diff` aligned the body
lines as context. That reasoning is wrong in a way worth recording: **the
function is new even when the lines inside it are moved**, so its dead branch
is new uncoverable code, and this project forbids that with no `pragma`
escape. Removed, with a comment saying why it must not come back. Re-measured
after removal: `missing lines: []`, `missing branches: []`.

### The probe — driving the shipped substrate, not reading the diff

`scratchpad/b006a/probe_wi2.py` builds a real fixture repository containing, at
once: two unsafe absolute symlinks in different sibling trees, an unsafe
repository-escaping relative symlink, a safe symlink, a dangling
repository-contained symlink, ordinary files beside each, and a repo-ROOT file
the "project" reads — the CMRU shape that killed every subtree-restriction
variant. It then drives the shipped `prepare_snapshot`/`materialize`.

All 27 assertions held. The load-bearing ones:

* **repository mode still refuses**, naming the link and its absolute target —
  so the two modes are distinguished by evidence, not by assertion;
* `git status` empty; **`write-tree` == `HEAD^{tree}`** (`64fbc1c6ce94` both);
* the `ls-files -v -z` skip set is **exactly** the three declared leaves and
  nothing else, parsed byte-exact on uppercase `S`;
* each omitted leaf is absent by `lstat`, while **all eight** retained paths
  survive — including the repo-root file, the sibling tree's ordinary files,
  the safe symlink (still resolving) and the dangling one (still dangling);
* the **source checkout is untouched**: clean status, no skip-worktree bit
  anywhere, and all three symlinks still present in it;
* **the honesty check passes too** — `git show HEAD:other/abs_link` inside the
  snapshot still returns `/etc/passwd`. A-268's "not a sandbox" property is
  demonstrated rather than merely written down.

### A second vacuous pass, caught by re-isolating it

The probe's "declaring a SAFE symlink is refused" case initially passed — but
its message was `symlink other/abs_link targets the absolute path
'/etc/passwd'`, i.e. it refused because *other* links were undeclared, not
because of the safe declaration. Re-run with every unsafe link declared so the
safe entry is the only possible cause, the guard is real and each refusal is
specific:

```text
control (only the unsafe link declared)  -> ACCEPTED (safe link present=True)
+ a SAFE symlink declared                -> 'project/safe_link' names a symlink whose target 'src/app.py' is already P22-safe
+ a REGULAR FILE declared                -> 'other/ordinary.txt' is a regular file ... not a symlink leaf (mode 120000)
+ a path ABSENT at the commit            -> 'project/zz_nope' is absent at commit cd55f86f
+ a TREE declared                        -> 'project/src' is a tree ... not a symlink leaf (mode 120000)
```

That is the guard which keeps the exclusion mechanism from ever hiding source,
tests or a judged target — B005's vacuity hole staying structurally shut — so
it mattered that it not be accepted on a vacuous pass. **Twice now in this
wave a probe of mine has passed for the wrong reason; both times only a
deliberate control exposed it.**

---

## WI-3 — runner collision check and the embargo (`7d2da7f3`)

Landed by the controller rather than the implementer: the agent's turns kept
ending while it waited on background jobs, so the suite and the coverage query
were run here, in the **foreground**, with exit codes captured directly rather
than through a pipe.

```text
EXIT=0
2619 passed, 11 skipped, 1 warning in 180.53s   (2607 baseline + 12 new)
```

Coverage over the new function's line range: **no missing lines, no missing
branches**.

### The probe — the near-misses, which is where this check can only go wrong

`scratchpad/b006a/probe_wi3.py` calls the shipped
`_refuse_coverage_artifact_omission_collision` directly. **10/10** as §3.4
specifies. The three that carry the weight:

* an artifact that **IS** the omitted leaf refuses, and one **beneath** it
  refuses at any depth;
* **`topos/link_evil` and `topos/linkage.json` do NOT collide with the omission
  `topos/link`** — the string-prefix trap A-145 exists to prevent, closed here
  by `PurePosixPath.is_relative_to` on components;
* an artifact that is an **ancestor** of an omission does not collide, which is
  right: B006(b) creates the artifact's parent chain, so only a parent that IS
  or CONTAINS the omitted leaf could overwrite it.

Repository mode is a strict no-op and a lane with no judge is a no-op, both
confirmed rather than assumed.

### The embargo tests, checked for vacuity rather than taken on trust

Both halves are non-vacuous by construction, which is exactly what I asked for
and rarely get:

* half (a) scans every live tracked `assay.toml` with **independent `tomllib`**
  — so a bug in assay's own loader could not hide a live declaration from the
  audit — and **asserts the scanned set actually contains the two lane files
  that exist today**, because an audit that silently scanned zero files would
  prove nothing;
* half (b) checks **real git ancestry**, not a hardcoded "as of today": no
  `assay-v*` tag may descend from WI-1's landing commit. It first asserts that
  commit is still reachable history, since an unreachable commit is nobody's
  ancestor and the check would otherwise pass vacuously forever. A release cut
  tomorrow fails it on the next run.

**Both tests must be revisited when the v6 work lands** — that is when the
embargo lifts by design, not when it becomes inconvenient.

---

## The v5→v6 cut (`71d98965`, `507ca1c7`)

99 files. Suite re-run by the controller, foreground, exit code captured
directly: **2814 passed, 11 skipped, EXIT=0**.

Boundaries held: the frozen coverage fixtures, the locked P22/P23/P26/P33 carve
assets and `cmru/` are all untouched, and no `pragma: no cover` was added.
`materialisation` appears in the shipped tree **only as explanatory prose**
saying why no such field exists — never as a field. `snapshot_policy` is present
in the model, the verifier and the schema.

### The removed validation — checked, because removing a check is not like removing a `continue`

The implementer deleted `config._load_targets`'s `PurePosixPath` round-trip
check as unreachable. Probed against the shipped loader, **every non-canonical
spelling still refuses**, each naming the offending entry: `./src/m.py`,
`src//m.py`, `src/m.py/`, `src/./m.py`, `src/../src/m.py`, `/abs/m.py`,
`../m.py`, empty, and `targets = []`. The component checks do the work; the
round-trip equality was a theorem, not a guard. Removal is safe.

### The finding: §5 contradicted itself, and the code chose correctly

**§5's Declaration half still said "A target may name a FILE or a DIRECTORY",
with the anti-vacuity rule applied to the EXPANSION rather than the
declaration.** That is the third review round's worst finding, never folded in —
B005 was declared "already specified" when B006(a) was recarved, so §5 was left
alone. **§5's Judging step 2 has always said the opposite**: "a regular file —
not a directory, not a symlink".

The shipped code implements the **safe** half, verified by reading the shipped
functions rather than the tests:

* `evaluate._resolve_whole_target` — source-root containment by
  `is_relative_to` on resolved paths, then `if not resolved.is_file()` refusing
  `ERROR`/`BAD_LANE_CONFIG` with *"a whole-target entry is always a regular
  file, never a directory"*, then excluded-dir, adapter-source-glob and
  test-path gates;
* `evaluate_targets` — **per declared target**, `file_cov is None or not
  target_executable` ⇒ `NO_MEASUREMENT`/`TARGET_NOT_MEASURED`. No expansion
  exists anywhere: `rglob`/`iterdir`/`walk` appear nowhere in `evaluate.py`.

So **the vacuity hole is closed**: a directory of 36 files with one measured
cannot pass, because a directory cannot be a target at all. The contract has
been corrected to match, with the withdrawn rule struck through and the reason
kept, since the usability argument for it was real and will be made again.

**The implementer chose right but did not report the contradiction.** That is
the fourth time this wave a superseded instruction has survived in text a reader
would follow — and the first time the reader happened to follow the other half.

### Deferred to the consolidated pre-merge review

The end-to-end anti-vacuity proof — a real `whole_target` lane, through the CLI,
whose target is absent from the artifact — is **not** discharged here. It
belongs to the controller's own review step, which drives the shipped entry
points against real inputs on disk. Recorded so it cannot be assumed done.

**DISCHARGED — see the next section.**

---

## B005 end to end, through the real CLI (the debt above)

`scratchpad/b006a/e2e_b005.sh` builds a real git repository — a covered module,
a module nothing imports, a module with an untaken branch, a test suite — and
drives **`python -m assay.cli run`** four times. Not the test suite, not a unit
call: the shipped command-line entry point, against a real commit.

```text
lane ok         exit=0  PASS                                     targets=['src/pkg/covered.py']
lane unmeasured exit=1  FAIL/UNCOVERED_LINES                     targets=['src/pkg/never_imported.py']
lane partial    exit=1  FAIL/UNCOVERED_LINES                     targets=['src/pkg/partial.py']
lane vacuous    exit=3  NO_MEASUREMENT/TARGET_NOT_MEASURED       targets=['src/pkg/never_imported.py']
```

Every verdict carries `schema_version=6`, `snapshot_policy=repository`, and
`judgment.r1: mode=whole_target` with the declared targets — so v6's three new
wire facts are witnessed by a real run rather than by a fixture.

**`vacuous` is the proof B005 exists for.** Its argv narrows coverage to
`--cov=pkg.covered`, so the declared target is **absent from the artifact
entirely** — precisely the shape of the stopgap this feature replaces, where
`--cov=` naming a module that was never imported reports *100% of zero* and
passes. assay refuses it. Contrast `unmeasured`, whose identical target under a
wide `--cov=pkg` **is** present at 0% and correctly FAILs rather than refusing:
the two outcomes are distinguished by evidence, which is what makes
`TARGET_NOT_MEASURED` meaningful rather than a synonym for failure.

`ok` is the must-succeed control. Without it a judge that refused everything
would have scored three out of four.

### A consumer trap found by doing this, not by reading

The first attempt returned `NO_MEASUREMENT/DIRTY_TREE` on every lane, and the
fixture's `git status` was clean. The cause was **coverage.py's own `.coverage`
data file**, which it writes into the working directory even when the report is
JSON elsewhere — untracked, unignored, and therefore real dirt under the
snapshot's post-run check. **The guard was right and the fixture was wrong.**

This will hit the first consumer who adopts a whole-target lane and has not
ignored `.coverage`, and the diagnostic they will see says `DIRTY_TREE`, not
"your coverage tool wrote a data file". Handed to WI-5 for `CONSUMERS.md`.

