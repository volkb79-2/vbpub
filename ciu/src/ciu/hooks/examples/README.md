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
    # Persist a value into [state].<key> in the stack's ciu.toml:
    "root_token": {
        "value": "s.secret-token",
        "persist": "state",        # only valid destination
    },
}
```

Plain `{KEY: scalar}` (v1 form) is **rejected** with exit 2 [S9.4].

---

## Files

- `pre_compose_example.py` — shows `apply_to_config` (inject a computed value)
- `post_compose_example.py` — shows `persist:'state'` (save a runtime token)

## Live examples in the test-repo

- `test-repo/infra/vault/post_compose_vault.py` — persists Vault's root token
- `test-repo/applications/app-config/pre_compose_app.py` — reads a secret file path
