"""CR-06a: the planning kernel -- the rule table, the arbiter, and the two
structural oracles over both.

Three kinds of test, for the same reason ``test_effects.py`` has three:

* ``TestThisRepo`` runs the structural rules against the REAL rule table the
  planner ships, so they bind the code that runs;
* the synthetic-table tests drive the arbiter and the emitter over made-up
  rules, so every refusal is seen to FAIL as well as pass. An arbiter that has
  never refused a second claim is not known to refuse one;
* the corpus tests assert the parent plan's two remaining acceptances --
  permutation invariance and single carve authority -- over the real
  historical projections rather than one hand-built input.

THE TWO ORACLES ARE THE POINT. ``emits`` and "rules are pure" are both
DERIVED from the code by walking each rule's own call graph with ``ast``, and
required to match the declaration exactly. A declaration that is not verified
is decoration, and the purity claim in particular had been FALSE transitively
for four packages before this oracle was written: ``plan_project`` reached
``stages.effective_concurrency``, which logged on every invocation.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

import corpus_profiles
import planner_corpus
import planner_synthetic
from nyxloom import planning, reconcile
from nyxloom.planning import (
    Channel, DuplicateRule, GrantIntent, GrantOutcome, MisusedGrant,
    PlanArbiter, PlanContext, RuleScope, RuleSpec, UnclaimedResource,
    UndeclaredClaim, UndeclaredEmission, UnknownResource, UnusedGrant,
    run_rules,
)
from nyxloom.types import (
    Attempt, AttemptState, Role, Route, TaskState, TaskStateFile,
)

SRC = Path(__file__).resolve().parent.parent / "src" / "nyxloom"

#: Every action class the planner can emit, derived from the module rather
#: than listed, so a new action joins the `emits` oracle the moment it exists.
ACTION_NAMES = frozenset(
    name for name, obj in vars(reconcile).items()
    if isinstance(obj, type) and issubclass(obj, reconcile.Action)
    and obj is not reconcile.Action
)


# ---------------------------------------------------------------------------
# the call graph both oracles walk


class CallGraph:
    """Module-level call graph over a set of Python sources.

    Follows a call when it can RESOLVE it: a function in the same module, a
    name imported from a sibling package module, or an attribute on a
    module alias (``stages.effective_concurrency``). It does not resolve
    method calls on values, which is the same limitation
    ``tests/test_effects.py`` accepts for the same reason -- the planner's
    shared helpers are module-level functions, which is exactly the population
    that can hide a capability from a reader.
    """

    def __init__(self, sources: dict[str, str]) -> None:
        self.functions: dict[tuple[str, str], ast.AST] = {}
        self.imports: dict[str, dict[str, tuple[str, str | None]]] = {}
        #: Names bound at each module's TOP LEVEL. A reachable function that
        #: stores through one of these mutates state that outlives the pass --
        #: the "no global mutation" clause of the parent acceptance, which a
        #: logging/clock/file scan cannot see.
        self.module_names: dict[str, frozenset[str]] = {}
        for module, source in sources.items():
            tree = ast.parse(source)
            self.module_names[module] = _module_level_names(tree)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.functions.setdefault((module, node.name), node)
            imports: dict[str, tuple[str, str | None]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1:
                    for alias in node.names:
                        local = alias.asname or alias.name
                        if node.module:
                            imports[local] = (node.module.split(".")[0], alias.name)
                        else:
                            imports[local] = (alias.name, None)   # module alias
            self.imports[module] = imports

    def _resolve(self, module: str, name: str) -> tuple[str, str] | None:
        if (module, name) in self.functions:
            return (module, name)
        target = self.imports.get(module, {}).get(name)
        if target and target[1] is not None and (target[0], target[1]) in self.functions:
            return (target[0], target[1])
        return None

    def reachable(self, module: str, name: str) -> list[tuple[tuple[str, str], ast.AST]]:
        pending = [(module, name)]
        seen: set[tuple[str, str]] = set()
        found: list[tuple[tuple[str, str], ast.AST]] = []
        while pending:
            key = pending.pop()
            if key in seen:
                continue
            seen.add(key)
            node = self.functions.get(key)
            if node is None:
                continue
            found.append((key, node))
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Name):
                    target = self._resolve(key[0], func.id)
                    if target is not None:
                        pending.append(target)
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    alias = self.imports.get(key[0], {}).get(func.value.id)
                    if (alias is not None and alias[1] is None
                            and (alias[0], func.attr) in self.functions):
                        pending.append((alias[0], func.attr))
        return found


def _module_level_names(tree: ast.Module) -> frozenset[str]:
    """The names a module binds at its top level."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def _package_sources() -> dict[str, str]:
    return {path.stem: path.read_text(encoding="utf-8") for path in SRC.glob("*.py")}


def _graph() -> CallGraph:
    return CallGraph(_package_sources())


def _root_of(spec: RuleSpec) -> tuple[str, str]:
    return (spec.rule.__module__.rsplit(".", 1)[-1], spec.rule.__name__)


# ---------------------------------------------------------------------------
# oracle 1: what a rule can emit


def derive_emits(graph: CallGraph, module: str, name: str) -> frozenset[str]:
    """Action classes the call graph rooted at ``module.name`` can construct.

    CONSTRUCTION, not emission: an action built in a helper and handed back to
    be emitted is still that rule's to declare, and a rule that merely names
    an action class in a comparison is not emitting it.
    """
    found: set[str] = set()
    for _key, node in graph.reachable(module, name):
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Name) and func.id in ACTION_NAMES:
                found.add(func.id)
            elif isinstance(func, ast.Attribute) and func.attr in ACTION_NAMES:
                found.add(func.attr)
    return frozenset(found)


def derive_note_kinds(graph: CallGraph, module: str, name: str) -> frozenset[str]:
    """Breadcrumb ``kind`` values the call graph rooted at ``module.name`` can
    record.

    The same derivation shape as ``derive_emits``, against the other declared
    vocabulary a rule writes into: ``reconcile.TRACE_KINDS``. Every kind is a
    string LITERAL at the call site (module contract item 8 forbids prose in a
    breadcrumb, and a computed kind would be prose by another route), so the
    first argument of every reachable ``.note(...)`` is exactly the set.
    """
    kinds: set[str] = set()
    for _key, node in graph.reachable(module, name):
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "note" and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)):
                kinds.add(call.args[0].value)
    return frozenset(kinds)


# ---------------------------------------------------------------------------
# oracle 2: what a rule may NOT reach
#
# The parent's acceptance is "rules perform no I/O, clock read, logging,
# environment read, or global mutation". Each entry below is one way to break
# that, named by what it would break.

#: Calls on these module names are the capability, whatever the attribute.
_FORBIDDEN_BASES = {
    "log": "logging", "logger": "logging", "logging": "logging",
    "subprocess": "subprocess", "os": "environment/process",
    "storage": "store append", "shutil": "file I/O", "socket": "network",
    "requests": "network", "urllib": "network", "sys": "process state",
}
#: Bare-name calls that are the capability by themselves.
_FORBIDDEN_NAMES = {
    "open": "file I/O", "print": "logging", "input": "console I/O",
    "utc_now": "clock read", "get_logger": "logging",
}
#: Clock reads, which are only impure on a clock-bearing base.
_CLOCK_BASES = {"datetime", "time", "clock"}
_CLOCK_ATTRS = {"now", "utcnow", "monotonic", "time"}
#: Path/file operations, recognised by method name because the receiver is a
#: value the walker cannot resolve.
_FORBIDDEN_METHODS = {
    "read_text": "file I/O", "write_text": "file I/O", "read_bytes": "file I/O",
    "write_bytes": "file I/O", "mkdir": "file I/O", "unlink": "file I/O",
    "iterdir": "file I/O", "glob": "file I/O", "rglob": "file I/O",
    "touch": "file I/O", "append_and_apply": "store append",
    "apply_event": "store append", "iter_events": "store read",
}
#: Methods that mutate their receiver in place. Only impure when the receiver
#: is a name the MODULE binds -- a rule appending to its own local list is the
#: normal way a rule works.
_MUTATING_METHODS = {
    "append", "extend", "insert", "remove", "sort", "reverse",
    "add", "discard", "update", "setdefault", "pop", "clear",
}


