"""Shared home-relative runner paths used by real and fake transports."""

ASSAY_DIR = ".assay"
PLAN_IDS_DIR = "fleetd-plan"
PROGRESS_PREFIX = "progress-"
PROGRESS_SUFFIX = ".jsonl"


def project_relative(project: str, path: str) -> str:
    prefix = "" if project == "." else f"{project}/"
    return f"{prefix}{path}"


def candidate_ids_relative(campaign: str, host: str, job_name: str) -> str:
    return f"{ASSAY_DIR}/{PLAN_IDS_DIR}/{campaign}/{host}/{job_name}.ids"


def candidate_ids_home_path(commit: str, project: str, campaign: str, host: str, job_name: str) -> str:
    return f"checkouts/{commit}/{project_relative(project, candidate_ids_relative(campaign, host, job_name))}"


def progress_relative(assay_lane: str) -> str:
    return f"{ASSAY_DIR}/{PROGRESS_PREFIX}{assay_lane}{PROGRESS_SUFFIX}"


def progress_home_path(commit: str, project: str, assay_lane: str) -> str:
    return f"checkouts/{commit}/{project_relative(project, progress_relative(assay_lane))}"
