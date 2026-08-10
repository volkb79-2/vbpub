# P26 — attested evidence and lane-wide Git deadline — Sol JIT carve

## Result

**READY.** This freeze was performed by `gpt-5.6-sol` at `xhigh` against clean
post-P25 main `233926cedd26a6e34512806e267b7141377913b2` (tree
`a704abed77692977df0eda8c6d39a17797b12b05`). P26 remains a 2c bounded
cross-module integration routed to Sonnet xhigh, followed by a fresh Opus
xhigh review. It must not be dispatched without the commit containing this
report, the amended handoff/decisions, and the locked packet under
`nyxloom-trove/carve-assets/P26/`.

The frozen packet contains eleven hashed files; its manifest SHA-256 is
`4cb702ddad368becd8aca55c0d5ef6ac2c55a086bb88751ff7a450d3b05352f8`.
Quick acceptance is a controlled red at **9 passed, 32 failed**. The carver
premise probe is green and records eight independently observed facts plus its
PASS marker.

## What the JIT pass changed

The provisional handoff was not dispatchable. Landed P23–P25 code and real
probes and the final hostile pass exposed eight contract defects plus five
proof/authoring gaps that would have forced the implementer to
invent product behavior:

1. **R0 grammar contradiction.** Evidence is a sibling axis independent of
   rigor, but the provisional `judge.evidence` example was illegal on every
   R0-only lane because A-048/A-062 permitted no judge table. Restricting
   attestation to R1+ would contradict the verdict model and make Assay's own
   permanently R0 self-hosting lane unable to consume Tier-3 evidence. A-209
   admits only the both-present attestation pair on R0 and leaves every
   computed judge field forbidden.
2. **The “4,096 comparisons” bound was not a bound.** Each reviewed path uses
   both existence and staleness queries, directory spelling had no byte/depth
   limit, and resolving one record at a time could start Git before discovering
   the batch exceeded its ceiling. A-210 stages all files/structure and freezes
   `2 * total valid paths <= 4096` before any Git.
3. **Missing parent had the wrong semantics.** P20's existing safe reader
   returns unreadable when an intermediate directory is absent. For a declared
   attestation, absent directory and absent final file mean the same thing: no
   producer supplied it. The new explicit safe-input seam returns `None` for
   `ENOENT` at any component but still refuses symlink/type/permission/race.
4. **The deadline began too late and excluded R0.** P23 starts its deadline
   only after repo identity, dirt, HEAD, and base resolution, and CLI resolves
   HEAD earlier still. P26's new attestation Git would also have been unbounded
   on R0. A-212 starts one deadline in CLI before HEAD and passes it through
   both execution states. R0 remains direct/no-snapshot; it no longer gets a
   fresh full command duration after evidence work.
5. **The stated scope could not close F8.** Higher-rigor mutation checks call
   Git from `mutation.py`, and isolated canary R1 calls originate in
   `canary.py`. Both were provisionally forbidden. They are now narrowly in
   touch scope for deadline forwarding only; computed semantics and payloads
   remain fixed. Conversely P22's already-deadlined isolation owner remains
   forbidden.
6. **Evidence flow was not bound to its source.** Defaulting runner evidence
   inputs to empty tuples lets a caller omit a declaration that is already
   present on the lane and still receive a plausible artifact. The runner now
   derives the authoritative ordered identities from the lane and rejects a
   mismatch before work; the final public assembler repeats the check. Empty
   defaults remain legitimate only for a lane that actually declares none,
   avoiding mechanical churn in existing callers.
7. **Ordinary JSON parsing left identity ambiguous.** Duplicate member names
   could silently select a first/last value, and lone surrogates evade a
   nominal UTF-8 byte bound. Both are explicitly unreadable before Git.
8. **Cleanup could be conditioned on the wrong process.** The direct Git child
   can exit while a forked descendant retains its pipe and process group. The
   group-kill obligation now survives `proc.poll() != None`, and the locked
   witnesses use that exact exited-parent shape.
9. **The new gate phase could certify source instead of the wheel.** A marker
   and test-path substring did not prove import provenance. The frozen block
   now uses the run-venv interpreter, clears ambient `PYTHONPATH`, overrides
   pytest's configured source path, and supplies only the worktree asset/root.
