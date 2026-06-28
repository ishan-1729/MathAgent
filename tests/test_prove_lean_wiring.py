"""Tests for the per-node Lean wiring in the CLI (scripts/prove.py) — Task A.

The per-node Lean authority (leaf ``node_verifier`` + AND-composition ``sketch_verifier``) and the
first-class ``LEAN_VERIFIED`` state were UNREACHABLE in real runs: ``DagDriver`` accepted
``node_verifier``/``sketch_verifier``/``lean_strict``/``lean_server`` but NO production call site wired
them and ``prove.py`` had no flag. This pins the ``--lean-per-node`` / ``--lean-strict`` wiring by
driving the ACTUAL ``build_arg_parser() -> build_dag_driver() -> DagDriver`` construction path (the real
argparse, not a hand-rolled stub) and asserting on the constructed driver:

  * WITHOUT the flag the driver is BYTE-IDENTICAL to the pre-feature path:
    ``node_verifier is None`` and ``sketch_verifier is None`` (and ``lean_strict`` False, no lean_server).
  * WITH ``--lean-per-node`` the driver is constructed WITH non-None ``node_verifier`` AND
    ``sketch_verifier`` and the warm ``lean_server`` threaded in, so LEAN_VERIFIED becomes reachable.
  * WITH ``--lean-strict`` the same, PLUS ``lean_strict=True`` (and it implies ``--lean-per-node``).

The formalizer and Lean server are stubbed; no Codex/Lean process is ever launched.

Each assertion exercises the REAL wiring: it fails against the pre-change tree (where ``prove.py`` has
no ``--lean-per-node`` flag and never constructs ``DagDriver`` with a ``node_verifier``).
"""
import importlib.util
import sys
from pathlib import Path

import pytest

import agent.tools.formalizer as _formalizer_mod
from agent.gates.toolkit import load_toolkit
from agent.orchestrator import Budget, RunTrace
from agent.orchestrator.state import NodeState
from agent.tools.codex_prover import CodexConfig

# scripts/ is not a package; load prove.py by path (mirrors tests/test_reporting_status.py).
_PROVE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prove.py"
_spec = importlib.util.spec_from_file_location("prove_cli", _PROVE_PATH)
prove_cli = importlib.util.module_from_spec(_spec)
sys.modules["prove_cli"] = prove_cli
_spec.loader.exec_module(prove_cli)


class _StubServer:
    """A stand-in for the warm LeanServer: only needs ``available()`` (the gates probe it)."""

    def __init__(self, available: bool = True):
        self._available = available

    def available(self) -> bool:
        return self._available


class _StubProver:
    pass


@pytest.fixture
def deps():
    return {
        "prover": _StubProver(),
        "toolkit": load_toolkit(),
        "cfg": CodexConfig(),
        "budget": Budget(),
        "trace": RunTrace("t"),
    }


def _parse(argv):
    """Drive the REAL argparse parser + the REAL flag normalization (the actual CLI path)."""
    args = prove_cli.build_arg_parser().parse_args(argv)
    prove_cli.normalize_lean_flags(args)
    return args


def _build(args, deps, server):
    return prove_cli.build_dag_driver(
        args, prover=deps["prover"], toolkit=deps["toolkit"], cfg=deps["cfg"],
        budget=deps["budget"], trace=deps["trace"], server=server,
        terminal_gate=None, comparator=None, refiner=None, evolve_fallback=None,
    )


# ---- WITHOUT the flag: the driver is byte-identical to the pre-feature default path ----------------

def test_without_lean_per_node_driver_has_no_node_verifiers(deps):
    args = _parse(["a goal"])
    assert args.lean_per_node is False and args.lean_strict is False
    driver = _build(args, deps, server=None)
    # The REAL DagDriver construction must leave the per-node Lean authority OFF (byte-identical).
    assert driver.node_verifier is None
    assert driver.sketch_verifier is None
    assert driver.lean_strict is False
    assert driver.lean_server is None


def test_build_lean_node_gates_returns_none_pair_without_flag(deps):
    """The wiring helper itself returns (None, None) on the default path — no formalizer/server built."""
    args = _parse(["a goal"])
    nv, sv = prove_cli.build_lean_node_gates(args, deps["toolkit"], deps["cfg"], server=None)
    assert nv is None and sv is None


# ---- WITH --lean-per-node: non-None node + sketch verifiers, warm server threaded in ---------------

def test_lean_per_node_wires_both_verifiers_and_server(deps):
    args = _parse(["a goal", "--lean-per-node"])
    assert args.lean_per_node is True
    srv = _StubServer()
    driver = _build(args, deps, server=srv)
    # The whole point: LEAN_VERIFIED is now reachable because EVERY leaf + AND-composition is wired
    # to a Lean verifier and the warm server is held by the driver.
    assert driver.node_verifier is not None
    assert driver.sketch_verifier is not None
    assert callable(driver.node_verifier)
    assert callable(driver.sketch_verifier)
    assert driver.lean_server is srv
    # --lean-per-node alone is NOT strict (only --lean-strict sets that).
    assert driver.lean_strict is False


def test_lean_per_node_implies_server_flag(deps):
    args = _parse(["a goal", "--lean-per-node"])
    # --lean-per-node implies --server (one warm Lean env reused by every per-node compile).
    assert args.server is True


