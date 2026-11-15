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
`fleet-r2-scope-cursor/2` (`fleet-r2-scope-cursor/1` is refused with its
migration), `fleet-r2-sizing/1`, `fleet-r2-measurement/1`,
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

With that mode each release judges, within ONE `slice_size` budget, first the
candidates of files that changed since they were last judged and then the next
slice of a persisted sweep over the whole target, so successive releases
cover the target without re-judging the whole list at once. The sweep state is a
`fleet-r2-scope-cursor/2` file, and it is keyed by **file content**: each path
records the git blob id of the content its marks belong to, the planned
candidate count, the candidate keys judged at that content, and the pass in
which each path was last judged. The candidate key is a digest of
`(repo-relative path, mutation operator, ordinal within that path's operator
group)` — deliberately NOT Assay's candidate id, which folds in the source file's
SHA-256 and therefore changes on every edit to the file.

The rules, which are what make rolling a sweep instead of a sample:

- A file whose content changed since its mark is **due again immediately**, and
  judged whole. A site identity moves when anything before it in the file moves,
  so a changed file's whole candidate set is re-judged; that is a superset of
  the changed-lines scope, and like a rename it may re-judge unchanged lines.
- A file whose content did not change keeps its marks. An edit to *one* file does
  not restart the sweep: the other files stay judged and the cursor is not stale.
- A mark that cannot be confirmed against the current plan — a different planned
  candidate count for the path, or a judged key the plan no longer contains (an
  operator-selection change looks like this) — makes that path due again and is
  reported as `changed`; it never marks an unjudged candidate as judged.
- A path with no planned candidate any more is **retired**, listed in `retired`
  with its key count and the pass that last judged it. Nothing is dropped
  silently.
- The slice is a prefix of the deterministic order: files whose content changed
  first (least recently judged first), then never-judged and
  not-yet-judged-in-this-content candidates in path/line order. `slice_size` is
  the budget, and changing it re-slices the same sweep rather than refusing.
- A finished sweep does not idle and is not reported as `nothing-to-judge`:
  the next release starts a new pass, least recently judged first, and the
  document says `"new_pass": true`. A mark counts only for the pass it was
  written in, so every later pass sweeps the whole target again (a path judged
  in an earlier pass is due again, least recently judged first).
- The changed files and the sweep share ONE budget, `slice_size`. Rolling is not
  "all changed candidates plus a slice": a release that changes more candidates
  than `slice_size` judges only the first `slice_size` of them (least recently
  judged first), and the rest of a half-judged changed file continues in path
  order with the other pending candidates, not first. A target whose files are
  edited every release by more than `slice_size` candidates can therefore starve
  the never-judged remainder. Size `slice_size` above the expected per-release
  churn, or use `changed-lines` for a release gate and `rolling-whole-target` for
  the background sweep.

The cursor is versioned and digest-protected (`state_digest` covers the whole
document), so a hand-edited cursor is refused rather than trusted, and a cursor
written for another lane is refused (`stale-cursor`, exit 3). A cursor from
`fleet-r2-scope-cursor/1` is refused with its migration: `--rebaseline` is that
migration, discarding the marks and starting one new pass from the current
content. A tool that commits no scope file keeps the `changed-lines` default,
so no existing lane changes behaviour.

The only inputs a rolling release cannot derive are refused rather than guessed:
a plan row whose path the judged commit does not describe (an untracked target
file, or a plan produced in another checkout) exits 2, because the sweep keys
that candidate to content it cannot read. The cursor path declared in a
committed `r2-scope.toml` may be absent on the first release — that is
configuration, not a typo; a cursor path typed as `--cursor` may not, because a
typo there would silently restart the sweep on every release.

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
- Rolling mode sweeps by **file content**, so its coverage guarantee is
  per-content, not per-revision: the whole target is judged within
  `ceil(candidates / slice_size)` releases for a target nobody edits, and every
  edit re-judges the edited file whole (a superset of its changed lines). What
  it does NOT do: pick up another checkout's plan, key a candidate whose file
  the commit does not describe, or keep a mark across an edit of that file's
  own content. Those are [rolled into the mode by FD-033](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-033-the-rolling-cursor-goes-stale-on-every-release-that-changes-a-target-file), with the remaining
  limits filed as
  [FD-037](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-037-rolling-keys-a-candidate-by-its-files-content-so-a-partial-edit-re-judges-the-whole-file):
  a release that edits part of a large file re-judges that whole file.
