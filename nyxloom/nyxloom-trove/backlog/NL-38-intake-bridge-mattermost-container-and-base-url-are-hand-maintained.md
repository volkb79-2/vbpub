---
kind: backlog-entry
schema_version: 1
id: NL-38
title: "[intake_bridge] container and base_url in nyxloom.toml are hand-maintained copies of the mattermost instance id and went stale when ciu regenerated it"
status: open
type: "bugfix"
severity: "medium"
provenance: "controller session 2026-10-07 (operator report: Mattermost not responding correctly; mm_reachability.py live run)"
filed_date: "2026-10-07"
---

## Observed

On 2026-10-06 04:23 the nyxloom checkout's ciu instance identity was regenerated with the schema-2 identity file (`ciu.instance.generated.toml`, `instance_id` `1dd3d1` -> `3oqua1`). The production Mattermost stack came back as `nyxloom-3oqua1-mattermost`; its data survived because it lives in stack-dir bind mounts.

`nyxloom/nyxloom-trove/nyxloom.toml` `[intake_bridge]` still named `container = "nyxloom-1dd3d1-mattermost"` and `base_url = "http://nyxloom-1dd3d1-mattermost:8065"`. Both the `mmctl` and `rest` transports would target a container that no longer exists. Mattermost itself was healthy: `mm_reachability.py` reported 39 PASS and 0 FAIL against the stack's own config.

The values were corrected by hand on 2026-10-07. The same hand-maintained pattern remains in:
- `nyxloom/pwmcp-instance/README.md` (`nyxloom-1dd3d1-nyxloomd-net`, `nyxloom-1dd3d1-pwmcp`);
- `nyxloom/mattermost/README.md` examples;
- `nyxloomd/ciu.toml` `container_prefix`, not examined.

## Expected

The bridge derives the container name and base URL from the mattermost stack's rendered config (`nyxloom/mattermost/ciu.toml` `container_prefix` plus the app name and port), or from `ciu.instance.generated.toml`, at load time. A stale explicit override fails loudly with the expected name. Docs use `<instance_id>` placeholders instead of a literal id.

## Oracles

- With an explicit stale `container` in `nyxloom.toml`, loading the bridge config refuses with a message naming the running container.
- With the keys omitted, the bridge resolves the current container name from the stack config.
- `mm_reachability.py` (or a sibling check) can verify the nyxloom.toml consumer values against the live container.

## Related

ciu CIU-115 (schema-2 instance identity); CIU-104 (`environment_tag = "$INSTANCE_ID"`).
