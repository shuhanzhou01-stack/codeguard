from __future__ import annotations

from abc import ABC, abstractmethod

from evaluation.dataset import EvaluationCase


class EvaluationAdapter(ABC):
    @abstractmethod
    def load_cases(self) -> list[EvaluationCase]:
        raise NotImplementedError


class SWEbenchAdapter(EvaluationAdapter):
    """Future regression-verification adapter, not a resolved-rate evaluator."""

    def load_cases(self) -> list[EvaluationCase]:
        raise NotImplementedError(
            "SWE-bench data/setup is not bundled. This adapter cannot produce scores."
        )
