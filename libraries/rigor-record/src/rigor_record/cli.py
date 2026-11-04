"""The ``rigor-record`` command line (cli-extended): ``assemble``, ``render`` and ``check`` (RR-2).

``sync``, ``update``, ``queue``, ``base``, ``adopt`` and ``note`` arrive with RR-3..RR-5 and GN-1. Exit codes:
0 success; 1 a refusal or a failed check (the cause is printed); 2 a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cli_extended import ArgumentSpec, CliFailure, CliIdentity, CliRegistry, OptionSpec, VerbGroup, VerbSpec

from .assemble import Refusal, assemble
from .derive import targets_at_factory
from .check import RELEASES, check_project, check_waivers, load_privacy_config, load_records
from .gitfacts import RepoGit
from .render import RenderError, build_index, render_body, render_record, render_views
from .store import StoreError, check_commits, write_through
from .sync import GhReleaseSource, SyncError, fetch_record, plan_sync
from .update import (
    fragment_key,
    plan_qualification,
    plan_r2,
    label_commits,
    plan_reconcile,
    plan_withdraw,
    qualification_key,
    read_waivers,
    sha12,
    withdraw_key,
)

PRIVACY_ENV = "RIGOR_RECORD_PRIVACY_CONFIG"
RECORD_FILE = "record.json"
BODY_FILE = "release-body.md"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(message: str, *, exit_code: int = 1) -> CliFailure:
    return CliFailure(message, exit_code=exit_code)


def _read_bytes(path: str, what: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise _fail(f"cannot read {what} {path}: {type(exc).__name__}") from exc


def _read_json(path: str, what: str) -> Any:
    try:
        return json.loads(_read_bytes(path, what))
    except ValueError as exc:
        raise _fail(f"{what} {path} is not valid JSON") from exc


def _read_toml(path: str, what: str) -> Any:
    try:
        return tomllib.loads(_read_bytes(path, what).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:  # tomllib.TOMLDecodeError is a ValueError
        raise _fail(f"{what} {path} is not valid TOML") from exc


def _privacy(args: argparse.Namespace) -> tuple[tuple[str, ...], tuple[str, ...]]:
    path = args.privacy_config or os.environ.get(PRIVACY_ENV)
    if not path:
        return (), ()
    try:
        return load_privacy_config(_read_bytes(path, "privacy config").decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise _fail(str(exc)) from exc


def _write(path: Path, text: str) -> None:
    try:
        path.write_bytes(text.encode("utf-8"))
    except OSError as exc:
        raise _fail(f"cannot write {path}: {type(exc).__name__}") from exc


def _emit(runtime, text: str, out: str | None) -> None:
    if out is None:
        runtime.output.stdout.write(text)
        runtime.output.stdout.flush()
    else:
        _write(Path(out), text)


# -- assemble --------------------------------------------------------------------------------------------------
def _fragment_files(directory: str) -> list[tuple[str, bytes]]:
    root = Path(directory)
    if not root.is_dir():
        raise _fail(f"fragments directory {directory} does not exist")
    named = []
    for entry in sorted(root.iterdir()):
        if entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".json"):
            raise _fail(f"fragments directory holds {entry.name}, which is not a regular .json fragment")
        named.append((entry.name, entry.read_bytes()))
    return named


def _assemble(args: argparse.Namespace, runtime) -> None:
    hosts, users = _privacy(args)
    out = Path(args.out)
    if not out.is_dir() or any(out.iterdir()):
        raise _fail(f"--out {args.out} must be an existing empty directory")
    waivers = None if args.waivers is None else _read_toml(args.waivers, "waivers")
    try:
        result = assemble(
            project=args.project, tag=args.tag, commit=args.commit, project_dir=args.project_dir,
            fragments=_fragment_files(args.fragments), review=_read_bytes(args.review, "review"),
            artifacts=_read_json(args.artifacts, "artifact inventory"), r2_lanes=args.r2_lane or (),
            waivers=waivers, published_at=args.published_at or _now(), git=RepoGit(Path(args.repo)),
            hosts=hosts, users=users,
        )
    except Refusal as exc:
        raise _fail("assemble refused:\n  " + "\n  ".join(exc.problems)) from exc
    _write(out / RECORD_FILE, result.record_text)
    _write(out / BODY_FILE, result.body_text)
    runtime.output.primary(
        f"assembled {args.tag}: {RECORD_FILE} ({len(result.record_text.encode())} bytes), "
        f"{BODY_FILE} ({len(result.body_text.encode())} bytes)"
    )


# -- render ----------------------------------------------------------------------------------------------------
def _render(args: argparse.Namespace, runtime) -> None:
    hosts, users = _privacy(args)
    if args.kind == "views":
        _render_views(args, runtime, hosts, users)
        return
    if args.record is None:
        raise _fail(f"--kind {args.kind} needs --record", exit_code=2)
    record = _read_json(args.record, "record")
    try:
        if args.kind == "record":
            text = render_record(record, hosts=hosts, users=users)
        elif args.project_dir is None:
            raise _fail("--kind body needs --project-dir", exit_code=2)
        else:
            text = render_body(record, args.project_dir, hosts=hosts, users=users)
    except RenderError as exc:
        raise _fail("render refused:\n  " + "\n  ".join(exc.problems)) from exc
    _emit(runtime, text, args.out)


def _render_views(args: argparse.Namespace, runtime, hosts, users) -> None:
    if args.project_dir is None or args.out is not None or args.record is not None:
        raise _fail("--kind views takes --project-dir only", exit_code=2)
    root = Path(args.project_dir)
    records, problems = load_records(root, hosts, users)
    if problems:
        raise _fail("render refused:\n  " + "\n  ".join(str(p) for p in problems))
    if not records:
        raise _fail(f"{root / RELEASES} holds no records")
    try:
        views = render_views(records, records[0]["project"], targets_at_factory(records, RepoGit(Path(args.repo))))
    except RenderError as exc:
        raise _fail("render refused:\n  " + "\n  ".join(exc.problems)) from exc
    for relative, text in views.items():
        _write(root / relative, text)
    runtime.output.primary("rendered " + ", ".join(sorted(views)))


# -- check -----------------------------------------------------------------------------------------------------
def _check(args: argparse.Namespace, runtime) -> None:
    hosts, users = _privacy(args)
    root = Path(args.project_dir)
    if args.commits is not None:
        if args.waivers or args.release_version is not None:
            raise _fail("--commits cannot be combined with --waivers or --release-version", exit_code=2)
        try:
            problems = check_commits(Path(args.repo), args.project_dir, args.commits)
        except StoreError as exc:
            raise _fail(str(exc)) from exc
    elif args.waivers:
        if args.release_version is None:
            raise _fail("--waivers needs --release-version", exit_code=2)
        problems = check_waivers(root, args.release_version, hosts=hosts, users=users)
    elif args.release_version is not None:
        raise _fail("--release-version belongs to --waivers", exit_code=2)
    else:
        problems = check_project(root, git=RepoGit(Path(args.repo)), hosts=hosts, users=users)
    if problems:
        raise _fail("check failed:\n  " + "\n  ".join(str(p) for p in problems))
    runtime.output.primary("ok")


# -- sync and update -------------------------------------------------------------------------------------------
def _publish(args: argparse.Namespace, runtime, plan_fn, idem, hosts, users) -> None:
    """Run the write procedure and report its outcome; refusals and git failures exit 1."""
    try:
        outcome = write_through(
            repo=Path(args.repo), project_dir=args.project_dir, plan_fn=plan_fn, push=args.push, idem=idem,
            remote=args.remote, branch=args.branch, lock=None if args.lock_file is None else Path(args.lock_file),
            hosts=hosts, users=users,
        )
    except Refusal as exc:
        raise _fail("refused:\n  " + "\n  ".join(exc.problems)) from exc
    except StoreError as exc:
        raise _fail(str(exc)) from exc
    if outcome.status == "noop":
        runtime.output.primary("nothing to do")
    else:
        note = "" if outcome.status == "pushed" else " (dry run: pass --push to publish)"
        runtime.output.primary(f"{outcome.status}: {outcome.subject} {outcome.commit[:12]}{note}")


def _sync(args: argparse.Namespace, runtime) -> None:
    hosts, users = _privacy(args)
    try:
        record, digest = fetch_record(
            tag=args.tag, project=args.project, project_dir=args.project_dir,
            source=GhReleaseSource(args.github_repo, args.gh), git=RepoGit(Path(args.repo)), hosts=hosts, users=users,
        )
    except Refusal as exc:
        raise _fail("sync refused:\n  " + "\n  ".join(exc.problems)) from exc
    except SyncError as exc:
        raise _fail(str(exc)) from exc
    _publish(
        args, runtime, lambda by_tag, _project: plan_sync(
            record=record, asset_sha256=digest, records=by_tag, hosts=hosts, users=users,
        ),
        sha12(digest), hosts, users,
    )


def _guard_stable(plan_fn, tag: str, git):
    """``--stable`` asserts the release is the index's ``stable`` after the update (memo 5.4)."""
    def guarded(by_commit, path):
        plan = plan_fn(by_commit, path)
        merged = list({**by_commit, **plan.changed}.values())
        if build_index(merged, merged[0]["project"], targets_at_factory(merged, git))["stable"] != tag:
            raise Refusal([f"{tag} would not be the stable release: a newer current release already passed promotion"])
        return plan
    return guarded


