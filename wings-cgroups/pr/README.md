# Upstream submission — prepared, NOT submitted

The series was reordered so that every planned PR is a **contiguous range** of
commits instead of a cherry-pick out of the middle. Current order (verified:
applies cleanly onto bare upstream, resulting tree identical to before):

| # | Commit | PR |
|---|---|---|
| 0001 | `Add docker.cgroup_parent to place containers under a systemd slice` | PR 2 |
| 0002 | `Support per-server cgroup parent override via WINGS_CGROUP_PARENT` | PR 2 |
| 0003 | `Add docker integration tests for cgroup parent placement` | PR 2 |
| 0004 | `Manage per-server transient slices via systemd D-Bus` | PR 3 |
| 0005 | `Let administrators state IO weights on BFQ's own scale` | PR 3 |
| 0006 | `Report configuration keys discarded while parsing` | PR 1 |

The independent config-diagnostics commit moved from 0005 to the **end** of the
series precisely so it can be submitted first and alone without disturbing the
cgroup commits.

## Submission plan — three PRs, in this order

### PR 1 — config parse diagnostics (0006), first and alone

Not part of the cgroup story at all: it warns when `config.yml` contains a key
Wings does not recognize, instead of dropping it silently and then erasing it
from the file on the next boot rewrite. ~136 lines, no new dependency, no
behaviour change beyond log output — the easiest of the series to merge, it
benefits every operator, and it stands on its own for any Wings user who has
ever mistyped an indent. Its tests have been decoupled from the cgroup config
keys (fixtures now use only upstream-native keys: `debug`, `uuid`, `api.host`,
`system.data`, `system.username`, `docker.network.interface`), so the commit
genuinely cherry-picks onto stock upstream and passes there.

Lead with the concrete failure it prevents (a real deployment lost
`per_server_slices.enabled: true` to a wrong indent level and had no way to
tell), and with the deliberate choice to warn rather than fail: strict parsing
would turn a stale key from an older Wings into a boot failure. If maintainers
want it stricter, the natural follow-up is a `--strict-config` flag, not a
default.

### PR 2 — placement (0001 + 0002 + 0003)

`docker.cgroup_parent` + guarded `WINGS_CGROUP_PARENT` override + the
build-tagged docker integration tests. Body: `pelican-wings-pr.md` (sections
through "Security notes"). Small, opt-in, default-off, **no new dependency**.
0003 is self-contained and CI-friendly (create-only, driver-agnostic) — include
by default, drop on maintainer pushback.

### PR 3 — per-server slices (0004 + 0005), stacked on PR 2

`docker.per_server_slices`, `internal/cgroups`, the go-systemd dependency, and
the BFQ-scale IO weight key. Open it stacked on PR 2 and reference the RFC
issue — this is a real design discussion (D-Bus access from Wings, budget
semantics, transient-unit lifecycle), not a knob. Selling points to lead with:
zero per-server admin steps, reload-safe systemd-owned properties, fail-open
degradation (D-Bus problems never block a server), floor-budget arithmetic
(child `memory.min` beyond the parent's floor is silently dead — the patch
enforces what admins today get wrong by hand), and a `budget_policy` that lets
an operator choose between honest-but-frozen guarantees (`clamp`/`refuse`) and
kernel-managed usage-proportional sharing (`distribute`).

