# ntfy dynamic-FQDN fix — REPORT

Branch: `fix/ntfy-dynamic-fqdn` · Worktree: `/workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn`

## Edits

| File | Change |
|---|---|
| `nyxloom/ntfy/ciu.defaults.toml.j2:17` | `public_host = "nyxloom.gstammtisch.dchive.de"` → `public_host = "nyxloom.{{ env.PUBLIC_FQDN }}"` |
| `nyxloom/ntfy/server.yml:7` | `base-url: "https://nyxloom.gstammtisch.dchive.de"` → `base-url: "https://{{ ntfy.public_host }}"` |
| `nyxloom/ntfy/README.md:4,52,57` | stale `ntfy.gstammtisch.dchive.de` → `nyxloom.gstammtisch.dchive.de` (3 occurrences) |
| `nyxloom/ntfy/ciu.compose.yml.j2:43` | **unchanged** — already read `Host(\`{{ ntfy.public_host }}\`)` |
| `ciu/tests/tests/test_ciu_composefile.py` | +1 optional regression test (see below) |

`nyxloom/ntfy/docker-compose.yml` (the static plain-compose fallback, forbidden
to touch): inspected only. It already reads
`Host(\`nyxloom.gstammtisch.dchive.de\`)` — correct subdomain, but still a
hand-pinned literal since plain YAML can't run Jinja. **Follow-up worth
filing:** if this host's `PUBLIC_FQDN` ever changes, this file needs a manual
edit (or a `sed`/generation step) to stay in sync — same caveat the file's own
header comment already documents ("keep in sync with the template").

## Why the rendered outputs aren't part of the diff

`ciu.toml`, `ciu.compose.yml`, and `.ciu/rendered/**` are git-ignored
(`.gitignore:167-170`) and did not exist in this worktree at all (they're
untracked, so `git worktree add` never copied them from the main checkout).
They were regenerated on disk for verification (see below) but intentionally
never `git add -f`'d — `git status --porcelain` in the worktree shows only the
three source-file edits plus the two handoff docs and the test file.

## Render entrypoint used

`ciu render` / `ciu up --dir ...` both funnel into
`workspace_env.bootstrap_workspace_env()`, which unconditionally calls
`generate_ciu_env()` the instant `ciu.env` is missing — true here, since this
worktree has none. Using either CLI verb would therefore have created a new
`ciu.env` outside this fix's scope (and adjacent to a separate physical-root
bug owned by a sibling fix). Per the handoff's escape hatch, rendered directly
via the **installed** `ciu` package's functions
(`/home/vscode/.venv/lib/python3.14/site-packages/ciu`), bypassing the CLI:

```
config_model.render_stack(stack_dir, global_config={}, preserve_state=True)   # -> ciu.toml
composefile.render_compose(stack_dir/"ciu.compose.yml.j2", merged_stack)      # -> ciu.compose.yml (written manually)
composefile.render_configfiles(stack_dir, "ntfy", merged_stack, secret_fn)    # -> .ciu/rendered/ntfy/etc/ntfy/server.yml
```

None of these three functions write `ciu.env` or
`.ciu/ciu.compose.overlay.yml` (that's a separate `generate_overlay()`
function, never invoked). Script kept at
`/tmp/claude-1003/.../scratchpad/render_ntfy.py` (scratch, not committed).

## O1 — real host (PUBLIC_FQDN=gstammtisch.dchive.de)

Command:
```
cd /workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn/nyxloom/ntfy
PUBLIC_FQDN=gstammtisch.dchive.de python3 render_ntfy.py "$(pwd)"
grep -n "public_host" ciu.toml
grep -n "Host(" ciu.compose.yml
grep -n "base-url" .ciu/rendered/ntfy/etc/ntfy/server.yml
```

Output:
```
6:public_host = "nyxloom.gstammtisch.dchive.de"
43:      traefik.http.routers.nyxloom-ntfy.rule: "Host(`nyxloom.gstammtisch.dchive.de`)"
7:base-url: "https://nyxloom.gstammtisch.dchive.de"
```

**PASS** — matches the current-host value, sourced dynamically (not fixed).

## O2 — portability (PUBLIC_FQDN=example.test)

Same render command, `PUBLIC_FQDN=example.test`:

```
6:public_host = "nyxloom.example.test"
43:      traefik.http.routers.nyxloom-ntfy.rule: "Host(`nyxloom.example.test`)"
7:base-url: "https://nyxloom.example.test"
```

**PASS** — both the Traefik `Host()` router label AND the ntfy `base-url` flip
to `nyxloom.example.test` under a different `PUBLIC_FQDN`, proving the value
is no longer hardcoded. (Negative case — value staying fixed at
`gstammtisch.dchive.de` regardless of `PUBLIC_FQDN` — is what the ORIGINAL
templates exhibited before this fix; confirmed by reading the pre-edit
`ciu.defaults.toml.j2`/`server.yml` literal strings.)

After this check, re-ran the render with
`PUBLIC_FQDN=gstammtisch.dchive.de` once more to restore the working tree's
(git-ignored, so inconsequential either way) rendered outputs to the
real-host value — confirmed above under O1's second run.

## O3 — no stale `ntfy.<domain>` references

```
cd /workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn/nyxloom/ntfy
grep -rn "ntfy\.gstammtisch\|ntfy\.example" . --include="*"
```
Output: no matches (`exit 1`).

The only remaining `ntfy.` + domain-like string is the legitimate upstream
comment `# upstream-base-url: "https://ntfy.sh"` in `server.yml:33`, which is
explicitly exempted (it names the actual `ntfy.sh` relay service, not this
stack's own hostname).

`nyxloom/ntfy/docker-compose.yml` already read `nyxloom.` (never needed a
fix); README's 3 stale `ntfy.` references were corrected (see Edits table).

**PASS.**

## Forbidden-file confirmation

```
$ ls -la /workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn/nyxloom/ntfy/.ciu/
rendered/          # only — no ciu.compose.overlay.yml was ever created
$ ls /workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn/nyxloom/ntfy/ciu.env
ls: cannot access 'ciu.env': No such file or directory
$ cd /workspaces/vbpub && git status --porcelain nyxloom/ntfy/
(empty — main checkout untouched)
```

`nyxloom/ntfy/docker-compose.yml`: read-only inspection, not edited (per
forbid list).

## Optional regression test

Added `TestRenderCompose.test_render_compose_host_label_composes_from_env_public_fqdn`
in `ciu/tests/tests/test_ciu_composefile.py` — exercises the same two-stage
pipeline ntfy relies on (`render_toml_template` composing `public_host` from
`env.PUBLIC_FQDN`, then `render_compose` reading it into a Traefik `Host()`
label) and asserts the label tracks two different `PUBLIC_FQDN` values,
guarding against a future re-hardcode of this pattern.

Gate command run:
```
cd /workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn/ciu
PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m pytest tests/tests/test_ciu_composefile.py -q
```
Output: `81 passed in 0.52s`

## Summary

- O1, O2, O3 all PASS.
- `nyxloom/ntfy/.ciu/ciu.compose.overlay.yml` and `nyxloom/ntfy/ciu.env`:
  confirmed never created/touched.
- Main checkout (`/workspaces/vbpub`, outside this worktree): confirmed
  untouched.
- `nyxloom/ntfy/docker-compose.yml`: untouched, flagged as a follow-up
  candidate (static literal, can't consume Jinja).
