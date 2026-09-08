# mdt-host-setup-wizard round 2 — REPORT

Package: `modern-debian-tools-python-debug/host-setup/plan-host-setup-wizard-round2.md`.
Branch `mdt-host-setup-wizard-r2`, worktree
`/workspaces/vbpub/.worktrees/mdt-host-setup-wizard-r2`. Code commit at gate
time: **`7a02c8a9`**. **Not merged to `main`** — a fresh adversarial reviewer
verifies first.

Everything below was run for real. Commands are quoted as issued; output is
pasted verbatim (trimmed only where marked `…`, never paraphrased).

Working directory for every command:
`/workspaces/vbpub/.worktrees/mdt-host-setup-wizard-r2/modern-debian-tools-python-debug/host-setup`
unless stated otherwise. Scratch artifacts live under a private scratchpad
directory abbreviated `$S` below; `/etc/mdt/host-setup.env` and
`host-setup.env.example` itself were never written to.

**The real IO baseline benchmark was never run** (~4 min of saturated disk, on
a host shared with a production game server). Item 1's paths were exercised
through the CLI's own injectable seams — see §1. `install.sh` was never
executed either; `--install-script` pointed at a stub.

Shared fixtures used throughout:

```
$ cat $S/meminfo
MemTotal:       16384000 kB
MemFree:         2000000 kB
MemAvailable:   10240000 kB
SwapTotal:      73400320 kB

$ cat $S/swaps
Filename				Type		Size	Used	Priority
```

---

## 0. The real gate — verbatim verdict

Command, from `modern-debian-tools-python-debug/`, against the clean tree at
`7a02c8a9`:

```
nice -n 10 ionice -c3 python3 run-gate.py smoke
```

Redirected to a file; the file read in a **separate** step, never off a piped
tail (LESSONS L4):

```
run-gate: admission: lane 'smoke' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 36 | lane smoke | env built-in 'host' default (ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest) | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 2m (advisory)
run-gate: docker argv: /usr/bin/docker run -d --name run-gate-vbpub-smoke-2296582-1788853520 --cgroup-parent dev-background.slice … python3 -m py_compile && echo 'smoke: OK''
smoke: OK
run-gate: lane 'smoke' exit 0
```

Exit code, read separately: `exit=0`. **PASS.**

An earlier attempt against the dirty tree was refused, which is worth recording
because it is the reason the code was committed before the gate ran:

```
run-gate: refusing to judge a dirty tree: /workspaces/vbpub/.worktrees/mdt-host-setup-wizard-r2 has 4 uncommitted change(s) (first: ' M modern-debian-tools-python-debug/host-setup/README.md') — commit or pass --allow-dirty
```

No pytest suite was added. `run-gate.toml`'s own header states this subproject
has none by policy; verification is by direct scratch runs, below.

Shell-syntax checks on the two touched shell files:

```
$ bash -n scripts/check.sh && echo "check.sh syntax OK" && bash -n install.sh && echo "install.sh syntax OK"
check.sh syntax OK
install.sh syntax OK
```

---

## 8. `resolve_default()` — the correctness item

Taken first because it is the one confirmed-live bug.

### 8a. The bug, reproduced against the UNMODIFIED pre-fix code

The pre-fix file was recovered with
`git show HEAD:…/scripts/mdt-host-setup-wizard.py > $S/orig-wizard.py` and run
against a fixture standing in for the overwhelmingly common real case — a
`/etc/mdt/host-setup.env` whose optional fields are present but blank:

```
$ cat $S/b-orig.env
IO_DEV_PATH=""
DEV_INTERACTIVE_MEMORY_MIN=
DEV_INTERACTIVE_MEMORY_LOW=
DEV_BACKGROUND_MEMORY_HIGH=
DEV_MEMORY_MIN_GUARANTEED_CEILING=

$ python3 $S/orig-wizard.py --example $S/example.env --output $S/b-orig.env \
    --meminfo-path $S/meminfo --swaps-path $S/swaps --skip-run-offer < /dev/null
```

Filtered to the relevant prompts:

```
auto-discovered: overlay
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [<empty>]:
  MemoryLow (DEV_INTERACTIVE_MEMORY_LOW) [<empty>]:
  MemoryHigh (DEV_BACKGROUND_MEMORY_HIGH) [<empty>]:
-- e. Memory-min-guaranteed ceiling (DEV_MEMORY_MIN_GUARANTEED_CEILING) --
Memory-min-guaranteed ceiling (empty = leave the mechanism off) (DEV_MEMORY_MIN_GUARANTEED_CEILING) [<empty>]:
```

