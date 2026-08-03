# srdm — agent operating guide

Project-specific operating instructions. Repo-wide doctrine is in
`../../AGENTS.md`; canonical nyxloom doctrine ships with the product
(`nyxloom/reference/{AUTHORING,STANDARD,DOCTRINE}.md`). This file is srdm's
delta.

## Cockpit vs runner

The devcontainer is the cockpit: inspect and drive, never gate. For srdm
this is not a rule you could accidentally break — **the cockpit has no Go
toolchain at all**. Everything that compiles or tests runs in `srdm-gate`.

## Running the gate

```bash
# One-time (or after gate/Dockerfile changes): the image builds itself on
# first use, so usually you just run the gate.
tools/gate.sh                       # unit target, this checkout
tools/gate.sh /path/to/worktree     # a nyxloom worktree
tools/canary-run.sh                 # prove the oracles still reject bad code
```

Both need a **verified** host cgroup tier. Resolution order:

1. `$SRDM_CGROUP_PARENT` — the explicit per-project override, which is what
   `nyxloom.toml`'s gate argv sets.
2. `$CGROUP_PARENT_DEV_BACKGROUND` — the ambient devcontainer tier.
3. Nothing — refuse to launch.

`tools/cgroup-parent.sh` does not trust the name. A slice systemd does not
know **fails open**: systemd silently creates an unlimited transient slice
and the container starts normally, so a typo removes every limit while
looking like success. The script therefore reads the host cgroup tree
through a `--cgroupns=host` probe and requires both that the slice's cgroup
exists and that at least one resource knob differs from the kernel default.
An unconfigured slice satisfies the first and fails the second.

> **This devcontainer, as of 2026-08-03:** `$CGROUP_PARENT_DEV_BACKGROUND`
> is unset — the image predates the mdt host-setup rollout that injects it,
> and it cannot be rebuilt mid-session. The tier itself IS installed and
> configured (`dev-background.slice`: MemoryMax 16G, MemoryHigh 8G,
> MemorySwapMax 48G, CPUWeight 20, IOWeight 10, under a `dev.slice`
> carrying the measured io.max). Export it by hand:
>
> ```bash
> export CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice
> ```
>
> Do not hardcode it anywhere that is not an explicit, verified override.

## Reading the verdict

The gate runs the container **detached** and reads the exit code from
`docker wait`, never from an attached stream. A container gate reached over
a truncating relay can drop output mid-run and hand back a forged exit code,
so a failing gate reads as passing — the same aliasing that makes a trailing
`echo` mask a real exit status, one layer down in the plumbing.

## Gates

| id | what it asserts | when |
|---|---|---|
| `unit` | gofmt, build, vet, and the O1–O5 oracles | implementation |
| `coverage` | changed-line floor, 80% over `internal/` | review |
| `canary` | that each oracle **rejects** a break of the contract it names | review |
| `privileged-e2e` | publication topology, hold services, charging | **empty until P02** (D-004) |

```bash
tools/gate.sh . coverage                      # against main
SRDM_COVERAGE_BASE=HEAD~1 tools/gate.sh . coverage
SRDM_COVERAGE_FLOOR=90 tools/gate.sh . coverage
```

`coverage` has **three** outcomes, not two. Exit 3 is NO MEASUREMENT — a
dirty tree (the `base..HEAD` diff cannot see uncommitted work) or a base that
resolves to HEAD. Both otherwise render as `0/0 changed lines covered
(100.0%)`, which is indistinguishable from a real clean pass. If you see
exit 3, commit first; do not read it as green.

`canary` is the stronger of the two. Coverage proves a changed line *ran*;
the canaries prove the oracle goes red when the contract breaks — which is
what hollow tests evade. If you change an oracle, run `canary`, and if one
reports "the mutation matched nothing", the code moved and that canary is
silently testing an unmodified tree. That is a failure, not a skip.

## Where handoffs live

`nyxloom-trove/handoffs/srdm-P<NN>-<slug>.md`, stem equal to the frontmatter
`id` (lint L1). Anything matching that glob is parsed as a handoff, so no
plain README belongs there.

`../HANDOFF-P01-bootstrap-and-store.md` stays at the project root on purpose:
it was written before `nyxloom.toml` existed, so it has no `[gates.*]` ids to
reference and carries no frontmatter. Lint would reject it inside `handoffs/`.

`nyxloom.toml` also deliberately omits the `roadmap` and `backlog` keys.
Declaring them opts the project into the numeric-prefixed direction spine
(`1-north-star.md` … `4-backlog.md`, each with validated frontmatter), which
rules S1–S4 then enforce. The plain `roadmap.md` / `backlog.md` this trove
holds stay valid; adopt the spine as its own package if it is ever wanted.

## What must not be touched

`../wings-cgroups/**` holds the **design** documents. A change there is a
decision, not an implementation — take it to the design session, not to a
package. Likewise `../scripts/gstammtisch-guide/**` is the live host's
files, and `../wings-patchstack/**` is the Wings patch stack.

## Committing from the shared main checkout

`/workspaces/vbpub` is shared; another agent's serial merge may stage files
at any moment. Scope commits to explicit paths and bypass the shared index:

```bash
git commit --only -F msg.txt -- shared-ramdisk-depot-manager
git show --stat HEAD --name-only | sed 's#/.*##' | sort -u   # verify
```

Never `reset`/`rebase`/`--amend` to repair a contaminated commit — HEAD may
have moved under a concurrent commit. Land a correct new one instead.
