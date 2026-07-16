# ntfy dynamic-FQDN fix — LOG

Branch: `fix/ntfy-dynamic-fqdn`
Worktree: `/workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn`

## Actions taken (in order)

1. Read the four source files under `nyxloom/ntfy/`: `ciu.defaults.toml.j2`,
   `server.yml`, `README.md`, `ciu.compose.yml.j2`. Confirmed
   `ciu.compose.yml.j2:43` already references `{{ ntfy.public_host }}` — no
   edit needed there.

2. Discovered the rendered outputs (`ciu.toml`, `ciu.compose.yml`,
   `.ciu/rendered/...`, `ciu.env`) do **not** exist in this worktree — they
   are git-ignored (`.gitignore:167-170`: `ciu.global.toml`, `**/ciu.toml`,
   `**/ciu.compose.yml`, `**/.ciu/`) and `git worktree add` does not copy
   untracked files. Only the main checkout at `/workspaces/vbpub/nyxloom/ntfy`
   has them (from a prior manual render there). Never touched that checkout.

3. Edited `nyxloom/ntfy/ciu.defaults.toml.j2:17`:
   `public_host = "nyxloom.gstammtisch.dchive.de"` →
   `public_host = "nyxloom.{{ env.PUBLIC_FQDN }}"`.

4. Edited `nyxloom/ntfy/server.yml:7`:
   `base-url: "https://nyxloom.gstammtisch.dchive.de"` →
   `base-url: "https://{{ ntfy.public_host }}"`.

5. Corrected stale `ntfy.` → `nyxloom.` in `README.md` lines 4, 52, 57 (three
   occurrences of `ntfy.gstammtisch.dchive.de` → `nyxloom.gstammtisch.dchive.de`).
   Left the `# upstream-base-url: "https://ntfy.sh"` comment in `server.yml`
   untouched (legitimate upstream relay, not this stack's own FQDN).

6. Investigated the render entrypoint. `ciu render` and `ciu up --dir PATH`
   both eventually call `bootstrap_workspace_env()`
   (`ciu/src/ciu/workspace_env.py:760`), which does:
   `if generate_env or not env_path.exists(): generate_ciu_env(env_root)`.
   Since this worktree has no `ciu.env`, **any** full-CLI invocation
   (`ciu render`, `ciu up --dir ...`, with or without `--render-toml`) would
   auto-generate a brand-new `ciu.env` at the detected env_root the moment it
   runs — outside this fix's scope and adjacent to the separate
   physical-root bug a sibling fix owns. Confirmed this by reading
   `workspace_env.py:773-778` (`generated = True` path unconditionally fires
   when the file is absent, regardless of the `generate_env` flag).

   Per the handoff's BLOCKED-avoidance escape hatch ("call render_stack /
   render_configfiles directly from python if no clean verb exists"), wrote
   a standalone script
   (`/tmp/.../scratchpad/render_ntfy.py` — not part of this commit, throwaway)
   that imports the **installed** `ciu` package
   (`/home/vscode/.venv/lib/python3.14/site-packages/ciu`) and calls, in
   order, against the worktree's `nyxloom/ntfy` directory:
     - `config_model.render_stack(stack_dir, global_config={}, preserve_state=True)`
       → writes `ciu.toml`
     - `composefile.render_compose(stack_dir/"ciu.compose.yml.j2", merged_stack)`
       → writes `ciu.compose.yml`
     - `composefile.render_configfiles(stack_dir, "ntfy", merged_stack, secret_value_fn)`
       → writes `.ciu/rendered/ntfy/etc/ntfy/server.yml`

   None of these three functions touch `ciu.env` or
   `.ciu/ciu.compose.overlay.yml` (`generate_overlay()` is a separate,
   unrelated function in `composefile.py` that was never called).
   `global_config={}` is safe here because `container_prefix` in
   `ciu.defaults.toml.j2:15` already guards `{% if deploy is defined %}`
   with an `{% else %}nyxloom{% endif %}` fallback, and no other key in this
   stack's defaults reads from `global_config`.

7. Ran the script three times from
   `/workspaces/vbpub/.worktrees/ntfy-dynamic-fqdn/nyxloom/ntfy`:
   - `PUBLIC_FQDN=gstammtisch.dchive.de` → O1 (see REPORT).
   - `PUBLIC_FQDN=example.test` → O2 portability check (see REPORT).
   - `PUBLIC_FQDN=gstammtisch.dchive.de` again → restored the working tree's
     rendered outputs to the real-host value (the `example.test` render was
     never committed — it's git-ignored anyway, and this final re-render
     leaves the on-disk state matching this host regardless).

8. Confirmed via `git status --porcelain` in the worktree that only the three
   source files show as modified — the rendered outputs never appear (they
   are git-ignored) and `.ciu/ciu.compose.overlay.yml` / `ciu.env` were never
   created (checked `ls .ciu/` → only `rendered/`; `ls ciu.env` → does not
   exist).

9. Confirmed via `git status --porcelain nyxloom/ntfy/` in the **main**
   checkout (`/workspaces/vbpub`, not the worktree) that it is untouched —
   empty output.

10. Added one optional regression test,
    `TestRenderCompose.test_render_compose_host_label_composes_from_env_public_fqdn`,
    in `ciu/tests/tests/test_ciu_composefile.py` (this stack's natural
    ciu-rendering test home, per the handoff's suggestion). It exercises the
    exact two-stage pipeline ntfy uses (`render_toml_template` composes
    `public_host` from `env.PUBLIC_FQDN`, then `render_compose` reads it into
    a Traefik `Host()` label) and asserts the label changes across two
    different `PUBLIC_FQDN` values. This is under `ciu/tests/`, not
    `ciu/src/`, so it doesn't violate the "everything under `ciu/src/`"
    forbid rule.

11. Ran the full test file via the specified command:
    `cd ciu && PYTHONPATH=src /workspaces/vbpub/.venv/bin/python -m pytest tests/tests/test_ciu_composefile.py -q`
    → `81 passed`.

No BLOCKED condition encountered.
