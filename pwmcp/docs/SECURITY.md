# Security

## Why Offload the Browser?

Running a browser (Chromium) inside a devcontainer or CI job is a security liability:
- The browser process has broad OS access, large attack surface, and often runs as the container user
- Browser binary versions diverge across environments, causing flaky tests and reproducibility gaps
- Installing browsers in every job image bloats images and slows cold starts

PWMCP solves this by running the browser in a dedicated, hardened container. The browser is isolated from the consumer's code and filesystem. Consumers connect over a protocol boundary (WebSocket / MCP), not via in-process calls.

## Browser Isolation Hardening

The following hardening is applied in **both deployment modes** (internal and external). It is not optional — it is the justification for offloading the browser in the first place.

| Control | Setting | Rationale |
|---|---|---|
| Non-root user | `user: 1000:1000` | Official Playwright base image ships UID 1000; no reason to run as root |
| Drop all capabilities | `cap_drop: [ALL]` | Chromium does not need any Linux capabilities; remove the entire set |
| No privilege escalation | `no-new-privileges: true` | Prevents setuid/setgid bits from granting elevated privileges at runtime |
| Shared memory | `shm_size: 2gb` | Chromium writes renderer frames to `/dev/shm`; too small causes crashes |

These are set in `ciu.compose.yml.j2` under `[pwmcp.hardening]` and cannot be overridden per-mode.

## Internal Mode Access Control

In internal mode the Docker network is the access control boundary. Services are not exposed outside the project network. Any container on the same network can connect — no additional auth is applied. This is appropriate for dev and CI where the network is already controlled.

Do not expose `pwmcp` ports to `0.0.0.0` in internal mode.

## External Mode Access Control

In external mode, access is controlled by:

1. **TLS termination** at Traefik (tls-edge). All traffic is encrypted in transit.
2. **Per-route basicAuth guard** (`guard_enabled = true` by default). Traefik enforces HTTP Basic Auth before forwarding the request. Consumers must supply credentials.

The guard htpasswd hash is stored in `ciu.toml.j2` (the operator's override file, not committed to shared repos with the hash in plaintext). Use `ASK_EXTERNAL:PWMCP_GUARD_HTPASSWD` as a placeholder when generating env; supply the real hash in the deployed override.

The guard covers both the Playwright WebSocket route and the MCP HTTP route independently.

## Credential Hygiene

- The guard htpasswd hash is a bcrypt hash (`htpasswd -nbB`), not a plaintext password
- Rotate the guard secret by regenerating the htpasswd hash and redeploying
- Do not commit the real htpasswd hash in shared version control
- Traefik access logs do not include Authorization header values by default

## Network Isolation Summary

```
internal mode:
  [project network] — only containers on this network can reach the services
  no ports published to host (unless expose = true for dev convenience)

external mode:
  [project network] — same as internal
  [ingress_public]  — tls-edge/Traefik is the only external entry point
  Traefik enforces TLS + basicAuth before forwarding
```