def _impure_uses(node: ast.AST, module_names: frozenset[str] = frozenset()) -> list[str]:
    hits: list[str] = []
    for child in ast.walk(node):
        # -- global mutation: the acceptance clause the capability scan above
        # cannot see. Three shapes reach module state from inside a function:
        # rebinding it (`global X`), storing through it (`X[k] = v`,
        # `X.attr = v`), and mutating it in place (`X.append(...)`). A plain
        # `X = v` is NOT one of them -- without a `global` statement that
        # binds a local that shadows the module name and dies with the call.
        if isinstance(child, ast.Global):
            hits.append(f"global {', '.join(child.names)} (global mutation)")
        targets: list[ast.AST] = []
        if isinstance(child, ast.Assign):
            targets = list(child.targets)
        elif isinstance(child, (ast.AugAssign, ast.AnnAssign)):
            targets = [child.target]
        for target in targets:
            base = target
            while isinstance(base, (ast.Subscript, ast.Attribute)):
                base = base.value
            if base is not target and isinstance(base, ast.Name) and base.id in module_names:
                hits.append(f"{base.id}[...] = ... (global mutation)")
        if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            capability = _FORBIDDEN_BASES.get(child.value.id)
            if capability is not None:
                hits.append(f"{child.value.id}.{child.attr} ({capability})")
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            capability = _FORBIDDEN_NAMES.get(func.id)
            if capability is not None:
                hits.append(f"{func.id}() ({capability})")
        elif isinstance(func, ast.Attribute):
            if func.attr in _FORBIDDEN_METHODS:
                hits.append(f".{func.attr}() ({_FORBIDDEN_METHODS[func.attr]})")
            if (isinstance(func.value, ast.Name) and func.value.id in _CLOCK_BASES
                    and func.attr in _CLOCK_ATTRS):
                hits.append(f"{func.value.id}.{func.attr}() (clock read)")
            if (isinstance(func.value, ast.Name) and func.value.id in module_names
                    and func.attr in _MUTATING_METHODS):
                hits.append(f"{func.value.id}.{func.attr}() (global mutation)")
    return hits


def impurities(graph: CallGraph, module: str, name: str) -> list[str]:
    """Every impure capability reachable from ``module.name``."""
    found: list[str] = []
    for (mod, fn), node in graph.reachable(module, name):
        found.extend(f"{mod}.{fn} -> {hit}"
                     for hit in _impure_uses(node, graph.module_names.get(mod, frozenset())))
    return sorted(set(found))


# ---------------------------------------------------------------------------
# the rules that bind the shipping table


class TestThisRepo:

    @pytest.fixture(scope="class")
    def table(self):
        return planning.rule_table()

    @pytest.fixture(scope="class")
    def graph(self):
        return _graph()

    def test_declared_emissions_match_what_each_rule_can_actually_emit(
            self, table, graph):
        """`emits` is derived from the code, not trusted.

        Over-broad fails exactly like missing: a declaration listing an action
        the rule cannot plan is not a permission, it is noise that makes the
        real permission unreadable -- and it is also how the resource-claim
        check below gets quietly widened, since a spec must claim the resource
        for every resource-bound action it declares.
        """
        mismatches = []
        for spec in table:
            actual = derive_emits(graph, *_root_of(spec))
            if actual != spec.emits:
                mismatches.append(
                    f"{spec.name}: declared {sorted(spec.emits)}, "
                    f"code can emit {sorted(actual)}")
        assert not mismatches, "\n".join(mismatches)

    def test_no_rule_can_reach_an_impure_capability(self, table, graph):
        """The parent's acceptance, made structural.

        `PlanContext.derive` is a root too: an impure context derivation would
        make the pass impure whatever the rules themselves do, and it is where
        the one real violation lived -- `stages.effective_concurrency` logged
        on every call, so a pass emitted a DEBUG record twice plus once per
        queued task while `reconcile.py`'s docstring claimed no logger was
        reachable from it.
        """
        roots = [_root_of(spec) for spec in table]
        roots += [("planning", "derive"), ("planning", "tasks"),
                  ("reconcile", "plan_project")]
        offenders = []
        for module, name in roots:
            for hit in impurities(graph, module, name):
                offenders.append(f"{module}.{name}: {hit}")
        assert not offenders, (
            "a planning rule can reach an impure capability:\n"
            + "\n".join(sorted(set(offenders))))

    def test_the_breadcrumb_vocabulary_is_exactly_what_the_rules_can_record(
            self, table, graph):
        """`reconcile.TRACE_KINDS` is derived from the rules, not trusted.

        It had ALREADY DRIFTED when CR-06a landed: F019 P1b (2026-07-25) added
        the `"gate-diagnosis"` breadcrumb to the REVIEW_REJECTED triage and
        never registered it here, and four months of passes emitted a kind the
        declaration said did not exist. Nothing failed, because this frozenset
        had NO consumer -- `TraceNote`'s docstring calls `kind` "one of
        TRACE_KINDS" and nothing checked. That is the same defect class as an
        unverified `emits`, and it gets the same answer.

        BOTH directions, like the legacy budget: a kind a rule can record and
        this set omits is drift, and a kind this set declares that no rule can
        record is a vocabulary entry outliving the rule that used it -- which
        is how a reader learns to distrust the whole list.
        """
        recorded = frozenset().union(*(
            derive_note_kinds(graph, *_root_of(spec)) for spec in table))
        assert recorded, "no breadcrumb kind derived -- the walk found nothing"
        assert recorded == reconcile.TRACE_KINDS, (
            f"kinds a rule records but TRACE_KINDS omits: "
            f"{sorted(recorded - reconcile.TRACE_KINDS)}; "
            f"kinds TRACE_KINDS declares that no rule can record: "
            f"{sorted(reconcile.TRACE_KINDS - recorded)}")

    def test_every_resource_bound_action_names_a_real_action_class(self):
        """`EXCLUSIVE_ACTIONS` is keyed by class NAME so the kernel need not
        import the vocabulary -- which means a TYPO in a key silently disables
        the guard for that action. Nothing else would notice: the emit-time
        lookup simply misses, `RuleSpec.__post_init__`'s claim check simply
        misses, and a carve-family action becomes emittable by any rule.

        The carve-family completeness check is by name deliberately. A new
        `*Carve*` action that genuinely is NOT carve authority is possible, but
        it should have to be argued in a diff to this test rather than arrive
        as an omission -- which is exactly how the ninth producer arrives.
        """
        unknown = set(planning.EXCLUSIVE_ACTIONS) - ACTION_NAMES
        assert not unknown, (
            f"EXCLUSIVE_ACTIONS names no such action class: {sorted(unknown)} "
            f"-- the guard for it is silently off")
        assert set(planning.EXCLUSIVE_ACTIONS.values()) <= planning.KNOWN_RESOURCES
        carve_family = {name for name in ACTION_NAMES if "Carve" in name}
        assert carve_family <= set(planning.EXCLUSIVE_ACTIONS), (
            f"carve-family actions with no exclusive resource: "
            f"{sorted(carve_family - set(planning.EXCLUSIVE_ACTIONS))}")

    def test_the_legacy_rule_budget_is_exact_in_both_directions(self, table):
        """Over budget means new debt; under budget means debt repaid without
        recording it. The second half is what keeps the ratchet a ratchet."""
        legacy = [spec for spec in table if spec.legacy_owner]
        assert len(legacy) == planning.LEGACY_RULE_BUDGET, (
            f"legacy rules are {sorted(s.name for s in legacy)}; "
            f"LEGACY_RULE_BUDGET says {planning.LEGACY_RULE_BUDGET}")
        assert {spec.legacy_owner for spec in legacy} <= {"CR-06b", "CR-06c"}

    def test_every_rule_names_a_real_contract_item(self, table):
        """The module contract is the frozen interface. Item 8 (actions never
        embed prose) is a constraint on every action rather than a decision,
        so it is the one numbered item no rule implements."""
        covered = {item for spec in table for item in spec.contract_items}
        assert covered <= set(range(1, 18))
        assert covered == set(range(1, 18)) - {8}, (
            f"contract items with no owning rule: "
            f"{sorted(set(range(1, 18)) - {8} - covered)}")

    def test_rule_names_and_ordering_are_unambiguous(self, table):
        names = [spec.name for spec in table]
        assert len(set(names)) == len(names)
        assert all(spec.rationale for spec in table)
        # Every TASK rule runs in the kernel's shared loop, so they must be
        # contiguous or the interleaving they preserve is not preserved.
        scopes = [spec.scope for spec in table]
        first_plan = scopes.index(RuleScope.PLAN)
        assert all(s is RuleScope.TASK for s in scopes[:first_plan])
        assert all(s is RuleScope.PLAN for s in scopes[first_plan:])

    def test_every_carve_family_action_is_claimed_by_its_rule(self, table):
        """The declaration side of single carve authority: a rule that can
        emit a carve-family action declares the claim, so the set of
        contenders is readable from the table rather than from eight `and not`
        clauses scattered through a function body."""
        claimants = {spec.name for spec in table if "carve-slot" in spec.claims}
        emitters = {
            spec.name for spec in table
            if spec.emits & set(planning.EXCLUSIVE_ACTIONS)
        }
        assert emitters <= claimants
        assert len(claimants) >= 5, "the arbiter has too few claimants to be real"


