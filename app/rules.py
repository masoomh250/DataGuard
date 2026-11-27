import re
from typing import Any

import pandas as pd

from .models import CheckResult

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _severity(rule: dict[str, Any], default: str = "MEDIUM") -> str:
    return str(rule.get("severity", default)).upper()


def check_required(df: pd.DataFrame, column: str, rule: dict[str, Any]) -> CheckResult:
    if column not in df.columns:
        return CheckResult(f"Required Check: {column}", "FAIL", "CRITICAL", "Column not found")
    nulls = int(df[column].isna().sum())
    return CheckResult(
        f"Required Check: {column}",
        "PASS" if nulls == 0 else "FAIL",
        _severity(rule, "HIGH"),
        "No null values found" if nulls == 0 else f"{nulls:,} null values found",
        {"null_count": nulls},
    )


def check_unique(df: pd.DataFrame, column: str, rule: dict[str, Any]) -> CheckResult:
    if column not in df.columns:
        return CheckResult(f"Unique Check: {column}", "FAIL", "CRITICAL", "Column not found")
    duplicate_values = int(df[column].duplicated().sum())
    affected_rows = int(df[column].duplicated(keep=False).sum())
    return CheckResult(
        f"Unique Check: {column}",
        "PASS" if duplicate_values == 0 else "FAIL",
        _severity(rule, "HIGH"),
        "All values are unique" if duplicate_values == 0 else f"{duplicate_values:,} duplicate values found",
        {"duplicate_values": duplicate_values, "affected_rows": affected_rows},
    )


def check_type(df: pd.DataFrame, column: str, expected_type: str, rule: dict[str, Any]) -> CheckResult:
    if column not in df.columns:
        return CheckResult(f"Type Check: {column}", "FAIL", "CRITICAL", "Column not found")
    series = df[column].dropna()
    invalid = 0
    expected = expected_type.lower()
    if expected == "integer":
        numeric = pd.to_numeric(series, errors="coerce")
        invalid = int(numeric.isna().sum() + ((numeric % 1) != 0).sum())
    elif expected in {"decimal", "float", "number"}:
        invalid = int(pd.to_numeric(series, errors="coerce").isna().sum())
    elif expected == "string":
        invalid = int((~series.map(lambda x: isinstance(x, str))).sum())
    elif expected == "email":
        invalid = int((~series.astype(str).str.match(EMAIL_RE)).sum())
    elif expected == "date":
        invalid = int(pd.to_datetime(series, errors="coerce").isna().sum())
    else:
        return CheckResult(f"Type Check: {column}", "FAIL", "HIGH", f"Unsupported expected type: {expected_type}")

    return CheckResult(
        f"Type Check: {column}",
        "PASS" if invalid == 0 else "FAIL",
        _severity(rule, "HIGH"),
        f"All values match type {expected_type}" if invalid == 0 else f"{invalid:,} invalid values found for type {expected_type}",
        {"invalid_count": invalid, "expected_type": expected_type},
    )


def check_range(df: pd.DataFrame, column: str, rule: dict[str, Any]) -> CheckResult:
    if column not in df.columns:
        return CheckResult(f"Range Check: {column}", "FAIL", "CRITICAL", "Column not found")
    minimum = rule.get("min")
    maximum = rule.get("max")
    numeric = pd.to_numeric(df[column], errors="coerce")
    invalid = pd.Series(False, index=df.index)
    if minimum is not None:
        invalid |= numeric < minimum
    if maximum is not None:
        invalid |= numeric > maximum
    invalid_count = int(invalid.sum())
    return CheckResult(
        f"Range Check: {column}",
        "PASS" if invalid_count == 0 else "FAIL",
        _severity(rule, "MEDIUM"),
        "All values are within range" if invalid_count == 0 else f"{invalid_count:,} values outside allowed range",
        {"min": minimum, "max": maximum, "invalid_count": invalid_count},
    )


def check_allowed_values(df: pd.DataFrame, column: str, rule: dict[str, Any]) -> CheckResult:
    if column not in df.columns:
        return CheckResult(f"Allowed Values Check: {column}", "FAIL", "CRITICAL", "Column not found")
    allowed = set(rule.get("allowed_values", []))
    invalid_values = sorted(set(df[column].dropna().tolist()) - allowed, key=str)
    invalid_count = int(df[column].isin(invalid_values).sum())
    return CheckResult(
        f"Allowed Values Check: {column}",
        "PASS" if invalid_count == 0 else "FAIL",
        _severity(rule, "MEDIUM"),
        "All values are allowed" if invalid_count == 0 else f"{invalid_count:,} invalid values found: {invalid_values}",
        {"allowed_values": sorted(allowed, key=str), "invalid_values": invalid_values, "invalid_count": invalid_count},
    )


def check_duplicates(df: pd.DataFrame, rule: dict[str, Any] | None = None) -> CheckResult:
    duplicates = int(df.duplicated().sum())
    rule = rule or {}
    return CheckResult(
        "Duplicate Row Check",
        "PASS" if duplicates == 0 else "FAIL",
        _severity(rule, "HIGH"),
        "No duplicate rows found" if duplicates == 0 else f"{duplicates:,} duplicate rows found",
        {"duplicate_rows": duplicates},
    )


def run_quality_rules(df: pd.DataFrame, configuration: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    dataset_rules = configuration.get("dataset", {})
    columns = configuration.get("columns", {})

    primary_key = dataset_rules.get("primary_key", [])
    if isinstance(primary_key, str):
        primary_key = [primary_key]
    primary_key_set = set(primary_key)

    # A primary key is inherently required and unique. The primary-key checks
    # below are the single source of truth, so column rules do not duplicate
    # required/unique checks for the same primary-key column.
    for column in primary_key:
        results.append(check_required(df, column, {"severity": "CRITICAL"}))
        results.append(check_unique(df, column, {"severity": "CRITICAL"}))

    for column, rule in columns.items():
        if rule.get("required") and column not in primary_key_set:
            results.append(check_required(df, column, rule))
        if rule.get("unique") and column not in primary_key_set:
            results.append(check_unique(df, column, rule))
        if rule.get("type"):
            results.append(check_type(df, column, rule["type"], rule))
        if "min" in rule or "max" in rule:
            results.append(check_range(df, column, rule))
        if "allowed_values" in rule:
            results.append(check_allowed_values(df, column, rule))

    if dataset_rules.get("check_duplicate_rows", True):
        results.append(check_duplicates(df, dataset_rules))

    return results
