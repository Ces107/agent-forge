# State of the art — what agent-forge draws from, and where it goes beyond

This document is the honest map of the field agent-forge competes in. It names the open-source
projects that define the 2026 state of the art in autonomous software engineering, states exactly
what agent-forge adopts from each, and then makes the harder argument: where the best tools stop,
and what agent-forge does past that line. It is updated as the field moves; treat it as a living
literature review, not a marketing page.

The thesis of this repo is narrow and unusual: not "an agent can write code" (settled, and the
leaders below prove it at scale), but "the *process* an agent followed is legible and falsifiable."
Most of the field optimises capability. agent-forge optimises **auditability of capability**.

---

## 1. The landscape (the projects worth stealing from)

| Project | License | What it pioneered / does best | Stars (≈, 2026) |
|---|---|---|---|
| [OpenHands](https://github.com/All-Hands-AI/OpenHands) (ex-OpenDevin) | MIT | Production agent runtime: Docker sandbox, browser + jupyter + bash tools, best open SWE-bench Verified score (~66% Sonnet, higher with Opus harness). | ~72k |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) (Princeton) | MIT | The Agent-Computer Interface (ACI) abstraction; minimal, text-first, the canonical research reference for tool design. | ~19k |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | MIT | Spec-Driven Development as a CLI: `Specify → Plan → Tasks → Implement`, agent-agnostic (30+ agents). | ~93k |
| [AWS Kiro](https://kiro.dev) | proprietary IDE | SDD with EARS-notation acceptance criteria; Requirements → Design → Tasks with completion tracking. | n/a |
| [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | MIT | Agentic-agile roles (PM, Architect, Dev, QA, Scrum Master); "context-engineered" story files that carry full context to the dev agent. | active, 42 platforms |
| [LangGraph](https://github.com/langchain-ai/langgraph) | MIT | Agents as nodes in a directed graph with shared state, durable checkpoints, human-in-the-loop interrupts. | leading by search volume |
| [CrewAI](https://github.com/crewAIInc/crewAI) | MIT | Role-based crews with typed task delegation; the most ergonomic multi-agent authoring model. | large |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | MIT | AutoGen + Semantic Kernel merged; event-driven runtime, GA 2026. | new GA |
| [Letta](https://github.com/letta-ai/letta) (ex-MemGPT) | Apache-2.0 | LLM-as-OS memory: main context / recall / archival tiers, self-editing memory. | large |
| [Mem0](https://github.com/mem0ai/mem0) | Apache-2.0 | CRUD memory layer with an extraction phase and ADD/UPDATE/DELETE/NOOP reconciliation; AWS Agent SDK's memory provider. | ~41k |
| [Langfuse](https://github.com/langfuse/langfuse) / [OpenLLMetry](https://github.com/traceloop/openllmetry) | MIT / Apache-2.0 | OpenTelemetry **GenAI semantic conventions**: `gen_ai.*` spans for agent / tool / model, vendor-portable traces. | large |
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) / [terminal-bench](https://github.com/laude-institute/terminal-bench) | MIT | The evaluation harnesses the whole field is scored on (real GitHub issues / real terminal tasks). | reference |
| [in-toto](https://github.com/in-toto/in-toto) / [SLSA](https://slsa.dev) / [sigstore](https://github.com/sigstore) | Apache-2.0 | Software **supply-chain attestation**: signed, content-addressed provenance for build artifacts. Mature in CI/CD; **un-applied to "which agent produced which artifact"** — agent-forge's opening. | reference |

Two patterns this table makes obvious. First, the capability leaders (OpenHands, SWE-agent) and the
*method* leaders (Spec Kit, Kiro, BMAD) are largely disjoint projects. Second, none of them treats
**provenance** (who/which agent is accountable for each artifact) as a first-class, machine-checked
property. Those two gaps are agent-forge's opening.

---

## 2. What agent-forge adopts (and from whom)

agent-forge is deliberately small, so it adopts *patterns*, not dependencies. Each row below is a
concrete thing in this repo and the project it is taken from.

| Adopted into agent-forge | Where | Drawn from |
|---|---|---|
| **Spec-Driven Development flow** `Spec → Architect → Tasks → Implement → Review → Verify` | `forge/orchestrator.py`, `.claude/agents/` | Spec Kit (`Specify→Plan→Tasks→Implement`), BMAD (role agents) |
| **EARS-notation acceptance criteria** (`AC-N`, "When… the system shall…") | `forge/work/spec.md` | AWS Kiro |
| **Explicit Tasks decomposition stage** with backward (AC) + forward (test) links | `.claude/agents/planner.md`, `forge/work/tasks.md` | Spec Kit Tasks, Kiro Tasks-with-tracking |
| **Role agents with restricted toolsets + model tiers** (Architect=opus, Implementer/Reviewer=sonnet, Verifier=haiku) | `.claude/agents/*.md` | BMAD specialist roles; SWE-agent ACI minimalism (each agent gets only the tools it needs) |
| **Adversarial review as a non-optional gate** | `.claude/agents/adversarial-reviewer.md`, `gate_review()` | The eval culture of SWE-bench (a claim is worth nothing until a test reproduces it) |
| **OpenTelemetry GenAI spans** (`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.request.model`, `gen_ai.usage.*`) | `forge/observability.py` | Langfuse / OpenLLMetry / OTel GenAI semantic conventions |
| **Self-correcting state via reconciliation** (ADD/UPDATE/DELETE/NOOP mental model for what the pipeline learns) | `forge/spawn-log.jsonl`, trajectory | Mem0's memory reconciliation; ACE Generator→Reflector→Curator loop |
| **Verify-everything gate** (tests + coverage + ruff + mypy --strict + bandit, run by the agent itself) | `.claude/agents/verifier.md`, `gate_verify()` | The "no green, no merge" discipline of all serious harnesses |
| **Content-addressed, hash-chained attestations** for provenance | `forge/attestation.py`, `forge/attestations/` | in-toto / SLSA / sigstore supply-chain attestation, applied to agents |

---

## 3. Where agent-forge goes beyond the field (the intelligent delta)

Adopting the best of the field gets you parity. These are the places agent-forge deliberately does
something the named leaders do **not**, because the leaders optimise a different objective.

### 3.1 Machine-checked spec↔task↔test traceability (the headline)
Spec Kit, Kiro and BMAD give you the *workflow* (Spec → Plan → Tasks → Implement) but they verify
the spec→code link with **human review**, if at all. Drift, dropped requirements and tasks that
point at tests nobody wrote are caught late or never. agent-forge makes the link a CI gate:
`forge/traceability.py` asserts that **every** `AC-N` in the spec is covered by a task and **every**
task's `verified_by:` selector is a test that actually exists. Exit non-zero otherwise. This is a
bijection check on requirements↔tests that, as far as we can find, no open SDD tool enforces
mechanically. Run it: `make trace`.

### 3.2 Provenance and observability are the *same* record
The field treats "who did this" (git authorship) and "what happened" (telemetry) as two disconnected
systems. agent-forge fuses them: every OTel GenAI span carries a `forge.provenance` attribute naming
the accountable agent identity, and that identity is the same string that appears as the git author
(`* <implementer@agent-forge.bot>`). Observability you can *audit*, not just watch.

### 3.2b Tamper-evident agent provenance (in-toto / SLSA, applied to agents — the new headline)
This is the sharpest version of the whole thesis. The naive provenance check — "the git author email
ends in `@agent-forge.bot`" — is **spoofable in one line** (`git config user.email
implementer@agent-forge.bot`). A label is not a proof. So agent-forge brings the discipline of
software-supply-chain attestation (in-toto, SLSA, sigstore), standard in CI/CD but **absent from the
agentic-dev field**, to the question of *which agent produced which artifact*.

`forge/attestation.py` records each stage as an **attestation** committing to the SHA-256 of every
artifact it read and produced, the agent identity, the model, the gate verdict, and the digest of the
previous attestation — a **hash chain**, the same append-only immutable structure the inner project's
double-entry ledger demonstrates. The meta-layer is now *itself* a tamper-evident ledger of agent
actions. Verification (`make attest`) re-reads the artifacts from the working tree, recomputes every
digest, walks the chain, and confirms the head matches the anchor committed to git. Edit any attested
file after the fact, fabricate a stage that never ran, or reorder history, and verification fails **at
the exact broken link**. No keys, no shared secret: integrity is keyless, anchored by git. This is the
difference between "trust me, an agent did it" and "here is a tamper-evident proof; try to forge it and
the chain tells you where you lied." `hooks/audit_provenance.py` enforces both the email convention
*and* the chain, in CI. Honesty constraint: the chain attests **only** the genuinely agent-authored
inner project (verifiable in `git log`); the operator-authored meta-layer is excluded, because a
cryptographically-signed false authorship claim would be the very dishonesty this is built to defeat.

### 3.3 Gates with teeth, proven by a red commit
OpenHands and friends are judged on a pass rate. agent-forge additionally proves its *review stage
adds value*: the canonical run contains a deliberately **failing** commit (`708ee99`, `GATE: FAIL`)
where the AdversarialReviewer reproduced a real `SQLITE_BUSY` concurrency leak, followed by the fix
(`49fa176`). The red commit is the evidence. A decorative gate that always says "looks fine" is, by
construction, rejected by the pipeline. See `AUTONOMOUS-TRAJECTORY.md`.

### 3.4 Falsifiability over capability theatre
The inner project is scoped to the single hardest *correctness* problem (exactly-once money movement
under retry storms), not the broadest feature surface. A reviewer can hold the whole correctness
model in their head and check it in five minutes. The bet: a small artifact whose every claim is
verifiable beats a large one whose claims are merely asserted.

---

## 4. Honest gaps (what the leaders have that we do not, yet)

Auditability is not a free lunch; here is the bill, tracked in `state/tech-debt-backlog.md`.

- **No sandboxed runtime.** OpenHands runs agents in Docker with a real browser/jupyter/bash
  surface. agent-forge runs sub-agents in-process with restricted tools. For untrusted or
  long-horizon tasks, a sandbox (OpenHands-style or git-worktree isolation) is the next module.
- **No standing eval harness.** We have one inner project, not a SWE-bench/terminal-bench-style
  battery the pipeline is scored against run-over-run. Building `forge/eval/` (N mandates → run →
  score gate-pass + provenance + coverage) is the highest-leverage next step to make "the pipeline
  works" itself falsifiable.
- **No persistent cross-run memory.** Mem0/Letta give agents a curated, self-editing memory. Our
  spawn-log is append-only audit, not a reconciled playbook. An ACE-style Generator→Reflector→Curator
  module (`forge/memory.py`) with Mem0's ADD/UPDATE/DELETE/NOOP semantics is designed and deferred.
- **Single inner project.** The pipeline is reusable in principle; it has produced one artifact in
  practice. A second, different mandate is the cheapest proof of reusability.

The discipline this repo commits to: these gaps are written down and dated, not hidden. A gap you can
read in the tech-debt log is a roadmap; a gap you discover in production is a liability.

---

## 5. Sources

- OpenHands vs SWE-agent comparisons and SWE-bench Verified scores, 2026 (CodeSOTA, ToolHalla,
  MarkTechPost).
- GitHub Spec Kit (`github/spec-kit`) and AWS Kiro spec-driven development docs; Martin Fowler,
  "Understanding Spec-Driven Development" (2026).
- BMAD-METHOD (`bmad-code-org/BMAD-METHOD`) docs.
- LangGraph / CrewAI / Microsoft Agent Framework 2026 framework comparisons (Langfuse, Uvik, Alice
  Labs).
- Letta (ex-MemGPT) and Mem0 memory-architecture comparisons, 2026 (Dev Genius, TokenMix, Vectorize).
- OpenTelemetry GenAI semantic conventions (`gen_ai.*`); Langfuse and OpenLLMetry observability
  guides, 2026.
