# nyxloom project standard — folder structure & document conventions

Everything nyxloom manages in a project lives under **`.nyxloom/`**. The dotted,
tool-named namespace is deliberate: it signals at a glance that these files are
**managed** and must follow the conventions below — unlike a bare `handoff/`
folder at the repo root, which reads like ordinary project content.

## Recommended directory structure

```
<project>/
  .nyxloom/
    project.toml     # config (schema-validated by `nyxloom lint`)
    STANDARD.md      # this file — conventions, copied per project
    handoffs/        # work packages: P<NN>-<slug>.md, YAML frontmatter, lint-gated
    reports/         # P<NN>-LOG.md (during) / P<NN>-REPORT.md (after)
    decisions.md     # decisions inbox — product calls awaiting the user
    archive/         # merged handoffs + reports land here (docs/ stays pristine)
```

`nyxloom init <project>` scaffolds this (proposed — see OPEN-WORKSTREAMS).

## Document conventions (what "managed" means)

- **Handoff naming:** `P<NN>-<kebab-slug>.md` (e.g. `P23-notify-two-channel.md`).
  `<NN>` is a zero-padded ordinal, unique within the project.
- **Frontmatter is mandatory + schema-validated.** Every handoff opens with a
  YAML frontmatter block (`schema_version`, `id`, `project`, `title`, `tier`,
  `scope.touch`, `oracles`, `gates`, `escalate_if`, …) validated against
  `schemas/handoff-frontmatter.schema.json`. `nyxloom lint` rejects a handoff
  whose frontmatter is missing or invalid — that lint IS the managed-folder
  guard.
- **Reports pair with handoffs:** `P<NN>-LOG.md` (resumability, written during)
  and `P<NN>-REPORT.md` (evidence, written after). Never trusted at face value —
  the reviewer verifies against actual git state (see the review contract).
- **Decisions inbox:** product calls (not engineering) get an entry in
  `decisions.md` with a stable `D-<NNN>` id; a blocking one becomes
  `depends_on: [D-NNN]` in the affected handoff. Agents keep working around it.
- **Lifecycle:** carve → dispatch → gate (in the project's declared gate, never
  the cockpit) → frontier review → merge (`--no-ff`, manual) → **archive**
  (move the handoff + reports to `.nyxloom/archive/`).
- **The gate is authoritative, and it is never the devcontainer.** Each project
  declares its gate in `project.toml [gates.*]`; for the vbpub family that is
  the `tester-unified` container.

## Migration (existing projects on `handoff/`)

Projects currently using a root `handoff/` (nyxloom, groop, dstdns) keep working
— `project.toml` globs are configurable. To adopt the namespace:
`git mv handoff .nyxloom/handoffs && git mv .nyxloom/handoffs/reports
.nyxloom/reports`, then repoint `handoff_globs`/`reports_dir`. Do it as one
deliberate pass per project (the handoff prose cross-references paths).
