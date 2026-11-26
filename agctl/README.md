# agctl — agent control

`agctl` gives a controller the estate picture in one read-only command: which package is in
which state, which branch and head a worktree holds, whether that head is merged into
`origin/main`, which runs are live, which gate slots are held and which recorded gate result
belongs to which head — each field naming the source it came from and that source's age.

**Status: slice 1 is being built.** `agctl estate` reads git, the legacy flat run records, the
remote-gate results and gate lock files, each peer's `STATE.md` package lines, the handover's
merge-queue line and the handover ages; the routes, `run`, `status`/`check`/`limits`, `plan.yaml`
and the handover verbs are later packages (backlog AGC-4 and AGC-6). `agctl estate --header`
prints the compact block DESIGN section 8 item 1d describes. Design:
[`docs/design/agctl/DESIGN.md`](../../docs/design/agctl/DESIGN.md) sections 2, 4a and 8 item 1d.

## Install

```bash
python3 -m pip install --no-index --find-links . agctl-*.whl cli_extended-*.whl
```

`cli-extended` is a real wheel dependency (never vendored). Until it ships inside
`tester-unified`, the gate and any source-tree use put it on `PYTHONPATH`:
`PYTHONPATH=src:../libraries/cli-extended/src`.

## Use

```bash
agctl estate             # human rows, one per package, with field provenance
agctl estate --json      # the same rows as agctl-estate/1 JSON
agctl estate --header    # the compact injected block (merge queue, leases, live runs, ages)
```

### Rows: what the key is, and what a row is not

One row per **package key**, and the key is derived per input because the inputs' own keys
differ:

- **A branch or a run label** loses only what identifies a *launch*: the generated stamp
  (`-20261125_031456`, `-20261125T042108`) and the yield-chain suffix (`-y<N>`). Everything else
  stays, because everything else is what tells two packages apart:
  `core-ciu-r2fix`, `core-cmru-ki208` and `core-thrashguard-design` are three rows, never one
  `core` row.
- **A run joins the row of the branch whose worktree it ran in** when its `.wt` record names
  that path — a path is a fact, not a convention. A run whose worktree is gone falls back to
  the name convention above, and **still gets a row**: a package with runs and no worktree
  reports `missing` git cells, not silence. A run is reported whether or not its branch exists.
- **A gate result joins a row only on that row's head**: `meta.json` names a project
  *directory* (`cmru`, `agctl`), never a package, so a project-name match would print a
  different branch's verdict as this row's. The result's commit id must equal the row's head,
  or be a prefix of at least 7 characters. Anything else is `unknown`, and the newest result
  for the same project directory is *named in the `detail`* rather than printed as the value.
- **`STATE.md` prose joins on the topic** (`plat`, `core`, `edge` — the first stage of the key),
  or on an exact package id when one exists. A `STATE.md` line names a *designator*
  (`P-core-08`), which a branch slug cannot carry, so the designator gets its own cell
  (`plan_id`) and the join itself stays `unverified` with its file and line.

`--header` is deterministic: fixed section order, fixed age buckets (`<1h`, `1-6h`, `6-24h`,
`>24h`), a **bucketed** live-context figure (`<50k`, `50-150k`, `150-250k`, `>250k`) and no
timestamp that moves between two calls, so an unchanged estate prints byte-identical text even
while a writer updates its `.live` file. Every line is bounded where it is composed (8 items
plus `+N more`), so the block fits; the 4096-byte cap is a backstop and `[truncated]` is never
reached on a real estate. `--header` and `--json` are refused together with exit 2: one
question, one shape of answer.

## Sources, and what is unverified

| Input | Where it comes from | State |
|---|---|---|
| worktrees, branches, heads | `git worktree list --porcelain` (`--no-optional-locks`) | read |
| merged into `origin/main` | `git branch --merged origin/main --format=%(refname)` | read |
| repository root | `$CONTROLLER_REPO` when set, else the caller's cwd | read; `CONTROLLER_REPO` that names no checkout is a stated error, and the **controller home is never asked** |
| run records | `$CONTROLLER_RUNS_DIR`, `$CONTROLLER_HOME/runs`, `~/c5-runs`, `[runs].legacy_dirs` in `agctl.toml` | read: `.done` outcome, `.live` context, `.wt`/`.route`/`.thread`, `.last.txt` READY/YIELD position, `<run>-y<N>` chains, `.lock` |
| registry records | `<runs>/<id>/run.json` | read when present, parseable, schema `agctl-run/1` and id == its directory; ignored otherwise |
| run liveness | the run's `.lock` file, matched by device **and** inode in `/proc/locks` | read: `active` (held), `DIED` (free and no `.done`), `undecided` (no lock file at all) |
| gate slots | `$CONTROLLER_HOME/remote-gate-locks/*.admit`, `*.slot<N>` | read: held/free from `/proc/locks` by device **and** inode, `unknown` when that cannot be decided |
| gate results | `$CONTROLLER_REMOTE_OUT`, else `$CONTROLLER_HOME/remote-gates/<run>/` | read: `lane.log`'s own verdict line for this lane, else `result.json.verdict`, else `unknown` |
| merge queue | the last `MERGE QUEUE` line of the current `.controller-state/HANDOFF.md` | **unverified**: prose, quoted as the header's `MERGE QUEUE` line; absent means the unmerged worktree branches and heads are used instead (`MERGE`) |
| plan and owner | each `.controller-state/<name>/STATE.md` line naming `P-<topic>-<designator>` | **unverified**: prose, reported with its file and line; the designator is its own cell |
| handover age | `.controller-state/HANDOFF.md` and each peer's `HANDOFF.md` mtime | read; the age is printed as a bucket |

