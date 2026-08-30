# Adversarial Review: assay B018 / B019 / B035 — the v8 synergy wave

**Reviewed:** 2026-08-30
**Reviewer:** independent adversarial agent (no implementation context)
**Branch:** `feature/assay-b018-b019-b035-v8-synergy`
**Head reviewed:** `0a315100` — **not** `745ac377`, which is the commit the implementer's
own transcript covers.
**Base:** `4575501434`
**Report under review:** `nyxloom-trove/reports/assay-B018-B019-B035-v8-synergy-REPORT.md`

Everything below was re-derived from the diff, the pre-wave backlog text
(`git show 4575501434:assay/nyxloom-trove/4-backlog.md`) and from running things. Where the
report and my measurement disagree, the measurement is quoted.

---

## What I reproduced (so the findings below are read against a verified baseline)

| claim | how I checked it | result |
|---|---|---|
| the registered gate is green | `bash tools/tester-unified-gate.sh ..` from `assay/`, at HEAD `0a315100`; exit code read from a separate file, phase markers grepped in a separate step | **GATE_EXIT=0**, all 11 `ASSAY_GATE_PHASE` markers in order + `ASSAY_REGISTERED_GATE_COMPLETE=1`, no `ASSAY_GATE_DIAGNOSTIC`. The self-hosted lane reports `commit: 0a315100…` — the true head, one better than the report's run |
| `3354 passed, 11 skipped` | `python -m pytest tests -q`, exit read separately | **exact match**: `3354 passed, 11 skipped, 1 warning in 319.97s`, exit 0 |
| B018 shape 1 — installed wheel | built a wheel from the hash-pinned offline closure, `pip install --no-index --no-deps`, ran `identify_judge()` | digest `2dc99a2d…` == host `sha256sum` of the wheel |
| B018 shape 2 — zipapp | built real artifacts with `gate/distribution/build_release.py`, ran under `PYTHONPATH=<pyz>` | digest `d3fe049c…` == `sha256sum` == the shipped `.pyz.sha256` sidecar |
| B018 shape 3 — bare source | source tree on `sys.path`, no install | refused, no identity |
| B018 shape 4 — shadowed install | wheel install + a **clean** source copy first on `sys.path` | refused by the shadow guard (`provenance.py:248`), naming both paths |
| the shadow guard is load-bearing | bypassed it and called `provenance._installed_wheel_digest(dist)` in the shadowed process | returns the wheel's **real** sha256 while `assay.__file__` is in the other tree — i.e. without the guard the record would be *false*, not absent. **Claim upheld.** |
| the shadow test is a real test | read `tests/test_cli_provenance_and_request_base.py:190` — its `_wheel_dist` carries a valid `direct_url`, so deleting the guard makes `identify_judge` return an identity | genuine; would fail |
| B019 end to end | throwaway two-commit repo, driven through the **installed wheel** | all three refusals fire with the documented text; happy path records `judgment.resolved.base` == the requested SHA, `base_resolution: "merge-base"`, and `assay verify` exits 0 |
| frozen-asset lockstep | `cmp W4/verdict.schema.v8.json src/assay/schemas/verdict.schema.json` | **identical**. And `cmp W2/verdict.schema.v7.json <packaged schema at base>` is identical too, so W4 follows W2's convention exactly |
| the W2→W4 template migration | diffed all six templates pairwise | exactly `schema_version` 7→8 in all six + `r2.mode = "changed_lines"` in the two carrying an `r2`. **Nothing else.** Claim verified |
| `decisions.md` is append-only | `git diff --numstat 4575501434..HEAD -- nyxloom-trove/decisions.md` | **+19 / −0**. No pre-existing row touched |
| A-331 is revertible in isolation | `git log 74c89475..HEAD -- <each of its six files>` | empty for all six — `git revert 74c89475` applies cleanly |
| no stale v7 references | swept `*.py/*.md/*.json/*.toml` excluding frozen generations, reports, CHANGES, backlog, decisions | none. Packaged `$id` is `urn:assay:schema:verdict:8`; the `mutation_operator` `oneOf` python branch is the original four |
| every fixture migrated | scripted audit of all 48 `tests/fixtures/verdicts/*.json` | all at `schema_version: 8`; every `judgment.r2` carries `mode` |
| `judge_provenance` reaches every verdict path | scripted scan of all `refuse_lane` / `_refuse_lane_with_plan` / `assemble_verdict` call sites in `runner.py` + `cli.py` | 16/16 thread it; none missed |
| the "prose only" gap | `git diff --name-only 745ac377..HEAD \| grep -v '\.md$'` | empty. Holds at the **true** head, not just the one the report names |

The engineering is strong. The findings below are about records, coverage claims and one
deployment shape — not about the three features being wrong.

---

## Blockers

None. The gate is green at the true head, the schema cut is complete and consistent across
all three layers plus the frozen copy, and every claim I could reproduce, reproduced.

---

## Major

### M1 — All three backlog items are left reading as OPEN

`nyxloom-trove/4-backlog.md:3109` still says, verbatim:

```
**Status:** open. Not a live defect — a gap in what the artifact can prove.
```

for B035 — the item this wave's entire schema bump exists for. B018 (`:1825`) and B019
(`:1852`) carry no `Status:` line at all, and all twelve acceptance checkboxes across the
three items are still `- [ ]`.

This project's convention is unambiguous and was followed by every recent wave, on the
implementing branch, before merge:

- B030 `:2627` — `**Status:** RESOLVED 2026-08-25 (A-319). Fixed by deleting…`
- B031 `:2710`, B032 `:2820` — same shape
- B033 `:2906` — `**Status:** **FIXED 2026-08-26 (A-325)**, on branch …`
- B034 `:3013` — `**Status:** **FIXED 2026-08-26 by WITHDRAWAL (A-326)**, on branch …`

