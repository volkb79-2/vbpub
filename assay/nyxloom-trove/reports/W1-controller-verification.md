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