**A source that cannot be listed costs its own cells, never the others.** An unreadable runs,
lock, gate or state directory is reported with its reason (`estate: gate slots UNKNOWN (…)`,
a `unreadable run directory: …` in the runs cell, `sources.*_detail` in JSON); an unresolvable
git repository leaves the git cells `unknown` and prints the run, gate and peer rows anyway.

Two things the output never does: it never invents a value for a field it could not read (it
prints `unknown` or `missing` with the reason), and it never guesses an age it never measured.
A malformed `agctl.toml` is refused with exit 2 rather than silently ignored, because a
`[runs].legacy_dirs` the caller set is a fact worth failing for.

### Limits a reader should know

- **`STATE.md` is prose.** A package line is quoted as `unverified` with its file and line
  number; it is never read as a machine state. `plan.yaml` (DESIGN section 3's machine source)
  is not read yet — AGC-4(a).
- **The topic join is a convention, not a derivation.** A branch carries a topic and a slug;
  `STATE.md` names a designator. Nothing derives one from the other, so a row joined on its
  topic reports `owner`/`plan` from prose that may be about a *sibling* package of the same
  topic — which is why the designator has its own cell and the join says `unverified`.
- **Gate results carry `project_dir`, not a package id.** A result whose commit id is not a
  row's head is `unknown` for that row, and the colliding result is named in the `detail`.
- **`/proc/locks` is a snapshot of this machine**, and its `pid` is whatever namespace it
  reports (this devcontainer prints host pids). Locks held in another devcontainer or on a
  host are not visible here, so a slot whose file exists but whose entry cannot be found reads
  `free` *on this machine*; and the printed pid may not be signallable from here. Both the
  POSIX and the `flock` entry kinds mark a held lease.
- **A lock file that cannot be stat'ed, or a `/proc/locks` that cannot be read**, is `unknown`,
  never `free`.
- **A peer is a directory holding `STATE.md` or `HANDOFF.md`.** Other directories under the
  state home (`archive`, `hooks`, `runs`, `handovers`, `e021`, `scratch-*`) are not controllers
  and are not printed as peers.
- **DIED is derived, "undecided" is not "alive".** A run with no `.lock` file at all cannot be
  decided from `/proc/locks`, so it is `undecided` and excluded from `LIVE`; the header states
  how many runs it excluded and why (`(excluded: 2 died, 1 undecided)`).
- **The cost is one pass per call**: every record set opens ~4 small files and every gate
  directory streams its `lane.log` for the verdict line. That is fine per invocation, but the
  header is meant to run on every tool call through the hook; DESIGN section 8 wires it in
  a later package, where a cache is worth adding.

## Safety contract

Read-only by construction: no file is written, no lock is taken (a probe `flock` could make a
real gate admission or launcher see "busy"; held/free is decided from `/proc/locks`), no host
and no network is contacted, and the only subprocess is `git` with `--no-optional-locks` and an
allow-listed environment (never the caller's whole one: an exported `GIT_DIR` would make
`worktree list` describe another repository).

Every read is bounded: record files at 4 KB, `STATE.md` at 256 KB, and the two files whose *last*
matching line is the answer (`last.txt`'s READY/YIELD position, `lane.log`'s verdict,
`HANDOFF.md`'s MERGE QUEUE line) are scanned in a streaming pass rather than read from a tail
window — a window can hold only a suffix of the file, and "no line in the window" is not "no
line in the file". A source too large to read whole is reported as `unknown` rather than read
as a prefix. Run streams, prompts, credentials and session transcripts are never read or
echoed: only the named record files and only their first lines or their READY/YIELD/verdict
tails.

## Development

```bash
cd agctl && run-gate tests     # tester-unified, pytest, 100 % line+branch coverage floor
```
