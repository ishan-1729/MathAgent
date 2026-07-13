"""H0 child-consistency gate for AND-node composition (P2, §7 / forge_relevance_study §4.3.4).

Before an AND-node is promoted to PROVEN by composing its children, the children's local proofs must
**agree on their overlaps**. The committed children of an AND-node are *precedence*-independent (the
acyclicity guard ensures it) — but **precedence-independent is NOT assumption-independent**: two
children may each be valid *locally* yet rest on *contradictory* hypotheses, variable bindings, or
different versions of a shared lemma. Composing them then proves nothing globally: there is no single
consistent world in which all the children hold at once.

The category-theory shadow (optional label): "agreement on overlaps" is the **0-cocycle condition**.
Operationally this module is a mandatory **signature-compatibility guard**, not a complete logical
decision procedure: a pass means the extracted signature language found no conflict, never that
arbitrary prose hypotheses have been formally proved jointly satisfiable. A detected violation blocks
promotion and is marked FAILED_GAP (a logical inconsistency, never an elementarity failure). Terminal
Lean remains the authority for formal certification.

What we extract from each child's proven ledger as its *signature*:
  * **assumptions / hypotheses** — the claims of `given`/`assumption` steps (the world the child rests
    on), and any predicate-style binding (`n is even`, `p is prime`, `x > 0`);
  * **bindings** — value definitions of a symbol (`let x = 0`, `n = 1`);
  * **lemma signatures** — the claims of `lemma` steps, keyed so the SAME lemma referenced by two
    siblings must state the SAME thing.

A conflict on an *overlap* (the same subject/symbol/lemma key constrained two incompatible ways by two
different children) is the violation. We name the conflicting overlap so the give-up is classified, not
silent.

ANTI-VACUITY (the Forge "running 0 tests fails loud" trap): a consistency check must inspect EVERY
child proof. No children yields `vacuous_check`; any missing, unparseable, or stepless child yields
`incomplete_check`. Neither can silently pass. A genuinely empty-but-inspectable signature set
(children that parsed and were inspected, finding nothing to conflict) DOES pass — vacuity is
"inspected nothing", not "found nothing".

Everything here is deterministic and prover-independent. It never execs/evals/imports model output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from agent.gates.ledger import Ledger, parse_ledger, LedgerError
from agent.orchestrator.dag import canonical_form, goal_hash


# Justifications that introduce a hypothesis / assumption the rest of the proof rests on.
_ASSUMPTION_JUSTIFICATIONS = frozenset({"given", "assumption", "hypothesis", "assume"})


@dataclass(frozen=True)
class Conflict:
    """One concrete 0-cocycle violation: two children disagree on a shared overlap."""

    kind: str                 # "binding" | "predicate" | "relation" | "lemma_signature"
    subject: str              # the overlapping symbol / lemma key the children disagree on
    left_child: str           # a child goal (the first asserting side)
    right_child: str          # the conflicting sibling
    left_value: str
    right_value: str

    def __str__(self) -> str:
        return (f"{self.kind} conflict on {self.subject!r}: child {self.left_child!r} asserts "
                f"{self.left_value!r} but sibling {self.right_child!r} asserts {self.right_value!r}")


@dataclass
class ChildSignature:
    """The compatibility-relevant facts a single child's proof commits to."""

    goal: str
    parsed: bool = False                                    # did the proof parse at all?
    step_count: int = 0                                     # # of steps actually scanned (inspection footprint)
    bindings: dict[str, str] = field(default_factory=dict)  # symbol -> canonical value ("x" -> "0")
    predicates: dict[str, str] = field(default_factory=dict)  # subject -> canonical predicate ("n" -> "even")
    lemma_sigs: dict[str, str] = field(default_factory=dict)  # (relation@subjects) -> canonical statement
    # subjects-bucket -> {normalized relation tokens asserted about that operand/subject set}. This
    # includes comparison assumptions as well as lemma steps, so `x > 0` vs `x <= 0` cannot evade H0.
    # Ordered comparisons are normalized to a stable operand orientation (`g<h` == `h>g`).
    lemma_relations: dict[str, set[str]] = field(default_factory=dict)

    @property
    def has_content(self) -> bool:
        return bool(self.bindings or self.predicates or self.lemma_sigs or self.lemma_relations)

    @property
    def inspectable(self) -> bool:
        """The signature was genuinely INSPECTED iff the proof parsed AND had steps to scan. A
        parsed-but-stepless or unparseable proof contributes nothing inspectable (anti-vacuity)."""
        return self.parsed and self.step_count > 0