def _update(args: argparse.Namespace, runtime) -> None:
    hosts, users = _privacy(args)
    modes = [args.fragments is not None, args.reconcile, args.withdraw is not None]
    if sum(modes) != 1:
        raise _fail("update needs exactly one of --fragments, --reconcile, --withdraw", exit_code=2)
    ref = args.withdraw or args.tag or args.commit
    if args.tag is not None and args.commit is not None:
        raise _fail("--tag and --commit are alternatives", exit_code=2)
    r2 = args.fragments is not None and args.qualification is None
    if ref is None and not r2:
        raise _fail("update needs --tag or --commit (an R2 update takes its commits from the fragments)", exit_code=2)
    if args.stable and args.qualification is None:
        raise _fail("--stable belongs to --qualification", exit_code=2)
    if (args.attribute_to is not None or args.base is not None or args.lane is not None) and not r2:
        raise _fail("--attribute-to, --base and --lane belong to an R2 update (--fragments without --qualification)", exit_code=2)
    git, expect = RepoGit(Path(args.repo)), args.expect_revision
    idem = None
    if args.fragments is not None:
        fragments = _fragment_files(args.fragments)
        digests = [hashlib.sha256(data).hexdigest() for _name, data in fragments]
        if args.qualification is not None:
            def plan_fn(by_commit, _project):
                return plan_qualification(
                    ref=ref, name=args.qualification, records=by_commit, fragments=fragments, project_dir=args.project_dir,
                    git=git, expect_revision=expect, hosts=hosts, users=users,
                )
            idem = qualification_key(args.qualification, ref, digests)
            if args.stable:
                plan_fn = _guard_stable(plan_fn, ref, git)
        elif args.attempts is None:
            raise _fail("an R2 update needs --attempts", exit_code=2)
        else:
            idem = fragment_key(digests, "" if args.attribute_to is None else f"\nattribute-to {args.attribute_to}")
            def plan_fn(by_commit, _project):
                base, base_label = _base(by_commit, args.base)
                return plan_r2(
                    records=by_commit, fragments=fragments, lane=args.lane, attempts=args.attempts,
                    closeout_exit=args.closeout_exit, base=base, base_label=base_label, attribute_to=args.attribute_to,
                    project_dir=args.project_dir, git=git, expect_revision=expect, hosts=hosts, users=users,
                )
    elif args.reconcile:
        def plan_fn(by_commit, project):
            return plan_reconcile(
                ref=ref, records=by_commit, waivers=read_waivers(project, hosts, users), git=git,
                expect_revision=expect, hosts=hosts, users=users,
            )
    elif args.reason is None or args.replacement is None:
        raise _fail("--withdraw needs --reason and --replacement", exit_code=2)
    else:
        idem = withdraw_key(ref, args.reason, args.replacement)
        def plan_fn(by_commit, _project):
            return plan_withdraw(
                tag=ref, records=by_commit, reason=args.reason, replacement=args.replacement, git=git,
                expect_revision=expect, hosts=hosts, users=users,
            )
    _publish(args, runtime, plan_fn, idem, hosts, users)


