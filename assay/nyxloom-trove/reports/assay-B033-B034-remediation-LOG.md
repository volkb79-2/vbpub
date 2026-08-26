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