# ---------------------------------------------------------------------------
# the oracles, seen to fail


def test_the_emissions_oracle_rejects_a_declaration_the_code_contradicts():
    """Negative control: narrowing and widening a real declaration must both
    disagree with the derivation, or a walk that silently found nothing would
    let every declaration pass."""
    graph = _graph()
    spec = next(s for s in planning.rule_table() if s.name == "reject-triage")
    actual = derive_emits(graph, *_root_of(spec))
    assert actual == spec.emits
    assert actual != spec.emits - {"Transition"}
    assert actual != spec.emits | {"CarveDispatch"}


def test_the_purity_oracle_sees_a_direct_impurity():
    """`stages.compose` logs directly. If the oracle cannot see that, its
    silence about the rules means nothing."""
    graph = _graph()
    assert any("logging" in hit for hit in impurities(graph, "stages", "compose"))


def test_the_purity_oracle_follows_a_call_into_another_module():
    """The control that matters, in the exact shape of the violation this
    package found: the impurity is not in the rule, it is two modules away
    behind an ordinary helper call.

    Synthetic sources rather than production ones, so the control keeps
    working after the production violation is fixed -- a negative control that
    depends on a bug surviving is not a control.
    """
    graph = CallGraph({
        "rules_probe": (
            "from .helpers_probe import resolve\n"
            "def a_rule(ctx, emit):\n"
            "    return resolve(1, 2)\n"
        ),
        "helpers_probe": (
            "from .log import get_logger\n"
            "log = get_logger('probe')\n"
            "def resolve(a, b):\n"
            "    result = a + b\n"
            "    log.debug('resolved', result=result)\n"
            "    return result\n"
        ),
    })
    hits = impurities(graph, "rules_probe", "a_rule")
    assert hits == ["helpers_probe.resolve -> log.debug (logging)"]
    # and the same graph with the log line gone is clean
    clean = CallGraph({
        "rules_probe": (
            "from .helpers_probe import resolve\n"
            "def a_rule(ctx, emit):\n"
            "    return resolve(1, 2)\n"
        ),
        "helpers_probe": "def resolve(a, b):\n    return a + b\n",
    })
    assert impurities(clean, "rules_probe", "a_rule") == []


@pytest.mark.parametrize("source, expected", [
    ("_CACHE = {}\ndef a_rule(ctx, emit):\n    global _CACHE\n    _CACHE = {}\n",
     "global _CACHE (global mutation)"),
    ("_CACHE = {}\ndef a_rule(ctx, emit):\n    _CACHE['seen'] = 1\n",
     "_CACHE[...] = ... (global mutation)"),
    ("_SEEN: set = set()\ndef a_rule(ctx, emit):\n    _SEEN.add(ctx)\n",
     "_SEEN.add() (global mutation)"),
    ("_N = 0\ndef a_rule(ctx, emit):\n    _N.total += 1\n",
     "_N[...] = ... (global mutation)"),
])
def test_the_purity_oracle_sees_a_rule_reach_module_state(source, expected):
    """The acceptance's fifth clause -- "no global mutation" -- which the
    capability scan cannot see at all: nothing about `_CACHE['seen'] = 1` is a
    log, a clock, a file or a subprocess.

    It is the clause that matters most to a rule TABLE: `PlanContext` is
    frozen precisely so a rule cannot hand state to a later rule, and a module
    global is the obvious way around that. Each shape is a separate case
    because they are separate AST nodes, and a scan that caught only `global`
    would miss the two that need no statement at all.
    """
    graph = CallGraph({"probe": source})
    hits = impurities(graph, "probe", "a_rule")
    assert [h.split(" -> ", 1)[1] for h in hits] == [expected], hits


def test_a_rules_own_local_bookkeeping_is_not_global_mutation():
    """The control that keeps the check from being a blanket ban: every legacy
    rule builds its actions in a local list and appends to it, so a scan that
    flagged `.append` regardless of receiver would fail the whole shipping
    table and have to be turned off."""
    graph = CallGraph(
        {"probe": "_CACHE = {}\ndef a_rule(ctx, emit):\n"
                  "    acc = []\n    acc.append(1)\n    local = {}\n"
                  "    local['k'] = 1\n    _CACHE = 2\n    return acc\n"})
    assert impurities(graph, "probe", "a_rule") == []


@pytest.mark.parametrize("body, expected", [
    ("    return open('/etc/passwd').read()", "file I/O"),
    ("    import datetime\n    return datetime.now()", "clock read"),
    ("    return os.environ.get('X')", "environment/process"),
    ("    return subprocess.run(['ls'])", "subprocess"),
    ("    return ctx.path.read_text()", "file I/O"),
    ("    return storage.append_and_apply('p', {})", "store append"),
])
def test_the_purity_oracle_names_each_forbidden_capability(body, expected):
    """One case per capability the acceptance lists, so none of them is
    covered only by a pattern that happens to overlap another."""
    graph = CallGraph({"probe": f"def a_rule(ctx, emit):\n{body}\n"})
    hits = impurities(graph, "probe", "a_rule")
    assert any(expected in hit for hit in hits), hits


# ---------------------------------------------------------------------------
# the arbiter, seen to fail
#
# These build synthetic rules rather than using the real table, because the
# rule that matters is about a producer NOBODY HAS WRITTEN YET -- the ninth
# carve producer that forgets the check. A test over only the existing rules
# could not express it.


def _probe_spec(**overrides) -> RuleSpec:
    base = dict(
        name="probe", contract_items=(9,), concern="probe",
        scope=RuleScope.PLAN, channel=Channel.CARVE,
        rule=lambda ctx, emit: None, emits=frozenset({"CarveDispatch"}),
        claims=frozenset({"carve-slot"}), rationale="probe",
    )
    base.update(overrides)
    return RuleSpec(**base)


def _context(**overrides) -> PlanContext:
    states = next(states for _, states in planner_corpus.projections())
    return PlanContext.derive(corpus_profiles.build(states, "neutral"))


def test_a_second_claim_on_one_resource_is_refused():
    """RESTATED from CR-06b (amendment §5.2): the observable moved.

    `grant` returned a bare `bool`, and `False` covered BOTH "another rule
    holds this" and "you already hold this" -- so the current holder re-taking
    its own grant read as losing a contest it had already won. Same
    projections, same three claims, one more outcome: `GrantOutcome`.
    """
    arbiter = PlanArbiter()
    first, second = _probe_spec(name="first"), _probe_spec(name="second")
    assert arbiter.grant(first, "carve-slot") is GrantOutcome.GRANTED
    assert arbiter.grant(second, "carve-slot") is GrantOutcome.REFUSED
    assert arbiter.holder("carve-slot") == "first"
    assert arbiter.available("carve-slot") is False
    assert arbiter.grants() == {"carve-slot": "first"}


