# Development environment

This repository runs in the mdt-cockpit devcontainer. Before
installing or downloading a CLI, inspect the existing image inventory:

```bash
manifest=$(sed -n 's/^IMAGE_MANIFEST=//p' /etc/os-release)
test -r "$manifest" && sed -n '1,220p' "$manifest"
```

The fuller inventory is
`/usr/local/share/mdt-cockpit/installed-tools-manifest.md`.

The image normally includes Python/uv/pip, Node/npm, Git/GitHub tooling,
Docker/Buildx and container inspection/security tools, common database and
network clients, and the Codex, Claude, OpenCode and Reasonix agent CLIs.
Availability is not authorization: follow this repository's safety, release,
network and credential rules before using a tool.

Add this repository's architecture, build, focused-test, full-test and release
commands below. Keep exact image tool versions out of this file; they are
generated in the image manifest.