10. **“After `--`” was not an independently proved literal-path claim.** P20's
    generic boundary already supplies global `--literal-pathspecs`, but a
    bespoke P26 child could omit it while simple metacharacter fixtures still
    passed. A decoy that matches only under pathspec expansion now makes that
    omission red.
11. **The hardest loop had signatures but no compiling construction.** The
    packet now includes a Ruff-clean, compiled, locally probed selector/
    process-group skeleton; locked product tests remain independent of it.
12. **An event did not wake an already-blocked selector.** The first expiry
    witnesses could race the worker into a 60-second selector wait, turning a
    hang failsafe into the deciding mechanism. A release FIFO now withholds the
    wakeup byte until after the test marks expiry; timing affects neither
    branch nor expected terminal.
13. **File existence did not mean PID publication was complete.** The premise
    probe could observe its descendant PID file between create and write. The
    helper now writes a sibling temporary file and atomically renames it; the
    60-second loop is only a hang failsafe.

A-209–A-214 ratify these corrections. They are product/contract decisions,
not implementation freedom.

## Premise probes

`probe_current_failures.py` ran against the input implementation using local
disposable repositories and controlled processes. It observed:

- a record reviewing directory `reviewed` returns **PASS** after
  `reviewed/child.py` changes;
- key `../outside` reads a seeded valid JSON record outside the declared
  attestation directory and returns **PASS**;
- the existing bounded safe reader maps `missing/review.json` to
  `ERROR/UNREADABLE_ARTIFACT`, not absence;
- killing only the generic Git boundary process leaves its forked pipe-holder
  alive, proving the boundary owns no process group; and
- real Git's literal `ls-tree -z` and `diff --quiet` construction correctly
  distinguishes a tree, a path containing `*?[x]` plus a literal newline, and
  descendant changes without any display-name parser.

The compact exact record is `probe-results.json`. The escaped process was
killed by the probe's exact process group during cleanup; no repository data or
external service was touched.

## Frozen solution

The packet transfers the solution in four complementary forms:

- `interface-contract.json` is the machine-readable list of constants,
  symbols, lifecycle, and query cost;
- `skeleton.patch` gives the exact public types/signatures and call flow so the
  implementer does not redesign the interfaces;
- `git_boundary_skeleton.py` is a compiling, locally probed construction for
  the deadline/selector/process-group loop; and
- `test_acceptance.py` owns real-Git, config, complete-artifact, aggregate,
  duplicate/Unicode grammar, exited-boundary process groups,
  bootstrap-no-successor, lane-bound evidence inputs, deadline-forwarding, and
  installed-wheel gate oracles.

The standalone skeleton passes Ruff, compiles under the frozen interpreter,
returns `(0, b"ok", b"")` for its bounded success probe, and maps a five-byte
producer against a four-byte ceiling to `ERROR/GIT_FAILED`. The locked actual-
product process tests add the synchronized exited-parent/pidfd cases rather
than trusting the skeleton as its own oracle.

Four hand-authored complete v4 templates cover current, stale-directory,
malformed/missing/current independent sibling states, and atomic attestation
timeout. Runtime substitution is
limited to the real version, exact OIDs, and parsed timestamps. Status-only or
producer-generated expected output cannot satisfy the comparison.

The production design is exact:

1. config loads a closed `EvidenceConfig` list and its explicit contained
   directory, with one narrow R0 exception for these consumed HOW fields;
2. descriptor-safe input reads every declared file once and stages all
   structure/bounds before Git;
3. four narrow sanitized Git helpers verify exact commit, ancestry, raw exact
   blob/tree existence, and literal per-path currentness;
4. CLI starts one deadline before HEAD, atomically resolves evidence, then
   adapter and computed work;
5. every generic Git child samples that same absolute remainder, owns a new
   session/process group, and preserves the original timeout terminal; and
6. normal and refused verdicts carry exact ordered declaration/evidence
   coverage, including the frozen atomic attestation-timeout artifact.

No runtime dependency, schema change, adjudicator registry, source copy,
ambient Git path, or second command-plan mechanism is introduced.