def test_the_current_holder_reclaiming_is_not_a_lost_contest():
    """The readability trap CR-06a and CR-06b both left for this package.

    `grant` answering `False` to the rule that ALREADY HOLDS the resource is
    not wrong so much as unreadable: the caller's only question is "may I act
    on this", and the honest answers are three, not two. A rule that read that
    `False` as "someone else won" would skip its own emission and leave the
    grant it holds unspent -- which now fails the pass as well, so the two
    footguns this package owns were the same footgun from both ends.
    """
    arbiter = PlanArbiter()
    spec = _probe_spec(name="only")
    assert arbiter.grant(spec, "carve-slot") is GrantOutcome.GRANTED
    again = arbiter.grant(spec, "carve-slot")
    assert again is GrantOutcome.ALREADY_HELD
    assert again.holds is True
    assert arbiter.grants() == {"carve-slot": "only"}


@pytest.mark.parametrize("outcome, holds", [
    (GrantOutcome.GRANTED, True),
    (GrantOutcome.ALREADY_HELD, True),
    (GrantOutcome.REFUSED, False),
])
def test_a_grant_outcome_answers_holds_and_refuses_to_be_truth_tested(outcome, holds):
    """`if emit.claim(r):` is the shape that made the old bool dangerous.

    An enum is truthy for every member, so leaving `__bool__` alone would have
    made that line silently ALWAYS true -- strictly worse than the bool it
    replaced. It raises instead, which is the only answer that cannot be
    misread, and `.holds` is the question the caller meant.
    """
    assert outcome.holds is holds
    with pytest.raises(TypeError) as exc:
        bool(outcome)
    assert ".holds" in str(exc.value)


def test_two_rules_cannot_both_authorize_the_carve_slot_in_one_pass():
    """The parent's acceptance, driven through the real kernel.

    Both rules WANT the slot and both try to emit. The second one's emission
    raises rather than landing in the plan -- which is the difference between
    an arbiter and a boolean: a flag can only stop a producer that consults
    it, and this stops one that does not.
    """
    def greedy(ctx, emit):
        emit.claim("carve-slot")
        emit(reconcile.CarveDispatch(project="p"))

    table = (_probe_spec(name="first", rule=greedy),
             _probe_spec(name="second", rule=greedy))
    arbiter = PlanArbiter(table)
    with pytest.raises(UnclaimedResource) as exc:
        run_rules(_context(), table, reconcile.ReconcileTrace(), arbiter)
    assert "second" in str(exc.value) and "first" in str(exc.value)
    assert arbiter.grants() == {"carve-slot": "first"}


def test_a_rule_that_emits_a_carve_action_without_claiming_is_refused():
    """The ninth producer. It never consults the mutex at all -- exactly the
    failure a `carve_dispatch_planned` boolean cannot detect -- and it is
    refused because the EMISSION requires the grant, not because the author
    remembered to check."""
    def forgetful(ctx, emit):
        emit(reconcile.CarveDispatch(project="p"))

    table = (_probe_spec(rule=forgetful),)
    with pytest.raises(UnclaimedResource):
        run_rules(_context(), table, reconcile.ReconcileTrace(), PlanArbiter(table))


# ---------------------------------------------------------------------------
# claim-then-not-fire, and the deliberate version of it (CR-06c)
#
# The distinction these four tests draw is the one CR-06a and CR-06b both
# recorded and deferred. A rule may legitimately take the pass's only carve
# slot and plan NOTHING -- `carver_session_ladder` does exactly that for
# STARTING/COMPACTING, where a carver turn is already in flight and the slot is
# correctly spent. So the arbiter cannot forbid it. But a rule that took the
# grant and FORGOT to fire starves every rule below it identically, and nothing
# structural told the two apart.


def test_a_rule_that_claims_the_slot_and_emits_nothing_fails_the_pass():
    """The forgot-to-fire half, caught at the pass that does it.

    This is what a ninth carve producer's FIRST bug looks like -- not emitting
    without the grant (already refused), but taking the grant on a branch that
    then falls through. Every rule below it is silently starved of a resource
    the pass never used, and the contention is invisible in this rule's own
    source, which is why it survives review.
    """
    def promises_and_forgets(ctx, emit):
        emit.claim("carve-slot")

    table = (_probe_spec(rule=promises_and_forgets),)
    with pytest.raises(UnusedGrant) as exc:
        run_rules(_context(), table, reconcile.ReconcileTrace(), PlanArbiter(table))
    assert "probe" in str(exc.value) and "reserve(" in str(exc.value)


def test_a_rule_may_consume_the_slot_deliberately_if_it_says_why():
    """The legitimate half, and the reason the check above cannot just ban it.

    A reservation blocks every later claimant exactly as a claim does -- that
    is its whole purpose -- and it survives the audit, because the ledger
    records WHY nothing was planned. "Deliberately consumed" stops being a
    comment and becomes a value.
    """
    def waits_on_a_turn_already_in_flight(ctx, emit):
        emit.reserve("carve-slot", "wait:starting")

    table = (_probe_spec(rule=waits_on_a_turn_already_in_flight),)
    arbiter = PlanArbiter(table)
    run_rules(_context(), table, reconcile.ReconcileTrace(), arbiter)
    record, = arbiter.ledger()
    assert record.rule == "probe"
    assert record.intent is GrantIntent.RESERVE
    assert record.reason == "wait:starting"
    assert record.spent is False
    assert arbiter.available("carve-slot") is False


def test_a_reservation_must_name_its_reason():
    """An unexplained reservation IS the forgot-to-fire case wearing the other
    method's name, so the reason is a required argument rather than a
    convention."""
    with pytest.raises(ValueError) as exc:
        PlanArbiter().reserve(_probe_spec(), "carve-slot", "")
    assert "forgot to fire" in str(exc.value)


def _reserve_then_claim(ctx, emit):
    emit.reserve("carve-slot", "wait:starting")
    emit.claim("carve-slot")


def _claim_then_reserve(ctx, emit):
    emit.claim("carve-slot")
    emit.reserve("carve-slot", "wait:starting")


@pytest.mark.parametrize("flip", [_reserve_then_claim, _claim_then_reserve])
def test_a_grants_purpose_cannot_be_changed_after_it_is_taken(flip):
    """The route around both checks, closed, in both directions.

    A rule that reserved ("nothing will be planned") could otherwise re-take
    the same grant as a claim and emit under it -- or claim, fail to fire, and
    re-take it as a reservation to escape the audit. Either would make the
    intent a label instead of a commitment, so a rule holds a resource for the
    purpose it took it for or not at all.
    """
    table = (_probe_spec(rule=flip),)
    with pytest.raises(MisusedGrant) as exc:
        run_rules(_context(), table, reconcile.ReconcileTrace(), PlanArbiter(table))
    assert "fixed when it is taken" in str(exc.value)


def test_emitting_under_a_reservation_is_refused():
    """The other direction: a rule that told the arbiter the slot was
    deliberately consumed, and then consumed it for real. Every rule below was
    blocked on the strength of the first claim, so the second one is not a
    bonus action -- it is the second carve authority this kernel exists to
    make unrepresentable, reached through the one branch that is allowed to
    plan nothing."""
    def reserves_then_emits(ctx, emit):
        emit.reserve("carve-slot", "wait:compacting")
        emit(reconcile.CarveDispatch(project="p"))

    table = (_probe_spec(rule=reserves_then_emits),)
    with pytest.raises(MisusedGrant) as exc:
        run_rules(_context(), table, reconcile.ReconcileTrace(), PlanArbiter(table))
    assert "RESERVATION" in str(exc.value)


def test_a_reservation_blocks_a_later_claimant_exactly_as_a_claim_does():
    """Two rules, the first reserving. The second must be refused -- otherwise
    "the slot is spent because a turn is already in flight" would be a comment
    the arbiter ignores."""
    refused: list = []

    def reserves(ctx, emit):
        emit.reserve("carve-slot", "wait:starting")

    def wants_it(ctx, emit):
        outcome = emit.claim("carve-slot")
        refused.append(outcome)
        assert outcome.holds is False, "a reservation did not block a claim"

    table = (_probe_spec(name="first", rule=reserves),
             _probe_spec(name="second", rule=wants_it))
    arbiter = PlanArbiter(table)
    channels = run_rules(_context(), table, reconcile.ReconcileTrace(), arbiter)
    assert refused == [GrantOutcome.REFUSED]
    assert channels.assemble() == []
    assert arbiter.grants() == {"carve-slot": "first"}


