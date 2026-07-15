# Historical branch disposition

Reviewed: 2026-07-15 against the operator-console decisions D-001 through
D-019 and current `main`.

This is the durable answer to “is there finished handoff work still waiting to
merge?”. There is not. Do not merge or cherry-pick the branches below. They are
kept only as provenance unless a maintainer later deletes them.

| Branch / tip | Disposition | Reason |
|---|---|---|
| `feat/groop-backlog-backfill` (`bfa8c60`) | Do not merge | Its B-005 through B-031 findings were reconciled into `docs/BACKLOG.md`; merging would only replay stale tracker text. |
| `feat/groop-web-ui-arch-reflection` (`51d26ad`, `0aaa1b7`) | Do not merge | The useful analysis is absorbed into D-001 through D-019. Its proposed contracts predate and conflict with the accepted token auth, same-origin hosting, projected query, persistent-history and route decisions. |
| `feat/groop-p82-repair-red-gate` (`aac9f89`) | Reject | P79 repaired the real recording error path and P84 made the dependency-complete zero-skip gate authoritative. This branch is superseded. |
| `feat/groop-p58-daemon-mcp-frontend-v3` (`d841a7a`) | Reject | Review found response-bound, redaction, selector, test and error-leak defects. The corrected v4 implementation is already on `main` (`7bf8389`, merged by `72e9c61`). |
| P51 alternatives (`740d8d4`, `034c54b`, `f829d17`, `26474c9`) | Reject; benchmark provenance only | The repaired selected implementation (`e87324c`, merged by `152b686`) is already on `main`. Combining alternatives would duplicate the daemon fan-out implementation. |

All other completed Groop feature tips inspected on 2026-07-15 are ancestors
of `main`. P52 (`5ef42bc`) and P57 (`78e83c`) are merged even though their old
REPORT headers originally said otherwise; those headers have been corrected.

Unreported P64, P65, P66, P68, P73, P77, P80, P81, P82 and P86 were handoff
specifications, not finished code awaiting integration. Their current queue
disposition is recorded in `docs/ROADMAP.md` and the handoff files themselves.
