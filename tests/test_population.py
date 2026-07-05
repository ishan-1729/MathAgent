"""Tests for the population/Elo search and its DAG integration (offline, deterministic)."""
import json

from agent.orchestrator.population import (
    Candidate, EloPopulation, KeyComparator, ScriptedComparator, fit_bradley_terry, DEFAULT_RATING,
)
from agent.orchestrator.dag_driver import DagDriver, ReasonCode, ReviewVerdict, ScriptedReviewer
from agent.orchestrator.dag import goal_hash
from agent.orchestrator.state import Budget, NodeState
from agent.orchestrator.trace import RunTrace
from agent.gates.toolkit import load_toolkit

TOOLKIT = load_toolkit()
OK_REVIEW = ReviewVerdict(useful=True, elementary=True)


# ---- Elo mechanics ----

def test_record_updates_ratings():
    pop = EloPopulation()
    a = pop.add(Candidate("a", "A"))
    b = pop.add(Candidate("b", "B"))
    pop.record(a, b, 1.0)  # a wins
    assert a.rating > DEFAULT_RATING > b.rating
    assert a.wins == 1.0 and b.wins == 0.0
    assert a.games == 1 and b.games == 1
    assert pop._win_matrix[("a", "b")] == 1


def test_tournament_ranks_by_strength():
    pop = EloPopulation()
    for i, strength in enumerate([3.0, 1.0, 2.0]):
        pop.add(Candidate(f"c{i}", "x", meta={"s": strength}))
    cmp = KeyComparator(key=lambda c: c.meta["s"])
    pop.tournament(cmp, rounds=2)
    order = [c.id for c in pop.ranking()]
    assert order == ["c0", "c2", "c1"]  # strengths 3 > 2 > 1


def test_tournament_respects_budget():
    pop = EloPopulation()
    for i in range(5):
        pop.add(Candidate(f"c{i}", "x", meta={"s": float(i)}))
    cmp = KeyComparator(key=lambda c: c.meta["s"])
    allowed = {"n": 3}

    def budget_ok():
        if allowed["n"] > 0:
            allowed["n"] -= 1
            return True
        return False

    made = pop.tournament(cmp, rounds=1, budget_ok=budget_ok)
    assert made == 3  # stopped at the budget, not all 10 pairs


def test_select_puct_explores_least_visited():
    pop = EloPopulation()
    a = pop.add(Candidate("a", "A"))
    b = pop.add(Candidate("b", "B"))
    a.visits = 5
    b.visits = 0  # equal ratings; b is under-explored
    chosen = pop.select_puct(c_explore=1.4)
    assert chosen.id == "b"
    assert b.visits == 1  # selection records a visit


def test_scripted_comparator_sequence():
    cmp = ScriptedComparator([1, -1, 0])
    a, b = Candidate("a", ""), Candidate("b", "")
    assert [cmp.compare(a, b) for _ in range(3)] == [1, -1, 0]


# ---- (R-ROBUST) a raising comparator is a TIE; the tournament never crashes -------------------

class RaisingComparator:
    """A comparator that ALWAYS raises (mirrors a live Codex/Claude CLI subprocess failure)."""
    def compare(self, a, b):
        raise RuntimeError("comparator subprocess timed out")


def test_raising_comparator_degrades_to_tie_not_crash():
    # A raising comparator must NOT abort the tournament: each comparison degrades to a TIE so the
    # Elo machinery records a draw and the loop completes all pairs (3 pairs over 3 candidates).
    pop = EloPopulation()
    for i in range(3):
        pop.add(Candidate(f"c{i}", "x"))
    trace = RunTrace("t")
    made = pop.tournament(RaisingComparator(), rounds=1, trace=trace)
    assert made == 3                                  # every pair compared (none aborted the run)
    # Every comparison was a tie -> equal games, no wins recorded, ratings unchanged from default.
    assert all(c.games == 2 and c.wins == 1.0 for c in pop.candidates)
    assert all(c.rating == DEFAULT_RATING for c in pop.candidates)
    assert len(trace.by_kind("comparator_error")) == 3  # each raise surfaced on the trace, not swallowed