That is the whole bug in four lines: the wizard **computes and prints**
`auto-discovered: overlay`, then offers `[<empty>]`. Same for every host-scaled
memory proposal — computed, then discarded. The plan's account is confirmed,
including its "verify this yourself rather than trusting this account" clause:
the earlier operator transcript's apparently-working `DEV_BACKGROUND_MEMORY_HIGH`
was indeed just a key that host's file did not happen to contain. When the key
IS present-but-blank, as above, it fails identically to `IO_DEV_PATH`.

An earlier run against a straight `cp` of `host-setup.env.example` shows the
narrower, shipped-default form of the same failure — `IO_DEV_PATH` is the one
optional key the example ships blank:

```
auto-discovered: overlay
Block device node backing docker's data dir (IO_DEV_PATH) [<empty>]:
…
IO_DEV_PATH=""      # written value
```

### 8b. The fix

```python
-    if key in cfg_current:
+    if key in cfg_current and cfg_current[key]:
         return cfg_current[key]
```

Same command, same fixture, post-fix code:

```
auto-discovered on this host: `overlay`
Block device node backing docker's data dir (IO_DEV_PATH) [overlay]:
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [500M]:
  MemoryLow (DEV_INTERACTIVE_MEMORY_LOW) [1.5G]:
  MemoryHigh (DEV_INTERACTIVE_MEMORY_HIGH) [3G]:
  MemoryHigh (DEV_BACKGROUND_MEMORY_HIGH) [3G]:
  MemoryMax (DEV_BACKGROUND_MEMORY_MAX) [5G]:
-- e. Memory-min-guaranteed ceiling (DEV_MEMORY_MIN_GUARANTEED_CEILING) --
(DEV_MEMORY_MIN_GUARANTEED_CEILING)
[<empty>]:
```

Every host-scaled proposal now reaches the prompt. `1.5G` is
`frac(MemAvailable=10240000 KiB, 15%)` — this fixture host's number, not the
example's shipped `2G`.

### 8c. Regression guard 1 — the ceiling must STILL default to empty

Visible in 8b's last two lines: `DEV_MEMORY_MIN_GUARANTEED_CEILING` still
resolves to `<empty>`. The chain, traced and then confirmed by the run:
`cfg_current["…CEILING"] == ""` → the new `and cfg_current[key]` is falsy →
falls through → `proposal is None` (passed unconditionally by
`step_memory_min_guaranteed()`) → falls through →
`example_defaults.get(key, "")`, and the example ships
`DEV_MEMORY_MIN_GUARANTEED_CEILING=` empty. Same end state as before the fix.

The written file agrees, from an accept-every-default run:

```
$ grep -E "^(IO_DEV_PATH|DEV_MEMORY_MIN_GUARANTEED_CEILING|DEV_BACKGROUND_MEMORY_HIGH|DEV_INTERACTIVE_MEMORY_MIN)=" $S/current-blank.env
IO_DEV_PATH="overlay"
DEV_INTERACTIVE_MEMORY_MIN=500M
DEV_BACKGROUND_MEMORY_HIGH=6G
DEV_MEMORY_MIN_GUARANTEED_CEILING=
```

### 8d. Regression guard 2 — a REAL prior value must still win

Constructed exactly as the plan asks, with a deliberately-chosen device that is
not the auto-discovered one:

```
$ cat $S/c-real.env
IO_DEV_PATH=/dev/sdb
DEV_INTERACTIVE_MEMORY_LOW=7G
DEV_MEMORY_MIN_GUARANTEED_CEILING=256M

$ python3 mdt-host-setup-wizard.py --example $S/example.env --output $S/c-real.env \
    --meminfo-path $S/meminfo --swaps-path $S/swaps --skip-run-offer < /dev/null
```

```
auto-discovered on this host: `overlay`
Block device node backing docker's data dir (IO_DEV_PATH) [/dev/sdb]:
  MemoryLow (DEV_INTERACTIVE_MEMORY_LOW) [7G]:
  MemoryHigh (DEV_INTERACTIVE_MEMORY_HIGH) [3G]:
-- e. Memory-min-guaranteed ceiling (DEV_MEMORY_MIN_GUARANTEED_CEILING) --
(DEV_MEMORY_MIN_GUARANTEED_CEILING)
[256M]:
```

`/dev/sdb` beats the freshly-discovered `overlay`; `7G` beats the `1.5G`
proposal; a real `256M` ceiling is remembered. Written file:

```
IO_DEV_PATH="/dev/sdb"
DEV_INTERACTIVE_MEMORY_LOW=7G
DEV_MEMORY_MIN_GUARANTEED_CEILING=256M
```

Note the third line specifically: an explicitly-configured ceiling still
survives a re-run. The only thing the fix makes unrememberable is an *empty*
prior value, which is indistinguishable from an untouched seed and which the
fall-through reproduces anyway.

