import re
import pandas as pd


def check_required(
    df: pd.DataFrame,
    column: str,
) -> dict:
    """Check whether a column exists and contains no null values."""

    if column not in df.columns:
        return {
            "name": f"Required Check: {column}",
            "status": "FAIL",
            "details": "Column is missing",
        }

    null_count = int(df[column].isna().sum())

    return {
        "name": f"Required Check: {column}",
        "status": "PASS" if null_count == 0 else "FAIL",
        "details": (
            "No null values found"
            if null_count == 0
            else f"{null_count} null values found"
        ),
    }


def check_unique(
    df: pd.DataFrame,
    column: str,
) -> dict:
    """Check whether column values are unique."""

    if column not in df.columns:
        return {
            "name": f"Unique Check: {column}",
            "status": "FAIL",
            "details": "Column is missing",
        }

    duplicate_count = int(
        df[column].duplicated().sum()
    )

    return {
        "name": f"Unique Check: {column}",
        "status": "PASS" if duplicate_count == 0 else "FAIL",
        "details": (
            "All values are unique"
            if duplicate_count == 0
            else f"{duplicate_count} duplicate values found"
        ),
    }


def check_type(
    df: pd.DataFrame,
    column: str,
    expected_type: str,
) -> dict:
    """Validate the data type of a column."""

    if column not in df.columns:
        return {
            "name": f"Type Check: {column}",
            "status": "FAIL",
            "details": "Column is missing",
        }

    series = df[column].dropna()

    if expected_type == "integer":
        converted = pd.to_numeric(
            series,
            errors="coerce",
        )

        invalid = converted.isna().sum()

        if invalid == 0:
            integer_check = (
                converted % 1 == 0
            )

            invalid = int(
                (~integer_check).sum()
            )

    elif expected_type == "decimal":
        converted = pd.to_numeric(
            series,
            errors="coerce",
        )

        invalid = int(
            converted.isna().sum()
        )

    elif expected_type == "string":
        invalid = 0

    elif expected_type == "email":
        pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

        invalid = sum(
            not bool(
                re.match(pattern, str(value))
            )
            for value in series
        )

    elif expected_type == "date":
        converted = pd.to_datetime(
            series,
            errors="coerce",
        )

        invalid = int(
            converted.isna().sum()
        )

    else:
        return {
            "name": f"Type Check: {column}",
            "status": "SKIP",
            "details": (
                f"Unsupported type: {expected_type}"
            ),
        }

    return {
        "name": f"Type Check: {column}",
        "status": "PASS" if invalid == 0 else "FAIL",
        "details": (
            f"All values are valid {expected_type}"
            if invalid == 0
            else (
                f"{invalid} invalid "
                f"{expected_type} values found"
            )
        ),
    }


def check_range(
    df: pd.DataFrame,
    column: str,
    minimum=None,
    maximum=None,
) -> dict:
    """Validate numeric range."""

    if column not in df.columns:
        return {
            "name": f"Range Check: {column}",
            "status": "FAIL",
            "details": "Column is missing",
        }

    series = pd.to_numeric(
        df[column],
        errors="coerce",
    )

    invalid = pd.Series(False, index=df.index)

    if minimum is not None:
        invalid |= series < minimum

    if maximum is not None:
        invalid |= series > maximum

    invalid_count = int(
        invalid.sum()
    )

    return {
        "name": f"Range Check: {column}",
        "status": (
            "PASS"
            if invalid_count == 0
            else "FAIL"
        ),
        "details": (
            "All values are within range"
            if invalid_count == 0
            else (
                f"{invalid_count} values "
                f"outside allowed range"
            )
        ),
    }


def check_allowed_values(
    df: pd.DataFrame,
    column: str,
    allowed_values: list,
) -> dict:
    """Validate allowed values."""

    if column not in df.columns:
        return {
            "name": f"Allowed Values Check: {column}",
            "status": "FAIL",
            "details": "Column is missing",
        }

    invalid = df[
        ~df[column].isin(allowed_values)
        & df[column].notna()
    ]

    invalid_count = len(invalid)

    return {
        "name": f"Allowed Values Check: {column}",
        "status": (
            "PASS"
            if invalid_count == 0
            else "FAIL"
        ),
        "details": (
            "All values are allowed"
            if invalid_count == 0
            else (
                f"{invalid_count} invalid "
                f"values found"
            )
        ),
    }


def check_duplicate_rows(
    df: pd.DataFrame,
) -> dict:
    """Check duplicate complete rows."""

    duplicate_count = int(
        df.duplicated().sum()
    )

    return {
        "name": "Duplicate Row Check",
        "status": (
            "PASS"
            if duplicate_count == 0
            else "FAIL"
        ),
        "details": (
            "No duplicate rows found"
            if duplicate_count == 0
            else (
                f"{duplicate_count} "
                f"duplicate rows found"
            )
        ),
    }