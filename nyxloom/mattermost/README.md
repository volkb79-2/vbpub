# mattermost — nyxloom notification channel

Self-hosted [Mattermost](https://mattermost.com/) Team Edition + a **dedicated**
PostgreSQL, as one ciu-managed stack. Replaces [ntfy](../ntfy/) as nyxloom's
live notification channel (backlog NL-17, package nyxloom-P106). ntfy is kept —
stopped, and still selectable as a `notify.py` backend — not deleted.

Payloads are typed-fields-only per nyxloom SPEC §13: `notify.py`'s Mattermost
backend folds the note's `title`/`body`/`click`/`priority`/`tags` into the
incoming webhook's Markdown `text`, because that is the only field Mattermost
renders.

## Files

- `ciu.defaults.toml.j2` + `ciu.compose.yml.j2` — ciu v2 stack package.
- `docker-compose.yml` — pre-rendered copy for plain-compose deploys, carrying
  ABSOLUTE PHYSICAL host paths (DooD) **and hand-inlined governance caps**.
  Keep in sync with the templates; read its header before using it.

## Deploy (ciu-managed — the ONLY supported path)

```bash
cd /workspaces/vbpub && ciu up --dir nyxloom/mattermost -y
```

Containers: `nyxloom-prod-mattermost`, `nyxloom-prod-mattermost-db`.

> **Do not start this stack from `docker-compose.yml` and believe it is
> governed.** The plain-compose path receives no ciu overlay: that is exactly
> how the live ntfy container ran unconfined on this shared host for weeks
> (backlog NL-6). The fallback file inlines the same caps by hand; if you touch
> one, touch both.

## Provisioning (once, after the first start)

Local mode is enabled, so every step runs over an in-container admin socket —
no network-exposed admin API, no password typed from memory. ciu generates and
stores the admin password itself (`GEN_LOCAL`, project store, gitignored):

```bash
MM_ADMIN_PW="$(cat /workspaces/vbpub/nyxloom/.ciu/secrets/mattermost/admin_password)"

docker exec nyxloom-prod-mattermost mmctl --local user create \
    --email admin@nyxloom.local --username nyxloom-admin \
    --password "$MM_ADMIN_PW" --system-admin
docker exec nyxloom-prod-mattermost mmctl --local team create \
    --name nyxloom --display-name "nyxloom"
docker exec nyxloom-prod-mattermost mmctl --local team users add nyxloom nyxloom-admin
docker exec nyxloom-prod-mattermost mmctl --local channel create \
    --team nyxloom --name alerts --display-name "Alerts"
docker exec nyxloom-prod-mattermost mmctl --local channel users add nyxloom:alerts nyxloom-admin

# The incoming webhook nyxloom posts to. Its URL IS the credential.
docker exec nyxloom-prod-mattermost mmctl --local webhook create-incoming \
    --channel nyxloom:alerts --user nyxloom-admin \
    --display-name "nyxloom" --description "nyxloom operator notifications"
```

The last command prints an `Id:` — the webhook URL is
`<site-url>/hooks/<id>`. Rotate it by deleting and recreating the webhook
(`mmctl --local webhook delete <id>`); nothing else needs to change.

Read messages back from the host (useful as a delivery oracle):

```bash
docker exec nyxloom-prod-mattermost mmctl --local post list nyxloom:alerts --number 5
```

## nyxloom wiring (consumer project.toml)

```toml
[notify]
backend = "mattermost"          # explicit selector — NL-17
mattermost_username = "nyxloom" # posting identity (override must be enabled — it is)
mattermost_channel = "alerts"   # optional; the webhook already targets a channel
# webhook_url is NOT committed: the URL is the credential. Export it instead —
# NYXLOOM_WEBHOOK_URL wins over any toml value (config.py, same shape as NTFY_URL).
```

```bash
export NYXLOOM_WEBHOOK_URL="http://nyxloom-prod-mattermost:8065/hooks/<id>"
```

Anything that must reach Mattermost has to share a network with it. The stack
owns a private bridge (`nyxloom-prod-mattermost_internal`); attach a consumer
(or, for a one-off check from the devcontainer) with:

```bash
docker network connect nyxloom-prod-mattermost_internal <container>
# ... and afterwards:
docker network disconnect nyxloom-prod-mattermost_internal <container>
```

## Network exposure — DECIDED "internal-only", OPERATOR TO CONFIRM

`expose_public = false` in `ciu.defaults.toml.j2`. Today the stack publishes
**no host ports and joins no shared ingress network**: it is reachable only
from its own private bridge. That is the safe default for an endpoint nobody
has signed off on, and it is enough for nyxloom's own delivery path.

It is **not** enough for a phone, a desktop client, or a browser off this host
— the same reason ntfy had to be publicly reachable. Setting
`expose_public = true` publishes `mattermost.gstammtisch.dchive.de` through
tls-edge (same domain convention and router-label pattern as ntfy) and re-points
`MM_SERVICESETTINGS_SITEURL` at it. **That is an operator decision**: it creates
a real public endpoint on a real domain. The locked-down posture below applies
either way.

## Governance / safety posture

- Governance is ciu-resolved, not hand-written: the root's `[governance]`
  supplies `enabled`/`cgroup_parent = dev-background.slice`/`device`, and
  `[mattermost.governance]` layers `mem_limit = 2g`, `mem_swap_limit = 18g`,
  `cpus = 1.5` over it (S15.10 shallow merge — do NOT restate the inherited
  keys). Postgres narrows itself to 512m/4g/0.5 via author-set compose keys
  (S15.3). Neither service emits `cgroup_parent` — that author-precedence trap
  is what NL-6 was.
- Verified live (2026-09-08): both containers sit under
  `/dev.slice/dev-background.slice/`, with `memory.max` / `memory.swap.max` /
  `cpu.max` matching the declared values.
- Accounts: no open server, no self-signup — admin-created only.
- No file attachments, no plugin framework (the prepackaged playbooks/AI
  plugins would otherwise run their own processes inside this 2g cgroup), no
  marketplace, no personal access tokens, no outgoing webhooks, no slash
  commands, no telemetry, no email (no SMTP is configured).
- Postgres is dedicated to this stack, on a private bridge, never published,
  password from ciu's `GEN_LOCAL` store via `POSTGRES_PASSWORD_FILE`.
- The one deliberate concession: the app's DSN password arrives through ciu's
  `expose_env` escape hatch (S4.19), because the 11.x image ships no shell for
  the S4.18 entrypoint-wrapper pattern and Mattermost has no `*_FILE` form for
  its datasource. See the comment in `ciu.defaults.toml.j2`.

## Data / backup

All six volumes are stack-owned named volumes (ciu S6.7(b)) — the images
initialise ownership themselves, which a DooD bind mount cannot do without a
root helper. `ciu down` preserves them; **`ciu up --reset` and `ciu clean`
delete them** (S6.4), taking every account, channel, message and webhook with
them. Dump first:

```bash
docker exec nyxloom-prod-mattermost-db sh -c \
  'PGPASSWORD=$(cat /run/secrets/postgres_password) pg_dump -U mmuser mattermost' > mattermost.sql
```
