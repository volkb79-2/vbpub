# Installing and operating tls-edge

tls-edge is one Traefik v3 container (plus a docker-socket-proxy that denies POST) that terminates TLS for
every service on a host. It is installed as a self-contained instance: `get.py` downloads one release bundle,
verifies it, and builds a private venv from the **bundled** `ciu` and its dependency wheels. Nothing is fetched
from PyPI or GitHub after the download, and the instance runs from a path that never changes between updates.

All examples use `example.test`, a reserved placeholder; use your own domain.

## 1. What you get

```text
/srv/tls-edge-instance/            instance root (system scope)
  bin/ciu                          -> ../current/venv/bin/ciu (the bundled ciu)
  bin/tls-edge-installer           copy of get.py for update / status / rollback
  current                          -> releases/<tag>-<sha12>-<variant>   (atomic switch)
  releases/                        previous releases kept for rollback
  shared/answers.toml              your wizard answers (0600)
  stack/                           THE CIU ROOT: always run ciu from here
    ciu                            stub: execs the ciu pinned by `current`
    ciu-stack/                     templates (synced from the release; do not edit)
      ciu.toml.j2                  YOUR site override (seeded once, never overwritten)
    consumer.env                   TLS_EDGE_DOMAIN=<your domain>, for consumer stacks
    .git, .gitignore               a one-commit repository so ciu can run (see 9)
    .ciu/                          ciu state (survives updates)
```

Containers started by `ciu up` are `tls-edge-<id>-traefik` and `tls-edge-<id>-dockerproxy`; `<id>` is derived from
the path `/srv/tls-edge-instance/stack`, which is why that path is stable. The compose service name `traefik`
stays a DNS name on the networks. Public ports are exactly **80/tcp, 443/tcp and 8443 tcp+udp** (8443 is the
Mattermost Calls media pair; there is no alternate HTTPS port).

## 2. Requirements

- Linux x86_64 with Docker (the daemon socket must be usable by root), `git`, and `python3` 3.13 or 3.14.
- DNS: one record per service host name (or one wildcard record) pointing at this host.
- Firewall (host and provider): inbound 80/tcp, 443/tcp, 8443/tcp, 8443/udp.
- The bundle is per interpreter: `--variant py313` for Python 3.13 (Debian 13), `--variant py314` for 3.14.
  `python3 --version` tells you which; a mismatch fails closed at install (the wheels do not match).

## 3. Install

```bash
install -d -m 0755 /srv/tls-edge-instance
# Fetch get.py from the RELEASE TAG, not the moving `main` branch (it runs as root):
curl -fsSLO https://raw.githubusercontent.com/volkb79-2/vbpub/tls-edge-v3.0.0/tls-edge/get.py
sudo python3 get.py install --variant py313          # asks the wizard questions
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

Pin a release with `--version tls-edge-v3.0.0`. The wizard asks: `domain` (required), `acme_email` (optional),
`tls_mode` (default `acme-tls`), `dns_provider` (only for `acme-dns`), `calls` (default yes). For automation:

```bash
sudo python3 get.py install --variant py313 --unattended --answers answers.toml
```

```toml
# answers.toml
domain = "example.test"
acme_email = "ops@example.test"
tls_mode = "acme-tls"
calls = true
```

A required answer that is missing exits 2 before anything changes. `get.py` verifies the bundle SHA-256 and the
manifest (every file, every wheel), installs the wheels offline with `--require-hashes`, runs the adapter, and
only then switches `current`. Do not pipe `get.py` into `python3 -` if you want `bin/tls-edge-installer`: the
self-copy needs a file.

Verify the running edge:

```bash
cd /srv/tls-edge-instance/stack
sudo ./ciu env generate     # first time only; ciu up does it too. Needs the Docker group id:
                            # sudo DOCKER_GID=$(stat -c %g /var/run/docker.sock) ./ciu env generate
