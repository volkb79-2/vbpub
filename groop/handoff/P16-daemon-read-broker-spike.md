# P16 — Privileged daemon read broker for non-root full reads

**Cut:** v1.5/v2 foundation. **Depends:** P12 preferred, P19 useful. Branch:
`feat/groop-p16-daemon-spike`. Follow `groop/README.md` workflow protocol.

## Goal

Define and prototype the smallest safe daemon boundary for read-only collection,
history, root-only data reads, and future privileged providers. The first product
reason is practical: non-root users should be able to see daemon-approved full
read-only data without running the TUI as root.

## Scope — in

1. Design document:
   - local Unix socket path and permissions;
   - request/response frame stream shape;
   - read-only authorization model;
   - admin/mutation disabled-by-default model;
   - threat model for root-read exposure and Docker socket metadata.
2. Prototype:
   - daemon process can collect frames and serve them over a local socket;
   - client can request current frame or short live stream;
   - no mutating API.
3. History:
   - bounded in-memory or simple on-disk frame store sketch;
   - retention by age and size specified even if not fully implemented.
4. Tests:
   - socket protocol fixture tests;
   - daemon never exposes arbitrary file reads or arbitrary command execution.

## Scope — out

- BPF provider implementation.
- Admin actions.
- Web UI.
- System packaging/unit install automation beyond a draft unit file.

## Acceptance

- `docs/ARCHITECTURE.md` is updated with the chosen daemon contract.
- Prototype tests pass without requiring root.
- Standalone TUI behavior is unchanged.

## Notes

- Treat this as a spike with a narrow code footprint. The result may be a design
  and prototype, not necessarily a production daemon.
