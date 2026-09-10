# Consuming this Mattermost instance — a guide for producers

This is for anything OUTSIDE this repo that needs to post into (or, later,
read from) nyxloom's Mattermost instance — `scripts/netcup` /
`scripts/debian-install-v2` today, possibly others later. If you're working
*inside* this stack (provisioning, hardening, hostdir layout), read
`README.md` instead — this file only covers the producer/consumer contract.

## The instance

- Public URL: `https://mattermost.gstammtisch.dchive.de` (valid Let's Encrypt
  cert, HTTP/2, rate-limited).
- Self-hosted Mattermost Team Edition, dedicated to nyxloom. No marketplace,
  no plugins, no OAuth2 provider, no email/SMTP, no self-signup.
- Every producer gets its **own account**, scoped to exactly the channel(s)
  it needs — never a shared credential, never `nyxloom-operator`/
  `nyxloom-admin` (those are human/bootstrap accounts and are never handed
  out).

## Current producer accounts

| Account | Channel | Credentials provisioned | Used by |
|---|---|---|---|
| `nyxloom-installer` | `installs` (public) | webhook (`installer_webhook_url`) + PAT (`installer_pat`) | `scripts/netcup` / `scripts/debian-install-v2` host-install progress |
| `nyxloom-daemon` | `alerts` (private) | webhook (`daemon_webhook_url`) | nyxloom's own daemon — not a 3rd-party consumer, listed for completeness |
| `nyxloom-intake` | `intake` (private) | webhook + PAT (`intake_webhook_url` / `intake_pat`) | nyxloom's own feature-intake chatbot (B9) — internal, not a 3rd-party consumer |

If you're adding a **new** external producer, you almost certainly want the
same shape `nyxloom-installer` has: its own account, its own channel (or a
shared one if it genuinely belongs there), a webhook for writing. See
"Adding a new producer" at the bottom — don't reuse an existing account.

## How to post: use the webhook, not the PAT

**The webhook is the preferred, default path for every producer here.**
Plain `HTTP POST` of JSON, no auth header, no token to manage, no client
library:

```bash
curl -sS -X POST "$WEBHOOK_URL" \
  -H 'Content-Type: application/json' \
  -d '{"text": "install: partitioning complete"}'
```

- Payload is `{"text": "markdown message"}` only. Don't set `channel` — the
  webhook is already bound to one channel at creation; overriding it is
  possible but pointless here.
- Post milestones (start, key stages, done/failed) — not a log stream, not
  per-line stdout.
- A failed POST (network error, 4xx/5xx) must **not** fail whatever you're
  doing. This is a status channel, not a control dependency: warn and move
  on.
- The credential is a **file path**, not a value to hardcode: read it from
  the environment at call time (see "Getting your credential" below), and
  assume it can be rotated out from under you.

### Why the webhook and not the PAT, when both exist

A Mattermost incoming webhook is **post-only and bound to one channel at
creation** — nothing else. A leaked webhook URL can spam that one channel
and do nothing more.

A PAT is a **bearer credential that inherits its account's full
permissions** (Mattermost Team Edition has no per-token scoping — this was
measured, not assumed, during nyxloom-P109). Today `nyxloom-installer` is
scoped to `installs` only, so a leaked PAT is currently no more dangerous
than the webhook in terms of blast radius — but it CAN read (channel
metadata, message history, member lists), which the webhook structurally
cannot do at all, and every new capability an account gains later widens
what a leaked PAT can do without any code change on your end. Preferring
the narrower credential (webhook) whenever it does the job is the same
least-privilege call the README makes for every other producer on this
instance.

