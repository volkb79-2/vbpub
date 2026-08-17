# B004 carve review — adversarial (fable)

**Reviewed:** `nyxloom-trove/W2-CARVE-B004-provenance-verified.md` (958 lines), against the
shipped source on this branch, the shipped docs, `decisions.md`, ciu 6.0.3's source and
test assets, and live `ciu provenance --json` runs from `/workspaces/dstdns` (2026-08-17).

## Verdict: READY WITH CORRECTIONS

The carve's stop-and-report verdict is **correct and survives adversarial review**. Both
gates are real: GATE 1 (one new `ReasonCode` is unavoidable) holds under an *exhaustive*
enumeration of all 30 shipped codes, not just the candidates the carve sampled (§Z below);
GATE 2 (no deployed host can produce `verified-match`) was independently re-measured by the
commissioning reviewer and re-confirmed live here (`overall: "mismatch"`, 20 containers,
16 unlabelled, 4 vendor-label mismatches, exit 2). Every zero-schema escape examined —
including two the carve did not examine — is either dishonest or empty. The corrections
below are real defects in the document, but none reverses its conclusion.

One correction to my own commissioning brief first: the carve does **not** miscount the
reason codes. M11 enumerates exactly the shipped enum — 7 FAIL + 7 ERROR +
9 NO_MEASUREMENT + 3 BUDGET_EXCEEDED + 4 INCONCLUSIVE = 30 — and the string "29" appears
nowhere in the document. Verified against `src/assay/errors.py` directly.

---

## Blocking findings

### F1. The frozen assets the carve claims to have captured do not exist

W4 states: *"Frozen assets, captured for this carve and byte-exact from real ciu 6.0.3
output (see §9): `nyxloom-trove/carve-assets/W2/ciu-provenance-mismatch.json`,
`.../ciu-provenance-not-verified-unknown.json`, `.../ciu-provenance-not-verified-dirty.json`"*
— and O2 calls the first "byte-exact real ciu 6.0.3 output, 2 377 bytes", O3 says "The
frozen asset is `carve-assets/W2/ciu-provenance-not-verified-unknown.json`".

**Measured:** `assay/nyxloom-trove/carve-assets/W2/` does not exist. The directory
contains only `P20…P33` and `W1`. `git log` shows no commit ever added them. The carve
asserts, in the present tense, assets that were never landed.

This matters beyond bookkeeping: the carve's own M8 shows the estate's documents are
non-reproducible on demand — `not-verified-dirty` occurred *spontaneously* between two
clean runs because dstdns has a concurrent committer, and ciu's version will drift. The
witness the carve says it froze may not be re-capturable identical later. (A fresh
mismatch capture succeeded during this review — `overall: "mismatch"`,
`commit_under_test: "016a2674"`, 20 containers — so capture is still possible today.)

**Correction:** capture and commit the three assets now, from real runs, before anything
else about B004 moves — or rewrite W4/O2/O3 to state the capture as an outstanding
obligation of the implementer's first hour, with M8's non-reproducibility named as the
reason it must not wait.

### F2. §2's headline property claims more than the mechanism delivers — and O7 falsifies it

§2: a PASS entry *"means — and means only — that assay read one `ciu provenance --json`
document … **that is, ciu compared the `org.opencontainers.image.revision` label of every
running container in its own compose project against its own repository's HEAD, found at
least one container whose label agreed and none whose label disagreed**…"*.

The mechanism cannot support the bolded clause. assay reads a *file at a declared path
that parses as ciu's schema*; nothing binds the file to ciu having produced it or to any
comparison having occurred. The carve knows this — O7 obtains a PASS from a hand-written
document ciu never emitted, which is a direct witness that PASS does *not* entail "ciu
compared". §2's ten disclaimer bullets cover staleness, unlabelled containers, and the
prefix width, but none says the one thing O7 proves: **document authorship is unverified;
any process able to write `<adjudication_dir>` can mint a green PASS.** For the section
whose stated job is "the exact property this capability claims", this is the defect class
three wave-1 rounds existed to kill (attestation stronger than its mechanism).

