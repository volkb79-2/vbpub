# P25 — real Python/Topos qualification — Sol JIT carve

## Result

**READY.** This freeze was performed by `gpt-5.6-sol` at `xhigh` against the
clean post-P24 input `9f522a72d37b9cb5beb1939ceca1978c9fc4ef23` (Topos tree
`1bc8a51296b74e536bf60b534efb2fc938dcc389`). P25 remains a 2d constrained
implementation routed to Sonnet xhigh, followed by a fresh Opus xhigh review.
It is not implementation-ready from any earlier revision or without the locked
`nyxloom-trove/carve-assets/P25/` packet in this freeze commit.

The claim is intentionally qualification, not adoption. Current Topos commits
three absolute `/etc/passwd` symlink fixtures and is therefore **not directly
adoptable** by an Assay R1+ lane. The frozen proof uses one exact disposable
prospective-consumer patch deleting those three links, retains the other five
contained relative symlinks, and requires the same issue to be resolved by
Topos before a later Topos-owned adoption.

## Resolved blockers

The provisional handoff was not dispatchable. Real construction and hostile
probes found five material contract defects:

1. **Unsafe committed topology.** P22 correctly refuses the three absolute
   links. Filtering them invisibly would be a false PASS; weakening P22 would
   reopen a snapshot escape. A-202 fixes the exact deletion set and the honest
   no-adoption boundary.
2. **The comparator evidence disappeared.** P23 destroys the private snapshot
   before an outer comparator can read its coverage file. Asking pytest-cov for
   two JSON outputs did not duplicate the evidence: only the last destination
   received bytes, while Assay saw `EMPTY_COVERAGE`. A-204 freezes a bounded,
   non-interpreting wrapper that byte-copies the exact profile after pytest
   succeeds.
3. **Ordinary Git indexing changed the source.** Reinitializing the exported
   tree and using ordinary `git add` caused the carried root ignore rules to
   omit four files that are tracked in real Topos. The targeted lane stayed
   green while the full suite failed 13 tests. A-203 force-adds the already
   enumerated source and asserts the exact 965-entry baseline index.
4. **The targeted environment was misleading.** `PYTHONPATH` alone passed the
   targeted 19-test proof but failed the full suite. A-204 freezes a closed
   interpreter/PATH/HOME/XDG/locale map with no ambient passthrough.
5. **One frozen wheel would prove stale code.** A permanent 1.2.5-only proof
   could remain green after P26 changed Assay. A-205 assigns the full and
   integrity proof to P24's current installed wheel, while a separately
   hash-installed clean-tagged 1.2.5 wheel proves only the release-consumer
   path.

These are contract corrections, not implementation discretion. Decisions
A-202–A-206 and the handoff now assign every resulting owner and terminal.

## Frozen implementation packet

The packet contains 19 payloads bound by
`carve-assets/P25/fixture-manifest.json` (manifest SHA-256
`eedb73711d8ad56b03ea11230b2f0f3d9e929683e15195453e89a0035a9a6ffd`):

- exact input, Topos blob/tree/symlink, source-line, and scenario manifests;
- complete PASS and missing-line v4 templates with a closed normalization
  allowlist;
- seven exact source/test/wrapper fixtures;
- a compiling production skeleton with fixed public signatures, constants,
  scenario objects, comparator, normalization grammar, and TODO boundaries;
- the complete carver tracer and compact result record;
- a twice-built byte-identical clean-tagged Assay 1.2.5 wheel plus P24 closed
  release manifest; and
- an independent 13-test quick suite that validates assets, source pins,
  literal manifests, whole-artifact comparison, exact promotion, and gate
  wiring.

The release wheel is 232,650 bytes with SHA-256
`a0f8d28e4f6359e90616343badcf3c663eb7e2075c1a521bf9da8afd7002dc86`;
its release-manifest SHA-256 is
`a77574808803d2445553716d0b45dbe8b1d9b0261c0604701f96b752dc625e3e`.
It was built twice from exact Assay source tree
`e077f25171778bf2e2982996f4466ad9d1259d12`, a fixed clean `assay-v1.2.5`
tag and epoch, and P24's five-wheel offline closure. The two wheels were
byte-identical.

