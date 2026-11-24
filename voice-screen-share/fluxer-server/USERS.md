# Fluxer accounts (administrator + two regular users)

## Accounts

| Username | Role | Created by |
|---|---|---|
| `fx_admin` | instance administrator (all admin permissions) | first registration with the setup token |
| `fx_alice` | regular user | public registration API, opened only for the moment of creation |
| `fx_bob` | regular user | public registration API, opened only for the moment of creation |

Names are `[fluxer.accounts]` in `ciu.defaults.toml.j2`. Sign-in is **username + password** (no e-mail).

## Where the credentials live

Each password is one ciu `GEN_LOCAL` secret. The files are mode 0600 in a 0700 directory and are git-ignored
(`**/.ciu/`). ciu generates each value on first materialization and reuses those bytes on later `ciu up` runs.
The account hook reads each file through `ctx.secret_file()`; passwords are never printed or logged. The
pre-compose hook removes an obsolete `.ciu/secrets/accounts.json` copy left by an older checkout.

From the repository root, the exact commands are:

```bash
cat voice-screen-share/fluxer-server/.ciu/secrets/fluxer/account_fx_admin
cat voice-screen-share/fluxer-server/.ciu/secrets/fluxer/account_fx_alice
cat voice-screen-share/fluxer-server/.ciu/secrets/fluxer/account_fx_bob
ciu secrets list --dir voice-screen-share/fluxer-server
```

`ciu secrets list` shows the three declared secrets and their paths without showing values. The setup token is
a different, single-purpose Fluxer service secret in `fluxer-secrets.json`; Fluxer clears it after the first
administrator exists.

## How they are created (nothing stays manual in the browser)

Fluxer has no admin CLI that creates users. The first administrator is the first account registered with the
setup token (the browser wizard does exactly that), and the registration API asks for a proof-of-work
challenge (ALTCHA). `hooks/post_compose_accounts.py` runs `hooks/fluxer_accounts.mjs` inside the api
container (`docker exec -i ... node`, credentials on stdin only):

1. sign in as the admin; if that fails, register it with the setup token (it becomes administrator);
2. mark the instance setup as configured (what the wizard's last step does), which opens sign-ups;
3. for `fx_alice` and `fx_bob`: sign in; if that fails, open registration, register, and afterwards enforce
   `registration_mode_after` (default `closed`, so nobody else can sign up on a public host).

Every step probes first, so `ciu up` can run it any number of times. The wizard's remaining steps (branding,
SMTP, legal links) are optional and left to the browser if wanted. Registration has a rate limit: a user that
could not be created because of `RATE_LIMITED` is created on the next run an hour later (the hook exits with an
error naming the step).

## Rotating a password

The account password and its ciu secret must match, so change both:

1. Sign in as the user and change the password in the app (Settings > My Account), or as `fx_admin` use the
   admin panel (`/admin`) to reset it.
2. Write the same raw value, with no trailing newline, into that user's `GEN_LOCAL` file under
   `.ciu/secrets/fluxer/`, preserving mode 0600.

If only the file is changed, the next `ciu up` fails to sign in, tries to register the existing name and
reports `user_failed:...`: it never overwrites an account. To start over completely, remove the data volumes
and reset the stack's ciu secrets before running `ciu up` again.

## Rotating infrastructure secrets

Do not edit `fluxer-secrets.json` by hand on a running instance: the Postgres password, the signing keys and the
VAPID pair are fixed at first start (the upstream `.env.example` says "set once, never change"). Deleting a key
makes the next `ciu up` generate a new one and rewrite the env files, which is only safe together with a fresh
data volume.
