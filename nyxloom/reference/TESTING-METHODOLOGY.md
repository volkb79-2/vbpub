# nyxloom testing methodology — mechanical evidence, limits, and adoption

> **Canonical guidance.** This is a risk-based catalogue of testing methods a
> nyxloom consumer may adopt. It does not widen nyxloom's gate contract: a
> project needs only one isolated, fail-closed command. The methods below are
> complementary evidence, not a universal checklist.

## The evidence model

No test method proves a program correct. Each rejects a different kind of
plausible bad change, so record the evidence actually obtained rather than
reducing quality to one coverage percentage.

| Method / action | What it establishes | Main target | It does **not** establish | Normal placement |
|---|---|---|---|---|
| Isolated, fail-closed gate | The declared command ran at the intended commit and its real exit propagates. | Cockpit-only greens, masked exits, wrong checkout. | Useful behavioral tests. | Every merge. |
| Build, import, schema, static checks | The program parses/builds and meets stated mechanical constraints. | Syntax, imports, many type/API/config errors. | Runtime behavior or valid requirements. | Every merge. |
| Deterministic unit/component tests | Named behavior holds for controlled inputs/dependencies. | Local regressions and edge cases. | Integration wiring or completeness. | Every merge. |
| API, schema, and message-envelope contracts | Public requests/responses and inter-service messages validate against the declared contract. | Client/server, queue, and version drift. | A complete user workflow. | Every merge for stable contracts. |
| Real fixture / contract integration | A real public boundary works with controlled infrastructure or a fixture repo. | Mocking the component claimed to be tested; protocol/config drift. | All deployment/environment failure. | Critical paths every merge; broader nightly. |
| Independent-channel round trip | A UI/API mutation is confirmed through a second source of truth (DB read model, API, mock backend, or mail sink). | UI-only smoke/feedback tests that never prove persisted effects. | Every cross-service failure mode. | Security and data-changing workflows. |
| Stateful journey | A resettable, serialized multi-step operator/user workflow reaches each expected state. | Bugs visible only after lifecycle history crosses several boundaries. | Isolated test independence; run it only in its own lane. | E2E/release lane. |
| Global line **and branch** coverage | The declared source tree was exercised. | Historic blind spots and accidental regressions. | Meaningful assertions or right logic. | Every merge when affordable. |
| Decision-table, boundary-value, and equivalence-partition tests | Declared input classes, edges, and rule combinations have explicit examples. | Off-by-one, missing validation class, and contradictory business rules. | Unknown classes or state history outside the table. | Validators, policy, configuration, finance. |
| MC/DC or condition coverage | Each atomic condition is shown to independently affect a decision. | Masked conditions in dense safety/security decisions. | End-to-end correctness; MC/DC is much costlier than branch coverage. | Selected high-assurance decision logic. |
| Changed-line coverage | Every changed executable line ran; changed exclusions fail. | Untested new guard/branch. | Assertion strength. | Every implementation gate. |
| Serial/parallel coverage parity | Parallel and serial collection credit the same executed lines. | Dropped worker/fork coverage and coverage plumbing lies. | Ordinary flakiness. | Gate introduction/runner change; periodically. |
| `nyxloom gate verify` canaries | Good code passes; import-break and uncovered-line canaries fail. | A green but nondiscriminating/LAUNDERS gate. | Correct product behavior. | Adoption, scheduled, and after gate transport/image changes. |
| Regression fail-before/pass-after | The new regression test detects the reported defect. | A test that only documents the bug. | Other unconsidered defects. | Every bug fix. |
| Changed-line mutation | Supported mutations of changed logic are killed. | Hollow assertions on new decision logic. | Correctness outside the mutator catalogue; equivalent mutants. | High-risk pre-merge or review-gated. |
| Whole-project mutation | A recorded population of legacy mutations is killed/survives. | Historic weak assertions and decision logic. | Specification correctness; score is not quality %. | Scheduled audit/release qualification. |
| Property/state-machine testing | Invariants hold over generated values/sequences. | Combinatorial and state-transition cases. | That chosen invariants reflect intent. | Pure/stateful cores. |
| Model-based testing | An implementation conforms to an executable abstract model over generated action sequences. | Lifecycle/state-machine omissions where examples miss transitions. | That the abstract model is itself correct. | Stateful protocols, workflows, and storage engines. |
| Combinatorial interaction testing | Every selected pairwise/t-wise configuration interaction is exercised. | Configuration matrices too large for exhaustive testing. | Higher-order interactions outside the chosen strength. | Configuration, feature flags, deployment matrices. |
| Coverage-guided fuzzing | Adversarial inputs do not crash, hang, or violate a checked invariant. | Parsers, serialization, input validation, DoS. | Product semantics without an oracle. | Continuous/off-host. |
| Differential/metamorphic testing | Implementations or stated transformations agree. | Output errors with no simple expected-value oracle. | Both sides sharing one wrong assumption. | Targeted high-risk domains. |
| Fault injection / chaos / recovery drill | The system restores stated invariants after dependency, process, network, clock, or storage faults. | Happy-path-only distributed tests and untested recovery. | Every real incident sequence or capacity limit. | Isolated integration/nightly; production only under explicit safety policy. |
| Schedule exploration / concurrency testing | A bounded set of interleavings preserves concurrency invariants. | Rare ordering, race, and cancellation defects. | All possible schedules without a formal model. | Concurrent stateful cores and queues. |
| Symbolic/concolic execution and model checking | Paths or finite-state invariants are explored/proved within stated bounds. | Deep boundary combinations and protocol-state errors. | Unbounded program correctness or valid requirements. | Small critical algorithms/protocols. |
| Formal specification/proof | A mathematical model or implementation satisfies stated theorems/invariants. | Classes of design/implementation errors inside the formal boundary. | Correct assumptions, environment, or product desirability. | Cryptography, safety/security protocols, high-assurance core. |
| Fixed shuffled order, repetition, stress | Tests remain stable across schedules/seeds. | Order dependence, races, global leaks, clock/network coupling. | Production performance under all loads. | Scheduled; after concurrency changes. |
| Deterministic simulation / virtual time | Long histories, retries, clocks, and faults can be explored reproducibly without wall-clock sleeps. | Slow/flaky timing tests and rare lifecycle sequences. | Fidelity beyond the simulator's explicit model. | Schedulers, leases, distributed protocols. |
| Soak, load, and resource-leak testing | Stated throughput/latency bounds hold and resources remain bounded over time. | Gradual leaks, queue growth, contention, and saturation collapse. | Functional correctness outside checked invariants. | Dedicated performance host; release/scheduled. |
| Upgrade, migration, rollback, and backup-restore drills | Persisted state and service compatibility survive supported transitions and recovery procedures. | Green fresh installs that fail on real historic state. | Every unsupported version path or disaster. | Release qualification and scheduled recovery drills. |
| Accessibility, performance, and observability checks | Stated UX budgets/accessibility rules and emitted traces, logs, metrics, or correlations remain present. | Silent nonfunctional and diagnosability regressions. | General correctness or production-scale capacity. | Critical checks per merge; load/soak scheduled. |
| Visual regression / golden-master comparison | Rendered UI, generated artifact, or legacy-compatible output matches an approved baseline with reviewed differences. | Accidental presentation/serialization compatibility drift. | Whether the baseline was desirable; avoid blind snapshot approval. | Stable presentation and compatibility surfaces. |
| Dependency/SBOM/vulnerability/license scan | Resolved dependencies meet policy at scan-database revision. | Known vulnerable/prohibited/untracked inputs. | Unknown vulnerabilities or app flaws. | Dependency changes and scheduled refresh. |
| SAST/secret/IaC/container-policy scan | Source and deployable artifacts avoid catalogued dangerous patterns and policy violations. | Common injection, credential, permission, and deployment errors. | Exploitability or safe runtime behavior. | Every merge/image build. |
| DAST/protocol and adversarial security testing | A running isolated target rejects tested attacks at its real boundary. | Wiring-dependent auth, injection, traversal, and protocol flaws. | Complete security; requires threat-led test selection. | Security lane and release qualification. |
| Reproducible-build/provenance verification | Rebuilding declared inputs yields the expected artifact and traceable dependency/toolchain identity. | Unrecorded inputs, build drift, and some supply-chain substitution. | Source correctness or compromise inside trusted inputs. | Release artifacts and toolchain changes. |

