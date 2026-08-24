from __future__ import annotations

from evaluation.dataset import EvaluationCase, GroundTruthFinding
from llm.models import ReviewFinding


def _normalize_path(value: str | None) -> str:
    return (value or "").strip().replace("\\", "/").removeprefix("./").lower()


def _overlaps(
    predicted_start: int | None,
    predicted_end: int | None,
    expected_start: int | None,
    expected_end: int | None,
) -> bool:
    if expected_start is None:
        return True
    if predicted_start is None:
        return False
    predicted_end = predicted_end or predicted_start
    expected_end = expected_end or expected_start
    return predicted_start <= expected_end and expected_start <= predicted_end


def _candidate_score(
    predicted: ReviewFinding, expected: GroundTruthFinding
) -> int | None:
    if _normalize_path(predicted.file_path) != _normalize_path(expected.file_path):
        return None
    if predicted.category.strip().lower() != expected.category.strip().lower():
        return None
    searchable = (
        f"{predicted.title} {predicted.description} {predicted.evidence}"
    ).lower()
    if expected.line_start is not None:
        if not _overlaps(
            predicted.line_start,
            predicted.line_end,
            expected.line_start,
            expected.line_end,
        ):
            return None
        evidence_score = 30
    else:
        keywords = [item.strip().lower() for item in expected.match_keywords if item.strip()]
        if not keywords or not any(keyword in searchable for keyword in keywords):
            return None
        evidence_score = 20 + sum(keyword in searchable for keyword in keywords)
    severity_score = int(
        predicted.severity.strip().lower() == expected.severity.strip().lower()
    )
    return 100 + evidence_score + severity_score


def match_findings(
    predictions: list[ReviewFinding], expected: list[GroundTruthFinding]
) -> dict:
    candidates: list[tuple[int, int, int]] = []
    for prediction_index, prediction in enumerate(predictions):
        for expected_index, expected_finding in enumerate(expected):
            score = _candidate_score(prediction, expected_finding)
            if score is not None:
                candidates.append((score, prediction_index, expected_index))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_predictions: set[int] = set()
    used_expected: set[int] = set()
    pairs: list[dict[str, int]] = []
    for score, prediction_index, expected_index in candidates:
        if prediction_index in used_predictions or expected_index in used_expected:
            continue
        used_predictions.add(prediction_index)
        used_expected.add(expected_index)
        pairs.append(
            {
                "predicted_index": prediction_index,
                "expected_index": expected_index,
                "score": score,
            }
        )
    return {
        "matched_tp_pairs": pairs,
        "unmatched_fp_indices": [
            index for index in range(len(predictions)) if index not in used_predictions
        ],
        "unmatched_fn_indices": [
            index for index in range(len(expected)) if index not in used_expected
        ],
    }


def finding_matches(
    predicted: ReviewFinding, expected: GroundTruthFinding
) -> bool:
    return _candidate_score(predicted, expected) is not None


def calculate_metrics(
    rows: list[tuple[EvaluationCase, list[ReviewFinding], dict]],
) -> dict[str, float | int | None]:
    true_positives = 0
    predicted_total = 0
    expected_total = 0
    severity_correct = 0
    matched_total = 0
    expected_files_total = 0
    localized_files = 0
    lined_findings = 0
    line_hits = 0
    test_cases = 0
    test_correct = 0
    analysis_latency = 0
    llm_latency = 0
    token_usage = 0
    estimated_cost = 0.0
    cost_observation_count = 0
    labelled_case_count = 0

    for case, predictions, telemetry in rows:
        if case.labelled:
            labelled_case_count += 1
            matching = match_findings(predictions, case.ground_truth_findings)
            pairs = matching["matched_tp_pairs"]
            true_positives += len(pairs)
            predicted_total += len(predictions)
            expected_total += len(case.ground_truth_findings)
            matched_total += len(pairs)
            severity_correct += sum(
                predictions[pair["predicted_index"]].severity.lower()
                == case.ground_truth_findings[pair["expected_index"]].severity.lower()
                for pair in pairs
            )

            predicted_files = {
                _normalize_path(item.file_path) for item in predictions if item.file_path
            }
            expected_files_total += len(case.expected_files)
            localized_files += sum(
                _normalize_path(path) in predicted_files for path in case.expected_files
            )

            for expected in case.ground_truth_findings:
                if expected.line_start is None:
                    continue
                lined_findings += 1
                if any(
                    _normalize_path(prediction.file_path)
                    == _normalize_path(expected.file_path)
                    and _overlaps(
                        prediction.line_start,
                        prediction.line_end,
                        expected.line_start,
                        expected.line_end,
                    )
                    for prediction in predictions
                ):
                    line_hits += 1

            if case.expected_test_status is not None:
                test_cases += 1
                test_correct += int(
                    telemetry.get("test_status") == case.expected_test_status
                )

        analysis_latency += int(telemetry.get("analysis_latency_ms") or 0)
        llm_latency += int(telemetry.get("llm_latency_ms") or 0)
        token_usage += int(telemetry.get("token_usage") or 0)
        if telemetry.get("estimated_cost") is not None:
            cost_observation_count += 1
            estimated_cost += float(telemetry["estimated_cost"])

    precision = (
        true_positives / predicted_total
        if predicted_total
        else (1.0 if expected_total == 0 and labelled_case_count else 0.0)
    )
    recall = (
        true_positives / expected_total
        if expected_total
        else (1.0 if labelled_case_count else 0.0)
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    count = len(rows)
    return {
        "case_count": count,
        "labelled_case_count": labelled_case_count,
        "finding_precision": precision if labelled_case_count else None,
        "finding_recall": recall if labelled_case_count else None,
        "finding_f1": f1 if labelled_case_count else None,
        "file_localization_accuracy": (
            localized_files / expected_files_total if expected_files_total else None
        ),
        "line_range_hit_rate": line_hits / lined_findings if lined_findings else None,
        "severity_classification_accuracy": (
            severity_correct / matched_total if matched_total else None
        ),
        "test_pass_fail_classification": (
            test_correct / test_cases if test_cases else None
        ),
        "mean_analysis_latency_ms": analysis_latency / count if count else None,
        "mean_llm_latency_ms": llm_latency / count if count else None,
        "total_token_usage": token_usage,
        "cost_observation_count": cost_observation_count,
        "total_estimated_cost": (
            estimated_cost if cost_observation_count else None
        ),
    }
