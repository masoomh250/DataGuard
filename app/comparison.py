from typing import Any
import hashlib

import pandas as pd

from .models import CheckResult


def _keys(df: pd.DataFrame, primary_key: str) -> set[Any]:
    return set(df[primary_key].dropna().tolist())


def _record_dict(row: pd.Series) -> dict[str, str]:
    return {str(k): "<NULL>" if pd.isna(v) else str(v) for k, v in row.items()}


def compare_row_counts(source_df: pd.DataFrame, target_df: pd.DataFrame) -> CheckResult:
    source_count, target_count = len(source_df), len(target_df)
    diff = target_count - source_count
    return CheckResult(
        "Row Count Comparison",
        "PASS" if source_count == target_count else "FAIL",
        "HIGH",
        f"Source: {source_count:,}, Target: {target_count:,}, Difference: {diff:+,}",
        {"source_count": source_count, "target_count": target_count, "difference": diff},
    )


def compare_columns(source_df: pd.DataFrame, target_df: pd.DataFrame) -> CheckResult:
    source, target = list(source_df.columns), list(target_df.columns)
    missing = [c for c in source if c not in target]
    extra = [c for c in target if c not in source]
    order_match = source == target
    passed = not missing and not extra and order_match
    details = "Column names and order match" if passed else f"Missing: {missing}; Extra: {extra}; Order matches: {order_match}"
    return CheckResult(
        "Column Structure Comparison",
        "PASS" if passed else "FAIL",
        "HIGH",
        details,
        {"missing": missing, "extra": extra, "order_match": order_match},
    )


def compare_data_types(source_df: pd.DataFrame, target_df: pd.DataFrame) -> CheckResult:
    mismatches = []
    for column in source_df.columns:
        if column in target_df.columns:
            source_type, target_type = str(source_df[column].dtype), str(target_df[column].dtype)
            if source_type != target_type:
                mismatches.append({"column": column, "source": source_type, "target": target_type})
    return CheckResult(
        "Data Type Comparison",
        "PASS" if not mismatches else "FAIL",
        "MEDIUM",
        "All common column data types match" if not mismatches else f"{len(mismatches)} type mismatches found",
        {"mismatch_count": len(mismatches)},
        mismatches,
    )


def compare_null_percentages(source_df: pd.DataFrame, target_df: pd.DataFrame, tolerance: float = 0.0) -> CheckResult:
    mismatches = []
    for column in source_df.columns:
        if column in target_df.columns:
            s = float(source_df[column].isna().mean() * 100) if len(source_df) else 0.0
            t = float(target_df[column].isna().mean() * 100) if len(target_df) else 0.0
            if abs(s - t) > tolerance:
                mismatches.append({"column": column, "source_pct": round(s, 2), "target_pct": round(t, 2)})
    return CheckResult(
        "Null Percentage Comparison",
        "PASS" if not mismatches else "FAIL",
        "MEDIUM",
        "Null percentages match" if not mismatches else f"Mismatch found in {len(mismatches)} column(s)",
        {"tolerance": tolerance},
        mismatches,
    )


def compare_primary_key(source_df: pd.DataFrame, target_df: pd.DataFrame, primary_key: str) -> CheckResult:
    if primary_key not in source_df.columns or primary_key not in target_df.columns:
        return CheckResult("Primary Key Comparison", "FAIL", "CRITICAL", f"Primary key '{primary_key}' not found in both datasets")
    missing = sorted(_keys(source_df, primary_key) - _keys(target_df, primary_key), key=str)
    extra = sorted(_keys(target_df, primary_key) - _keys(source_df, primary_key), key=str)
    passed = not missing and not extra
    mismatches = ([{"type": "MISSING_IN_TARGET", "key": key} for key in missing] +
                  [{"type": "EXTRA_IN_TARGET", "key": key} for key in extra])
    return CheckResult(
        "Primary Key Comparison",
        "PASS" if passed else "FAIL",
        "CRITICAL",
        "Source and target keys match" if passed else f"Missing in target: {len(missing)}; Extra in target: {len(extra)}",
        {"missing_count": len(missing), "extra_count": len(extra), "missing_keys": missing, "extra_keys": extra},
        mismatches,
    )


