"""PWMCP-58: the Obscura re-evaluation triggers and the anti-detection non-goal stay a closed set.

The 2026-11-09 comparison decided "reject adoption now, borrow ideas", with five
re-evaluation triggers and an explicit non-goal. Without a pinned record a later
"lightweight browser" request could reopen adoption without rechecking protocol fit,
maintenance, supply-chain, crash and policy risk.

This module keeps the decision a CLOSED VOCABULARY in `docs/DESIGN-GUIDE.md` and
checks that the same stance still appears in the other user-facing documents and in
the packaged skill. A capability cannot be re-opened by editing one document.
"""

from __future__ import annotations

import re
from pathlib import Path

PWMCP = Path(__file__).resolve().parents[1]
DESIGN = PWMCP / "docs" / "DESIGN-GUIDE.md"
COMPARISON = PWMCP / "docs" / "COMPARISON-obscura-2026-11-09.md"
README = PWMCP / "README.md"
SECURITY = PWMCP / "docs" / "SECURITY.md"
SKILL = PWMCP / "client" / "src" / "pwmcp_client" / "skills" / "pwmcp-fetch" / "SKILL.md"

# The five triggers, in the comparison's own order and wording (a closed set).
TRIGGERS = (
    "a consumer need for a very cheap extraction engine is documented",
    "Obscura reaches a stable API or 1.0 with checksums, signatures or attestations",
    "it has at least two active maintainers or an organisation owner",
    "the SIGSEGV and Playwright-compatibility issues in our evaluation corpus are closed",
    "the operator is comfortable with the project's stealth-feature direction",
)

# What the 2026-11-09 comparison itself says (semicolon-separated, slash form). Both documents
# must keep the SAME five facts, so a re-open cannot edit one of them.
COMPARISON_TRIGGERS = (
    "a consumer need for a very cheap extraction engine is documented",
    "reaches a stable API or 1.0 with checksums/signatures or attestations",
    "at least two active maintainers or an organisation owner",
    "SIGSEGV and Playwright-compatibility issues in the evaluation corpus are closed",
    "the operator is comfortable with the stealth-feature direction",
)

# The prohibited capabilities: each must be named as something PWMCP does NOT do.
NON_GOAL_TERMS = (
    "fingerprint randomisation",
    "impersonation",
    "navigator.webdriver",
    "isTrusted",
    "rotation for evasion",
    "challenge solver",
)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _design_section(text: str) -> str:
    """The Obscura entry of "Competitors and alternatives" (and nothing after it)."""

    match = re.search(r"### Obscura \(checked [^)]*\)\n(.*?)(?=\n### |\n## |$)", text, re.DOTALL)
    assert match is not None, "docs/DESIGN-GUIDE.md has no dated Obscura entry under 'Competitors and alternatives'"
    return match.group(1)


def test_the_design_guide_records_every_re_evaluation_trigger() -> None:
    section = _normalized(_design_section(DESIGN.read_text(encoding="utf-8")))
    for number, trigger in enumerate(TRIGGERS, 1):
        assert trigger in section, f"trigger {number} is missing: {trigger}"
    assert "Re-evaluation triggers (closed set)" in section
    assert "ONLY when ALL of these" in section
    # popularity/memory alone is named as NOT sufficient, so the triggers cannot be re-read as one of them
    assert "Popularity, a lower claimed memory footprint" in section


def test_the_design_guide_records_the_anti_detection_non_goal() -> None:
    section = _normalized(_design_section(DESIGN.read_text(encoding="utf-8")))
    assert "Explicit non-goal (no anti-detection, ever)" in section
    for term in NON_GOAL_TERMS:
        assert term in section, f"the non-goal does not name {term}"
    # never as the official image's replacement, and never the stealth build even after re-evaluation
    assert "never as a replacement for the official Playwright image" in section
    assert "no stealth or\nanti-detection capability" in section or "no stealth or anti-detection capability" in section


def test_the_comparison_keeps_the_same_five_triggers() -> None:
    comparison = _normalized(COMPARISON.read_text(encoding="utf-8"))
    trigger_sentence = re.search(r"Re-evaluate only if all hold:(.*?)Even then", comparison, re.DOTALL)
    assert trigger_sentence is not None, "the comparison's 'Re-evaluate only if all hold' paragraph is gone"
    body = trigger_sentence.group(1)
    for number, trigger in enumerate(COMPARISON_TRIGGERS, 1):
        assert trigger.lower() in body.lower(), f"comparison trigger {number} changed: {trigger}"


def test_every_user_facing_document_states_the_stance() -> None:
    """README, SECURITY and the packaged skill keep the stance (a capability cannot ship undocumented)."""

    readme = _normalized(README.read_text(encoding="utf-8"))
    assert "No challenge solver, no CAPTCHA-solving service, no stealth or anti-detection patch" in readme
    assert "no fingerprint spoofing" in readme
    # SECURITY restates the same prohibition term by term: a reader of the security document
    # cannot be told a narrower boundary than the design guide records.
    security = _normalized(SECURITY.read_text(encoding="utf-8"))
    for term in NON_GOAL_TERMS:
        assert term in security, f"docs/SECURITY.md does not name {term}"
    assert "Bot-protection and anti-detection stance" in security
    skill = _normalized(SKILL.read_text(encoding="utf-8"))
    assert "solver services or stealth tooling" in skill
    assert "Do not retry in a loop" in skill
    # every document also points at the decision itself
    assert "COMPARISON-obscura-2026-11-09.md" in (PWMCP / "README.md").read_text(encoding="utf-8")


def test_the_design_guide_entry_is_linked_from_the_document_index() -> None:
    readme = (PWMCP / "README.md").read_text(encoding="utf-8")
    assert "(docs/DESIGN-GUIDE.md#competitors-and-alternatives)" in readme
    design = DESIGN.read_text(encoding="utf-8")
    assert "(COMPARISON-obscura-2026-11-09.md)" in design