## Scope, rigor, and lanes

The catalogue above answers *which method*. It does not answer *how much of the
system each method runs against*, and the two are independent: a changed-line
coverage floor applies equally to an in-process unit run and to a full
multi-service deployment, at wildly different cost. Conflating them produces a
common failure — one project-wide "the gate" string carrying an unstated scope,
where raising rigor silently raises deployment cost, and where a narrowed scope
becomes invisible because nothing names it.

Treat them as two axes and compose them deliberately.

### Axis 1 — scope: how much of the system is under test

| | Scope | What is real | What is faked | Needs deployed topology |
|---|---|---|---|---|
| **S0** | static | source text only | everything | no |
| **S1** | unit | one module | all I/O, all peers | no |
| **S2** | component | one service's public surface | its dependencies (in-process client, fake cache/broker) | no |
| **S3** | stack | one deployable stack, real containers | other stacks | yes — one isolated instance |
| **S4** | landscape | multiple stacks, cross-service flows, UI | nothing | yes — a full landscape |

Scope is where the **environment tool's** authority begins. S0–S2 need only a
dependency closure; S3 and S4 need real deployed topology, so *which containers,
which network, which image* are questions the project's environment tool answers
(for the worked example below: `ciu`), not questions nyxloom answers. This does
not widen nyxloom's contract — it stays one isolated, fail-closed command per
declared gate. It only says that for S3/S4 that command is normally a thin call
into the environment tool rather than a bare test-runner invocation.

