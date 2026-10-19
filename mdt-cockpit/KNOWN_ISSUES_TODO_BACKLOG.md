# mdt - known issues, TODO, and backlog

## MDT-001 - requirements for `aider-chat`

starting `MDT_IMAGE_VERSION="20261002"`, `IMAGE_MANIFEST="/usr/local/share/mdt-cockpit/manifest.md"` gives this startup info

```
[INFO] Using requirements file: /workspaces/dstdns/requirements.txt
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed. This behaviour is the source of the following dependency conflicts.
aider-chat 0.86.3.dev53+g5dc9490bb requires aiohttp==3.13.3, but you have aiohttp 3.14.3 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires charset-normalizer==3.4.6, but you have charset-normalizer 3.5.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires click==8.3.1, but you have click 8.5.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires fastapi==0.135.1, but you have fastapi 0.142.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires packaging==26.0, but you have packaging 26.3 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pydantic==2.12.5, but you have pydantic 2.13.5 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pydantic-core==2.41.5, but you have pydantic-core 2.46.5 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires pygments==2.19.2, but you have pygments 2.21.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires requests==2.32.5, but you have requests 2.34.2 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires rich==14.3.3, but you have rich 15.0.0 which is incompatible.
aider-chat 0.86.3.dev53+g5dc9490bb requires urllib3==2.6.3, but you have urllib3 2.8.0 which is incompatible.
[SUCCESS] Python packages installed from /workspaces/dstdns/requirements.txt
```

## MDT-002  - docker daemon default-address-pools

host-setup should configure
```json
{
  "default-address-pools": [
    { "base": "10.240.0.0/16", "size": 24 }
  ]
}
```

## MDT-003 - integrate `fs.inotify.max_user_watches`

see `host-setup/etc/sysctl.d/99-inotify.conf`

## MDT-004 - move to a persisted `/home/vscode` in mdt-cockpit

**CLOSED 2026-10-18 (MDTC-01, Phase 1; runtime acceptance is MDT-006).** Redesigned as a
fresh-home layout instead of a migrated one:

- the home is `~/mdt-cockpit-home/` (`0700`), `/tmp` is `~/mdt-cockpit-tmp/` (`1777`), any further
  source is `~/mdt-cockpit-<purpose>`; a new home **starts empty**;
- **no migration**: `migrate_persistent_home.py`, its tests, the legacy-source refusal and the
  completion-marker logic are deleted. The bootstrap prints one informational line when the old
  `~/mdt--mounted-folders/` holds files and the new home is empty; users copy by hand
  (`docs/HOME-PERSISTENCE.md`, "Moving from modern-debian-tools-python-debug");
- image-managed trees (`/opt/mdt`, `/srv`) are group `mdt` (GID 2750, setgid dirs, group-writable
  files) so the re-numbered runtime user can update tools without `sudo`; the build asserts the
  contract (`mdt-perms check`);
- one `PATH` (Dockerfile `ENV`; `~/.local/bin` last), `NPM_CONFIG_PREFIX=/opt/mdt/npm`,
  `UV_PYTHON_INSTALL_DIR=/opt/mdt/python`;
- every default is wired system-wide (`/etc/bash.bashrc`, `/etc/zsh/zshrc`, `/etc/nanorc`,
  `/etc/htoprc`, `/etc/mc/mc.ini`, `/etc/xdg/nvim`, `LESSOPEN`); the finalizer's only home write is a
  comment-only `~/.zshrc` stub. The `~/.venv*`, `~/.oh-my-zsh`, `~/mdt-manifest.md` compatibility
  symlinks and the per-tool `.env` symlinks are gone;
- the namespace is `mdt-cockpit` (`/usr/local/share/mdt-cockpit`, `~/.config/mdt-cockpit`).

## MDT-007 - the shipped "NvChad" tree is the core repository, not a starter

`NVCHAD_VER` stages the `NvChad/NvChad` repository tag. Its `init.lua` is the placeholder
`print('use this along starter!!!')` - the real configuration is the separate `NvChad/starter`
repository. The previous layout seeded that tree as `~/.config/nvim` and the move to
`/etc/xdg/nvim` (system-wide) preserves the behaviour as it was. Decide: stage the starter (and let
`customization/nvim/sysinit.vim`'s `configs.lazy` lockfile redirection apply), or ship plain
defaults. Needs network on first run either way (lazy.nvim bootstraps its plugins).

## MDT-005 - adopt `cli-extended` for grammer, wizard, sematic check, ...

vbpub has a library to prohibit handrolling solutions every CLI needs. adopt it.

## MDT-006 - run acceptance probes against the built image artifacts

**CLOSED 2026-10-18 (MDTC-01, Phase 2).** The `image-runtime` lane (`./run-gate.py image-runtime`,
driver `tests/runtime/`, design in `docs/DESIGN-GUIDE.md` "Built-image acceptance is a separate test
boundary") starts real containers from the exact native and VS Code image refs and checks the final
container's user, process environment and mounted-home behaviour: the image contract, one `PATH`
in six shells, group-`mdt` writes under a re-numbered uid (with a negative control), the finalizer
on a fresh home, silent interactive shells, every system-wide default with an empty home, and
local-dev provenance. Its first real run found two defects the unit tests could not (`/srv`
re-moded by Debian's base-files postinst; the finalizer calling `os.getlogin()` without a tty).

Not covered, deliberately: the Dockerfile's install-time version probes for the apt-installed tools
(still best-effort, recorded in the manifest), anything needing the network (updates, first-run
plugin bootstraps), and GUI/IDE behaviour. Filed as MDT-008: the lane is not yet part of the
release flow, so `cmru release` does not run it against the images it publishes.

## MDT-008 - run `image-runtime` as part of the release flow

`image-runtime` needs the exact image refs (`MDT_RUNTIME_IMAGE_NATIVE`, `MDT_RUNTIME_IMAGE_VSC`).
A release builds and pushes images but never runs the lane against them, so a published image can
still skip the runtime contract. Decide where the refs come from (the build's saved state) and wire
the lane between build and push; until then it is run by hand against a local-dev or release build.

## MDT-009 - `resolve-devcontainers-release.py` consumes `cmru versions` JSON

Once cmru's `versions` has the OCI fixes in `handoff/CMRU-GAPS-2026-10-19.md` (G2 digest on semver
records, G3 no 5000-tag refusal, G6 Docker credential store), `scripts/resolve-devcontainers-release.py`
keeps only its devcontainers advisory (which devcontainers image release a base tag belongs to) and takes
the tag/digest selection from `cmru versions check --json` instead of its own `fetch_registry_tags` /
`select_latest_stable_tag` scanner. Until then the scanner is the design input for those cmru items.

## MDT-010 - aider returns to a vetted release

`aider-main-pin.toml` pins an exact commit of upstream `main` because aider's newest release needs
Python <3.13 (MDTC-03). When an aider release supports Python 3.14: delete the file, declare
`pypi.aider-chat` in `cmru.toml`, read it in `_vetted_ai_cli_versions` with the other three, and drop the
documented exception from README / DESIGN-GUIDE.