---

## 9. `mdt-host-check.sh` — the memory-min-guaranteed invariant

### 9a. What was added

A new section, in the existing `ok`/`warn`/`fail` house style, placed between
the `dev.slice` IO-caps section and the `docker-.scope.d` backstop section. It
reads `MemoryMin` and `FragmentPath` for `dev-memory_min_guaranteed.slice` with
`systemctl show <unit> -p <PROP> --value` — the same call shape the neighbouring
checks already use — and compares against `dev.slice`'s own `MemoryMin`.

### 9b. How it was verified, and why that way

There is no systemd in this container:

```
$ systemctl show dev.slice -p MemoryMin --value
"systemd" is not running in this container due to its overhead.
```

The existing checks in `check.sh` have **no** test harness of any kind (there is
nothing to match), and `CG`/`CONF` are the only injectable seams. So a
`systemctl` test double was put first on `PATH`; it answers only
`show <unit> -p <PROP> --value`, from `FIXTURE_<unit>_<PROP>` env vars, and
prints the empty string for anything unset — which is what real systemd prints
for an unknown property. No root required.

```
$ env FIXTURE_… PATH="$S/fakebin:$PATH" CG=$S/fakecg CONF=$S/fake-conf.env bash scripts/check.sh
```

Five fixtures, verbatim output of the new section:

```
--- A. unit not installed at all (no FragmentPath, no MemoryMin) ---
== dev-memory_min_guaranteed.slice (opt-in memory.min tier) ==
  OK   inert (unit not installed or MemoryMin unset — the mechanism is opt-in, this is the default and expected state on most hosts)

--- B. unit installed, MemoryMin unset (renders to 0) ---
== dev-memory_min_guaranteed.slice (opt-in memory.min tier) ==
  OK   inert (unit not installed or MemoryMin unset — the mechanism is opt-in, this is the default and expected state on most hosts)

--- C. opted in, values PINNED EQUAL (the correct configuration) ---
== dev-memory_min_guaranteed.slice (opt-in memory.min tier) ==
  OK   MemoryMin pinned equal on dev.slice and dev-memory_min_guaranteed.slice (268435456) — no protection leaks to dev-interactive/dev-background

--- D. DRIFTED: dev.slice GENEROUS (the silent leak this check exists for) ---
== dev-memory_min_guaranteed.slice (opt-in memory.min tier) ==
  FAIL MemoryMin MISMATCH: dev.slice=1073741824 vs dev-memory_min_guaranteed.slice=268435456 — these MUST be exactly equal. If dev.slice is HIGHER, the surplus redistributes to dev-interactive.slice/dev-background.slice (the leak the guaranteed tier exists to prevent); if LOWER, the guaranteed tier's own floor is silently inert. Fix: set DEV_MEMORY_MIN_GUARANTEED_CEILING in $CONF and re-run install.sh (both units render from that one var)

--- E. DRIFTED: dev.slice unset -> leaf floor silently inert ---
== dev-memory_min_guaranteed.slice (opt-in memory.min tier) ==
  FAIL MemoryMin MISMATCH: dev.slice=0 vs dev-memory_min_guaranteed.slice=268435456 — …
```

Case B matters: `install.sh`'s `render()` deletes any directive whose value
resolves to empty, so an unset `DEV_MEMORY_MIN_GUARANTEED_CEILING` leaves the
unit installed with no `MemoryMin` at all. That is the shipped default on every
host that has not opted in, and it is an `OK`, not a `WARN` — per the plan.

The FAIL reaches the script's exit code (the check counts into `$FAIL`), shown
by the summary line moving by exactly one between the matched and drifted
fixtures:

```
C (matched): result: 4 failure(s), 5 warning(s)
D (drifted): result: 5 failure(s), 5 warning(s)
```

(The other four failures are the pre-existing slice-unit checks, which the test
double answers as absent. They are constant across fixtures, so the delta is
attributable.)

### 9c. What this check does NOT do

It compares the two `MemoryMin` values, which is the invariant CGROUP-NOTES.md
names. It does not attempt to verify the *live* `memory.min` in cgroupfs, nor
the bounded under-utilisation gap that section quantifies and accepts. Both are
out of scope for a config-drift check and neither was claimed.

---

## 1. `--with-baseline` is not re-asked after a successful baseline run

The benchmark itself was never run. Instead, `--io-baseline-script` (an existing
CLI seam) pointed at stubs, and `--install-script` at a stub, so the full
end-of-run offer could be exercised:

```
$ cat $S/fake-baseline-ok.py
print("[stub mdt-io-baseline.py] pretending a successful ~4min measurement; exit 0")

$ cat $S/fake-baseline-fail.py
import sys
print("[stub mdt-io-baseline.py] pretending fio blew up; exit 3")
sys.exit(3)

$ cat $S/fake-install.sh
#!/bin/sh
echo "[stub install.sh] argv: $*"
```

Answers: `/dev/sdb`, then `y` or `n` at the baseline prompt, 18 blanks, `y` at
"Run install.sh now?".

```
$ python3 mdt-host-setup-wizard.py --example $S/example.env --output $S/i.env \
    --meminfo-path $S/meminfo --swaps-path $S/swaps \
    --io-baseline-script <stub> --install-script $S/fake-install.sh < <answers>
```

```
######## RUN 1: baseline ran, exit 0 -> sub-question SKIPPED ########
[y/N]: [stub mdt-io-baseline.py] pretending a successful ~4min measurement; exit 0
Run install.sh now to render + apply this config? [y/N]: Not offering --with-baseline: the IO baseline was just measured above in this
running:
[stub install.sh] argv:

######## RUN 2: baseline DECLINED -> sub-question ASKED ########
[y/N]: Skipping -- you can run it later with: sudo
Run install.sh now to render + apply this config? [y/N]: Also pass --with-baseline (re-run the IO benchmark during install)? [y/N]: running:
[stub install.sh] argv:

######## RUN 3: baseline ran, NONZERO exit -> sub-question ASKED ########
[y/N]: [stub mdt-io-baseline.py] pretending fio blew up; exit 3
WARN: the IO baseline run exited 3 -- the static caps remain in force until it
Run install.sh now to render + apply this config? [y/N]: Also pass --with-baseline (re-run the IO benchmark during install)? [y/N]:
running:
[stub install.sh] argv:
```

`[stub install.sh] argv:` is empty in run 1 — `--with-baseline` was neither
asked nor passed — and the skip is announced, not silent.

Fourth path, **cache already fresh**, using the REAL `mdt-io-baseline.py`
(so its own `cache_is_fresh()` is what decides) against a pre-seeded scratch
cache file, per the plan's own suggestion:

```
######## RUN 4b: cache ALREADY FRESH (no pre-existing config) ########
This cache is fresh (measured less than 30 days ago) -- leaving it as-is.
Run install.sh now to render + apply this config? [y/N]: Also pass --with-baseline (re-run the IO benchmark during install)? [y/N]: running:
[stub install.sh] argv:
```

Correct: nothing was measured *this session*, so the operator still gets the
choice. This run also doubles as proof that the moved
`DEFAULT_IO_BASELINE_SCRIPT` path works — the module was imported and
`cache_is_fresh()` called from the new location, with no override passed.

Ordering note: the child's output originally landed *above* the parent's
introducing lines in piped transcripts, because the wizard's stdout is
block-buffered when it is not a terminal. `sys.stdout.flush()` was added before
both `subprocess.run` calls; the transcripts above are post-fix and correctly
ordered.

---

## 2. Input validation

One canned run exercises the re-prompt loop at every reachable validator.
Answers, in order (28 lines):

```
sdb • /dev/sdb • n • sixty • 120 • 45 • 70 • "500 megs" • 500M •
(6 blanks) • 0 • (3 blanks) • lots • (blank) • moby/buildkit:latest •
"4 cores" • 0% • 400% • dev-background • "12 gigs" • 12G • (blank)
```

```
$ python3 mdt-host-setup-wizard.py --example $S/example.env --output $S/v-out.env \
    --meminfo-path $S/meminfo --swaps-path $S/swaps --skip-run-offer < $S/answers2.txt
```

Verbatim excerpts (a piped run puts the next output on the prompt's own line,
since `input()`'s prompt has no trailing newline and stdin's newline is not
echoed — in a real terminal the operator's Enter supplies it; this artifact is
identical to the round-1 transcripts):

**`IO_DEV_PATH` — not an absolute path:**
```
auto-discovered on this host: `overlay`
Block device node backing docker's data dir (IO_DEV_PATH) [overlay]:   invalid: 'sdb' is not an absolute path -- give a device node such as /dev/sda
  or /dev/mapper/vg-root (or leave it empty to omit the static IO caps)
Block device node backing docker's data dir (IO_DEV_PATH) [overlay]:
```

**`DEV_IO_CAP_PCT` — non-integer, then out of 1..100, then out-of-band (warned, accepted):**
```
Whole-estate IO cap % (DEV_IO_CAP_PCT) [60]:   invalid: 'sixty' is not an integer percentage (give a bare number, e.g. 60 --
  no '%' sign)
Whole-estate IO cap % (DEV_IO_CAP_PCT) [60]:   invalid: 120 is outside 1-100; a cap above 100% of the measured ceiling caps
  nothing, and 0% would stall the tier outright
Whole-estate IO cap % (DEV_IO_CAP_PCT) [60]: WARN: 45% is outside the recommended 60-80 band explained above -- accepted, but
re-read that reasoning before keeping it.
Per-container IO cap % (SWEEP_IO_CAP_PCT) [80]:
```