sudo ./ciu check            # all stages pass on a fresh install
sudo ./scripts/verify.sh --domain app.example.test
```

`verify.sh` discovers the container and volume names of a ciu instance by itself.

## 4. Configure domain and certificates

Edit `/srv/tls-edge-instance/stack/ciu-stack/ciu.toml.j2` (your override; defaults are in
`ciu.defaults.toml.j2`), then `sudo ./ciu up` again.

| `[tls_edge.tls] mode` | How | Port needed | Certificates |
|---|---|---|---|
| `acme-tls` (default) | TLS-ALPN-01 on :443 | 443 | one per host name |
| `acme-http` | HTTP-01 | 80 | one per host name |
| `acme-dns` | DNS-01 via a lego provider | none | per host, or one wildcard |
| `static` | certbot files under `cert_base` | none | yours |
| `dev` | self-signed (`scripts/dev-certs.sh`) | none | local CA |

**Per host vs wildcard.** The HTTPS entrypoint carries the resolver but no fixed domain list, so every consumer
router's `Host()` rule gets its own certificate the first time it is requested. A wildcard certificate exists
only with `acme-dns` and `[tls_edge.acme.dns] wildcard_main = "example.test"`.

**Staging CA** while testing: `[tls_edge.acme] ca_server = "https://acme-staging-v02.api.letsencrypt.org/directory"`
(switch back and remove the ACME volume contents before going live, or the staging account is reused).

**DNS credentials (`acme-dns`)** are CIU secrets, never literals in the override:

```toml
[tls_edge.tls]
mode = "acme-dns"
[tls_edge.acme.dns]
provider = "cloudflare"
wildcard_main = "example.test"
[tls_edge.secrets]
dns_api_token = { directive = "ASK_EXTERNAL:CF_DNS_API_TOKEN", mode = "0444" }
[tls_edge.acme.dns.environment]
CF_DNS_API_TOKEN_FILE = "/run/secrets/dns_api_token"
```

`ciu` asks for the token on `ciu up` (or reads `CF_DNS_API_TOKEN` from the environment). The seeded override has
this block commented, ready to uncomment.

**Port 80** is on by default (redirect and HTTP-01). `[tls_edge.ports] expose_http = false` turns it off;
`acme-http` then cannot work. **Calls**: `[tls_edge.calls] enabled = false` removes the 8443 pair.

## 5. Add a consumer

A consumer is a Compose stack on the external network `ingress_public` with Traefik labels. Start from
`consumer-examples/` (explained label by label in [consumer-examples/README.md](../consumer-examples/README.md)):

```bash
cd consumer-examples/pattern-a-public-only
docker compose --env-file /srv/tls-edge-instance/stack/consumer.env config   # offline check
docker compose --env-file /srv/tls-edge-instance/stack/consumer.env up -d
```

Rules: one host name per service, globally unique router/service names (`<stack>-<service>`), no published ports
80/443/8443 on the consumer. Mattermost Calls media attaches with the TCP and UDP routers of `pattern-e-media-udp`
(one media pair per edge).

## 6. Update

```bash
sudo /srv/tls-edge-instance/bin/tls-edge-installer update --variant py313
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

The update downloads and verifies the new bundle, builds its venv, swaps `current`, and syncs `stack/`. Files
you edited in the synced set (not `ciu.toml.j2`, which is not part of it) stop the update with exit 2 and a list;
keep host edits in `ciu.toml.j2` or pass `--overwrite-local` (the replaced files are saved under
`shared/overwritten/<UTC time>/`). `ciu.toml.j2`, `consumer.env`, `.ciu/`, volumes and the ACME account are never
touched. `status` shows the installed tag and any locally modified files.

## 7. Roll back

```bash
sudo /srv/tls-edge-instance/bin/tls-edge-installer rollback
cd /srv/tls-edge-instance/stack && sudo ./ciu up
```

`rollback` re-points `current` to the previous release, resyncs `stack/` and the stubs. The ACME volume is
independent of releases, so certificates survive.

## 8. Migrate from a hand-made deployment (PLAN, not executed)

State of the live host today (read only): compose project `edge-proxy` from `/root/tls-edge-LIVE`, containers
`edge-traefik` and `edge-dockerproxy`, volume `edge-proxy_acme-data` (holds `acme.json`: the ACME account and every
issued certificate), network `ingress_public` with consumers attached, `traefik_internal`, `cgroup_parent: edge.slice`,
ports 80, 443, 8443 tcp+udp plus the alternate port that is being removed. The published ports are the same on
old and new, so the two edges cannot run together: the cut-over is a stop plus start of about 15 to 30 seconds.

1. **Prepare (no downtime).** `install -d -m 0755 /srv/tls-edge-instance`, then install with the live values
   (domain, ACME email, mode `acme-tls`, calls yes). Do **not** run `ciu up`.
2. **Adopt what exists.** In `/srv/tls-edge-instance/stack/ciu-stack/ciu.toml.j2` set
   `[tls_edge] ingress_external = true`, `[tls_edge.acme] volume_name = "edge-proxy_acme-data"` and
   `[tls_edge.runtime] cgroup_parent = "edge.slice"`. The network and volume are adopted (`external: true`), never
   created or removed by the instance.
3. **Dry run.** `cd /srv/tls-edge-instance/stack && sudo ./ciu check && sudo ./ciu up --dry-run`. Compare the
   rendered ports, mounts and cgroup parent with `docker inspect edge-traefik`. Note: `ciu env generate` and
   `up --dry-run` create a Docker network and attach the calling container (ciu CIU-162); detach and remove it
   by its exact name afterwards.
4. **Back up.** Copy the ACME volume with one container and exact names:
   `docker run --rm -v edge-proxy_acme-data:/d:ro -v /root/backup:/b alpine tar czf /b/acme-data-$(date +%F).tgz -C /d .`
   and copy `/root/tls-edge-LIVE` aside.