def test_population_path_completes_when_comparator_raises():
    # End-to-end through the DagDriver population path: a RAISING comparator must let run() RETURN
    # (the tournament degrades to ties) instead of crashing DagDriver.run(). The good candidate still
    # proves because the deterministic hard pre-gate + best-first expansion do not depend on the
    # comparator producing a real signal.
    prover, queues, _cmp = _common()
    driver = DagDriver(prover, decomposer=QueueDecomposer(queues),
                       reviewer=ScriptedReviewer([OK_REVIEW]), toolkit=TOOLKIT, budget=Budget(),
                       trace=RunTrace("t"), comparator=RaisingComparator(),
                       population_k=2, max_decomp_attempts=2)
    res = driver.run("G")  # MUST NOT raise
    assert res.proven                                 # population path completed, did not crash
    assert res.trace.by_kind("population")
    assert res.trace.by_kind("comparator_error")      # the raised comparison was traced, not swallowed


def test_bradley_terry_orders_transitive_wins():
    ids = ["A", "B", "C"]
    wm = {("A", "B"): 3, ("B", "C"): 3, ("A", "C"): 3}
    p = fit_bradley_terry(ids, wm)
    assert p["A"] > p["B"] > p["C"]


# ---- DAG integration: Elo picks the better decomposition first ----

def _valid(goal):
    return json.dumps({"problem": "p", "claim": goal, "steps": [
        {"id": "s1", "claim": "setup", "justification": "given", "depends_on": []},
        {"id": "s2", "claim": goal, "justification": "conclusion", "depends_on": ["s1"]}]})


def _sketch(goal, children):
    steps = [{"id": f"L{i}", "claim": c, "justification": "lemma", "depends_on": []}
             for i, c in enumerate(children)]
    steps.append({"id": "c", "claim": goal, "justification": "conclusion",
                  "depends_on": [f"L{i}" for i in range(len(children))]})
    return json.dumps({"problem": "p", "claim": goal, "steps": steps})


_BAD = json.dumps({"problem": "p", "claim": "?", "steps": [
    {"id": "s1", "claim": "x", "justification": "class_field_theory", "depends_on": []},
    {"id": "s2", "claim": "?", "justification": "conclusion", "depends_on": ["s1"]}]})


class DictProver:
    def __init__(self, mapping):
        self.mapping = mapping

    def prove(self, goal, feedback=None):
        return self.mapping.get(goal, _BAD)


class QueueDecomposer:
    """Pops successive plans for a goal; ('', []) when the queue is empty/unknown goal."""
    def __init__(self, queues):
        self.queues = {g: list(v) for g, v in queues.items()}

    def decompose(self, goal, feedback=None):
        q = self.queues.get(goal)
        if q:
            return q.pop(0)
        return ("", [])


def _common():
    prover = DictProver({"A": _valid("A")})  # only child "A" proves directly
    queues = {"G": [(_sketch("G", ["DEADEND"]), ["DEADEND"]),   # bad candidate first
                    (_sketch("G", ["A"]), ["A"])]}              # good candidate second
    # Prefer candidates whose child is "A".
    cmp = KeyComparator(key=lambda c: 1.0 if "A" in c.children else 0.0)
    return prover, queues, cmp


def test_population_picks_good_candidate_first():
    prover, queues, cmp = _common()
    driver = DagDriver(prover, decomposer=QueueDecomposer(queues), reviewer=ScriptedReviewer([OK_REVIEW]),
                       toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("t"),
                       comparator=cmp, population_k=2, max_decomp_attempts=1)
    res = driver.run("G")
    assert res.proven  # Elo ranked the "A" candidate first; tried under a 1-attempt cap
    assert res.trace.by_kind("population")


