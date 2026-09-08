# mdt-host-setup-wizard — REPORT

Package: `modern-debian-tools-python-debug/host-setup/plan-host-setup-wizard.md`.
Branch `mdt-host-setup-wizard`, worktree
`/workspaces/vbpub/.worktrees/mdt-host-setup-wizard`. Final HEAD at gate
time: **`f9c1623f`**. **Not merged to `main`** — a fresh adversarial
reviewer verifies first.

All commands below were run for real, from
`/workspaces/vbpub/.worktrees/mdt-host-setup-wizard/modern-debian-tools-python-debug/host-setup`
unless noted, on the shared host (8 cores, co-located production game
server) — every scratch artifact lives under a private scratchpad
directory, never `/etc/mdt/host-setup.env`, never
`host-setup.env.example` itself. No IO baseline benchmark was ever actually
run (the disk-saturating ~4min fio pass) — every "run it now?" prompt
below was answered "no"/left at its default, satisfying the host-shared-load
rule; the one exception (§1 note) is a "yes" answer that hit a `run as root`
refusal instantly (this environment is non-root), never touching the disk.

---

## 1. The real gate — verbatim verdict

Command (from `modern-debian-tools-python-debug/`, against the clean tree
at `f9c1623f`):

```
python3 run-gate.py smoke
```

Run redirected to a file, then the file read in a **separate step** — never
off a piped tail:

```
run-gate: admission: lane 'smoke' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 36 | lane smoke | env built-in 'host' default (ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest) | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 2m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-smoke-809871-1788836439 --cgroup-parent dev-background.slice -e CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice [...] python3 -m py_compile && echo 'smoke: OK''
smoke: OK
run-gate: lane 'smoke' exit 0
```

**PASS**, exit 0. `py_compile` ran over every tracked `.py` file in
`modern-debian-tools-python-debug/`, `mdt-host-setup-wizard.py` included.

---

## 2. Byte-preservation oracle

### 2a. Accept-every-default run, diffed against the original

```
cp host-setup.env.example /scratch/example1.env
python3 scripts/mdt-host-setup-wizard.py \
  --example /scratch/example1.env --output /scratch/out1.env \
  --skip-run-offer < /dev/null
diff -u /scratch/example1.env /scratch/out1.env
```

Result — every changed line is one of the 10 host-scaled keys the wizard
walks (`IO_DEV_PATH` plus the 9 per-tier memory keys from step d); every
comment, blank line, section header, and every OTHER key (including
`DEV_IO_CAP_PCT`/`SWEEP_IO_CAP_PCT`, `DEV_MEMORY_MIN_GUARANTEED_CEILING`,
`DEV_BUILDKITD_IMAGE`/`CPU_QUOTA`, the `DOCKER_*` keys, and every
non-walked key in the file) is byte-identical:

```diff
-IO_DEV_PATH=""
+IO_DEV_PATH="overlay"
@@
 DEV_INTERACTIVE_MEMORY_MIN=500M
-DEV_INTERACTIVE_MEMORY_LOW=2G
-DEV_INTERACTIVE_MEMORY_HIGH=5G
-DEV_INTERACTIVE_MEMORY_MAX=8G
+DEV_INTERACTIVE_MEMORY_LOW=850M
+DEV_INTERACTIVE_MEMORY_HIGH=1.5G
+DEV_INTERACTIVE_MEMORY_MAX=3G
@@
-DEV_BACKGROUND_MEMORY_HIGH=6G
-DEV_BACKGROUND_MEMORY_MAX=8G
+DEV_BACKGROUND_MEMORY_HIGH=1.5G
+DEV_BACKGROUND_MEMORY_MAX=3G
-DEV_BACKGROUND_MEMORY_SWAP_MAX=48G
+DEV_BACKGROUND_MEMORY_SWAP_MAX=34.5G
@@
-DEV_BUILDKITD_MEMORY_HIGH=6G
-DEV_BUILDKITD_MEMORY_MAX=10G
-DEV_BUILDKITD_MEMORY_SWAP_MAX=20G
+DEV_BUILDKITD_MEMORY_HIGH=1.5G
+DEV_BUILDKITD_MEMORY_MAX=2.5G
+DEV_BUILDKITD_MEMORY_SWAP_MAX=14G
```

