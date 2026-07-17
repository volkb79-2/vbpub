---
kind: north-star
schema_version: 1
---

# nyxloom — north star

> Placeholder (PACKAGE F1). This is a MINIMAL-VALID spine doc, not nyxloom's
> real vision statement — that gets authored via the onboarding flow (F2,
> not yet built). F1's job is the doc structure + schema + validator; this
> file exists so `nyxloom lint`'s S1-S4 rules have something real (nyxloom's
> own trove) to run against.

nyxloom exists to run the "carve -> dispatch -> gate -> review -> merge"
loop for a project's backlog with as little synchronous human attention as
the project's own risk tolerance allows, without ever letting the daemon's
model of the world silently drift from what the git history actually says.

See `docs/spine-documents-spec.md` for the format contract this document
follows, and `docs/SPEC.md` / `docs/ARCHITECTURE.md` ([refs] in
`nyxloom-trove/nyxloom.toml`) for the current, detailed design.