**Use the PAT only when the job genuinely needs the REST API** — plain
status posts still don't; keep using the webhook for those. The PAT exists
for the one thing a webhook structurally cannot do: a Mattermost incoming
webhook **cannot carry file attachments at all** (verified against
Mattermost's own webhook docs), so attaching a file (an install log, say)
has to go through the PAT + the REST Files API.

## The PAT and file attachments — both live (nyxloom-P111, 2026-09-09/10)

`installer_pat` is minted and stored (same store, same account, same
`installs` scope as the webhook — see "Getting your credential"). It
authenticates as a normal Mattermost bearer token:

```bash
curl -sS -H "Authorization: Bearer $PAT" \
  "https://mattermost.gstammtisch.dchive.de/api/v4/teams/name/nyxloom/channels/name/installs"
```

`MM_FILESETTINGS_ENABLEFILEATTACHMENTS` is **on**. Uploading and attaching a
file is two calls — upload, then attach the returned file id to a post —
and both were verified end-to-end against the real public endpoint (not a
mock, not an internal-network shortcut):

```bash
# 1. Upload — returns a file id.
curl -sS -H "Authorization: Bearer $PAT" \
  -F "channel_id=$CHANNEL_ID" -F "files=@install.log" \
  "https://mattermost.gstammtisch.dchive.de/api/v4/files"

# 2. Attach it to a post.
curl -sS -H "Authorization: Bearer $PAT" -H 'Content-Type: application/json' \
  -d "{\"channel_id\":\"$CHANNEL_ID\",\"message\":\"install finished\",\"file_ids\":[\"$FILE_ID\"]}" \
  "https://mattermost.gstammtisch.dchive.de/api/v4/posts"
```

A file uploaded under one account's PAT and attached to a post is visible
to anyone who can read that channel — same read boundary as everything
else here (`installs` membership), nothing extra to configure.

Uploaded files land in the `mattermost-data` **named volume** (not a bind
mount like config/logs) — there's nothing to browse from the host for them
yet. See `ciu.defaults.toml.j2`'s `enable_file_attachments` comment if that
ever needs to change.

## Getting your credential

Never hardcode a webhook URL or a PAT into a script, and never commit one.
Both live in this repo's checkout, gitignored, 0440
(`vscode:docker`), readable only by whoever can already read this checkout:

```
nyxloom/mattermost/.ciu/secrets/installer_webhook_url
nyxloom/mattermost/.ciu/secrets/installer_pat
```

They have to reach your process out-of-band — an environment variable
forwarded in, the same pattern `scripts/netcup` already uses for
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`. If your integration lives in a
different repo (dstdns), someone with access to this checkout hands you the
*value*, not this path — the path is only meaningful inside this checkout.

## How these secrets are generated and stored (so you can trust them)

- **Webhook IDs and PAT values are minted by Mattermost itself**
  (`mmctl webhook create-incoming` / `mmctl token generate`), never
  by ciu — ciu only captures Mattermost's own output. The mint command's
  output is read once from the command's stdout and handed straight into
  storage; it is never logged, never included in an error message, and
  never printed anywhere except as a bare filename in a provisioning
  summary line (`hooks/post_compose_provision.py`, S4.23 discipline —
  refusals name the account/key, never the value).
- **Generated passwords** (account passwords, Postgres's own password —
  not producer-facing, listed for completeness) come from Python's
  `secrets.token_urlsafe(32)` — a CSPRNG-backed generator, 256 bits of
  entropy, URL-safe — via ciu's `GEN_LOCAL` directive
  (`ciu/src/ciu/secrets/materialize.py`).
- **Storage rule, identical for every secret in this stack** (ciu S4.9/S4.10/
  S9.4a, `ciu/src/ciu/secrets/materialize.py`): mode `0440`
  (owner+group read-only, no world access at all), owner/group set to the
  container's UID and the docker group, written atomically
  (`mkstemp` + `os.replace`, so a crash mid-write never leaves a partial
  file), guarded by a file lock so two concurrent `ciu up` runs can't race
  each other, and gitignored (`**/.ciu/`) so it can never land in a commit.
- **A declared `secret = "installer_pat"` in `ciu.defaults.toml.j2` is a
  NAME, not a value.** It is the key `hooks_runner.py`'s `persist: "secret"`
  channel files the value under (`<stack>/.ciu/secrets/installer_pat`) —
  the TOML never contains the credential itself, which does not exist until
  Mattermost mints it live.
- **Minting is idempotent and refuses to guess.** Re-running `ciu up` does
  not mint a second webhook/token for an account that already has one; if a
  token exists on the server but its store file is missing (Mattermost only
  reveals a token's value once), the hook **refuses** rather than silently
  minting a duplicate orphan or silently doing nothing — see
  `_ensure_tokens`'s docstring in `hooks/post_compose_provision.py` for the
  exact three-state logic.

## What this instance will never give you

- Self-signup / open registration — accounts are provisioned, not created
  by users.
- OAuth2 as a provider, plugins, marketplace apps, slash commands, outgoing
  webhooks, email/SMTP.
- Fine-grained token scoping — a PAT is always "this account's full
  permissions," which is exactly why each producer gets its own
  narrowly-scoped account rather than a shared one.
- Reading via the webhook — it is post-only and fire-and-forget by
  construction. Reading (if you ever need it) is a PAT+REST job, same as
  file attachments.

## Adding a new producer (for whoever needs this next)

Everything above is declared as data, not code, in
`ciu.defaults.toml.j2`'s `[mattermost.provision]` tables:
`[[mattermost.provision.accounts]]` (a new account, scoped to the channels
it needs), `[[mattermost.provision.webhooks]]` (its write credential), and
optionally `[[mattermost.provision.tokens]]` (a PAT, only if the job
genuinely needs REST/read access). Copy `nyxloom-installer`'s three entries
as the template — don't add a new capability to an existing account instead
of a new account; that was a deliberate, reviewed design decision (see the
per-account rationale comments in `ciu.defaults.toml.j2`'s
`[[mattermost.provision.accounts]]` block, and README.md's account table)
and applies to any future producer too.

## Full detail / audit trail

`README.md` in this directory has the complete hardening posture, the
go-public cutover recipe, and the measured (not assumed) security claims
behind everything above — read it before treating this instance as
equivalent-trust to whatever you're replacing (Telegram, etc.).