**A size field — malformed:**
```
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [500M]:   invalid: '500 megs' is not a systemd-style size string (e.g. '500M', '6G',
  '2048K', or a bare byte count)
  MemoryMin (DEV_INTERACTIVE_MEMORY_MIN) [500M]:
```

**The ceiling — malformed, then empty accepted:**
```
[<empty>]:   invalid: 'lots' is not a systemd-style size string (e.g. '500M', '6G',
  '2048K', or a bare byte count) -- press Enter alone to leave the mechanism off
[<empty>]:
```

**`DEV_BUILDKITD_CPU_QUOTA` — wrong shape, then zero, then accepted:**
```
CPUQuota (empty = auto-detect nproc-2 cores) (DEV_BUILDKITD_CPU_QUOTA)
[<empty>]:   invalid: '4 cores' is not a systemd CPUQuota= percentage -- use N% (e.g.
  '400%' for 4 cores), or leave it empty to auto-detect
CPUQuota (empty = auto-detect nproc-2 cores) (DEV_BUILDKITD_CPU_QUOTA)
[<empty>]:   invalid: a CPUQuota of 0% would stop buildkitd from running at all -- give at
  least '100%' (one core), or leave it empty to auto-detect
CPUQuota (empty = auto-detect nproc-2 cores) (DEV_BUILDKITD_CPU_QUOTA)
[<empty>]:
```

**`DOCKER_DAEMON_CGROUP_PARENT` — no `.slice` suffix (warned, accepted):**
```
Default cgroup-parent (DOCKER_DAEMON_CGROUP_PARENT) [dev-background.slice]: WARN: 'dev-background' does not end in '.slice' -- systemd slice units always
do. A name that does not resolve fails OPEN (the container lands in an unbounded
transient scope, bounded only by the docker-.scope.d backstop), so this is
accepted, but check it for a typo.
```

**`DOCKER_SCOPE_BACKSTOP_MEMORY_MAX` — malformed:**
```
Backstop MemoryMax (DOCKER_SCOPE_BACKSTOP_MEMORY_MAX) [12G]:   invalid: '12 gigs' is not a systemd-style size string (…)
Backstop MemoryMax (DOCKER_SCOPE_BACKSTOP_MEMORY_MAX) [12G]:
```

The values that survived, and the byte-preservation oracle still holding (only
walked keys whose answer actually differed appear in the diff — every comment,
blank line, section header and untouched key is byte-identical):

```
$ diff -u $S/example.env $S/v-out.env | grep -E "^[+-][A-Z]"
-IO_DEV_PATH=""
+IO_DEV_PATH="/dev/sdb"
-DEV_BACKGROUND_MEMORY_SWAP_MAX=48G
+DEV_BACKGROUND_MEMORY_SWAP_MAX=0
-DEV_BUILDKITD_CPU_QUOTA=""
+DEV_BUILDKITD_CPU_QUOTA="400%"
-DEV_BUILDKITD_IMAGE="moby/buildkit:buildx-stable-1-rootless"
+DEV_BUILDKITD_IMAGE="moby/buildkit:latest"
-DEV_IO_CAP_PCT=60
+DEV_IO_CAP_PCT=45
-SWEEP_IO_CAP_PCT=80
+SWEEP_IO_CAP_PCT=70
-DOCKER_DAEMON_CGROUP_PARENT=dev-background.slice
+DOCKER_DAEMON_CGROUP_PARENT=dev-background
```

`DEV_BACKGROUND_MEMORY_SWAP_MAX=0` was typed and accepted — the case a
`parse_size_to_kib(v) > 0` validator would have wrongly rejected.

**EOF safety.** With `< /dev/null`, an invalid *default* must not spin the
re-prompt loop. On this container `findmnt` returns `overlay`, which is not an
absolute path — a genuine, unstaged instance of exactly that case:

```
Block device node backing docker's data dir (IO_DEV_PATH) [overlay]:
WARN: 'overlay' is not an absolute path -- give a device node such as /dev/sda
or /dev/mapper/vg-root (or leave it empty to omit the static IO caps) --
accepting it anyway; stdin is exhausted, so there is nobody left to ask.
```

Reported, accepted, run completed with exit 0. No hang.

### Judgment calls (the plan asks these be stated, not silently picked)