The production harness must copy the skeleton and locked fixtures rather than
redesign their contracts. Private helper decomposition is free; pins, paths,
commands, environment, limits, terminals, proof sources, wheel roles, marker
order, and Topos adoption boundary are not.

## Real satisfiability proof

The complete tracer ran in `tester-unified:local` with `--network=none` under
the repository-mandated validated background cgroup. It reconstructed the
pinned source, applied only the three named disposable symlink deletions,
force-indexed 965 paths, and left the real checkout clean.

Observed facts:

- full Topos suite plus P25 probe: **2,923 passed**, coverage for 94 files;
- common PASS: Assay PASS 5/5 and copied Topos evaluator PASS 5/5;
- missing branch: Assay `FAIL/UNCOVERED_LINES` 4/5 at line 7 and copied Topos
  evaluator FAIL 4/5 at the same line;
- exclusions forbidden: Assay `FAIL/EXCLUDED_LINES`, while Topos's evaluator
  gives the expected capability-asymmetric PASS because it cannot represent
  exclusion provenance; and
- imported comment-only file: both PASS 0/0, with Assay `considered=1`.

The full result is locked in `probe-results.json`. Dynamic child commit OIDs
are checked as real 40-hex identities before normalization; the deterministic
baseline OID was `12ac3a4abba87522e95cae3233d06d10f39650c5`.

Before implementation, quick acceptance is the intended controlled red:

```text
9 passed, 4 failed in 0.36s
```

Only the absent production harness, byte-exact production promotions,
registered gate marker/wiring, and consequently the no-alternate-route check
are red. All packet, pin, comparator, source-manifest, and reference-probe
checks are green. After implementation all 13 must pass; the controller then
owns the registered live gate and receipt.

## Requirement-to-oracle traceability

| Requirement | Owner/evidence | Oracle | Convenient violation made observable |
|---|---|---|---|
| exact prospective Topos source | input manifest + exporter/index assertions | O1/O3 | wrong revision, ignored tracked files, extra filtered symlink |
| full current-product R1 answer | current P24 run-venv wheel + 2,923-test lane | O1/O4 | targeted-only or stale-release-only green |
| installed release consumer path | P24 verifier + pip hashes + 1.2.5 smoke | O2 | source import, wheel glob, post-check byte change |
| independent answer | literal line manifest + copied Topos evaluator | O1/O2 | producer-authored expected artifact or universal PASS |
| exclusion policy | hand line manifest + asymmetric terminal table | O2 | silently erase exclusion or demand an impossible Topos reason |
| complete v4 identity | locked templates + narrow normalization | O1/O4 | status-only comparison or invented default field |
| committed-state integrity | profile/dirt/base/HEAD/root/source negative matrix | O3/O4 | stale evidence, command-created dirt/commit, wrong root |
| execution boundary | existing P24 outer gate + new ordered phase | O3 | cockpit/Docker bypass, missing cgroup/network/receipt marker |

## Exact pre-dispatch adversarial specification review

The following canonical AUTHORING prompt was applied to the final handoff and
all named context, not merely treated as a checklist:

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

All five blockers above are resolved. In particular, the packet defines the
otherwise inventable source topology, baseline/HEAD contents, exact 965 index
count, wheel owners, install route, argv, closed environment, external witness
ownership, byte bounds, complete-artifact normalization, terminal matrix,
public helper API, gate insertion point, marker order, and no-adoption claim.
No externally visible choice remains for the implementer.

### 2. False-PASS attacks

| Oracle | Plausible wrong implementation that could pass a weaker test | Frozen counter-oracle |
|---|---|---|
| O1 | run only `test_config.py` and the probe against the frozen release, then call that “real Topos/current Assay” | current run-venv owner, exact 2,923 full result, 94 coverage files, complete PASS v4 |
| O2 | derive expected lines from Assay output, or compare only PASS/FAIL | literal hand line manifest, whole normalized v4 templates, copied unmodified Topos evaluator |
| O3 | export then ordinary-add, ignore absolute links, or operate on live Topos | exact eight-link enumeration, three deletions, five retained, 965 index paths, before/after source OID/status |
| O4 | accept a forged internally consistent universal PASS or reuse stale profile bytes | wrong-root and universal-PASS whole-template breaks plus missing/stale/profile, dirt, base, and HEAD terminals |

