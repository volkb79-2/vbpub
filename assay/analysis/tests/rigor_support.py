"""Validation of an exported fragment against the vendored rigor-fragment/1 contract.

The schemas and golden fragments under ``fixtures/rigor/`` are byte copies of the files the rigor-record library
publishes (vbpub ``libraries/rigor-record``: ``src/rigor_record/schemas`` and ``golden/fragments``). Assay never imports
that library; this module restates the library's cross-field rules for the assay fragment in a few lines, so a drift on
either side fails a test here.
"""

from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from analysis.tests.analysis_support import PROJECT_ROOT

RIGOR_FIXTURES = PROJECT_ROOT / "analysis" / "tests" / "fixtures" / "rigor"
SCHEMAS = RIGOR_FIXTURES / "schemas"
GOLDEN = RIGOR_FIXTURES / "golden"

#: Strings the privacy rule bans anywhere in a fragment.
DENYLIST = ("/home/", "/workspaces/", "/tmp/", "/run/", "@", "ghp_", "github_pat_", "browser.geekbench.com")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?<![\w/.])[0-9a-fA-F]{0,4}::[0-9a-fA-F]{0,4}(?!\w)")


def _load(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def _validator() -> Draft202012Validator:
    fragment, common = _load("rigor-fragment-1.json"), _load("rigor-common-1.json")
    registry = Registry().with_resources([
        ("urn:vbpub:rigor-fragment:1", Resource.from_contents(fragment)),
        ("rigor-common-1.json", Resource.from_contents(common)),
    ])
    return Draft202012Validator({"$ref": "urn:vbpub:rigor-fragment:1"}, registry=registry)


VALIDATOR = _validator()


def golden(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def violations(document: dict) -> list[str]:
    """Schema errors, then the cross-field rules of the assay fragment, then the privacy lint."""
    found = [f"{'.'.join(map(str, e.absolute_path))}: {e.message}" for e in VALIDATOR.iter_errors(document)]
    if found:
        return found
    if (document["outcome"] == "PASS") != (document["reason"] is None):
        found.append("reason is null exactly when the outcome is PASS")
    r2 = document["r2"]
    if r2 is not None:
        cand, buckets, surv = r2["candidates"], r2["buckets"], r2["survivors"]
        executed = cand["executed"]
        checks = [
            (executed <= cand["total"], "executed exceeds total"),
            (cand["resumed"] <= executed, "resumed exceeds executed"),
            (sum(buckets.values()) == executed, "buckets must add up to executed"),
            (sum(r2["worker_counts"].values()) <= executed, "worker counts exceed executed"),
            (sum(r2["profile_status_counts"].values()) <= executed, "profile counts exceed executed"),
            (surv["total"] == buckets["survived"], "survivors.total must equal buckets.survived"),
            (len(surv["listed"]) == min(surv["total"], 500), "lists min(total, 500) survivors"),
            ((surv["sidecar_sha256"] is not None) == (surv["total"] > 500), "sidecar exactly above 500"),
            (len({s["candidate_id"] for s in surv["listed"]}) == len(surv["listed"]), "duplicate candidate_id"),
            (r2["campaign"] is None or r2["campaign"]["started_at"] <= r2["campaign"]["completed_at"],
             "completed_at precedes started_at"),
        ]
        found += [message for ok, message in checks if not ok]
    for text in _strings(document):
        if any(word in text for word in DENYLIST) or _IPV4.search(text) or _IPV6.search(text):
            found.append(f"privacy: {text!r}")
    return found
