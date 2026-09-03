# ciu8-P001 — Carve review, round 1

**Reviewer:** fresh adversarial carve review (never a fork), per the `carve`
skill's mandatory pre-dispatch gate.
**Target:** `ciu8/nyxloom-trove/handoffs/ciu8-P001-config-schema-v8-and-conformance.md`
**Commits reviewed:** `67f5e734` (carve content + trove bootstrap), `cc8120f8`
(the `input_revision` freeze) — both verified real and reachable:
`git merge-base --is-ancestor 67f5e7344c54df38d7b5e271e4e08b2f5f68c5fd HEAD` and
same for `cc8120f82203b7ba192e5b104520227b370b405a` both report "is ancestor".
The previously self-reported hashes `c6e77ad3`/`c7abad7a` are confirmed
**pre-rebase orphans**: same author/date/message as `67f5e734`/`cc8120f8`,
identical tree content for the carve file, but `git merge-base --is-ancestor
c6e77ad342b7f66496d88de2db0e1e33c2f25729 HEAD` reports "NOT ancestor" — they
are dangling loose objects, not part of `main`'s history. See Finding 1: this
distinction is not just a self-report artifact, it is a **live bug in the
frozen frontmatter**.

## Verdict: **REJECT**

Three independent, concretely-verified defects each individually block a safe
dispatch (an invalid routing key, a broken freeze, and a self-contradicting
worked example in the package's hardest section). All three are small,
mechanical, precisely-located fixes — this is not a redesign — but each must
be repaired, the frontmatter/body agreement re-checked, `nyxloom lint`
re-run, and `input_revision` re-frozen before a fresh implementer is
dispatched, per the carve skill's REJECT path.

---

## Findings, ranked by severity

### F1 (BLOCKING) — `tier: luna-high` is not a live tier; it does not exist in `routes.host.toml`

Frontmatter line 7: `tier: luna-high`.

`luna-high` was retired by the B16 tier-taxonomy migration, which **landed
before this carve was authored**:

```
$ git merge-base --is-ancestor 882f2cac 67f5e7344c54df38d7b5e271e4e08b2f5f68c5fd
YES - migration landed BEFORE the carve
```

`nyxloom/routes.host.toml` (live, checked-in) today has exactly these
`[tiers.*]` tables: `implement-1`, `implement-2`, `implement-1-free`,
`review-3`. `luna-high` appears only in historical comments:

```
78:[tiers.implement-2]      # was sonnet5-high + luna-high + flash-max (band 2: medium)
```

`nyxloom/docs/routing-model-redesign.md` (committed, HEAD) confirms explicitly:
> **Status: BUILT (B16, 2026-07-23).** `routes.host.toml`'s `[tiers.*]` keys
> are verb-band names...
> | `luna-high` | `implement-2` | (folded in; `claude-sonnet5-high` stayed primary) |

`AUTHORING.md` (Level 2, frontmatter contract) is unambiguous: `tier: <a live
key from routes.toml>` and the 2a-2e ladder section: *"frontmatter `tier`...
must always be a literal key that exists in the CURRENT live `routes.toml`...
Do not put a nonexistent tier in frontmatter."*

The carve's **own body text already reasons this correctly** (lines 88-90):
> `tier: implement-2` for the whole package (AUTHORING.md: only implement-1/
> implement-2 are live routes today).

So the carver correctly determined `implement-2` and even cited the exact
AUTHORING.md rule for it — then left the frontmatter's actual `tier:` field
set to the stale `luna-high`. This is a direct frontmatter/body
contradiction, and it also correctly maps: AUTHORING's 2a-2e ladder says
contract class `2d` → tier `implement-2`, which is what this package claims
for both Part A and Part B (line 82-90) — so `implement-2` is not just "the
only live option," it is the class-correct one too.

`nyxloom lint` does **not** catch this (see F-lint below) — there is no
tier-liveness check in lint, so this would only surface at actual dispatch/
routing time, most likely as a silent misroute or a hard failure resolving
the route.

**Ruling on flagged decision (2):** `luna-high` is **not** the right live
mapping — it is stale, pre-B16. Must change to `tier: implement-2` before
dispatch.

**Fix:** frontmatter line 7 → `tier: implement-2`.

---

### F2 (BLOCKING) — the two-step `input_revision` freeze is broken: it pins an unreachable, GC-vulnerable orphan commit, not the carve's real first commit

Read both commits' diffs directly, not the self-report:

```
$ git show cc8120f8 -- ciu8/nyxloom-trove/handoffs/ciu8-P001-config-schema-v8-and-conformance.md
-input_revision: "b19880bc3b66b6cef22a58f69ddef67ff8a06b1e"
+input_revision: "c6e77ad342b7f66496d88de2db0e1e33c2f25729"
```

`cc8120f8`'s own commit message claims: *"input_revision now pins c6e77ad3,
this handoff's own prior commit"*. That is false in the current history:
`c6e77ad3` (full: `c6e77ad342b7f66496d88de2db0e1e33c2f25729`) is **not** an
ancestor of `HEAD`:

```
$ git merge-base --is-ancestor c6e77ad342b7f66496d88de2db0e1e33c2f25729 HEAD
NOT ancestor of HEAD
$ git merge-base --is-ancestor 67f5e7344c54df38d7b5e271e4e08b2f5f68c5fd HEAD
IS ancestor
```

`c6e77ad3` and `67f5e734` share byte-identical tree content for the carve
file (`diff` of both `git show <hash>:<path>` outputs is empty) and even
share the same parent-of-parent, but they are **different commit objects**
with different parents (`b7b5f38d` vs `4e5efa27`, both themselves ancestors
of `HEAD` — i.e. `c6e77ad3`/`c7abad7a` are a genuine sibling pair produced by
some rebase/reconciliation event, now dangling loose objects that happen to
still be present in the object store but are not reachable from any ref).

This is not cosmetic. The entire purpose of the two-step freeze (per the
`carve` skill: *"so a later re-carve/repair round can diff against exactly
what was reviewed"*) depends on `input_revision` naming a commit reachable
from history. Today it does not — the pinned hash is one `git gc` away from
disappearing, and even before that, any tooling that walks `git log
<input_revision>..HEAD` gets a merge-base one commit further back than
intended (at `b7b5f38d`, i.e. as if the entire carve+freeze pair were itself
"new" relative to input_revision), not the empty/near-empty diff a correct
freeze would produce.

**Fix:** re-freeze `input_revision` to `67f5e7344c54df38d7b5e271e4e08b2f5f68c5fd`
(the real, currently-reachable first commit of this carve) in a fresh commit.

---

### F3 (must-fix alongside F2) — body text cites a THIRD, different, stale `input_revision` value, disagreeing with both frontmatter states

`schema_tables.py`'s S3.8.6 section (body, line ~823-824):

> Expected result AS OF THIS CARVE (input_revision `b19880bc`) — assert your
> extractor produces exactly this before wiring it to anything else...

`b19880bc` (full: `b19880bc3b66b6cef22a58f69ddef67ff8a06b1e`) is a real,
reachable commit — it is the pre-carve base (`main`'s tip immediately before
the carve was authored: *"docs(ciu,cmru): host enrollment rev 2..."*, by
Claude Fable 5.1) — and it was the frontmatter's own `input_revision`
placeholder **before** the two-step freeze ran (visible as the `-` side of
`cc8120f8`'s diff, F2 above). It agrees with neither the current frontmatter
(`c6e77ad3`, itself wrong per F2) nor the correct value (`67f5e734`).

This is the frontmatter/body-agreement discipline the carve skill names
explicitly (D-129): the two halves must diff clean against each other, and
here there are three different hash citations across two commits and the
body prose, only one of which (`67f5e734`) is actually correct.

Content-wise the S13.1/S3.4.7/S6.10 sections of `SPEC-V8.md` have not moved
between `b19880bc` and `HEAD` (`git diff b19880bc..HEAD -- ciu/docs/
SPEC-V8.md` is empty), so this specific mismatch does not itself invalidate
the extractors' *expected values* — but it is still a real authoring defect,
and a template for future drift once SPEC-V8.md moves and a repair round
tries to figure out what "as of this carve" actually pinned.

**Fix:** once `input_revision` is correctly frozen to `67f5e734`, update the
body citation at line ~824 to the same value (or better: stop hardcoding the
hash in prose — say "as of this carve's frozen `input_revision` in the
frontmatter" so the two can never drift again).

---

### F4 (BLOCKING) — the S3.8.6 harness's own worked S13.1 extractor does not reproduce its own stated expected result when implemented exactly as specified

This is the carve's own billed "hardest, most novel part" (B5), and the task
explicitly called for rigor here. I ran the **literal algorithm the carve
prescribes** (B5, rule 3) against the current, unmodified `SPEC-V8.md`:

> Extractor: locate `"### S13.1 Vocabulary"` up to `"### S13.2"`, within the
> sentence starting `"The **resource key set** \`RK\` = "` apply
> `` r"`([a-z][a-z0-9_]*)`" `` restricted to that sentence (**stop at its
> terminating period**).

The real text (`ciu/docs/SPEC-V8.md` §S13.1) is:

```
The **resource key set** `RK` = `memory_max`, `memory_swap_max`,
`memory_high`, `memory_low`, `memory_min`, `cpu_weight` (1..10000), `cpu_max`
(`"<quota> <period>"` or `"max"`), `io_weight` (1..10000), `pids_max`. Sizes
per S1.4.
```

`cpu_weight` is immediately followed by `(1..10000)` — a **two-dot range**,
which contains a literal `.` well before the sentence's real terminating
period. A literal reading of "stop at its terminating period" (`str.index(".")`
or equivalent) finds that `.` inside `1..10000` and truncates the slice
there, **before** `cpu_max`, `io_weight`, and `pids_max` are ever seen by the
regex. I reproduced this directly:

```python
i = slice13.index("The **resource key set** `RK` = ")
j = slice13.index(".", i)          # <- lands inside "1..10000", not the real end
sentence = slice13[i:j]
extracted = frozenset(re.findall(r"`([a-z][a-z0-9_]*)`", sentence))
# extracted == {'cpu_weight', 'memory_high', 'memory_low', 'memory_max',
#               'memory_min', 'memory_swap_max'}                    # 6 items
```

The carve's own stated "Expected result" (line 850-852) is:

```
frozenset({"memory_max", "memory_swap_max", "memory_high", "memory_low",
"memory_min", "cpu_weight", "cpu_max", "io_weight", "pids_max"})   # 9 items
```

These do not match. (For contrast, I ran the same exercise for the S3.4.7 and
S6.10 extractors given in the same section — both **do** reproduce their
stated expected frozensets exactly against the current document text; only
S13.1 is broken.)

This matters more than an isolated typo because it is a genuine
specification contradiction, not an omission with a clean escape hatch. The
packet's own `escalate_if` list has an entry for "the current SPEC-V8.md text
... no longer matches the exact expected frozensets... (the document moved
since input_revision was frozen)" — but the document has **not** moved
(confirmed above); the algorithm itself is wrong against an unchanged
document. None of the five `escalate_if` triggers cover "the prescribed
extraction algorithm does not reproduce the prescribed expected value against
an unmoved document." A literal, disciplined implementer following BLOCKED
protocol strictly has no clean mechanical trigger to invoke here; a less
disciplined one is invited to "just fix the regex boundary," which is exactly
the kind of silent implementer-invented deviation AUTHORING's escalation
doctrine (mechanical, not introspective) exists to prevent, in the one
section of this package billed as requiring zero design judgment.

**Fix:** replace "stop at its terminating period" with an unambiguous
boundary rule that survives the `1..10000` range syntax — e.g. bound the
slice at the literal substring `"Sizes per S1.4"` instead of the first `.`,
or split on `". "` / `".\n"` (period followed by whitespace) rather than a
bare `.`. Re-verify the corrected algorithm reproduces the stated 9-item
frozenset before re-freezing (I confirmed above that stopping the slice at
`"Sizes per S1.4"` instead of the first literal `.` does reproduce the full
9-item set, if that concrete fix is wanted).

---

### F5 (must-fix, mechanical) — Part A11's proposed minimal `.gitignore` silently git-ignores the very file Part A10 vendors

A10 (line 388-402) directs vendoring `assay-4.1.0.pyz` into
`ciu8/tools/assay/`. A11's exact proposed `.gitignore` content (lines
408-420) does not include an un-ignore rule for it. Verified empirically:

```
$ git check-ignore -v --no-index -- ciu8/tools/assay/assay-4.1.0.pyz
.gitignore:3:*.py[codz]	ciu8/tools/assay/assay-4.1.0.pyz
```

The repo-root `.gitignore`'s general Python-bytecode pattern `*.py[codz]`
(matching `.pyc/.pyo/.pyd/.pyz`) catches `assay-4.1.0.pyz` by coincidence of
extension. Both existing precedents in this estate carry an explicit
un-ignore for exactly this reason: root `.gitignore` line 8
(`!cmru/tools/assay/*.pyz`) and `ciu/.gitignore` line 69
(`!tools/assay/*.pyz`, confirmed present). A11's list for `ciu8` has no
equivalent line, so following A10 + A11 verbatim leaves the vendored judge
untracked/ignored — `git add` would warn/refuse without `-f`, and even a
forced add leaves a landmine (the file reads as "ignored" to every future
`git status`/`git add -A`, and a `git clean` could remove it).

**Fix:** add `!tools/assay/*.pyz` (or the narrower `!tools/assay/assay-4.1.0.pyz`)
to A11's `.gitignore` content block.

---

## `nyxloom lint` — re-run, full output, both warnings verified

```
$ python3 nyxloom/exec-nyxloom.py lint ciu8/nyxloom-trove/handoffs/ciu8-P001-config-schema-v8-and-conformance.md
ciu8/nyxloom-trove/handoffs/ciu8-P001-config-schema-v8-and-conformance.md:- L10 warning handoff size 14618 tokens
ciu8/nyxloom-trove/handoffs/ciu8-P001-config-schema-v8-and-conformance.md:- L13 warning oracle 'O6' references path 'workspaces/vbpub/ciu/docs/SPEC-V8.md' not covered by scope.touch
```

Zero errors, exactly the two warnings the carve agent claimed. Both verified
as legitimate:
- **L10** (14618 tokens): below the 18000-token error floor, above the 10000
  warn floor. Given the package bootstraps an entire subproject skeleton
  (11 mechanical files) *and* carries a from-scratch declarative schema
  format plus a from-scratch conformance-generation design, this is a
  defensible size for a Checkpoint-A package — see the sizing note below,
  though.
- **L13** (O6 reads `ciu/docs/SPEC-V8.md`, outside `scope.touch`): this is a
  read-only cross-repo citation, required by the S3.8.6 harness's own design
  (comparing spec text to code) and explicitly named read-only in the
  `Out of scope / forbid` section. Accepted false positive, matches the
  carve's own framing.

**Gap worth noting (non-blocking):** `nyxloom lint` has no tier-liveness
check against `routes.host.toml`, so F1 (the invalid `luna-high` tier) is
invisible to lint and would only surface at dispatch time. Not something to
fix in this carve, but worth a backlog note against nyxloom itself if not
already filed.

---

## `escalate_if` / E-008 checkpoint clause

Present and correctly named (frontmatter, last `escalate_if` entry, line 77):
both checkpoint artifacts are named explicitly —
`nyxloom-trove/reports/ciu8-P001-BRIEF.md` and
`nyxloom-trove/reports/ciu8-P001-COMPACT.md` — and both are listed in
`scope.touch` (lines 39-40). Arm/cut/repeat/stop thresholds match the
estate-standard E-008 rule (~120k tokens or ~60 calls; cut at green
gate/commit/LOG-REPORT/edit-cluster boundary; repeat every 40-55 calls; stop
under ~40 remaining). No defect here.

## BLOCKED protocol

Present with the literal `BLOCKED:` marker in two places: the dedicated
`## BLOCKED rule` section (lines 104-112) and the closing paragraph (line
972: `**BLOCKED:** emit for any escalate_if trigger...`). Both correctly
forbid silent workarounds and forbidden-path touches. No defect.

## Frontmatter/body agreement — general sweep

Beyond F1/F2/F3 above (the tier and input_revision contradictions), the rest
of frontmatter and body agree:
- `scope.touch` covers every file named by a contract item (Part A1-A12,
  Part B1-B7) and every file an oracle needs, with the single accepted
  exception of O6's read-only `ciu/docs/SPEC-V8.md` citation (L13, above).
  No oracle is unsatisfiable within `scope.touch` — the classic carve-killer
  does not recur here.
- `gates: [tester-unified]` matches every oracle's `gate: tester-unified` and
  matches the already-committed `nyxloom-trove/nyxloom.toml`'s
  `[gates.tester-unified]` declaration (confirmed by reading that file
  directly): `argv = ["bash", "-c", "cd {worktree}/ciu8 && exec ./run-gate.py
  --worktree {worktree} ciu8"]`, `asserts = ["tests-pass",
  "changed-line-coverage", "assay-verdict"]` — this is exactly what O1
  claims. Good internal consistency between the pre-committed trove
  scaffold and this carve's own contract.
- Out-of-scope/forbid section correctly forbids editing `ciu/`, `nyxloom/`,
  `run-gate-project/`, `cmru/` (read-only vendoring only), and the
  already-committed trove files, and correctly routes any need to touch them
  to BLOCKED rather than silent creep.

## Gate argv — verified real and correctly ordered

`./run-gate.py --worktree <path> ciu8` (O1, Environment setup) was checked
against the actual tool:

```
$ python3 run-gate-project/run-gate.py --help
usage: run-gate.py <lane> [--worktree PATH] [--allow-dirty] [--base REF] [--fresh]
```

Live-tested that flag/positional order does not matter to argparse (`--worktree
PATH ciu8` and `ciu8 --worktree PATH` both parse identically, reaching the
same "unknown lane" error against a config that doesn't declare it) — the
carve's stated invocation order is syntactically valid. `run-gate.toml`
central (`/workspaces/vbpub/run-gate.toml`) does declare
`[environments.tester-unified]` with `image = "tester-unified:local"`,
matching `escalate_if` bullet 4's check and context-read item 7. Ordering is
sound: Part A's A5/A6 create `run-gate.py` (symlink) and `run-gate.toml`
*before* any gate invocation is attempted, and the already-committed
`nyxloom.toml`'s gate argv comment explicitly acknowledges this bootstrap
dependency ("ciu8-P001 bootstraps the files this argv needs").

## Measure-never-assume spot checks on Part A's "mirror `ciu/`" claims — all verified accurate

| claim | checked against | result |
|---|---|---|
| `pyproject.toml` build-system pins (`setuptools==82.0.1`, `wheel==0.47.0`, `setuptools_scm==10.0.5`) | `ciu/pyproject.toml` line 4 | exact match |
| `config_model.py` line numbers (`RESERVED_GLOBAL_TABLES` ~169, `validate_user_tables` ~818, `validate_service_registry` ~934, `validate_stack_shape`/`validate_stack_provisioning` ~1110-1360) | `grep -n '^RESERVED_GLOBAL_TABLES\|^def validate_'` | 169, 818, 934, 1110, 1226 — all exact/within range |
| `run-gate.py` symlink target, same relative depth for `ciu8/` | `readlink ciu/run-gate.py` → `../run-gate-project/run-gate.py`, resolves to `/workspaces/vbpub/run-gate-project/run-gate.py` | confirmed; `ciu8/` sits at the same depth as `ciu/`, so an identical relative symlink resolves correctly |
| assay pin `4.1.0` vendored sha256 `a1a5b09c...931` | `cat cmru/tools/assay/assay-4.1.0.pyz.sha256` | exact match; file exists at that path |
| "ciu's own pin is stale at 3.2.0" | `ciu/run-gate.toml` line 19, `ciu/tools/assay/assay-3.2.0.pyz*` | confirmed: `version = "3.2.0"` |
| `cmru.toml` v7 `prefix = "ciu-v"`, `scm_dist = "ciu"` (to be adapted to `ciu8-v`/`ciu8`) | `ciu/cmru.toml` | exact match |
| central `run-gate.toml` has `[environments.tester-unified]` | `/workspaces/vbpub/run-gate.toml` line 11-12 | confirmed, `image = "tester-unified:local"` |

This is a genuinely well-verified carve in its mechanical Part A claims — the
defects found (F1-F5) are concentrated in the parts that needed synthesis
(tier assignment, the freeze mechanics, the S13.1 extractor's boundary logic,
one `.gitignore` completeness gap) rather than sloppy transcription.

## Rulings on the five flagged decisions

1. **One combined handoff (bootstrap + schema-engine) vs. two sequential.**
   ACCEPT as carved. Part B's oracles (O1 in particular) cannot produce a
   green gate without Part A's bootstrap existing first, and Part A alone
   produces nothing independently testable/oracled — splitting would just
   move the same total work across two packages with an artificial
   dependency edge, not reduce risk. The size (14618/18000 tokens) is close
   enough to the error floor to watch, but not itself a reason to split
   given the ordering coupling. If a *repair* round (after F1-F5) pushes size
   materially higher, revisit.
2. **`tier: luna-high` vs. `implement-1`/`implement-2`.** **Must change** —
   see F1. `luna-high` is stale/retired (pre-B16), not a live route. The
   carve's own body reasoning (`implement-2`) is correct and should simply be
   copied into frontmatter.
3. **Assay judge pin `4.1.0` vs. ciu7's `3.2.0`.** ACCEPT as carved, verified
   accurate: ciu7's own pin is confirmed `3.2.0` (stale); `4.1.0` is
   confirmed present in `cmru/tools/assay/` with a matching sha256, and is
   the freshest verified zipapp in the estate as claimed. No change needed.
4. **S3.8.6 harness scoped to 3 live extractors + 6 stubs.** The *scoping*
   (which of the 9 rule ids named by SPEC-V8.md's own S3.8.6 sentence are
   live vs. stub, and which proposal item owns each stub) is faithful to the
   source and correctly enumerated — ACCEPT that split. But one of the three
   "live" extractors (S13.1) is broken as specified — see F4, which **must**
   be fixed before dispatch. S3.4.7 and S6.10 verified correct as specified.
5. **`nyxloom-trove/backlog.md` instead of porting `KNOWN_ISSUES_TODO_BACKLOG.md`'s shape.**
   ACCEPT as carved (and out of this carve's own contract regardless — it was
   decided in the already-committed trove bootstrap, not by this handoff).
   `ciu8` is a brand-new, nyxloom-native subproject; `assay` (also
   nyxloom-native) already uses its own trove-shaped backlog rather than
   porting `ciu`'s legacy `KNOWN_ISSUES_TODO_BACKLOG.md`/`CIU-NN` convention,
   which is a v7-era, pre-nyxloom artifact. No change needed.

## Forward-dependency risk (V8-27/V8-2/V8-13/V8-14 importing `ciu8.schema_spec.{TableSpec,KeySpec}` / `ciu8.schema_tables.ALL_TABLES`)

Concretely committed, not vague. B1 fixes the module (`schema_spec.py`),
class names (`TableSpec`, `KeySpec`), and field shapes with a full dataclass
definition; the `Degrees of freedom` section explicitly locks module names
(`schema_spec.py`, `schema_gen.py`, `schema_tables.py`, `cli.py` — "you may
NOT rename or merge them") and states "Nothing about serialized shapes
(KeySpec/TableSpec field names... the ALL_TABLES list-not-tuple decision) is
a degree of freedom." O7 is a real, mechanical proof of the extension
contract (an external script appends a `TableSpec` without touching
`schema_gen.py`). A later carve can safely build V8-27/V8-2/V8-13/V8-14
against this promise once F1-F5 are fixed. No defect here.

## Size/scope

Implementable as one package by one fresh implementer within the checkpoint
clause's stated budget — the size warning (F-lint, L10) is real but under the
error floor, and the coupling argument in decision-1's ruling holds. Not a
split recommendation at this time.

---

## What must change before a fresh implementer can be dispatched

1. Frontmatter `tier: luna-high` → `tier: implement-2` (F1).
2. Re-freeze `input_revision` to `67f5e7344c54df38d7b5e271e4e08b2f5f68c5fd`
   in a fresh commit (F2), and correct the body's stale `b19880bc` citation
   at line ~824 to match (F3) — ideally by removing the hardcoded hash from
   prose entirely and pointing at the frontmatter field instead, so this
   class of drift can't recur.
3. Fix the S3.8.6 S13.1 extractor's sentence-boundary rule so it reproduces
   the packet's own stated 9-item expected frozenset against the current,
   unmoved `SPEC-V8.md` text (F4) — verify by literally running the corrected
   algorithm against the live document text before re-freezing, the same way
   this review did.
4. Add the missing `!tools/assay/*.pyz` (or equivalent) line to A11's
   `.gitignore` content (F5).
5. Re-run `nyxloom lint` (expect the same 2 accepted warnings, no new ones),
   re-freeze `input_revision` (step 2 folds into this), and bring this same
   reviewer back for fix-verification before dispatch, per the carve skill's
   REJECT path.

None of the above requires new design judgment — all five are precisely
located, mechanical corrections to an otherwise carefully verified package
(Part A's mirroring claims, the gate argv, the S3.4.7/S6.10 extractors, the
forward-dependency contract, and the BLOCKED/checkpoint machinery all held up
under adversarial re-derivation).
