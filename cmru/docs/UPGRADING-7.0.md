# Upgrading to cmru 7.0 (from 6.x)

7.0.0 makes one configuration key mandatory (KI-94). SPEC section 2 breaking-change policy
requires a MAJOR bump for that, hence 7.0.0.

## The breaking change: `[github] token_source`

Every committed `[github]` table (in `cmru.orchestration.toml`, or in a portable `cmru.toml`
that carries its own `[github]`) must now declare where the GitHub token comes from:

```toml
[github]
owner = "volkb79-2"
repo = "vbpub"
owner_type = "user"
token_source = "cmru"      # or "gh"
```

| value | token comes from | secret file needed |
|---|---|---|
| `"cmru"` | exactly today's behaviour: `GITHUB_PUSH_PAT`, then `GITHUB_TOKEN`, then `cmru.secret.toml` (+ project overlay) | yes, or an env token |
| `"gh"` | `gh auth token --hostname github.com` (the user's `gh auth login`) | no; works in every worktree without `--secret-file` |

A missing or other value exits 2 and names both allowed values and where to set them.

**Migration (existing estate): add `token_source = "cmru"` to each `[github]` table.** Nothing
else changes: your `cmru.secret.toml` keeps working untouched. `token_source` is NOT a key of
`cmru.secret.toml` (putting it there is refused with a pointer to the committed table).

`cmru init` now scaffolds `token_source = "gh"`.

## Switching to `gh`

1. `gh auth login` (once per user).
2. For GHCR work, grant the package scopes once:
   `gh auth refresh -h github.com -s read:packages,write:packages,delete:packages`
   (`delete:packages` is needed by `cmru cleanup`).
3. Set `token_source = "gh"`.

Under `"gh"`, `GITHUB_PUSH_PAT`/`GITHUB_TOKEN` in the environment do not shadow the login, and
`cmru.secret.toml` is not read. `gh` runs with a minimal environment (no `GH_TOKEN`). A missing
`gh` binary, a non-zero exit or an empty token is an exit-2 configuration error with the hint
`gh auth login`. Trade-off: the `gh` OAuth token carries the account's full `repo` scope, which a
fine-grained PAT limited to one repository would not.

## Other behaviour changes since 6.2.1

- `cmru build` builds the checked-out commit (`HEAD`) by default, pushed or not (KI-95).
  `--from-origin-main` restores the old fetched-`origin/main` snapshot; `--allow-uncommitted`
  builds the committed state past uncommitted project edits (the refusal now names it).
- `cmru release --skip-gates REASON` (CMRU-SKIP-01), see CONSUMERS.md.

## Older transactions, old branches and 6.x controllers (honest limits)

The key is mandatory in the config the controller loads, so:

- **A controller (`cmru` 7.x) whose own checked-out config lacks `token_source`** refuses at
  once (exit 2), before any worktree is created. Add the key first.
- **An in-flight release transaction started by 6.x** (`release --resume`), **`--from-worktree`
  from an older branch** and **a default HEAD build on an older branch**: the 7.x parent
  declares its `token_source` in the credential handoff, and the child adopts it when the
  snapshot it loads lacks the key. These now work. A project step that re-invokes `cmru` below
  such a child does not receive that declaration and still needs the key in the snapshot.
- **A released 6.2.x `cmru` rejects `token_source` as an unknown key.** No single config works
  for both a 6.2.x and a 7.x controller. Upgrade every machine that runs `cmru` against the
  estate before committing the key, or keep the key out of branches a 6.x controller still builds.

## Credential exposure notes

- Under `"gh"` the token is fetched lazily (only commands that need a credential run `gh`) and
  injected only into publishing steps (`push`, steps with a registry `login`, steps listing
  `GITHUB_PUSH_PAT` in `required_env`); other steps never see it. Under `"cmru"` the token is still
  exported to the whole process environment as before (KI-96).
- `gh` stores its token in plaintext in `~/.config/gh/hosts.yml`: the same exposure as
  `cmru.secret.toml`, but typically with broader scopes (the account's full `repo` scope).
- `cmru doctor` reports the declared source even when the config otherwise fails to load. Under
  `"gh"` it checks only that `gh auth token` returns a token: that command does not validate the
  token against GitHub and says nothing about scopes (use `gh auth status`).
- `--secret-file` is refused under `"gh"`.

## HEAD builds and publication

`cmru build` records `source_mode` (`head` | `origin-main`) in `build.json`. `cmru publish
--build-output` refuses (exit 4) a `head` build whose commit is not contained in a freshly
fetched `origin/main`; push/merge the commit, or rebuild with `--from-origin-main`. Records written
before this field existed are treated as `origin-main` builds. A HEAD build now also warns about
dirty tracked files outside the selected project directories (`libraries/`, root config), which the
HEAD snapshot omits.
