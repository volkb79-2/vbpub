# ciu-P49 — CIU-89 (multi-service probe-container resolution) + CIU-90
# (governance CPU-quota key)

**Input revision:** ciu `main` @ current HEAD (`f43da249` is the last commit
touching `ciu/`; the shared vbpub checkout may accumulate unrelated commits
outside `ciu/` while you work — that's normal, ignore them). Post
ciu-v7.10.1/ciu-P48. Both backlog entries were filed by dstdns 2026-09-02,
after P48 shipped; neither was touched by P46/P47/P48.

**Why bundled:** both are small, independent, filed-the-same-day fixes that
touch **disjoint files** (CIU-89: `provisioning.py` + `config_model.py` +
`docs/SPEC.md` S13.2 + `docs/CONFIG.md`; CIU-90: `governance.py` +
`docs/SPEC.md` S15 + `docs/CONFIG.md`) with no shared functions. Same shape
as bundling P48's CIU-87 fix onto other in-flight work — implement both,
one gate run, one review, one merge. **Do not let them interact**: if a
change to one touches a file the other's section doesn't name, stop —
you're doing something neither section asked for.

**Status:** both backlog entries (`KNOWN_ISSUES_TODO_BACKLOG.md`, search
`## CIU-89`/table row and `CIU-90`) already carry the live reproduction and
a proposed-fix sketch — read both in full before writing code. **This
handoff makes the design call the backlog entries left open** (each offered
two implementation routes without picking one) and gives you the concrete
shape to build, verified directly against current source. Where this
document's file:line citations differ from the backlog entries' own (which
were written from a live-use reproduction, not a source read), THIS
document wins.

---

## Part A — CIU-89: multi-service stacks break the `pg:`/`minio:` probe's
container resolution

### Root cause, verified against current source

- `_resolve_probe_container()` — `src/ciu/provisioning.py:401-447`. For a
  `pg:`/`minio:` ref, finds the stack(s) whose `provides` list carries the
  ref (`provider_index`), then calls `_stack_container_name(config,
  stack_path)` for each.
- `_stack_container_name()` — `src/ciu/provisioning.py:645-673`. When the
  selector contains `/` (a declared stack path, e.g. `infra/db-core`), it
  resolves the path against known declared paths and takes the path's own
  **final segment** (`db-core`) as the literal service-name argument to
  `container_name()`.
- `container_name()` — `src/ciu/deploy.py:165-178`. A **pure string
  template**, `f"{project}-{env_tag}-{service_name}"` — it does no
  cross-check against the stack's actual rendered compose service keys. It
  cannot tell `db-core` isn't a real service; it just builds the string.
- The `stacks`/`probe_graph` dict threaded through this whole call chain
  (`provisioning_graph()`, `src/ciu/deploy.py:605-643`) is built from each
  stack's **TOML config** (`{"requires": [...], "provides": [...]}`), not
  from its rendered compose file — **the actual compose service keys are
  not available anywhere in this call path today.** This matters for the
  design choice below.
- `docs/SPEC.md` S13.2 (line ~2065) currently states, unconditionally:
  *"Your Postgres/MinIO service may be keyed anything."* That's the
  contract CIU-89 shows is false for a multi-service stack whose directory
  basename isn't one of its own service keys — the fix must make this true
  again, not just patch the one dstdns case.

### Design call, made here — sibling `provides_container` override table,
**not** a live compose-service-list walk

The backlog entry offered two routes: (a) an explicit per-ref override, or
(b) walk the providing stack's own rendered compose service list for a
canonical-key match (`postgres`/`minio`), falling back to today's guess.
**Route (b) is not cheaply reachable from here** — as shown above, nothing
in this call path has the providing stack's rendered compose services, only
its TOML `requires`/`provides` lists; reaching them would mean rendering
(or separately parsing) an arbitrary OTHER stack's compose file from inside
a probe call, a materially bigger plumbing change with its own new failure
modes (a stack whose compose render itself fails now breaks an unrelated
stack's probe). **Build route (a):**

- New optional key, sibling to `requires`/`provides`, in the same root-key
  table S13.1 already reads them from:

  ```toml
  [db_core]
  provides = ["pg:db/dstdns", "pg:role/controller"]
  provides_container = { "pg:db/dstdns" = "postgres" }
  ```

  Keys are exact `provides` ref strings; values are the literal compose
  service key `_stack_container_name` should use INSTEAD of the path-
  basename guess, for that one ref only. A ref present in `provides` but
  absent from `provides_container` is completely unaffected — falls
  through to today's byte-identical basename-guess behavior. This is
  **purely additive**, not a breaking change.
- `_resolve_probe_container()` (provisioning.py:401-447): when resolving a
  `stack_path`'s container for a given `ref`, check
  `stacks[stack_path].get("provides_container", {}).get(ref)` first; if
  present, pass it straight to `container_name()` (skip
  `_stack_container_name`'s basename-guess path entirely for that ref).
  Only fall back to `_stack_container_name(config, stack_path)` when no
  override is declared. `_stack_container_name` itself is untouched — this
  is a new check ABOVE it, not a change to its own logic (the `stack:`-ref
  callers of `_stack_container_name` at `provisioning.py:722` don't go
  through `provides_container` at all — that key only applies to `pg:`/
  `minio:` probe resolution, not `stack:*` refs; don't wire it in there).
  You'll need to thread `provides_container` through the `stacks` dict
  shape (currently `{"requires": [...], "provides": [...]}`) — both
  `provisioning_graph()` (deploy.py:605-643) and whatever else builds this
  dict (grep for the other builder near deploy.py:760 -
  `stacks[rel] = {"requires": requires, "provides": provides}`) need the
  new key added, defaulting to `{}` when absent.
- `config_model.validate_stack_provisioning()` (`src/ciu/config_model.py`,
  ~line 1226) needs a new check: `provides_container`, if present, must be
  a table (dict) whose keys are each a string ALREADY present in that same
  stack's own `provides` list (a `provides_container` entry for a ref the
  stack doesn't even provide is a config error, not silently ignored — S3
  precedent: this repo's own "defaults are hazards" / loud-refusal
  convention), and whose values are non-empty strings. Raise with an
  `[S13.2]`-tagged `ValueError` alongside the existing requires/provides
  violations (same "list ALL violations, never partial" pattern already
  there).
- Docs: `docs/SPEC.md` S13.2 (~line 2065-2089) gets a new bullet describing
  `provides_container` right after the existing resolution-rules list, and
  the "Your Postgres/MinIO service may be keyed anything" sentence needs a
  clause acknowledging the basename-guess is a **default**, not a
  guarantee, with `provides_container` as the escape hatch when the guess
  is wrong. `docs/CONFIG.md`'s `requires`/`provides` section (~line
  702-725) gets a worked example — reuse this handoff's `db_core` example
  above, or build a closer one to the doc's existing `db_core`/`authentik`
  pair.
- **Do not touch dstdns.** Whether dstdns's own `infra/db-core` stack
  adopts `provides_container = { "pg:db/dstdns" = "postgres" }` is a
  dstdns-side follow-up once this ships — this package only has to make
  the mechanism exist and work, proven against a `test-repo/` fixture (see
  oracle below), not against a real dstdns checkout.

### CIU-89 behavioral oracle (controlled wrong implementation, satisfy
exactly)

- Build a `test-repo/` fixture: a stack directory whose own basename is
  NOT a compose service key it declares (mirror the real dstdns shape —
  e.g. a stack dir `infra/foo-core` providing `pg:db/bar` at the root
  table, with its compose service keyed `postgres`, not `foo-core`).
  Without `provides_container`, resolving `pg:db/bar`'s container must
  produce `container_name(config, "foo-core")` (today's wrong, unchanged
  behavior — pin this as a regression guard, not just describe it).
- Add `provides_container = { "pg:db/bar" = "postgres" }` to the same
  fixture stack. Resolving `pg:db/bar`'s container must now produce
  `container_name(config, "postgres")`.
- **Controlled wrong implementation**: deleting the `provides_container`
  check from `_resolve_probe_container` (or reverting
  `config_model.validate_stack_provisioning`'s new validation) must make
  the second case fail again (falls back to the wrong `foo-core` name).
  Write this as an actual mutation-style test, not prose.
- A single-`/`-free-selector stack (no path segment, existing convention)
  must be provably byte-identical before/after this package — add or point
  to an existing test that pins this.
- Also pin: a `provides_container` entry for a ref NOT in that stack's own
  `provides` list is rejected by `validate_stack_provisioning` with a
  clear `[S13.2]` error (not silently accepted, not silently ignored).

---

## Part B — CIU-90: governance never injects a CPU-quota key

### Root cause, verified against current source

- `GOVERNANCE_DEFAULTS` (`src/ciu/governance.py:61-`) has no CPU-shaped
  key. `INJECTED_KEYS` (line 260-266) lists exactly `cgroup_parent`,
  `mem_limit`, `memswap_limit`, `mem_reservation`, `blkio_config` — no
  `cpus`/`cpu_quota`/`nano_cpus`. `build_injections()` (line 929-, the
  actual per-service fragment-building loop at 977-1034) mirrors this:
  every one of those five keys has explicit author-precedence-checked
  injection logic; nothing for CPU exists at all.
- `resolve_config()` (line 346-408) validates `io_weight` as the closest
  existing precedent for a numeric governance value with an "0 = unset"
  sentinel (lines 399-407) — **mirror this exact validation shape** for
  the new key, not the string-literal `mem_limit`/`mem_swap_limit`
  pattern (those are always-on with a real default; CPU should not be).

### Design call, made here — additive `cpus` key, default **unset/
uncapped**, not a nonzero default

The backlog entry proposed `governance.cpu_limit` as the config-table key
name, sourced from either the modern single-key Compose form (`cpus`) or
the legacy two-key form (`cpu_quota`+`cpu_period`, which is what
`mem_swap_limit`→`memswap_limit`'s naming split exists to disambiguate
from). **Build the modern single-key form, and name the config key `cpus`
too** — `mem_limit`'s config-key-equals-compose-key precedent (no rename
needed, unlike `mem_swap_limit`) applies cleanly here since there's no
legacy-key collision to disambiguate.

**Default to unset (`""`), not a nonzero cap.** This is the load-bearing
decision in this section, made deliberately: D-264 PC-1's actual ask was
that a stack (`worker-io`) that WANTS a CPU quota has no way to configure
one — not that every already-governed, currently-uncapped service should
suddenly get throttled by an invented estate-wide default the moment this
ships. Unlike `mem_limit`/`mem_reservation` (which have always injected a
real default since governance's introduction — no regression risk from
keeping that), a nonzero default CPU cap introduced here for the first
time could silently throttle every currently-uncapped governed container
on upgrade, with no config change on the consumer's part — exactly the
"defaults are hazards" failure shape this estate's own AGENTS.md warns
against. Mirror `read_bps`/`write_bps`'s "0 = uncapped, no key injected"
convention (governance.py:90-96), not `mem_limit`'s "always a real
default" one. A stack that wants a cap (worker-io, per D-264) sets
`governance.cpus = "2"` explicitly — that config change is dstdns-side
follow-up, out of scope here, same as CIU-89's dstdns adoption.

### Concrete shape

- `GOVERNANCE_DEFAULTS["cpus"] = ""` (empty string sentinel — mirrors
  `cgroup_parent`/`device`/`baseline_path`, NOT the int-`0` sentinel
  `io_weight`/`read_bps`/`write_bps` use, because Compose's real `cpus`
  value is a fractional string, e.g. `"1.5"`, that an int can't represent
  cleanly).
- `INJECTED_KEYS` gains `"cpus"` (compose key name is literally `cpus`,
  no translation needed — unlike `memswap_limit`, add a one-line comment
  saying so to keep the "translates between config and compose key names"
  note at line 256-259 accurate, since it no longer applies to every
  entry).
- `resolve_config()`: after the `io_weight` block (~line 399-407), add
  validation mirroring its shape — `""` (unset) or a value that parses as
  `float(...) > 0`; raise `[S15.21]`-tagged `ValueError` otherwise (Docker
  itself would reject a garbage `cpus` value far from where the typo was
  made, same reasoning `io_weight`'s own comment states).
- `build_injections()` (~line 977-1034, alongside the existing
  `mem_limit`/`memswap_limit`/`mem_reservation` block at 985-997): `if
  "cpus" not in author_keys and config.get("cpus"): frag["cpus"] =
  str(config["cpus"])`. Add `cpus=` to the `notes` list (~line 1036-1038,
  mirroring the existing `mem_limit=`/etc. entries) — but only when
  actually set, not unconditionally (matches `blkio_config`'s own
  conditional-note precedent, not `mem_limit`'s unconditional one — check
  how the notes list already conditions on presence for the optional
  keys before copying the pattern).
- New `docs/SPEC.md` section **S15.21** (next unused number — S15.1
  through S15.20 are all taken, S15.21 is free) documenting the key,
  its default, the explicit-opt-in rationale above, and the
  `NanoCpus`/`docker inspect` verification an operator would use (mirror
  S15.14's `io_weight` section's shape/length, not S15.2's longer one).
  `docs/CONFIG.md`'s governance section gets the matching worked example
  (grep for where `mem_limit`/`io_weight` are documented there and match
  the style).
- Update the CIU-90 backlog entry to FIXED with the actual mechanism
  (not the proposed-contract language it currently carries).

### CIU-90 behavioral oracle (controlled wrong implementation, satisfy
exactly)

- A `test-repo/` fixture service with governance enabled and no
  author-set `cpus`/CPU compose key, `governance.cpus` left unset (default
  `""`): `build_injections()`'s output for that service must NOT contain
  a `cpus` key at all (uncapped stays uncapped — pin this as a regression
  guard, this is the whole point of the "no silent default" decision
  above).
- Same fixture with `governance.cpus = "2"` configured: the injected
  fragment MUST contain `cpus: "2"` (or the numeric type you chose —
  be consistent, and say in your REPORT which you picked and why).
- An author-set `cpus:` key already in the service's own compose block
  must be left untouched (governance never overrides an author-set key —
  the existing S15.3 precedence rule, already tested for the other four
  keys; add the CPU case alongside them, don't invent new test
  infrastructure for it).
- `governance.cpus = "0"` and `governance.cpus = "-1"` must both raise the
  new `[S15.21]` `ValueError` from `resolve_config()` (0 and negative are
  not valid Compose `cpus` values — only `""` means "unset").
- **Controlled wrong implementation**: deleting the new `build_injections`
  conditional (or the `resolve_config` validation) must make the second
  oracle above fail again (no `cpus` key injected despite explicit
  config), and the fourth fail to raise. Real mutation-style tests, not
  prose.
- **Live verification, mirroring the CIU-90 backlog entry's own method**:
  after the fix, bring up a real governed test-repo service with
  `governance.cpus` configured, and confirm via `docker inspect
  --format '{{.HostConfig.NanoCpus}}'` that Docker actually applied it —
  the backlog entry's whole finding was a `docker inspect` value, not a
  config-shape assertion; close the loop the same way.

---

## Process requirements (same convention as P46/P47/P48)

- Fresh implementer, zero prior context beyond this document, both backlog
  entries in full, and the live repo.
- **Neither fix is breaking.** CIU-89's `provides_container` and CIU-90's
  `cpus` are both new, optional, additive keys — existing configs are
  byte-identical in behavior. Frame commits as `feat(ciu):` (new
  capability), not `fix(ciu):` — despite both riding on a filed "defect,"
  what ships is new opt-in mechanism, not a correction to previously-wrong
  default behavior for existing configs. Get this framing right in
  `CHANGES.md` — it's what CMRU's bump-type proposal will key off.
- **Real gate required**: `./run-gate.py ciu` (`--worktree <path>` if
  you're in an isolated worktree). Read the verdict in a separate step,
  never off a piped tail.
- Update both backlog entries (`KNOWN_ISSUES_TODO_BACKLOG.md`, `## CIU-89`
  table row and the `CIU-90` table row — note CIU-89 currently has no
  detailed `## CIU-89` section, only a table row; leave that structure as
  is, just flip status to FIXED with the real mechanism) to FIXED with the
  actual shipped mechanism and file:line citations from your own diff, not
  the proposed-contract language they currently carry.
- LOG/REPORT: `nyxloom-trove/reports/ciu-P49-{LOG,REPORT}.md`, same
  convention as P46/P47/P48 — LOG per commit (self-hash rule), REPORT with
  per-oracle evidence for BOTH parts separately (don't conflate CIU-89's
  oracle evidence with CIU-90's).
- Checkpoint clause: ARM at ~120k context or ~60 tool calls (whichever
  first), CUT at the next coherent boundary (green gate > commit >
  LOG/REPORT write > edit-cluster end; never on a red gate), repeat every
  ~40-55 calls, stop when <~40 calls remain. At the cut: continuation
  brief to a durable file + a self-authored `/compact`-style retention
  prompt, commit, stop.
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_019gR96GbCoafYzMiW72n6hS
  ```
- **Do not merge to `main`.** Commit in your worktree/branch and stop — a
  fresh adversarial reviewer verifies before any merge (fresh implementer
  → real gate → fresh reviewer → merge on ACCEPT, same as P46-P48).
- **Host is shared** — see `host-shared-with-production-load-rule`: serial
  pytest under nice/ionice, ONE gate container at a time, `docker update
  --cpus=3` right after launch, no builds concurrent with suites.
- Closing discipline: claim only what you ran, with the real numbers/
  outputs from both parts.