def compare_records(source_df: pd.DataFrame, target_df: pd.DataFrame, primary_key: str) -> CheckResult:
    if primary_key not in source_df.columns or primary_key not in target_df.columns:
        return CheckResult("Record Presence Comparison", "FAIL", "CRITICAL", f"Primary key '{primary_key}' not found in both datasets")
    missing = sorted(_keys(source_df, primary_key) - _keys(target_df, primary_key), key=str)
    extra = sorted(_keys(target_df, primary_key) - _keys(source_df, primary_key), key=str)
    mismatches = []
    for key in missing:
        row = source_df[source_df[primary_key] == key].iloc[0]
        mismatches.append({"type": "MISSING", "key": key, "record": _record_dict(row)})
    for key in extra:
        row = target_df[target_df[primary_key] == key].iloc[0]
        mismatches.append({"type": "EXTRA", "key": key, "record": _record_dict(row)})
    return CheckResult(
        "Record Presence Comparison",
        "PASS" if not mismatches else "FAIL",
        "CRITICAL",
        "All source records exist in target and no extra target records found" if not mismatches else f"{len(mismatches)} record presence issue(s) found",
        {"missing_count": len(missing), "extra_count": len(extra)},
        mismatches,
    )


def compare_record_values(source_df: pd.DataFrame, target_df: pd.DataFrame, primary_key: str, max_mismatches: int = 5000) -> CheckResult:
    if primary_key not in source_df.columns or primary_key not in target_df.columns:
        return CheckResult("Record Value Comparison", "FAIL", "CRITICAL", f"Primary key '{primary_key}' not found in both datasets")
    common_columns = [c for c in source_df.columns if c in target_df.columns and c != primary_key]
    source = source_df.drop_duplicates(subset=[primary_key]).set_index(primary_key)
    target = target_df.drop_duplicates(subset=[primary_key]).set_index(primary_key)
    common_keys = source.index.intersection(target.index)
    mismatches = []
    for key in common_keys:
        for column in common_columns:
            s, t = source.at[key, column], target.at[key, column]
            equal = (pd.isna(s) and pd.isna(t)) or s == t
            if not equal:
                mismatches.append({"key": key, "column": column, "source": str(s), "target": str(t)})
                if len(mismatches) >= max_mismatches:
                    break
        if len(mismatches) >= max_mismatches:
            break
    return CheckResult(
        "Record Value Comparison",
        "PASS" if not mismatches else "FAIL",
        "HIGH",
        "All common records match" if not mismatches else f"{len(mismatches):,} field mismatch(es) found",
        {"mismatch_count": len(mismatches), "max_mismatches": max_mismatches},
        mismatches,
    )


def _row_hash(row: pd.Series, columns: list[str]) -> str:
    payload = "|".join("<NULL>" if pd.isna(row[c]) else str(row[c]) for c in columns)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compare_hashes(source_df: pd.DataFrame, target_df: pd.DataFrame, primary_key: str) -> CheckResult:
    if primary_key not in source_df.columns or primary_key not in target_df.columns:
        return CheckResult("Hash Comparison", "FAIL", "CRITICAL", f"Primary key '{primary_key}' not found in both datasets")
    columns = [c for c in source_df.columns if c in target_df.columns and c != primary_key]
    source_hashes = {row[primary_key]: _row_hash(row, columns) for _, row in source_df.iterrows()}
    target_hashes = {row[primary_key]: _row_hash(row, columns) for _, row in target_df.iterrows()}
    common = set(source_hashes) & set(target_hashes)
    mismatched_keys = sorted([k for k in common if source_hashes[k] != target_hashes[k]], key=str)
    mismatches = [
        {"key": key, "source_hash": source_hashes[key], "target_hash": target_hashes[key]}
        for key in mismatched_keys
    ]
    return CheckResult(
        "Hash Comparison",
        "PASS" if not mismatched_keys else "FAIL",
        "HIGH",
        "Hashes match for all common records" if not mismatched_keys else f"{len(mismatched_keys):,} record hash mismatch(es) found",
        {"common_records": len(common), "mismatch_count": len(mismatched_keys)},
        mismatches,
    )


def run_comparison(source_df: pd.DataFrame, target_df: pd.DataFrame, primary_key: str, null_tolerance: float = 0.0) -> list[CheckResult]:
    return [
        compare_row_counts(source_df, target_df),
        compare_columns(source_df, target_df),
        compare_data_types(source_df, target_df),
        compare_null_percentages(source_df, target_df, null_tolerance),
        compare_primary_key(source_df, target_df, primary_key),
        compare_records(source_df, target_df, primary_key),
        compare_record_values(source_df, target_df, primary_key),
        compare_hashes(source_df, target_df, primary_key),
    ]
