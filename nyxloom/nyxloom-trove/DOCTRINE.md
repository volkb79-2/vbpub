# nyxloom — project delta to canonical DOCTRINE.md

> **Project sibling.** Canonical operational doctrine is `reference/DOCTRINE.md`;
> read it first. This file adds only the gotchas specific to *implementing
> nyxloom itself* — it refines, never replaces, the canonical rules.

## Logging: `structlog` has reserved keys that fail in two different ways

`nyxloom.log` wraps structlog, whose bound-logger methods are
`def info(self, event, **kw)` — so the FIRST positional is the message and it is
named `event`.

- **`event=` as a structured field is a hard `TypeError`** ("got multiple values
  for argument 'event'"), raised at call-argument binding — *unconditionally*,
  even when the level is suppressed, because the error happens before the
  method body's early-return guard ever runs. Name the field `event_type=`.
- **`level=` is silently clobbered** by the `add_log_level` processor. Rename it.
- **`log.trace` raises `AttributeError` before `configure()`** has run.
- **A `PrintLogger` writing to stdout corrupts CLI output** that is meant to be
  parsed.

None of these are visible on a fast diff read, and only some are caught by an
unconfigured-logging test. Re-gate authoritatively after touching any `log.*` call.

## `adapters.build_dispatch`: the prompt is on a hard argv budget

`build_dispatch` ends with `argv_max = route.argv_max or 1500; if len(prompt) >
argv_max: raise AdapterError`. Overflow is **not** a soft failure: the dispatch
never launches and the task strands at its pre-dispatch state, retrying every
reconcile tick.

Any text added to a role's prompt eats that budget, and the **FRONTIER_REVIEW
prompt already runs close to the cap with real deep-worktree paths**. A verbose
addition once pushed it to 1515 > 1500 and stranded seven behavioral tests at
`AWAITING_REVIEW` — while the adapters *unit* tests passed, because they use tiny
paths (`h.md`, `/wt`) and landed just under.

**Rules:** append optional prompt content **bounded** (skip it when it does not
fit; degrade, never strand), reserve headroom for long real-world paths, and
remember that **only the behavioral suite — which uses realistic paths — can catch
an overflow.** Unit tests will not.