### Axis 2 — rigor: how hard the result is being judged

| | Rigor | Claim earned | Catalogue rows above |
|---|---|---|---|
| **R0** | pass/fail | the declared command ran at the intended commit and passed | isolated fail-closed gate; deterministic unit/component tests |
| **R1** | reach | the changed (or declared) lines were *executed* | changed-line coverage; global line **and branch** coverage |
| **R2** | assertion strength | those lines are *asserted*, not merely executed | changed-line mutation; whole-project mutation; fail-before/pass-after |
| **R3** | gate integrity | the gate demonstrably *rejects* known-bad code | `nyxloom gate verify` canaries; serial/parallel coverage parity |

R3 is a claim about the *instrument*, not the product. It is the only axis whose
failure invalidates every other result already recorded, which is why it belongs
on a cadence rather than inside a per-package run.

### A lane is a cell selection

A **lane** is a named, budgeted composition: *a scope selection × a rigor
selection × a place to run × a wall-clock budget*. Naming lanes instead of
gates makes three things explicit that a single gate string hides: what was
**not** run, what the result costs, and which lane a given red belongs to.

Four design rules, each of which survives contact with a real project:

1. **Full scope, narrow rigor.** Run *all* cheap tests, but demand R1/R2 only on
   changed lines. This keeps the per-package signal affordable and, just as
   importantly, stops a diff-coverage percentage from being misread as a
   project-health metric — it is a property of one diff, not of the tree.
2. **Impact-based lane selection.** Map changed paths → owning stacks → run only
   those stacks' S3 lanes, plus the cheap lane unconditionally. This requires
   stacks to declare their own tests, which is the structural precondition, not
   an optimization detail.
3. **Artifact provenance is a precondition, not a lane.** Any S3/S4 run must
   refuse to start unless the running image's revision matches the commit under
   test. Without it, a live result silently describes an unknown artifact — the
   most expensive lane produces the least trustworthy evidence, and no amount of
   added rigor detects it.
4. **R3 runs on a schedule, not per package.** Canary and gate-integrity probes
   are expensive and slow-changing. Per-package they are waste; after a gate,
   image, or transport change they are mandatory.

### Worked example — dstdns, a multi-stack `ciu` consumer

