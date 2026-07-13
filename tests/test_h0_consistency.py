"""Tests for the H0 child-consistency gate (P2, forge_relevance_study §4.3.4 / §7).

The gate checks that an AND-node's children agree on overlaps (the 0-cocycle / global-section
condition) BEFORE the parent is promoted by composing them. A child proven under a hypothesis that
CONTRADICTS a sibling's is unsound to compose even though each child passed LOCALLY.
"""
import json

from agent.orchestrator.h0_consistency import (
    check_children_consistency, extract_signature,
)


def ledger(goal: str, givens: list[str], lemmas: list[str] | None = None) -> str:
    """A structurally-valid ledger that ASSUMES each `givens` claim and (optionally) cites `lemmas`."""
    steps = []
    for i, g in enumerate(givens):
        steps.append({"id": f"g{i}", "claim": g, "justification": "given", "depends_on": []})
    for i, lm in enumerate(lemmas or []):
        steps.append({"id": f"L{i}", "claim": lm, "justification": "lemma", "depends_on": []})
    dep_ids = [s["id"] for s in steps]
    steps.append({"id": "c", "claim": goal, "justification": "conclusion", "depends_on": dep_ids})
    return json.dumps({"problem": "p", "claim": goal, "steps": steps})


# ==================================================================================================
# CONSISTENT compositions PASS (non-vacuously)
# ==================================================================================================