(`DEV_INTERACTIVE_MEMORY_MIN` shows no diff: this host's 3%-of-MemTotal
proposal happened to round to the same `500M` the shipped example also
uses — coincidence, not evidence the value came from the example rather
than the live computation; §4 below proves the formula is live-derived with
fixtures where it does NOT coincide.)

### 2b. Controlled wrong implementation — proves the oracle actually catches the failure mode

Swapped the real `apply_value()` (anchored `^KEY=...$` regex) for a
deliberately blanket, unanchored one and applied both to the identical
input text for key `DEV_IO_CAP_PCT` (chosen because the example file
mentions this key's bare name, in prose, in 3 comment lines outside its own
assignment line — lines 9, 172, 183):

```python
# correct (the real implementation)
correct = wiz.apply_value(text, "DEV_IO_CAP_PCT", "99")

# WRONG (controlled failure mode)
def apply_value_blanket_WRONG(text, key, value):
    return re.sub(re.escape(key) + r".*", f"{key}={value}", text)
wrong = apply_value_blanket_WRONG(text, "DEV_IO_CAP_PCT", "99")
```

Output:

```
correct-impl lines changed:
  line 195: 'DEV_IO_CAP_PCT=60' -> 'DEV_IO_CAP_PCT=99'
blanket(WRONG)-impl lines changed:
  line 9: '#   - runtime values (DEV_IO_CAP_PCT, patterns, CGROUP2_FLAGS): picked up by' -> '#   - runtime values (DEV_IO_CAP_PCT=99'
  line 172: '#   DEV_IO_CAP_PCT protects PRODUCTION from the whole dev tier combined (an' -> '#   DEV_IO_CAP_PCT=99'
  line 183: "#     system.slice, not dev-background.slice) — DEV_IO_CAP_PCT's aggregate" -> '#     system.slice, not dev-background.slice) — DEV_IO_CAP_PCT=99'
  line 195: 'DEV_IO_CAP_PCT=60' -> 'DEV_IO_CAP_PCT=99'
```

The real implementation touches exactly the one assignment line. The
blanket implementation corrupts 3 additional comment lines that merely
mention the key's name in prose, truncating them mid-sentence — confirming
the byte-preservation diff oracle does catch this exact, real failure mode,
not just a "looks fine" check.

---

## 3. Value-lands oracle

3 typed, deliberately recognizable values across 3 different sections (the
plan's own suggested example: `IO_DEV_PATH`, one `DEV_BACKGROUND_MEMORY_*`
key, `DEV_MEMORY_MIN_GUARANTEED_CEILING`), every other prompt left at its
default:

```
grep -n "CANARY-DISK\|777G\|321M" /scratch/out2.env
```

```
21:IO_DEV_PATH="/dev/CANARY-DISK"
80:DEV_BACKGROUND_MEMORY_SWAP_MAX=777G
115:DEV_MEMORY_MIN_GUARANTEED_CEILING=321M
```

Each typed value landed on its own `KEY=value` line. Full diff against the
original confirms no OTHER key changed beyond exactly this run's typed
values plus the same host-scaled proposals accepted-by-default in §2a
(identical diff set, plus the 3 substitutions) — no unintended key was
touched:

