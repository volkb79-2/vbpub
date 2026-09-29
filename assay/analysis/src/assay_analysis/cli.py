"""The ``assay analyze`` command line: parser and dispatch over the evidence readers."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import TextIO

from assay.cli import AssayArgumentParser
from assay.errors import AssayError

from assay_analysis import evidence, plan_estimate


def _workers(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if not 1 <= value <= 64:
        raise argparse.ArgumentTypeError("--workers must be an integer from 1 to 64")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = AssayArgumentParser(prog="assay analyze")
    commands = parser.add_subparsers(dest="analysis_command", required=True)
    archive = commands.add_parser("collect", help="create a fresh artifact archive and manifest")
    archive.add_argument("--output", type=Path, required=True)
    archive.add_argument("--artifact", metavar=("NAME", "FILE"), nargs=2, action="append", default=[])
    archive.add_argument("--receipt", type=Path, help="include a receipt and all its fingerprinted inputs")
    check = commands.add_parser("check", help="check a collected archive's byte integrity")
    check.add_argument("directory", type=Path)
    launcher = commands.add_parser("launcher", help="inspect existing tester-unified launch evidence")
    launcher.add_argument("directory", type=Path)
    launcher.add_argument("--expected-commit", required=True)
    producer = commands.add_parser("record", help="record one explicit command and its actual exit")
    producer.add_argument("--worktree", type=Path, required=True)
    producer.add_argument("--expected-head", required=True)
    producer.add_argument("--output", type=Path, required=True)
    producer.add_argument("argv", nargs=argparse.REMAINDER)
    bound = commands.add_parser("receipt", help="bind recorded facts to a current clean worktree")
    bound.add_argument("--worktree", type=Path, required=True)
    bound.add_argument("--expected-head", required=True)
    bound.add_argument("--recorded", metavar=("NAME", "PREFIX", "MARKER"), nargs=3, action="append", default=[])
    bound.add_argument("--tester-run", metavar=("NAME", "DIRECTORY"), nargs=2, action="append", default=[])
    bound.add_argument("--verdict", metavar=("NAME", "FILE"), nargs=2, action="append", default=[])
    bound.add_argument("--progress", metavar=("NAME", "FILE"), nargs=2, action="append", default=[])
    bound.add_argument("--output", type=Path, required=True)
    verdict = commands.add_parser("verdict", help="inspect an Assay verdict at an explicit commit")
    verdict.add_argument("path", type=Path)
    verdict.add_argument("--expected-commit", required=True)
    verdict.add_argument("--format", choices=("json", "text"), default="json")
    snapshot = commands.add_parser("report", help="bounded snapshot of explicit lane evidence")
    snapshot.add_argument("--expected-commit", type=evidence._report_commit, required=True)
    for option in ("verdict", "progress", "log"):
        snapshot.add_argument("--" + option, metavar=("LANE", "FILE"), nargs=2,
                              action="append", default=[])
    snapshot.add_argument("--format", choices=("json", "text"), default="json")
    snapshot.add_argument("--max-errors", type=evidence._report_max_errors, default=5)
    progress = commands.add_parser("progress", help="summarize matching runs in appended progress JSONL")
    progress.add_argument("path", type=Path)
    progress.add_argument("--expected-commit", required=True)
    estimate = commands.add_parser(
        "plan-estimate",
        help="project campaign hours from an assay plan and a measured baseline (diagnostic)")
    estimate.add_argument("--plan-json", type=Path, required=True, metavar="PLAN")
    estimate.add_argument("--progress", type=Path, required=True, metavar="PROGRESS")
    estimate.add_argument("--workers", type=_workers, default=1, metavar="N")
    return parser


def cmd_analyze(args: argparse.Namespace, *, stdout: TextIO, stderr: TextIO) -> int:
    code = 0
    try:
        if args.analysis_command == "collect":
            result = evidence.collect(args.output, args.artifact, args.receipt)
        elif args.analysis_command == "check":
            result = evidence.check_archive(args.directory)
        elif args.analysis_command == "launcher":
            result = evidence._tester_run(args.directory, args.expected_commit)
            result["validation"] = "launcher-record-consistency-and-expected-commit"
        elif args.analysis_command == "record":
            if args.argv[:1] != ["--"]:
                raise ValueError("record requires a command after --")
            command = args.argv[1:]
            result, code = evidence.record(args.worktree, args.expected_head, args.output, command)
        elif args.analysis_command == "verdict":
            result = evidence.inspect_verdict(args.path, args.expected_commit)
            if args.format == "text":
                fact = result["summary"]
                print(f"{fact['lane']}: {fact['outcome']} (recorded exit {fact['exit_code']}) "
                      f"at {fact['commit']}; Assay {fact['assay_version']}", file=stdout)
                for claim in result["verdict"]["claims"]:
                    print(f"  {claim['rigor']}: {claim['status']}" +
                          (f" / {claim['reason_code']}" if claim.get("reason_code") else ""), file=stdout)
                    if "coverage" in claim:
                        coverage = claim["coverage"]
                        branches = (f"branches {coverage['branches_covered']}/{coverage['branches_total']}"
                                    if {"branches_covered", "branches_total"} <= coverage.keys()
                                    else "branch counts absent")
                        print(f"    lines {coverage['covered']}/{coverage['executable']}; {branches}", file=stdout)
                    if "mutation" in claim:
                        mutation = claim["mutation"]
                        buckets = ", ".join(f"{key}={len(value)}" for key, value in mutation.items()
                                            if isinstance(value, list))
                        print(f"    mutants {mutation['total']}; {buckets}", file=stdout)
                if fact["outcome"] != "PASS":
                    for stream in ("stdout", "stderr"):
                        tail_key = f"result_{stream}_tail"
                        dropped_key = f"result_{stream}_dropped_bytes"
                        document = result["verdict"]
                        if tail_key in document:
                            print(f"  captured {stream} tail:", file=stdout)
                            print(document[tail_key], file=stdout)
                        if dropped_key in document:
                            print(f"  {stream} dropped bytes: {document[dropped_key]}", file=stdout)
                return 0
        elif args.analysis_command == "report":
            result = evidence.report(args.expected_commit, args.verdict, args.progress, args.log, args.max_errors)
            code = result["exit_code"]
            if args.format == "text":
                evidence._report_text(result, stdout)
                return code
        elif args.analysis_command == "progress":
            result = evidence.inspect_progress(args.path, args.expected_commit)
        elif args.analysis_command == "plan-estimate":
            result = plan_estimate.plan_estimate(args.plan_json, args.progress, workers=args.workers)
        else:
            result = evidence.receipt(args.worktree, args.expected_head, args.recorded,
                                      args.tester_run, args.verdict, args.progress)
            output = args.output.resolve()
            root = Path(result["worktree"])
            evidence._output_location(root, output)
            evidence._write_new(output, result)
    except (OSError, ValueError, RecursionError, KeyError, TypeError,
            AttributeError, AssayError, subprocess.CalledProcessError) as exc:
        print(f"assay analyze: {exc}", file=stderr)
        return 2 if args.analysis_command in ("report", "plan-estimate") else 1
    print(json.dumps(result, indent=2, sort_keys=True), file=stdout)
    return code


def main(argv, *, stdout: TextIO, stderr: TextIO) -> int:
    return cmd_analyze(build_parser().parse_args(list(argv)), stdout=stdout, stderr=stderr)
