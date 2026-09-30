# X / Twitter drafts — do not post automatically

Use a real architecture diagram or real PR-validation screenshot only after checking that it contains no credentials, local paths, or private environment details.

## Short launch post

I built CodeGuard after a 7B model spotted PR issues but cited the wrong evidence. Ruff/Bandit + BASE/HEAD tests now create evidence IDs; the LLM selects IDs, and code resolves the real file/line. Two demo PRs, not a benchmark. https://github.com/shuhanzhou01-stack/codeguard

## Five-post thread

**1/5** A local 7B model could recognize a PR's shell risk and regression, but sometimes cited the wrong file, line, or rule. I didn't want a more fluent answer to be mistaken for verified evidence.

**2/5** CodeGuard checks the PR first: pinned BASE/HEAD commits, Ruff + Bandit on both revisions, and Docker-isolated pytest runs. A HEAD failure counts as an introduced regression only when the corresponding BASE test passed.

**3/5** Deterministic diff, static, and test evidence gets IDs in an Evidence Registry. The LLM returns structured findings with `evidence_ids`; CodeGuard resolves the authoritative file/line/rule/test metadata. Unknown IDs fail grounding.

**4/5** In two controlled public demo PRs with Ollama + `qwen2.5-coder:7b`, the clean case had 0 findings; the intentional failure case produced 2 findings, both fully grounded. These cases are not a statistical benchmark.

**5/5** It's an open-source review aid, not an autonomous patch generator. I'd value feedback on grounding, BASE/HEAD comparison, and setup. Repo: https://github.com/shuhanzhou01-stack/codeguard Demo: https://github.com/shuhanzhou01-stack/codeguard-demo