@dataclass
class H0Result:
    consistent: bool
    conflicts: list[Conflict] = field(default_factory=list)
    reason: Optional[str] = None          # None or "vacuous_check" / "incomplete_check" / "conflict"
    inspected_children: int = 0           # how many child signatures were actually inspected
    overlaps_checked: int = 0             # how many shared subjects/lemmas were compared (anti-vacuity)

    @property
    def offending_overlap(self) -> Optional[str]:
        return self.conflicts[0].subject if self.conflicts else None

    def summary(self) -> str:
        if self.consistent:
            return (f"consistent ({self.inspected_children} children, "
                    f"{self.overlaps_checked} overlaps checked)")
        if self.reason == "vacuous_check":
            return "VACUOUS: no child signature could be inspected (fails loud, not a pass)"
        if self.reason == "incomplete_check":
            return ("INCOMPLETE: one or more child signatures could not be inspected "
                    "(fails closed, not a pass)")
        return "H0 VIOLATION: " + "; ".join(str(c) for c in self.conflicts)


# --------------------------------------------------------------------------------------------------
# Signature extraction from a child's proven ledger.
# --------------------------------------------------------------------------------------------------
# "let x = 0", "x := 1", "define n = 2", "n = 3" (a value binding of a single symbol).
_BINDING_RE = re.compile(
    r"^(?:let|define|set|put)?\s*([A-Za-z][A-Za-z0-9_]*)\s*(?::?=)\s*(.+?)\s*$", re.IGNORECASE)
# "n is even", "p is prime", "x is positive", "n is not zero" (a predicate over a single subject).
_PREDICATE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*)\s+is\s+(not\s+)?(?:an?\s+|the\s+)?([A-Za-z][A-Za-z0-9_\- ]*?)\s*$",
    re.IGNORECASE)

# Mutually-exclusive predicate families: asserting two DIFFERENT members of a family about the SAME
# subject (e.g. n even vs n odd) is a contradiction. Negation ("not even") is folded into the family.
# Only pairs that cannot simultaneously hold belong here. In particular, open/closed is intentionally
# absent: clopen sets exist, so treating those properties as exclusive rejects valid compositions.
_EXCLUSIVE_FAMILIES = [
    {"even", "odd"},
    {"prime", "composite"},
    {"positive", "negative", "zero"},
    {"rational", "irrational"},
    {"finite", "infinite"},
]

# All canonical property-family members (the union). A token folding onto one of these names a RELATION
# (a property), not a SUBJECT, so it is peeled out of a lemma's subject bucket (V4).
_ALL_FAMILY_MEMBERS = frozenset().union(*_EXCLUSIVE_FAMILIES)

# Surface phrasings of a family member -> its canonical member, so predicate-phrasing variance ("n is
# an odd number", "n is an even integer") folds onto the same family member as the bare form ("n is
# odd"). Each member's own bare word maps to itself; common qualifier-suffixed phrasings are folded.
# SYMMETRIC across the family (every member has the same phrasings) so no member is privileged.
_PRED_SYNONYMS = {
    "even number": "even", "even integer": "even",
    "odd number": "odd", "odd integer": "odd",
    "prime number": "prime", "composite number": "composite",
    "open set": "open", "closed set": "closed",
    "positive number": "positive", "negative number": "negative",
    "rational number": "rational", "irrational number": "irrational",
}


def _canon_member(base: str) -> str:
    """Fold a predicate base onto its canonical family member when it is a known phrasing variant
    ('odd number' -> 'odd'); otherwise return it unchanged. Phrasing-variance must not make two
    genuinely-contradictory predicates miss each other's family."""
    return _PRED_SYNONYMS.get(base, base)


def _norm_pred(value: str, negated: bool) -> str:
    """Canonicalize a predicate value; a negated predicate becomes `not:<value>`. Known phrasing
    variants of a family member are folded onto the canonical member ('odd number' -> 'odd') so the
    same predicate phrased two ways compares equal AND shares a family."""
    v = re.sub(r"\s+", " ", value).strip().lower()
    v = _canon_member(v)
    return f"not:{v}" if negated else v