The one backlog edit this wave *did* make (`4-backlog.md:1778`, B017's third recurrence) shows
the author was in the file. Leaving the three implemented items open is the same class of
omission A-331's own reasoning condemns two rows later in `decisions.md:735`: *"an obligation
whose deadline is a specific event, skipped at that event because a later brief did not name
it, does not survive to the next one."*

**Fix:** three `Status:` lines and twelve checkboxes, plus a note on B018's fourth acceptance
criterion (see N1).

### M2 — A-331's coverage claim is false; two of its three reordered call sites are untested

`decisions.md:735` (A-331) states:

> Both call sites are reordered and pinned by a parametrized test over both names asserting
> the message says "withdrawn" and does NOT say "unknown operator".

There are **three** call sites, not two — `config._load_mutation` (`src/assay/config.py:1857`),
`cli._cmd_run` (`src/assay/cli.py:391`) and `cli._cmd_plan` (`src/assay/cli.py:691`) — and the
only ordering test, `tests/test_adapters_python_semantic_operators.py:196`, drives
`load_lane_file`. It never touches `--operators`. No test in the suite passes a withdrawn
name to `--operators` at all: `grep -rn 'uuid-equality-swap\|enum-comparison-swap' tests/`
returns hits in exactly one module, and that module never invokes the CLI with the flag.

This matters *because of A-331 itself*. Under v7 the two orders were indistinguishable; at v8
they are not, and A-331 calls the reordering "load-bearing and … the whole cost of the
deletion". The cost is therefore unguarded at the two sites the decision names.

Measured, not argued: I reverted **both** CLI reorderings in the working tree and re-ran the
full suite.

```
unmodified HEAD : 3354 passed, 11 skipped, 1 warning in 319.97s   exit 0
both CLI reorders undone : 3354 passed, 11 skipped, 1 warning in 329.87s   exit 0
```

**Identical.** Not one test moved. `src/assay/cli.py` was restored to byte-identity with
`HEAD` afterwards.

**Fix:** one parametrized test per verb driving `assay run|plan --operators <withdrawn>` and
asserting `"withdrawn"` in the message and `"unknown operator"` not in it — the loader test's
own assertions, one layer up. Alternatively correct A-331's sentence to say what is actually
pinned.

### M3 — `--require-judge-provenance` hard-fails a legitimate install shape that CIU §10.5 names, and the refusal misdiagnoses it

PEP 610 writes `direct_url.json` only for *direct* installs (a local path, a URL, a VCS ref).
An ordinary index install — `pip install assay==2.3.0` from a private index, inside an image
build — writes none. Measured, by moving the file aside on a real install:

```
$ mv .../assay-*.dist-info/direct_url.json{,.disabled}
$ python -c "from assay import provenance; print(provenance.identify_judge())"
(None, "the installed 'assay' distribution records no PEP 610 direct_url.json naming an
 installed wheel and its sha256, so the artifact this process was installed from cannot be
 identified -- an editable install, a directory install, and a source checkout carrying
 `*.egg-info` build residue are all exactly this case")
```

Two problems, in order of consequence:

1. **The CIU shape this breaks is one CIU explicitly plans for.**
   `ciu/docs/CIU-V8-TESTING-GATE-PROPOSAL.md:2361-2362` gives CIU two options at gate
   preparation: *"Either mounts the verified artifact into the test-runner container, **OR
   requires a runner image already carrying the pinned judge**."* The first path works (a
   mounted `.pyz`, or a wheel installed from a file). The second path produces **no
   `judge_provenance` at all** if that image was built with an index install — and a CIU gate
   following `docs/CONSUMERS.md`'s own advice ("do not merely read the field: **demand it**")
   would then hard-refuse `ERROR`/`BAD_LANE_CONFIG` on a correctly pinned, correctly verified
   judge. Nothing in the wave says which install shapes can satisfy the flag.
2. **The refusal message reads as an exhaustive list and omits this case.** "an editable
   install, a directory install, and a source checkout carrying `*.egg-info` build residue are
   all exactly this case" (`src/assay/provenance.py:276-282`) sends an operator looking for a
   source-tree problem that does not exist. The honest reading of the code is broader: *any*
   install that is not a direct-URL/local-file wheel.

The behaviour is *correct* — assay genuinely cannot identify the artifact, and refusing is
B018's whole point. What is missing is that the limitation is nowhere stated, in a wave whose
entire purpose is a CIU-facing contract.

**Fix:** name the index-install case in the refusal reason, and add one paragraph to
`docs/CONSUMERS.md`'s `judge_provenance` section listing the install shapes that yield an
identity (direct-URL/local-file wheel; zipapp) and the ones that do not.

---

## Minor

### m1 — The B017 recurrence record states a cause I could not reproduce, and it is wrong

`4-backlog.md` (recurrence 3) and report §5.2 both assert that
`ciu.global.worktree.toml.j2` *"reds not only assay's dirty check but the gate's own
pre-flight (`tester-unified-gate: assay has uncommitted changes; …`)"*.

It does not. That pre-flight is pathspec-limited (`tools/tester-unified-gate.sh:270`):

```bash
if [[ -n "$(git -C "$worktree" status --porcelain=v1 -- assay)" ]]; then
```

and the `.j2` sits at the **worktree root**, outside `assay/`. Measured with the file restored:

```
plain status:                    ?? ciu.global.worktree.toml.j2
pre-flight query (-- assay):     []          <- empty; NOT tripped
assay's own dirty query:         ciu.global.worktree.toml.j2
```

Both files red the lane through `git.dirty_paths`; neither reaches the shell pre-flight. The
operational conclusion ("move both aside") is unaffected, but a B017 recurrence entry whose
whole value is an accurate diagnosis now carries a reconstructed, incorrect one — and it is a
durable record that a fourth recurrence will be read against.

