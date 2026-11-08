# MDT-P01: SUPERSEDED

**This report is a historical record and no longer describes the image.** It is kept
only so an old link does not dangle.

The original body's Codex statements do not describe this tree: since its first commit
Codex has been installed from the `@openai/codex` npm package (with the exact staged
version), not from a release archive. The archive flow this report describes - extract
once, copy `codex` and `codex-code-mode-host` to `/usr/local/bin`, verify a package
checksum - no longer exists; its leftover helper has been deleted from
`scripts/install_ai_cli_tools.py` together with its dead tests.

What is true now:

- `codex` is the npm launcher in `/opt/mdt/npm/bin`; the native binary and
  `codex-code-mode-host` ship inside the platform package's vendor directory and are
  **not** copied anywhere. `codex update` rewrites that tree, so a copy would drift.
- `verify_codex_install()` in `scripts/install_ai_cli_tools.py` checks the installed tree
  in place: staged version equality, the platform package for the host, the vendor
  manifest's target, and both executables being executable regular files. A partial tree
  (for example `npm install --omit=optional`) fails the installer.
- The Dockerfile manifest step fails the build when `codex --version` exits non-zero or
  prints nothing, so a broken install cannot be recorded as a version string.
- OpenCode is installed from its own platform package (`opencode-linux-*`) by
  `install_opencode()`; the `opencode-ai` meta-package is not used.

Why the old layout was rejected, and the per-provider `tool_mode` rule that goes with
code mode, are in `docs/DESIGN-GUIDE.md` ("How the AI CLIs update themselves") and
`docs/CONSUMERS.md` ("The Codex layout inside `/opt/mdt/npm`"). The read-only analysis
behind this change lives outside the repository, in the operator's release-program
directory, so it is described here rather than linked.
