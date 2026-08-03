"""Route adapters: dispatch argv, probes, resume, usage extraction. PACKAGE P03.

Everything volatile about the four CLIs, table-driven from RouteDef
(config.Routes). NO free-text shell: argv lists only, rendered from trusted
templates. This module performs NO event writes.

INTERFACE CONTRACT (frozen):

- Placeholders in RouteDef list templates: {session}, {worktree}, {prompt},
  {task_id}, {handoff}, {model}. render_argv() substitutes them INSIDE list
  elements (str.format_map with missing keys -> AdapterError).
- build_dispatch composes the CLI-specific base invocation:
    claude:   [cli, '-p', prompt, '--output-format', 'stream-json',
               '--verbose', '--model', model] (+ ['--effort', effort] if set)
               (+ dispatch_extra)
               (P14 2026-07-15: `-p --output-format json` writes its ENTIRE
               output at process exit -- the log's mtime is structurally
               dead as a liveness signal until the CLI is already done.
               stream-json emits incremental JSONL lines as the turn
               progresses, so the log's mtime IS a real heartbeat and a
               live dashboard tail works. extract_usage below still finds
               the final `result` line by the same "last json-parsing
               brace-line with usage/total_cost_usd" rule.)
    codex:    [cli, 'exec', '--sandbox', sandbox or 'workspace-write',
               '--cd', worktree, prompt] (+ model via ['-m', model])
              (+ ['-c', 'model_reasoning_effort="<effort>"'] if route.effort;
               codex has no --effort flag, so effort rides a config override)
    opencode: [cli, 'run', '--model', model, '--dir', worktree]
              (+ ['--variant', variant] if set) (+ dispatch_extra) + [prompt]
    reasonix: [cli, 'run', '-dir', worktree, prompt]
    fake:     [cli] + dispatch_extra + [prompt]        (test route)
  The PROMPT is short (<= route.argv_max or 1500 chars; AdapterError if the
  rendered prompt exceeds it): it names the handoff path, worktree, branch,
  gate command hint and the receipt requirement; substance stays in the
  handoff (v2: argv wedge). If 'incremental-write' in route.prompt_hints,
  append the fixed sentence about ~80-line write batches.
  (P44 2026-07-19: the PROMPT TEXT is now role-scoped via the keyword-only
  `role: Role = Role.IMPLEMENTER` param -- everything above (the per-CLI argv
  shapes, prompt_hints appends, argv_max check) is unchanged and role-
  agnostic. IMPLEMENTER (the default, so every pre-existing call site that
  does not pass `role` keeps today's exact text) still gets Handoff:/
  Worktree:/Branch:/Gate:/Receipt: + "you MUST git commit ALL your work on
  the branch". CARVER gets its own prompt: when the optional
  `carve_authority` kwarg is 'files' the commit instruction is DROPPED
  entirely (that authority writes new handoff files WITHOUT committing --
  see daemon.py's module docstring above `_CARVE_AUTHORITIES`); 'branch'/
  'main'/unset keep a carve-worded commit instruction. REVIEW_INDEPENDENT gets
  its own prompt that never claims a branch to commit to and never says
  "git commit" -- fixes the live bug where a reviewer dispatched with
  branch=cfg.default_branch got told to commit to main.)
- probe(route) runs route.probe argv (subprocess, timeout 60s, captured):
  returns (ok: bool, detail: str). probe == 'session-limit-check' or
  'one-token-ping' are named builtins: for the pilot both execute
  [route.cli, '--version'] as a cheap liveness proxy (documented limitation).
  probe None -> (True, 'no-probe').
- build_resume(route, session, worktree, prompt) -> argv from route.resume
  template; AdapterError if template empty or session required but None
  ('{session}' present in template).
- capture_session(route, attempt_dir, worktree, launched_at, log_path=None)
  -> str | None:
    P17 2026-07-15: route.cli == 'claude' -- the FIRST line the CLI ever
      writes under --output-format stream-json (P14) is a JSON object
      carrying `session_id` (json.loads(line).get('session_id')). Read it
      straight from the attempt log (log_path if the caller passes it --
      the wrapper always knows the exact log file for the CURRENT run, both
      on first dispatch and on resume; else <attempt_dir>/attempt.log) --
      no subprocess, no directory scan. This REPLACES newest-jsonl for
      claude routes: scanning ~/.claude/projects/<slug>/ for the newest
      file modified after launched_at is now both unnecessary (the id is
      already in hand) and unreliable (concurrent claude processes / a
      missing or lagging project dir raced None in production). A missing
      log file, empty first line, non-JSON first line, or a first line
      lacking a non-empty string `session_id` all degrade to None (never
      raises) -- no newest-jsonl fallback for claude routes.
    session_capture == 'newest-jsonl' (non-claude routes only, now): newest
      *.jsonl under ~/.claude/projects/<slug(worktree)>/ modified after
      launched_at, where slug replaces '/' with '-' (leading '-' kept:
      /a/b -> -a-b).
    session_discover argv set: run it (timeout 30), parse JSON list, return
      the id field of the entry whose title/dir matches worktree.
    else None.
- extract_usage(route, attempt_dir, log_text) -> types.Usage:
    usage_source 'output-format-json': the LAST '{'-starting line of
      log_text that json-parses and has 'usage' or 'total_cost_usd' ->
      Usage(basis=ACTUAL, tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens, cached_in=
            usage.cache_read_input_tokens, cost=total_cost_usd,
            currency='USD').
    'exec-output-footer' (codex): regex r'tokens used[:\\s]+([\\d,]+)'
      (case-insens.) -> Usage(basis=ESTIMATED, tokens_out=<n>) (codex does
      not split in/out; documented).
    'session-json' / 'run-log-deepseek-usage': regex
      r'"(prompt|input)_tokens"\\s*:\\s*(\\d+)' and
      r'"(completion|output)_tokens"\\s*:\\s*(\\d+)' over log_text ->
      Usage(basis=ACTUAL tokens, cost=None).
    anything else/no match -> Usage(basis=UNKNOWN).
  Never raises on malformed logs; degrade to UNKNOWN.
- classify_log_tail(text) -> 'blocked' | 'limit' | None:
  'BLOCKED:' at a line start (last 200 lines) -> 'blocked';
  v2 §5.2 limit phrases (case-insens.: 'session limit', 'usage limit',
  'rate limit exceeded', 'quota', 'plan limit') -> 'limit'; else None.
  'blocked' beats 'limit' when both appear.
- find_controller_container(container_prefix=None, env=None) -> str | None:
  P27 2026-07-16: resolves the running nyxloomd daemon container so
  exec-nyxloom.py can dispatch into it instead of silently falling back to
  host mode. env['NYXLOOM_CONTAINER'] (default os.environ) wins outright
  when it names a running container. Otherwise, if container_prefix is
  given (the ciu.toml `container_prefix` value, e.g. "nyxloom-prod"),
  matches the exact name `<container_prefix>-nyxloomd` -- the literal
  container_name the compose template renders. Without a prefix, matches
  any running name ending in "-nyxloomd" (the fixed service-name suffix
  every ciu.toml container_prefix produces), so a caller that has not
  resolved a prefix still finds the daemon without hardcoding one. `docker`
  missing, `docker ps` failing, or no match all degrade to None (never
  raises) -- host fallback must still work.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import containment, results
from .config import RouteDef
from .log import get_logger
from .types import Basis, Role, Usage

log = get_logger("adapters")  # P05a (docs/plan-logging.md §5)


class AdapterError(Exception):
    pass


def find_controller_container(container_prefix: str | None = None,
                              env: dict | None = None) -> str | None:
    """Resolve the running nyxloomd daemon container name (see module contract)."""
    env = env if env is not None else os.environ
    override = env.get("NYXLOOM_CONTAINER")
    if not shutil.which("docker"):
        return None
    try:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        ).stdout.split()
    except (subprocess.SubprocessError, OSError):
        return None
    if override and override in names:
        return override
    if container_prefix:
        target = f"{container_prefix}-nyxloomd"
        return target if target in names else None
    for name in names:
        if name.endswith("-nyxloomd"):
            return name
    return None


def render_argv(template: list[str], mapping: dict[str, str]) -> list[str]:
    """Substitute placeholders in list elements; missing keys raise AdapterError."""
    result = []
    for elem in template:
        try:
            result.append(elem.format_map(mapping))
        except KeyError as e:
            raise AdapterError(f"missing placeholder {e}")
    return result


# D-BATCHC (2026-07-26, plan-factory-hardening.md; Batch C -- modulate
# review depth by complexity band + declared gate rigor). Two ALREADY-
# EXISTING inputs drive the band (Frontmatter.tier / types.py:434, a
# scope-size fallback when tier is absent or unparseable / types.py
# :403-405) -- no new schema field, no new frontmatter key.
_TIER_BAND = {"implement-1": 1, "implement-2": 2, "implement-3": 3}
_LOW_BAND = 1
_HIGH_BAND = 3
# >N touched paths in scope.touch is treated as band 3 when tier can't be
# read at all -- a handoff spanning that many files is, by the same
# "mechanical/cheap vs. hard" banding routes.host.toml already uses for
# implement-1 vs. implement-3, no longer a small/cheap change.
_HIGH_BAND_SCOPE_TOUCH_THRESHOLD = 5
# A gate whose `.asserts` (config.GateDef.asserts) is missing EITHER of
# these is "shallow" (catches less); one declaring BOTH is "rigorous".
_RIGOROUS_ASSERTS = {"changed-line-coverage", "mutation"}


def compute_review_depth_directive(tier: str | None, scope_touch: list[str] | None,
                                   gate_asserts: list[str] | None) -> str:
    """Pure signal -> reviewer-prompt directive (no I/O, no route/tier choice).

    Mirrors D1's `review_focus` shape: this function computes the SIGNAL only;
    `build_dispatch` below does the bounded argv append -- same "pure helper,
    then argv-bounded append" split.

    Band comes from `tier` ('implement-1'..'implement-3' per _TIER_BAND); an
    absent or unrecognized tier falls back to a scope-size proxy on
    `scope_touch` (see _HIGH_BAND_SCOPE_TOUCH_THRESHOLD). Gate rigor comes
    from `gate_asserts` (config.GateDef.asserts; None/[] is the shallowest
    case -- no gate, or a gate declaring nothing, both read as maximally
    shallow, never crash).

    Returns '' when the band is BELOW high (i.e. implement-1/implement-2, or
    the fallback's small-scope case) AND the gate is rigorous (declares both
    changed-line-coverage and mutation) -- the load-bearing neutral case:
    `build_dispatch` appends nothing and the REVIEW_INDEPENDENT prompt stays
    byte-identical to a pre-BATCHC dispatch. Otherwise returns a short
    directive naming the SPECIFIC reason(s) it fired (high band and/or the
    missing gate rigor) -- never a generic "look harder" with no reason,
    since the reviewer's own judgment about what to double-check depends on
    WHY it's being asked to.
    """
    band = _TIER_BAND.get(tier or "")
    if band is None:
        band = (_HIGH_BAND
                if len(scope_touch or []) > _HIGH_BAND_SCOPE_TOUCH_THRESHOLD
                else _LOW_BAND)

    asserts_set = set(gate_asserts or [])
    missing_rigor = _RIGOROUS_ASSERTS - asserts_set
    shallow = bool(missing_rigor)

    if band < _HIGH_BAND and not shallow:
        return ""

    reasons = []
    if band >= _HIGH_BAND:
        reasons.append(
            "This is a high-complexity change -- verify architectural "
            "correctness and edge cases, not just the handoff's stated "
            "oracles."
        )
    if shallow:
        if asserts_set:
            gate_desc = f"The automated gate asserts only {', '.join(sorted(asserts_set))}"
        else:
            gate_desc = "The automated gate declares no assertions"
        reasons.append(
            f"{gate_desc} -- no coverage or mutation floor, so verify "
            "coverage and edge-cases yourself."
        )
    return " ".join(reasons)


_REJECT_CLASS_INSTRUCTION = (
    "If REJECTED, also add `REJECT_CLASS: "
    "<fixable|architectural|incapable|product|transient>` "
    "(fixable=local defect; architectural=scope/design wrong, re-carve; "
    "incapable=scope fine but the model wasn't capable -- bump tier; "
    "product=human decision; transient=flaky/infra failure). Omit on APPROVED."
)


def build_dispatch(route: RouteDef, *, handoff_path: str, worktree: str,
                   branch: str, task_id: str, gate_hint: str,
                   receipt_path: str, role: Role = Role.IMPLEMENTER,
                   carve_authority: str | None = None,
                   attempt_id: str | None = None,
                   prior_verdict: str | None = None,
                   approved_amendments: list[str] | None = None,
                   review_focus: list[str] | None = None,
                   review_depth: str = "",
                   escalation_note: str = "",
                   repair_allowed: bool = False) -> tuple[list[str], str]:
    """Returns (argv, prompt). See module contract for per-CLI shapes.

    P44 2026-07-19: `role` selects the PROMPT TEXT only (the per-CLI argv
    shapes below are unchanged and role-agnostic). Defaults to
    Role.IMPLEMENTER so every call site written before this package (there
    are several outside daemon.py -- intake_chat.py, onboarding_scan.py,
    decision_chat.py, onboarding_questionnaire.py -- none of which pass
    `role`) keeps its exact byte-for-byte prompt/argv. `carve_authority` is
    only consulted when role is Role.CARVER (see daemon.py's module
    docstring above `_CARVE_AUTHORITIES` for what 'branch'/'main'/'files'
    each mean for who commits what).

    B4b 2026-07-20 (D-060 triage; critique "re-dispatch packets embed the
    review verdict" + "same-model context-free retries cease to exist"):
    `prior_verdict`, when given, is the prior review's rejection prose. It is
    embedded into an IMPLEMENTER re-dispatch prompt so a re-queued fix is
    TARGETED at what the reviewer flagged, never a bare context-free retry of
    the same handoff. Defaults None (a first dispatch, or any non-implementer
    role) -> the prompt is byte-identical to the pre-B4b text. Only the
    IMPLEMENTER branch consults it.

    B21 2026-07-23 (D-R16 §3, scope-amendment escalation; D-B21-1 overlay --
    the handoff's scope.touch on disk is never rewritten, this is the ONLY
    widening mechanism): every IMPLEMENTER dispatch now also carries a
    standing instruction for the escape hatch -- if the agent genuinely needs
    a file outside its scope.touch allowlist, emit a `SCOPE_AMENDMENT_
    REQUEST: {...}` marker and stop, instead of faking a workaround or
    hard-BLOCKing (the P26/P31 dead end this package fixes). `approved_
    amendments`, when given, is the list of file paths a PRIOR request for
    THIS task already had approved (the daemon counts these from
    SCOPE_AMENDMENT_APPROVED events) -- an IMPLEMENTER re-dispatch embeds them
    as "you MAY also edit" so the widened allowlist actually reaches the
    agent's next attempt. A REVIEW_INDEPENDENT dispatch instead tells the
    reviewer which files were approved, so it does not reject the (now
    legitimate) out-of-scope edit as a scope violation. Defaults None (no
    amendments yet) -> byte-identical to the pre-B21 prompt for both roles.

    D1 factory-hardening (2026-07-25, plan-factory-hardening.md §D part 1):
    `review_focus`, when given, is the carve-authored "adversarially check
    these" hint list from the handoff's frontmatter (types.Frontmatter.
    review_focus) -- the package-specific reviewer targeting that caught real
    bugs this session, now mechanical instead of ad-hoc controller judgment.
    Only the REVIEW_INDEPENDENT branch consults it, and only when non-empty,
    appended LAST and bounded to the remaining argv budget (see below) --
    Defaults None -> byte-identical to the pre-D1 prompt.

    D-BATCHC (2026-07-26, plan-factory-hardening.md §D part 2): `review_depth`,
    when non-empty, is the caller-computed complexity-band/gate-rigor
    directive from `compute_review_depth_directive` (see above) -- the SAME
    "pure helper computed at the call site, then argv-bounded append" shape
    review_focus uses, not a new route/tier (only ONE review tier exists
    today). Only the REVIEW_INDEPENDENT branch consults it, appended LAST
    (after review_focus) and bounded to the remaining argv budget. Defaults
    "" (the empty-string neutral case: low complexity band + a rigorous
    declared gate, or simply no signal available) -> byte-identical to the
    pre-BATCHC prompt.

    D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): `escalation_note`,
    when non-empty, is a terse NON-SPECIFIC context note for the review that
    follows a re-implementation carved via the `incapable` REJECT_CLASS path
    (a prior reviewer judged the ORIGIN task's assigned TIER incapable, not
    its scope wrong -- see the `_carve_packet_body_lines` rescope branch this
    pairs with). The caller resolves the provenance marker and computes the
    note's TEXT; this function makes no route/tier choice -- the SAME "pure
    signal computed at the call site, then argv-bounded append" shape
    review_depth uses. Only the REVIEW_INDEPENDENT branch consults it,
    appended LAST (after review_depth) and bounded to the remaining argv
    budget. Never embeds the prior reviewer's specific findings -- that would
    anchor the new review on stale specifics about code that no longer
    exists; it only names WHY a stronger tier ran. Defaults "" (every
    non-escalated dispatch, which is nearly all of them) -> byte-identical to
    the pre-D-R3 prompt.

    DR8 (2026-07-27, routing-model-redesign.md D-R8, refined): `repair_allowed`,
    when true, grants the independent reviewer a BOUNDED permission to fix a
    small test/fixture-side gap it finds and commit that on the task's own
    branch, instead of forcing a full carve/implement/review round-trip -- the
    caller passes `cfg.policy.reviewer_repair` (this function makes no policy
    decision, same "pure signal passed in" shape as escalation_note/
    review_depth above). Only the REVIEW_INDEPENDENT branch consults it.
    Defaults False -> this block is a complete no-op, so the prompt is
    byte-identical to pre-DR8 for EVERY role -- the bound itself is enforced
    mechanically AFTER the fact (daemon.py), never merely by this prompt text.
    """
    # Construct the prompt (short, names handoff, worktree, branch, gate, receipt)
    if role is Role.CARVER:
        if carve_authority == "files":
            # 'files' authority: the carver writes new handoff files WITHOUT
            # committing (no git) -- frontmatter.discover_handoffs globs disk
            # files regardless of git status, so there is nothing to commit
            # and telling it to commit anything would be actively wrong.
            prompt = (
                f"Handoff: {handoff_path}\n"
                f"Worktree: {worktree}\n"
                f"Gate: {gate_hint}\n"
                f"Receipt: {receipt_path}\n"
                "Carve authority: files. Write your new handoff file(s) to "
                "disk without running git at all (no staging, no "
                "committing) -- they will be picked up on the next "
                "reconcile pass regardless of git status."
            )
        else:
            # 'branch'/'main' (or unset -> treat as the safe default): the
            # carver DOES commit its new handoff file(s) -- O1 permits
            # keeping a commit instruction here.
            prompt = (
                f"Handoff: {handoff_path}\n"
                f"Worktree: {worktree}\n"
                f"Branch: {branch}\n"
                f"Gate: {gate_hint}\n"
                f"Receipt: {receipt_path}\n"
                "You MUST `git add` and `git commit` your new handoff "
                "file(s) on this branch before finishing."
            )
    elif role is Role.REVIEW_INDEPENDENT:
        # P59 2026-07-20 (M6a, Fable-xhigh critique -- prompt/packet
        # consistency). The daemon derives the merge verdict EXCLUSIVELY from
        # the reviewer's COMMITTED <task>-REVIEW.md (_parse_review_verdict via
        # `git show`); the receipt carries no verdict. The old prompt told the
        # reviewer "do not commit anything -- write your verdict to the
        # receipt", which flatly CONTRADICTED the packet's REQUIRED OUTPUT
        # CONTRACT ("write + commit <task>-REVIEW.md with a VERDICT: line") --
        # a reviewer obeying the prompt left NO committed verdict, so the
        # daemon read "missing" and false-rejected approved work (or, under
        # guarded-automatic merge, could mis-decide): a nondeterministic merge
        # gate whose outcome depended on which instruction the model followed.
        # This prompt now AGREES with the packet: commit ONLY the verdict
        # report, onto the task's own feat/ branch, never main and never a
        # code change (the original "never commit to main" safety intent is
        # kept -- the bug was forbidding ALL commits, not just the wrong ones).
        #
        # P59b 2026-07-20 (A7, M6/I8 -- verdict-attempt binding). The verdict
        # parser reads EVERY *REVIEW*.md on feat/<task> that mentions the task,
        # with no binding to WHICH review attempt produced it. In a reject
        # loop, review attempt #2 could therefore consume attempt #1's stale
        # `VERDICT: REJECTED` still sitting on the branch (I8), or a file that
        # merely name-drops the task (M6). The fix binds the verdict to THIS
        # attempt: the reviewer must stamp its attempt id on the VERDICT line,
        # and on a re-review the daemon counts ONLY verdicts carrying the
        # current attempt id. `attempt_id` is threaded from the launch site;
        # if absent (no caller currently omits it for a real review) the
        # instruction degrades to the old unbound form.
        _bind = f" (attempt {attempt_id})" if attempt_id else ""
        _bind_note = (
            f" The `(attempt {attempt_id})` suffix is REQUIRED and must be "
            "copied EXACTLY -- the daemon binds your verdict to this specific "
            "review by that id and ignores any verdict missing it or carrying "
            "a different id (a stale verdict from an earlier review of this "
            "same task)." if attempt_id else ""
        )
        prompt = (
            f"Handoff: {handoff_path}\n"
            f"Worktree: {worktree}\n"
            f"Gate: {gate_hint}\n"
            f"Receipt: {receipt_path}\n"
            "You are REVIEWING this packet, not authoring code changes. Follow "
            "the packet's REQUIRED OUTPUT CONTRACT: write your verdict as a "
            f"`VERDICT: APPROVED{_bind}` or `VERDICT: REJECTED{_bind}` line into "
            "the <task>-REVIEW.md the packet names, then `git add` and `git "
            "commit` ONLY that review file onto the task's own feat/ branch -- "
            "never main, and never a code change. The daemon reads your verdict "
            f"from that committed file, NOT from the receipt.{_bind_note}"
            # B4b 2026-07-20 (D-060 triage Tier-2; critique CRITIQUE.md:207; D-066).
            # You already hold the diff to reject, so you are the cheapest correct
            # classifier of WHY -- the daemon routes on it, no 2nd model call. Kept
            # TERSE: the reviewer prompt is close to argv_max (a verbose version
            # overflowed it, stranding the review dispatch -- caught by the gate).
            # D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): `incapable`
            # joins the vocabulary, distinct from `architectural` -- architectural
            # means the scope/design was wrong (re-scope, tier unspecified);
            # incapable means the scope was FINE but the model showed a
            # structural/repeated capability gap (not a one-off local defect) ->
            # re-carve at a HIGHER tier, scope held constant. Kept terse: this
            # prompt is close to argv_max with real paths (see
            # test_review_independent_prompt_stays_under_argv_max_with_real_paths).
            f"\n{_REJECT_CLASS_INSTRUCTION}"
        )
        # B21 2026-07-23 (D-R16 §3): the scope-amendment note (if any) is
        # appended LATER, after argv_max is known -- this prompt is already
        # close to argv_max with real paths (see
        # test_review_independent_prompt_stays_under_argv_max_with_real_paths),
        # so it MUST be bounded the same way prior_verdict's embed below is,
        # not appended unconditionally here. The DRY instruction (below,
        # after argv_max is known) follows the same rule -- an earlier
        # version appended it unconditionally into this literal and broke
        # a real route with argv_max=1000 (1023 > 1000), the exact overflow
        # class this comment already warns about.
    else:
        # IMPLEMENTER (default). The pre-P44 core text is preserved verbatim;
        # B21 2026-07-23 appends the standing scope-amendment escape-hatch
        # instruction below -- so this literal is no longer byte-identical to
        # pre-P44 (the escape hatch ships on EVERY implementer dispatch).
        prompt = (
            f"Handoff: {handoff_path}\n"
            f"Worktree: {worktree}\n"
            f"Branch: {branch}\n"
            f"Gate: {gate_hint}\n"
            f"Receipt: {receipt_path}\n"
            # 2026-07-15 (live P64 lesson): the first real dispatch produced a
            # full implementation but never committed it, and the review packet
            # diffs main...HEAD — uncommitted work is invisible to review.
            # 2026-07-16 (P21 live P93 lesson): "uncommitted work is discarded"
            # was FALSE -- the review packet now also captures uncommitted
            # worktree state (P21), so keep the commit pressure without
            # asserting a falsehood.
            "You MUST `git add` and `git commit` ALL your work on the branch "
            "before finishing. Uncommitted work will be surfaced to review but "
            "risks loss on worktree teardown — committing is required for a "
            "clean review."
            # B21 2026-07-23 (D-R16 §3): the scope-amendment escape hatch --
            # replaces the old dead end (fake a workaround, or hard-BLOCK)
            # that stranded P26/P31 when the correct fix needed a file outside
            # scope.touch. Standing instruction on EVERY implementer dispatch.
            "\nIf you genuinely need to edit a file outside your `scope.touch` "
            "allowlist, do NOT fake a workaround and do NOT hard-BLOCK. Emit "
            "exactly one line `SCOPE_AMENDMENT_REQUEST: {\"file\": \"<path>\", "
            "\"reason\": \"<why>\"}` and stop; your allowlist will be widened "
            "and you will be re-dispatched."
        )

    # Append incremental-write hint if present
    if "incremental-write" in route.prompt_hints:
        prompt += "\nFor large writes, batch in ~80-line chunks."

    # Free-endpoint confidentiality NOTICE: a free OpenRouter model is served
    # by providers that may log/train on prompts (that is the price of
    # "free"), so it must never receive secrets. Free routes carry the
    # "free-endpoint" hint in routes.host.toml; this injects the
    # operator-mandated no-secrets notice.
    #
    # CR-13a 2026-08-03: this is ADDRESSED TO THE MODEL and is not the guard;
    # it was, before containment existed, and calling a sentence in a prompt a
    # confidentiality guard is exactly the kind of untestable trust-boundary
    # claim the standing rule forbids. What actually stops a free route from
    # holding a secret is containment.child_env (it receives only what its
    # route declares) and the per-use container around it. Keep the notice --
    # an agent that knows not to paste credentials into a prompt is still
    # better than one that does not -- but do not read it as enforcement.
    if "free-endpoint" in route.prompt_hints:
        prompt += ("\nFor the free endpoint, never upload any confidential "
                   "information, personal data, credentials or secrets.")

    argv_max = route.argv_max or 1500

    # Doc-ownership 2026-07-23: canonical doctrine ships WITH the product under
    # `reference/`; a project's additions/overrides live in the same-named
    # `nyxloom-trove/` sibling. This emits the read-first POINTER, never the
    # content (the docs are mounted into the agent's environment, so inlining
    # them would burn argv on every dispatch). Appended ONLY if it fits: the
    # frontier-review prompt already runs close to argv_max with real paths
    # (test_review_independent_prompt_stays_under_argv_max_with_real_paths), and a
    # doc pointer must never be the thing that strands a dispatch.
    # Budgeted against argv_max MINUS the 200-char headroom the codebase already
    # reserves for longer real-world paths: a convenience pointer must not eat the
    # margin that keeps argv-tight roles dispatchable. In practice this lands on
    # IMPLEMENTER/CARVER (room to spare) and is skipped for REVIEW_INDEPENDENT (already
    # near the cap) -- acceptable, because the docs are mounted in the agent's
    # environment regardless; the pointer only saves it a lookup.
    manifest = ("\nDoctrine: `reference/AUTHORING.md` + `reference/DOCTRINE.md`; "
                "then any same-named `nyxloom-trove/` sibling for project overrides.")
    if len(prompt) + len(manifest) <= argv_max - 200:
        prompt += manifest

    # B4b 2026-07-20 (D-060 triage; critique "re-dispatch packets embed the review
    # verdict"): on a re-queued IMPLEMENTER fix after a REJECTED review, append the
    # reviewer's rejection prose so this pass targets exactly what was flagged --
    # the critique's ban on "bare same-model context-free retries". Appended LAST
    # and bounded to the REMAINING argv budget: a real review report can be multi-KB
    # and the base prompt + hints already vary with path length, so an unbounded
    # embed would overflow argv_max and strand the re-dispatch (the same failure the
    # terse REJECT_CLASS rewrite fixed on the reviewer side). Truncate to fit, or
    # skip entirely if too little room remains -- dispatching without the findings
    # beats not dispatching at all. First dispatches pass prior_verdict=None.
    if role is Role.IMPLEMENTER and prior_verdict:
        header = ("\n\nThis task was REJECTED by review on a prior attempt. Do NOT "
                  "re-submit the same work -- address the reviewer's findings below "
                  "before finishing:\n")
        marker = "\n[... review findings truncated to fit ...]"
        room = argv_max - len(prompt) - len(header)
        if room >= 200:
            body = prior_verdict.strip()
            if len(body) > room:
                body = body[: room - len(marker)].rstrip() + marker
            prompt += header + body

    # B21 2026-07-23 (D-R16 §3): the actual allowlist-widening the escape
    # hatch promises -- a re-dispatch for a task with prior approved
    # amendments tells the agent WHICH files it may now also touch. Appended
    # last (after prior_verdict) and BOUNDED to the remaining argv budget --
    # skip entirely (never truncate mid-list) if too little room remains,
    # same "degrade, never strand the dispatch" philosophy as prior_verdict's
    # embed above. Discovered live (not just theoretical): an EARLIER
    # unconditional version of this same idea on the REVIEW_INDEPENDENT side
    # (below) overflowed argv_max for realistic packet paths and permanently
    # stranded a review attempt at CREATED (build_dispatch raised
    # AdapterError, and the daemon never retries a review whose Attempt
    # record already exists) -- see LOG.md for the full trace. Amendment
    # file lists are normally short, but "normally" is not a bound.
    if role is Role.IMPLEMENTER and approved_amendments:
        files_str = ", ".join(approved_amendments)
        note = (
            f"\n\nAmendment-approved — you MAY also edit: {files_str} (a "
            "prior SCOPE_AMENDMENT_REQUEST for this task was approved; these "
            "files are now in-scope in addition to your original scope.touch "
            "allowlist)."
        )
        if len(prompt) + len(note) <= argv_max:
            prompt += note
        # else: skip -- dispatching without the widened-allowlist note beats
        # not dispatching at all (the agent still has its base scope.touch;
        # the SCOPE_AMENDMENT_REQUEST escape hatch remains available for a
        # follow-up request if it needs the file again).

    # B21 2026-07-23 (D-R16 §3): the reviewer-facing counterpart -- tell the
    # reviewer about any mid-flight scope.touch widening approved for this
    # wave's task(s), so the (now-legitimate) out-of-scope file(s) are not
    # mistaken for a scope violation and rejected on that basis alone.
    # Bounded for the SAME reason as the IMPLEMENTER note above (this is the
    # branch where the overflow was actually observed): the REVIEW_INDEPENDENT
    # prompt is already close to argv_max with real packet paths.
    if role is Role.REVIEW_INDEPENDENT and approved_amendments:
        files_str = ", ".join(approved_amendments)
        note = (
            f"\nScope-amendment note: {files_str} were approved mid-flight "
            "beyond this task's original scope.touch allowlist (a prior "
            "SCOPE_AMENDMENT_REQUEST was granted) -- treat them as "
            "legitimately in-scope; do not reject solely for touching them."
        )
        if len(prompt) + len(note) <= argv_max:
            prompt += note
        # else: skip -- the review still dispatches; worst case the reviewer
        # flags the amended file as out-of-scope and rejects, a recoverable
        # outcome (reject-loop re-queue) vs. a permanently stranded review
        # attempt (AdapterError here would abort a dispatch whose Attempt
        # record was already created and is never retried -- see LOG.md).

    # D1 factory-hardening (2026-07-25, plan-factory-hardening.md §D part 1):
    # the carve-authored review_focus hints -- package-specific "adversarially
    # check these" pointers, moving reviewer targeting from ad-hoc controller
    # judgment into the carve. Appended LAST and BOUNDED to the remaining
    # argv budget, the SAME "measure remaining room, truncate to fit, never
    # strand the dispatch" pattern as prior_verdict's embed above (this is
    # NOT the approved_amendments "skip whole block" pattern, because
    # review_focus is prose content like prior_verdict, not a short file
    # list): a real reviewer prompt is already close to argv_max with real
    # packet paths (test_review_independent_prompt_stays_under_argv_max_
    # with_real_paths), so an unbounded embed would overflow it and
    # permanently strand the review dispatch (the same failure class B21's
    # LOG.md documents). Absent/empty review_focus (every pre-D1 handoff)
    # -> this block is a complete no-op, so the prompt is byte-identical to
    # today.
    if role is Role.REVIEW_INDEPENDENT and review_focus:
        header = "\nCarve-authored focus -- adversarially check these:\n"
        marker = "\n[... review_focus truncated to fit ...]"
        room = argv_max - len(prompt) - len(header)
        if room >= 40:
            body = "\n".join(f"- {item}" for item in review_focus)
            if len(body) > room:
                body = body[: room - len(marker)].rstrip() + marker
            prompt += header + body
        # else: skip entirely -- dispatching without the focus hints beats
        # not dispatching at all (same rationale as prior_verdict/scope-
        # amendment above; the reviewer still runs its base adversarial
        # checks, just without the carve's extra pointers).

    # D-BATCHC (2026-07-26, plan-factory-hardening.md §D part 2): the
    # complexity-band/gate-rigor review-depth directive from
    # compute_review_depth_directive (caller-computed, passed in as a plain
    # string -- this function makes NO route/tier choice). Appended LAST
    # (after review_focus, so both coexist when both fire) and BOUNDED to the
    # remaining argv budget, the SAME "measure remaining room, truncate to
    # fit, skip entirely if too tight, never strand the dispatch" pattern as
    # review_focus immediately above. Absent/empty review_depth (the neutral
    # low-band + rigorous-gate case, and every pre-BATCHC call site) -> this
    # block is a complete no-op, so the prompt is byte-identical to today.
    if role is Role.REVIEW_INDEPENDENT and review_depth:
        header = "\nReview depth: "
        marker = " [... truncated to fit ...]"
        room = argv_max - len(prompt) - len(header)
        if room >= 40:
            body = review_depth
            if len(body) > room:
                body = body[: room - len(marker)].rstrip() + marker
            prompt += header + body
        # else: skip entirely -- dispatching without the depth directive
        # beats not dispatching at all (same rationale as review_focus
        # above; the reviewer still runs its base adversarial checks, just
        # without the extra depth guidance).

    # D-R3 (2026-07-26, refined; routing-model-redesign.md D-R3): the
    # incapable-escalation meta-note -- seeds the review that follows a
    # re-implementation carved via the `incapable` REJECT_CLASS path with a
    # terse, NON-SPECIFIC reason a higher tier ran, so the reviewer forms its
    # OWN judgment of the fresh diff instead of anchoring on stale specifics
    # about code that no longer exists (the prior reviewer's actual findings
    # are deliberately NEVER embedded here -- see docstring). SAME
    # bounded-append idiom as review_focus/review_depth immediately above --
    # appended LAST, measure remaining room, skip entirely (never truncate
    # into something misleading) if too tight. Absent/empty escalation_note
    # (every non-escalated dispatch, which is nearly all of them) -> this
    # block is a complete no-op, so the prompt is byte-identical to today.
    if role is Role.REVIEW_INDEPENDENT and escalation_note:
        header = "\n"
        marker = " [... truncated to fit ...]"
        room = argv_max - len(prompt) - len(header)
        if room >= 40:
            body = escalation_note
            if len(body) > room:
                body = body[: room - len(marker)].rstrip() + marker
            prompt += header + body
        # else: skip entirely -- dispatching without the meta-note beats not
        # dispatching at all (same rationale as review_depth/review_focus
        # above; the reviewer still runs its base adversarial checks, just
        # without the context on why a stronger tier ran).

    # DR8 (2026-07-27, routing-model-redesign.md D-R8, refined): the bounded
    # repair permission -- an already-engaged independent reviewer MAY commit
    # a small fix to this task's OWN test/fixture files (never production
    # source) instead of forcing a full carve/implement/review round-trip.
    # Appended AFTER escalation_note (task-specific content, above) and
    # BEFORE the DRY note (generic standing instruction, below) -- the
    # established priority convention this function already follows:
    # task-specific content is appended before generic standing instructions,
    # so generic content is the first to be dropped when the budget is tight.
    # Uses the SAME argv_max - 200 margin the DRY note below reserves (that
    # margin protects a real past incident -- see
    # test_review_independent_prompt_stays_under_argv_max_with_real_paths)
    # and the SAME "skip silently, never truncate" idiom the DRY note uses --
    # a permission grant truncated mid-sentence could read as something it
    # isn't, so this is a short fixed string that either fits whole or is
    # omitted entirely (never the truncate-with-marker pattern prior_verdict/
    # review_focus/review_depth/escalation_note use for variable-length
    # prose). Defaults False (repair_allowed omitted) -> this block is a
    # complete no-op, so the prompt is byte-identical to pre-DR8 for every
    # role.
    if role is Role.REVIEW_INDEPENDENT and repair_allowed:
        note = (
            "\nBounded repair: you MAY fix missed branches, absent fixtures "
            "or missing assertions in this task's OWN test files, and commit "
            "that on its branch. NEVER edit production source -- an "
            "out-of-scope repair is auto-reverted and this review becomes a "
            "REJECTION. If you repaired, write `VERDICT: APPROVED (repaired)` "
            "and one line naming what you patched."
        )
        if len(prompt) + len(note) <= argv_max - 200:
            prompt += note
        # else: skip -- dispatching without the repair permission beats not
        # dispatching at all (same rationale as the DRY note's own -200
        # margin below); the reviewer still runs its base adversarial review
        # and rejects as normal, just without the inline-fix permission.

    # D-R2 refinement (2026-07-26, routing-model-redesign.md): standing DRY
    # instruction on every dispatch of these two roles. Appended LAST (lowest
    # priority -- a generic reminder yields budget to task-specific content
    # like review_focus/review_depth/prior_verdict/escalation_note above) and
    # BOUNDED to the remaining argv budget, the SAME "skip whole addition if
    # it doesn't fit, never truncate a short fixed string" pattern
    # approved_amendments' notes use above (not review_focus's
    # truncate-with-marker pattern -- DRY text is a short fixed string, not
    # variable-length prose). An earlier version appended this
    # unconditionally into the base prompt literal and broke a real route
    # with argv_max=1000 (measured overflow: 1023 > 1000) -- the exact
    # overflow class every other append in this function already guards
    # against.
    if role is Role.IMPLEMENTER:
        note = ("\nKeep your diff DRY: extract a shared helper instead of "
                 "copying similar logic within the files you touch.")
        if len(prompt) + len(note) <= argv_max:
            prompt += note
    elif role is Role.REVIEW_INDEPENDENT:
        # REVIEW_INDEPENDENT-specific: uses the SAME -200 margin the doctrine
        # manifest above already reserves, not a bare <=argv_max check. B4b's
        # own live incident (test_review_independent_prompt_stays_under_
        # argv_max_with_real_paths' docstring) is exactly this failure class:
        # a real long-path dispatch overflowed argv_max and permanently
        # stranded a review attempt. That test pins a 200-char headroom
        # specifically so a FUTURE prompt addition (this one) fails a cheap
        # unit test instead of eating the last of that margin in production.
        # DRY is generic and low-value enough to yield the margin outright
        # rather than risk being the addition that finally consumes it.
        note = "\nDRY: reject duplicated production logic; never fix it yourself."
        if len(prompt) + len(note) <= argv_max - 200:
            prompt += note

    # Check prompt length
    if len(prompt) > argv_max:
        raise AdapterError(
            f"rendered prompt exceeds argv_max ({len(prompt)} > {argv_max})"
        )

    # Mapping for placeholder substitution in dispatch_extra
    mapping = {
        "session": "",
        "worktree": worktree,
        "prompt": prompt,
        "task_id": task_id,
        "handoff": handoff_path,
        "model": route.model,
    }

    # Build CLI-specific argv
    if route.cli == "claude":
        # P14 2026-07-15: stream-json + --verbose replaces the buffered
        # `json` format so the log gets incremental JSONL writes (a real
        # heartbeat) instead of one giant write at process exit.
        argv = [route.cli, "-p", prompt, "--output-format", "stream-json",
                "--verbose", "--model", route.model]
        if route.effort:
            argv.extend(["--effort", route.effort])
        argv.extend(render_argv(route.dispatch_extra, mapping))
    elif route.cli == "codex":
        sandbox = route.sandbox or "workspace-write"
        argv = [route.cli, "exec", "--sandbox", sandbox,
                "--cd", worktree, prompt, "-m", route.model]
        if route.effort:
            # codex has no --effort flag; reasoning effort is a config override
            # (mirrors ~/.codex/config.toml `model_reasoning_effort`). Without
            # this the route's `effort` was silently dropped, so every codex
            # route ran at the config default regardless of its declared tier.
            argv.extend(["-c", f'model_reasoning_effort="{route.effort}"'])
    elif route.cli == "opencode":
        argv = [route.cli, "run", "--model", route.model, "--dir", worktree]
        if route.variant:
            argv.extend(["--variant", route.variant])
        argv.extend(render_argv(route.dispatch_extra, mapping))
        argv.append(prompt)
    elif route.cli == "reasonix":
        argv = [route.cli, "run", "-dir", worktree, prompt]
    elif route.cli == "fake":
        argv = [route.cli]
        argv.extend(render_argv(route.dispatch_extra, mapping))
        argv.append(prompt)
    else:
        raise AdapterError(f"unknown cli: {route.cli}")

    return (argv, prompt)


def build_resume(route: RouteDef, *, session: str | None, worktree: str,
                 prompt: str) -> list[str]:
    """Build resume command from template."""
    if not route.resume:
        raise AdapterError("empty resume template")

    # Check if session is required but missing
    template_str = "".join(route.resume)
    if "{session}" in template_str and session is None:
        raise AdapterError("session required but not provided")

    # Render the template
    mapping = {"session": session or "", "worktree": worktree, "prompt": prompt}
    return render_argv(route.resume, mapping)


def self_review_prompt(*, task_id: str, worktree: str, branch: str,
                       report_path: str, attempt_id: str) -> str:
    """B5 2026-07-20 (hardened per P40 + AUTHORING): the self_review leg's prompt.
    Fed to build_resume (a WARM resume of the implementer's OWN session, which
    already holds the diff's full context -- no cold-start tax, no packet). The
    review is MECHANICAL and oracle-anchored -- deliberately NOT "reflect on your
    work" / "review with fresh eyes", which AUTHORING flags as false confidence
    (models are poor judges of what they missed). It runs each oracle's
    observable on REAL data, checks the oracle's NEGATIVE (the edge case: a test
    that also passes on the negative is a HOLLOW test), confirms every Work step,
    fixes in-session, and records its verdict as a TYPED JUDGEMENT DOCUMENT
    (CR-03/DR-13) the daemon reads through a schema, NEVER from the receipt
    (P33) and no longer from prose (the `SELF_REVIEW:` line this prompt used
    to ask for was scanned with a regex, which is the authority this package
    removes)."""
    judgement = results.judgement_filename(task_id)
    return (
        f"Self-review your OWN just-committed work on {branch} in {worktree}, "
        "before it goes to independent frontier review. Do NOT merely 're-read' "
        "or 'reflect' for a good feeling -- run a MECHANICAL, oracle-anchored "
        "check in THIS warm session: (1) for EACH oracle in the handoff, run its "
        "observable in the gate on REAL data and confirm it passes; (2) confirm "
        "each oracle's NEGATIVE case -- the edge/failure path, not just the happy "
        "path -- a test that would ALSO pass on the negative is a HOLLOW test, a "
        "defect to fix; (3) confirm every numbered Work step was met. Fix any "
        f"finding and `git add`/`git commit` it on {branch}. Write your prose "
        f"notes to {report_path} for humans. Then write your VERDICT as JSON to "
        f"{judgement} in the worktree root -- this file, not the prose, is what "
        "the daemon reads:\n"
        f'{{"schema_version": {results.SCHEMA_VERSION}, "kind": "self_review", '
        f'"task_id": "{task_id}", "attempt_id": "{attempt_id}", '
        '"verdict": "approved"|"rejected", "summary": "<one line>"}\n'
        "Use \"approved\" only when all oracles pass on real data with no hollow "
        "tests and every Work step met; \"rejected\" for a wall you cannot resolve "
        "in-session. The attempt_id above is yours -- copy it exactly, it is what "
        "binds this verdict to THIS run. `git add`/`git commit` the prose report "
        f"on {branch} (never main, never a code change in that commit); the JSON "
        "does not need committing."
    )


def review_resume_prompt(*, packet_path: str, attempt_id: str,
                         gate_hint: str, spine_pointer: str | None = None) -> str:
    """B6/P74 (D-R10): the WARM-resume frontier-review prompt.

    Fed to build_resume when reviewer session-reuse resumes a PRIOR review
    session (the ~35-40k role-contract/orientation prefix is already in that
    session's context, so it replays from prompt cache -- the cache-hit win).
    The warm session does NOT yet hold THIS wave's packet, so the prompt names
    it and repeats ONLY the load-bearing output contract, terse (this goes into
    argv; build_dispatch's cold REVIEW_INDEPENDENT branch is close to argv_max, so
    the resume form stays lean and skips the full role recap the session already
    holds).

    A7 (verdict-attempt binding) is PRESERVED on the resumed session: the prompt
    carries the NEW attempt id and requires the reviewer to stamp `(attempt
    <id>)` on every VERDICT line, WORD-FOR-WORD the same binding the cold
    build_dispatch prompt uses (P59b) -- the daemon counts only verdicts carrying
    the current attempt id, so a warm session (which still contains the PRIOR
    wave's packet and its OLD attempt id) cannot misbind a stale verdict to a new
    task. This binding is exactly why session-reuse was blocked until A7; keeping
    the stamp identical is the safety contract of B6."""
    _bind = f" (attempt {attempt_id})"
    spine_line = (
        f"\nStanding spine digest (standing invariants/risks/reflections): read "
        f"{spine_pointer} in the repo -- it is referenced, not inlined."
        if spine_pointer else ""
    )
    return (
        "Resume your frontier-review session for the NEXT review wave. The new "
        f"packet is at {packet_path} (read it now -- this session does not yet "
        "hold it). Same role and contract as your prior review in this session: "
        "verify real git state (git state is truth, receipts lie), adversarially "
        "check each task's diff against its handoff oracles (hollow tests, "
        "overclaimed evidence, edge-case gaps), and re-run the declared gate "
        f"yourself ({gate_hint}). For EACH task in the packet, write and `git "
        "add`/`git commit` its <task>-REVIEW.md onto that task's OWN feat/ branch "
        "(never main, never a code change), containing exactly one line "
        f"`VERDICT: APPROVED{_bind}` or `VERDICT: REJECTED{_bind}`. The `(attempt "
        f"{attempt_id})` suffix is REQUIRED and must be copied EXACTLY -- the "
        "daemon binds your verdict to THIS review by that id and ignores any "
        "verdict missing it or carrying a different id (a stale verdict from an "
        "earlier review of the same task in this warm session). "
        f"{_REJECT_CLASS_INSTRUCTION} Finally, if ANY "
        "task is rejected, make your FINAL output line exactly `BLOCKED: rejected "
        f"-- <task ids and one-line reasons>`.{spine_line}"
    )


def probe(route: RouteDef) -> tuple[bool, str]:
    """Test route liveness via probe."""
    if route.probe is None:
        return (True, "no-probe")

    # Handle named builtins
    if route.probe == "one-token-ping" or route.probe == "session-limit-check":
        probe_argv = [route.cli, "--version"]
    else:
        probe_argv = route.probe

    # P05a (§5): a provider call -> DEBUG.
    log.debug("probe", route=route.route_id, cli=route.cli)
    try:
        # CR-13a review: the SAME allowlist the dispatched leg gets. This runs
        # the route's own binary, in the DAEMON's process, on every pass -- and
        # it is not reached through the wrapper, so containment.child_env is
        # not applied to it by the launch path. Without this it was the one
        # place a route-declared argv still saw the daemon's whole environment
        # after CR-13a deleted the denylist. It cannot be more restrictive than
        # the real dispatch, which already runs on exactly this environment.
        result = subprocess.run(probe_argv, capture_output=True, text=True,
                              timeout=60, env=containment.child_env(route))
        if result.returncode == 0:
            return (True, "ok")
        else:
            # P05a (§5): "a route probe failure/pause" is a named WARNING example.
            log.warning("probe-failed", route=route.route_id, detail=f"exit code {result.returncode}")
            return (False, f"exit code {result.returncode}")
    except subprocess.TimeoutExpired:
        log.warning("probe-failed", route=route.route_id, detail="timeout after 60s")
        return (False, "timeout after 60s")
    except FileNotFoundError:
        log.warning("probe-failed", route=route.route_id, detail=f"command not found: {probe_argv[0]}")
        return (False, f"command not found: {probe_argv[0]}")
    except Exception as e:
        log.warning("probe-failed", route=route.route_id, detail=str(e))
        return (False, str(e))


def _stream_json_session_id(log_path: Path) -> str | None:
    """Read the FIRST line of a stream-json attempt log and pull out its
    `session_id` field. Degrades to None on any I/O or parse problem --
    this is a best-effort early capture, never a hard requirement."""
    try:
        with log_path.open("r", encoding="utf-8") as f:
            first_line = f.readline()
    except OSError:
        return None
    first_line = first_line.strip()
    if not first_line:
        return None
    try:
        data = json.loads(first_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    session_id = data.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def capture_session(route: RouteDef, *, attempt_dir: Path, worktree: str,
                    launched_at: datetime, log_path: str | Path | None = None) -> str | None:
    """Capture session ID (see module contract)."""
    if route.cli == "claude":
        # P17 2026-07-15: stream-json's first line carries session_id --
        # read it directly instead of guessing via the newest-jsonl scan
        # (unreliable for claude routes, see module contract). `log_path`
        # names the CURRENT run's exact log file (the wrapper always knows
        # it); fall back to the conventional first-dispatch path only when
        # the caller omits it.
        lp = Path(log_path) if log_path is not None else Path(attempt_dir) / "attempt.log"
        return _stream_json_session_id(lp)

    if route.session_capture == "newest-jsonl":
        # Build the slug: replace '/' with '-', keep leading '-'
        slug = worktree.replace("/", "-")
        if not slug.startswith("-"):
            slug = "-" + slug

        projects_dir = Path.home() / ".claude" / "projects" / slug.lstrip("-")
        if not projects_dir.exists():
            return None

        # Find newest .jsonl modified after launched_at
        jsonl_files = sorted(
            projects_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        for jf in jsonl_files:
            mtime = datetime.fromtimestamp(jf.stat().st_mtime, tz=timezone.utc)
            if mtime > launched_at:
                return jf.stem

        return None

    elif route.session_discover:
        # Run session discovery command. P05a (§5): a provider call -> DEBUG.
        log.debug("session-discover", route=route.route_id, worktree=worktree)
        try:
            # CR-13a review: the SAME allowlist the dispatched leg gets. The
            # wrapper calls this five seconds after launching a CONTAINED leg,
            # and it runs the route's own `session_discover` argv on the HOST,
            # outside that container -- every generated free route declares one
            # (free_models._render_route_block). Containment cannot reach this
            # call, so the environment half is applied here directly rather
            # than left inherited, which is what it was before this line.
            result = subprocess.run(route.session_discover, capture_output=True,
                                  text=True, timeout=30,
                                  env=containment.child_env(route))
            if result.returncode != 0:
                return None

            sessions = json.loads(result.stdout)
            if not isinstance(sessions, list):
                return None

            for session in sessions:
                if session.get("dir") == worktree or session.get("title") == worktree:
                    return session.get("id")

            return None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception):
            return None

    return None


def extract_usage(route: RouteDef, attempt_dir: Path, log_text: str) -> Usage:
    """Extract usage from logs based on usage_source."""
    if route.usage_source == "output-format-json":
        # Find LAST '{'-starting line (trimmed) that parses as JSON
        lines = log_text.split("\n")
        for line in reversed(lines):
            stripped = line.strip()
            if stripped.startswith("{"):
                try:
                    data = json.loads(stripped)
                    if "usage" in data or "total_cost_usd" in data:
                        usage_obj = data.get("usage", {})
                        return Usage(
                            basis=Basis.ACTUAL,
                            tokens_in=usage_obj.get("input_tokens"),
                            tokens_out=usage_obj.get("output_tokens"),
                            cached_in=usage_obj.get("cache_read_input_tokens"),
                            cost=data.get("total_cost_usd"),
                            currency="USD"
                        )
                except (json.JSONDecodeError, ValueError):
                    continue
        # Fallback (2026-07-15): newer claude CLI -p --output-format json
        # emits a result ARRAY whose summary carries modelUsage{...costUSD}.
        # Regex the tail rather than parsing the (possibly huge) array.
        tail = log_text[-8192:]
        m_cost = re.findall(r'"costUSD":\s*([0-9.]+)', tail)
        m_in = re.findall(r'"inputTokens":\s*(\d+)', tail)
        m_out = re.findall(r'"outputTokens":\s*(\d+)', tail)
        m_cache = re.findall(r'"cacheReadInputTokens":\s*(\d+)', tail)
        if m_cost:
            return Usage(
                basis=Basis.ACTUAL,
                tokens_in=int(m_in[-1]) if m_in else None,
                tokens_out=int(m_out[-1]) if m_out else None,
                cached_in=int(m_cache[-1]) if m_cache else None,
                cost=round(sum(float(c) for c in m_cost), 6),
                currency="USD",
            )
        return Usage(basis=Basis.UNKNOWN)

    elif route.usage_source == "exec-output-footer":
        # Regex: tokens used
        match = re.search(r"tokens\s+used[:\s]+([0-9,]+)", log_text,
                         re.IGNORECASE)
        if match:
            tokens = int(match.group(1).replace(",", ""))
            return Usage(basis=Basis.ESTIMATED, tokens_out=tokens)
        return Usage(basis=Basis.UNKNOWN)

    elif route.usage_source == "session-json" or route.usage_source == "run-log-deepseek-usage":
        # Regex for input and output tokens
        tokens_in_match = re.search(
            r'"(?:prompt|input)_tokens"\s*:\s*(\d+)',
            log_text
        )
        tokens_out_match = re.search(
            r'"(?:completion|output)_tokens"\s*:\s*(\d+)',
            log_text
        )

        if tokens_in_match and tokens_out_match:
            return Usage(
                basis=Basis.ACTUAL,
                tokens_in=int(tokens_in_match.group(1)),
                tokens_out=int(tokens_out_match.group(1))
            )
        return Usage(basis=Basis.UNKNOWN)

    return Usage(basis=Basis.UNKNOWN)


def classify_log_tail(text: str) -> str | None:
    """Classify the log tail for blocked/transient/limit indicators.

    2026-07-15 false-positive fix (topos-P91 "persistent capped history"):
    a real provider limit TERMINATES the process, so its phrase lands in the
    final lines — whereas a package about rate/quota/caps says "limit",
    "quota", "capped" throughout its own reasoning and tests. Matching
    limit phrases over the last 200 lines misread P91's domain vocabulary as
    a rate-limit hit and looped it back to re-dispatch (v2 §5.2: never infer
    a limit from free text — require a limit-SHAPED terminal signal).

    - BLOCKED: still recognized anywhere in the last 200 lines (the agent
      writes it as a deliberate final marker; a line-start match is
      unambiguous).
    - SCOPE_AMENDMENT_REQUEST: (B21 2026-07-23, D-R16 §3) recognized the same
      way as BLOCKED -- a line-start match anywhere in the last 200 lines.
      This is the mid-flight "I need file X outside scope.touch" escalation:
      distinct from BLOCKED (never a dead end -- wrapper.py/daemon.py route
      it to a bounded approve-and-requeue instead of a hard block). BLOCKED
      still takes precedence if BOTH somehow appear (checked first, below).
    - transient (B24 2026-07-23, D-R17): a PROVIDER-side throttle/outage
      signature (HTTP 502, google's ResourceExhausted, an upstream idle
      timeout, a worker request-limit message, or a distinct HTTP-429
      signature) in the last LIMIT_TAIL_LINES lines. Checked BEFORE the
      limit-phrase match below (a "...request limit" phrase would otherwise
      ALSO match limit_phrase's own bare "limit reached" alternative --
      transient must win so wrapper.py/daemon.py resume the SAME session
      rather than burning the task's attempt budget on a provider hiccup).
      Deliberately narrow so it does NOT reclassify the existing "<n> too
      many requests" limit phrasing below (unchanged) -- transient's own
      HTTP-429 signature requires an "http ... 429" or "error code: 429"
      shape, not the "too many requests" wording.
    - limit phrases only count in the last LIMIT_TAIL_LINES lines (never
      bare 'limit'/'quota'/'capped' -- that is domain vocabulary), and only
      in the terminal tail where a real provider limit that ended the
      process would land. (2026-07-23 correction: earlier revisions of this
      docstring additionally claimed limit phrases only count "when the
      line looks like a CLI/error surface (starts with a known error prefix
      or contains 'error'/'exceeded'/HTTP 429)" -- no such heuristic, and no
      `ERROR_PREFIXES` constant, has ever existed in the code below; the
      only actual gate is the fixed `limit_phrase` regex over `tail_lim`.)
      'blocked' still beats 'limit' (and now 'transient').
    """
    lines = text.split("\n")
    tail200 = lines[-200:] if len(lines) > 200 else lines
    tail_lim = lines[-LIMIT_TAIL_LINES:] if len(lines) > LIMIT_TAIL_LINES else lines

    if any(line.startswith("BLOCKED:") for line in tail200):
        return "blocked"

    if any(line.startswith("SCOPE_AMENDMENT_REQUEST:") for line in tail200):
        return "scope_amendment"

    if any(TRANSIENT_PHRASE.search(line) for line in tail_lim):
        return "transient"

    # Specific limit phrases only (never bare 'limit'/'quota'/'capped' — that
    # is domain vocabulary), and only in the terminal tail where a real
    # provider limit that ended the process would land.
    limit_phrase = re.compile(
        r"(?i)(session limit|usage limit|rate limit (exceeded|reached|hit)|"
        r"quota (exceeded|reached)|plan limit|429 too many requests|"
        r"too many requests|limit reached)")
    if any(limit_phrase.search(line) for line in tail_lim):
        return "limit"

    return None


LIMIT_TAIL_LINES = 25

# B24 2026-07-23 (D-R17, D-B24-3): PROVIDER-throttle/outage signatures --
# checked in classify_log_tail BEFORE limit_phrase (see its docstring).
# Deliberately does NOT include "too many requests" (the existing LIMIT
# phrasing for 429) -- the 429 alternatives here require an "http"/"error
# code" shape distinct from that wording, so the pre-existing "429 too many
# requests" -> "limit" classification is unchanged (no regression).
TRANSIENT_PHRASE = re.compile(
    r"(?i)(502 bad gateway|\bhttp\S*\s+502\b|\bresourceexhausted\b|"
    r"upstream idle timeout|worker local total request limit|"
    r"\bhttp\S*\s+429\b|error code:\s*429\b)")
