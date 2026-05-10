# Aristotle: IMO-level Automated Theorem Proving

**Authors:** Tudor Achim, Alex Best, Alberto Bietti, Kevin Der, Mathis Federico, Sergei Gukov, Daniel Halpern-Leistner, Kirsten Henningsgard, Yury Kudryashov, Alexander Meiburg, Martin Michelsen, Riley Patterson, Eric Rodriguez, Laura Scharff, Vikram Shanker, Vladmir Sicca, Hari Sowrirajan, Aidan Swope, Matyas Tamas, Vlad Tenev, Jonathan Thomm, Harold Williams, Lawrence Wu

Type: product + paper hybrid

## Abstract

Aristotle is a Lean-centered automated theorem-proving system that combines formal proof search, informal lemma generation/formalization, and a dedicated geometry subsystem. Its defining property is that success is measured at the level of complete Lean 4 proofs rather than persuasive informal arguments. The public technical report presents this architecture as the basis for gold-medal-equivalent performance on the 2025 IMO benchmark. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic technical report PDF](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf))

## Status

Aristotle currently exists as both a research system and a product surface. The technical report is public on arXiv, and Harmonic's public site says the Aristotle API became publicly available in October 2025. What is public, however, is not the same thing as full reproducibility: from the official sources checked here, Aristotle is available as a paper plus a product/API framing, not as a fully open end-to-end research stack. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic about page](https://harmonic.fun/about/))

## 1 Objective and acceptance criterion

Aristotle matters because it treats formal verification as the acceptance condition, not as optional post-processing. The paper's standard is that a problem counts as solved only if the system produces a complete Lean 4 proof using Mathlib, without unsound placeholders such as `sorryAx`. That makes Aristotle one of the clearest current examples of a frontier system whose success criterion is genuinely machine-checked mathematics. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic technical report PDF](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf))

## 2 System architecture

The paper describes three major components:

1. a Lean proof-search system
2. an informal reasoning system that generates and formalizes lemmas
3. a specialized geometry subsystem

The Lean side is described as highly parallel Monte Carlo Graph Search guided by a large transformer that produces actions conditioned on the current formal state and, when available, an informal proof plan. The informal side generates proof ideas, decomposes them into shorter lemmas, formalizes those lemma statements in Lean, and uses Lean feedback to decide what to revise next. The paper describes the geometry side as a separate solver for plane geometry based on AlphaGeometry-style methods, while Harmonic's public materials connect that geometry work to Yuclid. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic technical report PDF](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf), [Harmonic IMO post](https://harmonic.fun/news/imo-gold/))

## 3 Informal-to-formal lemma loop

The most distinctive design choice is the lemma loop. Aristotle does not try to solve every hard problem as one monolithic proof-search problem. It first generates an informal proof or proof sketch, breaks that sketch into shorter lemmas, formalizes those lemma statements, and then uses formal success or failure to refine the decomposition. Informal reasoning is therefore not the terminal product; it is a planning layer whose job is to create a formal search problem that Lean can actually check. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic technical report PDF](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf))

This design addresses a real bottleneck in formal mathematics. Many difficult problems are easier to understand as a structured informal argument before they are tractable as a single formal object. By turning informal decomposition into formalizable lemmas, Aristotle narrows the gap between human-style mathematical planning and machine-checked proof construction. ([arXiv paper](https://arxiv.org/abs/2510.01346))

## 4 Lean proof search

The proof-search component starts from Lean code with unresolved goals and searches over tactics and proof states until those gaps are closed. The report emphasizes graph structure rather than simple tree search because one action may produce multiple downstream obligations. It also emphasizes policy/value guidance and heavy parallelism. Relative to one-shot proof generation, this makes Aristotle closer to a search-and-verification system that happens to use large models than to a pure language-model decoder. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic technical report PDF](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf))

## 5 Specialized geometry handling

Geometry is handled separately through a dedicated solver rather than through the same general-purpose Lean search loop. The paper describes this solver as based on AlphaGeometry-style methods, while Harmonic's public materials emphasize Yuclid and its open sourcing. This matters less as a geometry detail than as an architectural lesson: Aristotle is not a monolithic "math model" trying to solve every domain in one way. It routes different reasoning workloads to different engines while keeping formal verification at the center. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic IMO post](https://harmonic.fun/news/imo-gold/))

## 6 Why the design helps

The design helps because it combines three ingredients that often appear separately:

- informal mathematical planning
- explicit formal search
- a hard formal acceptance criterion

Informal reasoning alone is too unconstrained; proof search alone can be too brittle; specialized engines alone do not provide a general proving framework. Aristotle works by using each component where it is strongest, then forcing the output back through Lean. That is a plausible reason the system can tackle olympiad-style problems that require both structured mathematical ideas and exact formal verification. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic technical report PDF](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf))

## 7 Availability and limitations

Aristotle is a very important research reference point, but the public operational surface is thinner than the paper surface. The architecture is well described relative to many frontier systems, yet a typical outside user still does not get a fully open reproducible stack. It is therefore especially valuable as a model of system design and as evidence about what currently works, while being less straightforward as a drop-in everyday tool than repo-centered open tooling. ([arXiv paper](https://arxiv.org/abs/2510.01346), [Harmonic about page](https://harmonic.fun/about/))

## Sources

- Tudor Achim et al. "Aristotle: IMO-level Automated Theorem Proving." arXiv:2510.01346, 2025. [https://arxiv.org/abs/2510.01346](https://arxiv.org/abs/2510.01346)
- Harmonic. "Aristotle technical report PDF." [https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf](https://harmonic.fun/pdf/Aristotle_IMO_Level_Automated_Theorem_Proving.pdf)
- Harmonic. "How Aristotle Achieved its IMO Gold Medal-Level Performance." [https://harmonic.fun/news/imo-gold/](https://harmonic.fun/news/imo-gold/)
- Harmonic. "About." [https://harmonic.fun/about/](https://harmonic.fun/about/)
