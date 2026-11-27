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

def validate_rule_configuration(
    rules_json: str,
    default_primary_key: str,
) -> str:
    """
    Parse, validate, normalize, and return project rule configuration JSON.

    This is intentionally independent of FastAPI so it can be reused by
    routes, tests, APIs, or future import workflows.
    """
    import json

    parsed_rules = json.loads(rules_json)

    if not isinstance(parsed_rules, dict):
        raise ValueError("Rules JSON must contain a JSON object.")

    columns = parsed_rules.get("columns")

    if not isinstance(columns, dict):
        raise ValueError("Rules JSON must contain a 'columns' object.")

    dataset = parsed_rules.get("dataset", {})

    if not isinstance(dataset, dict):
        raise ValueError("Rules JSON 'dataset' must be an object.")

    allowed_types = {
        "string",
        "integer",
        "decimal",
        "float",
        "number",
        "email",
        "date",
    }

    allowed_severities = {
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    }

    def validate_numeric_value(
        value: Any,
        field_name: str,
        column_name: str,
    ) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"{field_name} for column '{column_name}' must be numeric."
            )

    primary_key = dataset.get("primary_key", default_primary_key)

    if isinstance(primary_key, str):
        primary_key = primary_key.strip()

        if not primary_key:
            raise ValueError("Dataset primary_key cannot be empty.")

    elif isinstance(primary_key, list):
        if not primary_key:
            raise ValueError("Dataset primary_key cannot be empty.")

        cleaned_primary_keys = []

        for key in primary_key:
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    "Every dataset primary_key value must be a non-empty string."
                )

            cleaned_primary_keys.append(key.strip())

        if len(cleaned_primary_keys) != len(set(cleaned_primary_keys)):
            raise ValueError(
                "Dataset primary_key contains duplicate columns."
            )

        primary_key = cleaned_primary_keys

    else:
        raise ValueError(
            "Dataset primary_key must be a string or a list of strings."
        )

    if "check_duplicate_rows" in dataset:
        if not isinstance(dataset["check_duplicate_rows"], bool):
            raise ValueError(
                "Dataset check_duplicate_rows must be true or false."
            )

    for column_name, rule in columns.items():

        if not isinstance(column_name, str) or not column_name.strip():
            raise ValueError(
                "Every column rule must have a non-empty column name."
            )

        if not isinstance(rule, dict):
            raise ValueError(
                f"Rule for column '{column_name}' must be an object."
            )

        for boolean_field in ("required", "unique"):
            if boolean_field in rule and not isinstance(
                rule[boolean_field],
                bool,
            ):
                raise ValueError(
                    f"{boolean_field} for column '{column_name}' "
                    "must be true or false."
                )

        if "type" in rule:
            expected_type = str(rule["type"]).strip().lower()

            if expected_type not in allowed_types:
                raise ValueError(
                    f"Unsupported type '{rule['type']}' for column "
                    f"'{column_name}'. Supported types: "
                    f"{', '.join(sorted(allowed_types))}."
                )

            rule["type"] = expected_type

        if "severity" in rule:
            severity = str(rule["severity"]).strip().upper()

            if severity not in allowed_severities:
                raise ValueError(
                    f"Invalid severity '{rule['severity']}' for column "
                    f"'{column_name}'. Supported severities: "
                    f"{', '.join(sorted(allowed_severities))}."
                )

            rule["severity"] = severity

        if "min" in rule:
            validate_numeric_value(
                rule["min"],
                "Minimum",
                column_name,
            )

        if "max" in rule:
            validate_numeric_value(
                rule["max"],
                "Maximum",
                column_name,
            )

        if "min" in rule and "max" in rule:
            if rule["min"] > rule["max"]:
                raise ValueError(
                    f"Minimum cannot be greater than maximum "
                    f"for column '{column_name}'."
                )

        if "allowed_values" in rule:
            if not isinstance(rule["allowed_values"], list):
                raise ValueError(
                    f"allowed_values for column '{column_name}' "
                    "must be a list."
                )

            for value in rule["allowed_values"]:
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    raise ValueError(
                        f"allowed_values for column '{column_name}' "
                        "must contain only scalar values."
                    )

        if ("min" in rule or "max" in rule):
            numeric_types = {"integer", "decimal", "float", "number"}

            if rule.get("type") not in numeric_types:
                raise ValueError(
                    f"Range rules for column '{column_name}' "
                    "require a numeric type."
                )

    for key in primary_key if isinstance(primary_key, list) else [primary_key]:
        if key not in columns:
            raise ValueError(
                f"Primary key column '{key}' is not defined in columns."
            )

    parsed_rules["dataset"] = dataset
    parsed_rules["dataset"]["primary_key"] = primary_key

    return json.dumps(
        parsed_rules,
        indent=4,
        ensure_ascii=False,
    )
