import pandas as pd

from .rules import (
    check_required,
    check_unique,
    check_type,
    check_range,
    check_allowed_values,
    check_duplicate_rows,
)


def run_validation(
    df: pd.DataFrame,
    configuration: dict,
) -> list:
    """Run validation rules from configuration."""

    results = []

    columns = configuration.get(
        "columns",
        {},
    )

    for column, rules in columns.items():

        if rules.get("required") is True:
            results.append(
                check_required(
                    df,
                    column,
                )
            )

        if rules.get("unique") is True:
            results.append(
                check_unique(
                    df,
                    column,
                )
            )

        expected_type = rules.get("type")

        if expected_type:
            results.append(
                check_type(
                    df,
                    column,
                    expected_type,
                )
            )

        if (
            "min" in rules
            or "max" in rules
        ):
            results.append(
                check_range(
                    df,
                    column,
                    rules.get("min"),
                    rules.get("max"),
                )
            )

        if "allowed_values" in rules:
            results.append(
                check_allowed_values(
                    df,
                    column,
                    rules["allowed_values"],
                )
            )

    results.append(
        check_duplicate_rows(df)
    )

    return results


def calculate_quality_score(
    results: list,
) -> float:
    """Calculate quality score."""

    applicable = [
        result
        for result in results
        if result["status"] != "SKIP"
    ]

    if not applicable:
        return 0.0

    passed = sum(
        result["status"] == "PASS"
        for result in applicable
    )

    score = (
        passed / len(applicable)
    ) * 100

    return round(score, 2)