# OpenDesign DeepSeek in Codex, OpenCode, and Pi

This guide configures the two OpenDesign account routes for `deepseek-v4.1-flash-eco` and shows
how to change reasoning effort for one invocation. Both routes default to `max`; `low` and `high`
remain available as per-call choices. The harnesses translate those settings to DeepSeek's
thinking-mode request fields ([provider reference](https://api-docs.deepseek.com/guides/thinking_mode/)).

## Set the route keys in `.env.ai`

Use the [consumer guide's `.env.ai` setup](CONSUMERS.md#api-keys-and-shell-startup) to create the
private file and register it in `~/.config/mdt-cockpit/shell-start.d/`. Add the two keys to
`~/.env.ai`; the shipped
[`customization/.env.ai.example`](../customization/.env.ai.example) lists their names:

- `OPENDESIGN_API_KEY_GITHUB2`
- `OPENDESIGN_API_KEY_VOLKER`

Keep the values in `.env.ai`, not in harness config files. Shells export the registered file's
variables to child processes. The local `opencode-od` and `pi-od` launchers load
`${OPENDESIGN_ENV_FILE:-$HOME/.env.ai}` directly. Codex reads the selected profile's `env_key` from
the shell environment. These are user-level launchers in this workspace; the cockpit image does
not install them. If they are absent, run raw `opencode` or `pi` from a shell where the key was
exported by `shell-start.d/`.

The route identifiers are:

| Account route | Codex `CODEX_HOME` | OpenCode provider | Pi provider | Key |
|---|---|---|---|---|
| Github2 | `~/.codex-opendesign-github2` | `opendesign_github2` | `opendesign_github2` | `OPENDESIGN_API_KEY_GITHUB2` |
| Volker | `~/.codex-opendesign-volker` | `opendesign_volker` | `opendesign_volker` | `OPENDESIGN_API_KEY_VOLKER` |

## Codex

Use one Codex home per account. In each home's `config.toml`, configure the OpenDesign provider and
bind `env_key` to that account's environment variable. The provider ID may be `opendesign` in both
files because `CODEX_HOME` selects which file Codex reads.

```toml
model = "deepseek-v4.1-flash-eco"
model_provider = "opendesign"
model_catalog_json = "/absolute/path/to/catalog/opendesign-models.json"

[model_providers.opendesign]
name = "OpenDesign"
base_url = "https://amr-link.open-design.ai/v1"
env_key = "OPENDESIGN_API_KEY_GITHUB2"
wire_api = "responses"
```

Set `model_catalog_json` to the absolute catalog path under the selected Codex home. For the Volker
profile, change `env_key` to `OPENDESIGN_API_KEY_VOLKER`. The selected model's
catalog entry must set `default_reasoning_level` to `max` and list `low`, `high`, and `max` in
`supported_reasoning_levels`. Keep this setting on the Eco model entry, not as a global
`model_reasoning_effort`, so other models keep their own defaults.

The relevant fields inside that model's catalog object look like this:

```json
{
  "slug": "deepseek-v4.1-flash-eco",
  "default_reasoning_level": "max",
  "supported_reasoning_levels": [
    { "effort": "low", "description": "Lower reasoning effort" },
    { "effort": "high", "description": "High reasoning effort" },
    { "effort": "max", "description": "Maximum reasoning effort" }
  ]
}
```

Start a default run with the matching profile:

```bash
CODEX_HOME="$HOME/.codex-opendesign-github2" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex exec --model deepseek-v4.1-flash-eco "Solve this problem."
```

For Volker, replace `"$HOME/.codex-opendesign-github2"` with
`"$HOME/.codex-opendesign-volker"`. Override one run with
`-c 'model_reasoning_effort="high"'`; use `low` for lower effort:

```bash
CODEX_HOME="$HOME/.codex-opendesign-github2" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex exec --model deepseek-v4.1-flash-eco \
  -c 'model_reasoning_effort="high"' "Solve this problem."
```

## OpenCode

Add each route as a provider in `opencode.json` or `opencode.jsonc`. This is the Github2 provider;
copy it for Volker, changing the provider ID and environment variable to the values in the route
table above.

```jsonc
{
  "providers": {
    "opendesign_github2": {
      "name": "OpenDesign - Github2",
      "env": ["OPENDESIGN_API_KEY_GITHUB2"],
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": {
        "baseURL": "https://amr-link.open-design.ai/v1"
      },
      "models": {
        "deepseek-v4.1-flash-eco": {
          "name": "DeepSeek V4.1 Flash Eco",
          "capabilities": {
            "tools": true,
            "input": ["text"],
            "output": ["text"],
            "reasoning": { "supported": true }
          },
          "body": {
            "thinking": { "type": "enabled" },
            "reasoning_effort": "max"
          },
          "variants": [
            { "id": "low", "body": { "reasoning_effort": "low" } },
            { "id": "high", "body": { "reasoning_effort": "high" } },
            { "id": "max", "body": { "reasoning_effort": "max" } }
          ]
        }
      }
    }
  }
}
```

Run with the model default or select a variant after `#`:

```bash
opencode-od run --model opendesign_github2/deepseek-v4.1-flash-eco "Solve this problem."
opencode-od run --model 'opendesign_github2/deepseek-v4.1-flash-eco#high' "Solve this problem."
```

Use `#low` or `#max` for those explicit choices. To select Volker, use
`opendesign_volker/deepseek-v4.1-flash-eco`. If using raw `opencode`, start it from a shell where
the route key has been exported.

## Pi

Add both providers to `~/.pi/agent/models.json`. This Github2 entry shows the model's supported
thinking levels and DeepSeek-compatible request format; copy it for Volker with the matching
provider ID and key variable from the route table.

```json
{
  "providers": {
    "opendesign_github2": {
      "name": "OpenDesign - Github2",
      "baseUrl": "https://amr-link.open-design.ai/v1",
      "apiKey": "$OPENDESIGN_API_KEY_GITHUB2",
      "api": "openai-completions",
      "authHeader": true,
      "models": [
        {
          "id": "deepseek-v4.1-flash-eco",
          "name": "DeepSeek V4.1 Flash Eco",
          "reasoning": true,
          "thinkingLevelMap": {
            "minimal": null,
            "low": "low",
            "medium": null,
            "high": "high",
            "xhigh": null,
            "max": "max"
          },
          "input": ["text"],
          "contextWindow": 328000,
          "maxTokens": 128000,
          "compat": {
            "thinkingFormat": "deepseek",
            "supportsReasoningEffort": true
          }
        }
      ]
    }
  }
}
```

Set the model's default level in `~/.pi/agent/settings.json`:

```json
{
  "modelThinkingLevels": {
    "opendesign_github2/deepseek-v4.1-flash-eco": "max",
    "opendesign_volker/deepseek-v4.1-flash-eco": "max"
  }
}
```

Pi accepts `--thinking` as a one-run override. For example:

```bash
pi-od --provider opendesign_github2 \
  --model deepseek-v4.1-flash-eco --thinking high --print "Solve this problem."
```

Use `--thinking low` or `--thinking max` for the other supported levels. Change the provider to
`opendesign_volker` to use the Volker route. If using raw `pi`, start it from a shell where the
route key has been exported.

## Request verification

The configurations were exercised on 2026-11-23 against a local capture endpoint using
`codex-cli 0.171.0`, `opencode v2.3.0`, and `pi 1.6.0`, with temporary configs and mock keys. Across
the 18 account/harness/effort combinations, all three harnesses sent `max` by default and both
`low` and `high` when overridden for one invocation. Codex used the account-specific key and sent
the effort in the Responses `reasoning.effort` field. OpenCode and Pi sent the account-specific key,
enabled DeepSeek thinking, and sent `reasoning_effort` in Chat Completions. No OpenDesign API call
was made; this checks the harness request payloads, not live gateway acceptance.