0005 belongs here rather than in its own PR: it depends on 0004's
`internal/cgroups`. It exists because `IOWeight=` is close to unusable on a BFQ
node — BFQ schedules on `io.bfq.weight` (1..1000) and does not read `io.weight`
(that is iocost's file, inert unless `io.cost.*` is set), and systemd derives
the former from the latter by compressing 100..10000 into 100..1000, so
`IOWeight=1000` buys 1.81x against a default sibling, not 10x, and nothing
anywhere says so. `io_bfq_weight` lets an admin state the weight BFQ will
actually use; Wings converts it to the equivalent `IOWeight` property (systemd's
divisor is exactly 1/11, so the round trip is lossless) rather than writing
`io.bfq.weight` directly, which systemd would clobber on its next IO apply.
Reviewers will reasonably ask "why isn't this systemd's problem?" — it is, and
systemd carries a FIXME saying so ("drop this function when distro kernels
properly support BFQ through io.weight"). Until that lands, every BFQ node is
silently mis-weighted, and Wings is the layer that can state the intent. Raise
it as an RFC question rather than assuming the answer.

The findings in `../patchstack/README.md` (on-demand slice loading, transient
FragmentPath, the `memory_recursiveprot` mount-flag prerequisite, and the
distroless `/var/run` D-Bus trap) are worth calling out for reviewers — the last
two belong in the PR's deployment/README notes, since both make the feature
silently do nothing on a node that looks correctly configured.

**PR 3 must open with the `io_weight` collision**, not bury it: upstream Wings
already ships a per-server `io_weight`, and this series adds two more IO-weight
knobs with different scales at a different cgroup level. See "Relationship to
the existing `io_weight`" in `pelican-wings-pr.md` — that section is the single
most likely reason a maintainer rejects or stalls the series, so it goes above
the fold.

## Mechanics

1. Create the fork(s) on GitHub; set `FORK_REPO` in `../patchstack/stack.conf`.
2. Pelican first (faster merge cadence, owns panel+wings):
   ```bash
   cd ../build/wings-pelican
   git remote add fork git@github.com:OWNER/wings-pelican-fork.git
   git push fork cgroup/main
   gh pr create --repo pelican-dev/wings --base main --head OWNER:cgroup/main \
     --title "Add cgroup parent support: node-wide docker.cgroup_parent + optional per-server override" \
     --body-file ../../pr/pelican-wings-pr.md
   gh issue create --repo pelican-dev/wings --title "RFC: staged path for cgroup v2 resource guarantees" \
     --body-file ../../pr/rfc-issue.md
   ```
   Push one branch per PR, not the whole stack on one branch.
3. Pterodactyl in parallel: rebase onto `develop` first
   (`../patchstack/scripts/rebase.sh pterodactyl develop`), then the same
   `gh pr create` against `pterodactyl/wings` `develop` with
   `pterodactyl-wings-pr.md`.
4. Strip/adjust the PR-draft headers (the "Status: DRAFT" blocks) — they are
   for this repo, not for the PR body.

Also decide per-project whether to keep the `Co-Authored-By: Claude` trailers
in the commits (`git rebase -i` + reword to strip them if not wanted).

## Known review exposure — fix or disclose before submitting

Findings from a code review of the series, all in 0004/0005 (PR 3); PR 1 and
PR 2 are unaffected.

**Fixed in 0004:**

- The clamp/refuse/distribute budget logic used to live inline in `Ensure()`,
  reachable only with a live D-Bus connection, so it had no coverage in a
  tagless `go test ./...` — no runnable test of the headline feature. It is now
  a pure `applyBudget(props, budget, used) (Props, bool)` with a table test
  (`internal/cgroups/budget_test.go`) covering every policy, the exhausted-
  budget underflow case, and the invariant that non-floor properties pass
  through untouched.
- Budget enforcement was a read-then-write with no lock: two servers starting
  concurrently could each observe the same remaining budget and both claim it,
  overrunning the budget the feature exists to enforce. Now serialized by a
  package-level mutex held across the apply, so the sum a request reads already
  accounts for every request that has committed.

**Still open — fix or disclose before submitting:**

- Boot-time orphan GC snapshots the known-server set and then sweeps
  asynchronously for up to a minute; a server created inside that window can
  have its slice stopped.
- D-Bus error handling matches **English message text** ("already exists", "not
  loaded") instead of D-Bus error names
  (`org.freedesktop.systemd1.UnitExists` / `NoSuchUnit`).
- `sumMemoryMin` does one D-Bus round trip per sibling slice on **every
  container create**; on a large node this can blow the 10s ensure timeout and
  silently degrade to placement-only.
- `Ensure` is additive, not reconciling: removing an egg variable leaves the
  previously-set property on the live unit.
- The docker integration tests reuse fixed container names and
  `Environment.Create()` returns early if the container already exists, so a
  crashed prior run can leave the tests asserting against a stale container —
  and passing.
- `docker-compose.example.yml` is not updated with the required
  `/run/dbus/system_bus_socket` mount, and `CHANGELOG.md` is untouched — both
  things upstream maintainers will ask for.