def _predicate_signature(claim: str) -> Optional[tuple[str, str, str]]:
    """Normalize a single-subject predicate, including negation and phrasing variants.

    For example, ``n is not even`` becomes ``("n", "not:even", "n:not:even")`` and
    ``n is an even number`` becomes ``("n", "even", "n:even")``. Keeping negation in the relation
    token is essential: ``not even`` is compatible with ``odd`` but conflicts with ``even``.
    """
    match = _PREDICATE_RE.fullmatch(claim.strip())
    if match is None:
        return None
    subject = match.group(1).lower()
    relation = _norm_pred(match.group(3), bool(match.group(2)))
    return subject, relation, f"{subject}:{relation}"


def _family_of(pred: str) -> Optional[frozenset[str]]:
    base = pred[4:] if pred.startswith("not:") else pred
    base = _canon_member(base)
    for fam in _EXCLUSIVE_FAMILIES:
        if base in fam:
            return frozenset(fam)
    return None


def _predicates_conflict(a: str, b: str) -> bool:
    """Do two predicate assertions about the SAME subject contradict?

    - `even` vs `odd` (different members of an exclusive family) -> conflict.
    - `even` vs `not:even` (a member and its direct negation) -> conflict.
    - `even` vs `prime` (unrelated families / no shared family) -> NOT a conflict (both can hold).
    """
    if a == b:
        return False
    fam_a, fam_b = _family_of(a), _family_of(b)
    # Direct negation of the same base predicate.
    base_a = a[4:] if a.startswith("not:") else a
    base_b = b[4:] if b.startswith("not:") else b
    if base_a == base_b and a != b:
        return True                              # "even" vs "not:even"
    if fam_a is not None and fam_a == fam_b:
        # Two members of the SAME exclusive family. Two *positive* distinct members conflict
        # (even vs odd); a positive member vs the negation of a DIFFERENT member does not.
        a_neg, b_neg = a.startswith("not:"), b.startswith("not:")
        if not a_neg and not b_neg:
            return True
    return False


def extract_signature(goal: str, proof: Optional[str]) -> ChildSignature:
    """Extract the compatibility-relevant signature from a child's proof ledger (text/JSON/sketch).

    A proof that does not parse yields an *un-parsed* signature (`parsed=False`, no content). The
    fail-closed logic in `check_children_consistency` rejects any composition containing one."""
    sig = ChildSignature(goal=goal)
    if not proof:
        return sig
    try:
        ledger: Ledger = parse_ledger(proof)
    except LedgerError:
        return sig
    sig.parsed = True
    sig.step_count = len(ledger.steps)
    for step in ledger.steps:
        claim = step.claim.strip()
        if not claim:
            continue
        if step.justification == "lemma":
            # Key a lemma by RELATION + subjects (V4): two siblings citing the SAME relation about the
            # SAME subjects collide on key (and their canonical statements must agree), while two
            # INDEPENDENT relations on the same subjects ('g<h' vs 'g|h') get DISTINCT keys and do NOT
            # spuriously collide. A genuine cross-relation contradiction ('g<h' vs 'g>h') is caught
            # separately via `lemma_relations` (mutually-exclusive relation families), not by key.
            subjects = _lemma_subjects(claim)
            relation = _lemma_relation(claim, subjects)
            comparison = _comparison_signature(claim)
            predicate = _predicate_signature(claim)
            if comparison is not None:
                normalized_claim = comparison[2]
            elif predicate is not None:
                normalized_claim = predicate[2]
            else:
                normalized_claim = canonical_form(claim)
            sig.lemma_sigs[f"{relation}@{subjects}"] = normalized_claim
            sig.lemma_relations.setdefault(subjects, set()).add(relation)
            continue
        if step.justification in _ASSUMPTION_JUSTIFICATIONS:
            _ingest_assumption(sig, claim)
    return sig


# Connective / quantifier words that are neither SUBJECT nor RELATION — pure glue. Stripped when
# isolating a lemma's subject tokens so two siblings citing the same lemma about the same subjects line
# up. NOTE (V4): relation words ('divides', 'less', 'than', 'equals', '<', '|', ...) and predicate
# family words ('prime', 'even', ...) are NOT in this set — they name the RELATION, which is now part
# of the lemma key (and the cross-relation conflict check), so they must NOT be folded into subjects.
_LEMMA_CONNECTIVES = frozenset({
    "is", "are", "be", "a", "an", "the", "of", "to", "and", "or", "not", "for", "all", "any",
    "there", "exists", "such", "that", "with", "in", "on", "by", "then", "if", "iff",
    "implies", "holds", "true", "false",
})

