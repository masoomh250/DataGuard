from typing import Iterable
from .models import CheckResult

DEFAULT_WEIGHTS = {"CRITICAL": 4.0, "HIGH": 3.0, "MEDIUM": 2.0, "LOW": 1.0, "INFO": 0.0}


def calculate_score(results: Iterable[CheckResult], weights: dict[str, float] | None = None) -> float:
    weights = weights or DEFAULT_WEIGHTS
    applicable = [r for r in results if r.status != "SKIP" and weights.get(r.severity, 1.0) > 0]
    if not applicable:
        return 100.0
    total = sum(weights.get(r.severity, 1.0) for r in applicable)
    passed = sum(weights.get(r.severity, 1.0) for r in applicable if r.status == "PASS")
    return round((passed / total) * 100, 2)


def status_counts(results: Iterable[CheckResult]) -> dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts
