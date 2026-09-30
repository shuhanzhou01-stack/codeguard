# Evaluation harness — synthetic smoke result, not a benchmark

This file records a historical run of the Review Engine evaluation harness on `evaluation/datasets/fixture.jsonl`. It has **one labelled synthetic case**, uses `fake / codeguard-fake-v1`, and predates the v1.2.0 Evidence Registry. It verifies that the harness can parse a fixture and calculate metrics; it does **not** measure real-model quality or full-platform performance.

| Historical fixture field | Observed value |
| --- | --- |
| Prompt / dataset version | `codeguard-review-v1.1.1` / `dataset-v1` |
| Run status | `completed` |
| Cases / labelled cases | 1 / 1 |
| Fixture precision / recall / F1 | 1.0 / 1.0 / 1.0 |
| Fixture file / line / severity accuracy | 1.0 / 1.0 / 1.0 |
| Fixture test classification | 1.0 |
| Model tokens / cost | 0 / 0 (fake provider) |

The metrics above are expected for the controlled fixture and have no statistical meaning. The unlabelled `evaluation/datasets/real_world_template.jsonl` correctly reports `insufficient_labelled_cases`; it must not be presented as real-world accuracy. For the two real GitHub PR validation cases and their limitations, see [REAL_WORLD_VALIDATION.md](docs/REAL_WORLD_VALIDATION.md). A larger human-labelled dataset is still needed before making benchmark claims.
