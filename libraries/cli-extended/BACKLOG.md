# cli-extended backlog

Open usability gaps for the shared CLI contract layer. Items here are
proposals; they are not scheduled work.

## CLI-EXT-01 — expose the shared colour policy to consumer renderers

**Status:** Open  
**Type:** Feature  
**Area:** Output and terminal policy

`CliOutput.color_enabled(stream)` already resolves explicit `--color` /
`--no-color`, TTY state, and `NO_COLOR`. The documented contract currently
focuses on colour applied by `app.run()` to help, diagnostics, hints, and
progress. A CLI that owns a styled primary result (for example, a Rich or
Pygments renderer on stdout) has no documented contract for asking the shared
layer whether that destination should receive ANSI, so consumers can duplicate
the policy or rely on a method that is not named in the consumer guidance.

Promote a stream-specific colour-policy query as supported consumer API, either
by documenting and stabilizing `CliOutput.color_enabled(stream)` or by exposing
a small public helper with the same policy. Document how consumers pass that
result into their renderer, how stdout and stderr may differ, and the guarantee
that machine-readable JSON remains free of ANSI. Add a pasteable consumer
example and contract coverage when implemented.

**Provenance:** nyxloom's session extractor uses Rich and Pygments for its own
primary stdout rendering and currently implements terminal detection in its
CLI; this surfaced the adoption gap while reviewing whether it could reuse
cli-extended's existing TTY/`NO_COLOR` policy.

## CLI-EXT-02 — a shared `skills` verb group: install a tool's packaged agent skills into each harness

**Status:** Open  
**Type:** Feature  
**Area:** Command registration (shared verbs)

Each vbpub tool (ciu, cmru, assay, run-gate, nyxloom, pwmcp, cgprofile) is to
ship its agent skills (`<skill>/SKILL.md` trees) inside its own wheel as
package data, so the skill version always matches the installed tool version
and a consumer-only checkout (e.g. dstdns alone) needs neither a vbpub checkout
nor symlinks (dstdns D-647 #6). Wheels cannot run post-install hooks, so an
explicit install step is unavoidable. Every tool would otherwise hand-roll the
same verb, and seven copies of the same file-copy and drift logic would
diverge.

Proposed contract (one registration call per consumer, e.g.
`register_skills_verbs(app, package="ciu", resource_dir="skills")`):

- `<tool> skills install [--harness claude|agents|all] [--dest DIR] [--dry-run]`
  copies the packaged skills, located via `importlib.resources`, into
  `~/.claude/skills/` (Claude Code) and/or `~/.agents/skills/` (Codex and
  opencode). Neither harness reads the other's directory (verified
  2026-10-03). The copy is idempotent. Each copied skill carries a stamp
  (tool name + version + content hash), and a skill whose stamp names a
  different tool is refused, never overwritten.
- `<tool> skills list` shows each packaged skill with its installed state:
  current, stale (older stamp), modified locally (hash mismatch), or absent.
- `<tool> skills check` is the drift check. It exits non-zero when a skill
  is stale or absent, for use by `<tool> doctor` and by mdt's devcontainer
  finalize step, which runs `install` for every installed tool.
- `<tool> skills uninstall` removes only the skills stamped as this tool's.

Oracles:
- Installing twice changes nothing on the second run.
- An upgraded wheel makes `check` report stale and `install` refresh the skill.
- A locally edited skill is reported as modified and not overwritten without
  `--yes`.
- A skill stamped by another tool is refused.
- A controlled wrong implementation that copies without stamping must fail
  the foreign-tool refusal oracle.

**Provenance:** dstdns, 2026-10-03: D-647 #6, and the D-651 v8 interview,
where the operator chose `<tool> skills install` provided through
cli-extended for DRY. Per-tool adoption entries follow once this exists.
