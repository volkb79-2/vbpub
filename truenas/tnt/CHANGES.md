# tnt changes

## [Unreleased]

- M0 scaffold: `tnt --version`, `tnt doctor`, `tnt profile list|show`, `tnt disks`, `tnt tui` (Storage > Disks).
- Transport over `truenas_api_client` (pinned by sha of tag TS-27.0.0-RC.1): wss only, TLS verification on by
  default, API key from a mode-0600 file or the environment, never printed.
- Per-version adapters v27 and v28 (v28 is a stub) with a method manifest; read-only method guard.
- Plan/apply action model with confirm gates and a refusal list.
- Fake JSON-RPC websocket server (`tnt.testing`) used by the tests.
