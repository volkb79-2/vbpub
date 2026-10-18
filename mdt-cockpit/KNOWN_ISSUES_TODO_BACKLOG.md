# mdt - known issues, TODO, and backlog

## MDT-001 - requirements for `aider-chat`

starting `MDT_IMAGE_VERSION="20261002"`, `IMAGE_MANIFEST="/usr/local/share/modern-debian-tools-python-debug/manifest.md"` gives this startup info

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

The current `smoke` and `release` lanes run project tests in `tester-unified`;
they do not launch the native or devcontainer image produced by the build.
Dockerfile build steps run version commands for a subset of installed tools and
write those results into the image manifest, but several optional probes are
allowed to fail and this does not verify the final container's user, process
environment, or mounted-home behavior.

Add an image-runtime acceptance stage that consumes the exact candidate image
artifacts built by CMRU. It should verify both image families and each distinct
flavor, including the final `vscode` user, executable discovery, Python
interpreter selection, key CLI startup/version probes, PWMCP client presence,
empty-home invariant, and finalizer behavior with a mounted writable home.
Define the probe inventory alongside the installation declarations where
possible, with an explicit safe probe for each executable rather than trying to
run interactive tools blindly. Place test containers in the declared gate
cgroup and preserve build artifact identity so acceptance covers what would be
published without rebuilding a different image.