def test_consistent_siblings_pass_non_vacuously():
    # Two children that agree on the shared subject n (both assume `n is even`) -> consistent, and the
    # check actually INSPECTED an overlap (it is not a vacuous pass).
    proofs = {
        "A": ledger("A", ["n is even", "p is prime"]),
        "B": ledger("B", ["n is even", "k = 3"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent
    assert res.reason is None
    assert res.inspected_children == 2
    assert res.overlaps_checked >= 1            # the shared `n is even` predicate was compared


def test_non_overlapping_siblings_pass():
    # Children that constrain DISJOINT subjects (no shared symbol) cannot conflict -> consistent. The
    # proofs parsed and were scanned, so this is NOT vacuous even though no overlap exists.
    proofs = {
        "A": ledger("A", ["x = 0"]),
        "B": ledger("B", ["y = 1"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent
    assert res.reason is None
    assert res.inspected_children == 2


def test_unrelated_predicates_on_same_subject_do_not_conflict():
    # `n is even` and `n is prime` can BOTH hold (2 is even and prime) -> no conflict.
    proofs = {
        "A": ledger("A", ["n is even"]),
        "B": ledger("B", ["n is prime"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent


def test_matching_lemma_signatures_pass():
    proofs = {
        "A": ledger("A", [], lemmas=["g divides h"]),
        "B": ledger("B", [], lemmas=["g divides h"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent
    assert res.overlaps_checked >= 1


# ==================================================================================================
# INCONSISTENT siblings are REJECTED (0-cocycle violation), naming the overlap
# ==================================================================================================

def test_contradictory_predicate_siblings_rejected():
    # One child assumes `n is even`, the sibling `n is odd` -> a 0-cocycle violation on subject n.
    proofs = {
        "A": ledger("A", ["n is even"]),
        "B": ledger("B", ["n is odd"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "conflict"
    assert res.offending_overlap == "n"          # the conflicting overlap is NAMED
    assert res.conflicts[0].kind == "predicate"


def test_negated_predicate_conflict_rejected():
    # `n is even` vs `n is not even` is a direct negation conflict on n.
    proofs = {
        "A": ledger("A", ["n is even"]),
        "B": ledger("B", ["n is not even"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.offending_overlap == "n"


def test_contradictory_bindings_rejected():
    # One child binds x = 0, the sibling x = 1 -> incompatible value definitions on x.
    proofs = {
        "A": ledger("A", ["x = 0"]),
        "B": ledger("B", ["x = 1"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.offending_overlap == "x"
    assert res.conflicts[0].kind == "binding"


def test_binding_vs_predicate_conflict_rejected():
    # x = 0 in one child contradicts `x is positive` in a sibling.
    proofs = {
        "A": ledger("A", ["x = 0"]),
        "B": ledger("B", ["x is positive"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.offending_overlap == "x"


def test_diverging_lemma_signatures_rejected():
    # Both children cite a lemma about the SAME symbols (g, h) asserting MUTUALLY-EXCLUSIVE relations
    # ('g < h' vs 'g > h' — an order-trichotomy contradiction) -> the shared lemma signature does not
    # agree on the overlap. (V4: the conflict is flagged because the RELATIONS themselves conflict on
    # the same subjects, not merely because the canonical statements differ.)
    proofs = {
        "A": ledger("A", [], lemmas=["g < h"]),
        "B": ledger("B", [], lemmas=["g > h"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.conflicts[0].kind == "relation"


def test_conflict_only_needs_one_pair_among_many():
    # Three children, only the (B, C) pair conflicts on n; the gate still rejects.
    proofs = {
        "A": ledger("A", ["x = 0"]),
        "B": ledger("B", ["n is even"]),
        "C": ledger("C", ["n is odd"]),
    }
    res = check_children_consistency(["A", "B", "C"], proofs)
    assert not res.consistent
    assert res.offending_overlap == "n"


# ==================================================================================================
# R1-fix: phrasing-variant predicate contradictions are rejected without treating compatible
# properties (including open+closed for clopen sets) as exclusive.
# ==================================================================================================

def test_predicate_phrasing_variance_even_vs_odd_number_rejected():
    # (a) PHRASING VARIANCE: 'n is even' vs 'n is an odd number'. The odd-number phrasing must fold onto
    # the canonical 'odd' family member so the even/odd contradiction is still caught.
    proofs = {
        "A": ledger("A", ["n is even"]),
        "B": ledger("B", ["n is an odd number"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "conflict"
    assert res.offending_overlap == "n"
    assert res.conflicts[0].kind == "predicate"


def test_prime_vs_composite_siblings_rejected():
    # (b) prime/composite: 'g is prime' vs 'g is composite' are mutually exclusive. Previously 'prime'
    # was a lemma-stopword but 'composite' was not (asymmetry); the family handling is now symmetric.
    proofs = {
        "A": ledger("A", ["g is prime"]),
        "B": ledger("B", ["g is composite"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "conflict"
    assert res.offending_overlap == "g"


def test_open_vs_closed_siblings_are_compatible_for_clopen_sets():
    # Open and closed are NOT mutually exclusive: clopen sets exist (for example, the empty set and
    # the whole space). H0 must not reject a valid composition merely because both properties occur.
    proofs = {
        "A": ledger("A", ["S is open"]),
        "B": ledger("B", ["S is closed"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent
    assert res.reason is None


def test_open_vs_closed_lemma_steps_are_also_clopen_compatible():
    proofs = {
        "A": ledger("A", [], lemmas=["S is open"]),
        "B": ledger("B", [], lemmas=["S is closed"]),
    }
    assert check_children_consistency(["A", "B"], proofs).consistent


def test_symmetrized_families_do_not_introduce_false_positives():
    # CONSERVATISM GUARD: the broadened families must NOT flag genuinely compatible predicates. 'n is
    # even' and 'n is prime' can BOTH hold (2 is even and prime), and 'g is prime' vs an unrelated
    # 'h is composite' (different subjects) cannot conflict. Both pairs must still PASS.
    res_even_prime = check_children_consistency(
        ["A", "B"], {"A": ledger("A", ["n is even"]), "B": ledger("B", ["n is prime"])})
    assert res_even_prime.consistent
    res_disjoint = check_children_consistency(
        ["A", "B"], {"A": ledger("A", ["g is prime"]), "B": ledger("B", ["h is composite"])})
    assert res_disjoint.consistent


# ==================================================================================================
# V4-fix: lemma signatures are keyed by RELATION + subjects. Independent relations on the same
# subjects ('g<h' vs 'g|h') no longer collide into a SPURIOUS lemma_signature 0-cocycle conflict,
# while genuinely incompatible relations ('g<h' vs 'g>h') and true property-family contradictions
# (prime/composite, even/odd) are STILL rejected.
# ==================================================================================================

def test_v4_compatible_relations_on_same_subjects_do_not_conflict():
    # THE V4 false positive: 'g < h' (g less than h) and 'g | h' (g divides h) are COMPATIBLE
    # (g=2, h=4: 2<4 AND 2|4). The R1 stopword broadening stripped '<' and '|' into the SAME subject
    # bucket {g,h} so the differing canonical statements collided as a lemma_signature conflict,
    # REJECTING a valid proof. Keying by RELATION + subjects gives them DISTINCT keys ('<@g|h' vs
    # '|@g|h') and the relations share no exclusive family -> no conflict.
    proofs = {
        "A": ledger("A", [], lemmas=["g < h"]),
        "B": ledger("B", [], lemmas=["g | h"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent, "independent relations '<' and '|' on the same subjects must NOT conflict"
    assert res.reason is None
    assert res.inspected_children == 2          # the proofs were genuinely inspected (not vacuous)


def test_v4_same_relation_phrasing_variants_still_collide_and_agree():
    # The fix must not LOSE the genuine same-relation collision: 'g divides h' and 'g | h' phrase the
    # SAME relation ('|') about the SAME subjects -> they key identically ('|@g|h') and their canonical
    # statements agree -> consistent, and the overlap WAS inspected (non-vacuous).
    proofs = {
        "A": ledger("A", [], lemmas=["g divides h"]),
        "B": ledger("B", [], lemmas=["g | h"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent
    assert res.overlaps_checked >= 1            # the shared '|@g|h' lemma key was actually compared


def test_v4_mutually_exclusive_relations_on_same_subjects_rejected():
    # CONTROL: genuinely mutually-exclusive relations on the same subjects ('g < h' vs 'g >= h') are a
    # real order contradiction -> STILL rejected as a normalized relation conflict on operands {g,h}.
    proofs = {
        "A": ledger("A", [], lemmas=["g < h"]),
        "B": ledger("B", [], lemmas=["g >= h"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "conflict"
    assert res.conflicts[0].kind == "relation"
    assert res.offending_overlap == "g|h"       # the conflicting subject overlap is NAMED


def test_v4_property_family_contradictions_via_lemma_steps_still_rejected():
    # Genuine property contradictions must STILL be rejected even when asserted as LEMMA steps.
    for a_lemma, b_lemma, subj in [
        ("g is prime", "g is composite", "g"),
        ("n is even", "n is an odd number", "n"),
    ]:
        res = check_children_consistency(
            ["A", "B"], {"A": ledger("A", [], lemmas=[a_lemma]),
                         "B": ledger("B", [], lemmas=[b_lemma])})
        assert not res.consistent, f"{a_lemma!r} vs {b_lemma!r} must still be rejected"
        assert res.reason == "conflict"
        assert res.offending_overlap == subj


def test_property_lemma_direct_negation_is_rejected():
    proofs = {
        "A": ledger("A", [], lemmas=["n is even"]),
        "B": ledger("B", [], lemmas=["n is not even"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.conflicts[0].kind == "relation"


def test_unknown_property_lemma_direct_negation_is_also_rejected():
    proofs = {
        "A": ledger("A", [], lemmas=["S is connected"]),
        "B": ledger("B", [], lemmas=["S is not connected"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.conflicts[0].kind == "relation"


def test_negated_property_lemma_does_not_false_conflict_with_compatible_member():
    # On integers, odd implies not even. Dropping the negation from a lemma relation would falsely
    # turn this compatible pair into the exclusive positive pair even vs odd.
    proofs = {
        "A": ledger("A", [], lemmas=["n is not even"]),
        "B": ledger("B", [], lemmas=["n is odd"]),
    }
    assert check_children_consistency(["A", "B"], proofs).consistent


def test_property_lemma_phrasing_variants_normalize_to_the_same_signature():
    proofs = {
        "A": ledger("A", [], lemmas=["n is even"]),
        "B": ledger("B", [], lemmas=["n is an even number"]),
    }
    assert check_children_consistency(["A", "B"], proofs).consistent


def test_strict_and_weak_order_constraints_are_compatible():
    proofs = {
        "A": ledger("A", [], lemmas=["g < h"]),
        "B": ledger("B", [], lemmas=["g <= h"]),
    }
    assert check_children_consistency(["A", "B"], proofs).consistent


def test_reversed_equivalent_order_constraints_are_compatible():
    proofs = {
        "A": ledger("A", [], lemmas=["g < h"]),
        "B": ledger("B", [], lemmas=["h > g"]),
    }
    assert check_children_consistency(["A", "B"], proofs).consistent


def test_atomic_assumption_order_contradiction_is_rejected():
    # Regression: assumption extraction previously ignored symbolic inequalities entirely, so these
    # contradictory premises produced a false H0 pass.
    proofs = {
        "A": ledger("A", ["x > 0"]),
        "B": ledger("B", ["x <= 0"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "conflict"
    assert res.offending_overlap == "0|x"
    assert res.conflicts[0].kind == "relation"


def test_global_order_intersection_rejects_pairwise_compatible_triple():
    # Each pair has a model, but all three together do not: <= and >= force equality, contradicting !=.
    proofs = {
        "A": ledger("A", ["x <= y"]),
        "B": ledger("B", ["x >= y"]),
        "C": ledger("C", ["x != y"]),
    }
    res = check_children_consistency(["A", "B", "C"], proofs)
    assert not res.consistent
    assert res.offending_overlap == "x|y"
    assert res.conflicts[0].kind == "relation"


# ==================================================================================================
# ANTI-VACUITY: an empty / degenerate check must NOT vacuously pass
# ==================================================================================================

def test_no_children_is_vacuous_not_a_pass():
    res = check_children_consistency([], {})
    assert not res.consistent
    assert res.reason == "vacuous_check"


def test_all_unparseable_proofs_is_vacuous():
    proofs = {"A": "not a ledger", "B": None}
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "vacuous_check"


def test_one_uninspectable_child_fails_closed_instead_of_being_ignored():
    proofs = {"A": ledger("A", ["x > 0"]), "B": "not a ledger"}
    res = check_children_consistency(["A", "B"], proofs)
    assert not res.consistent
    assert res.reason == "incomplete_check"
    assert res.inspected_children == 1


def test_parsed_but_no_overlap_is_a_real_pass_not_vacuous():
    # The proofs parsed and had steps to scan, but declare no overlapping constraints. This is a
    # genuine PASS (inspected something, found nothing to conflict), NOT a vacuous fail.
    proofs = {
        "A": ledger("A", ["x = 0"]),
        "B": ledger("B", ["y = 0"]),
    }
    res = check_children_consistency(["A", "B"], proofs)
    assert res.consistent
    assert res.reason is None
    assert res.inspected_children == 2          # both were genuinely inspected


# ==================================================================================================
# signature extraction unit checks
# ==================================================================================================

def test_extract_signature_picks_up_predicate_and_binding():
    sig = extract_signature("A", ledger("A", ["n is even", "x = 5"]))
    assert sig.parsed
    assert sig.predicates["n"] == "even"
    assert sig.bindings["x"] == "5"


def test_extract_signature_counts_atomic_comparison_as_content():
    sig = extract_signature("A", ledger("A", ["x > 0"]))
    assert sig.parsed and sig.has_content and sig.inspectable
    assert sig.lemma_relations["0|x"] == {"<"}


def test_extract_signature_unparsed_has_no_content():
    sig = extract_signature("A", "garbage")
    assert not sig.parsed
    assert not sig.has_content
    assert not sig.inspectable
