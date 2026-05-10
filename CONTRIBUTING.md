# Contributing

MathAgent work should be branch based. Each workflow or experiment should use a separate branch, especially when comparing proof-search behavior.

Keep method/library edits separate from proof-attempt edits when possible. Method and library changes alter the agent context; proof attempts should record which context was loaded.

Merged attempts should include evaluation records under the relevant problem folder or `evaluation/`.

Ishan and Kieren can split work naturally: one person may author or edit method Markdown files while the other stands up repository and workflow infrastructure.

Mathematical claims need explicit proof status:

- `conjectural`
- `sketch`
- `verified`
- `Lean-checked`
- `failed`

Use the weakest accurate status until a claim has been reviewed.