**IO cap band — WARN, not hard refuse, outside 60-80; HARD refuse outside
1..100.** Two different kinds of rule, deliberately not merged. A percentage
`<= 0` or `> 100` is not an unusual choice, it is not a cap: 120% of a measured
ceiling constrains nothing and 0% would wedge the tier — that is a syntax-class
error and is refused. The 60-80 band is a strong recommendation the file argues
for at length, but it is a recommendation about *this* hardware's
characteristics. An operator whose measured ceiling is itself pessimistic (a
shared SAN, a baseline taken under load) can have a real reason to sit outside
it, and a hard gate would also make the wizard unable to reproduce a config a
knowledgeable operator had already hand-written — turning the wizard into a
worse tool than the text editor it replaces. The warning names the band and
tells them to re-read the reasoning; that is the useful intervention.

**`.slice` suffix — WARN, not hard refuse.** The failure mode of a wrong slice
name is fail-OPEN by this project's own documented design: the container lands
in an unbounded transient scope, caught by the `docker-.scope.d` backstop.
Nothing crashes. A warning is proportionate to a typo whose consequence is
already mitigated; a refusal would be presumption. Empty *is* refused, because
an empty string is not a valid daemon-wide default in `daemon.json` at all.

**Size strings — `_SIZE_RE`, not `parse_size_to_kib(...) > 0`.** The plan says
to reuse `parse_size_to_kib` and not write a second parser, describing it as
"already raises on garbage". It does not raise — it returns `0`, for garbage
*and* for the valid string `"0"`. Since `propose_memory_tiers()` itself emits
`"0"` for every swap key on a swapless host, a `> 0` test would reject the
wizard's own proposal. `is_size_string()` therefore matches
`parse_size_to_kib`'s **own** `_SIZE_RE` — the same parser, no duplication.
This is a correction to the plan's premise, not a departure from its intent.

