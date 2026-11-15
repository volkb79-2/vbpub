# The default R2 campaign

Multi-host, multi-instance R2 is the default way mutation judging runs: one
lane, several worker instances on each of the two test hosts, and shards whose
budgets derive from measured per-candidate cost instead of a hand-written
`12h`. Four pieces make that a mechanism instead of a per-tool script (they are
verbs of the `fleet` CLI, which declares `cli-extended>=0.5.0` as a wheel
dependency):

| piece | command | what it replaces |
|---|---|---|
| scope producer | `fleet scope` | a hand-prepared `--only-candidates` file regenerated ad hoc |
| sizing | `fleet sizing` | a fixed `cpus = "3"` container and an `11h`/`12h` budget pair |
| measurement recorder | `fleet measurement` | nobody ever recording what a candidate actually cost |
| merge check | `fleet merge-check` | trusting that every shard judged the same tree |

Each piece is a pure function over versioned documents, so Run-Gate, cmru and a
fleetd campaign can all call the same committed code. The fleetd CLI is the
reference caller; none of these commands contacts a host.

The documents these commands read and write are `fleet-r2-scope/1`,
`fleet-r2-scope-cursor/1`, `fleet-r2-sizing/1`, `fleet-r2-measurement/1`,
`fleet-shard-identity/1` and `fleet-shard-identity-set/1`. `fleet scope` exits
`0` for a scope, `3` for a stale cursor and a refused merge, and `4` for
`nothing-to-judge`; those non-zero exits still print their document on stdout,
so the reason is never lost. A bad argument or a side-effect refusal
(`--out`/`--write-cursor`) exits `2` with the reason on stderr and writes
nothing.

## Scope: what an R2 campaign judges

`fleet scope <lane> --plan <assay plan --json> --repo <checkout> --from-tag
<previous release tag>` selects the candidates whose start line is an **added
line** of `git diff -U0 <tag>..<commit>`. A file the release did not touch
contributes nothing. A renamed file is judged **whole**: candidate identity
folds in the repo-relative path, so every candidate in a renamed file is an
identity that has never been judged before.

An empty selection is an explicit `nothing-to-judge` result with its reason,
and `--out` refuses to write an empty candidate file for it; the CLI exits 4.
A campaign that judged nothing can therefore never look like a campaign that
passed.

The rolling baseline is a **per-tool opt-in**, declared in a committed
`r2-scope.toml` beside the project's `run-gate.toml`:

```toml
schema_version = 1

[r2]
mode = "rolling-whole-target"
slice_size = 40
cursor = ".assay/r2-scope-cursor.json"
note = "every release judges the next 40 candidates of the whole-target list"
```

With that mode each release judges the next slice of the whole-target list,
taken from a persisted `fleet-r2-scope-cursor/1` file. The cursor records the
digest and length of the list it was taken from; a list that no longer matches
is refused (`stale-cursor`, exit 3) rather than resumed at an offset that now
points at a different candidate. `--rebaseline` accepts the change explicitly
and records a new pass. A tool that commits no scope file keeps the
`changed-lines` default, so no existing lane changes behaviour.

## Sizing: how many shards and how long

```sh
fleet sizing plan.json --measurement r2-week46.measurement.json
```

The plan already resolved each host's CPU slots and overcommitted RAM budget,
so sizing reads those facts and derives the rest:

- **instances per host** from that host's budget, the fixed per-instance charge
  (judge and container) and the per-**worker** unit — one pytest worker, not
  one three-worker container;
- **shards = instances**, because each instance is one `--shard K/N` run;
- **per-instance budget** from that instance's candidate count, the per-candidate
  wall seconds and its worker count, plus the lane baseline;
- **the outer bound** = that budget plus a launch/collection allowance, so a
  lane's declared `budget` and Run-Gate's bound are one derivation instead of
  two numbers that drift.

Every number carries its `basis`: `measured` when a finished campaign's own
per-candidate records supplied it, `declared` when it is still the lane's
estimate. A sizing document therefore says out loud how much of itself is a
guess. The per-worker unit comes from `[lanes.<lane>.demand]`:

