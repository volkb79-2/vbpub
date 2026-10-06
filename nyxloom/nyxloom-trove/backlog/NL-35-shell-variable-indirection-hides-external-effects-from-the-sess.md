---
kind: backlog-entry
schema_version: 1
id: NL-35
title: "Shell-variable indirection hides external effects from the session ledger (shellcmd residual)"
status: open
type: "bugfix"
severity: "low"
component: "session_extract"
provenance: "nyxloom SUCCESSOR-2 package 2026-10"
filed_by: "Claude Sonnet (SUCCESSOR-2 implementer)"
filed_date: "2026-10-06"
---

**Observed mechanism (source-grounded).** `session_extract/shellcmd.py` classifies Bash segments mechanically (quote-aware tokenizer plus regexes, no execution). A segment whose command head is hidden behind shell-variable indirection (`NC="python3 nc.py"; $NC ... delete`, `$SSH host rm ...`), a shell function or alias, `eval`, a script that pushes internally, a command built by command substitution, or a mutating flag placed inside a quoted `curl` argument is NOT recognised as an external effect. The whole-session ledger's "external effects" bucket (the "already done, verify state, do not repeat" list for a successor) therefore misses it. The module docstring names the residual; no oracle pins it.

**Why filed here.** The detector and its default patterns are nyxloom's (`DEFAULT_EFFECT_PATTERNS`); a user-supplied `--effect-pattern` can add a pattern but cannot see through variable expansion.

**Proposed contract.** Either (a) a bounded static resolver for the simple, common case (a same-command-line `VAR=...` assignment later used as `$VAR` head, within one Bash call), or (b) an explicit "possible effect, head unresolved" marker in the ledger for any segment whose head is a `$VAR`/`$(...)`/backtick expansion, so a miss becomes a visible "unknown" rather than silence. Prefer (b) first: cheap, honest, no false confidence.

**Oracles.** Fixture commands `NC="python3 nc.py"; $NC snapshots delete 1` and `ssh host 'sudo $CMD'` must either resolve to an effect or print the unresolved marker; a controlled wrong implementation that drops unresolved heads silently must fail the test.

**Spec owner.** `session_extract/shellcmd.py` module docstring (KNOWN RESIDUALS) and `session_extract/README.md`.