def test_population_child_budget_starved_makes_parent_exhausted():
    # FIX 1 (memo-poisoning): the population arm's committed child fails ONLY because the LLM-call
    # budget ran out (budget_starved), a LIMIT-induced/retryable failure — NOT a genuine logical gap.
    # The parent must end retryable EXHAUSTED (reason budget_starved), NOT terminal FAILED_GAP; the
    # latter would poison the split-keyed memo so a budget-refilled retry of the SAME goal could never
    # prove it. Before the fix, _prove_via_population returned a bare bool and _decompose discarded the
    # child reason, leaving last_reason at its gap_found init -> FAILED_GAP.
    #
    # Exact call accounting with max_llm_calls=3 (ralph_episodes=1, population_k=1, 1 candidate,
    # max_decomp_attempts=1): (1) G direct RalphLoop episode; (2) population candidate generation;
    # (3) the _try_decomposition review; then proving child "C" finds the budget spent -> C is
    # mark_exhausted(budget_starved), which propagates up through the population arm.
    prover = DictProver({})                         # nothing proves directly (G fails -> decompose)
    queues = {"G": [(_sketch("G", ["C"]), ["C"])]}  # one committed candidate with child "C"
    driver = DagDriver(prover, decomposer=QueueDecomposer(queues),
                       reviewer=ScriptedReviewer([OK_REVIEW]), toolkit=TOOLKIT,
                       budget=Budget(max_llm_calls=3), trace=RunTrace("t"),
                       comparator=KeyComparator(key=lambda c: 1.0),
                       population_k=1, max_decomp_attempts=1, ralph_episodes=1)
    res = driver.run("G")
    assert not res.proven
    node = driver.dag.get_or_create("G")
    assert node.state is NodeState.EXHAUSTED            # retryable, NOT terminal FAILED_GAP
    assert node.state is not NodeState.FAILED_GAP
    assert node.reason == ReasonCode.budget_starved.value
    # The committed child that starved is itself EXHAUSTED (the limit-induced source of the parent's reason).
    assert driver.dag.get_or_create("C").state is NodeState.EXHAUSTED


def test_without_population_first_candidate_loses():
    prover, queues, cmp = _common()
    # Same decomposer order, but no population: DFS tries the DEADEND candidate first and (with a
    # single attempt) fails.
    driver = DagDriver(prover, decomposer=QueueDecomposer(queues), reviewer=ScriptedReviewer([OK_REVIEW]),
                       toolkit=TOOLKIT, budget=Budget(), trace=RunTrace("t"),
                       max_decomp_attempts=1)
    res = driver.run("G")
    assert not res.proven


# ---- (P3 / #5) HARD-FILTER pre-gate: a gate-failing candidate is NEVER ranked by Elo -----------

def test_hard_filter_rejects_candidate_before_ranking():
    # A candidate failing the hard pre-gate is never admitted, so the Elo machinery never sees it.
    rejected = []
    pop = EloPopulation(hard_filter=lambda c: c.id != "bad")
    a = pop.add(Candidate("good", "G"))
    b = pop.add(Candidate("bad", "B"))   # rejected by the filter
    assert a is not None and b is None
    assert pop.rejected_by_filter == 1
    assert [c.id for c in pop.candidates] == ["good"]


def test_hard_filtered_candidate_never_ranked_even_if_judges_love_it():
    # The judge ADORES the gameable candidate, but the hard filter bars it -> it is never ranked or
    # selected. The Elo/BT/PUCT machinery itself is unchanged; it just never sees the barred candidate.
    pop = EloPopulation(hard_filter=lambda c: c.id != "gameable")
    pop.add(Candidate("honest", "H", meta={"s": 1.0}))
    pop.add(Candidate("gameable", "X", meta={"s": 100.0}))  # high judge score, but barred
    # The comparator would rank "gameable" first if it were present.
    cmp = KeyComparator(key=lambda c: c.meta["s"])
    pop.tournament(cmp, rounds=2)
    pop.set_ratings_from_bradley_terry()
    ids = [c.id for c in pop.ranking()]
    assert "gameable" not in ids                 # never ranked
    assert pop.select_puct().id == "honest"      # never selectable
    assert ids == ["honest"]


def test_hard_filter_fails_closed_on_filter_exception():
    # A hard filter that ERRORS on a candidate rejects it (never admits an un-vouched candidate).
    def boom(c):
        raise RuntimeError("filter blew up")
    pop = EloPopulation(hard_filter=boom)
    assert pop.add(Candidate("c", "x")) is None
    assert pop.candidates == []


def test_no_filter_is_back_compat_unconditional_add():
    # With no hard_filter the population admits everything (the original behaviour).
    pop = EloPopulation()
    for i in range(3):
        assert pop.add(Candidate(f"c{i}", "x")) is not None
    assert len(pop.candidates) == 3 and pop.rejected_by_filter == 0
