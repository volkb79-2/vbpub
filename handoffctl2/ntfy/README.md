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

## Deploy (plain compose, current path)

```bash
cd /workspaces/vbpub/handoffctl2/ntfy
docker compose -p handoffctl-ntfy up -d      # needs ingress_public (tls-edge up first)
```

ciu path (once `ciu --generate-env` has refreshed the stale vbpub ciu.env):
`ciu up --dir handoffctl2/ntfy -y` from `/workspaces/vbpub`.

## Provisioning (once, after first start)

```bash
# admin (interactive password prompt — or NTFY_PASSWORD env for scripted):
docker exec -it handoffctl-ntfy ntfy user add --role=admin admin
# handoffctl publisher: restricted user + access token, write-only on its topics
docker exec -it handoffctl-ntfy ntfy user add handoffctl
docker exec handoffctl-ntfy ntfy access handoffctl "handoffctl-*" write-only
docker exec handoffctl-ntfy ntfy token add handoffctl   # -> tk_... for notify config
# your phone/browser subscriber (read access):
docker exec handoffctl-ntfy ntfy access admin "handoffctl-*" read-write
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
