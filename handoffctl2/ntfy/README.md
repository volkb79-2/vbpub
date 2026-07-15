# ntfy — handoffctl2 notification channel

Self-hosted ntfy behind [tls-edge](../../tls-edge/) at
`https://ntfy.gstammtisch.dchive.de` (decided 2026-07-15). Safe-governance
baseline: **deny-all auth** (every publish/subscribe needs a user or token),
no signup, no attachments, bounded cache, conservative visitor limits — see
`server.yml`. Payloads are typed-fields-only per handoffctl2 SPEC §13.

## Files

- `server.yml` — ntfy config (mounted ro; the governance surface).
- `ciu.defaults.toml.j2` + `ciu.compose.yml.j2` — ciu v2 stack package.
- `docker-compose.yml` — pre-rendered copy for plain-compose deploys
  (DooD note: carries the absolute physical host path for the server.yml
  bind). Keep in sync with the template.

## Deploy (ciu-managed — primary since 2026-07-15)

```bash
cd /workspaces/vbpub && ciu up --dir handoffctl2/ntfy -y
```

Standalone-root ciu stack (own `ciu.global.defaults.toml.j2` + stack-local
`ciu.env`; server.yml arrives via the S5 configfile overlay — a raw relative
volume bind would resolve to the LOGICAL devcontainer path and the host
daemon would create an empty directory). Container: `handoffctl-prod-ntfy`.
Data volume `handoffctl-ntfy_ntfy-data` is external — survives `ciu --reset`.

Fallback (plain compose, pre-rendered with absolute physical binds):
`docker compose -f docker-compose.yml -p handoffctl-ntfy up -d`
(container then named `handoffctl-ntfy` — stop the ciu one first).

## Provisioning (once, after first start)

```bash
# admin (interactive password prompt — or NTFY_PASSWORD env for scripted):
docker exec -it handoffctl-prod-ntfy ntfy user add --role=admin admin
# handoffctl publisher: restricted user + access token, write-only on its topics
docker exec -it handoffctl-prod-ntfy ntfy user add handoffctl
docker exec handoffctl-prod-ntfy ntfy access handoffctl "handoffctl-*" write-only
docker exec handoffctl-prod-ntfy ntfy token add handoffctl   # -> tk_... for notify config
# your phone/browser subscriber (read access):
docker exec handoffctl-prod-ntfy ntfy access admin "handoffctl-*" read-write
```

Auth state lives in the `ntfy-data` volume (`auth.db`) — never in files here.

## handoffctl wiring (consumer project.toml)

```toml
[notify]
ntfy_url = "https://ntfy.gstammtisch.dchive.de"
ntfy_topic = "handoffctl-<project>"
# token: NTFY_TOKEN env for the daemon (Authorization: Bearer) — never commit.
```

Phone: install the ntfy app → add server `https://ntfy.gstammtisch.dchive.de`
→ log in (admin) → subscribe to `handoffctl-*` topics. iOS instant push needs
`upstream-base-url` (commented in server.yml; metadata-only relay to ntfy.sh).

## Governance notes

- `auth-default-access: deny-all`: anonymous requests can do nothing;
  rate limits are a second layer, not the gate.
- Attachments disabled by design (no `attachment-cache-dir`).
- Container: pinned image, non-root (1003), read-only rootfs, cap_drop ALL,
  no-new-privileges, `handoffctl.slice` cgroup, bounded json-file logs,
  no host ports (tls-edge routes via `ingress_public`).