dstdns deploys ~5 application stacks (`controller`, `worker-io`, `worker-db`,
`webapp-server`, `webapp-ui-react`) plus infrastructure, and supports
per-worktree isolated instances. Its gating runner is a `test-runner` container
built `FROM` the app base image, so its dependency closure equals the app
runtime; greens from the interactive cockpit are explicitly not a ship signal.

| Lane | Scope | Rigor | Runs in | Budget |
|---|---|---|---|---|
| `quick` | S0+S1 | R0 | that instance's `test-runner` | < 60 s |
| `package` | S0–S2 | R1 + R2, changed lines only | that instance's `test-runner` | 2–5 min |
| `stack` | S3, touched stacks only | R0 + R1 | that stack's own instance | 5–10 min |
| `release` | S4 | R0 (+ provenance precondition) | the main landscape | 25–45 min |
| `audit` | meta | R3 | scratch worktree | scheduled / after gate change |

Two properties make this fit a multi-stack project specifically. **S3 is exactly
the instance boundary** — "test `worker-io`" resolves to "bring up `worker-io`'s
stack in my own instance, run its declared lane", which is a composition of
capabilities the environment tool already has, not a new concept. And because
each worktree can boot its own `test-runner`, lanes stop contending for a shared
runner, which removes the scheduling discipline that otherwise has to be
enforced by convention.

**Current honest state (2026-08-06), recorded because a lane table implies
capability it does not yet have.** dstdns's declared gate is narrowed to
`pytest tests/unit -q` (~800 passed, 34 s). Full collection is red with 255
pre-existing failures attributed to collection pollution — verified
byte-identical across an unrelated merge, so not a regression — with package
`dstdns-P38` (pollution diagnosis) as the named exit criterion for widening.
**S2 is therefore unreachable today**, the `package` lane runs at S1, and the
`stack` and `audit` lanes are designed but undeclared. A lane table is a target
architecture; the gap between it and the declared gates is the backlog, and
stating that gap is what keeps the table from reading as evidence.

## Mutation testing

### Current nyxloom support

`nyxloom.mutation_gate` is a **project-invoked** Python tool. It mutates changed
comparisons, boolean logic, boolean constants, and direct falsy returns; it then
runs the project-supplied test command. A surviving mutant makes the command
fail. On a clean tree every mutant is tested in its own disposable git worktree.

The project supplies source root, dependency closure, test command, and isolated
container in a `[gates.<name>]` argv. nyxloom executes that argv at the intended
commit. A project may define `phase="mutation"` and enable
`policy.mutation_gate=true`, making it an opt-in pre-publication check for
automatic merges. It must never run in the cockpit.

The current CLI intentionally targets **changed lines only**. That keeps a
pre-merge signal affordable: a change generally has few decision operators, and
`-x` stops each killed mutant at its first failing test.

### Whole-project mutation

Yes in principle, but it needs an explicit whole-tree or sampled-target mode; the
current tool has no `--all` flag. It must be separately budgeted rather than
quietly replacing changed-line mutation.

Approximate cost:

```
wall time ≈ (mutants × median selected-test time) / effective parallelism
```

CIU's full isolated suite is currently about 10 seconds. Thus 1,000 viable
mutants represent about 2.8 CPU-hours before overhead. Worktree setup, startup,
contention, and survivors that run the full selected suite mean real wall time is
typically tens of minutes to hours. Measure a representative module first; report
target count, selection method, wall-time budget, and survivors. Scores without
those facts are not comparable.

Do **not** nest `pytest -n auto` inside aggressively parallel mutant jobs. The
recommended shape is serial `pytest -x` per mutant and a bounded number of
isolated mutant jobs. The current mutator fans jobs out automatically, so add an
explicit `--jobs` cap before enabling it on a large project and run it in the
low-priority gate cgroup.

### Synchronous versus retroactive runs