# Words that NAME a relation (so they are not subjects). Used to peel the relation off a lemma claim.
# Includes the qualifier NOUNS of property-family phrasings ('odd number', 'even integer', 'open set')
# so the bare token 'number'/'integer'/'set' does not leak into the subject bucket and split a
# family-word lemma off from its bare-form sibling ('n is even' vs 'n is an odd number'). (V4)
_RELATION_WORDS = frozenset({
    "divides", "equals", "equal", "less", "greater", "than", "coprime", "mod", "modulo",
    "number", "integer", "set",
})

# Surface relation operators (post-canonicalization) and family/property words that name a relation.
# The canonical form already folds 'divides'->'|', 'less than'->'<', 'equals'->'=' etc.
_RELATION_OPERATORS = ("<=", ">=", "!=", "<->", "->", "<", ">", "=", "|")

# A comparison token denotes the possible outcomes of ``left ? right`` after normalizing both claims
# to the SAME stable operand orientation. Compatibility is set intersection, not "different operator
# means conflict": `<` is compatible with `<=`, and `<=` with `>=` at equality. This also makes the
# contradiction `<` vs `>=` exact rather than heuristic.
_ORDER_OUTCOMES = {
    "<": frozenset({-1}),
    "<=": frozenset({-1, 0}),
    "=": frozenset({0}),
    "!=": frozenset({-1, 1}),
    ">=": frozenset({0, 1}),
    ">": frozenset({1}),
}
_INVERT_ORDER = {"<": ">", "<=": ">=", "=": "=", "!=": "!=", ">=": "<=", ">": "<"}
_ATOMIC_TERM = r"(?:[A-Za-z][A-Za-z0-9_]*|[+-]?\d+(?:\.\d+)?)"
_COMPARISON_RE = re.compile(
    rf"^(?P<left>{_ATOMIC_TERM})(?P<op><=|>=|!=|=|<|>)(?P<right>{_ATOMIC_TERM})$")


def _comparison_signature(claim: str) -> Optional[tuple[str, str, str]]:
    """Return ``(operand_key, normalized_relation, normalized_claim)`` for an atomic comparison.

    The canonical operand order is lexical and the relation is inverted when operands are swapped.
    Thus ``g < h`` and ``h > g`` both become ``("g|h", "<", "g<h")``. Restricting this parser to
    atomic identifiers/numbers avoids guessing about algebraically complex expressions; an unsupported
    form conservatively misses a conflict instead of manufacturing a false one.
    """
    match = _COMPARISON_RE.fullmatch(canonical_form(claim))
    if match is None:
        return None
    left, op, right = match.group("left"), match.group("op"), match.group("right")
    if right < left:
        left, right = right, left
        op = _INVERT_ORDER[op]
    key = f"{left}|{right}"
    return key, op, f"{left}{op}{right}"


def _lemma_subjects(claim: str) -> str:
    """The sorted SUBJECT identifier tokens of a lemma (the symbols it is about), with connective AND
    relation/family words stripped. Two siblings citing a lemma about the same subjects share this
    bucket regardless of the relation, so a cross-relation contradiction on those subjects is checkable."""
    comparison = _comparison_signature(claim)
    if comparison is not None:
        return comparison[0]
    predicate = _predicate_signature(claim)
    if predicate is not None:
        return predicate[0]
    toks = re.findall(r"[A-Za-z][A-Za-z0-9_]*", claim.lower())
    subjects = sorted({t for t in toks
                       if t not in _LEMMA_CONNECTIVES
                       and t not in _RELATION_WORDS
                       and _canon_member(t) not in _ALL_FAMILY_MEMBERS})
    return "|".join(subjects) if subjects else goal_hash(claim)


def _lemma_relation(claim: str, subjects: str) -> str:
    """The canonical RELATION a lemma asserts between its subjects (V4). Prefers a canonical operator
    ('|', '<', '=', ...) found in the canonical form; else a family/property word ('prime', 'even', ...);
    else a relation word ('coprime'); else a stable fallback (the canonical statement) so a non-relational
    lemma keys uniquely and never spuriously collides with a different one."""
    comparison = _comparison_signature(claim)
    if comparison is not None:
        return comparison[1]
    predicate = _predicate_signature(claim)
    if predicate is not None:
        return predicate[1]
    canon = canonical_form(claim)
    for op in _RELATION_OPERATORS:
        if op in canon:
            return op
    low = claim.lower()
    toks = set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", low))
    for tok in toks:
        member = _canon_member(tok)
        if member in _ALL_FAMILY_MEMBERS:
            return member
    if "coprime" in toks:
        return "coprime"
    # No recognized relation -> key by the full canonical statement (unique per distinct lemma) so two
    # genuinely-different non-relational lemmas about the same subjects still collide and must agree.
    return canon


