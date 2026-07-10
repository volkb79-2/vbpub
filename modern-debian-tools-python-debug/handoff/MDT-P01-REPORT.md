# MDT-P01: Codex companion host and OpenCode integration

## Contract Status

Complete after controller review.

| Requirement | Result |
|---|---|
| Stage and install `codex` plus `codex-code-mode-host` | Pass |
| Preserve upstream Codex checksum verification | Pass |
| Fail clearly for corrupt or incomplete Codex packages | Pass |
| Resolve and install pinned `opencode-ai` npm package | Pass |
| Expose and verify the `opencode` command | Pass |
| Thread OpenCode controls through Docker, bake, and CMRU | Pass |
| Include OpenCode in image inventory and documentation | Pass |
| Persist correct OpenCode Linux config and data paths | Pass |
| Focused offline regression tests | 21 passed |
| Python, TOML, HCL/bake, and whitespace checks | Pass |

## Key Design

Codex continues to use the checksum-verified upstream package archive. The
installer extracts it once and copies both runtime executables to
`/usr/local/bin` with mode 0755. Staging and installation independently reject
a package that omits the companion host.

OpenCode follows MDT's existing npm-tool convention: staging resolves the
exact `opencode-ai` version, the resolved value enters `tool-versions.env`, and
the image installs that exact version globally. `INSTALL_OPENCODE=false`
disables it consistently with the other optional AI tools.

The template persists user configuration under `~/.config/opencode` through
the existing `.config` mount. Auth, sessions, logs, and runtime data under
`~/.local/share/opencode` use a dedicated bind mount. Project-local
`.opencode/` content remains in the workspace and needs no home mount.

## Evidence

- Focused pytest: 21 passed.
- Python compilation: passed.
- CMRU TOML parse: passed.
- Docker bake HCL rendering and JSON parse: passed.
- Diff whitespace check: passed.
- Official npm metadata exposes the `opencode` command.

No full image build was run during this package; source-level bake rendering,
installer behavior, archive inspection against the staged Codex package, and
npm metadata were validated before merge.
