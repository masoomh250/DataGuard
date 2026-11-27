import argparse
import json
from pathlib import Path
from .comparison import run_comparison
from .io import load_dataset
from .profiler import profile_dataset
from .report import generate_html_report
from .rules import run_quality_rules
from .scoring import calculate_score, status_counts


def _load_rules(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _print_results(results):
    for result in results:
        print(f"{result.status:5} {result.name}: {result.details}")


def profile_command(args) -> int:
    df = load_dataset(args.input)
    profile = profile_dataset(df)
    print("DataGuard Dataset Profile")
    print("=" * 70)
    print(f"Rows: {profile['row_count']:,}")
    print(f"Columns: {profile['column_count']:,}")
    print(f"Duplicate rows: {profile['duplicate_rows']:,}")
    print("\nColumns")
    for column in profile["columns"]:
        print(f"{column['name']}: {column['dtype']}, nulls={column['null_pct']}%, unique={column['unique_count']:,}")
    return 0


def validate_command(args) -> int:
    df = load_dataset(args.input)
    rules = _load_rules(args.rules)
    results = run_quality_rules(df, rules)
    score = calculate_score(results)
    print("\n" + "=" * 70 + "\nDataGuard Data Quality Validation\n" + "=" * 70)
    _print_results(results)
    print("\n" + "=" * 70 + f"\nDATA QUALITY SCORE: {score}%\n" + "=" * 70)
    if args.report:
        output = generate_html_report(args.report, "DataGuard Data Quality Report", results, score, profile_dataset(df), source=args.input)
        print(f"Report: {output}")
    return 0 if all(r.status in {"PASS", "SKIP"} for r in results) else 2


def compare_command(args) -> int:
    source_df = load_dataset(args.source)
    target_df = load_dataset(args.target)
    results = run_comparison(source_df, target_df, args.primary_key, args.null_tolerance)
    score = calculate_score(results)
    counts = status_counts(results)
    print("\n" + "=" * 70 + "\nDataGuard Source vs Target ETL Validation\n" + "=" * 70)
    print(f"Source: {args.source}\nTarget: {args.target}\n")
    _print_results(results)
    print("\n" + "=" * 70 + f"\nETL VALIDATION SCORE: {score}%\nPASS: {counts['PASS']}  FAIL: {counts['FAIL']}\n" + "=" * 70)
    report = args.report or str(Path("reports") / f"{Path(args.source).stem}_vs_{Path(args.target).stem}_etl_report.html")
    output = generate_html_report(report, "DataGuard ETL Validation Report", results, score, source=args.source, target=args.target)
    print(f"Report: {output}")
    return 0 if counts["FAIL"] == 0 else 2


def build_parser():
    parser = argparse.ArgumentParser(prog="dataguard", description="DataGuard data quality and ETL validation platform")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("profile", help="Profile a dataset")
    p.add_argument("input")
    p.set_defaults(func=profile_command)

    p = sub.add_parser("validate", help="Run configurable data quality rules")
    p.add_argument("input")
    p.add_argument("--rules", default="rules.json")
    p.add_argument("--report")
    p.set_defaults(func=validate_command)

    p = sub.add_parser("compare", help="Compare source and target datasets")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--primary-key", default="customer_id")
    p.add_argument("--null-tolerance", type=float, default=0.0)
    p.add_argument("--report")
    p.set_defaults(func=compare_command)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
