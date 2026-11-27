import argparse
import json
from pathlib import Path

import pandas as pd

from .profiler import profile_dataframe
from .validator import (
    run_validation,
    calculate_quality_score,
)
from .report import generate_html_report


def load_csv(
    file_path: str,
) -> pd.DataFrame:
    """Load CSV dataset."""

    return pd.read_csv(file_path)


def load_configuration(
    config_path: str,
) -> dict:
    """Load validation configuration."""

    with open(
        config_path,
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "DataGuard Data Quality Analyzer"
        )
    )

    parser.add_argument(
        "filename",
        help="CSV file to analyze",
    )

    parser.add_argument(
        "--rules",
        default="rules.json",
        help="Validation rules JSON file",
    )

    args = parser.parse_args()

    input_file = Path(
        args.filename
    )

    rules_file = Path(
        args.rules
    )

    if not input_file.exists():
        print(
            f"ERROR: File not found: "
            f"{input_file}"
        )
        return 1

    if not rules_file.exists():
        print(
            f"ERROR: Rules file not found: "
            f"{rules_file}"
        )
        return 1

    if input_file.suffix.lower() != ".csv":
        print(
            "ERROR: Currently only CSV files "
            "are supported."
        )
        return 1

    print()
    print("=" * 60)
    print("DataGuard")
    print("Data Quality Analyzer")
    print("=" * 60)

    print()
    print(
        f"Loading: {input_file}"
    )

    df = load_csv(
        str(input_file)
    )

    configuration = load_configuration(
        str(rules_file)
    )

    print(
        f"Rows: {len(df):,}"
    )

    print(
        f"Columns: {len(df.columns)}"
    )

    print()
    print("Profiling dataset...")

    profile = profile_dataframe(
        df
    )

    print(
        "Running configured quality checks..."
    )

    results = run_validation(
        df,
        configuration,
    )

    score = calculate_quality_score(
        results
    )

    print()
    print("Validation Results")
    print("-" * 60)

    for result in results:

        print(
            f'{result["status"]:5} '
            f'{result["name"]}: '
            f'{result["details"]}'
        )

    print()
    print("=" * 60)
    print(
        f"QUALITY SCORE: {score}%"
    )
    print("=" * 60)

    report_file = (
        Path("reports")
        / (
            f"{input_file.stem}"
            f"_data_quality_report.html"
        )
    )

    generate_html_report(
        profile,
        results,
        score,
        str(report_file),
    )

    print()
    print(
        f"Report generated: "
        f"{report_file}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )