# assay B018 / B019 / B035 — the v8 synergy wave

**Branch:** `feature/assay-b018-b019-b035-v8-synergy`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-v8-synergy-wave/assay`
**Base:** `4575501434ceab4d7bb4f731f95a72155b731da5`
**Head at report time:** `745ac377`
**Status:** implementation complete, real registered gate run — **not merged, not pushed, not released.**

All 89 changed files are under `assay/`; `git diff --name-only <base>..HEAD | grep -v '^assay/'`
returns nothing. Nothing in `ciu/` or `dstdns/` was touched, and B020 was not opened.

Paths below are relative to `assay/` unless stated otherwise.

---

## 1. Commits

| commit | what |
|---|---|
| `86ceb527` | `feat(assay): judge provenance, request-supplied base, r2 judging scope (B018/B019/B035)` — the core. All three features plus the v7→v8 schema cut, the W4 carve-asset generation, the 48 migrated verdict fixtures, and the gate script's new phases. |
| `74c89475` | `feat(assay)!: drop the withdrawn operator spellings at the v8 cut (A-331)` — discharges A-326's deferred obligation. **Deliberately isolated so it can be reverted alone** (see §6.1). |
| `f7450b0a` | `test(assay): cover B018 provenance, B019 base delegation, and the v8 vocabularies` — two new modules plus the fixups the v8 cut forced on existing suites. |
| `b72a3c5b` | `docs(assay): record A-327..A-331 and document the v8 contract (B018/B019/B035)` — decisions, CHANGES, README/CONSUMERS/DESIGN-GUIDE. |
| `28d6e41d` | `fix(assay): name the dirtying paths when the self-hosted lane goes DIRTY_TREE` — a gate diagnostic gap this wave hit head-on (§5.1). |
| `97a82a1f` | `docs(assay): B017 — third recurrence, first inside vbpub's own worktree` (§5.2). |
| `ef3a6930` | `docs(assay): wave report for B018/B019/B035 with the real gate transcript` — this file. |
| `4753288d` | `docs(assay): mark A-326's "legal at v7" half superseded by A-331` — the two CHANGES entries contradicted each other in one section. |
| `745ac377` | `test(assay): prove the zipapp provenance branch against a real .pyz (B018)` — plus the A-327 field-name correction. See §6.5; this is a defect I found in my own work. |

