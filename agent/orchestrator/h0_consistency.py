"""H0 child-consistency gate for AND-node composition (P2, §7 / forge_relevance_study §4.3.4).

Before an AND-node is promoted to PROVEN by composing its children, the children's local proofs must
**agree on their overlaps**. The committed children of an AND-node are *precedence*-independent (the
acyclicity guard ensures it) — but **precedence-independent is NOT assumption-independent**: two
children may each be valid *locally* yet rest on *contradictory* hypotheses, variable bindings, or
different versions of a shared lemma. Composing them then proves nothing globally: there is no single
consistent world in which all the children hold at once.

The category-theory shadow (optional label): "agreement on overlaps" is the **0-cocycle condition**;
when it holds a **global section exists** (H0 is nonempty) and the composition is sound; when it fails
there is a **0-cocycle violation** (no global section) and the parent must NOT be promoted — route to
review / mark FAILED_ELEMENTARY (reason `elementary_violation`). The operational content is what
matters: a concrete **signature-compatibility check**.

What we extract from each child's proven ledger as its *signature*:
  * **assumptions / hypotheses** — the claims of `given`/`assumption` steps (the world the child rests
    on), and any predicate-style binding (`n is even`, `p is prime`, `x > 0`);
  * **bindings** — value definitions of a symbol (`let x = 0`, `n = 1`);
  * **lemma signatures** — the claims of `lemma` steps, keyed so the SAME lemma referenced by two
    siblings must state the SAME thing.

A conflict on an *overlap* (the same subject/symbol/lemma key constrained two incompatible ways by two
different children) is the violation. We name the conflicting overlap so the give-up is classified, not
silent.

ANTI-VACUITY (the Forge "running 0 tests fails loud" trap): a consistency check that inspected NOTHING
— no children, or children whose proofs do not parse / carry no extractable signature at all — must NOT
vacuously pass. `check_children_consistency` returns `consistent=False` with reason `vacuous_check` in
that case (the caller treats a vacuous inspection as a non-promotion, never a silent OK). A genuinely
empty-but-inspectable signature set (children that parsed and were inspected, finding nothing to
conflict) DOES pass — vacuity is "inspected nothing", not "found nothing".

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

    kind: str                 # "binding" | "predicate" | "lemma_signature"
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
    # subjects-bucket -> {relation tokens asserted about that subject set}. Keyed by SUBJECTS ONLY so a
    # cross-relation contradiction on the same subjects (e.g. 'g<h' vs 'g>h') is checkable, while a
    # COMPATIBLE pair of independent relations ('g<h' vs 'g|h') is NOT keyed together (V4 fix).
    lemma_relations: dict[str, set[str]] = field(default_factory=dict)

    @property
    def has_content(self) -> bool:
        return bool(self.bindings or self.predicates or self.lemma_sigs)

    @property
    def inspectable(self) -> bool:
        """The signature was genuinely INSPECTED iff the proof parsed AND had steps to scan. A
        parsed-but-stepless or unparseable proof contributes nothing inspectable (anti-vacuity)."""
        return self.parsed and self.step_count > 0


@dataclass
class H0Result:
    consistent: bool
    conflicts: list[Conflict] = field(default_factory=list)
    reason: Optional[str] = None          # None on a real pass; "vacuous_check" / "conflict" otherwise
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
# open/closed (topology) is a genuine mutually-exclusive pair for a fixed set, so a sibling assuming a
# set is open while another assumes it closed is a 0-cocycle violation (it is NOT generally true that a
# set is clopen). These families are reused for the lemma-relation conflict check (_RELATION_FAMILIES).
_EXCLUSIVE_FAMILIES = [
    {"even", "odd"},
    {"prime", "composite"},
    {"open", "closed"},
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

    A proof that does not parse yields an *un-parsed* signature (`parsed=False`, no content) — the
    anti-vacuity logic in `check_children_consistency` treats an all-unparsed child set as a vacuous
    (fail-loud) inspection rather than a pass."""
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
            sig.lemma_sigs[f"{relation}@{subjects}"] = canonical_form(claim)
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

