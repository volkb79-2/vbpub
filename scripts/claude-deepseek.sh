#!/usr/bin/env bash
# claude-deepseek.sh — run Claude Code against DeepSeek's Anthropic-compatible
# endpoint, so the only CLI with async supervision (Claude Code) can drive the
# cheapest worker models. See docs/controller-workflow-v2.md §5.1.
#
# STATUS: UNPROBED (2026-07-12). Before first real use, run the probe:
#   scripts/claude-deepseek.sh -p --output-format json 'Reply with exactly ALIAS_OK.'
# Success criteria: reply text ALIAS_OK, no auth error, model id echoed in JSON.
# If DeepSeek rejects the request, check https://api-docs.deepseek.com/guides/anthropic_api
# for the current endpoint path and supported model names.
#
# Credentials: DEEPSEEK_API_KEY from ~/.reasonix/.env (same key Reasonix uses
# for its direct api.deepseek.com providers). Env is confined to this process;
# normal `claude` sessions are untouched.
#
# Model override: CLAUDE_DEEPSEEK_MODEL=deepseek-reasoner scripts/claude-deepseek.sh ...
set -euo pipefail

ENV_FILE="${HOME}/.reasonix/.env"
[[ -f "$ENV_FILE" ]] || { echo "error: $ENV_FILE not found" >&2; exit 1; }

DEEPSEEK_API_KEY="$(grep -E '^DEEPSEEK_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
[[ -n "$DEEPSEEK_API_KEY" ]] || { echo "error: DEEPSEEK_API_KEY empty in $ENV_FILE" >&2; exit 1; }

exec env \
  ANTHROPIC_BASE_URL="https://api.deepseek.com/anthropic" \
  ANTHROPIC_AUTH_TOKEN="$DEEPSEEK_API_KEY" \
  ANTHROPIC_MODEL="${CLAUDE_DEEPSEEK_MODEL:-deepseek-chat}" \
  ANTHROPIC_SMALL_FAST_MODEL="${CLAUDE_DEEPSEEK_MODEL:-deepseek-chat}" \
  claude "$@"