def _lemma_key(claim: str) -> str:
    """Backward-compatible relation+subjects key for a lemma reference (V4)."""
    subjects = _lemma_subjects(claim)
    return f"{_lemma_relation(claim, subjects)}@{subjects}"


def _relations_conflict(a: str, b: str) -> bool:
    """Whether two normalized relations on the same operands/subjects cannot both hold."""
    if a == b:
        return False
    if a in _ORDER_OUTCOMES and b in _ORDER_OUTCOMES:
        return not bool(_ORDER_OUTCOMES[a] & _ORDER_OUTCOMES[b])
    if (a.startswith("not:") or b.startswith("not:")
            or _family_of(a) is not None or _family_of(b) is not None):
        return _predicates_conflict(a, b)
    for fam in _EXCLUSIVE_FAMILIES:
        if a in fam and b in fam:
            return True
    return False


def _ingest_assumption(sig: ChildSignature, claim: str) -> None:
    """Pull a binding, predicate, or atomic comparison out of an assumption claim."""
    comparison = _comparison_signature(claim)
    mp = _PREDICATE_RE.match(claim)
    if mp and comparison is None:
        subject = mp.group(1).lower()
        sig.predicates[subject] = _norm_pred(mp.group(3), bool(mp.group(2)))
        return
    mb = _BINDING_RE.match(claim)
    if mb:
        symbol = mb.group(1).lower()
        # Skip values that are themselves predicates parsed as bindings ("n is even" can't reach here,
        # predicate matched first); keep a canonical RHS so "0" == " 0 ".
        sig.bindings[symbol] = canonical_form(mb.group(2))
    if comparison is not None:
        subjects, relation, _normalized = comparison
        sig.lemma_relations.setdefault(subjects, set()).add(relation)