### 3. Missing implementation-packet content

None remains. The skeleton deliberately leaves construction/process bodies as
TODO implementation work, but it freezes their signatures, inputs, outputs,
limits, comparison rules, scenario set, and CLI. The complete tracer proves a
satisfying construction exists and prevents “TODO means redesign the API.”

### 4. Scope/dependency defects

None remains. P25 may add only gate/qualification code, the one focused test,
gate wiring/config, its LOG, and documentation. It may not edit Assay runtime,
packaging, P20–P25 locked assets, or real Topos. P24 owns both current installed
wheel construction and release verification. P22/P23 own committed snapshots
and exact higher-rigor reexecution. A need to change any owner is mechanical
BLOCKED, not permission to widen P25.

The structurally invalid cross-project `../topos` frontmatter forbid was
removed: scope is rooted at the Assay project and cannot authorize that path;
the body and BLOCKED rule still state the product constraint. The handoff now
passes `nyxloom lint`.

### 5. Corrected oracle/fixture matrix

| Axis | A | B | C |
|---|---|---|---|
| source | exact Topos tree | wrong existing root decoy | changed input OID |
| index/topology | 965 forced paths, five safe links | ordinary-add omissions | retained/extra absolute link |
| product | current installed wheel | hash-installed 1.2.5 | source/alternate wheel exposure |
| profile | exact fresh bounded bytes | missing/stale bytes | oversized/symlink/type violation |
| semantics | 5/5 PASS | line-7 4/5 FAIL | excluded or comment-only 0/0 |
| Git state | clean fixed base→HEAD | preexisting dirt/base=HEAD | command-created dirt/HEAD move |
| proof | complete v4 + hand + Topos | terminal-only comparison | producer-derived/forged expected |
| environment | exact closed tester map | omitted identity/PATH | ambient passthrough |

Combined-axis fixtures required by the review include:

1. ordinary-add omissions **plus** targeted-only tests **plus** a green release
   wheel—the exact shape that hid 13 full-suite failures;
2. wrong source-root decoy **plus** an internally consistent universal-PASS
   artifact **plus** terminal-only comparison;
3. command-created clean commit **plus** a valid copied coverage profile
   **plus** current installed Assay, which must end `HEAD_CHANGED` rather than
   reuse otherwise truthful evidence;
4. comment-only 0/0 **plus** imported module consideration **plus** closed
   environment, preventing “no executable lines means no file” collapse; and
5. excluded line **plus** `allow_excluded=false` **plus** a Topos PASS,
   proving the documented capability asymmetry is neither erased nor treated
   as false common parity.

### 6. READY verdict

**READY.** The final handoff has a satisfiable real-container proof, explicit
owners and refusal states, a compiling implementation-shaped contract,
immutable independent fixtures, whole-artifact oracles, a controlled-red quick
suite, a real registered gate, exact scope, and a mechanical BLOCKED escape.
No product decision, interface, example, bound, terminal, or proof source is
left for Sonnet to invent.

## Successor dispositions

- P26 retains P23 finding F8 exactly as A-201 assigns it: thread the one
  lane-wide deadline through in-snapshot Git processes. P25 does not widen into
  that cross-runtime API work.
- Future Topos adoption must first resolve A-202's three absolute committed
  links in Topos itself and then compare its old/new gates on the same commits.
  P25's disposable patch is the witnessed prospective state, not permission
  for Assay to filter consumer source.
- P27–P31 may reuse the distinction between current-product qualification and
  a clean release smoke. They must not reuse P25's Python line oracle as a Go
  semantics oracle.
- The P25 reviewer must add one materially new combined-axis attack and must
  judge actual Git state and gate evidence, not the implementer's LOG alone.