**Empty accepted only where it is meaningful.** Checked per field rather than
blanket-allowed. Accepted for `DEV_MEMORY_MIN_GUARANTEED_CEILING` (documented
"leave the mechanism inert"), `IO_DEV_PATH` (documented "static IO caps
omitted") and `DEV_BUILDKITD_CPU_QUOTA` (documented "auto-detect"). Refused for
every per-tier memory key and both backstop keys, because `install.sh`'s
`render()` **drops** a directive whose value resolves to empty — so an empty
answer there silently removes the governance rather than setting it to "no
limit", which is never what someone answering a prompt labelled `MemoryHigh`
intends.

**A note the reviewer may want to weigh.** Because Enter accepts the default,
an operator can never *type* an empty value at a prompt whose default is
non-empty. The non-empty validators (`DEV_BUILDKITD_IMAGE`,
`DOCKER_DAEMON_CGROUP_PARENT`) are therefore only reachable when the default is
itself empty — i.e. when the template or the existing config is already
degenerate. They still catch typed garbage in every other respect, and they are
cheap; but their empty-rejection arm is a guard against bad input *state*, not
against operator typing.

---

## 3. Terminal-width-aware output and conditional colour

### 3a. Non-tty: no escape bytes anywhere

Every redirected run in this REPORT was checked. Example, on the full
accept-every-default run:

```
$ grep -c $'\033' $S/run-8a.txt
0
```

Zero escape sequences. The `backtick spans` survive as literal backticks, which
is the point of choosing that convention — the plain-text transcript reads as
correct Markdown-ish prose, not as leftover markup.

### 3b. Real tty: colour appears

Driven through a real pty (`pty.openpty()`), so `isatty()` is genuinely true:

```
$ TERM=xterm-256color, NO_COLOR unset
ESC count: 30
^[[1;36mdev.slice^[[0m's ^[[1;36mIOReadBandwidthMax^[[0m, ^[[1;36mIOWriteBandwidthMax^[[0m, ^[[1;36mIOReadIOPSMax^[[0m and

$ NO_COLOR=1
ESC count: 0
`dev.slice`'s `IOReadBandwidthMax`, `IOWriteBandwidthMax`, `IOReadIOPSMax` and

$ TERM=dumb
ESC count: 0
```

All three arms of the gate verified independently, on a real terminal each
time — not just the pipe case.

### 3c. Reflow tracks the real width, capped at 120

```
$ for w in 60 100 200; do COLUMNS=$w python3 mdt-host-setup-wizard.py … ; done
COLUMNS=60  -> longest output line = 124
COLUMNS=100 -> longest output line = 124
COLUMNS=200 -> longest output line = 124
```

The cap holds — at `COLUMNS=200` the prose wraps at ≤120, not at 200:

```
The proposals below scale off MemAvailable rather than MemTotal, deliberately: MemAvailable is what is genuinely free on
THIS host at this moment, already net of everything else using memory -- including any co-located production tier --
whereas MemTotal would overstate the real headroom on a shared host.
```

The identical `124` in all three rows is one unbreakable token, not a wrapping
failure. Every over-width line was enumerated:

```
--- COLUMNS=60 over-width lines ---
  3 [96] `…/scratchpad/example.env`
  5 [94] `…/scratchpad/w-out.env`
 56 [124] /workspaces/vbpub/.worktrees/…/host-setup/scripts/mdt-io-baseline.py
176 [84] ../../ciu/nyxloom-trove/handoffs/ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md)
--- COLUMNS=100 over-width lines ---
 43 [124] /workspaces/vbpub/.worktrees/…/host-setup/scripts/mdt-io-baseline.py
--- COLUMNS=200 over-width lines ---
(none)
```

All four are single paths. `break_long_words=False` is deliberate: a path split
across two lines cannot be copy-pasted, and these lines exist to be
copy-pasted.

At `COLUMNS=60` the prose itself reflows to 60 — confirming it is the real
width being used, not a fixed 80 with a 120 ceiling.

---

## 4. Help text

There is no single oracle for "reads better", so the check performed instead
was a **fact-preservation audit**: every distinct technical claim in the
original prose was enumerated from the pre-fix file and located in the new one.
All 27 survive.

IO device (1-3): the static caps are `dev.slice`'s `IOReadBandwidthMax` etc.;
they are the boot-window fallback until the baseline runs; auto-discovery is
`findmnt` against `/var/lib/docker` falling back to `/`; nothing discovered ⇒
static caps omitted. IO baseline (4-7): cache path; 30-day freshness; until
measured, `mdt-apply-dev-caps.sh` falls back to the tight `DEV_STATIC_*` caps;
~4 min, saturates the disk, quiet window, the script's own 5s Ctrl-C warning;
how to run it later; nonzero exit ⇒ statics remain in force. IO cap
percentages (8-10): percentages of the *measured* ceilings; the 60-80 band with
both halves of its reasoning (100% ⇒ everything queues behind the burst, the
exact stall the tiering prevents; below ~60% ⇒ throttling for no protective
gain); `DEV_IO_CAP_PCT` bounds the estate together and protects production from
the tier, not members from each other; `SWEEP_IO_CAP_PCT` protects members from
each other and is buildkit workers' only governance because Buildx placement
under `dev.slice` does not work at all. Per-tier memory (11-15): the live
host numbers; MemAvailable not MemTotal, and why; MemoryHigh soft, MemoryMax
leaning on swap; deliberate overlap rather than a 100% partition; the
no-swap ⇒ 0 case; interactive MemoryMin stable off MemTotal so a floor does not
shrink under load; background's relaxed swap and the swaps-slowly-vs-OOMs-
outright argument, sized against this host's own swap; buildkitd sized for
concurrent multi-project builds. Ceiling (16-24): hard floor vs MemoryLow's
soft protection; two places, sibling not nested, pinned equal; cgroup v2's
proportional redistribution of unclaimed protection and the resulting leak;
the converse failure (dev.slice lower ⇒ leaf silently inert); never two
independently-chosen numbers; opt-in per container via `governance.cgroup_parent`
and `governance.mem_min`; the ceiling alone protects nothing; the CIU-P50 link;
the leftover formula and the conservative suggestion; "generous here is actively
harmful, not merely wasteful" with its CGROUP-NOTES.md pointer; the
no-headroom case; empty is the default answer, not the suggestion. buildkitd
(25): CPUQuota's different empty-means convention, auto-detect `nproc-2`
floored at 1, `400%` = 4 cores, empty ≠ uncapped. Docker daemon (26-27): D-G7
default placement, merged-not-overwritten, restart-not-reload; D-G8 backstop
covers every transient scope regardless of slice, size generously, fail-open
backstop not a tier limit.

Nothing weakened, nothing dropped, nothing invented. What changed is sentence
length, ordering, and explicitness about *why*.

One point of care worth flagging: the plan's item-4 examples list "the
`--replace` rotation model" among the facts to preserve. No `--replace` prose
exists anywhere in the wizard — the nearest thing is `install.sh`'s `--force`
backup-and-reseed, which the wizard never described. Nothing to preserve, and
nothing was removed.

---

## 5 + 6. Executable, moved, and paths verified

Mode, as git records it and as it is on disk — matching `install.sh` and
`scripts/mdt-io-baseline.py`, exactly as the plan requires:

```
$ git ls-files -s mdt-host-setup-wizard.py install.sh scripts/mdt-io-baseline.py
100755 e1c9fdf81d796d1210b2ae03a224a77d0bbd35f4 0	install.sh
100755 fc8de7e930a7e0e189d89c7bd1038db79082cf50 0	mdt-host-setup-wizard.py
100755 9a8c56c60a54f2e93f84b942e9617d61e0e5b94c 0	scripts/mdt-io-baseline.py

$ ls -l mdt-host-setup-wizard.py install.sh scripts/mdt-io-baseline.py
-rwxr-xr-x mdt-host-setup-wizard.py
-rwxr-xr-x install.sh
-rwxr-xr-x scripts/mdt-io-baseline.py
```

The move itself was recorded as a rename with a mode change, from the commit's
own summary:

```
 create mode 100755 modern-debian-tools-python-debug/host-setup/mdt-host-setup-wizard.py
 delete mode 100644 modern-debian-tools-python-debug/host-setup/scripts/mdt-host-setup-wizard.py
```

**Real end-to-end run from the new location, with no path override except the
output, launched by absolute path from `cd /`** (so nothing can be resolving
relative to a convenient cwd), and executed *directly* rather than via
`python3` — exercising the shebang and the new mode at once:

```
$ cd / && /workspaces/…/host-setup/mdt-host-setup-wizard.py --output $S/e2e.env < /dev/null
exit=0
```

No path error, no `ERROR: template not found`, and it reached the end:

```
cache: `/var/lib/mdt/io-baseline.env`
`mdt-io-baseline.py` measures this disk's real IOPS and bandwidth ceilings with
…
/workspaces/vbpub/.worktrees/…/host-setup/scripts/mdt-io-baseline.py
…
Next step: sudo
```

Every constant resolved, from `cd /`:

```
HERE                         …/modern-debian-tools-python-debug/host-setup                       exists=True
HOST_SETUP_DIR               …/modern-debian-tools-python-debug/host-setup                       exists=True
DEFAULT_EXAMPLE              …/host-setup/host-setup.env.example                                 exists=True
DEFAULT_IO_BASELINE_SCRIPT   …/host-setup/scripts/mdt-io-baseline.py                             exists=True
DEFAULT_INSTALL_SCRIPT       …/host-setup/install.sh                                             exists=True
CIU_P50 (resolved)           /workspaces/vbpub/.worktrees/…/ciu/nyxloom-trove/handoffs/ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md   exists=True
```

`CIU_P50_RELATIVE_PATH` needed no change — **verified, not assumed**: it is
written relative to `host-setup/`, and `host-setup/` is now exactly where the
script lives, so it resolves to a file that exists.

Beyond static resolution, §1's RUN 4b is the load-bearing proof for
`DEFAULT_IO_BASELINE_SCRIPT`: with no `--io-baseline-script` passed, the wizard
imported the real `mdt-io-baseline.py` from the new `scripts/` path and called
its `cache_is_fresh()` successfully.

`install.sh`:

```
$ grep -n 'mdt-host-setup-wizard.py' install.sh
11:# interactively instead (mdt-host-setup-wizard.py, alongside this script) — sizes the tiers
75:  python3 "$HERE/mdt-host-setup-wizard.py" \
```

`$HERE` in `install.sh` is `dirname $0` — i.e. `host-setup/` — so
`$HERE/mdt-host-setup-wizard.py` is the file, confirmed present and executable.
`install.sh` itself was not executed (it requires root, `apt-get` and systemd,
none of which exist here); the check is on the resolved path and on `bash -n`.

`README.md`'s "What gets installed" row now reads
`mdt-host-setup-wizard.py` (a sibling of `install.sh`, not under `scripts/`,
because it is never installed onto the host), and mentions the standalone
`sudo ./mdt-host-setup-wizard.py` invocation the file's own header documents.

**Remaining old-path references, deliberately left alone.** A repo-wide grep
finds `scripts/mdt-host-setup-wizard.py` in five more places: the round-1 plan,
the round-1 LOG and REPORT (three hits), and the round-2 plan itself. All are
historical records of what was true when they were written; rewriting a shipped
report to match a later refactor would falsify a record, and the round-2 plan's
scope names only `README.md` for path updates.

---

## 7. `io.cost`

Informational per the plan. No code change, nothing measured, nothing claimed.
The gap remains open and documented in `CGROUP-NOTES.md`.

---

## Scope compliance

Touched: `host-setup/mdt-host-setup-wizard.py` (moved + rewritten),
`host-setup/install.sh` (the `--wizard` path and its header comment),
`host-setup/README.md` (one table row), `host-setup/scripts/check.sh` (one new
section). Plus this REPORT and its LOG.

Not touched: `scripts/mdt-io-baseline.py`, `CGROUP-NOTES.md`, `ciu/`, and any
`io.cost`/latency measurement work.

No BLOCKED condition was hit.