# MUTUALLY-EXCLUSIVE relation families (V4): two DIFFERENT relations on the SAME subjects conflict ONLY
# when they are distinct members of one of these families. The order/equality family is an exclusion
# group (g<h and g>h cannot both hold; g<h and g=h cannot both hold). Divisibility ('|') and coprimality
# are INDEPENDENT relations — they share NO family with order/equality, so 'g<h' vs 'g|h' do NOT conflict
# (the V4 false positive). Property families (prime/composite, even/odd, ...) reuse _EXCLUSIVE_FAMILIES
# so a family-word lemma ('g is prime' vs 'g is composite') still conflicts.
_RELATION_FAMILIES = [
    {"<", ">", "=", "<=", ">=", "!="},     # order/equality trichotomy — mutually exclusive
] + [set(fam) for fam in _EXCLUSIVE_FAMILIES]


def _lemma_subjects(claim: str) -> str:
    """The sorted SUBJECT identifier tokens of a lemma (the symbols it is about), with connective AND
    relation/family words stripped. Two siblings citing a lemma about the same subjects share this
    bucket regardless of the relation, so a cross-relation contradiction on those subjects is checkable."""
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
    """Do two DIFFERENT relation tokens on the SAME subjects contradict? Only when they are distinct
    members of one mutually-exclusive relation family ('<' vs '>', 'prime' vs 'composite'). Independent
    relations ('<' vs '|') share no family -> NOT a conflict (V4)."""
    if a == b:
        return False
    for fam in _RELATION_FAMILIES:
        if a in fam and b in fam:
            return True
    return False


def _ingest_assumption(sig: ChildSignature, claim: str) -> None:
    """Pull a binding (`x = 0`) or a predicate (`n is even`) out of an assumption claim."""
    mp = _PREDICATE_RE.match(claim)
    if mp:
        subject = mp.group(1).lower()
        sig.predicates[subject] = _norm_pred(mp.group(3), bool(mp.group(2)))
        return
    mb = _BINDING_RE.match(claim)
    if mb:
        symbol = mb.group(1).lower()
        # Skip values that are themselves predicates parsed as bindings ("n is even" can't reach here,
        # predicate matched first); keep a canonical RHS so "0" == " 0 ".
        sig.bindings[symbol] = canonical_form(mb.group(2))


# --------------------------------------------------------------------------------------------------
# The cross-child overlap check.
# --------------------------------------------------------------------------------------------------
def check_children_consistency(child_goals: list[str],
                               child_proofs: dict[str, Optional[str]]) -> H0Result:
    """Check that the children's signatures agree on overlaps (the H0 / 0-cocycle gate).

    `child_goals` is the ordered list of child goals; `child_proofs[goal]` is each child's proven
    ledger (text/JSON/sketch) or None. Returns an `H0Result`. ANTI-VACUITY: if NO child signature
    could be inspected (no children, or every child's proof was unparseable / contentless), the result
    is `consistent=False, reason='vacuous_check'` — a vacuous inspection FAILS LOUD, never passes."""
    sigs = [extract_signature(g, child_proofs.get(g)) for g in child_goals]

    # Anti-vacuity: a consistency claim requires SOMETHING to have been inspected. "Inspected" means a
    # child whose proof PARSED and had steps to SCAN (its `inspectable` footprint), NOT merely that a
    # contradictable fact was found — a valid decomposition whose children declare no overlapping
    # hypotheses genuinely has nothing to conflict and must PASS. An all-unparsed / stepless child set
    # (no proof could be inspected at all) is the vacuous case -> fail loud, never a silent OK.
    inspectable = [s for s in sigs if s.inspectable]
    if not inspectable:
        return H0Result(consistent=False, reason="vacuous_check",
                        inspected_children=sum(1 for s in sigs if s.parsed))

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

            # (2) SAME subjects, DIFFERENT relations (V4): flag a conflict ONLY when the two relations
            # are distinct members of a mutually-exclusive relation family ('g<h' vs 'g>h', 'g prime' vs
            # 'g composite'). Independent relations on the same subjects ('g<h' vs 'g|h') are COMPATIBLE
            # and must NOT be flagged — that was the V4 false positive.
            for subj in set(a.lemma_relations) & set(b.lemma_relations):
                for ra in a.lemma_relations[subj]:
                    for rb in b.lemma_relations[subj]:
                        if _relations_conflict(ra, rb):
                            overlaps += 1
                            conflicts.append(Conflict("lemma_signature", subj, a.goal, b.goal,
                                                      ra, rb))

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
