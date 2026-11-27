import pandas as pd


def compare_row_counts(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> dict:
    """Compare source and target row counts."""

    source_count = len(source_df)
    target_count = len(target_df)

    difference = target_count - source_count

    return {
        "name": "Row Count Comparison",
        "status": (
            "PASS"
            if source_count == target_count
            else "FAIL"
        ),
        "details": (
            f"Source: {source_count:,}, "
            f"Target: {target_count:,}, "
            f"Difference: {difference:+,}"
        ),
    }


def compare_columns(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> dict:
    """Compare source and target column names."""

    source_columns = list(source_df.columns)
    target_columns = list(target_df.columns)

    missing_in_target = [
        column
        for column in source_columns
        if column not in target_columns
    ]

    extra_in_target = [
        column
        for column in target_columns
        if column not in source_columns
    ]

    passed = (
        not missing_in_target
        and not extra_in_target
    )

    return {
        "name": "Column Comparison",
        "status": (
            "PASS"
            if passed
            else "FAIL"
        ),
        "details": (
            "Source and target columns match"
            if passed
            else (
                f"Missing in target: "
                f"{missing_in_target}; "
                f"Extra in target: "
                f"{extra_in_target}"
            )
        ),
    }


def compare_data_types(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> dict:
    """Compare column data types."""

    common_columns = [
        column
        for column in source_df.columns
        if column in target_df.columns
    ]

    mismatches = []

    for column in common_columns:

        source_type = str(
            source_df[column].dtype
        )

        target_type = str(
            target_df[column].dtype
        )

        if source_type != target_type:
            mismatches.append(
                {
                    "column": column,
                    "source": source_type,
                    "target": target_type,
                }
            )

    return {
        "name": "Data Type Comparison",
        "status": (
            "PASS"
            if not mismatches
            else "FAIL"
        ),
        "details": (
            "All common column data types match"
            if not mismatches
            else (
                f"{len(mismatches)} "
                f"type mismatches found"
            )
        ),
    }


def compare_null_percentages(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
) -> dict:
    """Compare null percentages."""

    common_columns = [
        column
        for column in source_df.columns
        if column in target_df.columns
    ]

    mismatches = []

    for column in common_columns:

        source_null_pct = (
            source_df[column]
            .isna()
            .mean()
            * 100
        )

        target_null_pct = (
            target_df[column]
            .isna()
            .mean()
            * 100
        )

        if round(
            source_null_pct,
            2,
        ) != round(
            target_null_pct,
            2,
        ):
            mismatches.append(column)

    return {
        "name": "Null Percentage Comparison",
        "status": (
            "PASS"
            if not mismatches
            else "FAIL"
        ),
        "details": (
            "Null percentages match"
            if not mismatches
            else (
                f"Mismatch found in: "
                f"{', '.join(mismatches)}"
            )
        ),
    }


def compare_primary_key(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    primary_key: str,
) -> dict:
    """Compare primary key records."""

    if (
        primary_key not in source_df.columns
        or primary_key not in target_df.columns
    ):
        return {
            "name": "Primary Key Comparison",
            "status": "FAIL",
            "details": (
                f"Primary key '{primary_key}' "
                f"not found in both datasets"
            ),
        }

    source_keys = set(
        source_df[primary_key]
        .dropna()
    )

    target_keys = set(
        target_df[primary_key]
        .dropna()
    )

    missing_in_target = (
        source_keys - target_keys
    )

    extra_in_target = (
        target_keys - source_keys
    )

    passed = (
        not missing_in_target
        and not extra_in_target
    )

    return {
        "name": "Primary Key Comparison",
        "status": (
            "PASS"
            if passed
            else "FAIL"
        ),
        "details": (
            "Source and target keys match"
            if passed
            else (
                f"Missing in target: "
                f"{len(missing_in_target)}; "
                f"Extra in target: "
                f"{len(extra_in_target)}"
            )
        ),
    }


def compare_records(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    primary_key: str,
) -> dict:
    """Compare source and target records using the primary key."""

    if (
        primary_key not in source_df.columns
        or primary_key not in target_df.columns
    ):
        return {
            "name": "Record Level Comparison",
            "status": "FAIL",
            "details": (
                f"Primary key '{primary_key}' "
                f"not found in both datasets"
            ),
        }

    source_keys = set(
        source_df[primary_key]
        .dropna()
    )

    target_keys = set(
        target_df[primary_key]
        .dropna()
    )

    missing_keys = sorted(
        source_keys - target_keys
    )

    extra_keys = sorted(
        target_keys - source_keys
    )

    if not missing_keys and not extra_keys:
        return {
            "name": "Record Level Comparison",
            "status": "PASS",
            "details": (
                "All source records exist in target"
            ),
        }

    details = []

    if missing_keys:
        details.append(
            f"Missing in target: {missing_keys}"
        )

    if extra_keys:
        details.append(
            f"Extra in target: {extra_keys}"
        )

    return {
        "name": "Record Level Comparison",
        "status": "FAIL",
        "details": "; ".join(details),
    }


def run_comparison(
    source_df: pd.DataFrame,
    target_df: pd.DataFrame,
    primary_key: str,
) -> list:
    """Run all source vs target comparisons."""

    return [
        compare_row_counts(
            source_df,
            target_df,
        ),
        compare_columns(
            source_df,
            target_df,
        ),
        compare_data_types(
            source_df,
            target_df,
        ),
        compare_null_percentages(
            source_df,
            target_df,
        ),
        compare_primary_key(
            source_df,
            target_df,
            primary_key,
        ),
        compare_records(
            source_df,
            target_df,
            primary_key,
        ),
    ]


def calculate_comparison_score(
    results: list,
) -> float:
    """Calculate source vs target score."""

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

    return round(
        (
            passed
            / len(applicable)
        )
        * 100,
        2,
    )