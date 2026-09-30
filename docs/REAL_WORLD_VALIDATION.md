# CodeGuard real-world validation (pre-v1.2.0)

Initial deterministic validation was performed 2026-09-29; real Ollama validation followed on 2026-09-30 (Asia/Shanghai) against [shuhanzhou01-stack/codeguard-demo](https://github.com/shuhanzhou01-stack/codeguard-demo). GitHub comment publishing was disabled throughout. No PR, review, or comment was changed.

## Environment and execution

Windows host; Python 3.13.2 in the existing `.venv`; Docker Engine 29.7.2; PostgreSQL 17; Redis 7; Ruff 0.16.4; Bandit 1.9.4. The Compose API, Celery worker, Alembic migration, PostgreSQL, and Redis services started. The isolated test runner image was built from `Dockerfile.test-runner`.

The project's fine-grained PAT was loaded. CodeGuard read the repository, PR #1/#2, changed-file lists, PR diffs, pinned compare diffs, and BASE/HEAD source tarballs. The in-process `AnalysisPipeline` and the live FastAPI → Redis → Celery → PostgreSQL path both completed for each PR and produced the same static and test deltas. HTTP submission returned 202; persisted reports were retrieved through the API.

The full suite ran with `CODEGUARD_TEST_DATABASE_URL` set to the running PostgreSQL database and `CODEGUARD_RUN_DOCKER_TESTS=1`. The final post-registry result was **86 passed, 0 skipped, 0 failed, 1 warning**. The formerly skipped PostgreSQL test and two Docker integration tests executed. The warning is a Starlette deprecation notice concerning `httpx` in `fastapi.testclient`. Repository hygiene and Ruff passed. Bandit passed with a non-failing warning about an existing `# nosec B108` annotation.

## PR #1 — clean case

PR: [codeguard-demo#1](https://github.com/shuhanzhou01-stack/codeguard-demo/pull/1)

| Evidence | Observed result |
| --- | --- |
| Pinned BASE | `db0668c7e430a25709eae5ffc2b01972fd410e5b` |
| Pinned HEAD | `e3b1548497173b4af842df0ea1cd17ed91f81bc3` |
| Changed files | `README.md`, `demo_app/calculator.py`, `tests/test_calculator.py` |
| PR and compare diff | Both fetched; 1,613 UTF-8 bytes each |
| Ruff / Bandit | Both completed on both revisions; zero findings |
| BASE Docker pytest | `tests_passed`; 11 passed, zero failed |
| HEAD Docker pytest | `tests_passed`; 13 passed, zero failed |
| BASE/HEAD delta | Zero introduced static findings; zero test regressions; `no_regression` |
| Original fake-provider report | Zero findings; risk `none`; publishing `disabled` |

The added tests passed on HEAD. The original 2026-09-29 report used the fake provider. The pre-registry real-model run 11 produced zero findings. With the final evidence-ID registry, **real-model run 16** again produced parseable JSON and zero findings, risk `none`, with 2,631 input and 42 output tokens in 42.3 seconds. Its four prompt-visible evidence entries were persisted despite there being no findings. There were no unsupported findings or observed false positives in this single clean case. Zero findings make grounding vacuously uneventful, not evidence of a measured false-positive rate.

## PR #2 — intentional failure case

PR: [codeguard-demo#2](https://github.com/shuhanzhou01-stack/codeguard-demo/pull/2)

| Evidence | Observed result |
| --- | --- |
| Pinned BASE | `db0668c7e430a25709eae5ffc2b01972fd410e5b` |
| Pinned HEAD | `069869761022a8f4101d10afc6681cf7b90bfd16` |
| Changed files | `demo_app/calculator.py`, `demo_app/command_utils.py` |
| PR and compare diff | Both fetched; 949 UTF-8 bytes each |
| Ruff | Completed on both revisions; zero findings |
| Bandit | BASE zero; HEAD one introduced high-severity `B602` at `demo_app/command_utils.py:7` (`subprocess.run(..., shell=True)`) |
| BASE Docker pytest | `tests_passed`; 11 passed, zero failed |
| HEAD Docker pytest | `tests_failed`; 9 passed, 2 failed |
| BASE/HEAD delta | `new_failure`; 2 confirmed BASE-pass-to-HEAD-fail regressions; 1 introduced static finding |
| Original fake-provider report | Zero findings; risk `none`; publishing `disabled` |

The confirmed regressions are `tests.test_calculator::test_calculate_discount` and `tests.test_calculator::test_calculate_full_discount`. Both passed on BASE and failed on HEAD. At `demo_app/calculator.py:14`, the changed formula adds the discount instead of subtracting it. In the full-discount test, HEAD returned `160.0` where approximately `0` was expected.

The evidence and delta are correct in both the in-process and Celery-driven runs. The original 2026-09-29 fake-provider report emitted no findings despite the evidence. Real-model runs then exposed a different quality problem:

- Run 8: the worker had correctly loaded `ollama / qwen2.5-coder:7b`, but the PR #2 model call hit CodeGuard's former fixed 120-second read timeout. The run failed at `llm_review`; it did **not** fall back to fake or create a success report.
- Run 9: after increasing only the Ollama read timeout to 300 seconds, real JSON parsing succeeded. The model produced one high-severity shell-execution finding and one regression finding in 143.2 seconds. Grounding was zero full, one partial, one ungrounded.
- Run 10: after a generic prompt clarification about exact machine-matchable evidence identifiers and changed-source-file locations, parsing again succeeded. The model emitted three findings in 148.5 seconds (2,541 input / 631 output tokens): one high-severity security item at `demo_app/command_utils.py:7` and two high-severity regression items, one per failing test. The security item's structured static identifier combined `demo_app/command_utils.py:7, B602` into one string instead of an exact rule ID; strict validation marked it **ungrounded**. Both regression items named the unchanged `tests/test_calculator.py` as the finding location; the validator cleared their unsupported line numbers and marked them **partially grounded**. The two regression findings also describe the same underlying calculator defect separately. No invented file or unrelated vulnerability was observed, but their attribution is not release-quality. Publishing remained disabled.

For these two real-world cases only, the model's **semantic issue-category recall** on PR #2 was 2/2 (shell risk and discount regression), while **fully grounded expected-issue recall was 0/2**. The final PR #2 full-grounding rate was **0/3** findings (two partial, one ungrounded). These figures are case observations, not a statistical benchmark. Automated provider retries were zero; runs 9 and 10 were explicitly initiated diagnostic reruns. Completed runs had zero JSON/schema parse failures. The run-8 timeout was a transport failure, not a parse failure.

## Deterministic evidence-ID registry follow-up

The earlier free-form citation failure motivated a provider- and repository-independent registry. CodeGuard now assigns deterministic per-run IDs to changed diff hunks, HEAD static findings (with introduced/existing classification), confirmed BASE-pass-to-HEAD-fail tests, and other observed HEAD test failures. The complete prompt-visible registry is persisted with the report, including clean cases. Registry entries retain tool/rule/severity, test outcomes, changed-hunk relationships, and locations. The model's strict proposal schema contains `evidence_ids` but **no model-owned file, line, or citation fields**. Unknown IDs fail grounding. CodeGuard resolves report locations and evidence from registry entries, keeps the existing path/rule/diff validator for source-located findings, and allows a proven regression to remain explicitly test-level when a source location cannot be established. It merges separate regression proposals only when they resolve to the same uniquely associated changed function hunk; uncertain root causes remain separate. Offline fixture IDs are derived dynamically, not fixed to an ID ordering.

In the final **real Ollama** runs, prompt `codeguard-review-v1.2.0-evidence-registry`:

| Case | Model findings | Fully grounded | Unsupported / false positive | Parse / automatic retry |
| --- | ---: | ---: | ---: | ---: |
| PR #1, run 16 | 0 | 0 (no findings) | 0 / 0 observed | 0 / 0 |
| PR #2, run 17 | 2 | 2 | 0 / 0 observed | 0 / 0 |

PR #2's security finding selected **E003**. The persisted resolved entry is an **introduced Bandit B602**, high severity, at `demo_app/command_utils.py:7`, related to changed diff hunk E002. The model did not provide that location or rule as structured output. The regression finding selected **E004 and E005**, the exact two tests recorded as BASE passed and HEAD failed. Both registry entries are linked by a conservative Python AST check (direct test import/call and one changed function hunk) to `calculate_discount` in `demo_app/calculator.py:14`, diff hunk E001. The stored test file is `tests/test_calculator.py`, but it is **not** used as the source finding location. The final report persisted all five registry entries, both findings, and their resolved metadata in PostgreSQL; GitHub publishing remained disabled. Model input/output tokens were 2,659/304, and its request took 85.6 seconds.

Compared with the pre-registry run 10, PR #2 changed from three findings with **0/3 fully grounded** (one invalid free-form rule citation and two partial test-file locations) to two findings with **2/2 fully grounded** and no duplicate regression finding. Expected issue-category recall stayed **2/2**; fully grounded expected-issue recall improved from **0/2 to 2/2**. This is a two-case real-world validation, **not** a statistical benchmark or proof that model prose can never contain an unsupported inference. ID membership and resolved metadata are deterministic; interpretation in title/description still warrants human review on broader repositories.

The source-linking check is intentionally conservative: it recognizes direct Python test imports/calls that map to one changed function hunk. Other languages, indirect calls, or ambiguous hunks remain test-level evidence with no invented source line. An oversized registry fails the prompt budget check rather than silently hiding IDs; large-PR handling is a known follow-up.

## Historical pre-registry structured-output check

A separate **scripted pre-registry response**, based on PR #2's real diff, Bandit result, and test delta, was evaluated without calling an LLM. Under the former free-form citation schema it contained a high-severity security finding at `demo_app/command_utils.py:7` with `diff` and introduced `B602` references, and a medium-severity correctness finding at `demo_app/calculator.py:14` with `diff` and both exact regression test identifiers. That historical response parsed and both findings were `grounded`. Its model-owned location/citation fields are **not** accepted by the current v1.2.0 proposal schema; the current real-model results are runs 16 and 17 above.

That scripted check showed the earlier validator could accept correct references; it was not evidence of model reliability. The later real-model runs test the Evidence Registry contract.

## Real LLM configuration, compatibility and limitation

The 2026-09-29 initial run used `fake`. For the follow-up, the user's `.env` already contained `LLM_PROVIDER=ollama`, `LLM_MODEL=qwen2.5-coder:7b`, and `LLM_API_BASE=http://host.docker.internal:11434/v1`; these values were not edited. The host process read them, but the still-running Celery container initially retained the old fake environment. Recreating the API/worker containers loaded the new values. The worker then reached Ollama's `/v1/models` endpoint (HTTP 200) and saw the target model. A minimal **real request from inside the worker** produced JSON accepted by CodeGuard's JSON parser and Pydantic report schema, with no retry. No real-provider failure triggered a fake fallback.

`ollama ps` reported the model running **100% on CPU** with a 4,096-token active context, despite the machine having an RTX 4060 8 GB. The final PR prompt inputs were 2,323 and 2,541 tokens, so an overlong input was not demonstrated. Slow local generation caused the initial 120-second PR #2 timeout; an Ollama-only 300-second read timeout resolved that transport failure without changing other providers. Hardware acceleration was not repaired or verified in this validation. More importantly, the 7B model still failed exact evidence-reference and location instructions after a generic prompt improvement. The grounding rules were not weakened and no demo-specific answer was inserted.

## Production-only coverage

Coverage selected `analysis/`, `context/`, `execution/`, `llm/`, `security/`, and the root API/service/model modules. It excluded tests, Alembic migrations, `demo.py`, generated files, and caches. Final result: **83%** (2,271 statements; 382 missed), with all 86 tests passing.

Lowest-coverage operational modules: `github_client.py` 33%, `tasks.py` 34%, `main.py` 62%, `analysis/static_analyzer.py` 72%, and `llm/client.py`/`database.py`/`repo_manager.py`/`services.py` 73–74%. Focused tests of API errors and permissions, GitHub pagination, real-provider responses, and task failure behavior would improve confidence. The three-line `context/pr_context.py` compatibility re-export is at 0%; older `models.py` and `test_runner.py` compatibility paths are at 30% and 55%. Trivial tests of those paths solely to raise the headline percentage would add little assurance for the release-critical workflow.

## Security check and fixes

- `.env` remains ignored and untracked; all eight credential fields in `.env.example` are blank. The local `.env` was preserved.
- All 90 reachable Git blob objects were checked for current `.env` secret values, common GitHub/OpenAI/AWS token shapes, and private-key headers; there were no hits. Pattern-based scanning cannot rule out every unknown credential.
- The built worker image did not contain `/app/.env`; exact local secret values were absent from captured API and worker logs.
- Before this validation, `create_repository_archive` could copy a target repository's `.env` or key file into the Docker test workspace. It now fails closed on `.env` variants, common credential filenames, and private-key/certificate suffixes; `.env.example` remains allowed. Five focused tests cover these behaviors.
- Before this validation, raw PR evidence was sent directly to a configured external LLM. The review engine now applies central secret redaction before provider transmission, including private-key blocks. Focused tests cover prompt and key redaction.
- File and value filtering are pattern based. Unrecognized secret names or credential formats in ordinary source files may still require an additional dedicated secret scan before processing hostile repositories.

## Release decision

**Ready to enter v1.2.0 release packaging, not a general production-quality claim.** GitHub read access, Docker/PostgreSQL integration, the live service path, static scanning, regression comparison, real Ollama connectivity, strict proposal parsing, and evidence-ID resolution passed on both pinned cases. PR #1 remained clean; PR #2's two expected issue categories were detected and both final findings were fully grounded to deterministic metadata. The earlier failed/partial runs remain documented above. Broader PRs, semantic accuracy of free-text interpretations, and CPU-only Ollama performance are known follow-up validation areas before describing CodeGuard as broadly reliable.
