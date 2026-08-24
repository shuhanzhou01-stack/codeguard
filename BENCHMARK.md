# CodeGuard Review Engine Evaluation Harness

Dataset: `evaluation\datasets\fixture.jsonl`
Provider/model: `fake` / `codeguard-fake-v1`
Prompt/dataset version: `codeguard-review-v1.1.1` / `dataset-v1`
Status: `completed`

This is a Review Engine Evaluation Harness, not a full CodeGuard end-to-end benchmark. Fixture/synthetic datasets are smoke tests; real labelled datasets remain future validation work.
Matching is deterministic and one-to-one: normalized file and category must match; labelled lines must overlap; labels without lines require match_keywords in prediction text/evidence.
Unlabelled cases report insufficient_labelled_cases and never receive fabricated ground truth.

| Metric | Value |
| --- | ---: |
| case_count | 1 |
| labelled_case_count | 1 |
| finding_precision | 1.0 |
| finding_recall | 1.0 |
| finding_f1 | 1.0 |
| file_localization_accuracy | 1.0 |
| line_range_hit_rate | 1.0 |
| severity_classification_accuracy | 1.0 |
| test_pass_fail_classification | 1.0 |
| mean_analysis_latency_ms | 0.0 |
| mean_llm_latency_ms | 0.0 |
| total_token_usage | 0 |
| cost_observation_count | 1 |
| total_estimated_cost | 0.0 |
