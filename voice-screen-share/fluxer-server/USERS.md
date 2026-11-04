# Fluxer accounts (administrator + dummy users)

## Accounts

| Username | Role | Created by |
|---|---|---|
| `fx_admin` | instance administrator (all admin permissions) | first registration with the setup token |
| `fx_alice`, `fx_bob`, `fx_carol` | ordinary dummy users | public registration API, opened only for the moment of creation |

Names are `[fluxer.accounts]` in `ciu.defaults.toml.j2`. Sign-in is **username + password** (no e-mail).

## Where the credentials live

`<project>/.ciu/secrets/accounts.json`, a JSON object `{"<username>": "<password>"}`: mode 0600 in a 0700
directory, git-ignored (`**/.ciu/`). The passwords are random (`secrets.token_urlsafe(18)`), generated once by
`hooks/pre_compose_secrets.py` and never printed or logged: the hooks print only the file path and result codes
(`admin_created`, `user_exists`, ...). Read one with, for example:

```bash
python3 -c "import json;print(json.load(open('.ciu/secrets/accounts.json'))['fx_alice'])"
```

The setup token is in `fluxer-secrets.json` (`setup_token`); it is single-purpose and cleared by Fluxer after
the first administrator exists.

## How they are created (nothing stays manual in the browser)

Fluxer has no admin CLI that creates users. The first administrator is the first account registered with the
setup token (the browser wizard does exactly that), and the registration API asks for a proof-of-work
challenge (ALTCHA). `hooks/post_compose_accounts.py` runs `hooks/fluxer_accounts.mjs` inside the api
container (`docker exec -i ... node`, credentials on stdin only):

1. sign in as the admin; if that fails, register it with the setup token (it becomes administrator);
2. mark the instance setup as configured (what the wizard's last step does), which opens sign-ups;
3. for each dummy user: sign in; if that fails, open registration, register, and afterwards enforce
   `registration_mode_after` (default `closed`, so nobody else can sign up on a public host).

Every step probes first, so `ciu up` can run it any number of times. The wizard's remaining steps (branding,
SMTP, legal links) are optional and left to the browser if wanted. Registration has a rate limit: a user that
could not be created because of `RATE_LIMITED` is created on the next run an hour later (the hook exits with an
error naming the step).

## Rotating a password

The stored value must match the account, so change both:

1. Sign in as the user and change the password in the app (Settings > My Account), or as `fx_admin` use the
   admin panel (`/admin`) to reset it.
2. Put the new value into `.ciu/secrets/accounts.json` (mode stays 0600).

If only the file is changed, the next `ciu up` fails to sign in, tries to register the existing name and
reports `user_failed:...`: it never overwrites an account. To start over completely: `ciu down` with volumes,
delete the whole `.ciu/secrets/` directory and `ciu up` again (new secrets, new accounts).

## Rotating infrastructure secrets

Do not edit `fluxer-secrets.json` by hand on a running instance: the Postgres password, the signing keys and the
VAPID pair are fixed at first start (the upstream `.env.example` says "set once, never change"). Deleting a key
makes the next `ciu up` generate a new one and rewrite the env files, which is only safe together with a fresh
data volume.
