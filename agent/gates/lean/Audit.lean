/-
MathAgent Layer-4 extractor.

Defines a `#audit <decl>` command that prints, to stdout, a one-line JSON dependency report for a
compiled declaration:

    MATHAGENT_AUDIT_JSON {"theorem":"...","axioms":[...],"constants":[{"name":...,"kind":...}, ...]}

The Python auditor (`agent/gates/lean_audit.py`) consumes that JSON and decides elementarity
(axiom whitelist + content denylist over the transitive closure). This file is self-contained
(`import Lean` only — no Mathlib), so the bridge can prepend it to a core-only proof and run
`lean <file>.lean` to validate the pipeline without a Mathlib build.

JSON is built with `Lean.Json` (no hand-escaping). The authoritative decision logic lives in the
toolchain-independent, fully tested Python auditor.
-/
import Lean
open Lean

namespace MathAgent

private def constKind : ConstantInfo → String
  | .axiomInfo _  => "axiom"
  | .defnInfo _   => "definition"
  | .thmInfo _    => "theorem"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _   => "quot"
  | .inductInfo _ => "inductive"
  | .ctorInfo _   => "ctor"
  | .recInfo _    => "recursor"

/-- Constants referenced by a declaration's type and value (the proof term). -/
private def usedConsts (ci : ConstantInfo) : Array Name :=
  let fromType := ci.type.getUsedConstants
  match ci.value? with
  | some v => fromType ++ v.getUsedConstants
  | none   => fromType

/-- Transitive constant-dependency closure of `start` (iterative worklist; no stack blow-up). -/
private partial def closure (env : Environment) : List Name → NameSet → NameSet
  | [],        seen => seen
  | n :: rest, seen =>
    if seen.contains n then closure env rest seen
    else
      let seen := seen.insert n
      match env.find? n with
      | some ci => closure env (rest ++ (usedConsts ci).toList) seen
      | none    => closure env rest seen

/-- Build the one-line JSON dependency report for `declName`. -/
def auditJson (declName : Name) : CoreM String := do
  let env ← getEnv
  let axioms ← collectAxioms declName
  let closed := (closure env [declName] {}).toList
  let consts : Array Json := (closed.filterMap (fun n =>
    match env.find? n with
    | some ci => some <| Json.mkObj [("name", Json.str n.toString), ("kind", Json.str (constKind ci))]
    | none    => none)).toArray
  let axArr : Array Json := (axioms.toList.map (fun a => Json.str a.toString)).toArray
  let tc := (← IO.getEnv "MATHAGENT_TOOLCHAIN").getD ""
  let obj := Json.mkObj [
    ("theorem",   Json.str declName.toString),
    ("toolchain", Json.str tc),
    ("axioms",    Json.arr axArr),
    ("constants", Json.arr consts)]
  return obj.compress

open Lean.Elab.Command in
/-- `#audit foo` (or `#audit foo "nonce"`) logs the dependency report for `foo` as an info message,
    tagged with a sentinel. The optional `nonce` (a fresh per-run secret the Python bridge injects)
    is echoed verbatim after the sentinel, so the bridge can tell a trusted report apart from one an
    untrusted proof body forged: an attacker cannot guess the nonce, and any extra bare-sentinel line
    is rejected. Using `logInfo` (not `IO.println`) keeps the output in Lean's message channel, so it
    is captured both by `lean <file>` (stdout diagnostics) and by the REPL server (response messages). -/
elab "#audit " id:ident nonce:(str)? : command => do
  let declName := id.getId
  let s ← liftCoreM do
    let env ← getEnv
    if !env.contains declName then
      throwError s!"#audit: unknown declaration {declName}"
    auditJson declName
  let tag := match nonce with
    | some n => "MATHAGENT_AUDIT_JSON " ++ n.getString ++ " "
    | none   => "MATHAGENT_AUDIT_JSON "
  logInfo (tag ++ s)

end MathAgent