```diff
-IO_DEV_PATH=""
+IO_DEV_PATH="/dev/CANARY-DISK"
-DEV_INTERACTIVE_MEMORY_LOW=2G
-DEV_INTERACTIVE_MEMORY_HIGH=5G
-DEV_INTERACTIVE_MEMORY_MAX=8G
+DEV_INTERACTIVE_MEMORY_LOW=800M
+DEV_INTERACTIVE_MEMORY_HIGH=1.5G
+DEV_INTERACTIVE_MEMORY_MAX=2.5G
-DEV_BACKGROUND_MEMORY_HIGH=6G
-DEV_BACKGROUND_MEMORY_MAX=8G
+DEV_BACKGROUND_MEMORY_HIGH=1.5G
+DEV_BACKGROUND_MEMORY_MAX=2.5G
-DEV_BACKGROUND_MEMORY_SWAP_MAX=48G
+DEV_BACKGROUND_MEMORY_SWAP_MAX=777G
-DEV_MEMORY_MIN_GUARANTEED_CEILING=
+DEV_MEMORY_MIN_GUARANTEED_CEILING=321M
-DEV_BUILDKITD_MEMORY_HIGH=6G
-DEV_BUILDKITD_MEMORY_MAX=10G
-DEV_BUILDKITD_MEMORY_SWAP_MAX=20G
+DEV_BUILDKITD_MEMORY_HIGH=1.5G
+DEV_BUILDKITD_MEMORY_MAX=2.5G
+DEV_BUILDKITD_MEMORY_SWAP_MAX=14G
```

---

## 4. Empty-stays-empty oracle (render-side, closes the loop against the sibling fix)

Accepting the ceiling's default (empty, §2a's `out1.env`):

```
grep -n "DEV_MEMORY_MIN_GUARANTEED_CEILING" /scratch/out1.env
```
```
115:DEV_MEMORY_MIN_GUARANTEED_CEILING=
```

Rendered through a harness reproducing `install.sh`'s exact `RENDER_VARS`
list and `render()` function verbatim (temp harness, not shipped/committed
— reads `units/dev.slice.in` and `units/dev-memory_min_guaranteed.slice.in`
read-only):

```
dev.slice MemoryMin:
(none -- correct)
dev-memory_min_guaranteed.slice MemoryMin:
(none -- correct)
```

Neither slice carries a `MemoryMin=` line — the render-side bug the sibling
commit `27a0fcc1` fixed stays fixed; this wizard doesn't reopen it from the
other direction.