# --------------------------------------------------------------------------------------------------
# The cross-child overlap check.
# --------------------------------------------------------------------------------------------------
def check_children_consistency(child_goals: list[str],
                               child_proofs: dict[str, Optional[str]]) -> H0Result:
    """Check that the extracted child signatures agree on overlaps (the H0 / 0-cocycle guard).

    `child_goals` is the ordered list of child goals; `child_proofs[goal]` is each child's proven
    ledger (text/JSON/sketch) or None. Returns an `H0Result`. ANTI-VACUITY: if NO child signature
    could be inspected, the result is `vacuous_check`. Every declared child must also be inspectable;
    ignoring one malformed sibling could hide exactly the contradiction H0 is meant to catch, so a
    partial inspection returns `incomplete_check`. Both outcomes fail closed."""
    sigs = [extract_signature(g, child_proofs.get(g)) for g in child_goals]

    # Anti-vacuity: a consistency claim requires EVERY child to have been inspected. "Inspected" means a
    # child whose proof PARSED and had steps to SCAN (its `inspectable` footprint), NOT merely that a
    # contradictable fact was found — a valid decomposition whose children declare no overlapping
    # hypotheses genuinely has nothing to conflict and must PASS. An all-unparsed / stepless child set
    # (no proof could be inspected at all) is the vacuous case -> fail loud, never a silent OK.
    inspectable = [s for s in sigs if s.inspectable]
    if not inspectable:
        return H0Result(consistent=False, reason="vacuous_check",
                        inspected_children=sum(1 for s in sigs if s.parsed))
    if len(inspectable) != len(sigs):
        return H0Result(consistent=False, reason="incomplete_check",
                        inspected_children=len(inspectable))

    conflicts: list[Conflict] = []
    overlaps = 0

    # Pairwise over children; for each pair compare bindings, predicates, lemma signatures on shared
    # subjects only (the *overlap* — agreement is only required where the children actually meet).
    for i in range(len(sigs)):
        for j in range(i + 1, len(sigs)):
            a, b = sigs[i], sigs[j]

            for sym in set(a.bindings) & set(b.bindings):
                overlaps += 1
                if a.bindings[sym] != b.bindings[sym]:
                    conflicts.append(Conflict("binding", sym, a.goal, b.goal,
                                              a.bindings[sym], b.bindings[sym]))

            for subj in set(a.predicates) & set(b.predicates):
                overlaps += 1
                if _predicates_conflict(a.predicates[subj], b.predicates[subj]):
                    conflicts.append(Conflict("predicate", subj, a.goal, b.goal,
                                              a.predicates[subj], b.predicates[subj]))

            # A binding in one child vs a predicate on the same symbol in the other can also clash
            # (x = 0 vs x is positive). Only the unambiguous numeric cases are flagged.
            for sym in set(a.bindings) & set(b.predicates):
                overlaps += 1
                if _binding_predicate_conflict(a.bindings[sym], b.predicates[sym]):
                    conflicts.append(Conflict("binding", sym, a.goal, b.goal,
                                              a.bindings[sym], b.predicates[sym]))
            for sym in set(b.bindings) & set(a.predicates):
                overlaps += 1
                if _binding_predicate_conflict(b.bindings[sym], a.predicates[sym]):
                    conflicts.append(Conflict("binding", sym, a.goal, b.goal,
                                              a.predicates[sym], b.bindings[sym]))

            # (1) SAME relation@subjects key: the two siblings reference the SAME relation on the SAME
            # subjects, so their canonical statements MUST agree (a residual disagreement is a conflict).
            for key in set(a.lemma_sigs) & set(b.lemma_sigs):
                overlaps += 1
                if a.lemma_sigs[key] != b.lemma_sigs[key]:
                    conflicts.append(Conflict("lemma_signature", key, a.goal, b.goal,
                                              a.lemma_sigs[key], b.lemma_sigs[key]))

            # (2) SAME normalized operands/subjects, possibly DIFFERENT relations. Property families
            # retain only genuinely exclusive members. Atomic order constraints are accumulated for
            # one global intersection below, because pairwise agreement alone is insufficient.
            for subj in set(a.lemma_relations) & set(b.lemma_relations):
                for ra in a.lemma_relations[subj]:
                    for rb in b.lemma_relations[subj]:
                        overlaps += 1
                        if (not (ra in _ORDER_OUTCOMES and rb in _ORDER_OUTCOMES)
                                and _relations_conflict(ra, rb)):
                            conflicts.append(Conflict("relation", subj, a.goal, b.goal,
                                                      ra, rb))

    # A global section is stronger than pairwise consistency. Intersect every normalized atomic-order
    # constraint on a subject across all children. This catches <=, >=, !=: every pair is compatible,
    # but their conjunction has no possible order outcome.
    order_assertions: dict[str, list[tuple[str, str]]] = {}
    for sig in sigs:
        for subj, relations in sig.lemma_relations.items():
            for relation in sorted(relations):
                if relation in _ORDER_OUTCOMES:
                    order_assertions.setdefault(subj, []).append((sig.goal, relation))
    for subj, assertions in order_assertions.items():
        allowed = frozenset({-1, 0, 1})
        accepted: list[tuple[str, str]] = []
        for child, relation in assertions:
            narrowed = allowed & _ORDER_OUTCOMES[relation]
            if not narrowed:
                conflicts.append(Conflict(
                    "relation", subj,
                    " & ".join(c for c, _ in accepted), child,
                    " & ".join(r for _, r in accepted), relation,
                ))
                break
            allowed = narrowed
            accepted.append((child, relation))

    n_inspected = len(inspectable)
    if conflicts:
        return H0Result(consistent=False, conflicts=conflicts, reason="conflict",
                        inspected_children=n_inspected, overlaps_checked=overlaps)
    return H0Result(consistent=True, inspected_children=n_inspected, overlaps_checked=overlaps)


def _binding_predicate_conflict(value: str, predicate: str) -> bool:
    """A concrete numeric binding vs a sign/parity predicate on the same symbol (x = 0 vs x positive)."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    base = predicate[4:] if predicate.startswith("not:") else predicate
    negated = predicate.startswith("not:")
    truth = {
        "positive": num > 0,
        "negative": num < 0,
        "zero": num == 0,
        "even": float(num).is_integer() and int(num) % 2 == 0,
        "odd": float(num).is_integer() and int(num) % 2 == 1,
    }
    if base not in truth:
        return False
    holds = truth[base]
    return holds == negated          # predicate asserts `holds==not negated`; conflict if value says otherwise
