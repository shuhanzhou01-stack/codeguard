# Show HN draft — do not post automatically

**Title:** Show HN: CodeGuard – deterministic evidence grounding for AI PR review

**Text:**

I built CodeGuard after hitting a frustrating failure in LLM-assisted PR review: a local model could recognize the kind of problem in a change, but it sometimes cited the wrong file, line, or tool result. Switching to a larger model might have hidden some errors, but it would not make citations authoritative.

CodeGuard now runs deterministic checks first. It pins a GitHub PR's BASE and HEAD commits, runs Ruff and Bandit on both revisions, and compares Docker-isolated pytest results to identify BASE-pass/HEAD-fail regressions. Diff hunks, static findings, and test results go into an Evidence Registry with IDs. The LLM returns structured findings that select those IDs; CodeGuard resolves file, line, rule, and test metadata from the registry and rejects unknown IDs. If a regression cannot be reliably tied to a source line, the report keeps test-level evidence instead of inventing one.

I validated the current path with Ollama and `qwen2.5-coder:7b` on two controlled PRs in a separate public demo repository. The clean PR produced zero findings; the intentional failure PR produced two fully grounded findings covering the expected security and regression categories. These are controlled validation cases, not a statistical benchmark or a claim of universal model accuracy.

Repo: https://github.com/shuhanzhou01-stack/codeguard
Validation details: https://github.com/shuhanzhou01-stack/codeguard/blob/main/docs/REAL_WORLD_VALIDATION.md
Demo PRs: https://github.com/shuhanzhou01-stack/codeguard-demo

I'd appreciate feedback on the evidence-ID contract, conservative regression-to-source linking, and whether the setup is clear enough for another developer to try.