**Positive case**, same harness against `out2.env` (§3's `321M`):

```
positive case (321M) dev.slice MemoryMin:
MemoryMin=321M
positive case (321M) dev-memory_min_guaranteed.slice MemoryMin:
MemoryMin=321M
```

Both slices carry the identical value — "the one invariant that actually
matters" (CGROUP-NOTES.md) survives the wizard -> render pipeline.

---

## 5. Live-sizing oracle

Two synthetic `/proc/meminfo` fixtures (mocked via `--meminfo-path`, no
real second host needed):

- **Fixture A**: `MemTotal=32000000kB MemAvailable=24000000kB SwapTotal=8000000kB`
  (~30.5GiB / ~22.9GiB avail / ~7.6GiB swap)
- **Fixture B**: `MemTotal=8000000kB MemAvailable=2000000kB SwapTotal=0kB`
  (~7.6GiB / ~1.9GiB avail / no swap)

```
python3 scripts/mdt-host-setup-wizard.py --example ... --output ... \
  --meminfo-path /scratch/meminfo-fixtureA --skip-run-offer < /dev/null
python3 scripts/mdt-host-setup-wizard.py --example ... --output ... \
  --meminfo-path /scratch/meminfo-fixtureB --skip-run-offer < /dev/null
```

Fixture A output:

```
This host: MemTotal=30.5GiB, MemAvailable=22.9GiB right now, SwapTotal=7.6GiB.
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [950M]:
  MemoryLow (DEV_INTERACTIVE_MEMORY_LOW) [3.5G]:
  MemoryHigh (DEV_INTERACTIVE_MEMORY_HIGH) [7G]:
  MemoryMax (DEV_INTERACTIVE_MEMORY_MAX) [11.5G]:
  MemoryHigh (DEV_BACKGROUND_MEMORY_HIGH) [7G]:
  MemoryMax (DEV_BACKGROUND_MEMORY_MAX) [11.5G]:
  MemorySwapMax (DEV_BACKGROUND_MEMORY_SWAP_MAX) [4G]:
  MemoryHigh (DEV_BUILDKITD_MEMORY_HIGH) [5.5G]:
  MemoryMax (DEV_BUILDKITD_MEMORY_MAX) [10.5G]:
  MemorySwapMax (DEV_BUILDKITD_MEMORY_SWAP_MAX) [1.5G]:
This host: MemAvailable (23G) - step d's three MemoryHigh figures (19.5G combined) = 3.5G left over; 5% of that leftover = 150M.
```

Fixture B output:

```
This host: MemTotal=7.6GiB, MemAvailable=1.9GiB right now, SwapTotal=0.0GiB.
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [250M]:
  MemoryLow (DEV_INTERACTIVE_MEMORY_LOW) [300M]:
  MemoryHigh (DEV_INTERACTIVE_MEMORY_HIGH) [600M]:
  MemoryMax (DEV_INTERACTIVE_MEMORY_MAX) [1000M]:
  MemoryHigh (DEV_BACKGROUND_MEMORY_HIGH) [600M]:
  MemoryMax (DEV_BACKGROUND_MEMORY_MAX) [1000M]:
  MemorySwapMax (DEV_BACKGROUND_MEMORY_SWAP_MAX) [0]:
  MemoryHigh (DEV_BUILDKITD_MEMORY_HIGH) [500M]:
  MemoryMax (DEV_BUILDKITD_MEMORY_MAX) [900M]:
  MemorySwapMax (DEV_BUILDKITD_MEMORY_SWAP_MAX) [0]:
This host: MemAvailable (2G) - step d's three MemoryHigh figures (1.5G combined) = 250M left over; 5% of that leftover = 0.
```

Every step-d number differs between fixtures (e.g. `MemoryMin` 950M vs.
250M, `MemoryHigh` 7G vs. 600M, swap-derived `MemorySwapMax` 4G vs. `0` —
fixture B has no swap and the proposal correctly reflects that), and each
is demonstrably arithmetic on the fixture's own numbers, not a hardcoded
constant (visibly `total_kib * pct // 100` / `avail_kib * pct // 100`
against the printed `MemTotal`/`MemAvailable` for that run). Step e's
suggestion also genuinely varies (150M vs. 0) and its printed formula shows
the arithmetic against each fixture's own `MemAvailable` figure. (Note:
this required correcting a first-draft bug where the three step-d
`MemoryHigh` percentages summed to exactly 100%, making the "leftover"
degenerately 0 on every host regardless of input — see LOG for the fix;
the numbers above are post-fix.)

---

## 6. Full manual interactive run transcript (stdin from `/dev/null` — every prompt, including the trailing "run install.sh now?", takes its default)

