# Metrics

## 1. Novelty

Does the attempt find a new use pattern, for example an `A7`-style pattern beyond existing examples?

## 2. Step count x complexity

Count logical steps. Allowed elementary methods have low complexity. Disallowed final methods receive a high penalty.

Use a product-style score, not merely a sum.

## 3. Generalizability

Score whether the attempt is a one-off proof or exposes a reusable method/class.

## 4. Percentage compilable in Lean

Track where formalization first fails. Consider nonlinear scoring where early failure is punished more than late failure.

## 5. Elementary compliance

Apply a hard or soft penalty for forbidden final tools, depending on the evaluation setup.
