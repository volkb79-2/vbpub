# CIU v2 Hook Examples (SPEC S9)

These examples demonstrate the v2 hook interface.  Copy and adapt them into
your stack directory; reference the file in your `ciu.toml` under the
`[<stack>.hooks]` table.

---

## Hook points (S9.1)

There are three hook points, executed in pipeline order (S8.3):

| Point | Pipeline step | Typical use |
|---|---|---|
| `pre_secrets` | step 9 — before secret materialization | inject config values; set up external state |
| `pre_compose` | step 11 — after secrets, before compose render | transform config; read secret file paths via `ctx.secret_file(name)` |
| `post_compose` | step 17 — after `docker compose up` succeeds | persist runtime tokens; run post-start initialization |

---

## Signature (S9.1)

Every hook module must expose **one** of:

```python
def run(config: dict, ctx) -> dict:
    ...
```

…or a `Hook` class with a `run(self, config, ctx)` method.  Both are called
with the merged in-memory config dict and a `HookContext` (see below).

---

## HookContext (S9.3)

```python
ctx.point       # the hook point name: 'pre_secrets', 'pre_compose', or 'post_compose'
ctx.stack_dir   # Path — absolute stack directory
ctx.repo_root   # Path — absolute repository root
ctx.secret_file(name)  # returns the Path of a secret's store file
```

---

## Return value contract (S9.4)

Return `None` or `{}` for a no-op hook.  Return a dict where every value is a
sub-dict containing at least a `'value'` key:

```python
return {
    # Apply a value to the in-memory config at the dotted path 'deploy.tag':
    "deploy.tag": {
        "value": "computed-value",
        "apply_to_config": True,   # optional — default False
    },
    # Persist a NON-SECRET fact into [state].<key> in the stack's ciu.toml:
    "initialized": {
        "value": True,
        "persist": "state",
    },
    # Persist a MINTED SECRET into <stack>/.ciu/secrets/<name>, mode 0440:
    "root_token": {
        "value": "s.minted-by-this-hook",
        "persist": "secret",       # S9.4a
    },
}
```

`state` and `secret` are the only two persist destinations [S9.4/S9.4a]; any
other value is rejected. Plain `{KEY: scalar}` (v1 form) is **rejected** with
exit 2 [S9.4].

### Choosing between the two

`[state]` is an ordinarily rendered, ordinarily readable plaintext table. It
is for **non-secret facts only** — booleans, counters, URIs, timestamps. A
secret-shaped key there (last `_`-separated component `password`/`token`/
`secret`/`api_key`/`credential`/`passphrase`/`private_key`/`key`, paired with
a literal string of 8+ characters) is refused outright by `ciu check`'s
`state-secrets` stage [S3.4a].

`persist: "secret"` [S9.4a] writes into the stack's secret store using the
same machinery a directive uses (0440 file, 0700 store dir, atomic write,
under the stack's lock). It is for a credential a hook **mints** — one no
directive could have expressed in advance, the canonical case being a real
Vault's `operator init` output. A value a directive CAN express belongs in
the stack's secrets table [S4.1] instead; a hook re-persisting an
already-materialized value is a second, unnecessary copy, and re-using a
declared name is refused as an S4.6 collision.

Two things `persist: "secret"` refuses, worth knowing before you write it:
combining it with `apply_to_config` (that would put the raw value in front of
every later template and hook — read it back with `ctx.secret_file(name)`
instead), and a dotted path (a secret name is flat; it IS the compose secret
name and the `/run/secrets/<name>` basename).

---

## Files

- `pre_compose_example.py` — shows `apply_to_config` (inject a computed value)
- `post_compose_example.py` — shows `persist:'secret'` (store a minted runtime
  token, S9.4a)

## Live examples in the test-repo

- `test-repo/infra/vault/post_compose_vault.py` — persists only the non-secret
  `initialized` flag into `[state]`; its root token is `GEN_LOCAL`-declared
  and therefore already materialized by the ordinary S4 machinery, so the hook
  deliberately persists no token at all
- `test-repo/applications/app-config/pre_compose_app.py` — reads a secret file path
