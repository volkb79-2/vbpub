"""handoffd: resident reconciler + read-only HTTP/SSE surface. PACKAGE P09.

Residency is an optimization, never an authority: every pass rebuilds its
snapshot FROM DISK, so kill -9 on the daemon loses nothing (deciding log
2026-07-15). Wrappers are detached and keep running across daemon restarts.

INTERFACE CONTRACT (frozen):

- Daemon(registry: dict[str, Path]) — one daemon supervises all registered
  projects (config.load_registry()).
- run():
    * write pidfile paths.daemon_dir()/handoffd.pid (refuse to start if an
      existing pidfile's pid is alive); append DAEMON_STARTED per project.
    * loop until SIGTERM/SIGINT: for each project run_pass(project), then
      sleep min(reconcile_interval_seconds over projects) — a plain
      time.sleep loop in the main thread is acceptable (no asyncio needed;
      the HTTP server runs in its own thread).
    * on shutdown: append DAEMON_STOPPED per project, remove pidfile. Never
      kill wrappers on shutdown.
- run_pass(project) — ONE reconcile pass, also the body of `tick --once`:
    1. Build ReconcileInput from disk:
       states=storage.list_states, frontmatters/lint via frontmatter+lint
       modules (lint_clean = not lint.has_blocking), decisions_open from
       decisions.parse_inbox, merged_branches from
       `git -C root branch --merged <default_branch>` plus branches whose
       task statefile is MERGED+, leases_free via leases.holder_info,
       provider_ok via adapters.probe MEMOIZED for probe_ttl_seconds (600)
       in daemon memory (a restart just re-probes), log_quiet_seconds /
       pid_alive / receipts by scanning attempt dirs of non-terminal
       attempts, stall_confirmed from _confirm_stall() (tier 2: pid alive
       AND log quiet AND /proc/<pid>/stat unchanged CPU over two passes —
       keep the two-pass cache in daemon memory), budget_remaining from
       policy.max_cost minus summed attempt usage costs (same currency
       only), merge_history/carve_outcomes/rejections from recent events
       (iter_events tail).
    2. actions = reconcile.plan_project(inp)
    3. execute(project, action) for each — see EXECUTION MAP below.
    4. render.render_all(...) if any event was appended this pass.
    5. Wrap the whole pass in try/except: append TICK_ERROR (bounded repr)
       and continue — one project's failure never stops the loop.
- EXECUTION MAP (all storage writes via append_and_apply, actor
  Actor(TICK, 'handoffd')):
    CreateTask -> TASK_CREATED (statefile CARVED, handoff_path set)
    Transition -> TASK_TRANSITIONED (payload from/to/notes)
    DispatchImplementer -> create worktree if missing (git worktree add -b
      feat/<task_id> <worktree_root>/feat/<task_id> <default_branch>; if
      branch exists, add without -b), build Attempt record (types.new_id
      ('att'), role IMPLEMENTER, state CREATED, route snapshot with
      routes_rev), ATTEMPT_CREATED; adapters.build_dispatch ->
      WrapperSpec -> wrapper.launch_detached; ATTEMPT_PREFLIGHTED (state
      PREFLIGHTING, pid=wrapper pid). Task ACTIVE via Transition.
    ResumeAttempt -> adapters.build_resume argv -> new WrapperSpec into the
      SAME attempt dir (suffix .resume-N) -> launch; ATTEMPT_RESUMED
      (state RUNNING).
    InterruptAttempt -> SIGTERM to attempt pgid (from child.pid; ESRCH is
      fine); the WRAPPER emits the interrupted event, not the daemon.
    MarkInterrupted -> ATTEMPT_INTERRUPTED (state INTERRUPTED, ended=now).
    StallCheck -> feed _confirm_stall cache only (no event).
    EmitAttemptExit -> idempotent healing (amended 2026-07-15): if the
      attempt is not yet EXITED (wrapper died before writing its event but
      receipt.json exists), emit ATTEMPT_EXITED from the receipt; in every
      case perform the task transition per reconcile contract item 4. The
      attempt scan feeding the planner includes EXITED attempts of
      still-ACTIVE tasks for exactly this purpose.
    ProviderPause -> PROVIDER_STATE_CHANGED {route_id, state:'limited'};
      daemon memory marks provider_ok[route_id]=False for
      provider_pause_seconds (3600) — and NEEDS_OPERATOR notification.
    OpenWave -> WAVE_OPENED (wave_id=new_id('wave'), task_ids).
    LaunchReview -> assemble packet dir under attempts dir of a synthetic
      review attempt: per task, `git -C root diff <default_branch>...HEAD`
      of its branch dumped to <packet>/<task_id>.diff + --stat + handoff/
      report paths list in packet.md; create Attempt (role FRONTIER_REVIEW,
      route = first route of tier 'frontier-review'), dispatch via wrapper
      like an implementer with the packet path in the prompt.
    SpecAttention -> SPEC_ATTENTION event.
    After every event append: notify.notify_event(cfg, states, ev).
- HTTP (loopback only, port = min over projects' policy.http_port):
  thread with http.server.ThreadingHTTPServer.
    GET /                    -> 302 /www/index.html
    GET /www/<path>          -> serve paths.www_dir() files (no traversal:
                                resolve() must stay under www_dir)
    GET /api/projects        -> registry summary JSON
    GET /api/tasks?project=  -> [statefile dicts]
    GET /api/task/<project>/<task_id> -> statefile dict
    GET /api/events?project=&since=   -> [event dicts] (cap 500)
    GET /api/log/<project>/<attempt_id>?tail=65536 -> text/plain, LAST n
        bytes of the attempt log passed through cfg.redact
    GET /api/stream?project= -> text/event-stream: poll events.jsonl every
        2s, emit new events as `data: <json>\n\n` (heartbeat comment line
        every 15s); connection ends when client disconnects.
  All responses read-only; no mutation endpoints exist.
- stop(): set the loop flag false and shut the HTTP server down (used by
  tests; signal handlers call it).
"""