```toml
schema_version = 1

[lanes.r2.demand]
unit = "candidate"
instance_memory = "250m"
unit_memory = "120m"
unit_memory_high = "600m"
unit_seconds_ref = 95
efficient_upper_bound = 3
baseline_seconds = 400
worker_memory = "120m"
worker_cpu = 1
instance_cpu = 1
```

`worker_memory`, `worker_cpu` and `instance_cpu` are optional in the contract
so every existing demand file keeps validating; `fleet sizing` refuses a plan
whose demand does not declare them, by name.

## Measurement: stop guessing

```sh
fleet collect r2-week46
fleet measurement r2-week46
```

`fleet measurement` writes `fleet-r2-measurement/1` beside the campaign
result, from the same owner-attributed records `collect` used. Each candidate
carries its own `elapsed_seconds` and `memory_peak_bytes`; the summary keeps
medians over `killed` and `survived` candidates only, because those two
buckets are the cost of judging. A candidate the observer could not measure is
`null`, never zero: a zero would drag every median down and size the next
campaign's budgets too small. `memory_peak_bytes` is the cgprofile observer's
`resource-profile/1` number (Assay 12 removed its own sampled
`peak_rss_bytes`), so its presence depends on the observer, not on this tool.

The next campaign passes that file to `fleet sizing --measurement` and the
`basis` becomes `measured`.

## Merge check: same commit, same image, same plan

```sh
fleet merge-check shard-0.identity.json shard-1.identity.json --require-complete
```

Each shard writes one `fleet-shard-identity/1` document stating the lane,
host, full commit, tester image digest, plan digest and its `index`/`count`.
The merge check compares every shard against the first: a differing commit,
image, lane or plan is `mismatched` and the set is refused (exit 3). A
duplicated index is a disagreement about which slice a store holds — also a
refusal. A missing index is reported as `incomplete` and is refused too
(exit 3; `--require-complete` is accepted and changes nothing), because a
merge over a missing shard silently reports its candidates as absent work.

The image digest is optional in `fleet-plan-execution/1`
(`execution.image_digest`): a campaign may run without naming its tester
image, and its shards then cannot be merged. Two unknown image identities are
exactly the mixed-image merge this check exists to refuse, so it never guesses
one.

## What is not covered

- The merge check validates *identity documents*; it does not merge Assay
  stores. Strict `--merge-manifest` merging remains FD-008/B544/RG-277.
- `fleet scope` reads the candidate list from `assay plan <lane> --json`; it
  does not derive which lines Assay would mutate, so a candidate Assay would
  not plan is never invented here.
- Sizing is a derived *plan*: Run-Gate's own admission is still the runtime
  authority, and fleetd's per-host RAM budget is still not passed into the
  runner (FD-003).
- Measurement depends on the observer; a lane without cgprofile records
  elapsed seconds only and its memory median stays `null`.
- Nothing calls these commands automatically yet: the Run-Gate campaign client
  (RG-276) and the Assay supervisor (`--shard`, `--instances`) are the intended
  callers, and wiring one into the other is tracked as
  [FD-029](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-029-nothing-wires-the-default-r2-commands-into-a-workflow).
- The per-worker demand keys still live in a separate TOML file because
  Run-Gate refuses `demand` inside its own file: see
  [FD-030](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-030-the-per-worker-demand-keys-are-not-accepted-inside-run-gatetoml).
- Budgets are derived from medians and a fixed margin, not from a measured
  tail, so a heavy-tailed lane can exceed its derived budget: see
  [FD-031](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-031-sizing-budgets-use-medians-and-a-fixed-margin-not-a-measured-tail).
- `fleet scope` refuses a checkout whose HEAD is not the named commit or whose
  tracked files have uncommitted changes (untracked files are ignored), because
  the plan describes the working tree and the diff the committed one: see
  [FD-032](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-032-the-scope-producer-does-not-check-the-checkout-it-reads).
- The rolling cursor goes stale on almost every release that changes a target
  file: candidate ids fold in the source file's hash, so the whole-target list
  digest changes, `fleet scope` refuses with `stale-cursor`, and `--rebaseline`
  restarts at offset 0. Rolling mode is therefore a sampling baseline that needs
  an explicit `--rebaseline` per changed release, not a guaranteed sweep of the
  whole target: see
  [FD-033](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-033-the-rolling-cursor-goes-stale-on-every-release-that-changes-a-target-file).
