# Plan — host-setup wizard round 2 (UX, correctness, and a real bug)

Status: carved, ready for implementation. Follow-up to
`plan-host-setup-wizard.md` (the original wizard, shipped, merged
`8fc9dccb`). This round: operator-requested UX fixes, one operator-found
live bug (`--with-baseline` re-prompt), one controller-found live bug
(`resolve_default()` silently defeats host-aware proposals), plus a gap in
`mdt-host-check.sh` found while verifying the applied config on a real
host.

## Context to read first

1. `host-setup/scripts/mdt-host-setup-wizard.py` — read the WHOLE file
   (847 lines) before touching anything; every item below names exact
   functions/line regions but the file is small enough to read in full
   and you need the whole picture for items 2/3/6 especially.
2. `host-setup/install.sh` — the `--wizard` flag block (near the top,
   `if [ "$WIZARD" = 1 ]`) and the `render()` function — item 6 changes
   the path this invokes the wizard at.
3. `host-setup/scripts/mdt-io-baseline.py` — `cache_is_fresh`, `main()`,
   `write_cache_atomic` — the wizard already reuses these; item 1's fix
   needs to know exactly what `main()` returns/does on success so the
   wizard can detect "the benchmark just ran, successfully, in this same
   session" without duplicating its logic.
4. `host-setup/README.md` and `host-setup/scripts/check.sh`
   (`mdt-host-check.sh`) — item 9 adds a new check there; read the
   existing check functions' shape (`OK`/`WARN`/`FAIL` verdict lines,
   e.g. the `memory_recursiveprot` check and the slice-unit checks) to
   match the house style exactly, don't invent a new verdict format.
5. `host-setup/CGROUP-NOTES.md` §"Per-container `memory.min` guarantees"
   — needed for item 9's exact invariant to check (`dev.slice`'s
   `MemoryMin` must equal `dev-memory_min_guaranteed.slice`'s own,
   exactly — "the one invariant that actually matters").

## Work

### 1. Stop re-asking `--with-baseline` when the baseline was just run

`step_io_baseline()` (search for `def step_io_baseline`) currently
returns `None` — `main()` has no way to know whether the benchmark
actually ran (and succeeded) during THIS invocation. Change it to return
a bool: `True` only when it actually invoked `mdt-io-baseline.py` and got
exit 0 (a fresh, successful measurement this session); `False` for every
other path (already fresh, declined, script missing, or a nonzero exit).
In `main()`, thread that result through and skip the `"Also pass
--with-baseline...?"` sub-question entirely when it's `True` — the
benchmark is already fresh, re-running it inside `install.sh` too would
just repeat the same ~4-minute disk-saturating measurement for nothing.
Print one line saying why it's being skipped ("baseline just measured
above, not re-running it") rather than silently omitting the question —
an operator scanning back through the transcript should be able to tell
this was a deliberate skip, not a missed prompt.

### 2. Input validation on every prompt

`ask()` and `walk_key()` (search for `def ask` / `def walk_key`)
currently accept literally any string. Add a `validate:
Callable[[str], str | None] | None = None` parameter to both — the
callable takes the raw (already-defaulted) value and returns `None` if
valid, or an error message string if not; `ask()` re-prompts on an
invalid answer rather than silently accepting it (loop, print the error,
ask again — never crash, never silently coerce). Wire a validator into
EVERY call site that has a real shape constraint:

- Size-string fields (every `MemoryMin`/`Low`/`High`/`Max`/`SwapMax`
  key, the memory-min-guaranteed ceiling): reuse `parse_size_to_kib`
  (already exists, already raises on garbage) as the validator's
  correctness check — don't write a second parser. Remember the
  memory-min-guaranteed ceiling's empty answer is a valid, first-class
  choice — the validator must accept `""` there, and ONLY there (or
  wherever else empty is genuinely meaningful — check each field's own
  semantics before deciding, don't blanket-allow empty everywhere).
- `DEV_IO_CAP_PCT`/`SWEEP_IO_CAP_PCT`: integer, and per the file's own
  documented 60-80% band reasoning (printed right above these prompts
  already) — decide whether to HARD-refuse outside some range or WARN
  and let the operator override (there's a real judgment call here:
  the band is a strong recommendation the file's own comments argue for
  at length, but "60-80" isn't a hard protocol requirement the way a
  size string's syntax is — don't silently pick one, say which you chose
  and why in your REPORT).
- `IO_DEV_PATH`: empty is valid (means "static IO caps omitted", already
  documented); if non-empty, at minimum reject anything that isn't an
  absolute path (starts with `/`) — don't try to verify the device
  actually exists on THIS machine (the wizard may reasonably be run to
  produce a config for a DIFFERENT host than the one it's running on;
  don't add a check that would wrongly refuse a valid answer just
  because this host's own filesystem doesn't have it).
- `DEV_BUILDKITD_CPU_QUOTA`: empty (auto-detect) is valid; otherwise must
  match systemd's `CPUQuota=` percentage shape (`N%`, integer N > 0).
- `DEV_BUILDKITD_IMAGE`: at minimum non-empty (an image reference of some
  kind) — don't over-engineer a real OCI-reference-format validator for
  this, non-empty is a reasonable bar.
- `DOCKER_DAEMON_CGROUP_PARENT`: non-empty, and per systemd slice-name
  convention should end in `.slice` — WARN (don't hard-refuse) if it
  doesn't, since a typo'd slice name fails OPEN per this whole project's
  own documented "graceful degradation" behavior (README/CGROUP-NOTES.md
  both cover this) rather than crashing anything — a wizard-level warning
  is a kindness, not a hard gate.

### 3. Terminal-width-aware output + highlighted code/paths

Every `print()` call with hand-wrapped, hardcoded-width prose (the whole
file is full of these — count them, there are dozens) needs to reflow
against the REAL terminal width, capped at 120 columns. Add one small
output layer, used everywhere instead of raw `print()`:

- Width: `min(shutil.get_terminal_size(fallback=(80, 24)).columns, 120)`.
  `shutil.get_terminal_size()` already handles "not a real terminal"
  (falls back to `COLUMNS` env var, then the given fallback) — don't
  hand-roll that.
- Reflow: `textwrap.fill`/`textwrap.wrap` (stdlib, don't add a
  dependency) against the resolved width. The existing prose is already
  written as sensible paragraphs with manual `\n`s inside triple-quoted
  strings — convert each print's TEXT (not its meaning) to reflow at
  the real width rather than the current hardcoded ~76-78 chars.
- Highlighting: pick ONE simple inline convention (e.g. backtick-quoted
  spans, matching this whole codebase's own Markdown convention already
  used in every comment/doc in this repo) and render them in a bold or
  cyan ANSI SGR sequence when color is appropriate. **Color must be
  conditional**: only emit ANSI codes when `sys.stdout.isatty()` is true
  AND the `NO_COLOR` env var (https://no-color.org convention, check
  estate precedent — grep for `NO_COLOR` anywhere else in this repo
  first) is unset — never emit raw escape codes into a pipe, a redirected
  file, or `capsys`-style test capture. Get this right and it also
  keeps every EXISTING manual verification transcript (the kind used in
  this package's own prior REPORT) readable as plain text when piped.
- Do this as ONE small helper module-level function (e.g. `out(text,
  **)`) that every step function calls instead of `print()` directly —
  not a per-call ad hoc reflow, or you'll get inconsistent wrapping.

### 4. Rewrite the help text for clarity — re-read it fresh, then improve it

Read every prompt's surrounding explanation text as if you'd never seen
this tool before (which you haven't — you're a fresh implementer). Where
something reads terse, assumes context the reader doesn't have yet, or
packs too much into one sentence, expand it — a LITTLE more verbose and
easier to follow, not a rewrite of the tool's actual technical claims
(every existing fact in the text — the 60-80% band reasoning, the
redistribution-leak mechanism, the `--replace` rotation model, etc. — is
correct and hard-won; don't weaken or drop any of it, just make it
easier to read). This is a judgment call across the whole file — there's
no single line-numbered fix list for this item; use your own read as the
first-draft feedback, matching the register/detail level the REST of
this codebase's comments already use (verbose-but-precise, not
dumbed-down).

### 5. Make the wizard executable

`chmod 755 host-setup/mdt-host-setup-wizard.py` (or `scripts/...` if you
do this BEFORE item 6's move — either way, the file must be `-rwxr-xr-x`
at its FINAL location once you're done, matching `mdt-io-baseline.py`
and `install.sh`'s own permissions in the same directory).

### 6. Move the wizard to be a sibling of `install.sh`

`git mv host-setup/scripts/mdt-host-setup-wizard.py
host-setup/mdt-host-setup-wizard.py`. Then fix everything that assumed
the old location:
- The wizard's own path constants (`HERE`, `HOST_SETUP_DIR`,
  `DEFAULT_IO_BASELINE_SCRIPT`, `DEFAULT_INSTALL_SCRIPT` — search for
  them near the top of the file): `HERE` now IS `host-setup/`, not
  `host-setup/scripts/`, so `HOST_SETUP_DIR = HERE.parent` is now WRONG
  (it would resolve one level too far up) — it should just be `HERE`
  itself. `DEFAULT_IO_BASELINE_SCRIPT` (still `mdt-io-baseline.py`,
  which is NOT moving, it stays in `scripts/`) needs to become `HERE /
  "scripts" / "mdt-io-baseline.py"` instead of the old `HERE /
  "mdt-io-baseline.py"`.
- `CIU_P50_RELATIVE_PATH` (search for it): its own comment already says
  "Relative to host-setup/" — since the wizard's NEW location IS
  `host-setup/`, this constant's VALUE does not need to change (it was
  already correct for a script living at `host-setup/`, not
  `host-setup/scripts/` — verify this reasoning yourself rather than
  assuming it, but don't "fix" something that's already right).
- `install.sh`'s `--wizard` block: `python3
  "$HERE/scripts/mdt-host-setup-wizard.py"` → `python3
  "$HERE/mdt-host-setup-wizard.py"`.
- `README.md`'s "What gets installed" table and "Quick start" section:
  any path reference to the old `scripts/mdt-host-setup-wizard.py`
  location.
- Verify with a real end-to-end run after the move (not just
  `py_compile`) — the path-resolution bugs this kind of move produces
  are exactly the ones static syntax checking can't catch.

### 7. `mdt-io-baseline.py` and `io.cost` — informational, no code change

Confirmed separately (not part of this package's own scope, no fix
needed here): the existing 4-point ceiling baseline's P99 latency figures
(`RIOPS_P99_US` etc.) are measured AT the saturating throughput ceiling,
which is a fundamentally different number from what `io.cost.qos`'s
`rlat`/`wlat` fields need (a latency SLA target under normal/target
load, used to trigger early throttling — using saturation-p99 there
would mean `io.cost` never intervenes until the device is already
maxed out). `CGROUP-NOTES.md`'s own "io.cost vs BFQ" section already
documents this gap accurately and it remains open, unmeasured, deferred
work — do not attempt to close it as part of this package.

### 8. Fix `resolve_default()` — an already-verified real bug, broader than it first looked

`resolve_default()` (search for `def resolve_default`) currently does:
```python
if key in cfg_current:
    return cfg_current[key]
```
`parse_env_file()` stores a key with an EMPTY string value exactly the
same as a key with a real one (`IO_DEV_PATH=""` parses to
`cfg_current["IO_DEV_PATH"] = ""`, key present). So "an already-configured
host's own value wins" (this function's own documented priority rule)
currently ALSO fires for "a key that's merely present-but-blank" — which
is the overwhelmingly common case for ANY host whose
`/etc/mdt/host-setup.env` was seeded from the example and never
hand-edited (the example ships most of its own optional fields empty).
**Confirmed live** on a real host: `IO_DEV_PATH` auto-discovered
correctly and was DISPLAYED (`auto-discovered: /dev/mapper/...`), but the
actual prompt default still showed `[<empty>]` and the written config
ended up with `IO_DEV_PATH=""` — the live auto-discovery was computed and
shown, then silently discarded. **This is not limited to `IO_DEV_PATH`**:
`step_memory_tiers()`'s own 7 calls to `walk_key(..., proposals[key])`
(positional, not keyword — grep the actual call sites, don't rely on a
keyword-arg grep to find them all) go through the exact same priority
logic with a real, non-None proposal every time; whether the bug
visibly manifests for any GIVEN field on any GIVEN host depends entirely
on whether that specific key happens to be present in that host's
current `/etc/mdt/host-setup.env` at the moment — which is why the
operator's own transcript showed the bug clearly for `IO_DEV_PATH`
(present, blank, from the shipped example) while ALSO showing what
looked like a working host-scaled proposal for
`DEV_BACKGROUND_MEMORY_HIGH` (that one likely just wasn't present as a
key in that particular host's file at that moment — verify this
yourself rather than trusting this account, by testing the actual
current code both ways).

**The fix**: an existing `cfg_current` value should only win over a real
proposal when it's genuinely non-empty:
```python
if key in cfg_current and cfg_current[key]:
    return cfg_current[key]
```
**Verify this doesn't regress the ONE case that's supposed to stay
empty**: `step_memory_min_guaranteed()` always passes `proposal=None`
(deliberately, per its own existing comment) — trace through the fixed
logic for that field with `cfg_current["DEV_MEMORY_MIN_GUARANTEED_CEILING"]
== ""` and confirm it STILL resolves to empty (it should: the `and
cfg_current[key]` check now fails, falls through to `if proposal is not
None` which is also false since proposal IS None, falls through to
`example_defaults.get(key, "")` which is also empty — same end state,
verify this chain for real rather than trusting the description). Also
verify the OPPOSITE case doesn't regress: a field where `cfg_current`
holds a REAL, deliberately-chosen non-empty value from a genuine prior
wizard run (not just the unedited example) still wins over a fresh
proposal — construct a scratch `host-setup.env` with e.g.
`IO_DEV_PATH=/dev/sdb` already set, confirm the wizard still defaults
to `/dev/sdb`, not the freshly auto-discovered device, when re-run
against it.

### 9. `mdt-host-check.sh` doesn't check the memory-min-guaranteed mechanism at all

Found while independently verifying the wizard's applied config against
a real host's live systemd/cgroupfs state: `mdt-host-check.sh`
(`scripts/check.sh`) checks `dev.slice`/`dev-interactive.slice`/
`dev-background.slice` (unit loaded, effective values) but has NO check
for `dev-memory_min_guaranteed.slice` at all — not whether the unit is
loaded, and NOT the one invariant CGROUP-NOTES.md itself calls "the one
invariant that actually matters": `dev.slice`'s own `MemoryMin` must
equal `dev-memory_min_guaranteed.slice`'s own `MemoryMin`, exactly, or
the whole mechanism silently leaks protection to the other two tiers.
Add a new check section (matching the existing `OK`/`WARN`/`FAIL`
verdict-line style used by the neighboring slice checks):
- If `dev-memory_min_guaranteed.slice` isn't loaded at all: `OK, inert
  (unit not installed or MemoryMin unset — the mechanism is opt-in, this
  is the default and expected state on most hosts)` — do NOT warn or
  fail just because a host hasn't opted in; that's the documented
  default.
- If it IS loaded with a real `MemoryMin`: read BOTH `dev.slice`'s and
  `dev-memory_min_guaranteed.slice`'s live `MemoryMin` (`systemctl show
  <slice> --property=MemoryMin`, matching how the existing checks
  already read other properties) and FAIL loudly if they don't match
  exactly — this is exactly the silent-leak failure mode the whole
  design exists to prevent, and nothing currently detects it.

## Scope / forbid

**Touch:** `host-setup/mdt-host-setup-wizard.py` (after the move — the
implementer performs the move as part of item 6), `host-setup/install.sh`
(the `--wizard` block's path only), `host-setup/README.md` (path
references only), `host-setup/scripts/check.sh`.

**Forbid:** `host-setup/scripts/mdt-io-baseline.py` (reuse it, never
modify its own logic), `ciu/` (any path — a separate repo subproject),
`host-setup/CGROUP-NOTES.md` (read for context, don't edit — nothing in
this package changes that design), any actual `io.cost`/latency-baseline
measurement work (item 7 is informational-only, explicitly out of
scope).

**BLOCKED rule:** if a named contract cannot be met as specified, or
scope requires a forbidden file, STOP — write `BLOCKED: <reason>` to your
LOG, commit, and exit. Do NOT improvise a workaround.

## Process requirements

- Fresh implementer, zero prior context beyond this document and the
  live repo.
- Real gate: `./run-gate.py smoke` (repo root) — `py_compile` over every
  tracked `.py` file. This subproject has no pytest suite by deliberate
  policy (`run-gate.toml`'s own header comment) — do not add one; verify
  by direct scratch runs instead (canned-input runs via `yes ""` or
  piped answers, same technique the original wizard package's own REPORT
  used), and paste the actual transcripts in your REPORT, not a
  description of what they'd show.
- Specifically verify, with real transcripts: item 1 (a real baseline run
  followed by the install offer shows NO `--with-baseline` sub-question);
  item 2 (at least one deliberately-invalid answer per validated field,
  showing the re-prompt); item 3 (a non-tty run — e.g. piped through
  `cat`, or via `capsys`-style capture — shows NO raw ANSI escape
  sequences in the output); item 6 (a full run after the move, from the
  NEW location, completes without a path error); item 8 (both the
  regression-guard case and the bug-reproduction-then-fix case, as
  described above); item 9 (both the inert-host case and a
  deliberately-mismatched-MemoryMin fixture, if you can construct one
  without needing real root — check how the EXISTING checks in this
  script are themselves tested/verified, if at all, and match that
  approach).
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01K6ZFTkbjEnh4haBGDUGyuJ
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop —
  a fresh adversarial reviewer verifies before any merge.
- **Host is shared with a production game server** — 8 cores: serial
  work under nice/ionice, no builds concurrent with anything else. This
  package does not need to run the real IO baseline benchmark itself
  (item 1's oracle can use a pre-seeded fresh cache file rather than
  actually running the 4-minute disk-saturating measurement) — don't run
  it unless you genuinely need to.
- Closing discipline: claim only what you ran, with the real
  command/transcript output — especially items 8 and 9, which are the
  two correctness-bearing (not just UX-polish) items in this package.