from __future__ import annotations

import http.server
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.parse
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import adapters, config, decisions, frontmatter, leases, lint, notify, paths, reconcile, render, storage, wrapper
from .config import ProjectConfig
from .types import (
    Actor, ActorKind, Attempt, AttemptState, Blocker, BlockerType, Event,
    EventType, Receipt, ReceiptResult, Role, Route, TaskState, TaskStateFile,
    TERMINAL_ATTEMPT_STATES, new_id, utc_now,
)

# Tunables (module constants so tests can shrink them for determinism).
PROBE_TTL_SECONDS = 600
PROVIDER_PAUSE_SECONDS = 3600
SSE_POLL_SECONDS = 0.5
SSE_HEARTBEAT_SECONDS = 15.0
DEFAULT_HTTP_PORT = 8942
DEFAULT_RECONCILE_INTERVAL = 30.0


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Daemon:
    def __init__(self, registry: dict[str, Path]):
        self.registry = registry
        self.http_port: int = 0
        self._stop_event = threading.Event()
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        # Daemon memory: disposable, rebuilt on restart.
        self._probe_memo: dict[str, tuple[float, bool, str]] = {}
        self._stall_cache: dict[str, str | None] = {}
        self._provider_paused: dict[str, float] = {}
        self._decisions_seen: dict[str, dict[str, str]] = {}

    # -- lifecycle ------------------------------------------------------

    def run(self) -> None:
        pidfile = paths.daemon_dir() / "handoffd.pid"
        if pidfile.exists():
            try:
                existing = int(pidfile.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                existing = None
            if existing is not None and _pid_alive(existing):
                raise RuntimeError(f"handoffd already running (pid {existing})")
        paths.daemon_dir().mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
        self._install_signal_handlers()
        try:
            for project in self.registry:
                self._emit_lifecycle(project, EventType.DAEMON_STARTED)
            self._start_http()
            try:
                while not self._stop_event.is_set():
                    for project in list(self.registry):
                        self.run_pass(project)
                    self._stop_event.wait(self._min_interval())
            finally:
                self._stop_http()
                for project in self.registry:
                    self._emit_lifecycle(project, EventType.DAEMON_STOPPED)
        finally:
            try:
                pidfile.unlink()
            except FileNotFoundError:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        self._stop_http()

    def _install_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGTERM, lambda signum, frame: self.stop())
            signal.signal(signal.SIGINT, lambda signum, frame: self.stop())
        except ValueError:
            pass  # not the main thread (e.g. under test); stop() is still callable directly

    def _min_interval(self) -> float:
        intervals = []
        for project, root in self.registry.items():
            try:
                cfg = config.ProjectConfig.load(root)
                intervals.append(cfg.policy.reconcile_interval_seconds)
            except Exception:
                continue
        return float(min(intervals)) if intervals else DEFAULT_RECONCILE_INTERVAL

    def _emit_lifecycle(self, project: str, ev_type: EventType) -> None:
        try:
            cfg: ProjectConfig | None = config.ProjectConfig.load(self.registry[project])
        except Exception:
            cfg = None
        ev = storage.append_and_apply(
            project, {}, actor=Actor(ActorKind.TICK, "handoffd"), type=ev_type, payload={},
        )
        if cfg is not None:
            try:
                notify.notify_event(cfg, {}, ev)
            except Exception:
                pass

    # -- one reconcile pass -----------------------------------------------

    def run_pass(self, project: str) -> int:
        """One reconcile pass; returns number of actions executed."""
        try:
            root = self.registry[project]
            cfg = config.ProjectConfig.load(root)
            states = storage.list_states(project)
            appended: list[Event] = []

            appended.extend(self._reconcile_decisions(project, cfg, states))

            inp = self._build_input(project, cfg, states)
            actions = reconcile.plan_project(inp)
            for action in actions:
                appended.extend(self._execute(project, cfg, states, action))
            if appended:
                render.render_after_event(self.registry)
            return len(actions)
        except Exception as exc:
            detail = repr(exc)[:500]
            try:
                ev = storage.append_and_apply(
                    project, {}, actor=Actor(ActorKind.TICK, "handoffd"),
                    type=EventType.TICK_ERROR, payload={"error": detail},
                )
                try:
                    cfg2 = config.ProjectConfig.load(self.registry[project])
                    notify.notify_event(cfg2, {}, ev)
                except Exception:
                    pass
            except Exception:
                pass
            return 0

    def _reconcile_decisions(self, project: str, cfg: ProjectConfig,
                              states: dict[str, TaskStateFile]) -> list[Event]:
        seen = self._decisions_seen.setdefault(project, {})
        out: list[Event] = []
        try:
            events = decisions.reconcile_decisions(cfg, states, seen)
        except Exception:
            events = []
        for ev_type_str, decision_id in events:
            out.append(self._append_ev(project, cfg, states, EventType(ev_type_str), {},
                                        decision_id=decision_id))
        inbox_path = cfg.root / cfg.decisions_inbox
        if inbox_path.exists():
            try:
                parsed = decisions.parse_inbox(inbox_path.read_text(encoding="utf-8"))
                for d in parsed:
                    seen[d.id] = d.status
            except Exception:
                pass
        return out

    # -- input building ----------------------------------------------------

    def _build_input(self, project: str, cfg: ProjectConfig,
                      states: dict[str, TaskStateFile]) -> reconcile.ReconcileInput:
        routes = config.Routes.load()
        frontmatters: dict[str, tuple] = {}
        for path in frontmatter.discover_handoffs(cfg):
            try:
                fm, _body = frontmatter.parse_handoff(path)
            except Exception:
                continue
            try:
                relpath = str(path.resolve().relative_to(cfg.root.resolve()))
            except ValueError:
                relpath = str(path)
            frontmatters[fm.id] = (fm, relpath)

        try:
            findings = lint.lint_project(cfg)
        except Exception:
            findings = {}
        lint_clean: dict[str, bool] = {}
        for fm_id, (_fm, relpath) in frontmatters.items():
            f = findings.get(relpath, [])
            lint_clean[fm_id] = not lint.has_blocking(f)

        project_paused = paths.pause_flag(project).exists()
        try:
            decisions_open = decisions.open_ids(cfg)
        except Exception:
            decisions_open = set()
        merged_branches = self._merged_branches(cfg, states)
        leases_free = self._leases_free(cfg)
        provider_ok = self._provider_ok(routes)
        log_quiet_seconds, pid_alive, receipts = self._attempt_scan(project, states)
        stall_confirmed = self._confirm_stall(states, log_quiet_seconds, pid_alive, cfg)
        budget_remaining = self._budget_remaining(cfg, states)
        merge_history, carve_outcomes, review_rejections_by_area, blocked_underspecified_count = \
            self._history(project)
        ratchet_already_open = self._ratchet_already_open(project)

        return reconcile.ReconcileInput(
            now=utc_now(),
            cfg=cfg,
            routes=routes,
            states=states,
            frontmatters=frontmatters,
            lint_clean=lint_clean,
            project_paused=project_paused,
            decisions_open=decisions_open,
            merged_branches=merged_branches,
            leases_free=leases_free,
            provider_ok=provider_ok,
            log_quiet_seconds=log_quiet_seconds,
            pid_alive=pid_alive,
            receipts=receipts,
            stall_confirmed=stall_confirmed,
            budget_remaining=budget_remaining,
            merge_history=merge_history,
            ratchet_already_open=ratchet_already_open,
            carve_outcomes=carve_outcomes,
            review_rejections_by_area=review_rejections_by_area,
            blocked_underspecified_count=blocked_underspecified_count,
        )

    def _merged_branches(self, cfg: ProjectConfig, states: dict[str, TaskStateFile]) -> set[str]:
        out: set[str] = set()
        try:
            res = subprocess.run(
                ["git", "-C", str(cfg.root), "branch", "--merged", cfg.default_branch],
                capture_output=True, text=True, timeout=15,
            )
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    name = line.strip().lstrip("*").strip()
                    if not name:
                        continue
                    out.add(name)
                    if name.startswith("feat/"):
                        out.add(name[len("feat/"):])
        except Exception:
            pass
        for tsf in states.values():
            if tsf.state in (TaskState.MERGED, TaskState.VALIDATING, TaskState.COMPLETED):
                out.add(tsf.task_id)
                out.add(f"feat/{tsf.task_id}")
        return out

    def _leases_free(self, cfg: ProjectConfig) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for mdef in cfg.mutexes.values():
            lease_name = mdef.lease_name(cfg.project_id)
            try:
                info = leases.holder_info(lease_name, capacity=mdef.capacity)
                out[lease_name] = any(not slot["held"] for slot in info)
            except Exception:
                out[lease_name] = True
        return out

    def _provider_ok(self, routes: config.Routes) -> dict[str, bool]:
        now = time.monotonic()
        out: dict[str, bool] = {}
        for route_id, route in routes.routes.items():
            paused_until = self._provider_paused.get(route_id)
            if paused_until is not None and now < paused_until:
                out[route_id] = False
                continue
            memo = self._probe_memo.get(route_id)
            if memo is not None and (now - memo[0]) < PROBE_TTL_SECONDS:
                out[route_id] = memo[1]
                continue
            try:
                ok, detail = adapters.probe(route)
            except Exception as exc:
                ok, detail = False, repr(exc)[:200]
            self._probe_memo[route_id] = (now, ok, detail)
            out[route_id] = ok
        return out

    def _attempt_scan(self, project: str, states: dict[str, TaskStateFile]):
        log_quiet: dict[str, float | None] = {}
        pid_alive: dict[str, bool] = {}
        receipts: dict[str, dict | None] = {}
        now = time.time()
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state in TERMINAL_ATTEMPT_STATES:
                    # EXITED with the task still ACTIVE: the wrapper emitted
                    # its own exit event but the TASK transition hasn't run
                    # yet — the planner needs the receipt to schedule it.
                    if not (att.state == AttemptState.EXITED
                            and tsf.state == TaskState.ACTIVE):
                        continue
                attempt_dir = paths.attempt_dir(project, att.attempt_id)
                receipt_path = attempt_dir / "receipt.json"
                if receipt_path.exists():
                    try:
                        receipts[att.attempt_id] = json.loads(receipt_path.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        receipts[att.attempt_id] = None
                else:
                    receipts[att.attempt_id] = None
                pid_alive[att.attempt_id] = _pid_alive(att.pid)
                log_path = Path(att.log_path) if att.log_path else (attempt_dir / "attempt.log")
                if log_path.exists():
                    log_quiet[att.attempt_id] = max(0.0, now - log_path.stat().st_mtime)
                else:
                    log_quiet[att.attempt_id] = None
        return log_quiet, pid_alive, receipts

    def _confirm_stall(self, states: dict[str, TaskStateFile], log_quiet_seconds, pid_alive,
                        cfg: ProjectConfig) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for tsf in states.values():
            for att in tsf.attempts:
                if att.state in TERMINAL_ATTEMPT_STATES:
                    continue
                aid = att.attempt_id
                quiet = log_quiet_seconds.get(aid)
                alive = pid_alive.get(aid, False)
                if not alive or quiet is None or quiet <= cfg.policy.stall_log_quiet_seconds:
                    self._stall_cache.pop(aid, None)
                    out[aid] = False
                    continue
                cpu = self._read_proc_cpu(att.pid)
                prev = self._stall_cache.get(aid)
                out[aid] = prev is not None and cpu is not None and prev == cpu
                self._stall_cache[aid] = cpu
        return out

    @staticmethod
    def _read_proc_cpu(pid: int | None) -> str | None:
        if not pid:
            return None
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            parts = stat.split()
            return f"{parts[13]}:{parts[14]}"
        except Exception:
            return None

    def _budget_remaining(self, cfg: ProjectConfig, states: dict[str, TaskStateFile]) -> float | None:
        if cfg.policy.max_cost is None:
            return None
        spent = 0.0
        for tsf in states.values():
            for att in tsf.attempts:
                if att.usage is not None and att.usage.cost is not None:
                    if cfg.policy.cost_currency is None or att.usage.currency == cfg.policy.cost_currency:
                        spent += att.usage.cost
        return cfg.policy.max_cost - spent

    def _history(self, project: str):
        merge_history: list[tuple[str, int, str]] = []
        carve_outcomes: list[dict] = []
        review_rejections_by_area: dict[str, int] = {}
        blocked_underspecified_count = 0
        try:
            events = list(storage.iter_events(project))
        except Exception:
            events = []
        for ev in events:
            if ev.type is EventType.MERGE_RECORDED and ev.task_id:
                units = len(ev.payload.get("progress_units", []) or [])
                source = ev.payload.get("source_kind", "review")
                merge_history.append((ev.task_id, units, source))
            elif ev.type is EventType.CARVE_OUTCOME:
                carve_outcomes.append(ev.payload)
            elif ev.type is EventType.REVIEW_RECORDED and ev.payload.get("result") == "rejected":
                area = ev.payload.get("area", "unknown")
                review_rejections_by_area[area] = review_rejections_by_area.get(area, 0) + 1
            elif ev.type is EventType.TASK_BLOCKED:
                blocker = ev.payload.get("blocker") or {}
                if blocker.get("type") == "contract":
                    blocked_underspecified_count += 1
        merge_history.reverse()  # most recent first
        return merge_history[:50], carve_outcomes[-20:], review_rejections_by_area, blocked_underspecified_count

    def _ratchet_already_open(self, project: str) -> bool:
        try:
            recent = list(storage.iter_events(project))[-500:]
        except Exception:
            return False
        return any(ev.type is EventType.SPEC_ATTENTION and ev.payload.get("reason") == "ratchet"
                   for ev in recent)

    # -- event helpers -------------------------------------------------

    def _append_ev(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                   ev_type: EventType, payload: dict[str, Any], **kw) -> Event:
        ev = storage.append_and_apply(
            project, states, actor=Actor(ActorKind.TICK, "handoffd"), type=ev_type,
            payload=payload, **kw,
        )
        try:
            notify.notify_event(cfg, states, ev)
        except Exception:
            pass
        return ev

    def _transition(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                     task_id: str, to: TaskState, notes: str | None) -> Event:
        frm = states[task_id].state
        return self._append_ev(project, cfg, states, EventType.TASK_TRANSITIONED,
                                {"from": frm.value, "to": to.value, "notes": notes}, task_id=task_id)

    def _provider_pause(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                        route_id: str | None, task_id: str | None) -> list[Event]:
        out: list[Event] = []
        if route_id:
            self._provider_paused[route_id] = time.monotonic() + PROVIDER_PAUSE_SECONDS
        out.append(self._append_ev(project, cfg, states, EventType.PROVIDER_STATE_CHANGED,
                                    {"route_id": route_id, "state": "limited"}, task_id=task_id))
        out.append(self._append_ev(project, cfg, states, EventType.NEEDS_OPERATOR,
                                    {"route_id": route_id, "reason": "provider-limited"}, task_id=task_id))
        return out

    # -- execution map ---------------------------------------------------

    def _gate_hint(self, cfg: ProjectConfig) -> str:
        if not cfg.gates:
            return ""
        gate = sorted(cfg.gates.values(), key=lambda g: g.gate_id)[0]
        return " ".join(gate.argv)

    def _frontmatter_for(self, cfg: ProjectConfig, tsf: TaskStateFile):
        if not tsf.handoff_path:
            return None
        path = cfg.root / tsf.handoff_path
        if not path.exists():
            return None
        try:
            fm, _body = frontmatter.parse_handoff(path)
            return fm
        except Exception:
            return None

    def _lease_specs(self, cfg: ProjectConfig, fm) -> list[dict[str, Any]]:
        if fm is None:
            return []
        out = []
        for m in fm.effective_mutexes():
            mdef = cfg.mutexes.get(m)
            if mdef is None:
                continue
            out.append({"name": mdef.lease_name(cfg.project_id), "capacity": mdef.capacity})
        return out

    def _ensure_worktree(self, root: Path, branch: str, worktree_path: Path, default_branch: str) -> None:
        if worktree_path.exists():
            return
        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        check = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", branch],
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "add", str(worktree_path), branch],
                check=True, capture_output=True, text=True,
            )
        else:
            subprocess.run(
                ["git", "-C", str(root), "worktree", "add", "-b", branch, str(worktree_path), default_branch],
                check=True, capture_output=True, text=True,
            )

    def _next_resume_n(self, attempt_dir: Path) -> int:
        n = 1
        while (attempt_dir / f"attempt.resume-{n}.log").exists() or (attempt_dir / f"spec.resume-{n}.json").exists():
            n += 1
        return n

    def _execute(self, project: str, cfg: ProjectConfig, states: dict[str, TaskStateFile],
                 action: reconcile.Action) -> list[Event]:
        events: list[Event] = []

        if isinstance(action, reconcile.CreateTask):
            tsf = TaskStateFile(
                schema_version=storage.SCHEMA_VERSION, task_id=action.task_id, project=project,
                state=TaskState.CARVED, since=utc_now(), handoff_path=action.handoff_path,
            )
            events.append(self._append_ev(project, cfg, states, EventType.TASK_CREATED,
                                           {"statefile": tsf.to_dict()}, task_id=action.task_id))

        elif isinstance(action, reconcile.Transition):
            events.append(self._transition(project, cfg, states, action.task_id, action.to, action.notes))

        elif isinstance(action, reconcile.DispatchImplementer):
            task_id = action.task_id
            tsf = states[task_id]
            branch = f"feat/{task_id}"
            worktree_path = cfg.root / cfg.worktree_root / branch
            self._ensure_worktree(cfg.root, branch, worktree_path, cfg.default_branch)

            routes_obj = config.Routes.load()
            route_def = routes_obj.routes[action.route_id]
            attempt_id = new_id("att")
            route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                                variant=route_def.variant, effort=route_def.effort,
                                routes_rev=routes_obj.revision)
            attempt = Attempt(attempt_id=attempt_id, role=Role.IMPLEMENTER, state=AttemptState.CREATED,
                               route=route_snap, started=utc_now(), worktree=str(worktree_path),
                               branch=branch)
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=attempt_id))

            attempt_dir = paths.attempt_dir(project, attempt_id)
            fm_obj = self._frontmatter_for(cfg, tsf)
            gate_hint = self._gate_hint(cfg)
            receipt_path = str(attempt_dir / "receipt.json")
            argv, _prompt = adapters.build_dispatch(
                route_def, handoff_path=tsf.handoff_path or "", worktree=str(worktree_path),
                branch=branch, task_id=task_id, gate_hint=gate_hint, receipt_path=receipt_path,
            )
            spec = wrapper.WrapperSpec(
                project=project, task_id=task_id, attempt_id=attempt_id, argv=argv,
                cwd=str(worktree_path), log_path=str(attempt_dir / "attempt.log"),
                receipt_path=receipt_path, attempt_dir=str(attempt_dir),
                route_def=asdict(route_def), leases=self._lease_specs(cfg, fm_obj),
            )
            pid = wrapper.launch_detached(spec)
            attempt.state = AttemptState.PREFLIGHTING
            attempt.pid = pid
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=attempt_id))
            events.append(self._transition(project, cfg, states, task_id, TaskState.ACTIVE, None))

        elif isinstance(action, reconcile.ResumeAttempt):
            task_id = action.task_id
            tsf = states[task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt_dir = paths.attempt_dir(project, action.attempt_id)
            resume_n = self._next_resume_n(attempt_dir)
            routes_obj = config.Routes.load()
            route_def = routes_obj.routes[attempt.route.route_id]
            worktree = attempt.worktree or str(cfg.root)
            prompt = f"Resume {task_id} attempt {action.attempt_id} in {worktree}"
            argv = adapters.build_resume(route_def, session=attempt.session_handle,
                                          worktree=worktree, prompt=prompt)
            fm_obj = self._frontmatter_for(cfg, tsf)
            spec = wrapper.WrapperSpec(
                project=project, task_id=task_id, attempt_id=action.attempt_id, argv=argv,
                cwd=worktree, log_path=str(attempt_dir / f"attempt.resume-{resume_n}.log"),
                receipt_path=str(attempt_dir / "receipt.json"), attempt_dir=str(attempt_dir),
                route_def=asdict(route_def), leases=self._lease_specs(cfg, fm_obj),
            )
            pid = wrapper.launch_detached(spec)
            attempt.state = AttemptState.RUNNING
            attempt.pid = pid
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_RESUMED,
                                           {"attempt": attempt.to_dict()}, task_id=task_id,
                                           attempt_id=action.attempt_id))

        elif isinstance(action, reconcile.InterruptAttempt):
            tsf = states[action.task_id]
            attempt_dir = paths.attempt_dir(project, action.attempt_id)
            child_pid_file = attempt_dir / "child.pid"
            if child_pid_file.exists():
                try:
                    child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
                    pgid = os.getpgid(child_pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (ValueError, ProcessLookupError, OSError):
                    pass
            # No event: the wrapper emits ATTEMPT_INTERRUPTED itself on exit.

        elif isinstance(action, reconcile.MarkInterrupted):
            tsf = states[action.task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt.state = AttemptState.INTERRUPTED
            attempt.ended = utc_now()
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_INTERRUPTED,
                                           {"attempt": attempt.to_dict()}, task_id=action.task_id,
                                           attempt_id=action.attempt_id))

        elif isinstance(action, reconcile.StallCheck):
            pass  # _confirm_stall cache already updated during input build

        elif isinstance(action, reconcile.EmitAttemptExit):
            task_id = action.task_id
            tsf = states[task_id]
            attempt = tsf.attempt_by_id(action.attempt_id)
            attempt_dir = paths.attempt_dir(project, action.attempt_id)
            receipt_data = json.loads((attempt_dir / "receipt.json").read_text(encoding="utf-8"))
            receipt = Receipt.from_dict(receipt_data)
            if attempt.state != AttemptState.EXITED:
                # Wrapper died before emitting its own exit event — heal it.
                attempt.state = AttemptState.EXITED
                attempt.ended = utc_now()
                attempt.receipt = receipt
                events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_EXITED,
                                               {"attempt": attempt.to_dict()}, task_id=task_id,
                                               attempt_id=action.attempt_id))
            # else: wrapper already recorded the exit; only the task
            # transition below remains (idempotent healing).

            result = receipt.result
            if result is ReceiptResult.DONE:
                events.append(self._transition(project, cfg, states, task_id,
                                                TaskState.AWAITING_REVIEW, None))
            elif result is ReceiptResult.BLOCKED:
                blocker = Blocker(type=BlockerType.CONTRACT, unblock_condition="triage BLOCKED reason",
                                   detail=(receipt.blocked_reason or "")[:200])
                events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                               {"from": states[task_id].state.value,
                                                "blocker": blocker.to_dict()}, task_id=task_id))
            elif result is ReceiptResult.LIMIT:
                events.append(self._transition(project, cfg, states, task_id, TaskState.QUEUED, None))
                events.extend(self._provider_pause(project, cfg, states, attempt.route.route_id, task_id))
            elif result is ReceiptResult.ERROR:
                attempts_used = sum(
                    1 for a in states[task_id].attempts
                    if a.receipt is not None and a.receipt.result != ReceiptResult.LIMIT
                )
                if attempts_used < cfg.policy.max_attempts_per_task:
                    events.append(self._transition(project, cfg, states, task_id, TaskState.QUEUED, None))
                else:
                    blocker = Blocker(type=BlockerType.ENVIRONMENT,
                                       unblock_condition="triage BLOCKED reason",
                                       detail="attempts exhausted")
                    events.append(self._append_ev(project, cfg, states, EventType.TASK_BLOCKED,
                                                   {"from": states[task_id].state.value,
                                                    "blocker": blocker.to_dict()}, task_id=task_id))

        elif isinstance(action, reconcile.ProviderPause):
            events.extend(self._provider_pause(project, cfg, states, action.route_id, action.task_id))

        elif isinstance(action, reconcile.OpenWave):
            wave_id = new_id("wave")
            events.append(self._append_ev(project, cfg, states, EventType.WAVE_OPENED,
                                           {"task_ids": list(action.task_ids)}, wave_id=wave_id))

        elif isinstance(action, reconcile.LaunchReview):
            wave_id = action.wave_id
            attempt_id = new_id("att")
            attempt_dir = paths.attempt_dir(project, attempt_id)
            packet_dir = attempt_dir / "packet"
            packet_dir.mkdir(parents=True, exist_ok=True)
            packet_lines = ["# Review packet", ""]
            for t in action.task_ids:
                tsf_t = states.get(t)
                branch = f"feat/{t}"
                diff_res = subprocess.run(
                    ["git", "-C", str(cfg.root), "diff", f"{cfg.default_branch}...{branch}"],
                    capture_output=True, text=True,
                )
                stat_res = subprocess.run(
                    ["git", "-C", str(cfg.root), "diff", "--stat", f"{cfg.default_branch}...{branch}"],
                    capture_output=True, text=True,
                )
                (packet_dir / f"{t}.diff").write_text(diff_res.stdout, encoding="utf-8")
                packet_lines.append(f"## {t}")
                if tsf_t is not None and tsf_t.handoff_path:
                    packet_lines.append(f"- handoff: {tsf_t.handoff_path}")
                packet_lines.append(f"- diff stat:\n{stat_res.stdout}")
                packet_lines.append("")
            (packet_dir / "packet.md").write_text("\n".join(packet_lines), encoding="utf-8")

            routes_obj = config.Routes.load()
            review_routes = routes_obj.for_tier("frontier-review")
            route_def = review_routes[0]
            route_snap = Route(route_id=route_def.route_id, cli=route_def.cli, model=route_def.model,
                                variant=route_def.variant, effort=route_def.effort,
                                routes_rev=routes_obj.revision)
            first_task = action.task_ids[0] if action.task_ids else None
            attempt = Attempt(attempt_id=attempt_id, role=Role.FRONTIER_REVIEW,
                               state=AttemptState.CREATED, route=route_snap, started=utc_now(),
                               wave_id=wave_id)
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_CREATED,
                                           {"attempt": attempt.to_dict()}, task_id=first_task,
                                           attempt_id=attempt_id, wave_id=wave_id))

            gate_hint = self._gate_hint(cfg)
            receipt_path = str(attempt_dir / "receipt.json")
            argv, _prompt = adapters.build_dispatch(
                route_def, handoff_path=str(packet_dir / "packet.md"), worktree=str(cfg.root),
                branch=cfg.default_branch, task_id=first_task or wave_id or "review",
                gate_hint=gate_hint, receipt_path=receipt_path,
            )
            spec = wrapper.WrapperSpec(
                project=project, task_id=first_task or wave_id or "review", attempt_id=attempt_id,
                argv=argv, cwd=str(cfg.root), log_path=str(attempt_dir / "attempt.log"),
                receipt_path=receipt_path, attempt_dir=str(attempt_dir), route_def=asdict(route_def),
            )
            pid = wrapper.launch_detached(spec)
            attempt.state = AttemptState.PREFLIGHTING
            attempt.pid = pid
            events.append(self._append_ev(project, cfg, states, EventType.ATTEMPT_PREFLIGHTED,
                                           {"attempt": attempt.to_dict()}, task_id=first_task,
                                           attempt_id=attempt_id, wave_id=wave_id))

        elif isinstance(action, reconcile.SpecAttention):
            events.append(self._append_ev(project, cfg, states, EventType.SPEC_ATTENTION,
                                           {"reason": action.reason, "detail": action.detail},
                                           task_id=action.task_id))

        else:
            raise ValueError(f"unhandled action type: {type(action)!r}")

        return events

    # -- HTTP / SSE --------------------------------------------------------

    def _chosen_port(self) -> int:
        ports = []
        for root in self.registry.values():
            try:
                cfg = config.ProjectConfig.load(root)
                ports.append(cfg.policy.http_port)
            except Exception:
                continue
        return min(ports) if ports else DEFAULT_HTTP_PORT

    def _start_http(self) -> None:
        port = self._chosen_port()
        daemon = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
                pass

            def do_GET(self) -> None:  # noqa: N802
                try:
                    daemon._handle_get(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    try:
                        body = str(exc).encode("utf-8")
                        self.send_response(500)
                        self.send_header("Content-Type", "text/plain")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                    except Exception:
                        pass

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.http_port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        t.start()
        self._http_thread = t

    def _stop_http(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=5)
            self._http_thread = None

    @staticmethod
    def _send_json(handler: http.server.BaseHTTPRequestHandler, code: int, body: bytes) -> None:
        handler.send_response(code)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _handle_get(self, handler: http.server.BaseHTTPRequestHandler) -> None:
        parsed = urllib.parse.urlparse(handler.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            handler.send_response(302)
            handler.send_header("Location", "/www/index.html")
            handler.end_headers()
            return

        if path.startswith("/www/"):
            self._serve_www(handler, path[len("/www/"):])
            return

        if path == "/api/projects":
            self._send_json(handler, 200, json.dumps(self._api_projects()).encode("utf-8"))
            return

        if path == "/api/tasks":
            project = qs.get("project", [None])[0]
            if project is None or project not in self.registry:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            states = storage.list_states(project)
            body = json.dumps([s.to_dict() for s in states.values()]).encode("utf-8")
            self._send_json(handler, 200, body)
            return

        m = re.match(r"^/api/task/([^/]+)/([^/]+)$", path)
        if m:
            project, task_id = m.group(1), m.group(2)
            tsf = storage.load_state(project, task_id) if project in self.registry else None
            if tsf is None:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            self._send_json(handler, 200, json.dumps(tsf.to_dict()).encode("utf-8"))
            return

        m = re.match(r"^/api/log/([^/]+)/([^/]+)$", path)
        if m:
            project, attempt_id = m.group(1), m.group(2)
            try:
                tail = int(qs.get("tail", ["65536"])[0])
            except ValueError:
                tail = 65536
            self._serve_log(handler, project, attempt_id, tail)
            return

        if path == "/api/events":
            project = qs.get("project", [None])[0]
            try:
                since = int(qs.get("since", ["0"])[0])
            except ValueError:
                since = 0
            if project is None or project not in self.registry:
                self._send_json(handler, 404, b'{"error":"not found"}')
                return
            evs = list(storage.iter_events(project, since=since))[:500]
            body = json.dumps([e.to_dict() for e in evs]).encode("utf-8")
            self._send_json(handler, 200, body)
            return

        if path == "/api/stream":
            project = qs.get("project", [None])[0]
            self._serve_sse(handler, project)
            return

        self._send_json(handler, 404, b'{"error":"not found"}')

    def _api_projects(self) -> list[dict]:
        out = []
        for project, root in sorted(self.registry.items()):
            entry = {"project_id": project, "root": str(root)}
            try:
                cfg = config.ProjectConfig.load(root)
                entry["default_branch"] = cfg.default_branch
            except Exception:
                pass
            out.append(entry)
        return out

    def _serve_www(self, handler: http.server.BaseHTTPRequestHandler, rel: str) -> None:
        www = paths.www_dir().resolve()
        target = (www / rel).resolve()
        if not target.is_relative_to(www) or not target.is_file():
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        data = target.read_bytes()
        ctype = "text/html; charset=utf-8" if target.suffix in (".html", ".htm") else "application/octet-stream"
        handler.send_response(200)
        handler.send_header("Content-Type", ctype)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def _serve_log(self, handler: http.server.BaseHTTPRequestHandler, project: str,
                   attempt_id: str, tail: int) -> None:
        if project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        try:
            cfg = config.ProjectConfig.load(self.registry[project])
        except Exception:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        log_path = paths.attempt_dir(project, attempt_id) / "attempt.log"
        if not log_path.exists():
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        data = log_path.read_bytes()
        if tail > 0 and len(data) > tail:
            data = data[-tail:]
        text = data.decode("utf-8", errors="replace")
        redacted = cfg.redact(text)
        body = redacted.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/plain; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _serve_sse(self, handler: http.server.BaseHTTPRequestHandler, project: str | None) -> None:
        if project is None or project not in self.registry:
            self._send_json(handler, 404, b'{"error":"not found"}')
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "close")
        handler.end_headers()
        since = 0
        last_heartbeat = time.monotonic()
        try:
            while not self._stop_event.is_set():
                evs = list(storage.iter_events(project, since=since))
                for ev in evs:
                    since = ev.sequence
                    chunk = f"data: {json.dumps(ev.to_dict())}\n\n".encode("utf-8")
                    handler.wfile.write(chunk)
                handler.wfile.flush()
                now = time.monotonic()
                if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                    handler.wfile.write(b": hb\n\n")
                    handler.wfile.flush()
                    last_heartbeat = now
                time.sleep(SSE_POLL_SECONDS)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def run_once(project: str | None = None) -> int:
    """`tick --once`: single pass over one or all registered projects,
    no HTTP server, no pidfile. Returns total actions executed."""
    registry = config.load_registry()
    daemon = Daemon(registry)
    projects = [project] if project else list(registry)
    total = 0
    for p in projects:
        total += daemon.run_pass(p)
    return total