## Requirement-to-oracle traceability

| requirement | frozen owner/evidence | oracle | convenient violation made observable |
|---|---|---|---|
| R0/higher exact declaration | `EvidenceConfig`, round-trip matrix, complete templates | O1 | silently forbid R0 or drop/reorder an identity |
| evidence survives refusal | CLI lifecycle + adapter-refusal fixture | O1 | adapter error emits empty evidence siblings |
| contained safe input | `read_bounded_input`, outside/symlink/missing matrix | O2 | precheck then reopen escaped or call all absence unreadable |
| structural work bound | staged records + 3×700 query-cost fixture | O2 | start early Git then discover aggregate excess |
| immutable commit identity | 40-lowercase parser + annotated-tag object witness + exact `rev-parse` helper | O2/O3 | peel a tag/branch/short/uppercase identity into an apparent commit |
| exact file/tree currentness | raw `ls-tree -z`, literal `diff --quiet`, metachar-decoy witness | O3 | newline/name parser, pathspec expansion, or exact-membership directory PASS |
| singular lane budget | missing-only atomic expiry + callable signatures + exact 17-second R0 witness | O4 | never sample a no-Git batch, start after HEAD/evidence, or reset command duration |
| child ownership | FIFO/pidfd synchronized fake child | O4 | kill only direct Git child; descendant holds pipe |
| no successor after expiry | fake Git bootstrap argv ledger | O4 | timeout bootstrap then launch substantive command |
| installed product | exact run-venv command, cleared `PYTHONPATH`, ini override, and marker | all | cockpit/source import passes while wheel lacks P26 |

## Exact pre-dispatch adversarial specification review

The canonical AUTHORING prompt was run against the final handoff and every
named packet file:

> Review this handoff as a hostile implementer, a hostile environment, and an
> independent acceptance engineer. Do not propose code yet. Build a
> requirement-to-oracle traceability table and try to make every oracle pass
> while violating the stated product goal. Identify: undefined interfaces or
> data grammar; values the implementer must invent; shadowing or silent
> defaults; ambiguous ownership; missing terminal states; repo/project,
> host/container, source/artifact, or declared/effective namespace confusion;
> stale or producer-authored evidence; unbounded work; order, clock, ambient
> environment, and repeated-execution dependence; scope/dependency conflicts;
> and tests that share the implementation's assumption. Then construct a
> pairwise input matrix and name at least three combined-axis fixtures likely
> to break a convenient implementation. For each oracle, give one plausible
> wrong implementation that still passes the proposed test. Mark the handoff
> NOT READY if any externally visible decision, interface, example, bound,
> refusal, or proof source remains for the implementer to invent. Return only:
> (1) blocking ambiguities, (2) false-PASS attacks, (3) missing implementation-
> packet content, (4) scope/dependency defects, (5) a corrected oracle/fixture
> matrix, and (6) READY or NOT READY with reasons.

### 1. Blocking ambiguities

All thirteen defects/gaps above are resolved. In particular, the final packet defines
the R0 grammar exception, types/signatures, path namespaces, canonical spelling,
every byte/cardinality/depth/query bound, missing-vs-unsafe distinction, staging
order, full-OID grammar, raw Git argv/exit meanings, timeout precedence,
atomic-batch artifact, adapter/refusal sequencing, direct-R0 deadline behavior,
process-group cleanup even after the boundary child exits, evidence inputs
bound to their authoritative lane source, duplicate/UTF-8 JSON behavior, gate
location/marker, and exact owners. No external choice remains for Sonnet.

### 2. False-PASS attacks