# ---- WITH --lean-strict: same wiring PLUS lean_strict=True (and it implies --lean-per-node) --------

def test_lean_strict_implies_per_node_and_sets_strict(deps):
    args = _parse(["a goal", "--lean-strict"])
    assert args.lean_strict is True
    assert args.lean_per_node is True   # --lean-strict implies --lean-per-node
    assert args.server is True
    srv = _StubServer()
    driver = _build(args, deps, server=srv)
    assert driver.node_verifier is not None
    assert driver.sketch_verifier is not None
    assert driver.lean_strict is True   # passed through to DagDriver
    assert driver.lean_server is srv


# ---- the formalizer is stubbable and the --formalizer-model selects the impl ----------------------

def test_formalizer_model_selects_codex_by_default(deps, monkeypatch):
    built = {}

    class _StubCodexFormalizer:
        def __init__(self, toolkit, cfg=None):
            built["codex"] = True

    class _StubClaudeFormalizer:
        def __init__(self, toolkit, cfg=None):
            built["claude"] = True

    # Patch where build_lean_node_gates imports them (it does `from agent.tools.formalizer import ...`).
    monkeypatch.setattr(_formalizer_mod, "CodexFormalizer", _StubCodexFormalizer)
    monkeypatch.setattr(_formalizer_mod, "ClaudeFormalizer", _StubClaudeFormalizer)

    args = _parse(["a goal", "--lean-per-node"])
    nv, sv = prove_cli.build_lean_node_gates(args, deps["toolkit"], deps["cfg"], server=_StubServer())
    assert nv is not None and sv is not None
    assert built.get("codex") is True and "claude" not in built


def test_formalizer_model_claude_selects_claude(deps, monkeypatch):
    built = {}

    class _StubClaudeFormalizer:
        def __init__(self, toolkit, cfg=None):
            built["claude"] = True

    monkeypatch.setattr(_formalizer_mod, "ClaudeFormalizer", _StubClaudeFormalizer)
    args = _parse(["a goal", "--lean-per-node", "--formalizer-model", "claude"])
    nv, sv = prove_cli.build_lean_node_gates(args, deps["toolkit"], deps["cfg"], server=_StubServer())
    assert nv is not None and sv is not None
    assert built.get("claude") is True


# ---- the stub server's availability is what the wired gate actually probes ------------------------

def test_wired_node_gate_probes_the_server_availability(deps):
    """The node_verifier is a REAL make_node_gate closure threaded to our stub server: when the server
    reports UNAVAILABLE the gate returns a retryable lean_unavailable result WITHOUT compiling, proving
    the warm server is genuinely wired through (not ignored)."""
    args = _parse(["a goal", "--lean-per-node"])
    driver = _build(args, deps, server=_StubServer(available=False))
    res = driver.node_verifier("some node goal", "claim: X\nconclusion: X")
    assert getattr(res, "lean_unavailable", False) is True
    assert getattr(res, "compiled", True) is False


# ---- main() errors out (does not crash) when --lean-per-node but Lean is unavailable --------------

def test_main_exits_when_lean_per_node_but_lean_unavailable(monkeypatch, capsys):
    """--lean-per-node REQUIRES Lean: a missing REPL is a clean non-zero exit, never a crash and never a
    silent fall-back to per-call lean. Drives the REAL main() up to the server-start guard."""
    import agent.tools.codex_prover as cp
    import agent.gates.lean_server as ls

    monkeypatch.setattr(cp.CodexProver, "available", staticmethod(lambda: True))
    monkeypatch.setattr(ls.LeanServer, "available", classmethod(lambda cls, project_dir=None: False))
    monkeypatch.setattr(sys, "argv", ["prove.py", "a goal", "--lean-per-node"])

    rc = prove_cli.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "--lean-per-node requires the Lean server" in err


def test_main_falls_back_for_plain_server_when_lean_unavailable(monkeypatch, capsys):
    """CONTRAST (byte-identical pre-feature behavior): plain --server (NOT --lean-per-node) with no REPL
    must NOT exit early — it falls back to per-call lean. This guards that the new error path is scoped
    to --lean-per-node only. We stop the run right after the server block by stubbing the DAG run."""
    import agent.tools.codex_prover as cp
    import agent.gates.lean_server as ls

    monkeypatch.setattr(cp.CodexProver, "available", staticmethod(lambda: True))
    monkeypatch.setattr(ls.LeanServer, "available", classmethod(lambda cls, project_dir=None: False))

    # Make the DAG driver a no-op so main() does not launch a real Codex run after the server block.
    class _NoopResult:
        proven = False
        terminal = None

        class dag:
            nodes = {}

            @staticmethod
            def stats():
                return {"nodes": 0, "proven": 0, "cache_hits": 0}

        def proof_tree(self):
            return {}

    class _NoopDriver:
        def __init__(self, *a, **k):
            pass

        def run(self, goal):
            return _NoopResult()

    monkeypatch.setattr(prove_cli, "build_dag_driver", lambda *a, **k: _NoopDriver())
    monkeypatch.setattr(sys, "argv", ["prove.py", "a goal", "--server"])

    rc = prove_cli.main()
    out = capsys.readouterr().out
    # Did NOT take the --lean-per-node error exit (rc==2); fell through to per-call lean.
    assert rc in (0, 1)
    assert "using per-call lean" in out