### m2 — The gate diagnostic added for this failure cannot see this class of failure

`28d6e41d` ("name the dirtying paths when the self-hosted lane goes DIRTY_TREE") adds, at
`tools/tester-unified-gate.sh:243-244`:

```bash
echo 'ASSAY_GATE_DIAGNOSTIC=worktree-status-after-the-lane' >&2
git status --porcelain >&2 || true
```

`git.dirty_paths` (`src/assay/git.py:618`, A-177) is deliberately the **union** of
`git status --porcelain -z` *and* `git ls-files --others --exclude-per-directory=.gitignore`,
precisely because porcelain status honours `.git/info/exclude` and assay must not. The
diagnostic runs only the half that hides the B017 class — which is why, as the report itself
records, "that diagnostic printed **nothing**" in the very run it was written for. The commit
subject overstates it: it does not name the dirtying paths in the failure mode that motivated
it.

**Fix:** add assay's own query beside it — one line:
`git ls-files --others --exclude-per-directory=.gitignore >&2 || true`.

### m3 — `f7450b0a` is a knowingly-red commit, which is what the batching rationale said it was avoiding

A-330 / report §1 justify the single large feature commit on the grounds that "a sequence of
knowingly-red commits is worse for a reviewer than one coherent one". The sequence contains
one anyway.

`f7450b0a` adds `test_every_judge_base_source_value_is_documented`
(`tests/test_docs_examples_and_vocabulary.py:305`), which requires the literal string
`request` to appear in README + CONSUMERS + DESIGN-GUIDE. Those three docs are untouched until
`b72a3c5b`, two commits later — `git log 4575501434..b72a3c5b~1 -- README.md docs/CONSUMERS.md
docs/DESIGN-GUIDE.md` is empty. Reconstructing the docs at `f7450b0a`:

```
judge.base_source value 'declared' present: True
judge.base_source value 'request'  present: False     <- test fails here
```

So `git bisect` over this branch hits a red tree. Not serious in itself; it is worth recording
because the batching call was argued *from* the absence of exactly this.

### m4 — `test_every_judge_artifact_kind_is_documented` is vacuous

`tests/test_docs_examples_and_vocabulary.py:315` checks that `"wheel"` and `"zipapp"` appear as
plain substrings anywhere in the three docs. At the **base** commit — before a single line of
B018 documentation existed — `wheel` appears 35 times and `zipapp` 6. The test passes on a tree
with no `judge_provenance` documentation at all, so it cannot fail for the reason it exists.

(The sibling `base_source` test is half-real: `request` was genuinely absent at base, `declared`
was not. `judge.mode`'s pre-existing test is fine — `changed_lines`/`whole_target` are
distinctive.)

**Fix:** assert the field name (`judge_provenance`, `base_source`) is documented too, or match
the values in a backticked/quoted context rather than as bare substrings.

### m5 — Dead A-326-era guards left standing in the tests, with comments that are now false

`tests/test_config_mutation.py:78`:

```python
# B034/A-326: two `python:*` names are still SPELLABLE (so a v7
# artifact naming them keeps verifying) but no longer DECLARABLE.
if operator in WITHDRAWN_MUTATION_OPERATORS:
    continue
```

and `:143`:

```python
# ... a withdrawn operator is still spellable in a v7 artifact, so it
# stays in `MUTATION_OPERATORS` ...
if operator in WITHDRAWN_MUTATION_OPERATORS:
    assert operator not in str(exc.value)
```

Since A-331, `WITHDRAWN_MUTATION_OPERATORS ∩ MUTATION_OPERATORS == ∅`, so both branches are
unreachable and both comments assert the opposite of what the code now does. This is the exact
"a filter that reads like a live guard and can never fire is itself the defect class" argument
A-331 used to justify deleting the sibling filters in `config.py` — applied there, missed here.

### m6 — `W3/`'s manifest says "Never edit them", and this wave edited a file in `W3/`

`nyxloom-trove/carve-assets/W3/MANIFEST.md:5` reads **"These are evidence. Never edit them."**
This wave migrated `W3/expected/dstdns-sql-r2-v6-witness.json` in place (7→8, `r2.mode`), which
the v6→v7 cut (`b6d9615c`) also did — so the *practice* has a precedent. But the manifest
covers only the A-279 ordering pair and never mentions the `expected/` witness at all, and
`W3/` now holds files at two different schema versions (`a279-*.json` frozen at 6, the witness
at 8). A-330 flags the stale *filename* but not this tension. A reader following the manifest
concludes the migration was a violation.

**Fix:** one paragraph in `W3/MANIFEST.md` distinguishing the frozen A-279 pair from the
live-comparison witness in `expected/`.

### m7 — `ciu/assay.toml:49` is the one estate lane that pays for "refuse both"

The A-328 design matches the CIU proposal as written: §10.10
(`CIU-V8-TESTING-GATE-PROPOSAL.md:2565-2592`) says `# NO base_ref here — that comes from the
request` and "The lane reads it from there, not from its own config", and the GateRequest field
table (`:2537`) does not list base among its overridable fields. Refusing precedence forecloses
nothing CIU asked for. I surveyed every `assay.toml` in both repos:

- dstdns's five lanes with a `judge.base` (`p128_persister_lineage:157` and the four
  `worker_execution_admission_r2_*` at `:757/:783/:808/:833`) all pin the **same frozen SHA**
  and migrate for free — delete one line, add `base_source = "request"`. No refusal.
- `dstdns/assay.toml`'s `cw2b_schema` (`:90`) is `whole_target` and sets **no** base on main, so
  it is unaffected except by refusal (c) if CIU blanket-passes `--request-base`.
