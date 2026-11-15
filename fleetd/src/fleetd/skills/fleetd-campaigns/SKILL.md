---
name: fleetd-campaigns
description: Plan and supervise a RAM-first Assay campaign across the configured compute fleet with the fleet CLI — host facts, candidate placement, one-shot dispatch, restart recovery and one-owner result collection.
---

# fleetd: plan and run a compute-fleet campaign

`fleetd` turns "which candidate runs where" into a deterministic plan and
then supervises the hosts through the existing runner jobs. It is a one-shot
loop: `run` starts work and returns, and `status`, `tick`, `collect` are
explicit calls you repeat from whatever owns the campaign. There is no daemon,
socket or lease.

Facts this skill assumes, all of them product invariants:

- A **candidate** is one Assay R2 mutant candidate, identified by its lowercase
  64-character SHA-256 digest. A **worker** is a lane slot on a host; a
  **batch** is candidates placed on one host and run as one Run-Gate invocation;
  a **campaign** is the named, versioned state file `fleet` supervises.
- RAM decides placement. Each candidate must fit a host's physical lane budget
  after the control-plane reserve; overcommit applies to expected **sums** only.
  Geekbench single-core scores inform ETA, never eligibility.
- Some hosts are **refused**: a shared-prod role, a PSI reading above the
  configured threshold, an unknown role, or unknown physical facts. A refused
  host is never contacted by any command. The production game host and the
  controller machine itself are never campaign targets: do not list them in a
  hosts file and do not point a runner job at them by hand.
- fleetd contains no host address, provider credential or key. Host IDs in a
  plan are aliases of the operator's local transport configuration; the
  transport is the identity-checked `hx` wrapper only.

## Check the environment

```bash
command -v fleet
fleet --version
```

`fleet` is an installed console script whose wheel declares `cli-extended`. If
`fleet` is missing, ask the environment owner to install the fleetd wheel on the
machine you are on — do not copy the sources onto a path or run a stdlib-only
entry. On a managed host, the wheel (plus `cli-extended`) is what provides
`fleet facts collect`.

## Host facts, in place of guesses

```bash
fleet facts collect > host-facts.json      # runs on the host itself
fleet facts validate host-facts.json       # anywhere, no host contact
```

`fleet facts collect` reads host-owned inputs only: `/proc`, `/sys`, the
control-plane service `MemoryMin` properties through `systemctl show`, the
Geekbench record and `/etc/fleetd/facts.toml`. It never invokes Geekbench and
never writes sysfs or a cgroup. `--root DIR` maps every absolute host path
beneath `DIR`, which is how you collect from a fixture tree without querying
systemd. A missing or incomplete facts config is a refusal (exit 2), not a
partial record.

`fleet guard validate FILE` parses the host-owned guard rules (including
drop-ins). It parses only: fleetd does not apply guard policy.

## Plan, then dispatch

```bash
fleet plan r2 --candidates candidates.json --hosts hosts.json \
  --demand lane-demand.toml > plan.json
fleet run plan.json --transport hx --campaign r2-week46
fleet status r2-week46
fleet tick r2-week46
fleet collect r2-week46
```

`fleet plan` is deterministic: identical inputs produce byte-identical JSON. It
also pins `execution` (commit, project path) only when you run it from a clean
Git project with a Run-Gate configuration; `fleet run` refuses a plan without
that object rather than guessing the checkout. `--commands` prints the
equivalent serial shell commands instead of JSON, for a manual handoff.

Before `fleet run`, sync the plan's commit to every planned host: the runner
checkout must already be a real `checkouts/<commit>/` directory with the
selected project and lane. Start refuses (and leaves the batch queued) when it
is not, instead of creating a directory the runner would refuse.

`fleet run` re-adopts jobs by their deterministic names, so re-running it after
a restart is safe and does not duplicate work. Echo the same `--state-dir` on
`status`, `tick` and `collect`; the default is
`$XDG_STATE_HOME/fleetd/campaigns`. Overlapping commands on one campaign fail
fast with "busy" and change nothing.

Call `tick` every few minutes. A host unreachable for more than 30 minutes
counts as lost and its unfinished work is re-queued; a batch becomes `STALLED`
after `max(15 minutes, 3 x predicted p95, 3 x observed p95)`. A batch that exits
or stalls before finishing any candidate is `FAILED` with its reason and exit
code, and is not re-queued.

`fleet collect` writes `fleet-campaign-result/1` JSON plus a Markdown report into
the state directory. Each candidate has exactly one owner: the earliest finished
record wins and later copies are counted as ignored duplicates; never-started
candidates stay `missing` with a distinct reason.

## Reading the output

Results go to stdout and diagnostics to stderr. Every read verb prints its
documented, key-sorted JSON; `--json` prints one compact JSON document instead.
Exit status: `0` success, `2` a refused input (bad file, missing facts config,
unknown campaign, bad option value), `1` an unexpected failure (add
`--traceback` for the stack), `3` the CLI itself is not installed correctly.
`fleet plan --commands` exit status describes rendering only: the generated
script accepts Run-Gate exit 4 (`BUDGET_EXCEEDED`) and stops on other codes.

Do not invent limits. The campaign client passes only generated `--jobs` and
`--only-candidates`; lane budgets, `jobs_max` and cgroup limits stay host and
Run-Gate policy. A printed plan's per-host RAM budget is not forwarded to the
current runner (tracked as FD-003).
