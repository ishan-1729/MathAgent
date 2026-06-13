"""LEAP's AND-OR proof DAG with deep-hash memoization (= AlphaProof_Nexus's goal cache).

A theorem is the root OR-node. An OR-node (a goal) can be proven either:
  - directly  (a self-contained ledger), or
  - by decomposition: a *sketch* ledger that proves the goal while citing child sub-lemmas
    (steps with justification `lemma`), whose claims become child OR-nodes (an AND-node:
    all children must be proven).

Nodes are keyed by a **deep hash** of the (normalized) goal statement, so an identical sub-lemma
arising on different branches resolves to the *same* node and is proven once and reused
(memoization). A committed decomposition may not introduce a child that is an ancestor of the goal
(acyclicity guard, LEAP's state-writer), so the "proof" can never be circular.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from agent.orchestrator.state import NodeState


# --------------------------------------------------------------------------------------------------
# Semantic-ish canonicalization for the memo key (see research/docs/system_design.md §7).
#
# This key is SOUNDNESS-CRITICAL: a *false hit* (two distinct goals hashing equal) would reuse a proof
# of the wrong lemma. So canonicalization folds ONLY meaning-preserving surface differences (notation,
# synonyms, spacing) and DELIBERATELY does NOT rename variables (α-renaming free single letters is
# unsound — it would merge `x ∣ y` with `y ∣ x`) and does NOT reorder operands. Missing a true match
# only costs recomputation; a false match would be unsound, so we bias hard toward never merging.
# --------------------------------------------------------------------------------------------------

_SUPERSCRIPT = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}
_SUBSCRIPT = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
              "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9"}

# Single-character math symbols → a canonical ASCII spelling (meaning-preserving).
_SYMBOLS = [
    ("⟺", "<->"), ("⇔", "<->"), ("↔", "<->"),
    ("⟹", "->"), ("⟶", "->"), ("⇒", "->"), ("→", "->"),
    ("≤", "<="), ("⩽", "<="), ("≥", ">="), ("⩾", ">="), ("≠", "!="),
    ("×", "*"), ("⋅", "*"), ("·", "*"),
    ("−", "-"), ("–", "-"), ("—", "-"),          # U+2212 minus, en-dash, em-dash → '-'
    ("∤", " notdvd "), ("∣", "|"),               # divisibility
    ("ℤ", " Int "), ("ℕ", " Nat "), ("ℚ", " Rat "), ("ℝ", " Real "), ("ℂ", " Complex "),
    ("∀", " forall "), ("∃", " exists "), ("∈", " in "),
    ("∞", " infinity "), ("√", " sqrt "), ("∑", " sum "), ("∏", " prod "), ("∅", " emptyset "),
]

# Natural-language phrasings → a canonical spelling. Applied longest-first with word boundaries so
# "iff" never matches inside "diff" and "less than or equal to" is folded before "less than".
# NOTE: only ORDER-PRESERVING synonyms are folded; reversing phrases ("is divisible by", "is a
# multiple of") are intentionally omitted because they would change operand order.
_PHRASES = [
    ("if and only if", "<->"),
    ("greater than or equal to", ">="), ("less than or equal to", "<="),
    ("is greater than", ">"), ("is less than", "<"),
    ("greater than", ">"), ("less than", "<"),
    ("is not equal to", "!="), ("not equal to", "!="),
    ("is equal to", "="), ("equals", "="),
    ("for all", "forall"), ("for every", "forall"), ("for each", "forall"), ("for any", "forall"),
    ("there exists", "exists"), ("there exist", "exists"),
    ("such that", "s.t."), ("divides", "|"), ("implies", "->"), ("iff", "<->"), ("modulo", "mod"),
]
_PHRASE_RES = [
    (re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE), f" {r} ")
    for p, r in sorted(_PHRASES, key=lambda x: -len(x[0]))
]

_LEAD = re.compile(
    r"^\s*(?:we\s+(?:want|need|must|have)\s+to\s+show\s+that|prove\s+that|show\s+that|"
    r"prove|show|claim\s+that|claim|it\s+holds\s+that)\s+", re.IGNORECASE)
_OP_SPACE = re.compile(r"\s*([-+*/^=<>|(){}\[\],!])\s*")


def _fold_scripts(s: str) -> str:
    """Super/subscript runs → `^digits` / `_digits` (so `n²` ≡ `n^2`, `x₁` ≡ `x_1`)."""
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        table = _SUPERSCRIPT if s[i] in _SUPERSCRIPT else (_SUBSCRIPT if s[i] in _SUBSCRIPT else None)
        if table is None:
            out.append(s[i]); i += 1
            continue
        digits = []
        while i < n and s[i] in table:
            digits.append(table[s[i]]); i += 1
        out.append(("^" if table is _SUPERSCRIPT else "_") + "".join(digits))
    return "".join(out)


def canonical_form(statement: str) -> str:
    """Conservative semantic canonicalization of a goal statement (see the module note above)."""
    s = unicodedata.normalize("NFC", statement or "")
    s = s.replace("$", "").replace("`", "")          # drop math/code delimiters
    s = _fold_scripts(s)
    for src, dst in _SYMBOLS:
        s = s.replace(src, dst)
    for rx, repl in _PHRASE_RES:
        s = rx.sub(repl, s)
    s = _LEAD.sub("", s)                              # drop a leading "prove that"/"show that"/…
    s = re.sub(r"\s+", " ", s).strip()
    s = _OP_SPACE.sub(r"\1", s)                       # normalize spacing around operators
    s = s.rstrip(".")                                 # drop a trailing period
    return s.strip()


def goal_hash(statement: str) -> str:
    """Deep hash of a goal statement, keyed by its conservative canonical form. Case-preserving
    (`x` != `X`); folds notation/synonyms/spacing but never renames variables or reorders operands."""
    return hashlib.sha256(canonical_form(statement).encode("utf-8")).hexdigest()[:16]


@dataclass
class Decomposition:
    """An AND-node: a sketch proving the parent from these child sub-lemma goals."""
    sketch: str                      # the sketch ledger (text/JSON) citing the children
    child_keys: list[str]
    child_goals: list[str]


@dataclass
class OrNode:
    goal: str
    key: str
    state: NodeState = NodeState.OPEN
    proof: Optional[str] = None          # winning artifact: a direct ledger or the sketch
    proof_kind: Optional[str] = None     # "direct" | "decomposition"
    children: list[str] = field(default_factory=list)  # child keys of the winning decomposition
    attempts: int = 0

    @property
    def proven(self) -> bool:
        return self.state is NodeState.PROVEN


class CycleError(Exception):
    pass


class ProofDAG:
    def __init__(self) -> None:
        self.nodes: dict[str, OrNode] = {}
        self.cache_hits = 0

    # --- node access (memoized) ---
    def get_or_create(self, goal: str) -> OrNode:
        key = goal_hash(goal)
        node = self.nodes.get(key)
        if node is None:
            node = OrNode(goal=goal, key=key)
            self.nodes[key] = node
        elif node.proven:
            self.cache_hits += 1
        return node

    def get(self, key: str) -> Optional[OrNode]:
        return self.nodes.get(key)

    def is_proven(self, goal: str) -> bool:
        node = self.nodes.get(goal_hash(goal))
        return bool(node and node.proven)

    # --- acyclicity ---
    def reaches(self, start_key: str, target_key: str) -> bool:
        """Can target_key be reached from start_key via committed decomposition edges?"""
        seen: set[str] = set()
        stack = [start_key]
        while stack:
            cur = stack.pop()
            if cur == target_key:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            node = self.nodes.get(cur)
            if node:
                stack.extend(node.children)
        return False

    def would_create_cycle(self, parent_goal: str, child_goals: list[str],
                           ancestors: Optional[set[str]] = None) -> bool:
        pkey = goal_hash(parent_goal)
        ancestors = ancestors or set()
        for cg in child_goals:
            ckey = goal_hash(cg)
            if ckey == pkey:                      # child is the goal restated
                return True
            if ckey in ancestors:                 # child is an ancestor on the current path
                return True
            if self.reaches(ckey, pkey):          # child already (transitively) needs the goal
                return True
        return False

    # --- commits ---
    def mark_proven_direct(self, goal: str, ledger: str) -> OrNode:
        node = self.get_or_create(goal)
        node.state = NodeState.PROVEN
        node.proof = ledger
        node.proof_kind = "direct"
        node.children = []
        return node

    def commit_decomposition(self, goal: str, sketch: str, child_goals: list[str],
                             ancestors: Optional[set[str]] = None) -> Decomposition:
        if self.would_create_cycle(goal, child_goals, ancestors):
            raise CycleError(f"decomposition of {goal!r} would create a cycle")
        node = self.get_or_create(goal)
        child_keys = [self.get_or_create(cg).key for cg in child_goals]
        node.children = child_keys
        node.proof = sketch
        node.proof_kind = "decomposition"
        # state stays OPEN/IN_PROGRESS until all children prove; caller flips it.
        return Decomposition(sketch=sketch, child_keys=child_keys, child_goals=list(child_goals))

    def mark_proven_via_children(self, goal: str) -> OrNode:
        node = self.get_or_create(goal)
        if not all(self.nodes[ck].proven for ck in node.children):
            raise ValueError("cannot mark proven: not all children are proven")
        node.state = NodeState.PROVEN
        return node

    def mark_failed(self, goal: str) -> OrNode:
        node = self.get_or_create(goal)
        if not node.proven:
            node.state = NodeState.FAILED_GAP
        return node

    # --- output ---
    def assemble(self, goal: str) -> dict:
        """A serializable proof tree rooted at goal. A node reused across branches (memoized) is
        expanded once; later occurrences are marked `shared` (the DAG is acyclic by construction)."""
        key = goal_hash(goal)
        expanded: set[str] = set()

        def build(k: str) -> dict:
            node = self.nodes.get(k)
            if node is None:
                return {"goal": "<unknown>", "state": "missing"}
            entry = {"goal": node.goal, "state": node.state.value, "kind": node.proof_kind}
            if k in expanded:
                entry["shared"] = True   # memoized reuse; do not expand again
                return entry
            expanded.add(k)
            if node.children:
                entry["children"] = [build(ck) for ck in node.children]
            return entry

        return build(key)

    def proof_bundle(self, goal: str) -> str:
        """Serialize the proven proof rooted at goal into one informal text (for formalization)."""
        key = goal_hash(goal)
        seen: set[str] = set()
        lines: list[str] = []

        def visit(k: str, depth: int) -> None:
            node = self.nodes.get(k)
            if node is None or not node.proven:
                return
            pad = "  " * depth
            lines.append(f"{pad}GOAL: {node.goal}")
            if k in seen:
                lines.append(f"{pad}(proven above; reused)")
                return
            seen.add(k)
            if node.proof_kind == "decomposition":
                lines.append(f"{pad}DECOMPOSITION SKETCH (cites the sub-lemmas below): {node.proof}")
                lines.append(f"{pad}SUB-LEMMAS:")
                for ck in node.children:
                    visit(ck, depth + 1)
            else:
                lines.append(f"{pad}PROOF LEDGER: {node.proof}")

        visit(key, 0)
        return "\n".join(lines)

    def stats(self) -> dict:
        proven = sum(1 for n in self.nodes.values() if n.proven)
        return {"nodes": len(self.nodes), "proven": proven, "cache_hits": self.cache_hits}
