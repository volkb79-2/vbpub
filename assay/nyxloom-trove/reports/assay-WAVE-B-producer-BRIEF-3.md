# Wave B — continuation brief 3

Written at the third checkpoint of the assay Wave B ("producer wave", target
release **4.0.0**, verdict schema **v8 → v9**). Cut on a clean boundary: two
commits landed, **full `pytest tests/` green at the tip (3668 passed, 13
skipped, 0 failed, 328.17s)**, nothing in flight.

**Read BRIEF-1 and BRIEF-2 first.** This brief re-copies neither seam map. It
records only what moved this session, what is now DONE that those briefs list
as remaining, and the deltas a successor would otherwise re-derive.

## Topic index

1. Where I am
2. Committed vs. in-flight
3. Seam-map DELTAS only (what this session moved, plus four seams no brief had)
4. Design calls made this session (in `decisions.md` — do not re-argue)
5. The remaining work, with the trap per item
6. Decision asks
7. Environment facts
8. Housekeeping

---

## 1. Where I am

**Scope item (A) — the schema cut — is COMPLETE, including W5.** That is the
whole of what brief 2 §5(A) asked for, plus the W5 freeze and gate wiring the
dispatch bundled into it.

`judgment.r1.coverage_producer` was B045's last open piece; it landed with the
cut, so **B045 is now fully COMPLETE**.

**B046, B043 and B041(b) have their SCHEMA and MODEL in place and no BEHAVIOUR
yet.** Every field those three items need is now declared, validated, wire-
serialised, registered in `verify.py`, frozen in W5 and pinned by 44 acceptance
nodes. What does not exist is the code that POPULATES them: no mutation parser,
no ingested R2 path, no `cwd` loader key or execution wiring, no `link_paths`
materialisation or teardown canary.

**The successor's job is behaviour, not schema.** Nothing remaining should
touch `src/assay/schemas/verdict.schema.json` — and if something must, the W5
byte-identity guard fails loudly, which is the intended alarm, not a nuisance
to work around.

## 2. Committed vs. in-flight

| # | hash | subject | state |
|---|---|---|---|
| 1 | `384f3c0f` | `test(assay): a REAL StrykerJS mutation-testing-report-schema artifact (B046 evidence)` | landed |
| 2 | `fac1b73b` | `feat(assay): B045 (1/2) -- the coverage PRODUCER as a declared, per-format, closed fact` | landed |
| 3 | `b85d3a6e` | `docs(assay): Wave B checkpoint 1` | landed |
| 4 | `cc4e955f` | `feat(assay): B045 (2/2 non-schema) -- real branch arcs and the type-only lexer` | landed |
| 5 | `b1a2f0e9` | `docs(assay): Wave B checkpoint 2` | landed |
| 6 | `af14021f` | **`feat(assay)!: verdict schema v8 -> v9 -- the producer cut`** | landed |
| 7 | `1577fa45` | `test(assay): W5 -- the v9 frozen drift-guard generation, and the gate wiring that demotes W4` | landed |
| 8 | *(this brief + LOG hash fix)* | `docs(assay): Wave B checkpoint 3` | landing now |

Branch `feature/assay-wave-b-producer`, worktree
`/workspaces/vbpub/.worktrees/assay-wave-b-producer`, from `main` at
`a78a0046`. **In flight: nothing.**

**The wave's one and only `feat(assay)!:` commit now EXISTS — `af14021f`.** Do
not add another `!` to anything. cmru takes a `!` anywhere in the release range
literally, and one is exactly what 4.0.0 needs.

**Test state: full `pytest tests/` is GREEN at `1577fa45`** — 3668 passed / 13
skipped / 0 failed, 328.17s. Identical node count to the pre-cut baseline at
`cc4e955f`, which is the right shape for a migration commit. The registered
gate has NOT been run (correct, A-335: it runs after the wave's LAST commit,
and three scope items remain).

## 3. Seam-map deltas

### 3a. What commits 6 and 7 MOVED

