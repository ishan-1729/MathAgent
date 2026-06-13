# Branch and Workflow Protocol

Workflows are compared per fixed problem. Do not compare one workflow on a hard problem against another workflow on an easier one.

Use the same metrics across workflows for one problem. Different problem classes may use different metric weightings, but the weighting must be fixed before comparing workflows on that problem.

Run actual explorations on branches. Suggested branch naming:

```text
wf/vierzahlensatz-first/x2-plus-1-y3
```

Keep prompt exposure, loaded methods, and loaded library identities explicit in each run record.

When a workflow is inspired by systems in `research/papers/`, record the paper or software configuration explicitly. A useful workflow configuration should say which components are being permuted, for example planner, prover, verifier, retrieval layer, refinement loop, evaluator, or Lean interface.

Do not compare paper-inspired workflows unless they are run on the same fixed problem with the same allowed mathematical context and metric weights.
