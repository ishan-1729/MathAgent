"""LEAP's AND-OR proof DAG with deep-hash memoization (= AlphaProof_Nexus's goal cache).

A theorem is the root OR-node. An OR-node (a goal) can be proven either:
  - directly  (a self-contained ledger), or
  - by decomposition: a *sketch* ledger that proves the goal while citing child sub-lemmas
    (steps with justification `lemma`), whose claims become child OR-nodes (an AND-node:
    all children must be proven).

Nodes are keyed by a **deep hash** of the (normalized) goal statement (`semantic_goal_hash`), so an
identical sub-lemma arising on different branches resolves to the *same* node and is proven once and
reused (memoization). The memo is **split-keyed** (T100 P1 item 4): the *effective* cache key is the
PAIR `(semantic_goal_hash, proof_context_hash)`, where `proof_context_hash` fingerprints the
elementary toolkit / gate / denylist / axiom-whitelist. A goal stays shared across branches under one
context, but a toolkit/gate/lemma change INVALIDATES a stale PROVEN certificate (it is not reused).
A committed decomposition may not introduce a child that is an ancestor of the goal (acyclicity
guard, LEAP's state-writer), so the "proof" can never be circular.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from agent.orchestrator.state import NodeState

if TYPE_CHECKING:  # avoid a runtime import cycle (toolkit is only needed for typing here)
    from agent.gates.toolkit import Toolkit


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


def semantic_goal_hash(statement: str) -> str:
    """Deep hash of a goal STATEMENT, keyed by its conservative canonical form. Case-preserving
    (`x` != `X`); folds notation/synonyms/spacing but never renames variables or reorders operands.

    This is the *statement identity* (T100 split-keyed memo): an identical sub-lemma arising on
    different branches resolves to the SAME node and is proven once (cross-branch sharing / fan-in).
    It deliberately does NOT depend on the toolkit/gate config — that invalidation axis is carried
    separately by `proof_context_hash` so a goal stays shared while a ruleset change invalidates only
    the stale PROVEN certificate."""
    return hashlib.sha256(canonical_form(statement).encode("utf-8")).hexdigest()[:16]


# Backwards-compatible public alias (callers across the repo import `goal_hash`). The semantic hash
# IS the goal identity; the name `goal_hash` is retained so the gate/CLI/driver imports keep working.
goal_hash = semantic_goal_hash


def _ctx_tokens(toolkit: "Toolkit") -> list[str]:
    """The proof-context inputs that, if changed, must INVALIDATE a stale PROVEN certificate:
    the allowed-justification vocabulary (+ each justification's obligation), the boundary rulings,
    the prose denylist/allow-context terms, the Lean denylist/allowlist/by-fiat declarations, and the
    axiom whitelist. Order-insensitive (sorted) so a reordering of YAML keys is not a context change,
    but any addition/removal/retag is. Length-prefixed below to avoid delimiter-collision aliasing."""
    tokens: list[str] = []
    # Allowed justification vocabulary + the obligation each requires + any method_ref.
    for key in sorted(toolkit.justifications):
        j = toolkit.justifications[key]
        tokens.append(f"just:{key}|{j.obligation}|{j.method_ref or ''}")
    # Contested boundary rulings (a tool moving allowed<->disallowed is a context change).
    for tool in sorted(toolkit.boundary_rulings):
        ruling = (toolkit.boundary_rulings.get(tool) or {}).get("ruling", "")
        tokens.append(f"rule:{tool}|{ruling}")
    # Prose denylist + allow-context terms (the Layer-1b router vocabulary).
    for t in sorted(toolkit.prose_terms):
        tokens.append(f"deny:{t}")
    for t in sorted(toolkit.allow_context_terms):
        tokens.append(f"allow:{t}")
    # Lean Layer-4 audit vocabulary.
    for t in sorted(toolkit.lean_denylist_decls):
        tokens.append(f"lean_deny:{t}")
    for t in sorted(toolkit.lean_infrastructure_allowlist):
        tokens.append(f"lean_infra:{t}")
    for t in sorted(toolkit.lean_elementary_by_fiat):
        tokens.append(f"lean_fiat:{t}")
    for t in sorted(toolkit.lean_axiom_whitelist):
        tokens.append(f"axiom:{t}")
    return tokens


def proof_context_hash(toolkit: "Toolkit") -> str:
    """A stable hash over the elementary-proof CONTEXT (T100 split-keyed memo): the toolkit /
    elementary-axiom vocabulary, the allowed-justification vocabulary, the denylist/allow-context and
    Lean allowlist/denylist, and the axiom whitelist. Two runs with the same context share PROVEN
    certificates; any toolkit/gate/lemma/denylist change yields a DIFFERENT hash so a stale PROVEN
    minted under the old context is NOT reused (a cache MISS).

    Length-prefixed token encoding (steal from Forge's `_canon`): each token is emitted as
    `<len>:<token>` so a goal/string boundary can never be forged by embedding a delimiter."""
    h = hashlib.sha256()
    for tok in _ctx_tokens(toolkit):
        b = tok.encode("utf-8")
        h.update(str(len(b)).encode("ascii"))
        h.update(b":")
        h.update(b)
        h.update(b"|")
    return h.hexdigest()[:16]


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
    # The proof_context_hash this node's PROVEN certificate was accepted under (the receipt of WHICH
    # context admitted it). None until proven. A PROVEN whose context != the DAG's current context is
    # a STALE certificate and must not be reused (see ProofDAG._context_valid).
    proof_context: Optional[str] = None
    # Precise blocker classification for a failed/exhausted node (ReasonCode.value) so no give-up is
    # unclassified (T100 P1 item 3). None while OPEN/IN_PROGRESS/PROVEN.
    reason: Optional[str] = None
    # OPT-IN per-leaf Lean authority annotation (P0/P2): set True ONLY when an optional per-node
    # verifier compiled + audited this leaf's proof as elementary. It is a pure ANNOTATION on a node
    # that is PROVEN by the existing path — NOT a new NodeState and NOT a new NodeEvent (the
    # enum-cartesian FSM proptest is untouched). False by default and when no per-node verifier is
    # configured (the byte-identical default path), or when formalization could not compile (a
    # re-attemptable soft PROVEN). It never gates promotion; it records WHETHER Lean confirmed the leaf.
    lean_verified: bool = False
    # OPT-IN per-AND-node Lean COMPOSITION annotation (P4, LEAP §2.2/§2.5): set True ONLY when an
    # optional sketch verifier compiled + audited this node's WINNING decomposition sketch as a
    # SORRY-FREE composition theorem (the parent follows from the children-as-hypotheses, body deps
    # elementary). Like `lean_verified` it is a pure ANNOTATION — NOT a new NodeState/NodeEvent. False
    # by default and on the byte-identical path (no sketch verifier). It records WHETHER the COMPOSITION
    # compiled in Lean; the LEAP parent-composition rule (mark_proven_via_children) reads it together
    # with each child's `lean_verified` to set the parent's `lean_verified`.
    sketch_lean_verified: bool = False

    @property
    def proven(self) -> bool:
        return self.state is NodeState.PROVEN


class CycleError(Exception):
    pass


class ProofDAG:
    """An AND-OR proof DAG with a SPLIT-KEYED memo (T100 P1 item 4).

    Nodes are keyed by `semantic_goal_hash` (statement identity → cross-branch sharing / fan-in). The
    DAG additionally carries a `context` = `proof_context_hash(toolkit)`. A PROVEN certificate records
    the context it was accepted under (`OrNode.proof_context`). The *effective* cache key is therefore
    the PAIR `(semantic_goal_hash, proof_context_hash)`: a PROVEN node minted under a DIFFERENT context
    (a toolkit/gate/denylist/lemma change) is a STALE certificate and is NOT reused — it is reset to
    OPEN so the goal is re-attempted under the new context (a cache MISS). Identical context ⇒ hit.

    `context` may be None (legacy / context-agnostic callers): then no invalidation occurs and the
    memo behaves exactly as before (goal-identity sharing only), so existing call sites are unchanged.
    """

    def __init__(self, context: Optional[str] = None) -> None:
        self.nodes: dict[str, OrNode] = {}
        self.cache_hits = 0
        self.context = context              # proof_context_hash for this run (None = context-agnostic)
        self.stale_invalidations = 0        # # of stale PROVEN certificates evicted on context mismatch

    def _context_valid(self, node: OrNode) -> bool:
        """Is this node's PROVEN certificate valid under the DAG's current context? A None DAG-context
        means context-agnostic (always valid); otherwise the node's recorded `proof_context` must
        match. A node minted with proof_context=None pre-dates context keying and is treated as valid
        only when the DAG is also context-agnostic."""
        if self.context is None:
            return True
        return node.proof_context == self.context

    def _evict_if_stale(self, node: OrNode) -> bool:
        """If `node` is PROVEN under a different context, reset it to OPEN (a cache MISS) and report
        True. The stale artifact/children are cleared so a re-attempt under the new context starts
        clean and `assemble`/`proof_bundle` never render a certificate the new context did not admit."""
        if node.proven and not self._context_valid(node):
            node.state = NodeState.OPEN
            node.proof = None
            node.proof_kind = None
            node.children = []
            node.proof_context = None
            node.reason = None
            # Clear the OPT-IN Lean annotations too: a stale certificate's Lean stamps (leaf or
            # composition) are not valid under the new context and must be re-earned on re-attempt.
            node.lean_verified = False
            node.sketch_lean_verified = False
            self.stale_invalidations += 1
            return True
        return False

    # --- node access (memoized) ---
    def get_or_create(self, goal: str) -> OrNode:
        key = goal_hash(goal)
        node = self.nodes.get(key)
        if node is None:
            node = OrNode(goal=goal, key=key)
            self.nodes[key] = node
        elif node.proven:
            # Split-keyed memo: only count a SHARE when the certificate is valid under this context;
            # a stale PROVEN (context changed) is evicted to OPEN and re-attempted (a MISS).
            if self._evict_if_stale(node):
                pass  # stale -> miss; node is now OPEN
            else:
                self.cache_hits += 1
        return node

    def get(self, key: str) -> Optional[OrNode]:
        return self.nodes.get(key)

    def is_proven(self, goal: str) -> bool:
        node = self.nodes.get(goal_hash(goal))
        if node is None or not node.proven:
            return False
        # A stale PROVEN (minted under a different context) is NOT proven for THIS context.
        return self._context_valid(node)

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
        node.proof_context = self.context   # stamp the context this certificate was accepted under
        node.reason = None
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
        node.proof_context = self.context   # certificate is valid only under the current context
        node.reason = None
        # P4 LEAP parent-composition rule: a parent is lean_verified ONLY when its COMPOSITION sketch
        # compiled in Lean (sketch_lean_verified, stamped by the driver before this call) AND EVERY
        # child is itself lean_verified (every hypothesis discharged). This is the LEAP invariant: a
        # parent's per-node Lean authority requires both the composition AND all the discharged
        # children. When no sketch verifier ran (sketch_lean_verified stays False — the byte-identical
        # default path) this leaves lean_verified False (soft PROVEN), unchanged behavior.
        node.lean_verified = bool(
            node.sketch_lean_verified
            and all(self.nodes[ck].lean_verified for ck in node.children))
        return node

    def mark_failed(self, goal: str, *, elementary: bool = False,
                    reason: Optional[str] = None) -> OrNode:
        """Terminally fail a node. `elementary=True` records a real-but-non-elementary proof
        (FAILED_ELEMENTARY); otherwise a logical gap (FAILED_GAP). `reason` is a ReasonCode.value
        string so no give-up is unclassified (T100 P1 item 3)."""
        node = self.get_or_create(goal)
        if not node.proven:
            node.state = NodeState.FAILED_ELEMENTARY if elementary else NodeState.FAILED_GAP
            if reason is not None:
                node.reason = reason
        return node

    def mark_exhausted(self, goal: str, *, reason: Optional[str] = None) -> OrNode:
        """Mark a node EXHAUSTED (limit-induced, retryable elsewhere — NOT a terminal gap). Carries a
        ReasonCode.value so every give-up is classified."""
        node = self.get_or_create(goal)
        if not node.proven:
            node.state = NodeState.EXHAUSTED
            if reason is not None:
                node.reason = reason
        return node

    # --- output ---
    def _reported_state(self, node: OrNode) -> NodeState:
        """The state to REPORT for a node through the read accessors, respecting context validity. A
        PROVEN certificate minted under an OLD proof_context is STALE: it must NOT be rendered as PROVEN
        (the new context never admitted it). We report a stale-PROVEN as OPEN (a cache MISS that would be
        re-attempted) — the same read-through guard get_or_create/_evict_if_stale apply on access — so
        assemble()/proof_bundle()/stats() never advertise a certificate the current context does not
        admit. Non-PROVEN states are reported as-is."""
        if node.proven and not self._context_valid(node):
            return NodeState.OPEN
        return node.state

    def assemble(self, goal: str) -> dict:
        """A serializable proof tree rooted at goal. A node reused across branches (memoized) is
        expanded once; later occurrences are marked `shared` (the DAG is acyclic by construction).

        Context read-through: a context-STALE PROVEN (minted under an old proof_context) is reported as
        OPEN, not PROVEN — the same validity guard get_or_create applies — so a stale certificate is
        never rendered through this accessor."""
        key = goal_hash(goal)
        expanded: set[str] = set()

        def build(k: str) -> dict:
            node = self.nodes.get(k)
            if node is None:
                return {"goal": "<unknown>", "state": "missing"}
            entry = {"goal": node.goal, "state": self._reported_state(node).value,
                     "kind": node.proof_kind}
            if k in expanded:
                entry["shared"] = True   # memoized reuse; do not expand again
                return entry
            expanded.add(k)
            if node.children:
                entry["children"] = [build(ck) for ck in node.children]
            return entry

        return build(key)

    def proof_bundle(self, goal: str) -> str:
        """Serialize the proven proof rooted at goal into one informal text (for formalization).

        Context read-through: a context-STALE PROVEN (minted under an old proof_context) is NOT proven
        for the current context, so it is skipped — the bundle never serializes a certificate the
        current context does not admit."""
        key = goal_hash(goal)
        seen: set[str] = set()
        lines: list[str] = []

        def visit(k: str, depth: int) -> None:
            node = self.nodes.get(k)
            if node is None or not node.proven or not self._context_valid(node):
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
        # Context read-through: only count a PROVEN that is VALID under the current context. A stale
        # PROVEN (minted under an old proof_context) is not reported as proven — the same validity guard
        # get_or_create/is_proven apply — so stats never over-reports a certificate the current context
        # does not admit.
        proven = sum(1 for n in self.nodes.values() if n.proven and self._context_valid(n))
        return {"nodes": len(self.nodes), "proven": proven, "cache_hits": self.cache_hits}
