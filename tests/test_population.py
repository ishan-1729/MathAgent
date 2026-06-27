"""Tests for the population/Elo search and its DAG integration (offline, deterministic)."""
import json

from agent.orchestrator.population import (
    Candidate, EloPopulation, KeyComparator, ScriptedComparator, fit_bradley_terry, DEFAULT_RATING,
)
from agent.orchestrator.dag_driver import DagDriver, ReviewVerdict, ScriptedReviewer
from agent.orchestrator.state import Budget
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
