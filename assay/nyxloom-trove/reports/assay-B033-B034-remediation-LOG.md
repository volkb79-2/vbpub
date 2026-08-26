# assay B033/B034 — remediation — LOG

**Branch:** `fix/assay-b033-b034-sql-mutation-operators`
**Base:** `main` at `8de40fa2` (assay-v2.4.1)
**Filings:** B033, B034 in `nyxloom-trove/4-backlog.md`, from
`reports/assay-review-gap-audit-2026-08-25.md` §5 (`ba8908d6`) and §1
(`126ef577`/`6324548d`).
**Evidence:** `reports/assay-B033-B034-remediation-REPORT.md` (per-item
before/after transcripts driven through the CLI, plus the gate transcript).

One entry per commit, newest last. Each entry carries a sha256 over its own
commit hash + body so a later edit to this file is detectable:

```sh
python -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())'
```

## Commits

### `6e0dca84`

B033 (A-325) and B034 (A-326), code and tests. B033: `judge.mode` becomes what the shipped code had already made it and no document admitted — a LANE-level judging scope read by R1 and R2 together. `_mutation_targets_whole` refuses a declared target that fails a containment gate (outside `source_roots`, absent at the judged commit, inside an excluded directory, not adapter-recognised source, a test path) naming the target and the gate, where it previously `continue`d silently past three of the five and let an unnamed `GIT_FAILED` surface for the fourth; `evaluate._resolve_whole_target` is the reference shape, gate for gate and order for order. `judgment.resolved.base`/`base_resolution` are omitted whenever no tier that reads a base ran, which on a whole-target lane means both of them — the same false rule ("r2 always compares a base") was found in three independent places (`Judgment.__post_init__`, `verify._check_base_matches_the_tiers_present`, the packaged JSON Schema's own conditional) and corrected in step, with the W2 frozen schema copy kept byte-identical. Schema v7 is deliberately NOT bumped: the relaxation is a pure widening, and A-324's own test ("nothing that ever successfully ran `assay verify` stops verifying") was applied and passes. The narrowing half is now asserted only where `judgment.r1.mode` witnesses it; the unwitnessable `R0,R2` case is left unasserted and filed as B035 rather than guessed at. `judge.base` under `whole_target` is refused as inert config in every language at every rigor, both `declared_language != "sql"` carve-outs are deleted, `targets` leaves the surplus exemption, and `mode` requires R1 **or** R2. B034: `_semantic_comparison_sites`, `_is_enum_member_expression` and `_is_uuid_expression` are deleted rather than repaired — their 87 sites over `src/assay/**.py` were a byte-identical subset of `compare-swap`'s, so the double-counting and the stale-`left` mis-splice die with them; `config._load_mutation` and both `--operators` paths refuse either name by name; the two spellings stay in the catalogue and therefore in the schema, because a released `assay verify` accepts a v7 document naming them and a released `assay run` emitted such documents. The two items land in one commit because they share `src/assay/config.py` and hunk-level staging is not available in this environment.

**entry-sha256:** `55ac2d84953105db7135ae533beb96cffa47f776bad3b77ad60ffbd2dfb394d4`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `1fedb8fa`

Decisions, backlog and consumer documentation. A-325 and A-326 recorded in `decisions.md` with their reasoning, including the two questions a reviewer is most likely to re-open: why schema v7 is not bumped for either item (A-324's test applied twice, with opposite answers — B033's schema change is a widening that breaks nothing, B034's would be a removal that breaks real artifacts, which is why the operator spellings survive the behaviour), and why B034's redesign path was rejected on A-112/A-221 rather than on difficulty (a concrete distinct rule was found — splice `==`→`is` on an in-place UUID construction — and rejected as new, unmeasured mutation-testing design of exactly the kind A-221 already excluded once). B015's status corrected from IMPLEMENTED to WITHDRAWN, since the one acceptance box that would have caught this was the one left unchecked when it was marked implemented. B035 filed for the `judgment.r2` scope field that a v8 bump would carry. `README.md`, `docs/CONSUMERS.md` and `docs/DESIGN-GUIDE.md` corrected on both counts — every shipped doc still described `mode`/`targets` as R1-only, and the design guide's own example lane still recommended declaring all six Python operators. `CHANGES.md` names all three breaking lane-config changes, including the one that requires a consumer edit: dstdns's `cw2b_schema` must delete `base = "origin/main"`.

**entry-sha256:** `85005fc588dfe5b13a7003a7051eb26e9d184ac7ac17c7423831ca2230053afa`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `949cac2f`

This LOG, plus one README correction the doc sweep had missed: the opening bullet still said `judge.mode` chooses "which question R1 asks", which is the same R1-only framing A-325 corrects everywhere else and the reason `judge.base` is now refused on such a lane.

**entry-sha256:** `f01f276cf631300f43c7868dbba101ea9412abafa7da517464859ae3ed305d28`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `40127f76`

`docs/CONSUMERS.md`'s SQL section gains the paragraph it never had: its pasteable lane is a DIFF-mode R2 lane and correctly declares `judge.base`, while dstdns's real lane is whole-target and must not. Written after re-reading the section rather than assumed — the shipped example needed no change, only the missing alternative did.

**entry-sha256:** `f484c780ec97f5452de2976f1b6188521cfb2827b5cdbc467bc7ec7abc94671d`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `b3648130`

The registered-gate transcript in the REPORT: green at `40127f76`, through `ASSAY_GATE_PHASE=independent-self-hosting-passed`, with `GATE_EXIT=0` read in a separate step per LESSONS L4. Two earlier attempts are recorded as aborted rather than quietly dropped: both refused `NO_MEASUREMENT`/`DIRTY_TREE` because an uncommitted file of mine left the worktree dirty, which is the gate working, not failing. The REPORT also records the consumer notification this wave owes dstdns.

**entry-sha256:** `3e6cdc7400b1e1b88c3561eb962face45a5968775737aad84ae7a95720f991a5`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

---

The three entries above were written after their own commits landed, so this
file's final commit is not itself logged — the same shape every append-only
self-hashed log ends on.

## Round 2 — 2026-08-26, independent adversarial review (ACCEPT-conditional)

One blocker, four findings; all addressed. The blocker is worth naming here as
well as in the REPORT, because of what it was: two `4-backlog.md` status lines
claimed B033/B034 were "released in assay-v2.4.2", a tag that does not exist on
a branch that is not merged. This wave's own remit included correcting B015's
status from a claim that outran its acceptance state — shipping the same shape
one notch further out would have been a self-inflicted repeat.

### `a667862c`

Round-2 blocker plus findings 2, 3 and 5. Blocker: both backlog status lines now name the branch and say unmerged/unreleased, and the two further stray "2.4.2" claims go with them (B015's own status line, README's "as of 2.4.2"); the `assay-2.4.2.devN+g<sha>` strings in the REPORT are deliberately untouched, being literal `git describe` output from the wheel the gate built rather than claims about a release. Finding 2: the "known operators:" and per-language suggestion lists rendered the full catalogue, which still SPELLS the withdrawn names, so a mistyped operator was answered with a withdrawn suggestion and a second refusal on the retry; both messages now render the declarable set while the membership checks either side keep reading the spellable one — A-326's asymmetry, now load-bearing in both directions. Finding 3: taken as a code fix rather than a wording softening — R2's whole-target resolver gains R1's symlink gate as its own first check, eight lines mirroring `evaluate.py`, replacing a fall-through to `read_regular_file` that refused a symlink as `ERROR`/`GIT_FAILED` (a repository failure for a lane-config mistake) and making this LOG's own "gate for gate, order for order" claim true rather than qualified. Finding 5: A-326 cited `go:*`/`sql:*` together as precedent for "legal to spell, impossible to produce"; `adapters/go.py` returns `UNSUPPORTED` unconditionally and holds, `adapters/sql.py`'s own docstring reads "Never `UNSUPPORTED`" and does not — both read in the source before editing, the false half withdrawn in place with the correction recorded, the ruling left standing on `go:*` alone.

**entry-sha256:** `7d825592f2b9120836158cfc4911f49eda7a9f5027317101a04517e570dc0c39`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### `6eb0f925`

Round-2 finding 4, the one item that was about saying something rather than changing something. "A pure widening" was true and half the story: widening a verifier means it stops rejecting things, and some of what it stops rejecting is malformed. Measured on both builds in both directions and recorded in the REPORT — the honest whole-target R2 artifact 2.4.1 refused now verifies (the intended gain), and a diff-based `R0,R2` verdict with its `base` deleted now verifies too (the cost). One rule cannot both require and forbid `base` for two shapes the artifact does not distinguish, which is precisely what B035 exists to fix; B035's backlog entry now carries that argument, naming dstdns's `cw2b_schema` as the live instance, instead of reading as a neutral deferral.

**entry-sha256:** `21e74ede2389dc8aa2fc1761b6144993d860527a7372f3c6b78d07fab0ba8124`
(over `"{commit}\n{body}\n"`, UTF-8, this entry's own text)

### Round-2 gate

`bash tools/tester-unified-gate.sh ..` re-run at `6eb0f925`, green through
`ASSAY_GATE_PHASE=independent-self-hosting-passed`; `GATE_EXIT=0` read in a
separate step (LESSONS L4). Full phase transcript in the REPORT.
