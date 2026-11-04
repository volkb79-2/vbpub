"""Privacy lint (SPEC section 8, layer 2). vbpub is public, so a record, a view, a release body or a git note line
must never carry host identity, local paths, user names or secrets.

The lint is a denylist over every string in a JSON document, keys included. A finding names the JSON path and the
rule. It never echoes the matched text: an error message that repeated a token would itself leak it into a log.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Iterable

#: Substrings that are always a local path, listed separately from the generic absolute-path rule so that the
#: reported rule names the most specific class.
LOCAL_PATH_PREFIXES = ("/home/", "/workspaces/", "/tmp/", "/run/")

_ABS_PATH = re.compile(r"(?<![A-Za-z0-9_.)\]:/-])/[A-Za-z0-9_.-]+(?:/|\b)")
_HOME_TILDE = re.compile(r"(?:^|[\s=:])~(?:/|\s|$)")
_WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_TOKEN_PREFIX = re.compile(
    r"ghp_|gho_|ghu_|ghs_|ghr_|github_pat_|glpat-|xox[abprs]-|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{10,}\."
)
_TOKEN_LIKE = re.compile(r"[A-Za-z0-9+/_-]{32,}")
_SECRET_WORD = re.compile(r"(?i)\b(?:bearer|token|secret|password|passwd|apikey|api_key)\b\s*[=:]")
_ENV_ASSIGNMENT = re.compile(r"\b[A-Z][A-Z0-9_]{2,}=\S")
_UID = re.compile(r"(?i)\b(?:uid|gid)\s*[=: ]\s*[0-9]+")
_IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
_IPV6 = re.compile(r"(?<![0-9A-Za-z:])[0-9A-Fa-f:]{3,}(?![0-9A-Za-z:])")
_GEEKBENCH = re.compile(r"(?i)geekbench\.com")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: Every rule id the lint can report, for tests and for documentation.
RULES = (
    "local-path", "abs-path", "home-tilde", "windows-path", "url", "at-sign", "token-prefix", "token-like",
    "secret-word", "env-assignment", "uid", "ipv4", "ipv6", "geekbench", "host", "user", "control-char",
)


@dataclass(frozen=True)
class Finding:
    """One lint hit. ``path`` is a ``$.a.b[0]`` style location; ``rule`` is one of :data:`RULES`."""

    path: str
    rule: str

    def __str__(self) -> str:
        return f"{self.path}: privacy rule {self.rule}"


def _is_ip(token: str, family: type) -> bool:
    try:
        family(token)
    except ValueError:
        return False
    return True


def _token_like(text: str) -> bool:
    for match in _TOKEN_LIKE.finditer(text):
        word = match.group(0)
        if any(c.islower() for c in word) and any(c.isupper() for c in word) and any(c.isdigit() for c in word):
            return True
    return False


def _at_sign(text: str, is_cpu_model: bool) -> bool:
    # The one sanctioned use: the CPU model string "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz" (SPEC 8.2).
    if is_cpu_model:
        text = text.replace(" @ ", "")
    return "@" in text


def lint_string(
    text: str,
    path: str = "$",
    *,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> list[Finding]:
    """Lint one string. ``hosts`` and ``users`` are the operator's local denylists (never committed)."""
    hits: list[str] = []
    if any(prefix in text for prefix in LOCAL_PATH_PREFIXES):
        hits.append("local-path")
    if _ABS_PATH.search(text):
        hits.append("abs-path")
    if _HOME_TILDE.search(text):
        hits.append("home-tilde")
    if _WINDOWS_PATH.search(text):
        hits.append("windows-path")
    if _URL.search(text):
        hits.append("url")
    if _at_sign(text, path.endswith(".cpu_model")):
        hits.append("at-sign")
    if _TOKEN_PREFIX.search(text):
        hits.append("token-prefix")
    if _token_like(text):
        hits.append("token-like")
    if _SECRET_WORD.search(text):
        hits.append("secret-word")
    if _ENV_ASSIGNMENT.search(text):
        hits.append("env-assignment")
    if _UID.search(text):
        hits.append("uid")
    if any(_is_ip(m.group(0), ipaddress.IPv4Address) for m in _IPV4.finditer(text)):
        hits.append("ipv4")
    if any(m.group(0).count(":") >= 2 and _is_ip(m.group(0), ipaddress.IPv6Address) for m in _IPV6.finditer(text)):
        hits.append("ipv6")
    if _GEEKBENCH.search(text):
        hits.append("geekbench")
    lowered = text.lower()
    if any(h and h.lower() in lowered for h in hosts):
        hits.append("host")
    if any(u and re.search(rf"(?<![A-Za-z0-9]){re.escape(u.lower())}(?![A-Za-z0-9])", lowered) for u in users):
        hits.append("user")
    if _CONTROL.search(text):
        hits.append("control-char")
    return [Finding(path, rule) for rule in hits]


def lint(
    document: Any,
    path: str = "$",
    *,
    hosts: Iterable[str] = (),
    users: Iterable[str] = (),
) -> list[Finding]:
    """Lint every string value and every object key of a JSON-shaped document."""
    hosts = tuple(hosts)
    users = tuple(users)
    findings: list[Finding] = []
    if isinstance(document, str):
        findings += lint_string(document, path, hosts=hosts, users=users)
    elif isinstance(document, dict):
        for key, value in document.items():
            key_findings = lint_string(str(key), f"{path}.<key>", hosts=hosts, users=users)
            findings += key_findings
            # A key that itself fails the lint must not be echoed through the path of its value.
            child = f"{path}.<redacted-key>" if key_findings else f"{path}.{key}"
            findings += lint(value, child, hosts=hosts, users=users)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            findings += lint(value, f"{path}[{index}]", hosts=hosts, users=users)
    return findings