```
== mdt host-setup wizard ==
template: /scratch/example5.env
writing:  /scratch/out5.env
Press Enter at any prompt to accept the shown default.

-- a. IO device (IO_DEV_PATH) --
The block device install.sh sizes the static IOPS/bandwidth fallback
caps against (dev.slice's IOReadBandwidthMax etc. — the boot-window
fallback in force until the IO baseline below has actually run).
Auto-discovered the SAME way install.sh does at render time: findmnt
against /var/lib/docker, falling back to /.
auto-discovered: overlay
Block device node backing docker's data dir (IO_DEV_PATH) [overlay]:

-- b. IO baseline --
cache: /var/lib/mdt/io-baseline.env
no cache found yet. Until this is measured, mdt-apply-dev-caps.sh falls back to
the deliberately tight DEV_STATIC_* caps in host-setup.env.
Run the IO baseline benchmark now? It takes ~4 minutes and SATURATES THE DISK -- only in a quiet window (the script itself also warns and gives you 5s to Ctrl-C if containers are running) [y/N]:
Skipping -- run it later with: sudo .../scripts/mdt-io-baseline.py

-- c. IO cap percentages --
Both are percentages of the MEASURED device ceilings (io-baseline.env)
and both belong in a 60-80% band: never 100% (a saturated device
queues everything behind the burst -- the exact stall this tiering
exists to prevent), and below ~60% you're just throttling ordinary
work for no protective gain.
  DEV_IO_CAP_PCT   -- whole-estate ceiling on dev.slice (bounds every
                      dev container together; protects PRODUCTION
                      from the tier, not tier members from each other).
  SWEEP_IO_CAP_PCT -- per-container ceiling (protects tier members
                      from EACH OTHER; it's buildkit workers' ONLY
                      governance, since Buildx placement under
                      dev.slice doesn't work at all).
Whole-estate IO cap % (DEV_IO_CAP_PCT) [60]:
Per-container IO cap % (SWEEP_IO_CAP_PCT) [80]:

-- d. Per-tier memory --
This host: MemTotal=15.6GiB, MemAvailable=4.3GiB right now, SwapTotal=69.0GiB.
Proposed splits below scale off MemAvailable (what's actually free
on THIS host right now, already net of anything else -- including a
co-located production tier -- using memory), NOT off MemTotal, which
would overstate real headroom on a shared host. MemoryHigh is a soft
throttle and MemoryMax relies on this host's own swap to absorb
overflow (see README's tiering model), so these proposals deliberately
overlap across tiers rather than partitioning 100% of RAM up front --
override any of them if you know better for this host.

  dev-interactive.slice (devcontainers/IDE) -- MemoryMin is a small,
  STABLE % of MemTotal (a protection floor shouldn't shrink just
  because something else is using more RAM right now); Low/High/Max
  scale off MemAvailable as above.
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [500M]:
  MemoryLow (DEV_INTERACTIVE_MEMORY_LOW) [650M]:
  MemoryHigh (DEV_INTERACTIVE_MEMORY_HIGH) [1.5G]:
  MemoryMax (DEV_INTERACTIVE_MEMORY_MAX) [2G]:

  dev-background.slice (test/build/gate containers) -- relaxed swap:
  a build that swaps just finishes slowly, one that OOMs fails
  outright. Sized against THIS host's own 69.0GiB swap, not a
  fixed number.
  MemoryHigh (DEV_BACKGROUND_MEMORY_HIGH) [1.5G]:
  MemoryMax (DEV_BACKGROUND_MEMORY_MAX) [2G]:
  MemorySwapMax (DEV_BACKGROUND_MEMORY_SWAP_MAX) [34.5G]:

  dev-buildkitd.slice (shared BuildKit worker) -- must cover
  CONCURRENT multi-project builds, not just one build at a time.
  MemoryHigh (DEV_BUILDKITD_MEMORY_HIGH) [1G]:
  MemoryMax (DEV_BUILDKITD_MEMORY_MAX) [2G]:
  MemorySwapMax (DEV_BUILDKITD_MEMORY_SWAP_MAX) [14G]:

-- e. Memory-min-guaranteed ceiling (DEV_MEMORY_MIN_GUARANTEED_CEILING) --
What it is: a HARD floor (unlike MemoryLow's soft/best-effort
protection) -- memory this cgroup keeps even under host-wide
pressure, protected from reclaim.

Where it's set, and why TWO places: dev.slice (root) AND
dev-memory_min_guaranteed.slice (a SIBLING of the interactive/
background tiers you just configured -- NOT nested under either),
pinned to the EXACT SAME value on purpose. cgroup v2 hands a parent's
UNCLAIMED protection down to whichever child is using memory,
proportionally -- so a generous number on dev.slice alone leaks
protection to the interactive/background tiers, defeating the point
of a dedicated guaranteed tier. Give dev.slice less than this ceiling
instead and the leaf's own MemoryMin is silently inert. Both slices
need EXACTLY this one number, never two independently-chosen ones.

The effect / who actually gets protected: this is opt-in PER
CONTAINER -- a stack must explicitly place itself on
dev-memory_min_guaranteed.slice (governance.cgroup_parent in its ciu
config) and declare its own claim (governance.mem_min). Setting this
ceiling alone protects NOTHING by itself; it only raises the total a
stack COULD claim. (ciu's own admission control enforcing this
ceiling against live claims is a separate, in-flight piece of work --
see ../../ciu/nyxloom-trove/handoffs/ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md)

This host: MemAvailable (4.5G) - step d's three MemoryHigh figures (4G combined) = 300M left over; 5% of that leftover = 0.
A conservative suggestion, IF you want a nonzero ceiling at all: 0
(meant to stay small -- see CGROUP-NOTES.md 'Per-container memory.min guarantees' for why a generous number here is
actively harmful, not just wasteful.)

Leaving this EMPTY (just press Enter) keeps the whole mechanism
fully inert, matching host-setup.env.example's own shipped default --
that stays the default answer below, not the suggestion above.
Memory-min-guaranteed ceiling (empty = leave the mechanism off) (DEV_MEMORY_MIN_GUARANTEED_CEILING) [<empty>]:

-- f. buildkitd --
BuildKit worker image (DEV_BUILDKITD_IMAGE) [moby/buildkit:buildx-stable-1-rootless]:
CPUQuota uses a DIFFERENT empty-means convention from every other key
in this file: empty here does NOT mean 'not applied' -- it means
'auto-detect (nproc - 2) cores at install time,' floored at 1 core.
An explicit percentage (e.g. '400%' = 4 cores) always overrides the
auto-detected value. Don't assume empty = uncapped here.
CPUQuota (empty = auto-detect nproc-2 cores) (DEV_BUILDKITD_CPU_QUOTA) [<empty>]:

-- g. Docker daemon.json keys this tool owns --
DOCKER_DAEMON_CGROUP_PARENT is the daemon-wide default placement
(D-G7) for any container that names no --cgroup-parent of its own.
install.sh MERGES this single key into /etc/docker/daemon.json; it
never overwrites the file, and needs a dockerd RESTART (not reload)
to take effect.
Default cgroup-parent (DOCKER_DAEMON_CGROUP_PARENT) [dev-background.slice]:

The docker-.scope.d backstop (D-G8) is a 'never truly unbounded'
floor for EVERY container's transient scope, regardless of which
slice (or none) it named -- size it GENEROUSLY, above any legitimate
single container's real ceiling on this host; this is a fail-open
backstop, not a tier limit.
Backstop MemoryMax (DOCKER_SCOPE_BACKSTOP_MEMORY_MAX) [12G]:
Backstop MemorySwapMax (DOCKER_SCOPE_BACKSTOP_MEMORY_SWAP_MAX) [16G]:

wrote /scratch/out5.env
Run install.sh now to render + apply this config? [y/N]:
Next step: sudo .../install.sh
```