**Correction:** rewrite the headline to claim what is delivered — *"assay read one
well-formed document conforming to ciu's provenance schema v1 which reported
`overall: "verified-match"` and whose `commit_under_test` is a lowercase-hex prefix of the
HEAD assay resolved"* — move ciu's comparison semantics into an explicitly-labelled
"what such a document means *when ciu produced it*" sentence, and add the missing bullet:
the document's authorship and integrity are the harness's responsibility;
`verified_by_assay: false` is load-bearing for exactly this reason.

### F3. W2's "Docs (A-270): none" is false — the DESIGN-GUIDE ships a closed reason-code table

W2 says *"Docs (A-270): none — a reason code is not a value a consumer types into a lane."*
A-270's trigger is not limited to typed values: it fires on *"a closed vocabulary value,
or a compatibility fact"*. Measured:

- `docs/DESIGN-GUIDE.md:299-310` carries the full per-outcome reason-code table under the
  sentence *"The enumeration is **closed** — an implementer that needs a code not listed
  here must stop and ask, never invent one."* Adding `PROVENANCE_UNVERIFIED` without that
  row leaves a shipped document enumerating a stale closed vocabulary.
- `docs/DESIGN-GUIDE.md:925-926` states every fixture project carries *"an expected
  verdict artifact covering all six outcomes and every `reason_code`"* — so W2 also owes a
  fixture verdict carrying the new code, or that sentence becomes false.
- None of A-270's three *mechanical* checks covers the reason-code table (check (b) derives
  only vocabularies a consumer types), so the human obligation is the only guard — which
  makes writing "none" worse, not safer.

**Incidental defect found while verifying this:** the table is *already* stale. Its
INCONCLUSIVE row lists three codes; the shipped enum has four —
`ALL_MUTANTS_EQUIVALENT` (added in P33/v5, `git log -S`) **appears nowhere in
`docs/DESIGN-GUIDE.md`, `README.md`, or `docs/CONSUMERS.md`**. A pre-existing A-270
desync, independent of B004.

**Correction:** W2's docs row becomes "DESIGN-GUIDE (reason-code table + a wave note) and
the §10 fixture obligation". Separately — and landable *now*, without B004 — extend W7 to
derive a reason-code vocabulary from `REASON_CODES` alongside the evidence-source set; that
mechanical check would have caught the `ALL_MUTANTS_EQUIVALENT` desync and will catch
`PROVENANCE_UNVERIFIED`'s row when it lands. File the `ALL_MUTANTS_EQUIVALENT` doc fix as
its own small item regardless of B004's fate.

### F4. The mixed-source loader seam is undesigned, and the shipped code refuses the design's own lanes

W3 requires "a lane declaring both sources accepted" and the new pairing rule allows
adjudicated-only lanes with no `attestation_dir`. But the shipped runtime, measured:

- `cli.py:334-345`: `if declared_evidence:` calls `attestation.load_attested_evidence`
  with the **full** declared list, under the comment *"attestation_dir exists by config
  invariant"* — an invariant **W3 deletes** (adjudicated-only ⇒ `attestation_dir is None`).
- `attestation.py:506-509`: the attested loader **refuses any declaration list containing
  a non-attested entry** — *"this loader handles only attested evidence — adjudicated
  evidence has no loader (A-085)"*.
- `runner.py:707` (`_require_evidence_bound_to_lane`): the final `evidence` tuple must
  equal the lane's declared identities **as an ordered list** (list equality, not set
  membership), so two per-source loaders' outputs must be merged back into exact declared
  order for an interleaved declaration like `[attested, adjudicated, attested]`.

