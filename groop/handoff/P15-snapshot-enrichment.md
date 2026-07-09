# P15 — Incident snapshot enrichment and UX

**Cut:** v1.5 stabilization. **Depends:** P10, P13 preferred. Branch:
`feat/groop-p15-snapshot-enrichment`. Follow `groop/README.md` workflow
protocol.

## Goal

Make incident bundles more useful for postmortems by collecting fresh metadata
at snapshot time and improving operator feedback.

## Scope — in

1. Fresh provider metadata:
   - systemctl show for the selected entity/unit where resolvable;
   - Docker inspect summary for Docker-backed entities;
   - network and DAMON provider status already present on the frame.
2. Snapshot status UX:
   - nonblocking or clearly bounded TUI feedback;
   - success path includes bundle path;
   - failures explain which source failed while still preserving partial bundle
     value when safe.
3. Privacy controls:
   - document redaction behavior;
   - add tests for env/label/path redaction choices;
   - avoid adding arbitrary file content.
4. Inspection improvements:
   - `groop snapshot inspect` should report hash failures, redaction state,
     frame count, entity, timestamp, and notable included source files.

## Scope — out

- Full file/log/content browser.
- Upload/sharing.
- Arbitrary command execution.

## Acceptance

- Tests prove systemctl/docker metadata are included from fixtures/injections.
- Redaction tests cover sensitive Docker fields.
- Existing snapshot bundle hash validation stays green.
- `docs/OPERATIONS.md` explains snapshot location and redaction.

## Notes

- Do not make snapshot creation depend on Docker daemon availability. Missing
  providers should degrade into manifest/provider status, not failure.
