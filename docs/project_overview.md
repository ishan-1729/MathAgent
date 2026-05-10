# Project Overview

MathAgent supports agentic search for elementary proofs of Diophantine and number-theory theorems, followed where possible by Lean formalization.

The elementary restriction matters because many target results have powerful standard proofs using algebraic number theory, elliptic curves, or other heavy machinery. MathAgent instead evaluates whether agents can discover compact integer-arithmetic arguments using an explicitly permitted method set.

Method files bias agents by exposing reusable transformations, trigger patterns, failure modes, and downstream moves. They should make the search space narrower without handing over complete benchmark proofs.

Examples are separated from target problems. Examples can show solved method usage; problem folders should usually contain only statements and allowed context to reduce contamination during evaluation.

Lean verification matters because informal proof sketches can hide gaps. Even partial formalization records where the argument first stops being mechanically checkable.