| Mode | Merge policy | Accurate claim | Best use |
|---|---|---|---|
| Changed-line mutation, synchronous | Survivor blocks merge. | Selected tests detect supported mutations of new logic. | Frozen-core, security-sensitive, high-blast-radius work. |
| Changed-line mutation, asynchronous | Normal trustworthy gate may merge; red result opens repair/escalation. | A later audit found a weak assertion. | Medium risk when merge latency matters. |
| Whole-project sampled, asynchronous | Never called pre-merge certification. | A stated legacy-health sample passed/failed. | Nightly/weekly health. |
| Whole-project exhaustive | Release policy decides promotion. | The declared mutation catalogue was exhausted at a revision. | Small projects, release candidates, risky subsystems. |

Retroactive work must be commit-addressed: queue `(commit SHA, source root, image
digest, test command, mutator version, budget)` and retain artifacts and survivor
reproducers. The dashboard must say **pending**, **passed**, **failed**, or
**inconclusive**. A red audit should create a durable finding; auto-reverting an
already-published commit is a product policy decision because some survivors are
equivalent or low-value mutants.

### Remote execution

Mutation and fuzzing are good off-host workloads. A remote worker must check out
the immutable commit, use a pinned test-image digest, enforce CPU/memory/I/O/
wall-time/job budgets, and return the actual exit status separately from logs plus
structured artifacts. Lost transport, timeout, missing artifact, or stale result
must fail closed. A job nyxloom can wait for and verify may block a merge; a
fire-and-forget job is an audit, not a gate.

`tools/remote-mutation-audit-host.sh` is the reference host launcher and
`tools/remote-mutation-audit.example.toml` is its consumer-manifest template.
Consumer manifests name source roots, a serial per-mutant test argv, and optional
trusted infrastructure hooks; CIU and Topos provide live examples. The launcher
keeps reports outside the disposable checkout and the worker emits `events.jsonl`,
per-mutant stdout/stderr, and `summary.json` even when a baseline, mutant, or
teardown fails.

The launcher records the exact commit, manifest SHA-256, and built tester image
ID in `host.json`, and the worker repeats those values in every summary. Large
audits can be split deterministically with `--shard-index/--shard-count`; each
mutant keeps its stable whole-tree ordinal. `--start-at` resumes from a known
ordinal and `--max-mutants` bounds a pilot. A shard or resumed suffix is evidence
only for that declared selection, never an implicit whole-project pass. Selecting
zero supported mutants is `INCONCLUSIVE_NO_MUTANTS`, not green. Reports include
baseline, per-mutant, and total durations plus the non-secret test argv/source
configuration needed for cost comparisons.

For replayable evidence, pass a content-addressed tester reference through
`--tester-image registry/name@sha256:...`. Without it, the launcher builds the
audited commit's Dockerfile and records the resulting local image ID; this is
useful audit evidence, but a mutable base tag may prevent an identical future
rebuild and must not be presented as reproducible.

Docker-socket access is separate from lifecycle authorization:
`--allow-infra` permits trusted argv hooks, while `--allow-docker-socket` is an
additional explicit capability. The default launcher grants neither a Docker
socket nor a privileged container. A cgroup parent is optional and explicit via
`--cgroup-parent` or `MUTATION_AUDIT_CGROUP_PARENT`.

For stateful test infrastructure, prefer a unique Compose project and disposable
named volumes per audit. When cold setup is material, a trusted hook may create a
copy-on-write volume/filesystem snapshot after seeding and restore it before and
after each mutant; it must also destroy that snapshot in `finally`. Docker/CRIU
checkpoint-restore of a live container is **not** a default: open sockets,
external services, kernel/version coupling, mounted volumes, and secret state are
not reliably captured. Use it only after a project-specific reproducibility
proof; immutable images plus seeded volume snapshots are the portable baseline.

### Host ZFS lifecycle

The reference supports ZFS without exposing host authority to the tester. Add an
enabled `[zfs]` table to the consumer manifest with a dedicated `dataset` and its
absolute `container_mount`. On the host, opt that dataset in once:

```console
zfs set nyxloom:mutation-audit=on tank/nyxloom-audits/my-project
```

Then launch directly on that Docker/ZFS host with both lifecycle consent and an
operator-owned allowlist:

```console
./tools/remote-mutation-audit.sh --allow-infra \
  --zfs-allowed-prefix tank/nyxloom-audits --zfs-sudo
```

