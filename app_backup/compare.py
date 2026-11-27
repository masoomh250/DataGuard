import argparse
from pathlib import Path

import pandas as pd

from .comparison import (
    run_comparison,
    calculate_comparison_score,
)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "DataGuard Source vs Target "
            "ETL Validator"
        )
    )

    parser.add_argument(
        "source",
        help="Source CSV file",
    )

    parser.add_argument(
        "target",
        help="Target CSV file",
    )

    parser.add_argument(
        "--primary-key",
        default="customer_id",
        help="Primary key column",
    )

    args = parser.parse_args()

    source = Path(args.source)
    target = Path(args.target)

    if not source.exists():
        print(
            f"ERROR: Source file not found: "
            f"{source}"
        )
        return 1

    if not target.exists():
        print(
            f"ERROR: Target file not found: "
            f"{target}"
        )
        return 1

    source_df = pd.read_csv(source)
    target_df = pd.read_csv(target)

    print()
    print("=" * 70)
    print("DataGuard")
    print("Source vs Target ETL Validation")
    print("=" * 70)

    print()
    print(
        f"Source: {source}"
    )

    print(
        f"Target: {target}"
    )

    print()
    print("Running comparisons...")
    print()

    results = run_comparison(
        source_df,
        target_df,
        args.primary_key,
    )

    score = calculate_comparison_score(
        results
    )

    print("Comparison Results")
    print("-" * 70)

    for result in results:

        print(
            f'{result["status"]:5} '
            f'{result["name"]}: '
            f'{result["details"]}'
        )

    print()
    print("=" * 70)
    print(
        f"ETL VALIDATION SCORE: {score}%"
    )
    print("=" * 70)

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )