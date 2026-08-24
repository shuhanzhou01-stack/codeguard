from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.dataset import load_dataset
from evaluation.evaluator import create_evaluation_provider, evaluate_cases


def run_ablation(
    dataset: Path,
    output: Path,
    *,
    provider_name: str = "fake",
    model: str | None = None,
) -> dict:
    cases = load_dataset(dataset)
    provider = create_evaluation_provider(provider_name, model)
    variants = {
        "A_diff_only": evaluate_cases(
            cases,
            "diff_only",
            provider=provider,
            provider_name=provider_name,
            model_name=model,
        ).model_dump(),
        "B_diff_static": evaluate_cases(
            cases,
            "diff_static",
            provider=provider,
            provider_name=provider_name,
            model_name=model,
        ).model_dump(),
        "C_full_evidence": evaluate_cases(
            cases,
            "full_evidence",
            provider=provider,
            provider_name=provider_name,
            model_name=model,
        ).model_dump(),
    }
    labelled_case_count = sum(case.labelled for case in cases)
    payload = {
        "dataset": str(dataset),
        "provider": provider_name,
        "model": model or (
            "codeguard-fake-v1"
            if provider_name == "fake"
            else "configured-default"
        ),
        "labelled_case_count": labelled_case_count,
        "conclusion": (
            "No comparative conclusion: insufficient labelled cases."
            if labelled_case_count < 2
            else "Metrics are reported per evidence variant; interpret with dataset scope."
        ),
        "variants": variants,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CodeGuard evidence ablations")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--provider", default="fake")
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/results/ablation.json")
    )
    args = parser.parse_args()
    print(
        json.dumps(
            run_ablation(
                args.dataset,
                args.output,
                provider_name=args.provider,
                model=args.model,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