(`/scratch/...` paths above stand in for this session's actual scratchpad
directory, elided for readability; every path in the literal runs was a
scratch file, never `/etc/mdt/host-setup.env`.)

---

## 7. `install.sh --wizard` wiring — sanity checks (not a live privileged run)

`bash -n install.sh` — syntax OK. `--help` now shows the flag and a
complete (previously truncated) usage block:

```
  sudo ./install.sh [--wizard] [--with-baseline] [--force] [--restart-docker]
...
it, then re-run to apply your edits). --wizard walks that seeding step
interactively instead (scripts/mdt-host-setup-wizard.py) — sizes the tiers
against THIS host's own /proc/meminfo rather than the example's fixed
numbers, then falls through into the same render/apply logic below either
way. ...
```

`./install.sh --wizard` (unprivileged, this environment's real uid) exits
cleanly at the pre-existing root check (`run as root`) before touching
anything — confirms the new branch doesn't bypass that gate. The actual
privileged path (wizard invocation -> render -> systemd unit
install/apply) was NOT run live: doing so would modify real systemd units,
install packages, and touch `/etc/docker/daemon.json` on the shared
production host, which is out of scope for this package's own
verification (the plan's oracles are all scratch-file/harness-based for
exactly this reason). The wizard's own output (byte-preservation,
value-lands, empty-stays-empty via the render-logic harness) is verified
directly in §§2-4 above; `install.sh`'s `--wizard` branch itself is a ~20
line, straight-line addition (backup-if-exists, invoke with explicit
paths, no conditionals beyond that) reviewed by inspection and diffed
against git to confirm no other line in the file changed (LOG, "Scope").
