"""Merge-time identity checks for multi-host, multi-shard R2 campaigns.

A campaign's shards may only be merged when every shard judges the SAME commit
with the SAME tester image and the SAME candidate plan. A merge over mixed
shards would silently combine evidence from two different trees -- exactly the
class of defect this check exists to refuse, because the merged verdict looks
complete either way.

Each shard writes one ``fleet-shard-identity/1`` document into the campaign
evidence:

```json
{
  "schema": "fleet-shard-identity/1",
  "lane": "assay-r2",
  "host": "runner-a",
  "commit": "<full lowercase Git object id>",
  "image_digest": "sha256:<64 lowercase hex digits>",
  "plan_sha256": "<64 lowercase hex digits>",
  "shard": {"index": 0, "count": 2}
}
```

A field the shard could not observe is an error, never a placeholder: the
identity document is the merge's only statement about what each store judged.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import ContractError

IDENTITY_SCHEMA = "fleet-shard-identity/1"
IDENTITY_SET_SCHEMA = "fleet-shard-identity-set/1"

_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\Z")
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

_IDENTITY_KEYS = {"schema", "lane", "host", "commit", "image_digest", "plan_sha256", "shard"}
_SHARD_KEYS = {"index", "count"}

#: Compare these across every shard; a difference is a refusal. The shard
#: COUNT is compared separately (it lives inside the ``shard`` object).
MERGE_FIELDS = ("lane", "commit", "image_digest", "plan_sha256")


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{where}: expected non-empty text")
    return value


def _count(value: object, where: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractError(f"{where}: expected an integer of at least {minimum}")
    return value


def validate_shard_identity(value: object) -> dict[str, Any]:
    """Validate one ``fleet-shard-identity/1`` document."""
    if not isinstance(value, Mapping):
        raise ContractError("shard identity: expected a JSON object")
    if value.get("schema") != IDENTITY_SCHEMA:
        raise ContractError(f"shard identity.schema: expected {IDENTITY_SCHEMA}")
    extra = sorted(set(value) - _IDENTITY_KEYS)
    if extra:
        raise ContractError(f"shard identity: unknown field(s): {', '.join(extra)}")
    missing = sorted(_IDENTITY_KEYS - set(value))
    if missing:
        raise ContractError(f"shard identity: missing field(s): {', '.join(missing)}")
    lane = _text(value["lane"], "shard identity.lane")
    host = _text(value["host"], "shard identity.host")
    commit = value["commit"]
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise ContractError("shard identity.commit: expected a full lowercase Git object id")
    image = value["image_digest"]
    if not isinstance(image, str) or _IMAGE.fullmatch(image) is None:
        raise ContractError(
            "shard identity.image_digest: expected sha256: followed by 64 lowercase hex digits"
        )
    plan = value["plan_sha256"]
    if not isinstance(plan, str) or _DIGEST.fullmatch(plan) is None:
        raise ContractError("shard identity.plan_sha256: expected a lowercase SHA-256 digest")
    shard = value["shard"]
    if not isinstance(shard, Mapping):
        raise ContractError("shard identity.shard: expected an object")
    extra = sorted(set(shard) - _SHARD_KEYS)
    if extra:
        raise ContractError(f"shard identity.shard: unknown field(s): {', '.join(extra)}")
    missing = sorted(_SHARD_KEYS - set(shard))
    if missing:
        raise ContractError(f"shard identity.shard: missing field(s): {', '.join(missing)}")
    count = _count(shard["count"], "shard identity.shard.count", minimum=1)
    index = _count(shard["index"], "shard identity.shard.index")
    if index >= count:
        raise ContractError(
            "shard identity.shard.index: must be below the shard count"
        )
    return {
        "schema": IDENTITY_SCHEMA,
        "lane": lane,
        "host": host,
        "commit": commit,
        "image_digest": image,
        "plan_sha256": plan,
        "shard": {"index": index, "count": count},
    }


def identity_document(
    *,
    lane: str,
    host: str,
    commit: str,
    image_digest: str,
    plan_sha256: str,
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    """Build one ``fleet-shard-identity/1`` document, validating every field.

    Every argument is REQUIRED: a shard that could not observe its own commit
    or image identity must not write a document at all, because a placeholder
    here would be the merge check's only statement about what was judged.
    """
    return validate_shard_identity({
        "schema": IDENTITY_SCHEMA,
        "lane": lane,
        "host": host,
        "commit": commit,
        "image_digest": image_digest,
        "plan_sha256": plan_sha256,
        "shard": {"index": shard_index, "count": shard_count},
    })


def identity_from_plan(
    plan: Mapping[str, Any],
    *,
    host: str,
    image_digest: str,
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    """Build a shard identity whose commit and plan digest come from the plan.

    The plan digest is the SAME canonical document the campaign state
    fingerprints (``json.dumps(sort_keys, separators, ensure_ascii=False)``
    plus a newline), so a merge check and a campaign cannot disagree about
    which plan a shard judged.
    """
    if not isinstance(plan, Mapping):
        raise ContractError("plan: expected fleet-plan/1")
    execution = plan.get("execution")
    if not isinstance(execution, Mapping):
        raise ContractError(
            "plan.execution: a shard identity needs a plan pinned to a commit and project"
        )
    lane = plan.get("lane")
    if not isinstance(lane, str) or not lane:
        raise ContractError("plan.lane: expected a non-empty lane name")
    canonical = json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8") + b"\n"
    return identity_document(
        lane=lane,
        host=host,
        commit=str(execution.get("commit")),
        image_digest=image_digest,
        plan_sha256=hashlib.sha256(canonical).hexdigest(),
        shard_index=shard_index,
        shard_count=shard_count,
    )


def load_shard_identity(path: Path) -> dict[str, Any]:
    """Read one shard identity document from disk."""
    source = Path(path)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise ContractError(f"shard identity {source}: cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"shard identity {source}: is not valid JSON: {exc}") from exc
    try:
        return validate_shard_identity(document)
    except ContractError as exc:
        raise ContractError(f"shard identity {source}: {exc}") from exc


def validate_shard_set(
    identities: Sequence[Mapping[str, Any]],
    *,
    require_complete: bool = False,
) -> dict[str, Any]:
    """Compare every shard's identity and decide whether a merge is allowed.

    The FIRST shard is the reference. Every later shard must agree on lane,
    commit, image digest, plan digest and shard count; a differing value is a
    ``mismatched`` problem naming both values, never a silent pick of one. Two
    shards claiming the same index, or a count that leaves indices missing,
    make the set ``incomplete``. An incomplete set is ALWAYS a refusal (the CLI
    exits 3): a merge over a missing shard silently reports the missing
    candidates as absent work. *require_complete* is accepted so the documented
    command line keeps working; it changes nothing, because there is no
    permissive mode to opt out of.
    """
    del require_complete
    if not identities:
        raise ContractError("shard identity set: at least one shard is required")
    validated = [validate_shard_identity(item) for item in identities]
    reference = validated[0]
    problems: list[dict[str, str]] = []
    for position, shard in enumerate(validated[1:], start=1):
        for field in MERGE_FIELDS:
            if shard[field] != reference[field]:
                problems.append({
                    "field": field,
                    "shard": shard["host"],
                    "reason": (
                        f"shard {position} ({shard['host']}) has {field} "
                        f"{shard[field]!r}, the reference shard has {reference[field]!r}"
                    ),
                })
        if shard["shard"]["count"] != reference["shard"]["count"]:
            problems.append({
                "field": "shard.count",
                "shard": shard["host"],
                "reason": (
                    f"shard {position} ({shard['host']}) declares a count of "
                    f"{shard['shard']['count']}, the reference shard declares "
                    f"{reference['shard']['count']}"
                ),
            })
    counts = {shard["shard"]["count"] for shard in validated}
    indices = [shard["shard"]["index"] for shard in validated]
    duplicates = sorted({index for index in indices if indices.count(index) > 1})
    total = max(counts) if counts else 1
    missing = sorted(set(range(total)) - set(indices)) if len(counts) == 1 else []
    if duplicates:
        # A duplicated index is a DISAGREEMENT about which slice a store holds,
        # not an absent shard: it is a refusal, never an "incomplete" report.
        problems.append({
            "field": "shard.index",
            "shard": ",".join(
                shard["host"] for shard in validated if shard["shard"]["index"] in duplicates
            ),
            "reason": f"shard index(es) claimed twice: {', '.join(str(index) for index in duplicates)}",
            "duplicate": "true",
        })
    elif missing:
        problems.append({
            "field": "shard.index",
            "shard": reference["host"],
            "reason": (
                f"shard(s) missing from the set: {', '.join(str(index) for index in missing)}"
            ),
        })
        # One status expression below decides every case: a missing shard is
        # still reported as incomplete (never as a clean match) when the caller
        # did not ask to refuse an incomplete set, and the exiting status does.
    status = "ok" if not problems else (
        "incomplete"
        if all(problem["field"] == "shard.index" and not problem.get("duplicate") for problem in problems)
        else "mismatched"
    )
    return {
        "schema": IDENTITY_SET_SCHEMA,
        "status": status,
        "lane": reference["lane"],
        "commit": reference["commit"],
        "image_digest": reference["image_digest"],
        "plan_sha256": reference["plan_sha256"],
        "shard_count": total,
        "shards": [
            {"host": shard["host"], "index": shard["shard"]["index"]}
            for shard in validated
        ],
        "problems": problems,
    }