- **`verdict.schema.json` grew by ~200 lines and every line number in brief 1
  §3's schema map is now wrong.** Re-derive with `grep -n '"<defname>": {'`.
  Two NEW `$defs` exist: `source_position` and `mutation_producer_tool`, both
  placed immediately before `mutation_operator`.
- **`judgment_r2.required` is now `["kill_attribution", "mode", "producer"]`.**
  `jobs`/`max_mutants`/`operators` moved OUT of it into a fifth `allOf` block
  (the producer fork). That `allOf` now has **5** members, not 4.
- **`mutation_operator.oneOf` has FOUR branches**, the fourth being a
  `pattern`, not an `enum`. Anything iterating those branches and reading
  `branch["enum"]` will KeyError —
  `tests/test_verdict_schema_is_packaged.py` was taught to partition them and
  is the model for any new consumer.
- **`verdict.py` grew ~350 lines.** New public names, all exported:
  `MUTATION_PRODUCERS`, `SourcePosition`, `MutationProducerTool`. `JudgmentR2`
  gained `_check_producer_fork` / `_check_native_policy` /
  `_check_ingested_record`, and its v8 validation body now lives inside
  `_check_native_policy` — reached ONLY under `producer = "native"`.
- **`JudgmentR2.jobs`, `.max_mutants` and `.operators` are now `| None` with
  defaults.** Any code dereferencing them unconditionally is a latent
  `TypeError` on an ingested document. The four in-repo consumers were found
  and fixed (below); a fifth added later will not be.
- **`verify.py`** gained `_reconstruct_producer_tool` and
  `_reconstruct_source_positions`; `_reconstruct_judgment_r2` now reads its
  policy fields with `.get`.

### 3b. Four seams I derived this session that no brief had

1. **`W3/expected/dstdns-sql-r2-v6-witness.json` is NOT a frozen generation,
   despite its `v6` filename.** It is a LIVE witness that
   `gate/python/qualify_dstdns_sql.py` regenerates and compares end-to-end, so
   it tracks the CURRENT schema and must migrate with every cut. Both briefs'
   maps imply everything under `carve-assets/*/expected/` is frozen history;
   this one file is the exception, and `test_capture_witness_end_to_end_
   matches_the_frozen_witness` is what catches you. Now stated in
   `W5/MANIFEST.md`.
2. **`gate/python/qualify_topos.py` carries TWO hardcoded
   `schema_version != 8` guards** (`normalize_artifact`, and the locked-template
   check ~55 lines below) plus its own `_EXPECTED_ROOT`. Only the FIRST is
   reachable from `pytest tests/`, via `test_python_qualification.py`'s direct
   `normalize_artifact` calls; the second is reachable only from the real gate.
   Neither is in any brief's seam map. Both are now v9.
3. **`_reject_unknown_keys(raw, obj.to_dict(), ...)` compares against the
   RECONSTRUCTED OBJECT's `to_dict()` output, not against a static field
   list.** This is the mechanism behind the 2.4.0 lesson and it has a
   consequence worth knowing: a field is registered iff the reconstruct
   function READS it AND `to_dict()` EMITS it. A conditionally-emitted field
   (like everything in the producer fork) is therefore automatically
   registered on the branch that emits it and automatically refused on the
   branch that does not — which is why the fork needed no extra verify.py
   work beyond reading the keys.
4. **`test_distribution_build_release.py`'s zipapp tests build from git HEAD,
   not from the working tree.** During a schema cut they fail on uncommitted
   work and clear on commit. This is a real property, not a defect — do not
   chase it. (`version='3.2.1.dev9+gb1a2f0e9'` in the fixture repr is the
   tell: it names the last commit.)

### 3c. Every consumer of `JudgmentR2`'s now-optional policy fields, swept

Recorded because the successor's B046 work will add the first one that is
actually reached with `None`:

| site | what it does now |
|---|---|
| `verdict.py` `_check_judgment_matches_claims` | the payload-vs-`operators` check is guarded by `if judgment_r2.operators is not None` |
| `verdict.py` `_check_operator_language_agrees` | forks on `producer`; the ingested branch is `_check_ingested_operators_only` |
| `verdict.py` `_check_mutation_cardinality` | returns early when `max_mutants is None` |
| `verdict.py` `MutantOutcome.__post_init__` | consults `is_ingested_operator` BEFORE the closed catalogue |
| `verify.py` 505-525, 620-640, 995-1020 | already `isinstance`-guarded; they SKIP rather than crash on an ingested document — **which means the raw layer currently checks NOTHING about an ingested payload. That gap is B046's to close (see §5).** |

## 4. Design calls made this session — recorded, do not re-argue

**`decisions.md` now runs to A-366; next free row is A-367.**

- **A-359** one bump for four items, cut BEFORE the features that fill it.
- **A-360** `judgment.r2.producer` is required and forks the object, BOTH
  directions. This is the controller-endorsed option (i) from brief 2 §6,
  implemented — plus two things the endorsement did not specify and a reviewer
  should check: the native→ingested forbidding (my own mirror of the rule) and
  `equivalence_artifact` joining the forbidden set on the wire.
- **A-361** `producer_tool` is its own object, not a `helpers[]` entry;
  `report_schema_version` stays a string.
- **A-362** the open `stryker:` branch shares ONE source string with
  `INGESTED_OPERATOR_RE`, single-alternative group and all.
- **A-363** `cwd_declared` is not lane-resolved.
- **A-364** `coverage_producer` is a bare string in the schema; the per-format
  closure lives in the loader.
- **A-365** `source_position` objects, and required-and-possibly-empty.
- **A-366** `link_paths` is independent of `selection`.

**A-354 remains the call most open to reviewer challenge** (whether
`go-cover`'s `go-test`/`covdata` producers should have shipped now). Unchanged
from briefs 1 and 2.

## 5. Remaining work, with the trap per item

Brief 2 §5's ordering stands for what is left. (A) is done; **(B), (C), (D),
(E) remain, and their traps are unchanged except where noted.**

**(B) B046 — the ingested R2 path.** `mutation_parsers/mutation_report_json.py`
+ registry + loader keys + scope intersection + bucket map + the runner's
ingested branch.
  *Traps, updated for what now exists:*
  - The `stryker:` prefix collision is **PARTLY closed already**:
    `verdict.MutantOutcome` and `verify._check_resolved_language_owns_every_
    operator`'s model-side twin are done. **`verify.py`'s own raw
    `_check_resolved_language_owns_every_operator` (~line 620) is NOT** — it
    still compares `operator_language(...) != language` over the payload and
    will refuse every ingested mutant. `config`'s loader path is also
    untouched. Grep `operator_language` again; two callers remain.
  - **The raw verifier currently checks nothing about an ingested payload**
    (§3c). B046's acceptance box asks for `verify.py` re-derivation of `pct`
    and the buckets FROM the payload; that is a NEW checker, and it is the
    only thing standing between an ingested document and a layer that merely
    shrugs at it. Do not let the `isinstance` guards' silence read as coverage.
  - `producer_tool` FROM THE REPORT, never `helpers[]` (A-230a/A-361).
  - `javascript` registers `{"R1","R2"}` through the ingested path only;
    `generate_mutation_sites` stays `UNSUPPORTED`; `cli.py`'s 340-374 docstring
    amended.
  - An absent `projectRoot` is refused — assay's own added requirement (the
    upstream schema makes it optional), needs its own A-row.
  - The model already forbids `operators`/`jobs`/`max_mutants`/
    `equivalence_artifact` on an ingested lane at the VERDICT layer; B046 must
    also refuse them at LOAD, in `config.py`, with its own message. Two layers,
    two messages — the verdict layer's refusal is not a loader diagnostic.

**(C) B043 — `cwd` / `cwd_declared`.** The wire half is done; the LOADER and
the four execution sites are not.
  *Trap:* unchanged — `runner.py` 1754 and 3364, `mutation.py:1598`,
  `canary.py:214` must agree, and nothing else re-roots (A-271). Note commit 6
  moved `runner.py` line numbers by roughly +12 around the `JudgmentR1`
  construction; the four cwd sites themselves were not touched.

**(D) B041(b) — `link_paths`.** The wire half is done; rules 1-6 are not.
  *Trap:* unchanged and still the review's #1 flagged risk. **Plant a REAL
  symlink to a target outside the snapshot, run teardown, assert the TARGET's
  contents survive.** "stdlib `rmtree` unlinks symlinks" is not the canary the
  acceptance box asks for.

**(E) Closing work.** Partly done:
  - `decisions.md` A-359…A-366 — **done**; continue at A-367.
  - `CHANGES.md` v8→v9 block — **extended** with the wire additions, the
    `judgment.r2` fork's consumer impact, and three new migration notes. Extend
    further; do not restart.
  - LOG entries 6 and 7 — **done**.
  - Still to do: mark **B037/B038/B040 RESOLVED** with the ids that close them
    (B038(a)=A-356/A-357, B038(b)=A-358, B040(b)=A-353); tick
    B041/B043/B045/B046 acceptance boxes with file:line evidence IN THE REPORT;
    write `reports/assay-WAVE-B-producer-REPORT.md` (**does not exist yet**;
    `reports/assay-WAVE-A-js-consumer-REPORT.md` is the model, contract in
    `WAVE-PROMPT-2026-08-30-js-consumer-producer.md` lines 176-183); then the
    registered gate.

## 6. Decision asks

**None blocking. Nothing new.** Brief 2 §6's one fork is RESOLVED and
implemented (A-360). Two things for the reviewer rather than questions:

1. **A-360's two unendorsed extensions**, flagged in §4 above — the
   native→ingested forbidding, and `equivalence_artifact` on the wire's
   forbidden list. Both follow from the endorsed reasoning; neither was
   explicitly ruled.
2. **A-354**, unchanged from briefs 1 and 2.

## 7. Environment facts — corrections to brief 2

- **Full `pytest tests/` is ~5m20s–5m30s** (measured twice this session:
  316.80s and 328.17s). Brief 2's ~5m53s was a slightly slow sample. Still far
  too slow to foreground.
- **The Bash tool's working directory is the WORKTREE by default, and a bare
  `cd /workspaces/vbpub` silently moves you to the SHARED MAIN CHECKOUT.** I
  did this once on a `git commit` and it failed on a pathspec rather than
  committing to main — but a command with a valid pathspec would NOT have
  failed. Use absolute paths under `.worktrees/assay-wave-b-producer`, and
  verify `git log --oneline -1` shows a Wave-B commit before trusting any git
  result.
- Everything else in brief 2 §7 holds: Node `v26.5.1` / npm `11.17.0` here and
  not in `tester-unified`; the Stryker recipe in
  `tests/fixtures/mutation/PROVENANCE.md`; the upstream schema package is
  `mutation-testing-report-schema@3.8.4`.

## 8. Housekeeping

- **`coverage_parsers/__init__.py`'s DAG sentence is still stale** (brief 2 §8
  item 1 — it omits the `assay.vocabulary` edge). Not fixed; no commit this
  session touched that file. Still low priority, still real.
- **Tool-use compliance, since two prior generations slipped:** every file
  CONTENT change this session went through `Edit`/`Write`, including all 48
  fixture migrations, both report files and this brief. The two `cp`
  invocations were byte-for-byte DUPLICATIONS (the frozen v9 schema, verified
  with `cmp`; and creating the six W5 template files before editing them),
  which is the one operation `Write` cannot do honestly. Stated in LOG entry 7
  rather than buried.
- **A 44-node suite passing on its first run was treated as suspicious, not as
  success.** `W5/test_acceptance_v9.py`'s negatives were spot-probed directly
  through `verify_document` to confirm each produces its own named diagnostic
  rather than passing vacuously. Recommend the successor do the same for
  B046's parser refusals — the same shape of risk.
