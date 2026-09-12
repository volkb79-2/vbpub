# run-gate-WAVE-RG55-P8 — adversarial review, round 2

**Verdict: ACCEPT-conditional.** All six round-1 blockers (B1–B6) are FIXED
and independently re-verified against my own probes, not the implementer's
claims; every required non-blocking item landed; M5 is sound in design and
mechanism. Two NEW blockers, both introduced by the repair set itself, must
land before merge — **B7** (the new `check.sh` B3 mechanism hard-FAILs a
*correctly configured* host whenever a size uses a half-GiB or percentage
value — and the project's own wizard emits half-GiB values routinely) and
**B8** (the new README paragraph asserts a protection this repository has
verified does not exist, as the mechanical substitute for round-1 S10).
Both are small, named, and local. Round 3 is available.

Base `main@11ac5d67`, round-1 tip `7bd2f03c`, round-2 tip `ae38d55a`
(11 commits). No host mutation: no `systemctl`, no `systemd-tmpfiles`
except `--dry-run`, nothing written under `/etc`, no commits to the branch.
The registered gate ran **once**. Host memory PSI checked first
(`full avg10 = 2.65`); everything under `nice -n 19` / `ionice -c 3`.

---

## Per-blocker checklist

### B1 — cap watcher covers `dev-gates.slice` with its own knob — **PASS**

Probe: imported `scripts/mdt-dev-cap-watcher.py` with `CONF` pointed at
scratch files and drove `apply_default_cap` with `subprocess.run` stubbed
(no `systemctl`, no docker).

```
WATCHED_SLICES        : ['dev-interactive.slice', 'dev-background.slice', 'dev-gates.slice']
SLICE_DEFAULT_MEM_MAX : {'dev-interactive.slice': '1G', 'dev-background.slice': '1G', 'dev-gates.slice': '4G'}
  set-property -> MemoryMax=1G   (dev-interactive.slice)
  set-property -> MemoryMax=1G   (dev-background.slice)
  set-property -> MemoryMax=4G   (dev-gates.slice)
```

* default `4G`, other slices unchanged at `DEV_CAP_MEMORY_MAX=1G` ✅
* env-then-file lookup: with `CONF` setting `DEV_CAP_GATES_MEMORY_MAX=9G`
  the module resolves `9G`; with `DEV_CAP_GATES_MEMORY_MAX=2G` in the
  environment, `2G` wins over the file's `9G` ✅
* the `SLICE_DEFAULT_MEMORY_MAX` completeness guard is real: appending an
  unmapped slice to `WATCHED_SLICES` and calling `main()` produced the
  `FATAL:` line and `SystemExit(1)` ✅ (a genuinely good addition — it
  makes the next tier's omission loud instead of silent)
* `sys` is imported (`:56`), so the `sys.exit(1)` path cannot `NameError` ✅
* docstring, `units/mdt-dev-cap-watcher.service` header, `host-setup.env.example`
  and README all state the compose-with-placement rule ✅

### B2 — extraction guards in `tests/test-render.sh` — **PASS**

My round-1 demonstration repeated verbatim (append one variable to the last
`RENDER_VARS` line):

```
FAIL: RENDER_VARS end anchor no longer matches install.sh -- extraction over-ran …   exit 1
```

Two further probes, because an end-anchor check alone is not the whole
hazard:

| probe | result |
|---|---|
| `render() ` end anchor broken (`}` → `}  # comment`) | **RED** — "render() end anchor no longer matches" |
| **both anchors intact**, a `render … /etc/systemd/system/dev.slice` line inserted *inside* the range | **RED** — "extracted snippet contains a host-mutating statement (see above) -- refusing to source it" |

So all three guards fire independently, and the content scan catches the
case the anchor checks cannot. `export TMPDIR="$TMP"` is in place (`:54`).

### B3 — loud signal for the unbounded-upgrade path — **PASS** (mechanism), see **B7** (parser)

`install.sh:94-110`: extracted the `MISSING_KEYS` block verbatim and ran it
against a config stripped of the new keys — it names all seven
(`DEV_GATES_MEMORY_HIGH … DEV_CAP_GATES_MEMORY_MAX`) and points at the
README's additive sequence; run against the shipped example as config
(fresh-host case) it is silent ✅. Name-presence-only comparison is right:
`DEV_MEMORY_MIN_GUARANTEED_CEILING`, `DEV_BUILDKITD_CPU_QUOTA` and
`IO_DEV_PATH` ship declared-but-empty on purpose and produce no noise ✅.

`scripts/check.sh:63-108`: I ran the **whole** `check.sh` against a fake
cgroup tree and a fake `$CONF` (both are already env-overridable: `CG`,
`CONF`), with `systemctl`/`findmnt`/`docker` stubbed on `PATH` — hermetic,
zero host access:

```
(a) config 6G/4G, kernel matches        OK   memory.max=6442450944 matches DEV_GATES_MEMORY_MAX=6G
                                        OK   memory.high=4294967296 matches DEV_GATES_MEMORY_HIGH=4G
(b) config 6G/4G, kernel "max"          FAIL memory.max=max but DEV_GATES_MEMORY_MAX=6G …
                                        FAIL memory.high=max but DEV_GATES_MEMORY_HIGH=4G …
(e) keys absent from $CONF              WARN … not set in $CONF … unchecked
```

That is exactly the prescription: `fail` for "config sets it, kernel does
not have it", `warn` for genuinely unset. ✅ The defect is in the value
parser only — **B7**.

### B4 — `--force`-free additive operator sequence — **PASS**

`README.md:170-227`. The sequence is now backup → `diff` the key sets →
hand-add the missing keys → **one** `install.sh` pass → `mdt-host-check.sh`
→ rebuild the devcontainer. `--force` is demoted to "only for a scheduled
maintenance window", and the fall-through is described accurately: no
`exit`, falls into apt-get / every render / `daemon.json` / `daemon-reload`
/ `systemctl start` / `enable --now` — which matches `install.sh:81-89`
then `:91` onward as I read it. ✅
(Checklist nit: the coordinator asked for line numbers on the fall-through;
the README describes the branch and its consequences but cites no
`install.sh:NN`. Non-blocking, see S21.)

### B5 — withdrawn-design residue — **PASS**

`host-setup.env.example:126-128` is back to `BOTH this slice and dev.slice
below`. Residue sweep over the **full** branch diff (`11ac5d67..ae38d55a`,
added lines only) for `dev-infra|DEV_INFRA|its share of|three MemoryHigh|
byte-for-byte|D-21`: every remaining hit is deliberate — the README
paragraph that *explains* the withdrawal, the test's own withdrawal guard,
a `check.sh` comment noting where the helper came from, and `D-28, amending
D-21` (correct post-A1 phrasing). ✅

### B6 — renderer test covers the wiring — **PASS**

All three round-1 mutations repeated verbatim, all now RED:

| mutation | round 1 | round 2 |
|---|---|---|
| delete `render "$HERE/units/dev-gates.slice.in" …` | GREEN | **RED** — "install.sh never renders units/dev-gates.slice.in" |
| drop `dev-gates.slice` from the `systemctl start` list | GREEN | **RED** — "install.sh never starts dev-gates.slice (… incl. continuations)" |
| `@VAR@` in `RENDER_VARS` but not in the example | GREEN | **RED** — "@DEV_GATES_ZSWAP@ has no DEV_GATES_ZSWAP= line …" |

The line-continuation collapse in assertion (5) genuinely works — the
`systemctl start` list spans two lines and the check still finds the name.
Placeholder↔example check: **34/34 placeholders matched, zero false
positives**, unchanged from round 1. ✅

---

## Required non-blocking items

| item | result |
|---|---|
| wizard earmark sum includes the fourth `MemoryHigh` | **PASS** — `main():1300-1315` adds `DEV_GATES_MEMORY_HIGH` via `resolve_default`; formula text generalized off the hardcoded "three" |
| … proven by doctest | **PASS, and it really runs** — `python3 -m doctest -v` reports `5 tests in mdt-host-setup-wizard.propose_memory_min_guaranteed_suggestion … 5 passed` (a vacuous 0-test file would also exit 0, so I checked the count), and mutating one expected tuple turns the gate assertion RED |
| wizard carries the gates keys through key surgery | **PASS** — `text = example_text` + `apply_value()` on walked keys only (`:1285-1294`); `resolve_default` for a key absent everywhere returns `""` and `parse_size_to_kib("")` returns `0`, so the earmark degrades without raising |
| "byte-for-byte" overclaim gone | **PASS** — withdrawn in the test header (`:11-19`), in the inline comment (`:161-163`) and explicitly in the REPORT ("Overclaim withdrawn (self-caught)") |
| D-24 fallback wording as ruled | **PARTIAL — see B8.** The correct half is there ("protects a devcontainer that was not rebuilt, not a host that was not upgraded"); the sentence added after it is false |
| `AGENTS.md` declares all three variables | **PASS** — `AGENTS.md:93-101` adds `$CGROUP_PARENT_DEV_GATES` with the "no spawner reads it yet" caveat and names all three non-adopters |
| REPORT `Tip:` current | **FAIL (cosmetic)** — see S17 |
| D3 arithmetic verified from the RENDERED values | **PASS** — recomputed below |
| D5 record in README "Changes" + REPORT paste-in | **PASS** — `README.md:181-194` "## Changes"; REPORT "Operator: add to TODO.md"; `TODO.md` untouched on the branch |

**D3 arithmetic, recomputed from the rendered units (not from the ruling):**

```
rendered: interactive CPU 200 / IO 100 | background 20/10 | gates 20/10
          buildkitd 50/50 | memory_min_guaranteed <absent> → systemd default 100
CPU interactive vs background        90.9%   (README "~91%, 200/220")   ✅
CPU + gates (3-way)                  83.3%   (README "~83%, 200/240")   ✅
IO  interactive vs background        90.9%   (README "100/110")         ✅
IO  + gates (3-way)                  83.3%   (README "100/120")         ✅
```
The stated numbers are exactly right. The *label* is not — see S16.

---

## M5 (`/run/cgprofile`, D-30/A2) — attacked as asked

| question | finding |
|---|---|
| does `mounts` break creation on a host without host-setup? | **Yes, and it is correctly documented.** `mounts` entries are Docker `--mount`, which refuses a missing bind source. `/run/cgprofile` **does not exist on this host right now** (`ls: cannot access '/run/cgprofile'`), so any devcontainer rebuilt from this template before the operator runs the new `install.sh` will fail to start. The template's comment block carries all four things RW-32 asked for — purpose, group access, HOST PREREQUISITE, opt-out ("delete this line"). Ruled acceptable by RW-32; see S19 for the one thing the controller should still know. |
| is the gid story right? | **Yes.** The socket ships `root:docker 0660` and the directory `root:docker 0770`; `runArgs` still carries `--group-add ${localEnv:DOCKER_GID}` (verified present and unchanged in the parsed JSON), which is the host docker gid — the same principal set that can `docker exec`, i.e. the exec carrier's own trust boundary. On this host `getent group docker` → `docker:x:994:vscode`, consistent. No new plumbing needed, as claimed. |
| is the tmpfiles entry applied before docker starts after a reboot? | **Yes.** `systemd-tmpfiles-setup.service` is `DefaultDependencies=no`, `After=local-fs.target systemd-sysusers.service`, **`Before=sysinit.target`** (read from the installed unit, no `systemctl`); `docker.service` takes the default dependency on `basic.target`, which is ordered after `sysinit.target`. So the directory exists before dockerd can start any container. `install.sh` additionally runs `systemd-tmpfiles --create` at install time so no reboot is needed. I verified the argument form and the entry's syntax with `systemd-tmpfiles --dry-run --create units/tmpfiles-cgprofile.conf` → `Would create directory /run/cgprofile`, rc 0 (dry-run only; nothing created). |
| any host mutation in the test path? | **No.** `tests/test-render.sh`'s only mentions of `systemctl`/`/etc/`/`install ` are grep *patterns* and comments; assertion (7) is a `grep -qF` over `install.sh`'s text. Confirmed by reading every match. |
| `check.sh` M5 block | `:160-188` — hard `fail` on a missing directory or wrong mode/owner, `info` (uncounted) for socket present/absent with the exact wording ruled. The new `info()` helper does not touch `FAIL`/`WARN`. ✅ |

---

## Blockers

### B7 — the new B3 check hard-FAILs a correctly configured host on half-GiB and percentage sizes

`modern-debian-tools-python-debug/host-setup/scripts/check.sh:66-79`
(`_bytes_of`), consumed at `:88-104`.

`_bytes_of` strips the suffix and multiplies in **bash integer arithmetic**,
so any non-integer mantissa is a syntax error, and any non-K/M/G/T form
(notably a percentage) falls through the `*)` arm and is returned verbatim,
then compared against a byte count. Extracted the function verbatim and ran
it:

```
_bytes_of "6G"    -> 6442450944
_bytes_of "512M"  -> 536870912
_bytes_of "4.5G"  -> bash: 4.5: syntax error: invalid arithmetic operator (error token is ".5")
_bytes_of "50%"   -> 50%
```

Both forms are legal systemd syntax — confirmed with `systemd-analyze
verify` on a scratch unit carrying `MemoryMax=50%` / `MemoryHigh=4.5G`
(rc 0, no complaint; the control unit with `MemoryMax=bogus` *is* rejected,
so the verifier does discriminate).

And this is not hypothetical: **mdt's own wizard emits half-GiB values
routinely.** `mdt-host-setup-wizard.py`'s `kib_to_size_str` rounds to the
nearest 0.5 G (`rounded_g = round(gib * 2) / 2`, then `f"{rounded_g:g}G"`).
Driving `propose_memory_tiers` with real host shapes:

```
MemAvailable  9 GiB -> INTERACTIVE_MEMORY_MAX '4.5G'  BACKGROUND_MEMORY_MAX '4.5G'
MemAvailable 15 GiB -> INTERACTIVE_MEMORY_HIGH '4.5G' BACKGROUND_MEMORY_MAX '7.5G'
MemAvailable 21 GiB -> INTERACTIVE_MEMORY_MAX '10.5G' BACKGROUND_MEMORY_HIGH '6.5G'
```

The README tells the operator to "size them for this host" in exactly that
style. End-to-end consequence, from the hermetic `check.sh` run (fake `CG`
tree, fake `$CONF`, stubbed `systemctl`/`findmnt`/`docker`):

```
(c) DEV_GATES_MEMORY_MAX=4.5G, cgroup memory.max=4831838208  (a CORRECT host)
    check.sh: line 76: 4.5: syntax error: invalid arithmetic operator
    FAIL dev-gates.slice memory.max=4831838208 but DEV_GATES_MEMORY_MAX=4.5G ( bytes)
         -- config says bounded, the kernel's effective value differs … Re-run install.sh.
(d) DEV_GATES_MEMORY_MAX=50%,  cgroup memory.max=8589934592   (a CORRECT host)
    FAIL dev-gates.slice memory.max=8589934592 but DEV_GATES_MEMORY_MAX=50% (50% bytes) …
```

`check.sh` ends in `[ "$FAIL" -eq 0 ]`, so `mdt-host-check.sh` exits 1 — the
final step of both operator sequences fails on a correctly configured host,
with a bash error on stderr and advice ("Re-run install.sh") that cannot
fix it. B3's purpose was to convert a silent fail-open into a loud signal;
as written it also converts a correct configuration into a loud false
alarm, which trains the operator to ignore the signal.

**Prescription** — make the parser total, and make "not byte-comparable" a
`warn`, never a `fail`:

```bash
_bytes_of() { # -> byte count, "" / "max" verbatim, or "?" when not byte-comparable
  local v="${1:-}"
  case "$v" in ""|max) printf '%s' "$v"; return ;; esac
  awk -v v="$v" 'BEGIN{
    if (v !~ /^[0-9]+(\.[0-9]+)?[KMGT]?i?B?$/) { print "?"; exit }   # e.g. "50%"
    m = 1
    if (v ~ /K/) m = 1024; else if (v ~ /M/) m = 1024^2
    else if (v ~ /G/) m = 1024^3; else if (v ~ /T/) m = 1024^4
    printf "%d", (v + 0) * m
  }'
}
```
and at the comparison site:
```bash
cfg_bytes="$(_bytes_of "$cfg")"
if [ "$cfg_bytes" = "?" ]; then
  warn "$var=$cfg is not byte-comparable (percentage or non-size form) -- effective $prop=$eff not checked"
elif [ "$eff" = "$cfg_bytes" ]; then ok  …
else fail … fi
```
Please also add the two cases to `tests/test-render.sh` (it can source
`_bytes_of` out of `check.sh` the same way it sources `render()` out of
`install.sh`, guards included) so the next size form cannot regress it.

### B8 — the README asserts a protection this repository has verified does not exist

`modern-debian-tools-python-debug/host-setup/README.md:163-169`:

> "…**not** a **host that was not upgraded** (the slice unit missing) — that
> failure mode is instead caught mechanically by `mdt-host-check.sh` … and,
> independently, **by `docker run` itself failing outright when a named
> `--cgroup-parent` slice was never installed** and the daemon-wide default
> cannot resolve it either, which run-gate reports"

The emphasised claim is false, and this repository says so in four places —
including one file this same repair set edited and one the same commit
touched:

* `AGENTS.md:108` — "A typo'd or nonexistent slice name fails **open**
  (systemd silently auto-creates an unlimited transient slice)"
* `host-setup/units/docker-scope-default-limits.conf.in:9-12` — "a container
  placed under a typo'd or nonexistent slice name fails OPEN — systemd
  silently auto-creates an unlimited transient slice and the container
  starts normally (**verified** — see nyxloom/docs/plan-resource-governance.md)"
* `host-setup/host-setup.env.example:287-289` — same wording
* `host-setup/scripts/check.sh:193` — "a typo'd/missing `--cgroup-parent`
  fails OPEN (unbounded), unmitigated"

The whole `docker-.scope.d` backstop exists *because* the container starts
normally. This matters beyond tidiness: the sentence is offered as the
mechanical protection covering precisely the gap round-1 S10 identified
(D-24's fallback keys on the variable being unset, but the template always
sets it, so nothing protects "host not upgraded"). The repair therefore
replaces an imprecise statement with a confidently wrong one, in the fail-open
direction, in the section a consumer author will read before deciding
whether they need their own preflight.

**Prescription:** delete the `docker run`-fails-outright clause and state
the verified behaviour:

> …**not** a **host that was not upgraded** (the slice unit missing). That
> case is NOT self-announcing: a `--cgroup-parent` naming a slice with no
> unit file fails **open** — systemd auto-creates an unlimited transient
> slice and the container starts normally (`units/docker-scope-default-limits.conf.in`,
> `AGENTS.md`), which is exactly why the `docker-.scope.d` backstop exists.
> It is caught by `mdt-host-check.sh` (`dev-gates.slice has no unit file`,
> a hard FAIL) and by a consumer that verifies `LoadState=loaded` before
> launch, as `AGENTS.md` already requires — not by docker.

---

## Non-blocking findings

**S15(r2) — `/etc/tmpfiles.d/cgprofile.conf` is the wrong name for an mdt-owned
file, and is the name P6 will want.** `install.sh:211` installs it
unprefixed, while every other mdt-owned drop-in on this host is namespaced:
`/etc/modules-load.d/mdt-bfq.conf`, `/etc/udev/rules.d/60-mdt-bfq-scheduler.rules`,
`/etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf`. The
design has the daemon package asserting on the same directory (D-30 "the
daemon re-asserts owner/mode at start"), so `cgprofile.conf` is exactly what
cgroup-profiler's own deployment would ship — and whichever is installed
last silently wins, after which mdt's new hard `fail` on mode/owner fires on
a host where nothing is actually wrong. Prescription: install it as
`/etc/tmpfiles.d/mdt-cgprofile.conf` (one line in `install.sh`, one in
`check.sh`'s remediation hint, one in the README), and tell P6 the path is
mdt-owned.

**S16 — "worst-case share" is not the worst case.** `README.md:139-147`. The
numbers are right (verified above) but they describe interactive-vs-
background-vs-gates only. With `dev-buildkitd.slice` (CPUWeight 50) also
runnable the share is **69.0 %**, and with `dev-memory_min_guaranteed.slice`
— which declares no `CPUWeight` at all, so systemd's default 100 applies —
it is **51.3 %**; on the IO side, all five runnable gives **37.0 %**. Say
"under simultaneous gates + background contention" instead of "worst-case",
or state the five-way figure too.

**S17 — REPORT `Tip:` still is not the branch tip** (round-1 S6 recurrence).
`run-gate-WAVE-RG55-P8-REPORT.md:3` says `e6af30df`; the branch tip is
`ae38d55a`. `e6af30df` is the last *code* commit, which is the useful fact —
so say that: "Tip: `ae38d55a` (this REPORT commit); last code commit
`e6af30df`, which is what the gate ran against."

**S18 — the REPORT's repair table mislabels two round-1 S-numbers.** Its
"S7" row describes the `TMPDIR` isolation fix (that was S14) and its "S14"
row describes pinning shellcheck severity (not an S at all; S7 was the
"byte-for-byte" overclaim, which is handled correctly in its own paragraph
further down). Bookkeeping only — every item is in fact done.

**S19 — the unconditional mount contradicts the same file's own precedent,
and the ordering is now load-bearing.** `templates/devcontainer.json:70-88`
adds `/run/cgprofile` unconditionally; twenty lines below, the BuildKit
mount is kept **commented out** with the reason "an unconditional mount here
would break container start on any host that hasn't also run the updated
host-setup/install.sh yet. Uncomment … once that's the assumed baseline."
RW-32 ruled the unconditional form with a documented prerequisite and
opt-out, and that is what shipped — so this is not a blocker. But the file
now carries two opposite conventions with no reconciling sentence, and the
concrete state today is that `/run/cgprofile` does not exist on this host,
so the first devcontainer rebuild from this template fails until the
operator runs the new `install.sh`. Either add one sentence to the BuildKit
comment explaining why this mount is treated differently, or adopt the
file's own precedent (ship commented-out, uncomment when host-setup ≥ this
version is the baseline). The controller should also be aware that "upgrade
the host **before** anyone rebuilds a devcontainer" is now an ordering
constraint of the merge, not just of the docs.

**S20 — two of the new assertions can pass vacuously.** (a) assertion (7),
`tests/test-render.sh:196-198`, is `grep -qF 'tmpfiles-cgprofile.conf'
"$INSTALL_SH"` — it would still pass if the only remaining mention were a
comment; match the `install -m … /etc/tmpfiles.d/` line instead (and, for
symmetry with assertion 5, assert the `systemd-tmpfiles --create` call too).
(b) assertion (8) runs `python3 -m doctest`, which exits 0 on a file with
**zero** doctests, so deleting the doctest would not go red; add
`| grep -q '[1-9][0-9]* passed'` on a `-v` run, or an explicit
`grep -q '>>> propose_memory_min_guaranteed_suggestion'` guard. (I verified
the doctest currently does run — 5 tests — and that mutating it goes red.)

**S21 — B4's fall-through description carries no line numbers**, which the
round-2 checklist asked for. `README.md:198-210` describes the `--force`
branch accurately but cites no `install.sh:NN`; adding "(`install.sh:81-89`,
falling through to `:91` and everything after)" makes it checkable by the
next reader.

---

## Claims I could not verify

1. **Anything requiring a real host**, unchanged from round 1: I never ran
   `install.sh`, `systemctl`, or `systemd-tmpfiles` (other than `--dry-run`),
   and wrote nothing under `/etc`. So the live behaviour of the tmpfiles
   entry, the cap watcher against a real new scope, the `check.sh`
   mode/owner branch on a real `/run/cgprofile`, and `systemctl start
   dev-gates.slice` are all reasoned-from-source, not observed. The
   `check.sh` results above come from a fake `CG` tree and stubbed binaries.
2. **Whether `systemd-oomd` is installed and running** on the target host —
   every `ManagedOOM*` directive is a silent no-op without it.
3. **The 2026-08-04 memory finding itself** (session-memory file, not
   repo-tracked) — taken from the design doc's §1 row, as before.
4. **`docker --mount` refusing a missing source** — I did not run a
   throwaway container to prove it (no containers beyond the one gate run).
   It is documented Docker behaviour and the template, the tmpfiles header
   and RW-32 all rest on it; if the controller wants it proven, a two-second
   `docker run --rm --mount source=/nonexistent,target=/x,type=bind alpine
   true` would settle it, and I did not spend the container budget on it.
5. **The REPORT's process claims** (checkpoint clause, "ran ONCE for this
   repair round") — no external evidence available to a reviewer.

---

## Gate

`nice -n 19 ionice -c 3 ./run-gate.py smoke` from
`.worktrees/mdt-dev-slices/modern-debian-tools-python-debug`, run **once**,
verdict read in a separate step:

```
ok: render()/RENDER_VARS extracted from install.sh and guarded (anchors + host-mutation scan)
ok: every rendered unit is free of unresolved @VAR@ placeholders
ok: dev-gates.slice renders every DEV_GATES_* key from the shipped example, and ManagedOOMSwap=kill stays withdrawn
ok: dev.slice and dev-memory_min_guaranteed.slice render with no MemoryMin (opt-in, unset by default)
ok: no units/*.in template mentions the withdrawn dev-infra.slice
ok: install.sh renders every units/*.in, and starts every *.slice
ok: every @VAR@ placeholder used in a template has a matching assignment in host-setup.env.example
ok: install.sh installs the cgprofile tmpfiles.d entry
ok: wizard's earmark-sum doctest (before/after dev-gates.slice's MemoryHigh) passes
ok: bash -n / py_compile clean on every touched script
ok: shellcheck (warning severity+) clean on every touched shell script
test-render: ALL OK
smoke: OK
run-gate: lane 'smoke' exit 0
```

No leftover container (`docker ps -a --filter name=run-gate-vbpub-smoke`:
empty). Worktree clean (`git status --porcelain`: empty). Shared checkout
untouched: the same seven pre-existing dirty mdt files as at session start,
`host-setup/` and `AGENTS.md` clean there. Every mutation in this review was
applied to a scratch copy under the session scratchpad and reverted (`diff -r`
against the worktree identical afterwards, modulo `__pycache__`).

## What round 3 needs to cover, if the controller takes it

Only B7 and B8, plus whichever of S15(r2)/S16/S17/S19/S20/S21 are accepted.
B7 wants its own regression case in `test-render.sh`; B8 is a paragraph
rewrite. Nothing else in this repair set needs re-verification — B1–B6 and
M5's mechanism are settled.
