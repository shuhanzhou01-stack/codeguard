from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.dataset import load_dataset
from evaluation.evaluator import create_evaluation_provider, evaluate_cases


def write_benchmark_markdown(payload: dict, path: Path) -> None:
    if payload["status"] == "No evaluation cases configured.":
        content = (
            "# CodeGuard Review Engine Evaluation Harness\n\n"
            "No evaluation cases configured. No quality conclusion is available.\n"
        )
    else:
        metrics = payload["metrics"]
        rows = [
            "# CodeGuard Review Engine Evaluation Harness",
            "",
            f"Dataset: `{payload['dataset']}`",
            f"Provider/model: `{payload['provider']}` / `{payload['model']}`",
            f"Prompt/dataset version: `{payload['prompt_version']}` / `{payload['dataset_version']}`",
            f"Status: `{payload['status']}`",
            "",
            "This is a Review Engine Evaluation Harness, not a full CodeGuard end-to-end benchmark. Fixture/synthetic datasets are smoke tests; real labelled datasets remain future validation work.",
            "Matching is deterministic and one-to-one: normalized file and category must match; labelled lines must overlap; labels without lines require match_keywords in prediction text/evidence.",
            "Unlabelled cases report insufficient_labelled_cases and never receive fabricated ground truth.",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
        ]
        rows.extend(f"| {name} | {value} |" for name, value in metrics.items())
        content = "\n".join(rows) + "\n"
    path.write_text(content, encoding="utf-8")


def run_benchmark(
    dataset: Path,
    output: Path,
    markdown: Path,
    *,
    provider_name: str = "fake",
    model: str | None = None,
) -> dict:
    provider = create_evaluation_provider(provider_name, model)
    result = evaluate_cases(
        load_dataset(dataset),
        provider=provider,
        provider_name=provider_name,
        model_name=model,
    )
    payload = {
        "benchmark": "CodeGuard Review Engine Evaluation Harness",
        "dataset": str(dataset),
        **result.model_dump(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    write_benchmark_markdown(payload, markdown)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the CodeGuard Review Engine Evaluation Harness"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--provider",
        default="fake",
        help="fake (default, no cost) or an explicitly configured real provider",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--output", type=Path, default=Path("evaluation/results/benchmark.json")
    )
    parser.add_argument("--markdown", type=Path, default=Path("BENCHMARK.md"))
    args = parser.parse_args()
    payload = run_benchmark(
        args.dataset,
        args.output,
        args.markdown,
        provider_name=args.provider,
        model=args.model,
    )
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