| oracle | plausible wrong implementation that passes a weaker test | frozen counter-oracle |
|---|---|---|
| O1 | permit declarations only on R1, or resolve evidence after adapter so a Go refusal erases it | R0 exact round-trip, adapter-refusal complete artifact |
| O2 | validate `attestation_dir/key` with `resolve()` then reopen; map every `ENOENT` to missing | seeded swap/outside/symlink/type matrix and descriptor API |
| O2 | cap each record at 1,000 paths but process 64 records serially | three 700-path records, all unreadable before first Git argv |
| O3 | retain `git diff --name-only` and exact membership | real changed directory plus literal newline/metachar filename |
| O3 | stop at the first stale path and never notice a later nonexistent path in that record | all of that record's existence calls precede its diff calls |
| O4 | pass `lane.budget_seconds` into each Git/process or start deadline inside scratch | injected R0/deadline owner and decreasing-remainder call flow |
| O4 | skip group cleanup once the direct child exits | synchronized exited boundary plus descendant-held pipe and pidfd exit witness |
| O4 | catch all `AssayError` in attestation as unreadable | exact sentinel identity survives and no successor argv exists |

### 3. Missing implementation-packet content

None remains. The skeleton intentionally omits private bodies, but every public
surface, external result, ordering constraint, lower owner, proof source,
constant, and refusal is fixed. The locked quick suite is directly runnable and
its 9/32 baseline was observed rather than predicted.

### 4. Scope/dependency defects

The provisional `canary.py`/`mutation.py` forbid was a real defect and is fixed.
Both are touchable only for remaining-callable forwarding. `isolation.py`,
verdict/schema, adapters, gate qualification assets, and locked P26 assets stay
forbidden. The registered gate driver and DESIGN-GUIDE are explicitly in scope;
the nonexistent provisional `README.md` target was replaced by the actual docs
owner. No cross-project or unowned path remains.

### 5. Corrected pairwise and combined-axis matrix

| axis | A | B | C |
|---|---|---|---|
| rigor | direct R0 | R0+R1 | R0+R2/R3 nested integrity |
| declaration | none | one current | ordered malformed/missing/current |
| directory | absent | real contained | symlink/replaced/special |
| record | exact | over byte/count/path | unknown/short/uppercase OID |
| reviewed identity | file | directory | newline/metachar/leading-dash |
| history | equal | ancestor + unrelated change | descendant/unrelated/missing path |
| Git phase | bootstrap | existence | diff/status/HEAD/base |
| deadline | ample | expires before spawn | expires after byte + descendant |
| computed state | PASS | adapter refusal | command dirt/HEAD/timeout |
| artifact | current | stale/missing/unreadable | atomic timeout/refused evidence |

Required combined-axis attacks include:

1. R0-only evidence plus missing directory plus later current record, proving
   the grammar and missing-parent semantics together;
2. changed directory descendant plus unrelated exact file plus newline/
   metachar filename, preventing one convenient parser from covering another;
3. malformed first, missing second, current third plus a passing command,
   proving independence, order, complete sibling coverage, and computed work;
4. aggregate excess across individually valid records plus forbidden Git spies,
   proving “bounded” is before work rather than after retained output;
5. adapter refusal plus current evidence, proving external/computed axes do not
   erase each other; and
6. bootstrap writes one byte, forks a pipe-holder, then the atomic evidence
   deadline expires, proving original terminal, group death, no successor, and
   no command side effect in one shape.

### 6. READY verdict

**READY.** The corrected handoff has exact owners/interfaces/examples/bounds/
terminals, an implementation-shaped packet, hand-authored complete artifacts, real
Git witnesses, deterministic process synchronization, controlled red, installed
wheel gate, explicit scope, and mechanical BLOCKED triggers. No product
decision is delegated to the implementer.

## Successor dispositions

- P27 remains the Go gate/adapter resolution package. P26 must not register Go
  or treat adapter refusal as permission to erase already-resolved evidence.
- P28 inherits A-208's complete independent tuple and P25's current-product vs
  release distinction; attestation is not a substitute for real srdm R1.
- P29/P30 retain SB-P23-02/03: symbolic base uses the complete consumer
  merge-base/first-parent contract, and helper/reachability evidence must be
  live. P26's full-OID rule applies only to untrusted attestation identity,
  never to `judge.base` grammar.
- P32 Vitest remains independent of Tier-3 evidence.
- Future Topos adoption still owns A-202's three absolute symlinks. P26 does
  not weaken committed-snapshot topology or turn P25 qualification into
  adoption.
- The P26 reviewer carries only genuinely non-repository successor hints. It
  must not concatenate P25 briefs or restate the frozen contract.
