# mdt devcontainer template: host-side folder (one cockpit, several repos)

Use this variant when you work in **several repositories at once** from one cockpit container and do not want
the cockpit's config to live inside (and be tied to) any one of them. The per-repo variant is
[`../devcontainer.json`](../README.md); the two are kept identical except for the keys the test
`scripts/test_devcontainer_template_variants.py` lists.

## When to choose which

| | Per-repo ([`../devcontainer.json`](../devcontainer.json)) | Host-side folder (this directory) |
|---|---|---|
| Config lives in | each repo's `.devcontainer/` | one folder on the host, outside every repo |
| Container name / hostname | `<repo>-devcontainer-<user>` | `mdt-cockpit-<user>` (fixed) |
| Opens in | `/workspaces/<repo>` | `/workspaces` (a directory of repos) |
| State (home, `/tmp`) | `mdt-cockpit-home/`, `mdt-cockpit-tmp/` (rename per project if you want separate state) | same sources, one cockpit |
| Consumer hooks (`finalize.{pre,post}.d`) | the repo's `.devcontainer/` | the config folder, mounted read-only at `/run/mdt/devcontainer` |
| Versioned with | the repo | its own small git repo, or nothing |
| Best for | one project per container, teams that vendor the config | one person, many repos, a standing cockpit |

## Adopt

```sh
mkdir -p ~/mdt-cockpit-config/.devcontainer && cd ~/mdt-cockpit-config && git init
cp <vbpub>/mdt-cockpit/templates/host-folder/devcontainer.json \
   <vbpub>/mdt-cockpit/templates/initialize_container_environment.py .devcontainer/
```

1. Edit `workspaceMount`: the host directory whose subdirectories are your repos (default `~/repos`). It
   appears as `/workspaces`, so `/workspaces/<repo>` keeps the path git worktree files and your tooling expect.
2. Open `~/mdt-cockpit-config` in VS Code and run **Dev Containers: Reopen in Container** (or
   `devcontainer up --workspace-folder ~/mdt-cockpit-config`).
3. Install and verify the MDT host setup first, as for the per-repo variant: the initializer checks the
   declared cgroup slices and creates missing bind sources
   ([host cgroup preflight](../../docs/CONSUMERS.md#host-cgroup-preflight)).

`initializeCommand` (`python3 .devcontainer/initialize_container_environment.py`) runs on the host in the
folder you opened, so the path is the same as in the per-repo variant.

## Choosing what the cockpit can see

A parent directory exposes **every sibling** in it. To expose only chosen repositories, point `workspaceMount`
at a dedicated directory (for example an empty `~/mdt-cockpit-workspaces/`) and add one entry per repo to
`mounts`:

```jsonc
"source=${localEnv:HOME}/src/<repo>,target=/workspaces/<repo>,type=bind,consistency=cached",
```

`mounts` entries are `--mount`: a missing source stops the container from starting, which is what you want.

## What is different inside the file

| Key | Per-repo | Host-folder |
|---|---|---|
| `name`, `--name`, `--hostname` | built from `${localWorkspaceFolderBasename}` | fixed `mdt-cockpit-${localEnv:USER}` |
| `workspaceMount` | not set (the opened folder is mounted) | your repos directory at `/workspaces` |
| `workspaceFolder` | `/workspaces/${localWorkspaceFolderBasename}` | `/workspaces` |
| extra mount | none | `${localWorkspaceFolder}/.devcontainer` read-only at `/run/mdt/devcontainer` |
| extra env | none | `MDT_DEVCONTAINER_DIR=/run/mdt/devcontainer` (finalize hook discovery) |

Everything else (image, features, bind sources, cgroup parent, bootstrap and finalize commands) is identical.

## Optional: btrfs copy-on-write area

Both variants carry the same two commented-out lines (one `mounts` entry, one `containerEnv` entry) for the
optional CoW area described in [`../../docs/BTRFS-COW.md`](../../docs/BTRFS-COW.md). Uncomment them together,
once, after the host side exists: in this variant that is one edit for all your repos instead of one per repo.

## Updating

The config folder is yours, so template changes do not arrive by themselves: after upgrading mdt, diff
`templates/host-folder/devcontainer.json` against your copy (`git diff --no-index`) and take what you want,
then rebuild.
