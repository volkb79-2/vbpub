# agctl — agent control

`agctl` gives a controller the estate picture in one read-only command: which package is in
which state, which branch and head a worktree holds, whether that head is merged into
`origin/main`, which runs are live and which gate slots are held — each field naming the
source it came from and that source's age.

**Status: slice 1 is being built.** `agctl estate` reads git, the legacy flat run records, the
remote-gate results and gate lock files, and each peer's `STATE.md` package lines; the routes,
`run`, `status`/`check`/`limits` and handover verbs are later packages (backlog AGC-4).
`agctl estate --header` prints the compact block DESIGN section 8 item 1d describes. Design:
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
agctl estate --header    # the compact injected block (merge candidates, leases, live runs, ages)
```

### The join key, and why the rows look the way they do

One row per **package key**, derived from the branch name (the DESIGN 4a convention): the
generated stamp is dropped and the numeric topic id kept, so
`plat-22c-resumebusy-20261125_031456` and `plat-22c-pi` are the same package `plat-22c`, while
`plat-19m-acceptkit-...` is not. Two branches of one key share a row; every cell built on the
derivation says `derived via topic_key` in its `source`. Run labels follow the same convention,
and `agctl estate` reports a run under its key whether or not the branch still exists.

`--header` is deterministic: fixed section order, fixed age buckets (`<1h`, `1-6h`, `6-24h`,
`>24h`) and no timestamp that moves between two calls, so an unchanged estate prints
byte-identical text. It is capped at 4096 bytes and ends with a `[truncated]` line when the cut
lands. `--header` and `--json` are refused together: one question, one shape of answer.

## Sources, and what is unverified

| Input | Where it comes from | State |
|---|---|---|
| worktrees, branches, heads | `git worktree list --porcelain` (`--no-optional-locks`) | read |
| merged into `origin/main` | `git branch --merged origin/main` | read |
| run records | `$CONTROLLER_RUNS_DIR`, `$CONTROLLER_HOME/runs`, `~/c5-runs`, `[runs].legacy_dirs` in `agctl.toml` | read: `.done` outcome, `.live` context, `.wt`/`.route`/`.thread`, `.last.txt` READY/YIELD position, `<run>-y<N>` chains |
| registry records | `<runs>/<id>/run.json` | read when present, parseable, schema `agctl-run/1` and id == its directory; ignored otherwise |
| gate slots | `$CONTROLLER_HOME/remote-gate-locks/*.admit`, `*.slot<N>` | read: held/free from `/proc/locks` by device **and** inode, `unknown` when that cannot be decided |
| gate results | `$CONTROLLER_REMOTE_OUT`, else `$CONTROLLER_HOME/remote-gates/<run>/` | read: `lane.log`'s own verdict line for this lane, else `result.json.verdict`, else `unknown` |
| plan and owner | each `.controller-state/<name>/STATE.md` line naming `P-<package>` | **unverified**: prose, reported with its file and line |
| handover age | `.controller-state/HANDOFF.md` and each peer's `HANDOFF.md` mtime | read; the age is printed as a bucket |

Two things the output never does: it never invents a value for a field it could not read (it
prints `unknown` or `missing` with the reason), and it never guesses an age it never measured. A
malformed `agctl.toml` is refused with exit 2 rather than silently ignored, because a
`[runs].legacy_dirs` the caller set is a fact worth failing for.

### Limits a reader should know

- **`STATE.md` is prose.** A package line is quoted as `unverified` with its file and line
  number; it is never read as a machine state. `plan.yaml` (DESIGN section 3's machine source)
  is not read yet — AGC-4.
- **Gate slots record no holder identity**, only a pid from `/proc/locks`. Held/free is a fact;
  *who* holds it is the lock's pid, and nothing more.
- **Gate results carry `project_dir`, not a package id.** A row's gate cell therefore matches on
  the project directory (`cmru`, `libraries/iobench`, ...); a package whose project has no
  recorded run shows `unknown` with the count of currently held slots instead of a verdict.
- **`/proc/locks` is a snapshot of this machine.** Locks held in another devcontainer or on a
  host are not visible here, so a slot whose file exists but whose lock cannot be found reads
  `free` *on this machine* — and the cell says so.
- **A lock file that cannot be stat'ed, or a `/proc/locks` that cannot be read**, is `unknown`,
  never `free`.
- **`run.json` state `launching`/`running` with a free lock** is the DESIGN 4a DIED case; the
  current build reports the state it read and does not yet derive DIED (AGC-4).

## Safety contract

Read-only by construction: no file is written, no lock is taken (a probe `flock` could make a
real gate admission or launcher see "busy"; held/free is decided from `/proc/locks`), no host
and no network is contacted, and the only subprocess is `git` with `--no-optional-locks`.

Every read is bounded: record files at 4 KB, `last.txt`/logs by tail (and by a streaming scan
when a line's *last* occurrence is what matters), `STATE.md` at 256 KB. A source too large to
read whole is reported as `unknown` rather than read as a prefix. Run streams, prompts,
credentials and session transcripts are never read or echoed: only the named record files and
only their first lines or their READY/YIELD/verdict tails.

## Development

```bash
cd agctl && run-gate tests     # tester-unified, pytest, 100 % line+branch coverage floor
```