- **`ciu/assay.toml:49` sets `base = "origin/main"`** — a *symbolic, self-updating* ref, and
  literally the value §10.10's own example request supplies. To become CIU-drivable it must
  delete that line, after which `assay run ciu` with no `--request-base` is a hard refusal
  where today it runs standalone from any checkout. Four more lanes share this shape on a
  dstdns branch. That is the real, if small, cost of the design, and A-328 does not name it
  even though it uses `base = "origin/main"` as its illustration.

Not a defect. Worth a migration note in `docs/CONSUMERS.md` so the first consumer to hit it
is not surprised.

### m8 — CHANGES.md's `[Unreleased]` section still carries B033/B034 entries that shipped in 2.4.2

`assay-v2.4.2` is tagged, and `CHANGES.md:160`'s generated section names `a667862c` /
`6e0dca84` — the B033/B034 fixes. Their hand-written `[Unreleased]` entries were never cleared,
so the next release's notes will re-announce them. This wave **edited one of those stale lines**
(`CHANGES.md:51`, appending A-331's supersession) rather than noticing they belong to a
released version, which makes the staleness harder to spot, not easier. Pre-existing, but
touched here.

### m9 — No cross-repo note left for CIU

CIU §11.3 (`:2205-2206`) asks for `judge.version` and `judge.sha256` as the minimum viable
shape. The wave delivers a superset under different names —
`judge_provenance.{name,version,artifact,digest_algorithm,digest}` — which matches B018's own
backlog text exactly, so the implementation is right. But the estate convention is that
cross-tool findings are filed in the other tool's backlog, and nothing was filed in
`ciu/KNOWN_ISSUES_TODO_BACKLOG.md` telling CIU (a) the field name and shape it will consume,
(b) that `--require-judge-provenance` exists, or (c) M3's install-shape constraint. The report
says only "Nothing in `ciu/` or `dstdns/` was touched", which is scoping, not closure.

---

## Nitpicks

- **N1.** B018's fourth acceptance criterion — *"existing v7 consumers tolerate the optional
  fields before V8 requires them"* — is **not met**, and cannot be, because B035's hard cut
  ships in the same release: a v7 consumer refuses the whole document on `schema_version`.
  Report §2 says this plainly and honestly; it just needs to be the reason the checkbox is
  annotated rather than ticked when M1 is discharged.
- **N2.** Report §7: "The branch head is one commit later (`7515c57d`)". It is **two** later —
  `0a315100`. The substantive claim (the gap is `.md`-only) still holds at the true head; I
  verified it and gated at `0a315100`, so this is stale prose, not a broken argument.
- **N3.** `W4/MANIFEST.md:3` says "Captured 2026-08-29"; `decisions.md:718` heads the wave
  "2026-08-30".
- **N4.** `verify._check_base_matches_the_tiers_present`: a foreign document whose `r2` is
  present but not a dict, carrying a base, now gets "judgment carries neither r1 nor r2".
  Cosmetic; the document is rejected either way.
- **N5.** `qualify_topos._check_judge_provenance` validates name/version/kind/algorithm/64-hex
  but never the digest **value** (it has no wheel path). Report §6.4 flags "validated then
  removed" but not this; the only value-level binding in the gate is the shell's
  `require_emitted_judge_provenance`, so that one function is a single point of failure for
  B018's central claim. Acceptable — it is well-tested in
  `tests/test_distribution_gate.py` — but worth knowing it is not belt-and-braces.
- **N6.** No test asserts directly that a **v8 verdict** naming a withdrawn operator is
  refused (A-331's / CONSUMERS.md's "a v8 verdict naming one fails validation"). It is covered
  transitively: `tests/test_verdict_schema_is_packaged.py:63-78` pins the schema's `oneOf`
  branches to `MUTATION_OPERATORS_BY_LANGUAGE` exactly, and I confirmed the packaged schema's
  python branch is now the original four. A one-line artifact-level negative in
  `W4/test_acceptance_v8.py` would close it.
- **N7.** `DESIGN-GUIDE.md:852` said `schema_version: 6` at the base commit — stale since the
  v6→v7 cut, and fixed here in passing. The frozen schema copy has a byte-identity drift guard;
  the *prose* version number has none, and has now been wrong once. A trivial test asserting
  `str(VERDICT_SCHEMA_VERSION)` appears in the docs would prevent a third occurrence.

---

## Things I deliberately tried to break and could not

- **The shadow guard.** I reproduced it against a real wheel install plus a real source tree,
  and proved by bypass that it is the only thing standing between that configuration and a
  *false* digest. One precision note for the record: as this tree actually stands, the example
  A-327 cites — `gate/python/qualify_dstdns_sql.py`'s `sys.path.insert` bootstrap
  (`:918`) — is refused by the **`direct_url` branch**, not the shadow guard, because the
  source tree carries `src/assay.egg-info` build residue and
  `importlib.metadata.distribution("assay")` resolves to *that* dist first. Remove the
  egg-info and the shadow guard is what fires. The guard is real and necessary; the cited
  example does not currently exercise it.
- **The frozen-asset lockstep.** `cmp`-identical, and W2's own copy is `cmp`-identical to the
  packaged schema at base, so the convention was followed rather than approximated.
- **The A-323 recurring class.** `judge_provenance` and `judgment.r2.mode`/`targets` are
  registered in `verify.py`'s raw reconstruction layer (`:1059`, `:1327`, `:1152-1153`) in the
  same commit as the model and schema, and `_reject_unknown_keys` makes the omission fatal
  rather than silent. I checked this first; it is clean.
- **`judge_provenance` threading.** Every refusal shape carries it, including the ones B025 and
  B028 added. A real run through the installed wheel records it on a `NO_MEASUREMENT` verdict.
- **The revert story for A-331.** Genuinely isolated; no later commit touches any of its six
  files.

---

## Verdict

**ACCEPT-conditional.**

The three features are correct, the schema cut is complete and consistent across model, raw
verifier, packaged schema and frozen copy, the registered gate is green at the real head, and
every measurable claim in the report reproduced. Nothing here is worth another round of
implementation.

Conditions, in order:

1. **M1 — close the backlog.** Add `Status:` lines to B018/B019/B035 in the project's own
   convention, tick the acceptance boxes, and annotate B018's fourth criterion per N1. This is
   the one condition I would hold a merge for: three items reading "open" after the wave that
   closed them is exactly the record-drift this project keeps paying for.
2. **M2 — make A-331's claim true.** Either add the two `--operators` ordering tests, or correct
   the sentence in `decisions.md:735` to say only the loader is pinned. A decision row that
   overstates its own coverage is worse than one that admits a gap.
3. **M3 — say what `--require-judge-provenance` can and cannot identify.** Name the
   index-install case in `provenance.py`'s refusal reason and add the supported-install-shapes
   paragraph to `docs/CONSUMERS.md`. Without it, the first CIU integration to use a runner
   image that pip-installed assay from an index gets a hard gate refusal with a message
   pointing at the wrong cause.
4. **m1 + m2 — fix the B017 record and the diagnostic.** Correct the pre-flight claim in the
   recurrence-3 entry (it is assay's dirty check, not the shell pre-flight), and add
   `git ls-files --others --exclude-per-directory=.gitignore` beside the `git status` line so
   the diagnostic can actually see the class of file it was written for.

The remaining Minors (m3–m9) and all Nitpicks are cleanup and can ride a later wave. The scope
call on A-331 was, in my judgement, **correct**: A-326 wrote its own deadline in as many words,
B035 is that deadline, the revert is genuinely one clean commit, and the implementer flagged it
rather than burying it. I would not ask for it to be undone — only for its coverage claim to
match its coverage.

Separately, and not a condition on this branch: the two-line
`/workspaces/vbpub/.gitignore` fix (report §6.3) should be taken by whoever owns the estate
root. I hit the same wall the implementer did, applied the same workaround, and restored both
files; every future gate run from a ciu-created vbpub worktree hits it again until then.

---

## Appendix — what I touched, and how to re-run what I ran

**Worktree state on hand-back.** Every tracked file is exactly as committed
(`git status --porcelain=v1 -- assay` shows only this file, untracked). During M2 I edited
`src/assay/cli.py` in the working tree and then restored it — verified with
`git show HEAD:assay/src/assay/cli.py | diff - src/assay/cli.py`, which is empty. Nothing
else in the repository was modified.

**B017 workaround, applied and undone.** To get a real gate run I moved
`ciu.worktree-instance.json` and `ciu.global.worktree.toml.j2` out of the worktree root and
put both back afterwards; they are present now, byte-for-byte. Anyone re-running the gate has
to do the same until vbpub's root `.gitignore` carries both names (report §6.3 — I hit it
exactly as described, and endorse taking that two-line fix).

**Gate.** From `assay/`:

```bash
bash tools/tester-unified-gate.sh ..          # outer mode wants the repo TOP, i.e. `..`
```

Read the exit code and the phase markers in separate steps (LESSONS L4). Mine:
`GATE_EXIT=0`, eleven `ASSAY_GATE_PHASE` markers, `ASSAY_B006A_CMRU_QUALIFIED=1`,
`ASSAY_REGISTERED_GATE_COMPLETE=1`, and the self-hosted lane naming commit `0a315100`.

**B018 four shapes.** Build the wheel from the gate's own hash-pinned offline closure
(`gate/distribution/build-wheelhouse` + `build-requirements.txt`) into a throwaway
`build-venv`, `pip wheel` the worktree, install into a separate `run-venv` with
`--no-index --no-deps`, and call `provenance.identify_judge()` under each of: the run-venv
alone; `PYTHONPATH=<pyz>` after `gate/distribution/build_release.py`; a source path first on
`sys.path`; and — the one that matters — a source path first on `sys.path` **with no
`*.egg-info` in it**. Copy `src/assay` somewhere clean to get that last shape: with the
worktree's own `src/` the refusal comes from the `direct_url` branch instead, because
`src/assay.egg-info` is build residue that `importlib.metadata` resolves to first.

---
---

# Round 2 — review of the remediation

**Reviewed:** 2026-08-30
**Head reviewed:** `c27273af` (the true branch head; the implementer's own transcript covers
`0fad7842`, three markdown-only commits earlier)
**Round-2 commits:** `9c39e271`, `6b40e3ad`, `0fad7842`, `15dea56d`, `e8990bf5`, `c27273af`

Exactly one source file changed in the whole remediation (`src/assay/provenance.py`, 29 lines).
Everything else is tests, docs and records. My round-1 review was committed verbatim at 454
lines and has not been edited since — checked.

## Verified green, independently

| claim | how I checked | result |
|---|---|---|
| gate is green after remediation | ran `bash tools/tester-unified-gate.sh ..` from `assay/` at **`c27273af`**; exit code and markers read in separate steps | **GATE_EXIT=0**, 11 `ASSAY_GATE_PHASE` markers in order, `ASSAY_B006A_CMRU_QUALIFIED=1`, `ASSAY_REGISTERED_GATE_COMPLETE=1`, **zero** `ASSAY_GATE_DIAGNOSTIC` lines. Self-hosted lane names `commit: c27273af…` |
| `3365 passed` (+11) | `python -m pytest tests -q`, exit read separately | **exact**: `3365 passed, 11 skipped, 1 warning in 366.67s`, exit 0. The arithmetic checks out too: 12 new tests, of which one lives in `carve-assets/W4/` and is not collected by `pytest tests` — hence +11, not +12 |
| gated commit → head is prose only | `git diff --name-only 0fad7842..HEAD \| grep -v '\.md$'` | empty. And 92 changed files vs base, all under `assay/` — the `c27273af` count correction is right |

## M1 — closed, and closed properly. **Discharged.**

Scripted the check rather than eyeballing it: B018 3 ticked + 1 `[~]`, B019 4 ticked, B035 3
ticked, **zero unticked boxes across all three**, and each carries a `**Status:** **FIXED
2026-08-30 (A-3nn)**, on branch …` line in the exact B033 (`:3016`) / B034 (`:3123`) shape.
B035's `**Status:** open.` is gone (`:3219`). The `[~] NOT MET` annotation on B018's fourth
criterion is the right call and explains itself well.

## M2 — discharged, and I confirmed the validation claim myself. **Discharged.**

The implementer says they "reverted both reorderings and all four go red". That is true, and it
is stronger than they claimed. I re-ran it three ways:

```
unmodified                          -> 4 passed
both CLI reorderings reverted       -> 4 failed   (run×2, plan×2)
ONLY _cmd_run reverted              -> 2 failed, 2 passed
                                       (exactly the two `run` params; both `plan` params green)
```

That third run is the one that matters and nobody asked for it: it proves the two CLI sites are
**independently** pinned, which is precisely what `tests/test_cli_run.py:534`'s docstring claims
("a fix applied to one would otherwise leave the other silently wrong"). `src/assay/cli.py` was
restored to byte-identity with `HEAD` afterwards.

A-331's sentence is corrected in place with an explicit "CORRECTED after round-1 review (M2)"
marker naming what it used to say. That is the right way to fix a durable record.

## A-332's refusal to synthesise an index-install digest — **the reasoning holds.**

The coordinator asked for my judgement on whether this is sound or a rationalisation. It is
sound, and I would have argued the same way.

`sha256(wheel)` is a hash of a zip archive. Nothing recoverable after an index install can equal
it: `RECORD` carries per-file hashes of the *extracted* tree, and there is no canonicalisation
contract that maps one to the other. So a "digest over the installed files" could only be
compared against another such digest — which means a new artifact kind, a new closed-vocabulary
member, and a new computation the consumer would also have to implement. That is new design,
which A-112 forbids outright, and in the meantime the field would carry a 64-hex string that
looks exactly like the thing CIU verified on download and is not it. Absence is strictly more
informative than that. **Endorsed, not merely accepted.**

## Blockers

None. No code is wrong, the gate is green at the true head, and M1/M2 are properly discharged.

## Major

### R2-M1 — The "real bug" fixed by A-332 is not reachable through any installer I can find, and the claim that it was measured is false

`decisions.md:746` (A-332) records:

> Measured across the four real URL forms — local `file://`, bare `https`, `?query`,
> `#fragment` — only the fragment form was refused

and the report (§8) presents it as "I probed the wheel branch against the four URL forms **a real
installer produces**". `provenance.py:151` states it as fact: *"PEP 610 records that URL verbatim,
fragment included"*. `CHANGES.md:44-49` announces it to consumers under **### Fixed**.

**pip does not record the fragment.** Measured three ways:

1. Real local install — `pip install --no-index --no-deps "file:///…/assay-….whl#sha256=<d>"`:
   ```json
   {"archive_info": {…}, "url": "file:///…/assay-2.4.3.dev22+g0a315100-py3-none-any.whl"}
   ```
2. Real **remote** install over a loopback HTTP server — the exact shape A-332 names as "how a
   gate pins a judge artifact by URL and digest",
   `pip install --no-deps "http://127.0.0.1:18731/assay-….whl#sha256=<d>"`:
   ```json
   {"archive_info": {…}, "url": "http://127.0.0.1:18731/assay-2.4.3.dev22+g0a315100-py3-none-any.whl"}
   ```
3. pip's own source, in the venv under test (pip 26.1.2):
   `pip/_internal/utils/direct_url_helpers.py:78` and `:89` both build the record with
   `url=link.url_without_fragment`.

`uv` 0.12.1 strips it too (and rejects a fragment on a local path outright). So the old
`endswith(".whl")` test would have identified every one of these installs correctly, and the
"silently unidentifiable" row in the report's table is a property of a hand-written
`_FakeDistribution`, not of any install. The tests confirm this reading: they drive
`_wheel_dist(tmp_path, A_DIGEST, url=url)` (`tests/test_cli_provenance_and_request_base.py:116`),
never an installer.

I also measured the fix's actual behavioural delta by reverting `provenance.py:160` to the
round-1 parse and running the new tests: **1 of 6 fails**, the bare-`#sha256=` case. The
`?t=1#sha256=` case passes under the old parser too, because `split("?")[0]` already truncates
before the fragment.

To be clear about what is and is not wrong:

- **The code change is fine.** Stripping the fragment is correct defensive parsing — PEP 610
  does not forbid a fragment, and the negative test proving an sdist is still refused is real
  and well-judged. I am not asking for it to be reverted.
- **The record is wrong**, in four places that outlive this branch: `decisions.md:746`,
  `provenance.py:145-156`, `CHANGES.md:44-49` (a **Fixed** entry announcing a defect no consumer
  had), and `docs/CONSUMERS.md:806` (whose "why" column implies the fragment previously
  defeated identification).

And it is the same defect class as the one A-333 was written **in the same commit** to close:
*"a diagnosis reconstructed from a plausible mechanism is a hypothesis until the mechanism's own
query has been run."* The mechanism's own query here is one `pip install` and one `cat`.

**Fix:** reword to what it is — hardening for a `direct_url.json` shape PEP 610 permits and pip
happens not to write — and drop or re-scope the `CHANGES.md` **Fixed** entry so it does not tell
consumers they were affected. Keep the code and the tests.

### R2-M2 — The m2 diagnostic fix still cannot see the file it was written for

`tools/tester-unified-gate.sh:254-255` now adds assay's own query beside `git status`:

```bash
echo 'ASSAY_GATE_DIAGNOSTIC=worktree-untracked-by-assays-own-query' >&2
git ls-files --others --exclude-per-directory=.gitignore >&2 || true
```

But `run_self_hosted_lane` does `cd "$worktree/assay"` at `:225`, and **`git ls-files` is scoped
to the current directory**. The B017 files live at the worktree *root*, outside `assay/`.
Reproduced in a scratch repo with the identical shape (root-level untracked file, one of them
hidden by `.git/info/exclude`):

```
from the REPO TOP:
  git status --porcelain                  -> ?? ciu.global.worktree.toml.j2
  git ls-files --others --exclude-…       -> ciu.global.worktree.toml.j2
                                             ciu.worktree-instance.json

from assay/  (what the gate actually runs):
  git status --porcelain                  -> ?? ciu.global.worktree.toml.j2
  git ls-files --others --exclude-…       -> (empty)
```

So for `ciu.worktree-instance.json` — the file that actually caused the red lane, hidden from
`git status` by `.git/info/exclude` — the diagnostic prints **nothing**, exactly as before.
This is the second diagnostic added for B017 that cannot see B017.

Both candidate fixes are tested and work from `assay/`:

```bash
git ls-files --others --exclude-per-directory=.gitignore -- :/      # -> ../ciu.*
git -C "$worktree" ls-files --others --exclude-per-directory=.gitignore   # -> ciu.*
```

The second matches what `git.dirty_paths` actually queries (the repo top), so it is the one I'd
take.

### R2-M3 — The B017 entry now records a "directly observed" ciu invocation that was my own round-1 workaround, and uses it to soften the recurrence

`4-backlog.md:1858-1860` (and the report at `:303`) now states:

> **The files are TRANSIENT, not permanent dirt.** A ciu invocation observed at 01:38
> regenerated this worktree's `ciu.env` and removed both render inputs outright.

and draws the conclusion that "the claim that a reviewer WILL hit it softens to MAY".

That was me. My round-1 review's own appendix says so in as many words — *"I moved
`ciu.worktree-instance.json` and `ciu.global.worktree.toml.j2` out of the worktree root and put
both back afterwards"* — and that file was committed in `0fad7842`, the same remediation commit.
The filesystem agrees:

```
worktree created                     2026-08-29 23:07:10
ciu.env, .j2, .json  (as I found them, preserved in my stash)  2026-08-29 23:07:11
live ciu.env                         2026-08-30 01:38:26   <- my `cp` restoring it
both render inputs                   2026-08-30 02:02:25   <- my restore at end of round 1
```

and `cmp` says the live `ciu.env` is **byte-identical** to the 23:07:11 original I stashed — a
regeneration would have produced new content; a `cp` produces exactly this. Between 01:38 and
02:02 the worktree looked precisely as described: a freshly-dated `ciu.env`, both render inputs
gone. No ciu ran.

Why this matters more than a footnote: it is load-bearing in the entry. It converts a
three-times-recurring, reproducible false `DIRTY_TREE` into an "intermittent" one and downgrades
the warning to a future reader. The mechanism half of that paragraph is fine and I re-confirmed
it; the transience half should go.

The same misattribution runs through the report: `:517` still calls the `0a315100` run "a
**ciu-orchestrated** invocation … launched by something other than this session", while the
header at `:7` correctly says "`0a315100` (the reviewer's own, independent)". That was my
`bash tools/tester-unified-gate.sh ..`. The corroboration survives — a second green run at a
second commit by a second party is real evidence — but the attribution does not.

This is the third confidently-wrong root-cause in this wave on the same axis (the pre-flight
claim, now this, and the un-checkable one below). A-333 names the rule; these are two more
instances of breaking it, one of them written after A-333.

## Minor

### R2-m1 — `uv` installs a wheel from a direct URL and records **no** digest, which the new install-shape table's own rule says cannot happen

`docs/CONSUMERS.md:800` introduces the table with: *"an artifact identity exists only where the
installer recorded one, and it records one only for a direct install."* The rows are all pip.
Measured with `uv` 0.12.1 — which is on `PATH` in this very devcontainer — installing over the
same loopback HTTP server:

```
$ uv pip install --no-deps "http://127.0.0.1:18732/assay-….whl#sha256=<digest>"
$ cat .../assay-*.dist-info/direct_url.json
{"url":"http://127.0.0.1:18732/assay-….whl","archive_info":{}}

$ python -c "from assay import provenance; print(provenance.identify_judge())"
(None, "…records no PEP 610 direct_url.json naming an installed wheel and its sha256…")
```

A direct URL install, with the digest supplied on the command line, is **unidentified** — and
the refusal tells the operator the most common cause is an INDEX install, which it is not. This
is the same trap round-1's M3 described, one installer over. The table is not literally wrong
(its rows say `pip`), but its organising rule is, and a uv-based CI hits a hard
`--require-judge-provenance` refusal that the docs say should not happen.

**Fix:** one row (`uv pip install <direct URL>` → no, uv records `archive_info: {}`) and one
clause narrowing the rule from "a direct install" to "a direct install *by an installer that
records the archive hash* — pip does; uv currently does not".

### R2-m2 — The report contradicts itself about who ran the `0a315100` gate

`:7` says "the reviewer's own, independent"; `:517-521` says "ciu-orchestrated … launched by
something other than this session". One of them was corrected and the other was not.

### R2-m3 — The CIU hand-off note exists only in a session scratchpad

Report §8 says the m9 text is "prepared at `scratchpad/CIU-NOTE-READY-TO-FILE.md`". That path is
session-local and not in the repository, so it disappears with the session. The coordinator has
said they will file it, which resolves it — worth stating only so nobody assumes the artifact is
durable.

### R2-m4 — One remediation claim is not independently checkable

The corrected B017 entry says the pre-flight failure the wave actually saw "came from an
uncommitted edit to `tools/tester-unified-gate.sh`, which IS under `assay/`". That is a claim
about the implementer's own transient working tree; I can neither confirm nor refute it. It is
plausible (the gate script was being edited in that wave) and it moves in the right direction.
Recorded as unverified rather than accepted.

## Everything else from round 1: checked and discharged

- **m4** — the vacuous vocabulary test now asserts the field name and literal-value context, with
  the base-commit counts (`wheel` 35, `zipapp` 6) written into the docstring so the reasoning
  survives. Good.
- **m5** — both dead A-326-era guards deleted, with the replacement assertions written the right
  way round (`for withdrawn … assert withdrawn not in message`).
- **m6** — `W3/MANIFEST.md` now separates the frozen `a279-*` evidence from the live `expected/`
  witness, so "Never edit them" no longer reads as violated by every schema cut. Exactly the fix
  I asked for.
- **m7** — `docs/CONSUMERS.md` carries a migration section that names `ciu/assay.toml`'s
  `base = "origin/main"` case explicitly and says plainly that delegating it costs standalone
  runnability. Better than what I asked for: it gives a decision rule per lane.
- **m8** — flagged unmissably in `CHANGES.md` rather than cleared, with the right justification
  (clearing `[Unreleased]` is the releaser's job). The coordinator owns this.
- **m3** — acknowledged in §1 rather than argued away.
- **N1** — B018's fourth criterion annotated `[~] NOT MET` with the reason.
- **N3** — W4 manifest date corrected to 2026-08-30.
- **N6** — now asserted against the frozen v8 schema itself
  (`W4/test_acceptance_v8.py:100`), differentially: the two withdrawn names must fail
  validation *and* the four survivors must pass, so it cannot go green on an emptied enum. The
  W4 suite went 40 → 41 nodes in my gate run.
- **N4 / N5 / N7** — deliberately not taken, as round 1 allowed.

## Round-2 verdict

**ACCEPT-conditional**, with lighter conditions than round 1.

The remediation is good work. M1 and M2 are fully discharged — M2 with a validation I was able to
reproduce and then strengthen. A-332's central judgement (refuse to synthesise a digest) is
correct and well-argued. The gate is green at the true head, the suite count matches to the test,
and the only source change is nine lines of parser hardening plus a much better refusal message.

What holds it short of ACCEPT is that the wave's own recurring failure mode — asserting a
measurement that was actually a reconstruction — occurred **twice more inside the commit that
added A-333 to stop it**, and one of the two remediations does not do what it says.

Conditions:

1. **R2-M1 — reword the `#sha256=` claim.** Keep the code and the tests; correct
   `decisions.md:746`, `provenance.py:145-156` and `docs/CONSUMERS.md:806` to say this is
   hardening for a PEP 610-permitted URL shape that pip does not currently write, and drop or
   re-scope the `CHANGES.md:44-49` **Fixed** entry so it stops telling consumers they were hit by
   a bug they were not. Two `pip install`s and a `cat` settle it; the transcripts are above.
2. **R2-M2 — make the diagnostic actually see the file.** `git -C "$worktree" ls-files --others
   --exclude-per-directory=.gitignore` (or a `-- :/` pathspec). One word. Without it the fix for
   m2 is the same shape as the thing m2 was about.
3. **R2-M3 — remove the "ciu invocation observed at 01:38" transience claim** from
   `4-backlog.md:1858` and the report at `:303`, and reconcile the report's `:517`
   "ciu-orchestrated" passage with its own `:7`. Both describe my documented round-1 workaround.
   The B017 entry should not carry a softening it did not earn.
4. **R2-m1 — one table row for `uv`.** Measured above; a direct-URL install that records no
   digest is exactly the surprise the table was added to prevent.

None of these touch behaviour. Conditions 1 and 3 are edits to prose that will outlive the
branch; condition 2 is a one-word shell change; condition 4 is one table row. I would merge on
those four, and I do not think a round 3 needs to re-run the gate — nothing in them can redden
it.

## Appendix — round-2 reproduction notes

**Worktree state on hand-back.** All tracked files exactly as committed. During this round I
temporarily edited `src/assay/cli.py` (three times, for the M2 revert experiments) and
`src/assay/provenance.py` (once, to measure the fix's real delta); both were restored and
verified with `git show HEAD:<path> | diff - <path>`, empty in both cases. The two B017 files
were moved aside for the gate and restored byte-for-byte from my stash — `cmp`-checked against
the round-1 copies.

**The measurements anyone can repeat.**

```bash
# R2-M1: does pip record the fragment?
python -m http.server 18731 --bind 127.0.0.1 &      # serve the wheel
pip install --no-deps "http://127.0.0.1:18731/assay-<v>-py3-none-any.whl#sha256=<d>"
cat .../assay-*.dist-info/direct_url.json           # -> url has NO fragment
grep -n url_without_fragment .../pip/_internal/utils/direct_url_helpers.py

# R2-M2: can the new diagnostic see a root-level untracked file?
cd "$worktree/assay" && git ls-files --others --exclude-per-directory=.gitignore   # -> empty
cd "$worktree"       && git ls-files --others --exclude-per-directory=.gitignore   # -> both files

# R2-M3: was ciu.env regenerated, or restored?
cmp <stashed original> "$worktree/ciu.env"          # -> identical
```