5. **Cut over (downtime starts).** `cd /root/tls-edge-LIVE && docker compose -p edge-proxy down` (never `-v`; compose
   keeps `ingress_public` because consumers are attached and removes the unused `traefik_internal`), then
   `cd /srv/tls-edge-instance/stack && sudo ./ciu up`. Traefik re-reads `acme.json`: no re-issuance.
6. **Verify.** `scripts/verify.sh --domain <host>`, `curl -I https://<each consumer host>`, one certificate check per
   consumer host (not the default certificate, not expired), a Calls test from outside on 8443 tcp and udp, one real call.
7. **Rollback (any failure in 6).** `cd /srv/tls-edge-instance/stack && sudo ./ciu down` (no `-v`), then
   `cd /root/tls-edge-LIVE && docker compose up -d`. Volume and network were never touched and the ports are the same;
   downtime is seconds.
8. **Later cleanup** (after one soak and a renewal window): archive `/root/tls-edge-LIVE`; remove old compose-project
   leftovers by exact name. Keep `ingress_external = true` as long as the network predates the instance.

**Impact on consumers.** Networks and labels are unchanged. The container name changes from `edge-traefik` to
`tls-edge-<id>-traefik` (the service name `traefik` still resolves on the networks). In this repository nothing but
tls-edge's own scripts refers to `edge-traefik` by name (`scripts/verify.sh` discovers names;
`scripts/certbot-deploy-hook.sh` takes `TRAEFIK_CONTAINER`); a host script or runbook that runs
`docker exec edge-traefik ...` must be updated. The alternate HTTPS port is gone: confirm no consumer uses
`https://<host>:<former alternate port>` before the cut-over (none is known).

## 9. Notes and limits

- `stack/` is a git repository with one empty commit because ciu derives its identity from git and cannot run in a
  non-git root (ciu backlog CIU-163; the installer adapter creates it). Do not delete `.git`.
- `ciu env generate` and `ciu up --dry-run` create/attach a Docker network (CIU-162): run them on the host, not
  from a devcontainer you care about.
- The release publishes one bundle per interpreter; publishing goes through a project script until cmru has a
  variants handler (cmru KI-115).

### Releasing tls-edge (maintainers)

- **The version is set explicitly: `cmru release tls-edge --set-version 3.0.0`.** The project uses
  `bump = "conventional"`, but the two breaking changes of this release (alternate HTTPS port removed, container
  names `tls-edge-<id>-traefik`) were committed without a `!` / `BREAKING CHANGE:` footer, so the automatic bump
  would produce 2.1.0. The history is merged and is not rewritten; `--set-version 3.0.0` is the intent. From the
  next release on, mark breaking commits with `!` and drop the explicit version.
- Stage the bundled ciu reproducibly: `TLS_EDGE_CIU_VERSION=<x.y.z>` (or `scripts/stage-wheels.py --ciu-version`)
  refuses any other ciu release; without it the newest stable ciu is bundled. The staged ciu version and sha256 are
  printed: copy them into the release notes. `--allow-fresh` waives the 14-day rule and must be justified there.
- Tests: `run-gate tls-edge tests` (lane `tests` in `run-gate.toml`, tester-unified). A missing cmru/jinja2/docker
  fails the lane; only `TLS_EDGE_ALLOW_INSTALLER_SKIP=1` skips the installer test, deliberately, outside the lane.

### Trust and signing

Releases are currently **unsigned**: `get.py` ships with an empty `MANIFEST_PUBKEY`, so no minisign signature is
checked (as for pwmcp). What is verified: the bundle SHA-256 sidecar, then the in-bundle `manifest.json` (every
file and every wheel), then `pip --require-hashes`. The sidecar and manifest come from the same GitHub release as the
bundle, so they detect corruption and partial tampering but are **not an independent trust anchor**: whoever can
replace the release assets can replace all three. Likewise the first `curl` of `get.py` is trust-on-first-use
(HTTPS to GitHub; pinned to the release tag above, not to a hash). Because `get.py` then runs as root and starts
Docker, read it before running it (it is one file). Decision: a minisign
key (cmru signature policy) is wanted before tls-edge is used beyond the operator's own hosts; until it exists,
treat the release page as the trust boundary.

### Resource governance

The ciu default is `[tls_edge.runtime] cgroup_parent = ""` so an install works on hosts without the slice. On a host
that runs under the shared governance rule, set `cgroup_parent = "edge.slice"` in the override
(`ciu-stack/ciu.toml.j2`) at install time; this is mandatory migration step 2 in section 8, and the plain-compose
fallback (`edge-proxy/`) has no cgroup parent at all: add it by hand if you use that path.
- An update never changes the instance path, so the ciu identity, container names and volumes stay the same.