def _base(records: dict[str, dict], base: str | None) -> tuple[str | None, str | None]:
    """``--base`` names a commit (40 hex) or a release tag; the tag is kept as the scope's ``base_label``."""
    if base is None:
        return None, None
    if len(base) == 40 and all(c in "0123456789abcdef" for c in base):
        return base, None
    commit = label_commits(records).get(base)
    if commit is None:
        raise Refusal([f"base {base} is neither a commit nor a release tag with a record"])
    return commit, base


# -- registry --------------------------------------------------------------------------------------------------
def _opt(flags: tuple[str, ...], description: str, metavar: str | None = None, *, required: bool = False, group: str = "INPUT", **extra: Any) -> OptionSpec:
    kwargs = {"default": None, **extra}
    if required:
        kwargs["required"] = True
    return OptionSpec(flags, description, group=group, metavar=metavar, parser_kwargs=kwargs)


_PRIVACY_OPTION = _opt(
    ("--privacy-config",),
    f"operator's local host and user denylist (TOML, never committed); default: ${PRIVACY_ENV}",
    "FILE", group="PRIVACY",
)


def _build_cli(identity: CliIdentity | None = None):
    if identity is None:
        identity = CliIdentity.resolve(
            name="RIGOR-RECORD", long_name="rigor-evidence record tooling", command="rigor-record",
            distribution="rigor-record",
        )
    registry = CliRegistry(
        identity, prog="rigor-record",
        description="Assemble, render and check the per-release rigor-evidence record.",
        getting_started=("rigor-record check --project-dir cmru", "rigor-record render --kind views --project-dir cmru"),
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "assemble",
        description="build the release-time record and release-body appendix from gate fragments; refuses a tree mismatch, an unbound review or an expired waiver",
        group=VerbGroup.MODIFICATION.value, mutating=True, confirmation_required=False, include_json=False, include_progress=False,
        examples=("rigor-record assemble --project P --tag P-v1.0.0 --commit C --project-dir P --fragments F --review R.md --artifacts build.json --out OUT",),
        options=(
            _opt(("--project",), "project name", "NAME", required=True),
            _opt(("--tag",), "release tag, <prefix>-v<version>", "TAG", required=True),
            _opt(("--commit",), "the tag's commit (40 hex)", "SHA", required=True),
            _opt(("--project-dir",), "project directory relative to the repository root (`.` for the root)", "DIR", required=True),
            _opt(("--repo",), "repository checkout holding the tag", "DIR", default="."),
            _opt(("--fragments",), "directory of gate fragments (*.json)", "DIR", required=True),
            _opt(("--review",), "review document carrying the rigor-review line", "FILE", required=True),
            _opt(("--artifacts",), "cmru build.json inventory", "FILE", required=True),
            _opt(("--r2-lane",), "an R2 lane of the project (repeatable); recorded in the inventory, never a sequence member", "LANE", action="append"),
            _opt(("--waivers",), "rigor/waivers.toml", "FILE"),
            _opt(("--published-at",), "publication time, YYYY-MM-DDTHH:MM:SSZ; default: now (UTC)", "TIME"),
            _opt(("--out",), "existing empty directory that receives record.json and release-body.md", "DIR", required=True, group="OUTPUT"),
            _PRIVACY_OPTION,
        ),
        handler=_assemble,
    ))
    registry.register(VerbSpec(
        "render",
        description="render a record, a release-body appendix, or a project's index.json and RIGOR.md (byte-deterministic)",
        group=VerbGroup.MODIFICATION.value, mutating=True, confirmation_required=False, include_json=False, include_progress=False,
        examples=("rigor-record render --kind views --project-dir cmru", "rigor-record render --kind body --record R.json --project-dir cmru"),
        options=(
            _opt(("--kind",), "what to render", "KIND", required=True, choices=("record", "body", "views")),
            _opt(("--record",), "record JSON (record and body)", "FILE"),
            _opt(("--project-dir",), "project directory (views; body names its RIGOR.md)", "DIR"),
            _opt(("--out",), "output file; default: standard output", "FILE", group="OUTPUT"),
            _opt(("--repo",), "repository checkout whose object ids decide which results are valid (views)", "DIR", default="."),
            _PRIVACY_OPTION,
        ),
        handler=_render,
    ))
    registry.register(VerbSpec(
        "check",
        description="offline check that every record is valid, the views equal their regeneration and the hand-written files are valid; with --waivers, the pre-gate waiver preflight",
        group=VerbGroup.EXPLORATION.value, include_json=False, include_progress=False,
        examples=("rigor-record check --project-dir cmru", "rigor-record check --project-dir cmru --waivers --release-version 1.2.0"),
        options=(
            _opt(("--project-dir",), "project directory", "DIR", required=True),
            _opt(("--waivers",), "check only the waivers against --release-version", None, action="store_true", default=False),
            _opt(("--release-version",), "release version the waivers are checked against", "VERSION"),
            _opt(("--commits",), "check the commits of this git range (for example origin/main~20..origin/main) touching generated rigor files carry the chore(rigor): prefix and a Rigor-Record trailer", "RANGE"),
            _opt(("--repo",), "repository checkout (with --commits)", "DIR", default="."),
            _PRIVACY_OPTION,
        ),
        handler=_check,
    ))
    write_options = (
        _opt(("--project-dir",), "project directory relative to the repository root (`.` for the root)", "DIR", required=True),
        _opt(("--repo",), "repository checkout; its fetch refspec for the remote is used, its worktree is never touched", "DIR", default="."),
        _opt(("--remote",), "remote carrying the branch", "NAME", default="origin", group="GIT"),
        _opt(("--branch",), "branch the commit is pushed to", "NAME", default="main", group="GIT"),
        _opt(("--lock-file",), "single-writer lock; default: ~/.local/state/rigor-record/<repo>.lock", "FILE", group="GIT"),
        _opt(("--push",), "publish the commit; without it the procedure runs in a throw-away worktree and reports (dry run)", None, action="store_true", default=False, group="GIT"),
        _PRIVACY_OPTION,
    )
    registry.register(VerbSpec(
        "sync",
        description="commit the release-time record (the release's rigor asset, sha256-checked) to main; idempotent",
        group=VerbGroup.MODIFICATION.value, mutating=True, confirmation_required=False, include_json=False, include_progress=False,
        examples=("rigor-record sync --project cgroup-profiler --tag cgprofile-v2.0.0 --project-dir cgroup-profiler --push",),
        options=(
            _opt(("--project",), "project name", "NAME", required=True),
            _opt(("--tag",), "release tag whose rigor asset is committed", "TAG", required=True),
            _opt(("--github-repo",), "OWNER/NAME for gh; default: gh's own resolution from the checkout", "SLUG"),
            _opt(("--gh",), "the gh executable", "PATH", default="gh"),
            *write_options,
        ),
        handler=_sync,
    ))
    registry.register(VerbSpec(
        "update",
        description="apply R2 fragments, a qualification, survivor waivers (--reconcile) or a withdrawal to the record of a commit and commit it to main",
        group=VerbGroup.MODIFICATION.value, mutating=True, confirmation_required=False, include_json=False, include_progress=False,
        examples=(
            "rigor-record update --fragments INBOX --attempts 2 --closeout-exit 1 --base B --project-dir P --push",
            "rigor-record update --fragments INBOX --attempts 1 --closeout-exit 0 --attribute-to COMMIT --project-dir P --push",
            "rigor-record update --tag T --reconcile --project-dir P --push",
            "rigor-record update --withdraw T --reason WHY --replacement T2 --project-dir P --push",
        ),
        options=(
            _opt(("--tag",), "release tag whose record is updated (qualification, reconcile)", "TAG"),
            _opt(("--commit",), "commit whose record is updated (alternative to --tag)", "SHA"),
            _opt(("--attribute-to",), "R2 only: store the result at this published commit, whose targets must equal the judged ones", "SHA"),
            _opt(("--fragments",), "directory of gate fragments (R2 campaign, or the lanes of a qualification)", "DIR"),
            _opt(("--attempts",), "campaign attempts (R2 fragments)", "N", type=int),
            _opt(("--closeout-exit",), "optional cross-check: the close-out exit of the last attempt (0, 1 complete; 2 error; 3 incomplete); the status is read from the fragment and a differing value refuses", "N", type=int, choices=(0, 1, 2, 3)),
            _opt(("--lane",), "pin the R2 lane the fragments must carry (default: the one lane they carry)", "LANE"),
            _opt(("--base",), "commit or release tag the campaign was based on; default: whole-source", "REF"),
            _opt(("--qualification",), "record the fragments as this post-publish qualification (for example promotion)", "NAME"),
            _opt(("--stable",), "with --qualification: require the release to become the stable one", None, action="store_true", default=False),
            _opt(("--reconcile",), "apply the survivor dispositions of rigor/waivers.toml to the open survivors", None, action="store_true", default=False),
            _opt(("--withdraw",), "withdraw this release (publish the replacement first)", "TAG"),
            _opt(("--replacement",), "the release that replaces the withdrawn one", "TAG"),
            _opt(("--reason",), "reason (withdraw)", "TEXT"),
            _opt(("--expect-revision",), "compare-and-swap: refuse unless the record is at this revision", "N", type=int),
            *write_options,
        ),
        handler=_update,
    ))
    return registry.build()


def main(argv: list[str] | None = None, **run_kwargs: Any) -> int:
    from cli_extended.identity import VersionLookupError

    try:
        cli = _build_cli()
    except VersionLookupError:
        print("[ERROR] rigor-record is not installed as a distribution; install the wheel", file=sys.stderr)
        return 3
    return cli.run(argv=argv, **run_kwargs)