Omit `--zfs-sudo` when the audit account has narrowly delegated ZFS permissions.
The manifest cannot choose the allowlist, alter the fixed safety property, or
provide an arbitrary privilege command. The dataset must be a child of the
authorized prefix, must not be `/`, and must carry the opt-in property. The host
broker exposes only snapshot-create, rollback, destroy, and status over a Unix
socket authenticated by a per-run capability token; the tester never receives
`/dev/zfs`, host root, or `sudo`.

ZFS mode requires `jobs=1`. The worker runs `[infra].reset` to quiesce services,
rolls back the dedicated dataset, then runs `[infra].snapshot_restore` to resume
them, before and after each mutant. On normal completion it restores the seed
snapshot and destroys it. If the process is killed before safe quiescence, the
snapshot is deliberately retained as a recovery point and named in
`zfs-events.jsonl`; an operator must quiesce, restore, and destroy it. Never point
this mechanism at production data or a dataset shared with another audit.

## Do tests test the right thing?

Not fully mechanically. The strongest practical approach is independent,
layered challenge rather than trusting the test author.

| Challenge | What it reveals | Intelligence still required |
|---|---|---|
| Gate/coverage canaries | Whether the gate rejects known-bad code and uncovered changes. | Pick a source subtree representing the shipped boundary. |
| Fail-before/pass-after | Whether a regression test detects its defect. | Reproduce and characterize the actual incident, not a proxy. |
| Mutation | Assertions that do not distinguish nearby wrong logic. | Classify survivors; turn real gaps into behavioral tests. |
| Property/stateful tests | Invariant violations over broad input/sequence space. | Invent the right invariant/model and legal transitions. |
| Differential/metamorphic tests | Disagreement with an independent implementation/relation. | Establish comparator/relation authority. |
| Real fixture integration | Mock-heavy tests that never exercise the shipped interface. | Select representative users, configurations, versions, and failures. |
| Adversarial review | Spec contradictions, missing threats, wrong abstraction, plausible omitted cases. | Read intent and attack the reasoning; a runner cannot infer intent. |
| Privacy-safe production replay | Gaps between assumed and actual workload shape. | Define safe capture/redaction and turn failures into deterministic fixtures. |

The short rule: **coverage proves reach; mutation probes assertion strength;
properties/fuzzing explore input space; integration proves a boundary; review
judges meaning.** A 100% suite can still test the wrong requirement, and a high
mutation score can preserve a wrong specification.

## Adoption ladder

1. Establish an isolated, fail-closed, commit-addressed gate.
2. Add branch-aware global coverage and changed-line coverage; prove
   serial/xdist parity.
3. Run `nyxloom gate verify` at adoption and on a cadence; run `nyxloom doctor`
   after image, Docker transport, or cgroup changes.
4. Add API/message contracts, real fixtures, and independent-channel round trips
   on critical public boundaries; use resettable serialized journeys for
   lifecycle workflows. Add fail-before/pass-after evidence for bugs.
5. Pilot changed-line mutation on one small risk-bearing module. Add bounded job
   control before enabling it broadly.
6. Add properties/model-based state machines where crisp invariants exist;
   use pairwise/t-wise matrices for configuration space, fault injection for
   recovery contracts, and fuzzing for untrusted structured input. Promote
   minimized findings to regressions.
7. Run sampled whole-project mutation, shuffled-order/repetition, dependency
   scans, and fuzzing asynchronously on a remote/batch worker. Promote only
   measured, valuable checks into release requirements.

## Suggested nyxloom enhancements

- Add `mutation_gate --jobs N`, explicit whole-tree/sample targets, and distinct
  budget-exhausted reporting.
- Add a mutation canary to `nyxloom gate verify`; today `mutation` is
  declared-only, unlike canary-proven tests and changed-line coverage.
- Persist asynchronous-validation state keyed by commit and image/gate digest,
  with finding/escalation policy.
- Define a remote-runner result-integrity protocol; never treat a vendor status
  badge alone as a gate verdict.