def test_one_grant_authorises_exactly_one_carve_action():
    """The last way one rule could still plan two carves, closed.

    The arbiter made a SECOND GRANT unrepresentable from CR-06a. It did not
    make a second ACTION under one grant unrepresentable, so "at most one
    CarveDispatch per pass" still rested on each rule's internal shape -- the
    ladder's elif chain appending exactly one action, each cadence returning
    after its single emit. That is the per-author discipline the arbiter
    exists to replace, and it is exactly how the eighth copy of
    `and not carve_dispatch_planned` came to exist.

    Found by trying to break the invariant rather than by reading for it, and
    it moved zero corpus plans: no shipping rule emits twice today.
    """
    def greedy_once_granted(ctx, emit):
        emit.claim("carve-slot")
        emit(reconcile.CarveDispatch(project="p"))
        emit(reconcile.CarveDispatch(project="p", kind="test-health"))

    table = (_probe_spec(rule=greedy_once_granted),)
    arbiter = PlanArbiter(table)
    with pytest.raises(MisusedGrant) as exc:
        run_rules(_context(), table, reconcile.ReconcileTrace(), arbiter)
    assert "second" in str(exc.value)
    # the FIRST action was legitimate and the grant records it as spent
    assert arbiter.ledger()[0].spent is True


def test_a_spec_that_can_emit_a_carve_action_must_declare_the_claim():
    with pytest.raises(UndeclaredClaim):
        _probe_spec(claims=frozenset())


def test_reserving_a_resource_the_spec_does_not_declare_is_refused():
    """A reservation is a grant, so it answers to the same declaration the
    arbiter holds a claim to -- otherwise a rule could block the carve slot
    without ever appearing in the table's list of contenders."""
    spec = _probe_spec(emits=frozenset({"VerifyGate"}), claims=frozenset())
    with pytest.raises(UndeclaredClaim):
        PlanArbiter().reserve(spec, "carve-slot", "why")


def test_claiming_a_resource_the_spec_does_not_declare_is_refused():
    spec = _probe_spec(emits=frozenset({"VerifyGate"}), claims=frozenset())
    with pytest.raises(UndeclaredClaim):
        PlanArbiter().grant(spec, "carve-slot")


def test_an_unknown_resource_is_refused_in_every_direction():
    spec = _probe_spec(emits=frozenset({"VerifyGate"}), claims=frozenset())
    arbiter = PlanArbiter()
    with pytest.raises(UnknownResource):
        arbiter.available("gpu")
    with pytest.raises(UnknownResource):
        arbiter.holder("gpu")
    with pytest.raises(UnknownResource):
        arbiter.grant(spec, "gpu")
    with pytest.raises(UnknownResource):
        _probe_spec(claims=frozenset({"gpu"}))


def test_emitting_an_undeclared_action_type_is_refused():
    def wrong(ctx, emit):
        emit(reconcile.OpenWave(task_ids=["t1"]))

    table = (_probe_spec(emits=frozenset({"VerifyGate"}), claims=frozenset(),
                         rule=wrong),)
    with pytest.raises(UndeclaredEmission):
        run_rules(_context(), table, reconcile.ReconcileTrace(), PlanArbiter(table))


def test_two_rules_may_not_share_a_name():
    with pytest.raises(DuplicateRule):
        PlanArbiter((_probe_spec(), _probe_spec()))


def test_two_rules_sharing_a_name_cannot_launder_one_grant_into_two_carves():
    """The name-collision route to a second carve authority, closed in the
    kernel rather than only in the arbiter's constructor.

    A rule NAME is the arbiter's IDENTITY for a rule: the grant ledger is
    keyed by it and `RuleEmitter.__call__` authorises an exclusive emission by
    comparing the holder's name to the emitting spec's. So two specs sharing a
    name are ONE identity -- the second emits under the first's grant and the
    pass plans TWO CarveDispatches, which is precisely the invariant this
    kernel exists to make unrepresentable, reached by copy-pasting a table
    entry and forgetting to change `name=`.

    `PlanArbiter.__init__` refused the collision, but `run_rules` takes an
    ARBITER, not a table, so the invariant depended on the caller having built
    that arbiter from this table. `run_rules` builds the emitter map, so it is
    where the collision becomes an identity, and it now refuses there too.
    """
    def greedy(ctx, emit):
        emit.claim("carve-slot")
        emit(reconcile.CarveDispatch(project="p"))

    table = (_probe_spec(name="dup", rule=greedy),
             _probe_spec(name="dup", rule=greedy))
    # An arbiter built WITHOUT the table -- the gap the constructor check left.
    with pytest.raises(DuplicateRule):
        run_rules(_context(), table, reconcile.ReconcileTrace(), PlanArbiter())


def test_a_spec_must_state_its_contract_items_and_its_rationale():
    with pytest.raises(ValueError):
        _probe_spec(contract_items=())
    with pytest.raises(ValueError):
        _probe_spec(rationale="")


# ---------------------------------------------------------------------------
# the kernel's own mechanics


def test_task_rules_share_one_pass_over_the_handoffs():
    """The interleaving guarantee. Running each task rule to completion over
    all tasks would group the trace by RULE; the monolith grouped it by TASK,
    and the differential's subsequence check would call the difference a lost
    trace."""
    seen: list[str] = []

    def first(ctx, emit, task):
        seen.append(f"first:{task.task_id}")

    def second(ctx, emit, task):
        seen.append(f"second:{task.task_id}")

    table = (
        _probe_spec(name="first", scope=RuleScope.TASK, rule=first,
                    emits=frozenset(), claims=frozenset()),
        _probe_spec(name="second", scope=RuleScope.TASK, rule=second,
                    emits=frozenset(), claims=frozenset()),
    )
    ctx = _context()
    run_rules(ctx, table, reconcile.ReconcileTrace(), PlanArbiter(table))
    ids = [t.task_id for t in ctx.tasks()]
    assert seen == [f"{rule}:{tid}" for tid in ids for rule in ("first", "second")]


def test_a_lifecycle_action_that_names_no_task_is_refused_by_name():
    """CR-06b, from CR-06a's review. The LIFECYCLE channel is grouped and
    sorted BY TASK ID, so a lifecycle action carrying `task_id=None` has no
    position in the plan -- and before this it did not fail at `add`, it
    failed much later inside `sorted(self.lifecycle)` with a `TypeError` about
    NoneType and str, in a function the author never wrote.

    No rule can reach this today (`implementer-dispatch` is the only rule on
    the channel that CR-06b owns, and it always names its task), which is
    exactly why it is worth closing now: the trap is for the rule that does
    not exist yet, and `RuleMatch`'s own `getattr(action, "task_id", None)`
    was already the tell that the two disagreed about whether the field is
    optional. Refused rather than defaulted, because WHERE a task-less
    lifecycle action sorts is a design decision and not this method's to
    invent.
    """
    channels = planning.PlanChannels()
    with pytest.raises(planning.TaskLessLifecycleAction) as exc:
        channels.add(Channel.LIFECYCLE, reconcile.SpecAttention(reason="ratchet"))
    assert "SpecAttention" in str(exc.value)
    # and the same action on any OTHER channel is ordinary
    channels.add(Channel.SPEC, reconcile.SpecAttention(reason="ratchet"))
    assert len(channels.assemble()) == 1


def _oracle_roots() -> list[tuple[str, str]]:
    """Every function the two oracles start a walk from.

    One list, so the collision guard below and the purity oracle cannot drift
    into disagreeing about which walks exist.
    """
    return ([_root_of(spec) for spec in planning.rule_table()]
            + [("planning", "derive"), ("planning", "tasks"),
               ("reconcile", "plan_project")])