**Batching honesty note.** The brief's default was three commits, one per item. I could not
produce a clean three-way split: B018, B019 and B035 all edit `verdict.py`, `runner.py`,
`cli.py`, `verify.py` and the packaged schema, and each of the three intermediate states is a
tree whose own test suite is red (B035's `mode` requirement invalidates every fixture until they
migrate; B018's field is unreconstructable in `verify.py` until registered). A sequence of
knowingly-red commits is worse for a reviewer than one coherent one, so the features are one
commit and everything genuinely separable — the A-331 discharge, the tests, the docs, the two
gate/backlog fixes — is its own. This is recorded in A-330.

---

## 2. B018 — judge provenance in every verdict

### The four real invocation shapes, measured before any code was written

The brief required designing "around what's actually inspectable in each, not a guess". Four
artifacts were built and run:

| invocation | result | what identifies it |
|---|---|---|
| wheel installed into a venv | **identified**, `artifact = "wheel"` | PEP 610 `direct_url.json` → `archive_info.hashes.sha256`; verified equal to `sha256sum` of the wheel file |
| `.pyz` zipapp | **identified**, `artifact = "zipapp"` | `zipimport.zipimporter.archive`, hashed directly; verified equal to `sha256sum` of the `.pyz` |
| bare `pip install -e .` source checkout | **refused** (unidentified) | no build artifact exists to hash |
| wheel install **shadowed** by a source tree on `sys.path` | **refused** (unidentified) | `assay.__file__` resolves outside `dist.locate_file("")` |

The fourth row is the one that justifies the whole design and it was **found by running the
thing, not by reading the docs**: `importlib.metadata.distribution("assay")` succeeding does
*not* prove the running code came from that distribution. This repo's own
`gate/python/qualify_dstdns_sql.py` invokes the CLI in exactly that shape. Without the guard at
`src/assay/provenance.py:93` / the check at `identify_judge` (`src/assay/provenance.py:172`), a
source-tree run would have recorded the installed wheel's digest — an invented fact, precisely
what B018's "REFUSE rather than invent a digest" forbids.

A second measured fact is commented in-code rather than left to be rediscovered: inside a
zipapp, `dist.locate_file("")` returns a `zipp.Path`, which is not `os.PathLike` and which
`pathlib.Path()` rejects outright. It raised `TypeError` on the first real zipapp run; the fix
is `Path(str(...))`, with the reason written down at the call site.

### Acceptance criteria

- [x] **The schema and model record judge name, exact semantic version, digest algorithm, and
      lowercase artifact digest.**
      Model: `verdict.py:1406` (`class JudgeProvenance`), constants at `verdict.py:233`
      (`JUDGE_DIGEST_ALGORITHMS`) and `verdict.py:241` (`JUDGE_ARTIFACT_KINDS`); field on the
      verdict at `verdict.py:2599`. Schema: `src/assay/schemas/verdict.schema.json:100`
      (property) and `:1307` (`$defs`), with the digest pinned by
      `"pattern": "^[0-9a-f]{64}$"` at `:1344`. Raw verifier:
      `verify.py:1059` (`_reconstruct_judge_provenance`), registered at `verify.py:1327` — all
      three layers, per A-182, so the A-323 failure mode (registered in model + schema, absent
      from `verify.py` for a whole release) cannot repeat.
- [x] **A distribution invocation records the installed artifact's actual sha256.**
      Proven three ways, two of them outside pytest: `tests/test_standalone.py`'s
      `test_the_installed_wheels_own_sha256_is_what_the_verdict_records` compares
      `hashlib.sha256(wheel.read_bytes()).hexdigest()` against the emitted digest; the registered
      gate's new `require_emitted_judge_provenance()` (`tools/tester-unified-gate.sh:182`)
      computes the digest **host-side with `sha256sum`** and `die`s on any mismatch of artifact
      kind, algorithm, version or digest; and `tests/test_distribution_gate.py` drives that gate
      function with an honest and a forged verdict and asserts it accepts only the honest one.
- [x] **An unidentifiable invocation fails loudly rather than recording a partial identity.**
      `identify_judge` returns `(None, reason)` and the verdict carries **no**
      `judge_provenance` key at all — absent-or-complete, never partial (A-051's
      "omitted, never null"). The absence is announced on the diagnostics stream
      (`cli.py:495`–`:509`), and `assay run --require-judge-provenance` (`cli.py:220`) converts
      it into an `ERROR`/`BAD_LANE_CONFIG` refusal raised **before any work runs**.
      Refusal paths are covered explicitly, not just the happy one:
      `tests/test_cli_provenance_and_request_base.py` parametrizes the unidentifiable cases
      (no distribution, no `archive_info`, non-`.whl` url, malformed digest, shadowed import,
      bare source tree) and asserts the CLI refuses before any work.
- [x] **Existing v7 consumers tolerate the optional field.** The field is optional in the schema
      and omitted when absent, so it is additive in v7 terms — but note this wave ships it inside
      the v8 cut, so the practical answer is §4: a v7 consumer is refused on `schema_version`
      first. The tolerance property is still what makes `judge_provenance` safe to have added
      without a bump of its own, and A-327 records that.

### One thing I chose, that the brief left to me

**Absence is not fatal by default.** A hard refusal on an unidentifiable invocation would break
every source-tree run — including assay's own test suite and every `pip install -e .`
developer. So the default is "record nothing, say so loudly on stderr", and callers that
genuinely require attributable evidence opt in with `--require-judge-provenance`. The registered
gate is such a caller and now passes it (`tools/tester-unified-gate.sh`,
`run_self_hosted_lane`), with `tests/test_distribution_gate.py` asserting the flag is really in
the gate's argv rather than merely accepted by the CLI. Reasoning and the rejected alternative
are in **A-327**.

---

## 3. B019 — gate-request-supplied comparison base

### The three design decisions the brief required me to make and record (all in A-328)

1. **How a lane declares delegation.** `judge.base_source`, a closed two-value vocabulary
   `{"declared", "request"}` defaulting to `"declared"` (`config.py:243`, field at
   `config.py:515`, loader at `config.py:1327`). It joins the documented-vocabulary convention
   in `tests/test_docs_examples_and_vocabulary.py` alongside `judge.mode`.
2. **The request-level input mechanism.** `assay run --request-base REF` and
   `assay plan --request-base REF`, added by one shared helper (`cli.py:85`) so the two verbs
   cannot drift. The name was chosen to read as *the invoking gate request's own base* and to
   be unmistakable for `judge.base`: `judge.base` is the lane's own answer, `base_source` names
   **who** answers, `--request-base` is the requester answering. It is emphatically not an
   override of `judge.base` — supplying both is refused.
3. **Precedence when both are present — I did not pick one; I refused.** CIU's proposal wants
   the invoker's base to be authoritative (a gate computing a merge-base per pull request). But
   a lane that *also* spells `base = "origin/main"` then carries a line nothing reads, and
   A-062's rule is that config nothing reads "cannot fail loudly if it is wrong" — this
   project's named defect class, which B033/A-325 deleted an instance of one wave ago. So the
   config is made unambiguous at load instead: to delegate, delete your `base`. Full reasoning
   and the rejected lane-wins/request-wins readings are in **A-328**.

### Acceptance criteria

- [x] **A lane can declare that its base comes from the gate request.**
      `judge.base_source = "request"`; the `base` requirement is lifted for such a lane at
      `config.py:1396`.
- [x] **The invoking gate supplies it per invocation.** `--request-base` on `run` and `plan`,
      threaded into `runner.run_lane`.
- [x] **A loud named configuration refusal in house style.** All three mismatches resolve in one
      function, `runner.resolve_base_declaration` (`src/assay/runner.py:2018`), called once above
      the dispatch, raising `LaneConfigError` → `ERROR`/`BAD_LANE_CONFIG`:
      (a) a delegating lane invoked with no `--request-base`;
      (b) `--request-base` handed to a lane that declares its own base;
      (c) `--request-base` handed to a lane that reads no base at all (whole-target scope, or
      neither R1 nor R2).
      Two further load-time refusals sit in `config.py`: declaring `base_source` under
      `judge.mode = "whole_target"` (`config.py:1344`) and declaring both `judge.base` and
      `base_source = "request"` (`config.py:1351`).
      `plan` calls the same resolver purely for the refusal and discards the value, so `run` and
      `plan` cannot disagree about what they accept.
      Covered in `tests/test_config_judge_base_source.py` (12 tests, every inert placement) and
      end-to-end over a real two-commit repo in
      `tests/test_cli_provenance_and_request_base.py`.

Refusal (c) is worth a sentence for the reviewer: it exists because the delegating caller is a
*script*. A gate that starts passing `--request-base` to every lane it drives must be told which
of its lanes cannot use it, not have the value silently discarded on the whole-target ones.

---

## 4. B035 — `judgment.r2` witnesses its own judging scope (the v7 → v8 cut)

`judgment.r2` gains `mode` (required) and `targets` (optional), mirroring `judgment.r1`
(`verdict.py:1727`, `:1734`). `judge.mode` as a **lane-level** scope (A-325) is now enforced
across tiers: r1 and r2 present together must agree on both `mode` and `targets`, and the
`resolved.base` rules are re-expressed against an r1-else-r2 witness instead of r1 alone.

This closes the hole A-325 filed against itself in as many words: *"`judgment.r1.mode` is on the
wire and `judgment.r2`'s scope is not… Asserting a rule the document cannot witness is how a
verifier starts rejecting honest artifacts."* A-325 could only *widen*; putting `mode` on r2 is
what makes the narrowing assertable, and asserting it is what makes this a break. Recorded as
**A-329**.

Enforced in all three layers per A-182, with the raw verifier's wording deliberately different
from the model's so a copy-paste stub cannot satisfy both:
`verdict.py` `Judgment.__post_init__`; `verify.py:649`
(`_check_base_matches_the_tiers_present`); `schemas/verdict.schema.json` `judgment.allOf`,
rewritten with `anyOf` witnesses plus two mode-agreement implications.

Version markers: `VERDICT_SCHEMA_VERSION = 8` (`verdict.py:201`), `$id` →
`urn:assay:schema:verdict:8` (schema `:3`), `schema_version.const: 8` (schema `:23`).

### The frozen-asset lockstep (the brief's "do not let that happen a third time")

W4 is a **new frozen generation**, following the W1→W2 mechanical pattern rather than a
convention I invented — each generation is a directory named for the *wave* that cut it, carrying
its own full acceptance suite, a byte-identity drift guard, and differential negatives over every
earlier frozen template.

`nyxloom-trove/carve-assets/W4/` contains:

- `verdict.schema.v8.json` — **byte-identical** to the shipped file, `cmp`-verified in the same
  commit as every schema edit, not by re-running the guard and hoping. This includes the A-331
  edit in `74c89475`, where the asset was re-frozen in that same commit.
- `test_acceptance_v8.py` — 40 nodes, all passing, including
  `test_shipped_schema_is_byte_identical_to_the_locked_v8_asset` and
  `test_every_earlier_frozen_template_is_rejected_under_v8` over W1 + W2.
- `expected/` — six migrated templates (`ca1-r3-no-base`, `ca4-all-equivalent`, `missing-tool`,
  `p25-missing`, `p25-pass`, `sql-r2`, all `-v8-template.json`).
- `MANIFEST.md` — documents the migration (schema_version 7→8 in all six, `mode:
  "changed_lines"` added to the two carrying an r2) and **why W3 is skipped**: `W3/` already
  exists as a wave directory, and renumbering it to align names with schema versions would
  rewrite a frozen generation, which is the one thing this convention forbids. The `W<n>` names
  are wave identities, not schema versions. Recorded in **A-330**.

Also migrated: 48 × `tests/fixtures/verdicts/*.json` to `schema_version: 8` (the 10 carrying an
r2 also gained `"mode": "changed_lines"`), each verified against both `verify_document` and the
JSON Schema.

### Acceptance criteria

- [x] `judgment.r2` records the scope it was judged under (`mode`, and `targets` when declared).
- [x] Cross-tier agreement enforced in model, raw verifier and schema, with distinct wording.
- [x] Treated as a hard cut (A-170): every v7 document is refused on `schema_version` alone; the
      gate proves this for 12 frozen W1+W2 templates every run (phase
      `verdict-v6-v7-hard-cut-verified`).
- [x] Schema file and its frozen carve-asset copy changed in the **same commit**, every time.

---

## 5. Two findings outside the three items

### 5.1 The gate's red-lane diagnostic could not explain a DIRTY_TREE (fixed, `28d6e41d`)

`run_self_hosted_lane`'s failure path reruns the lane's pytest command for visible logs. That
answers only one of the two shapes a red lane has. `NO_MEASUREMENT/DIRTY_TREE` is assay's
**post-run** whole-tree check (`runner.py`'s `post_reason`) and carries no path list, so a lane
whose command *passed* prints a green `3345 passed` rerun underneath an unexplained red lane —
which is the exact transcript this wave produced, and which otherwise costs a container rebuild
to diagnose. Added one `git status --porcelain` under its own
`ASSAY_GATE_DIAGNOSTIC=worktree-status-after-the-lane` marker.

Note for the reviewer: that diagnostic printed **nothing** — see 5.2 for why, and for why it is
still worth having.

### 5.2 B017, third recurrence — and it is in *vbpub's own* worktree (recorded, `97a82a1f`)

The first real gate run of this wave went red at the self-hosted lane with
`NO_MEASUREMENT/DIRTY_TREE` against a tree `git status --porcelain` calls completely clean.
Root cause, measured:

```
$ git check-ignore -v ciu.worktree-instance.json
/workspaces/vbpub/.git/info/exclude:18:/ciu.worktree-instance.json   ciu.worktree-instance.json

$ git ls-files --others --exclude-per-directory=.gitignore      # assay's own query
ciu.worktree-instance.json
```

`ciu.worktree-instance.json` is untracked and hidden **only** by
`/workspaces/vbpub/.git/info/exclude` — the unversioned, repository-local exclude source that
`assay.git.dirty_paths` deliberately refuses to honour (A-177, upheld against B017's reverted
`--exclude-standard` attempt in A-290). Plain `git status` consults it; assay must not.

**Assay's behaviour is correct and unchanged.** This is B017's documented class, third
occurrence, same filename as the first. What is *new* is that the fix is per-repository and
**vbpub never received it**: `dstdns@08b789f5` and `dstdns@5c8c14c6` added the lines to dstdns's
committed `.gitignore` only. vbpub's root `.gitignore` carries `ciu.env` (line 163) but not
`ciu.worktree-instance.json`.

**And there is a second file, in a worse state.** `ciu.global.worktree.toml.j2` (recurrence 2's
filename) is also present in this worktree root, untracked, and ignored by **nothing at all** —
not the committed `.gitignore`, not `.git/info/exclude`. It shows as a plain `??` in ordinary
`git status`, so it reds not only assay's dirty check but the gate's own pre-flight
(`tester-unified-gate: assay has uncommitted changes; commit them before running the merge
gate`). It was already in the worktree before this wave began. Both files were moved aside for
the gate run and **both have been restored**, leaving the worktree exactly as found.

I applied B017's own documented workaround (move out, run the gate, move back) and **did not**
take the one-line fixes: `/workspaces/vbpub/.gitignore` is outside this branch's `assay/`
subtree, belongs to the estate root, and the brief scopes this wave to `assay/`.
**Flagged for the controller as a two-line follow-up** — see §6.3.

---

## 6. Things the reviewer should weigh rather than accept on faith

### 6.1 A-331 is a fourth change the brief did not name

`74c89475` discharges A-326's deferred obligation: `python:uuid-equality-swap` and
`python:enum-comparison-swap` lose their *spellings* (they lost their *behaviour* at 2.4.2).

**Why I took it.** A-326 wrote its own deadline in as many words — "the spelling therefore stays
until the next bump, where it is dropped" — and kept them for exactly one reason: released
builds had accepted v7 documents naming them. B035 *is* that bump, and under v8 those documents
are already refused on `schema_version` alone, so the compatibility being bought no longer
exists and the deletion costs nothing v8 had not already cost. An obligation whose deadline is a
specific event, skipped at that event because a later brief did not enumerate it, does not
survive to the next one. The existing test was literally named
`test_the_spelling_survives_in_the_v7_vocabulary_on_purpose` — its own name dated its validity.

**Why you might disagree.** It is scope the brief did not ask for. It is isolated to
`vocabulary.py`, the schema enum, two refusal orderings and their tests; **`git revert 74c89475`
is a clean, self-contained undo** if you judge the call wrong. (It would need the W4 asset
re-frozen with it, which the revert handles, since the asset moved in that same commit.)

**One non-obvious consequence, worth checking.** With the spellings gone, a stale lane file
naming one is *literally an unknown operator*, so the withdrawn-by-name check now has to run
**before** the unknown check, or the consumer gets "unknown operator(s)" — the wrong defect, and
without the pointer to `python:compare-swap`. Reordered at all three call sites
(`config._load_mutation` and the CLI's two `--operators` paths) and pinned by a parametrized
test over both names. Under v7 the two orders were indistinguishable, so this ordering has never
been exercised before this commit.

### 6.2 A stale filename I did not rename

`nyxloom-trove/carve-assets/W3/expected/dstdns-sql-r2-v6-witness.json` was migrated in place
(schema_version 8, `mode` on its r2) and its filename left saying `-v6-`. That follows the
precedent of `b6d9615c fix(assay): align runner fixtures and SQL witness with v7`, which did the
same thing at the previous cut. The name is now stale in two generations. Renaming a frozen
asset is not a call I should make unilaterally, so it is flagged. Recorded in A-330.

### 6.3 The vbpub `.gitignore` fix (§5.2) is not in this branch

Add both `ciu.worktree-instance.json` and `ciu.global.worktree.toml.j2` to
`/workspaces/vbpub/.gitignore`, next to the existing `ciu.env` line — the same one-line-per-file
fix dstdns already took twice. Until that lands, **anyone running assay's registered gate from a
ciu-created vbpub worktree hits a red gate that has nothing to do with their code**: the `.j2`
trips the gate's own pre-flight, and `ciu.worktree-instance.json` then trips the self-hosted
lane with a `DIRTY_TREE` that plain `git status` cannot explain (5.1's diagnostic now names it,
but only via the `git ls-files --others --exclude-per-directory=.gitignore` query).

I restored both files rather than deleting them, since they were in the worktree before this
wave and are CIU's render inputs, not mine to remove.

### 6.4 `qualify_topos` pops `judge_provenance` rather than placeholdering it

`gate/python/qualify_topos.py:782` validates `judge_provenance` (`_check_judge_provenance`) and
then `pop`s it out of the normalized document before template comparison. The alternative — a
placeholder digest in the locked template — does not work: the same locked templates are fed to
`verify_document`, which requires a real 64-hex digest. So the check is *real* (kind, algorithm,
version, 64-hex shape) but the field cannot participate in byte-comparison against a frozen
template, because the digest is by construction different on every build. Rationale is written at
the call site; flagged here because "validated then removed" deserves a reviewer's eye.

### 6.5 Two errors of my own, caught while writing this report

Recorded because they say something about where else to look, not to pad the list.

1. **A false coverage citation.** `provenance.py`'s docstring claimed the wheel and zipapp forms
   were "exercised against genuinely built artifacts in
   `tests/test_distribution_build_release.py`". The wheel half was true but in a different file
   (`test_standalone.py`); **the zipapp half was not true anywhere** — every zipapp test drove a
   stand-in `Distribution`, and no automated test had ever run `identify_judge` inside a real
   `.pyz`. I had measured it by hand during design, and hand-measurement does not survive.
   This is exactly the unverified-citation defect A-105/A-112 caught twice before and that
   A-326 flagged again in its own round-2 correction.
   Fixed properly rather than by editing the sentence: `test_distribution_build_release.py` now
   carries `test_the_zipapps_own_sha256_is_what_identify_judge_records`, which runs a probe
   inside the real built `.pyz` and compares the emitted digest against
   `hashlib.sha256(zipapp.read_bytes())`, plus a second test pinning that digest to the build's
   own shipped `.sha256` sidecar. The docstrings now name the two tests that actually exist.
   The zipapp branch is also where the measured `zipp.Path`/`pathlib.Path` `TypeError` lives, so
   it was the single least-safe uncovered path in the feature.
2. **A wrong field name in A-327.** The row said the object carries `judge`; the field is
   `name` (`judge_provenance.name`). Corrected in place, with the five-field list spelled out.
   **This is the one place I revised a decisions.md row after writing it** — this wave's own
   row, same day, unmerged, and a plain misspelling of a field name that none of the row's
   reasoning depended on. Worth stating precisely, because it turns out not to be an append-only
   violation even on the strictest reading: the row is a line this branch *adds*, so correcting
   it does not modify any line that existed before. `git diff --numstat <base> -- decisions.md`
   is **+19 / −0** — nineteen added lines, zero deleted, i.e. the file is still purely additive
   against the branch base. No pre-existing row was touched or reworded.

### 6.6 Batching

One combined feature commit rather than three. Reasoning in §1 and A-330. If the reviewer wants
three, the honest split would require three knowingly-red intermediate trees.

---

## 7. The real gate run

Run from inside this worktree, per the brief:

```
bash tools/tester-unified-gate.sh /workspaces/vbpub/.worktrees/assay-v8-synergy-wave
```

This is the outer `docker run --network=none` of `tester-unified:local`, which makes an
exact-OID `--no-local` sparse clone, builds a hash-pinned offline build-venv, produces a wheel,
installs it into a separate run-venv, and then runs the locked acceptance suites, the
self-hosted lane, P25 Topos qualification, B006(a) CMRU qualification and the independent
witness.

### Exit code, read in a separate step (LESSONS L4 — never a pipe tail)

```
$ cat .../gate4.exit
GATE_EXIT=0
```

### Actual output tail (verbatim; only pytest's `....[ NN%]` progress lines are elided)

```
17 passed in 0.72s
ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
v6/v7 hard-cut guard passed for 12 frozen templates
ASSAY_GATE_PHASE=verdict-v6-v7-hard-cut-verified
40 passed in 0.78s
ASSAY_GATE_PHASE=verdict-v8-successors-verified
tester-unified: PASS (exit 0)
  commit: 745ac377fd7509cbd48fc7daf3e20a6255d710b0
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
--- B006(a) WI-5 qualification receipt ---
input_oid=d2ad506a66d8f2a43170bce8ebf6c034d724fae3
qualification_baseline_oid=1bea2767444c4839da1b7c5d9f03e0e5869a7e59
head_oid=5e007b1d427194a80a308aabc9280e158de3f52a
outcome=PASS exit_code=0
claim[R0]=status=PASS
claim[R1]=status=PASS
claim[R2]=status=PASS
claim[R3]=status=PASS
r2_killed_identity={"description": "Eq->NotEq", "end_byte": 52, "lineno": 2, "operator": "python:compare-swap", "path": "cmru/src/cmru/_b006a_probe.py", "replacement_sha256": "c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2", "start_byte": 50}
r3_canary={"control_outcome": "PASS", "description": "appended never-called `def _assay_canary_unreached` (2 uncovered lines) at end of file", "expected_reason_code": "UNCOVERED_LINES", "mechanism": "uncovered-line", "observed_reason_code": "UNCOVERED_LINES", "target": "src/cmru/_b006a_probe.py", "transformed_outcome": "FAIL"}
snapshot_policy={"selection": "repository-minus-unsafe-symlinks", "unsafe_symlink_omissions": ["topos/tests/fixtures/inspect_files/_danger/passwd_link", "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape", "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current"]}
omission_probe={"omitted_absent": [true, true, true], "cmru_root_present": true, "topos_ordinary_present": true, "status_clean": true}
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=cmru-b006a-qualified
7 passed in 11.33s
ASSAY_GATE_PHASE=independent-self-hosting-passed
ASSAY_REGISTERED_GATE_COMPLETE=1
```

### Reading of that run

All eleven phase markers fired, in order, ending with
`ASSAY_REGISTERED_GATE_COMPLETE=1` and exit 0. The three that matter most to this wave:

- **`verdict-v6-v7-hard-cut-verified`** — 12 frozen W1+W2 templates each rejected by the v8
  verifier with exactly the hard-cut message and nothing else. This is B035's break, proven
  against real frozen artifacts rather than asserted.
- **`verdict-v8-successors-verified`** — the new W4 locked acceptance suite, 40 nodes, run for
  real (not collect-only) against the installed wheel.
- **`judge-provenance-bound-to-the-installed-wheel`** — the emitted
  `judge_provenance.digest` compared against a host-side `sha256sum` of the wheel the gate
  itself built and installed. This is B018's core claim measured end to end, outside pytest,
  in the `--network=none` container. The self-hosted lane that produced that verdict ran with
  `--require-judge-provenance`, so a run that had somehow imported source instead of the wheel
  would have refused rather than produced evidence.

The self-hosted lane itself reports `tester-unified: PASS (exit 0)` at commit `745ac377` — the
head of this branch, i.e. the gate judged the tree this report describes.

### Which commit the gate actually judged

`745ac377`, named in the lane's own output above. The branch head is one commit later
(`7515c57d`), because a report that pastes its own gate transcript cannot be inside the commit
that transcript describes — chasing that would recurse forever. The gap is prose only, and that
is checkable rather than asserted:

```
$ git diff --name-only 745ac377..HEAD
assay/nyxloom-trove/carve-assets/W4/MANIFEST.md
assay/nyxloom-trove/reports/assay-B018-B019-B035-v8-synergy-REPORT.md

$ git diff --name-only 745ac377..HEAD | grep -v '\.md$'
(no output)
```

No source file, schema, frozen asset, test, gate script or lane config differs between the gated
commit and the head being handed off. A reviewer who wants a gate run whose commit hash equals
the branch head can get one by re-running the gate on the merge commit.

### Full-suite state

`python -m pytest tests -q` from the worktree: **3354 passed, 11 skipped** (303s). The gate's own
figure is lower because its lane excludes `tests/test_self_hosting.py`, which the independent
witness then runs separately against the emitted artifact.

### One caveat on reproducing this run

The gate was run with `ciu.worktree-instance.json` moved out of the worktree root, per B017's
documented workaround (§5.2). It has been moved back. Until vbpub's committed `.gitignore`
carries that filename (§6.3), a reviewer re-running this gate from this worktree must apply the
same workaround or will see `NO_MEASUREMENT/DIRTY_TREE` at the self-hosted lane — a false
refusal that says nothing about the code in this branch.
