# MathCode

### MathCode: A Frontier Mathematical Coding Agent

```
███╗   ███╗ █████╗ ████████╗██╗  ██╗ ██████╗ ██████╗ ██████╗ ███████╗
████╗ ████║██╔══██╗╚══██╔══╝██║  ██║██╔════╝██╔═══██╗██╔══██╗██╔════╝
██╔████╔██║███████║   ██║   ███████║██║     ██║   ██║██║  ██║█████╗
██║╚██╔╝██║██╔══██║   ██║   ██╔══██║██║     ██║   ██║██║  ██║██╔══╝
██║ ╚═╝ ██║██║  ██║   ██║   ██║  ██║╚██████╗╚██████╔╝██████╔╝███████╗
╚═╝     ╚═╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
```

**Project Page:** [math-ai-org/mathcode](https://github.com/math-ai-org/mathcode)

<p align="right"><strong>English</strong> | <a href="./README.ZH.md">中文</a></p>

MathCode is a terminal AI coding assistant with a built-in math formalization engine. Give it a math problem in plain language and it will automatically convert it into a Lean 4 theorem and attempt a formal proof.

![](./Demo.png)

## Quick Start

```bash
git clone https://github.com/math-ai-org/mathcode.git
cd mathcode
bash setup.sh
codex auth login
./run
```

The `math-ai-org/mathcode` repository is a lightweight bootstrap checkout. You clone the repo, run `bash setup.sh`, and `setup.sh` downloads the matching `mathcode` binary from GitHub Releases when it is missing.

What `setup.sh` does:

- downloads the matching `mathcode-vX.Y.Z-<os>-<arch>.tar.gz` asset from GitHub Releases when `./mathcode` is missing
- verifies `SHA256SUMS.txt` when `shasum` or `sha256sum` is available
- creates `.env` from `.env.example` when needed
- bootstraps Lean locally when `lean` / `lake` are missing
- creates `skills/`, `tools/`, `plugins/` extension directories

Optional maintenance commands:

```bash
bash setup.sh --status   # check whether the binary/tooling look healthy
bash setup.sh --clean    # remove install artifacts, keep proofs/vault data
bash setup.sh --help     # show all setup flags
```

`bash setup.sh --clean` preserves user outputs in `LeanFormalizations/` and vault data.

## Requirements

- macOS (arm64) or Linux (x86_64)
- enough disk space for the bundle, Lean toolchain, and Mathlib caches
- `codex` CLI if you want the default backend and default math flow
- Python 3.12+ (optional, only needed for analysis tools in `tools/`)

## Common Commands

```bash
./run -p "prove that the square of an even number is even"
echo "hello" | ./run -p
./run --help
```

Math outputs are written to `LeanFormalizations/`.

## Features

### Persistent Lean REPL

Enable a persistent Lean language server for sub-second compile checks:

```env
MATHCODE_LEAN_REPL=1
```

After a one-time ~90s warmup (importing Mathlib), every subsequent compile check takes **~0.4s** instead of ~30s. Both error detection and pass confirmation are near-instant. The REPL automatically imports your theorem library and axiom library.

### Theorem Library

Automatically store proved theorems for reuse in future proofs:

```bash
/theorem-store on     # enable (writes to .env)
/theorem-store off    # disable
/theorem-store sync   # backfill all proved-but-unstored theorems
/theorem-store status # show stored count and vault info
```

When enabled, every successfully proved theorem is automatically named, appended to `TheoremLib/Stored.lean`, and made importable for future proofs. The prover and planner can reuse stored theorems instead of re-deriving them.

### Axiom Library

Store conversational assumptions as persistent, consistency-checked declarations:

```bash
/axiomatize "A is faster than B"     # formalize + store
/axiomatize list                     # show all active axioms
/axiomatize check                    # consistency review
/axiomatize remove <name>            # remove a declaration
```

Axioms are stored per-vault with Lean formalization, compile-checked, and auto-injected into formalization and proving prompts. Supports any domain: math, physics, chemistry, narrative, general.

### Lean LSP Integration

Enable Lean LSP for smarter lemma discovery and structured error feedback during proving:

```env
MATHCODE_USE_LSP=1
```

When enabled, the prover:
- Searches leansearch.net and Loogle for verified Mathlib lemma names before planning
- Uses structured LSP diagnostics (line/col/severity) instead of raw stderr
- Extracts proof goal at error location for targeted repairs
- Injects search results and vault knowledge into planner and prover prompts

LSP is built-in — no separate installation required.

### Obsidian Theorem Graph

Generate an Obsidian vault that visualizes theorem dependencies as a knowledge graph:

```bash
/obsidian on       # enable + generate from existing formalizations
/obsidian off      # disable
/obsidian generate # regenerate now
```

When enabled, every formalization and proof auto-updates the vault. Open it in Obsidian and use Graph View to see theorem-to-lemma relationships. Each lemma stub includes the full Lean definition queried from Mathlib via `#print`.

### Agent-Mode Proving

Each proof session becomes a full interactive chat where the agent uses tools to iteratively prove theorems:

```env
MATHCODE_AGENT_PROVE=1
```

Works best with Obsidian Theorem Graph enabled (the agent reads the vault for context). When enabled, the agent can:
- Search the vault for relevant Mathlib lemmas
- Write proof candidates and compile them via the persistent REPL
- Read compile errors, search for fixes, and recompile (up to 10 times per session)
- Stream its reasoning and tool calls in real-time

### Tree-of-Subgoals Proving

Decompose complex theorems into independent subgoals and prove them in parallel:

```env
MATHCODE_TREE_PROVE=1
MATHCODE_MAX_TREE_DEPTH=2    # recursion depth (default: 1)
```

The decomposer generates a skeleton with `have ... := by sorry` placeholders. Each subgoal is proved independently (with cooperative cancellation if one fails). Proven bodies are stitched back into the skeleton and compile-checked.

### Multi-Planner

Run multiple planners in parallel to get diverse proof strategies:

```env
MATHCODE_NUM_PLANNERS=3
```

Each planner proposes a different strategy. All discovered lemmas are saved to the vault. The prover sees all plans and picks the best approach. Default is 1 (single planner).

### Scheduled Agent Loops

The bundled CLI ships with recurring prompt scheduling enabled out of the box.

Inside interactive MathCode sessions you can use:

```bash
/loop 10m check the deploy
/loop 1h /standup 1
```

Use short-lived loops for reminders and monitoring. When you want a schedule to survive restarts, create a durable schedule from the interactive session.

## Extensibility

MathCode supports three extension mechanisms:

### Skills (`skills/`)

Drop `.md` files to add domain-specific knowledge and proving strategies. Auto-discovered at startup.

### Tools (`tools/`)

Drop Python `.py` scripts with YAML frontmatter to add analysis tools. Auto-discovered at startup.

4 analysis tools are included: `axiom_checker`, `sorry_analyzer`, `proof_stats`, `lib_search`. Python 3.12+ is required only if you use these tools.

### Plugins (`plugins/`)

Drop plugin folders with `.mathcode-plugin/plugin.json` manifests to add commands, skills, agents, MCP servers, hooks, and more. Load via `--plugin-dir` or install from Git repos via `/plugin`.

## Backend Setup

Default setup: no `.env` edits are required.

```bash
codex auth login
./run
```

To use an Anthropic-compatible backend instead, set:

```env
MATHCODE_USE_OPENAI=0

ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
```

If you also want the math tools to stop using `codex exec`, add:

```env
AUTOLEAN_USE_CODEX=0
```

Shell-exported environment variables override `.env`.

## FAQ

**Q: `./run` says the MathCode binary is not installed yet**

Run:

```bash
bash setup.sh
```

**Q: `./run` fails with `exec format error`, `Bad CPU type in executable`, or a similar startup error**

You probably downloaded the wrong binary for your platform. Re-run `bash setup.sh`, or download the correct release asset manually from GitHub Releases.

**Q: Startup says Codex auth is missing**

Run:

```bash
codex auth login
```

**Q: Can I skip cloning and just download a release asset**

Yes. You can download and extract the `.tar.gz` bundle from GitHub Releases directly. The bootstrap repo just makes `bash setup.sh` the default path.

## Community

Join our Discord for help, feedback, and discussion: **[discord.gg/f2AFP9W5](https://discord.gg/f2AFP9W5)**

## Acknowledgments

The math formalization and proving pipeline in MathCode is based on the [AUTOLEAN](https://github.com/T3S1AMAX/autolean.git) project.
