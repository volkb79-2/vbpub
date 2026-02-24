# Purpose

Preparation of english source text including markup and control sequences for machine translation (MT) has limits.
Translation might need intelligent post-processing and validation. You can use this prompt below.

# Prompt

Translate the JSONL records from English to German.

Input format (one JSON object per line):
```json
{
  "id": "...",
  "source_masked": "...",
  ...
}
```
Output format (one JSON object per line, no markdown, no commentary):
```json
{"id":"...","translation_masked":"..."}
```

Mandatory rules:
1) Keep id unchanged.
2) Translate ONLY source_masked into translation_masked.
3) Preserve all __PH_n__ tokens exactly (same token text, same order).
4) Do not remove, rename, merge, or split placeholder tokens.
5) Keep the sentence fluent and natural in German, especially around placeholders/tags.
6) Keep game dialogue/UI tone concise.
7) If source contains control-flow fragments around placeholders, preserve grammatical continuity in German.
8) Return valid JSONL only.

Quality emphasis for medium/high risk rows:
- prefer idiomatic phrasing over literal word-for-word mapping
- ensure punctuation and sentence boundaries remain readable
- avoid token-stuck text like "__PH_1__Word"; keep spacing sensible unless source requires no space
