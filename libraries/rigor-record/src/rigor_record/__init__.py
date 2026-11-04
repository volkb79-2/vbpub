"""rigor-record: interchange schemas, validator, privacy lint (RR-1), and assemble / render / check (RR-2) for the vbpub rigor-evidence record."""

from .assemble import Assembled, Refusal, assemble
from .check import Problem, check_project, check_waivers, load_privacy_config
from .gitfacts import GitError, RepoGit
from .privacy import Finding, lint, lint_string
from .render import RenderError, render_body, render_index, render_project_md, render_record, render_views
from .schema import Validator, Violation, load_schema
from .derive import latest_label, level_summary, valid_at, valid_results, worst
from .validate import (
    LIMITS,
    MIN_N,
    canonical_json,
    check_size,
    is_expired,
    load_waivers,
    validate_fragment,
    validate_index,
    validate_record,
    validate_waivers,
)

__all__ = [
    "Assembled", "Finding", "GitError", "LIMITS", "MIN_N", "Problem", "RenderError", "RepoGit", "Refusal", "Validator",
    "Violation", "assemble", "canonical_json", "check_project", "check_size", "check_waivers", "is_expired",
    "latest_label", "level_summary", "lint", "lint_string", "load_privacy_config", "load_schema", "load_waivers",
    "render_body", "render_index", "render_project_md", "render_record", "render_views", "valid_at", "valid_results",
    "validate_fragment", "validate_index", "validate_record", "validate_waivers", "worst",
]
