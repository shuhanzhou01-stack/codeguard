# Reddit drafts — choose one suitable community and follow its rules

Do not cross-post both versions indiscriminately. Disclose that this is your own project and answer technical questions in the thread.

## A. r/LocalLLaMA-style: small local model and grounding

**Suggested title:** A 7B local model spotted PR issues but cited the wrong lines, so I moved citations out of the model

**Draft:**

I tested a local `qwen2.5-coder:7b` model (via Ollama) for GitHub PR review. It could identify a shell-execution risk and a test regression in a controlled demo PR, but its free-form file/line/rule citations were unreliable. Prompt tweaks alone did not make those fields safe to treat as facts.

I changed the interface instead of switching models. CodeGuard builds an Evidence Registry from the PR diff, Ruff/Bandit output, and BASE-vs-HEAD pytest results. Each entry gets an ID. The model can select `evidence_ids` in structured JSON, while the report resolves authoritative location and tool/test metadata from the registry. Unknown IDs fail grounding; ambiguous regressions stay at test level rather than being assigned a guessed source line.

On two real but controlled demo PRs, the clean case returned zero findings and the intentional failure case returned two final findings, both fully grounded. Two cases are not a benchmark, and this does not measure accuracy across other repos or models.

Code and architecture: https://github.com/shuhanzhou01-stack/codeguard
Run details: https://github.com/shuhanzhou01-stack/codeguard/blob/main/docs/REAL_WORLD_VALIDATION.md

I'm curious how others keep local-model explanations useful without letting the model invent authoritative citations.

## B. Open-source/software-engineering style: review reliability

**Suggested title:** Open-source PR verifier that checks BASE and HEAD before asking an LLM to explain findings

**Draft:**

I built CodeGuard because a failing test on a PR's HEAD is not necessarily an introduced regression, and a plausible LLM review is not necessarily supported by the diff. The pipeline pins both commits, runs Ruff/Bandit on each, executes tests in a Docker sandbox, and compares BASE/HEAD outcomes. Only then does a model propose findings against deterministic evidence IDs.

The final report resolves file, line, rule, and test metadata from CodeGuard's Evidence Registry. It rejects unknown IDs and does not invent a source location when a failing test cannot be reliably tied to a changed line. It is a review aid, not an autonomous patch generator.

The public demo repo has a clean PR and an intentional failure PR. In the documented Ollama/`qwen2.5-coder:7b` validation, the clean case had zero findings; the failure case detected the expected security and regression categories with 2/2 final findings grounded. This is a small validation set, not a statistical benchmark.

Repo: https://github.com/shuhanzhou01-stack/codeguard
Demo: https://github.com/shuhanzhou01-stack/codeguard-demo

Would love feedback on the BASE/HEAD design, grounding contract, or the developer setup.
