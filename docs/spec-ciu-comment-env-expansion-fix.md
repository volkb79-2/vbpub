# SPEC CIU-COMMENT-ENV — Fix env-expansion false-positive on TOML comment lines

**Spec ID:** CIU-COMMENT-ENV
**Repo:** `vbpub` (ciu) + a one-line `dstdns` escape workaround.
**Tracks:** surfaced during SW2 Tier-B (the SkyWalking deploy had to **bypass ciu** and manually
render the compose because of this bug — a regression from the SPEC F deployment model).
**Status:** ready. Small (S), offline.

> Self-contained. Grounded in the 2026-06-21 survey.

---

## Worktree directive
```
Worktree: create a git worktree for branch `feat/ciu-comment-env-fix` at
/tmp/vbpub-ciu-comment-env-fix and do all work there — never modify /workspaces/vbpub directly:
  git worktree add -b feat/ciu-comment-env-fix /tmp/vbpub-ciu-comment-env-fix main
```

## 1. The bug (verified)
`ciu`'s `expand_env_vars_or_fail` (`vbpub/ciu/src/ciu/config_model.py:75–97`) runs
`ENV_VAR_PATTERN.sub(...)` over the **entire post-Jinja2 rendered TOML text — including comment
lines.** dstdns's `ciu.global.defaults.toml.j2:697` contains `cmru-node-${value.node_id}` inside a
`#` **comment**. After Jinja2 renders the `.toml.j2`, that comment text remains; `expand_env_vars_or_fail`
then sees `${value.node_id}`, tries to resolve env var `value.node_id`, finds none, and **raises a
"missing required env var" error** — failing the render of the whole observability/skywalking deploy.

This is why the SW2 deploy bypassed ciu and rendered compose by hand. Any ciu-driven deploy of a
profile whose rendered TOML carries a `$…`/`${…}` token in a comment hits this.

## 2. Tasks

### Task A — Fix ciu (the real fix)
In `vbpub/ciu/src/ciu/config_model.py` `expand_env_vars_or_fail` (~:75–97): **do not expand env-var
patterns that occur inside TOML comments.** Strip/ignore comment content (everything from an
unquoted `#` to end-of-line) before applying `ENV_VAR_PATTERN.sub`, OR run expansion line-by-line and
skip the comment portion of each line. Be careful: a `#` inside a quoted string is NOT a comment —
use a minimal TOML-aware scan (track basic/literal string state on the line) rather than a naive
`split('#')`. Preserve the comment text verbatim in the output (only suppress expansion within it).
Add ciu unit tests: (i) `${VAR}` in a comment → no expansion, no error; (ii) `${VAR}` in a real value
→ still expands/fails as before; (iii) `#` inside a quoted value → not treated as a comment.
File it in `vbpub/ciu/KNOWN_ISSUES_TODO_BACKLOG.md` as fixed.

### Task B — dstdns escape (immediate workaround, separate small dstdns change)
In `dstdns/ciu.global.defaults.toml.j2:697`, change the comment so it carries no live env token —
e.g. rewrite `cmru-node-${value.node_id}` as `cmru-node-<value.node_id>` (or `$${value.node_id}`).
This unblocks ciu-driven skywalking/observability deploys even before the ciu fix ships, and is
harmless once Task A lands.

## 3. Live-stack tier
Tier-A (offline render/unit). Validate by running a ciu render of the dstdns observability profile and
confirming it no longer errors on the commented token.

## 4. Acceptance
1. ciu unit tests prove comment-embedded `${VAR}` is not expanded and does not fail the render, while
   real values still expand/fail correctly and quoted `#` is not mis-parsed.
2. A ciu render of dstdns's observability/skywalking profile succeeds end-to-end (no manual compose
   render needed) — restoring the SPEC F deploy path.
3. `KNOWN_ISSUES_TODO_BACKLOG.md` updated; dstdns comment escaped (Task B).