So after W3+W4 land, an adjudicated-only lane still ERRORs inside the *attested* loader,
and a both-source lane cannot assemble a verdict, unless W5 does work it never names:
split the declared list per source, guard/skip each loader on its own subset, merge
results in declared order, and preserve A-213's *atomic* timeout contract across two
sequential loaders (a deadline expiry in the second must still render **every** declared
identity `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, discarding the first loader's already-loaded
results — today's atomicity lives inside the single attested call). W5's file list also
omits `attestation.py` (it appears only in W3, for the validator promotion), so as carved
the guard cannot even be touched.

**Correction:** W5 gains (a) the split/merge-in-declared-order design statement, (b)
`attestation.py` — or an explicit CLI-side filter decision — in its file list, (c) an
integration test for an interleaved `[attested, adjudicated, attested]` lane, and (d) a
timeout test proving second-loader expiry still yields the atomic all-identities
LANE_TIMEOUT artifact.

### F5. W1's Gate-2 clearing condition is under-specified — scoping alone does not make green reachable

GATE 2 / W1 tell ciu to scope the comparison to images ciu produced. The carve's own M4
measured a second, independent blocker: ciu's comparison is **string equality**
(`deploy.py:676`, `actual == commit`) between a label and an 8-character short hash, and
dstdns's own labelled images carry **40-hex** revisions — *"which ciu's `actual == commit`
comparison against an 8-character short hash would report as `mismatch` even though they
are dstdns's own images"* (M4's words). A ciu implementer who reads W1's ticket and ships
only the marker-label scoping will still never produce `verified-match`: the vendor false
mismatches disappear and ciu's own 40-hex labels then fail the equality. §8.2's freshness
gap is also a ciu-side fix (a timestamp/nonce) that would otherwise need a *second* ciu
document revision later.

**Correction:** W1's ticket must enumerate all three ciu-side requirements in one change:
(1) scope to ciu-produced images; (2) a prefix-tolerant comparison or a full-hash
`commit_under_test`; (3) a timestamp or monotonic run identifier in the document. The
acceptance criterion stays as the carve states it — a correctly deployed instance can emit
`overall: "verified-match"` — but the ticket must not imply (1) alone achieves it.

### F6. §3.1's worked example leaves a stale-document window that §8.2's missing freshness bound then makes permanent

The carve mandates documenting `ciu provenance --json > "$P/artifacts/adjudicated/image-provenance.json" || true`
with `|| true` called "load-bearing" — correct as far as it goes (exit 2 with a valid
document, M2). But the snippet reuses one path run after run with no delete-first, and the
design has **no freshness bound** (§8.2: a green document at commit X satisfies every
later run at X). So any harness evolution that stops the capture step running — a
conditional that skips it, a refactor that drops it, ciu removed from the image — leaves
the *previous* capture in place, silently satisfying the lane with yesterday's document:
the exact "reads as checked when nothing was checked" state B004 exists to remove. (The
in-step failures are honest by luck: ciu-missing truncates the file to empty via the
redirect and an empty or partial file is `UNREADABLE_ARTIFACT`.) A delete-first pattern
cannot fix a *wholly* skipped step — only ciu's future timestamp can (F5) — but it closes
every window where the step runs and the producer fails, and it costs one line.

**Correction:** the CONSUMERS.md example must make producer failure yield an *absent*
document (row 4, honest `NO_MEASUREMENT`) rather than a stale one:

```sh
rm -f "$P/artifacts/adjudicated/image-provenance.json"
ciu provenance --json > "$P/artifacts/adjudicated/image-provenance.json.tmp" || true
[ -s "$P/artifacts/adjudicated/image-provenance.json.tmp" ] && \
  mv "$P/artifacts/adjudicated/image-provenance.json.tmp" "$P/artifacts/adjudicated/image-provenance.json"
```

with the delete-first line documented as load-bearing for the same reason `|| true` is.
This is the highest-leverage freshness mitigation available at zero schema cost.

### F7. O7 need not fabricate its input — ciu's own suite carries producer-emitted green output the carve never found

§8.1 says the green state has *"no witness anywhere in this estate"* and O7 therefore
hand-writes its document under a `⚠ FABRICATED INPUT` banner. Measured: ciu's own tree
carries seven frozen provenance documents at
`ciu/nyxloom-trove/carve-assets/ciu-P01-worktree-isolation-primitives/provenance-*.json`,
**including `provenance-verified-match.json`** — and `ciu/tests/tests/test_ciu_provenance_json.py`
(docstring + `test_verified_match`, lines 65-78) shows they are reproduced by the **real
`deploy.verify_running_provenance` producer over a mocked docker seam**
(`monkeypatch.setattr(deploy.procutil, "docker", fake)`), compared as parsed JSON. That is
not a live deployment — GATE 2 stands, O8 stays blocked, and adopting the lane still
bricks it — but it is categorically stronger than a hand-written fixture: ciu's shipped
decision logic and serializer produced every byte; only the docker answers are doubles.

**Corrections:** (a) O7 consumes ciu's producer-emitted document instead of a hand-written
one — at the adjudicator level byte-exact, passing `head = "1b369e23" + 32 hex` as the
function's `head` parameter (the fixture's `commit_under_test` is `1b369e23`); at the CLI
level with exactly one named edit (`commit_under_test` → the test repo's real HEAD
prefix). The A-274 re-witness obligation on Gate-2 day stays. (b) §8.1's "no witness
anywhere in the estate" narrows to "no *deployed* witness; a producer-level witness exists
in ciu's own frozen assets". (c) The fixture should be *copied* into assay's carve-assets
with its ciu provenance recorded, not read cross-repo at test time. (d) Bonus measurement
the carve missed, from the same assets: `provenance-refused-no-identity.json` carries
`"commit_under_test": null` — the carve's green-path-only grammar handles this correctly,
but §3.3/§9 never state that `commit_under_test` can be JSON `null` on non-green paths;
W4's tests must pin that a null commit on a non-green document is **not** refused.

---

## §Z. The primary question: is there a zero-schema shape that ships real value? — No, and here is the complete proof

The carve's §5.2/§4.1/§4.2 claim every zero-schema alternative is dishonest. The claim
**survives**, and can be made airtight rather than sampled. The mechanical core, measured:
`verdict.py:340-360` (`_check_reason_code`) requires a reason code on **every** non-PASS
status, drawn from that outcome's closed set, applied identically to claims, evidence
entries, and the verdict itself; the schema's `$defs/status_contract` states the same rule
independently (A-182); and `_check_evidence_covers_declared_evidence` (`verify.py:362`)
makes a declared identity that renders no judgement a verification failure — evidence
cannot be silently skipped. So any lane that declares provenance evidence **must** render
every document state as some legal `(status, reason_code)` pair from today's 30 codes.

Exhaustive enumeration for a `source="adjudicated"` entry:

- **PASS** — legal, green path only.
- **FAIL** (7) — all name coverage/mutation/canary/command defects; `COMMAND_FAILED` means
  the *lane's* command. All lies here.
- **ERROR** (7) — `UNREADABLE_ARTIFACT` true only for the unreadable-file row;
  `FORMAT_MISMATCH` true only for the malformed-document row; `BAD_LANE_CONFIG` blames a
  correct lane file; `GIT_FAILED`/`EXEC_FAILED`/`OUTPUT_WRITE_FAILED`/
  `MUTATION_DISCOVERY_FAILED` name things that did not happen.
- **NO_MEASUREMENT** (9) — `MISSING_ATTESTATION`/`STALE_ATTESTATION` are *mechanically
  refused* on non-attested sources (`verdict.py:2077-2081`, re-measured). `DIRTY_TREE`,
  `HEAD_CHANGED`, `BASE_IS_HEAD`, `EMPTY_COVERAGE`, `BRANCH_UNAVAILABLE`,
  `TARGET_NOT_MEASURED` each carry a shipped, documented, different meaning (A-145/A-268(a)
  name-collision bar). `MISSING_EXTERNAL_TOOL` is the closest name and still fails twice:
  its docstring binds it in writing — *"RESERVED for P27 … named in
  `LanguageAdapter.external_tools`"* (`errors.py:132-135`) — and it could at most describe
  the *absent-document* row; a present, well-formed, non-green document is not a missing
  tool, so the semantic core of B004 ("the tool said not-verified") still has no name.
- **BUDGET_EXCEEDED / INCONCLUSIVE** — timeout and mutation terminals. Lies.

**Conclusion: with today's enum, the only truthfully renderable adjudicated states are
green→PASS, unreadable→`UNREADABLE_ARTIFACT`, malformed→`FORMAT_MISMATCH`. The state the
feature exists to report has no legal truthful spelling.** GATE 1 is real. Now each
candidate escape:

**(a) Green-or-refuse: refuse the lane when the document is non-green, existing ERROR
code.** Dies three ways. (1) *At config load* it is mechanically incapable of the commit
binding — the CLI's documented sequence (`cli.py:325-333`) resolves HEAD *after* load, so
a load-time check must accept any `verified-match` ever captured, deleting the design's
only binding. (2) Moved to run stage, the refusal must carry a reason code, and the only
semi-defensible one, `BAD_LANE_CONFIG`, blames a correct lane file for a stale
*deployment*; worse, it misfiles a **measured finding** ("provenance is not verified") in
the taxonomy class reserved for "assay could not operate" — collapsing exactly the
judged-vs-hollow distinction the outcome vocabulary exists to preserve, with ERROR
additionally outranking everything in `rollup()`. (3) It is not "a narrower true
statement": on a refusal the declared evidence renders no judgement in the artifact, so
the true sentence ("provenance unverified") appears nowhere and a false one
("your lane config is bad") is what consumers' machines read. A-254's precedent does not
stretch this far: a missing `env_required` variable is an invocation defect the caller
fixes before the run; a non-green provenance verdict *is the measurement*.

**(b) Ship the mechanism now, gated behind an unreachable declaration; terminal later.**
This *inverts* the precedent it cites. `MISSING_EXTERNAL_TOOL` reserved the **terminal**
(the enum value, carried by an already-scheduled migration) and landed the **mechanism**
later (A-086: "belongs to the package that first makes the state reachable";
A-144/P22). Landing the mechanism first, with `_EVIDENCE_SOURCES` still `{"attested"}` so
no config reaches it, ships a registry validating only fixtures — the "could only validate
its own empty set" object A-078 refused, resurrected with one dead entry — plus O1/O6
becoming unwritable, in a project that forbids `pragma: no cover`. Zero consumer value,
positive review and maintenance cost.

**(c) Reuse an existing NO_MEASUREMENT code truthfully.** Exhausted above. Two are
mechanically refused; six are documented other meanings; the closest reservation is bound
in writing to another package and covers only one of the three states. The carve's
sampling missed none that survive.

**(d) Split B004: green path now, non-green terminal later.** Dies on totality. The
adjudicator is total over documents the moment any lane declares the evidence: under GATE
2 the non-green branch is not a later increment, it is the **only** branch real input can
take on day one. There is no ordering in which the deferred part is not the first part
executed.

**(e) — novel, unexamined by the carve — recast as Tier-3 attested evidence, zero assay
change, available today.** The harness runs ciu, and *only on* `verified-match` writes
`<attestation_dir>/image-provenance.json` with `{"producer": "ciu provenance 6.0.3",
"attested_commit": <full HEAD>, "reviewed_paths": […]}`. assay's shipped Tier-3 machinery
then does real work: exact-or-ancestor commit binding, path existence, path currentness
(`attestation.py:353-390`), `MISSING_ATTESTATION` when non-green (harness wrote nothing),
`STALE_ATTESTATION` when the commit moved. This is the strongest zero-schema construction
available — and it still is not B004: the green/non-green mapping moves into each
consumer's unaudited shell (A-255's caller-assertion, returned at the mapping layer); the
schema **requires** `reviewed_paths` on attested PASS (measured in `$defs/evidence`), a
field that has no honest value for a container comparison; and ancestor-or-equal is the
*wrong* binding for provenance (containers built from an ancestor commit are precisely not
the code under test). It may serve a consumer as interim *process discipline with eyes
open*, at the recorded rung's trust level plus commit binding — but assay must not
document it as provenance verification, and this review does not propose it as B004.

**Net: the carve's dishonesty claim is upheld under enumeration, strengthened by two
additional escapes it never tested. The one honest bump-free route is the one the carve
already names in §5.2 — reserve `PROVENANCE_UNVERIFIED` in the next schema bump another
item pays for (the exact `MISSING_EXTERNAL_TOOL` maneuver, in the correct direction) — and
this review promotes that from "an option belonging to the operator" to the
recommendation.**

---

## Second question: should assay build this now at all? — No

Even if GATE 1 were granted free today, shipping W3-W7 now produces a capability no
consumer can adopt: under GATE 2 a lane that declares `(adjudicated, "image-provenance")`
renders `NO_MEASUREMENT` on **every run on every host in the estate** — adopting the
feature bricks the lane it is meant to protect. O8, the only oracle proving the headline
claim against real output, is unwritable. And M8 shows that even *after* ciu goes green,
row 7 fires in normal operation on any repo with a concurrent committer, so adoption
guidance needs the capture-fresh discipline of F6 regardless. Wave 1's "a check that
cannot fire does not belong" applies with extra force to a *success* path that cannot
fire: a refusal nobody can reach is dead code; a green branch nobody can reach is a
feature whose entire observable behavior is its failure mode. The honest disposition is
the carve's own stop — sharpened below.

---

## Non-blocking findings

**N1. `FORMAT_MISMATCH` boundary asymmetry with the attested precedent.** The shipped
attested pipeline renders *every* document-grammar violation `ERROR`/`UNREADABLE_ARTIFACT`
(`parse_attestation`: "Raises … on **any** violation of the closed grammar"); the carve's
§3.3 renders decodable-but-wrong-shape `FORMAT_MISMATCH`, a code whose only shipped
producers are the coverage-artifact readers (A-007, `coverage.py:142`). Both spellings are
defensible; two sibling evidence pipelines drawing the undecodable/wrong-shape line
differently is not. O5's two-terminal property survives either choice. Either align with
the attested precedent or state the asymmetry and its reason in §3.3 (A-271's pattern:
asymmetries are ruled, not accidental) and pin it in W4's tests.

**N2. §3.5's stderr line is unspecified for row 4.** "One line naming the input path and
the exact `overall` value it read" — an absent document has no `overall`. State the
absent-file wording.

**N3. §5.4's model tightening (`adjudicated` ⇒ `verified_by_assay is False` in
`Evidence.__post_init__`) is independent of B004** and zero schema surface; it could land
now as a small hardening with a direct test (per the estate's Protocol-stub lesson, a
reachable `raise`-path test, no pragma). Confirmed against the schema: the `else` branch
of `$defs/evidence` constrains only the payload triple, so a schema-legal
`verified_by_assay: true` adjudicated entry is representable today exactly as §5.4 says.
The carve's sequencing (with W5) is also acceptable; flagging only that it need not wait.

**N4. W7's evidence-source vocabulary gap is pre-existing and standalone-landable.** The
four derived vocabularies and the must-fail control are exactly as the carve states
(`tests/test_docs_examples_and_vocabulary.py:287-334`, control at 313). The gap covers
`attested` today, not just B004's future value; closing it (plus F3's reason-code
vocabulary) is a small, immediately valuable item with or without B004.

**N5. Verified accurate, no action:** the six-value `overall` vocabulary against ciu's
producer and fixtures; the closed-enum count (30) and every per-outcome set in M11;
`_EVIDENCE_SOURCES = {"attested"}` (`config.py:106`) and the load refusal
(`config.py:1372`); the `has_attestation_dir == has_evidence` rule (`config.py:1160`);
`EVIDENCE_SOURCES` already carrying `"adjudicated"` (`verdict.py:224`,
`verdict.schema.json:507`); the payload prohibition (`verdict.py:2084`); the
attested-only binding of `MISSING_ATTESTATION`/`STALE_ATTESTATION` (`verdict.py:2077`);
`_timed_out_evidence` being source-agnostic (`cli.py:296`); `read_bounded_input`'s
absent-vs-untrustworthy contract matching rows 4/5; DESIGN-GUIDE:78's falsified
"invokes" wording; `rollup`'s `ERROR > NO_MEASUREMENT > … > FAIL` precedence making the
§3.5 rollup claim true; and every test file named in W2-W7 exists. Line-number drift found
was ≤2 lines and immaterial.

**N6. The 8-hex prefix binding is adequately disclosed and is not the weak point.** The
accidental cross-commit collision risk (~2⁻³²) is dominated by the two real weaknesses the
design already carries: whole-document forgeability (F2) and same-commit staleness
(§8.2/F6). No change requested beyond F2's bullet and F5's full-hash request to ciu.

---

## Shape reconsidered — recommended disposition

**Defer B004's implementation entirely; land its paperwork and its independent spin-offs
now; reserve the terminal in the next bump; build when both gates clear.** Concretely:

1. **Now — W0, amended by this review:** the decisions rows the carve lists, plus F3's
   correction of W2's doc obligation, §8.1's narrowed no-deployed-witness phrasing (F7),
   and a row **reserving the name `PROVENANCE_UNVERIFIED`** so the next verdict-schema
   bump that ships for any other reason (B001/P34's SQL adapter, or B007) carries the enum
   value and the §5.4/§8.8 schema tightenings at zero incremental consumer cost — the
   `MISSING_EXTERNAL_TOOL` maneuver, applied in its correct direction.
2. **Now — W1, expanded per F5:** one ciu ticket naming all three requirements (scoping to
   ciu-owned images; prefix-tolerant comparison or full-hash `commit_under_test`; a
   timestamp/run identifier). This is the critical path for the entire capability and it
   is not assay's code.
3. **Now — the independent items:** F1's asset capture; F3's `ALL_MUTANTS_EQUIVALENT` doc
   fix; N4's evidence-source + reason-code derived vocabularies; optionally N3's model
   tightening.
4. **Not now — W3, W4, W5, W6, W7's B004 half.** Blocked until *both* the reserved code
   exists in a shipped schema *and* ciu can produce `verified-match` on a correctly
   deployed instance — because until the second, adoption bricks lanes and O8 is
   unwritable. When built: with F2's honest §2, F4's loader-seam design, F6's
   capture-fresh CONSUMERS example, F7's producer-emitted O7, and N1's ruled boundary.
5. **Interim consumer guidance** stays the shipped recorded rung (A-254/A-255
   `env_required`), upgraded to ciu-attested for free when CIU-21 lands (§4.6, endorsed).
   The Tier-3 recast (§Z(e)) exists for a consumer who insists on gating today, and should
   be presented — if at all — as their process discipline, never as assay-documented
   provenance verification.

The carve is a genuinely strong document — its measurements are honest, its two gates are
the right two gates, and its refusal to route around them is exactly the discipline the
backlog asked for. The corrections above make its stop verdict *executable*: what to do
now, what to reserve, and what the day-both-gates-clear implementer will actually need
that the current text does not give them.