def test_no_module_the_oracles_walk_shadows_a_name_they_can_resolve():
    """The call-graph walker both oracles use keys functions by
    `(module, name)` and resolves collisions with `setdefault` -- so two
    same-named functions in one module become ONE node, and whichever `ast`
    reached first wins. A rule's derived `emits`, or its purity verdict, could
    then be computed from the wrong body.

    SCOPED BY WHAT THE WALKER CAN ACTUALLY RESOLVE, in two directions, and
    CR-06b had the first one too narrow.

    * MODULES: every module the walk ENTERS, not only the ones that host
      rules. The walk crosses module boundaries on `from .x import y`, so
      `stages` and `planning` are inside it -- `planning` because
      `PlanContext.derive` and `.tasks` are explicit roots, and `stages`
      because `derive` calls into it. A collision there corrupts an oracle
      verdict exactly as one in a rule module would.
    * NAMES: only names the resolver can reach -- a module-level `def`, or a
      name some root names directly. `_resolve` follows a bare call to a
      module-level function or an imported one; it never resolves a method on
      a value, so `PlanArbiter.available` and `RuleEmitter.available` sharing
      a name is harmless and a blanket ban would be false. Banning what cannot
      be reached is how a guard acquires exceptions and then gets deleted.

    Making the walker qualify names properly is a change to shared test
    infrastructure `tests/test_effects.py` also depends on, and it is not
    CR-06b's; refusing the collision is.
    """
    roots_by_module: dict[str, set[str]] = {}
    for module, name in _oracle_roots():
        roots_by_module.setdefault(module, set()).add(name)

    graph = _graph()
    walked: set[str] = set()
    for module, name in _oracle_roots():
        walked |= {key[0] for key, _node in graph.reachable(module, name)}
    assert walked >= set(roots_by_module), "a declared root is unreachable"

    collisions = []
    for module in sorted(walked):
        tree = ast.parse((SRC / f"{module}.py").read_text(encoding="utf-8"))
        everywhere: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                everywhere[node.name] = everywhere.get(node.name, 0) + 1
        resolvable: dict[str, int] = {
            node.name: everywhere[node.name] for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in roots_by_module.get(module, ()):
            resolvable[name] = everywhere.get(name, 0)
        collisions += [f"{module}.{name} x{count}"
                       for name, count in sorted(resolvable.items()) if count > 1]
    assert not collisions, (
        f"a module the oracles walk defines one resolvable name twice: "
        f"{collisions} -- the call graph would silently walk only one of them")


def test_the_lifecycle_channel_is_grouped_by_task_and_sorted():
    channels = planning.PlanChannels()
    channels.add(Channel.LIFECYCLE, reconcile.Transition(task_id="b", to=TaskState.QUEUED))
    channels.add(Channel.LIFECYCLE, reconcile.CreateTask(task_id="a"))
    channels.add(Channel.SPEC, reconcile.SpecAttention(reason="ratchet"))
    channels.add(Channel.LIFECYCLE, reconcile.RunPostMergeGate(task_id="b"))
    assert [type(a).__name__ for a in channels.assemble()] == [
        "CreateTask", "Transition", "RunPostMergeGate", "SpecAttention"]


def test_the_context_derives_each_shared_fact_exactly_once():
    """A frozen context is the structural half of "computed ONCE and shared":
    if a fact is not a field here, no two rules can be sharing it."""
    ctx = _context()
    assert dataclasses.is_dataclass(ctx)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.carve_in_flight = True
    assert ctx.dispatch_capacity == ctx.implement_concurrency - ctx.active_count
    assert ctx.review_session_reuse == ("session-reuse" in ctx.review_stage_context)
    assert ctx.sorted_task_ids == tuple(sorted(ctx.inp.states))


# ---------------------------------------------------------------------------
# the arbiter's cross-rule consistency step


def _blocked(task_id: str) -> reconcile.Transition:
    return reconcile.Transition(task_id=task_id, to=TaskState.BLOCKED,
                                notes="interrupted-dead-end")


@pytest.mark.parametrize("launch", [
    reconcile.DispatchImplementer(task_id="t1", route_id="r"),
    reconcile.ResumeAttempt(task_id="t1", attempt_id="a1"),
    reconcile.LaunchSelfReview(task_id="t1", source_attempt_id="a1"),
])
def test_a_launch_for_a_task_this_plan_dead_ends_is_dropped(launch):
    """Executing both blocks the task and then wastes an agent session on the
    now-BLOCKED task whose exit receipt is never consumed. The dead-end is
    kept -- it is the inspectable half."""
    plan = PlanArbiter().deconflict([_blocked("t1"), launch])
    assert [type(a).__name__ for a in plan] == ["Transition"]


def test_a_wave_launch_keeps_the_members_that_are_not_being_blocked():
    plan = PlanArbiter().deconflict([
        _blocked("t1"),
        reconcile.LaunchReview(wave_id="w1", task_ids=["t1", "t2"]),
    ])
    assert len(plan) == 2
    assert plan[1].task_ids == ["t2"]
    assert plan[1].wave_id == "w1"


def test_a_wave_launch_whose_every_member_is_blocked_is_dropped():
    plan = PlanArbiter().deconflict([
        _blocked("t1"),
        reconcile.LaunchReview(wave_id="w1", task_ids=["t1"]),
    ])
    assert [type(a).__name__ for a in plan] == ["Transition"]


def test_a_plan_with_no_dead_end_is_returned_untouched():
    actions = [reconcile.DispatchImplementer(task_id="t1", route_id="r"),
               reconcile.Transition(task_id="t2", to=TaskState.QUEUED)]
    assert PlanArbiter().deconflict(actions) is actions


# ---------------------------------------------------------------------------
# structured explanations (parent work item 3)


def test_every_planned_action_is_attributable_to_the_rule_that_decided_it():
    checked = 0
    for _case_id, states in planner_corpus.projections()[:40]:
        for profile in ("neutral", "test-health-due", "carver-cold"):
            plan = reconcile.plan_project(corpus_profiles.build(states, profile))
            kinds = [type(a).__name__ for a in plan]
            matches = plan.trace.matches
            # deconfliction may DROP an action, so the plan is a sub-multiset
            # of what the rules decided -- never a superset.
            for kind in kinds:
                assert kind in [m.action_kind for m in matches]
            assert len(matches) >= len(kinds)
            for match in matches:
                assert match.rule and match.concern and match.contract_items
            checked += 1
    assert checked >= 30


def test_every_breadcrumb_names_the_rule_that_recorded_it():
    for _case_id, states in planner_corpus.projections()[:40]:
        plan = reconcile.plan_project(corpus_profiles.build(states, "neutral"))
        for note in plan.trace.breadcrumbs:
            assert note.rule, f"unattributed breadcrumb {note!r}"


# ---------------------------------------------------------------------------
# the corpus acceptances


def _normalise(action) -> tuple:
    """(class, fields) -- EVERY field, with nothing excused.

    Until CR-06b this dropped `LaunchReview.resume_session`, because it was
    chosen by `max(..., key=started)` over an unordered scan and ties therefore
    followed snapshot iteration order. That was the last field the permutation
    acceptance could not cover; the tie-break in `rules_review.py` closed it,
    so the exclusion is gone rather than left standing as a permanent excuse.
    """
    return (
        type(action).__name__,
        tuple(sorted(
            (f.name, repr(getattr(action, f.name)))
            for f in dataclasses.fields(action)
        )),
    )


def _channels(inp) -> dict:
    table = planning.rule_table()
    channels = run_rules(PlanContext.derive(inp), table,
                         reconcile.ReconcileTrace(), PlanArbiter(table))
    result = {channel: [_normalise(a) for a in actions]
              for channel, actions in channels.other.items()}
    result[Channel.LIFECYCLE] = [
        _normalise(a) for task_id in sorted(channels.lifecycle)
        for a in channels.lifecycle[task_id]
    ]
    return result


def _permuted(inp):
    return dataclasses.replace(
        inp,
        states=dict(reversed(list(inp.states.items()))),
        frontmatters=dict(reversed(list(inp.frontmatters.items()))),
    )


@pytest.mark.parametrize("profile", corpus_profiles.PROFILE_NAMES)
def test_permuting_the_snapshot_key_order_cannot_change_the_plan(profile):
    """The parent's permutation acceptance, over the real corpus -- MET.

    A `ReconcileInput`'s maps are unordered by intent -- the daemon builds
    them by scanning a directory -- so a plan that depends on their iteration
    order depends on the filesystem. Asserted per CHANNEL, which is sharper
    than asserting over the flat plan: it says WHICH decisions are
    order-independent rather than only that the total is.

    EVERY channel is now compared in ORDER. CR-06a had to compare the ATTEMPT
    channel as a multiset, because the attempt ladder walked
    `inp.states.items()` raw where every other rule sorts, and it recorded the
    acceptance as only PARTIALLY met for exactly that reason. CR-06b moved the
    ladder and made it walk `PlanContext.sorted_task_ids`, so the concession is
    gone. Restoring a multiset comparison here would silently un-meet the
    acceptance, which is why the strictness lives in this test rather than in a
    note about it.
    """
    differences = []
    for case_id, states in planner_corpus.projections():
        first = _channels(corpus_profiles.build(states, profile))
        second = _channels(_permuted(corpus_profiles.build(states, profile)))
        for channel in first:
            if first[channel] != second[channel]:
                differences.append(f"{case_id}/{channel}: order or contents differ")
        if len(differences) >= 5:
            break
    assert not differences, "\n".join(differences)


def test_the_attempt_channels_order_is_sorted_task_id_order():
    """RESTATED from CR-06a's `test_the_attempt_channels_order_follows_the_snapshot`.

    That test pinned the CURRENT behaviour -- the attempt channel's order
    followed the snapshot map -- precisely so that repairing it would be a
    visible, reported change rather than a silent one. CR-06b repaired it, so
    the OBSERVABLE moved and the test is restated rather than deleted
    (amendment §5.2): the same projections, the same channel, the opposite
    claim.

    Two halves, because "permutation-stable" alone would pass for a rule that
    emitted nothing: the channel must be in sorted task-id order, and there
    must still be a projection that plans several attempt actions across
    several tasks -- otherwise this asserts sortedness of a list with one
    element in it.
    """
    exercised = 0
    for case_id, states in planner_corpus.projections():
        inp = corpus_profiles.build(states, "neutral")
        first = _channels(inp)[Channel.ATTEMPT]
        second = _channels(_permuted(inp))[Channel.ATTEMPT]
        assert first == second, f"{case_id}: the attempt channel still follows the map"
        task_ids = [
            dict(fields)["task_id"] for _kind, fields in first
        ]
        assert task_ids == sorted(task_ids), f"{case_id}: {task_ids}"
        if len(set(task_ids)) > 1:
            exercised += 1
    assert exercised, (
        "no corpus projection plans attempt actions for two DIFFERENT tasks "
        "-- sortedness is vacuous here, so re-read this before trusting it")


def test_a_poisoned_attempt_with_no_frontmatter_parks_and_retries_next_pass():
    """The one row of P34's resume-safety decision table nothing exercised.

    A resume-poisoned INTERRUPTED attempt on an ACTIVE task, with budget left
    and every transient guard clear, is re-cut as a FRESH dispatch -- but only
    if there is a frontmatter to dispatch FROM. Without one the ladder parks
    and retries next pass, rather than dead-ending a task whose handoff file
    the snapshot simply had not read yet.

    Asserted against its own contrast, because "no action" is what a broken
    rule also produces: the SAME projection WITH a frontmatter must plan the
    fresh dispatch. Found by CR-06b's coverage of the moved ladder -- the
    branch was reachable and unexercised in `reconcile.py` too, so this is a
    gap the move surfaced rather than one it created.
    """
    attempt = Attempt(
        attempt_id="a-poisoned", role=Role.IMPLEMENTER,
        state=AttemptState.INTERRUPTED,
        route=Route(route_id=corpus_profiles.ROUTE_A, cli="fake", model="fake-a"),
        started=corpus_profiles.NOW, session_handle="warm")
    states = {"t-a": TaskStateFile(
        schema_version=1, task_id="t-a", project="corpus",
        state=TaskState.ACTIVE, since=corpus_profiles.NOW, attempts=[attempt])}
    inp = dataclasses.replace(
        corpus_profiles.build(states, "neutral"),
        resume_failures={"a-poisoned": 99})

    def re_cuts(plan):
        return [a.task_id for a in plan
                if isinstance(a, reconcile.DispatchImplementer)]

    assert re_cuts(reconcile.plan_project(inp)) == ["t-a"]
    assert re_cuts(reconcile.plan_project(
        dataclasses.replace(inp, frontmatters={}))) == []


def _self_reviewing(task_id: str) -> TaskStateFile:
    """A SELF_REVIEWING task whose implementer leg finished, warm handle kept."""
    return TaskStateFile(
        schema_version=1, task_id=task_id, project="corpus",
        state=TaskState.SELF_REVIEWING, since=corpus_profiles.NOW,
        attempts=[Attempt(
            attempt_id=f"{task_id}-impl", role=Role.IMPLEMENTER,
            state=AttemptState.EXITED,
            route=Route(route_id=corpus_profiles.ROUTE_A, cli="fake", model="fake-a"),
            started=corpus_profiles.NOW, session_handle="warm")])


def test_two_self_reviewing_tasks_launch_in_sorted_order():
    """The gap the permutation oracle could NOT see, and why it could not.

    `self_review_dispatch` walked `inp.states.items()` raw, so two
    SELF_REVIEWING tasks in one pass produced their two `LaunchSelfReview`
    actions in snapshot iteration order -- and the daemon builds `inp.states`
    by scanning a directory, so the plan depended on the filesystem. Exactly
    the attempt ladder's defect, in a rule THIS package moved.

    `test_permuting_the_snapshot_key_order_cannot_change_the_plan` asserts
    strict order equality for the SELF_REVIEW channel and passed anyway,
    because NO corpus projection contains even one SELF_REVIEWING task: the
    channel's acceptance was vacuous, not met. A green over a corpus says
    nothing about a branch the corpus never enters, which is why this input is
    built rather than found.

    Fixed by CR-06a rather than pinned, because sorting here moved 0 of 877
    corpus projections. The attempt ladder's identical gap was deferred on the
    opposite ground -- sorting it moves real plans -- and CR-06b closed it,
    declaring the divergence in `tests/test_planner_differential.py` rather
    than relying on a zero-delta it does not have.
    """
    states = {"t-b": _self_reviewing("t-b"), "t-a": _self_reviewing("t-a")}
    inp = corpus_profiles.build(states, "neutral")
    forward = [a.task_id for a in reconcile.plan_project(inp)
               if isinstance(a, reconcile.LaunchSelfReview)]
    reverse = [a.task_id for a in reconcile.plan_project(_permuted(inp))
               if isinstance(a, reconcile.LaunchSelfReview)]
    assert forward == ["t-a", "t-b"], forward
    assert forward == reverse


def test_the_warm_review_session_choice_is_deterministic_under_a_tie():
    """RESTATED from CR-06a's `..._is_ambiguous_under_a_tie`.

    Same observable, opposite claim, for the same reason as the attempt
    channel above: CR-06a pinned the ambiguity so that repairing it could not
    be silent, and CR-06b repaired it. `resume_session` was
    `max(prior_sessions, key=lambda a: a.started)`, and `max` returns the
    FIRST maximal element -- so when two EXITED review attempts shared a
    `started` timestamp, which warm session a wave resumed depended on
    `inp.states` iteration order, i.e. on the daemon's directory scan. The
    tie-break is now `(started, attempt_id)`.

    Non-vacuity is the load-bearing half here. A corpus with no tie would pass
    this trivially, so the tie is COUNTED: at least one projection must have
    two `started`-maximal prior review sessions AND plan a review that resumes
    one of them, or the determinism asserted below is about nothing.
    """
    tied_and_launched = 0
    for case_id, states in planner_corpus.projections():
        inp = corpus_profiles.build(states, "neutral")
        first = [a for a in reconcile.plan_project(inp)
                 if isinstance(a, reconcile.LaunchReview)]
        second = [a for a in reconcile.plan_project(_permuted(inp))
                  if isinstance(a, reconcile.LaunchReview)]
        assert ([a.resume_session for a in first]
                == [a.resume_session for a in second]), (
            f"{case_id}: the warm-session choice still follows the map order")
        priors = [
            a for tsf in inp.states.values() for a in tsf.attempts
            if a.role == Role.REVIEW_INDEPENDENT
            and a.state == AttemptState.EXITED and a.session_handle
        ]
        if priors and any(a.resume_session for a in first):
            newest = max(a.started for a in priors)
            if sum(1 for a in priors if a.started == newest) > 1:
                tied_and_launched += 1
    assert tied_and_launched, (
        "no corpus projection both ties two review sessions on `started` and "
        "plans a review that resumes one -- the determinism asserted above is "
        "vacuous, so re-derive the corpus before trusting it")


@pytest.mark.parametrize("profile", corpus_profiles.PROFILE_NAMES)
def test_at_most_one_carve_authority_is_granted_per_pass(profile):
    """Single carve authority over the real corpus: at most one grant, and at
    most one carve-family action, whichever rules wanted it.

    CR-06c adds the converse, which is what makes the count meaningful now
    that all six claimants ship as ordinary rules: a grant that exists is
    either SPENT on the carve action in the plan, or is a RESERVATION carrying
    its reason. There is no third state -- an unspent claim never reaches this
    assertion, because `run_rules`' audit refuses the pass.
    """
    for case_id, states in planner_corpus.projections():
        inp = corpus_profiles.build(states, profile)
        table = planning.rule_table()
        arbiter = PlanArbiter(table)
        channels = run_rules(PlanContext.derive(inp), table,
                             reconcile.ReconcileTrace(), arbiter)
        assert len(arbiter.grants()) <= 1, case_id
        carve_actions = [
            a for a in channels.assemble()
            if type(a).__name__ in planning.EXCLUSIVE_ACTIONS
        ]
        assert len(carve_actions) <= 1, f"{case_id}: {carve_actions}"
        if carve_actions:
            assert arbiter.holder("carve-slot") is not None
        for record in arbiter.ledger():
            if record.intent is GrantIntent.EMIT:
                assert record.spent and carve_actions, f"{case_id}: {record}"
            else:
                assert record.reason and not carve_actions, f"{case_id}: {record}"


@pytest.mark.parametrize("profile", corpus_profiles.PROFILE_NAMES)
def test_at_most_one_carve_authority_over_the_synthetic_states_too(profile):
    """The same acceptance over the states real history never reached.

    `READY_TO_CARVE` is the one that matters: contract item 12 is a whole
    carve claimant the HISTORICAL corpus cannot exercise at all, so a corpus
    green said nothing about the rule most likely to contend with the
    headroom refill -- `ready-to-carve-pair` reaches it, and
    `mixed-unreached-states` makes it contend with a dispatchable task in the
    same pass.
    """
    for case_id, states in planner_synthetic.projections():
        inp = corpus_profiles.build(states, profile)
        table = planning.rule_table()
        arbiter = PlanArbiter(table)
        channels = run_rules(PlanContext.derive(inp), table,
                             reconcile.ReconcileTrace(), arbiter)
        carve_actions = [
            a for a in channels.assemble()
            if type(a).__name__ in planning.EXCLUSIVE_ACTIONS
        ]
        assert len(arbiter.grants()) <= 1, case_id
        assert len(carve_actions) <= 1, f"{case_id}: {carve_actions}"


def test_item_12_is_reached_and_takes_the_slot_from_the_headroom_refill():
    """Non-vacuity for the acceptance above, and the ordering the contract
    argues.

    `ready-to-carve-pair` is a projection whose ready queue is also below
    target, so BOTH item 12 and item 9 want the pass's slot. Item 12 is above
    item 9 in the table because finishing already-started work outranks
    starting new work -- so the plan must carry the TARGETED dispatch (naming
    the lowest READY_TO_CARVE id) and not the untargeted refill. Asserted
    against the holder as well as the action, because "one carve action" would
    also be true if the wrong rule had won.
    """
    states = dict(next(s for n, s in planner_synthetic.projections()
                       if n == "ready-to-carve-pair"))
    inp = corpus_profiles.build(states, "neutral")
    table = planning.rule_table()
    arbiter = PlanArbiter(table)
    channels = run_rules(PlanContext.derive(inp), table,
                         reconcile.ReconcileTrace(), arbiter)
    carves = [a for a in channels.assemble()
              if isinstance(a, reconcile.CarveDispatch)]
    assert [(a.task_id, a.kind) for a in carves] == [("t-a", "headroom")]
    assert arbiter.holder("carve-slot") == "ready-to-carve"


def test_every_carve_dispatch_on_the_lifecycle_channel_names_its_task():
    """The channel trap `PlanChannels.add` raises on, pinned where it is
    actually reachable.

    `CarveDispatch` is emitted by FOUR rules. Three are project-wide and carry
    `task_id=None`, on the CARVE channel; `ready-to-carve` carries the origin
    task id and sits on LIFECYCLE, which is GROUPED AND SORTED BY TASK ID -- so
    a task-less action there raises `TaskLessLifecycleAction`. The channel is
    declared per RULE and the property that makes it safe is per EMISSION, so
    the two cannot be made to coincide by declaration: this asserts it instead,
    over every projection that reaches item 12.
    """
    lifecycle_rules = {
        spec.name for spec in planning.rule_table()
        if spec.channel is Channel.LIFECYCLE and "CarveDispatch" in spec.emits
    }
    assert lifecycle_rules == {"ready-to-carve"}, lifecycle_rules

    reached = 0
    for case_id, states in planner_synthetic.projections():
        for profile in ("neutral", "test-health-due", "gap-audit-due"):
            inp = corpus_profiles.build(states, profile)
            table = planning.rule_table()
            channels = run_rules(PlanContext.derive(inp), table,
                                 reconcile.ReconcileTrace(), PlanArbiter(table))
            for actions in channels.lifecycle.values():
                for action in actions:
                    if isinstance(action, reconcile.CarveDispatch):
                        assert action.task_id, f"{case_id}/{profile}"
                        reached += 1
    assert reached, (
        "no synthetic projection routed a CarveDispatch onto the LIFECYCLE "
        "channel -- this assertion is vacuous, so re-read it before trusting it")


def test_the_ladder_reserves_the_slot_when_a_carver_turn_is_already_in_flight():
    """The deliberate claim-and-plan-nothing, in the SHIPPING table.

    `STARTING` and `COMPACTING` both mean a carver turn is already in flight
    for this generation, so plan §2.4 says every intake/feed/carve decision
    waits -- the pass's carve authority is spent even though nothing is
    planned. That reading is now recorded as a RESERVATION with its reason,
    which is the whole answer to "how do you tell this from a rule that forgot
    to fire": the forgetful rule leaves an unspent EMIT grant and fails the
    pass, and no comment has to be believed.

    The contrast is the load-bearing half: the SAME projection with no carver
    session plans the headroom carve, so the reservation is observably what
    suppressed it rather than some other guard.
    """
    from nyxloom.carver_session import CarverStatus

    states = next(states for _, states in planner_corpus.projections())
    baseline = corpus_profiles.build(states, "neutral")
    assert [a for a in reconcile.plan_project(baseline)
            if isinstance(a, reconcile.CarveDispatch)], (
        "the control projection no longer plans a carve, so the suppression "
        "asserted below would be vacuous")

    for status in (CarverStatus.STARTING, CarverStatus.COMPACTING):
        inp = dataclasses.replace(
            baseline, carver_session=corpus_profiles._carver(status))
        table = planning.rule_table()
        arbiter = PlanArbiter(table)
        channels = run_rules(PlanContext.derive(inp), table,
                             reconcile.ReconcileTrace(), arbiter)
        record, = arbiter.ledger()
        assert record.rule == "carver-session-ladder"
        assert record.intent is GrantIntent.RESERVE
        assert record.reason == f"wait:{status.value.lower()}"
        assert record.spent is False
        assert not [a for a in channels.assemble()
                    if type(a).__name__ in planning.EXCLUSIVE_ACTIONS]
