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

see `modern-debian-tools-python-debug/host-setup/etc/sysctl.d/99-inotify.conf`

## MDT-004 - make `~` able to be persisted as mount

if `/home/vscode` gets entirely bind-mounted to a host folder to carryu over user state, `~` must not be used anymore
for installed tools and so on.

## MDT-005 - adopt `cli-extended` for grammer, wizard, sematic check, ... 

vbpub has a library to prohibit handrolling solutions every CLI needs. adopt it.

## MDT-006 — cgprofile observer directory setup must fail closed

**Status: implementation and behavioral oracles are in the contract-2
candidate; registered host-setup gate and live host verification pending.**

`install.sh` previously printed a warning and continued when
`systemd-tmpfiles --create` failed. It could therefore report a successful host
setup while `/run/cgprofile/observer` was missing or had the wrong owner, group,
or mode, causing later observer socket mounts to fail or expose the wrong
access boundary.

The installer now stops on tmpfiles failure and verifies the resulting
directory against the resolved `cgprofile-observer` group and exact
`0751 root:cgprofile-observer` contract before proceeding. Before writing, it
refuses symlinks, non-directories, a missing group identity for an existing
path, and drifted owner/group/mode on an existing observer directory. The
host-check also verifies the observer group differs from Docker, the live path
has the required metadata, and the complete active sysusers/tmpfiles rule sets
match the contract (comments and blank lines are ignored; extra active
directives fail closed). `test-render.sh` has behavioral cases for mismatch refusals,
idempotence, and preserving a live control socket; the registered host-setup
gate and live post-install `stat` plus `mdt-host-check.sh` are the runtime
oracles.

## MDT-007 — publish host setup as a self-contained, updateable adopter bundle

**Status: PLANNED (2026-10-13).** The current MDT release is an OCI image.
An adopter who does not clone the MDT repository cannot run the host-side
setup: `host-setup/install.sh` consumes adjacent scripts, unit templates,
configuration examples, and helper files. A Python wheel by itself would not
carry this operational tree or provide the right host installation/update
boundary.

**Proposed distribution:** add a CMRU `bundle` artifact alongside the image
and use CMRU's generated standalone `get.py` installer for install, update,
status, and rollback. The bundle should contain the complete supported host
setup tree (installer, wizard, checks, every referenced helper, systemd and
tmpfiles/sysusers templates, config examples, and adopter documentation),
plus the MDT CLI/runtime and its required dependency closure. As part of
MDT-005, include the `cli-extended` wheel in that closure if MDT's host CLI
adopts it. The installer should place immutable versioned content under an
MDT-owned data directory and update a stable launcher/pointer transactionally;
the privileged host-setup action remains an explicit operator command.

A wheel can be an additional artifact for Python-only integrations, but a
wheel alone is not the host-setup distribution: it must also carry or fetch the
shell scripts, templates, configs, and helper assets. The normal adopter path
is to install the verified bundle on the **host**, then invoke its explicit
`mdt` host command there. The OCI devcontainer image remains a separate
artifact and may include the same version of the CLI for cockpit diagnostics.
Today `scripts/mdt_cli.py` is a shared `argparse` helper imported by individual
scripts, not a public `mdt` executable or a wheel. Define that public entry
point as part of this work. `cli-extended` is not currently imported by
`mdt_cli.py`; it becomes a bundle dependency only if MDT-005 changes that.

Provide a pasteable install command through CMRU's generated `get.py`. The
direct `curl ... | python - install` form is currently documented by CMRU as
unverified: the bundle's signature does not verify the bootstrap script before
it runs. MDT's root-level setup must use the secure path CMRU supports; do not
present an unchecked pipe as a verified install.

**Required decisions and oracles:**

- Define supported host OS/Python versions and whether the bundle carries
  platform-independent or versioned wheel assets; prefer one bundle if its
  complete dependency closure is portable.
- Ensure no repository checkout, editable install, PyPI lookup at install
  time, or network access from `install.sh` is required after bundle
  verification.
- Preserve the host's existing `/etc/mdt/host-setup.env` and live state during
  update; include a tested rollback path and never replace an existing
  bind-mounted runtime directory inode during a service restart.
- On a clean supported Debian host with no MDT checkout, install from the
  generated `get.py`, run the host-side doctor/setup command, update to a
  second release, and roll back. Verify the installed file inventory against
  the signed manifest and verify the `mdt` CLI can import its bundled
  `cli-extended` dependency once MDT-005 is complete.

CMRU owns generic bundle/get.py lifecycle behavior; MDT owns the artifact
contents, host-setup entrypoint, compatibility contract, and live acceptance
for this adopter use case. Coordinate the bootstrap UX with CMRU KI-76.

## MDT-008 — preserve the BuildKit host bind-directory inode across service restarts

**Status: implemented on the current candidate; registered gate and live bind
verification pending (2026-10-13).** `mdt-buildkitd.service` used
`RuntimeDirectory=mdt-buildkitd` with the default stop cleanup. Restarting the
service removed `/run/mdt-buildkitd` and made systemd create a new directory
inode. Existing devcontainers retained a bind mount of the old, now-empty
directory, so the restarted daemon socket stayed invisible inside the cockpit
until its container was recreated.

**Required behavior:** preserve the runtime directory for the host boot, so
service stop/restart never replaces an existing bind source. Start-up may
repair its owner/mode and remove the stale `buildkitd.sock` entry; it must not
delete and recreate the directory. `/run` being tmpfs means a host reboot
still creates a fresh directory on the next service start.

**Oracle:** rendered-unit tests require `RuntimeDirectoryPreserve=yes`; a live
acceptance check records the host directory's device/inode and a running
devcontainer's bind source before and after `systemctl restart
mdt-buildkitd.service`, then proves the inode is unchanged and the socket is
visible through the existing container mount. Also verify required owner/mode
repair and stale-socket replacement without changing the directory inode.

## MDT-009 — keep test frameworks out of the cockpit and make `mdt doctor` detect drift

**Status: OPEN (filed 2026-10-13).** The devcontainer is the cockpit; registered
gates and test suites run in `tester-unified`. The current toolkit dependency
set installs pytest and pytest plugins into the cockpit's Python environment,
so an accidental `pytest` or `python -m pytest` can look like an acceptable
local gate even though it is running in the wrong container. There is no
reliable inverse `requirements.txt` syntax that prevents a user or another
installer from adding those packages later.

**Expected direction:** remove pytest, Hypothesis, mutation and coverage test
dependencies from the default cockpit environment when no cockpit feature
needs them; keep them in the dedicated tester image. Add an explicit runtime
role marker to the devcontainer so `mdt doctor` can identify the cockpit
without guessing from a path or package set. When invoked there, `mdt doctor`
checks the configured cockpit interpreter for the declared test-only package
set and reports any installed packages as an architecture-policy error. The
host-side `mdt doctor` remains usable for MDT host setup and does not apply the
cockpit-only rule. This detects later manual installs; it is not an OS-level
restriction on what a user can install.

**Oracles:** a clean cockpit image lacks the test-only packages, while
`tester-unified` retains the exact test dependency closure it needs; `mdt
doctor` reports a package installed into the cockpit interpreter and passes
for the same package in the dedicated tester image; running the host-side
doctor does not misclassify host packages as cockpit drift. A fresh cockpit
container can still inspect repositories and drive CIU/CMRU/Run-Gate without
pytest or Hypothesis in its venv.
