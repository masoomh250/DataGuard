import json
from pathlib import Path
import pandas as pd
from app.comparison import run_comparison
from app.profiler import profile_dataset
from app.rules import run_quality_rules
from app.scoring import calculate_score

ROOT = Path(__file__).resolve().parents[1]


def test_profile():
    df = pd.read_csv(ROOT / "sample_data" / "customers.csv")
    profile = profile_dataset(df)
    assert profile["row_count"] == 10
    assert profile["column_count"] == 6
    assert profile["duplicate_rows"] == 1


def test_quality_rules():
    df = pd.read_csv(ROOT / "sample_data" / "customers.csv")
    rules = json.loads((ROOT / "rules.json").read_text())
    results = run_quality_rules(df, rules)
    assert any(r.status == "FAIL" for r in results)


def test_etl_comparison():
    source = pd.read_csv(ROOT / "sample_data" / "source_customers.csv")
    target = pd.read_csv(ROOT / "sample_data" / "target_customers.csv")
    results = run_comparison(source, target, "customer_id")
    names = {r.name: r for r in results}
    assert names["Row Count Comparison"].status == "FAIL"
    assert names["Record Presence Comparison"].status == "FAIL"
    assert names["Record Value Comparison"].status == "FAIL"
    assert names["Hash Comparison"].status == "FAIL"
    assert any(item["key"] == 1005 and item["column"] == "age" for item in names["Record Value Comparison"].mismatches)


def test_score_range():
    df = pd.read_csv(ROOT / "sample_data" / "source_customers.csv")
    rules = json.loads((ROOT / "rules.json").read_text())
    score = calculate_score(run_quality_rules(df, rules))
    assert 0 <= score <= 100


def test_primary_key_unique_check_is_not_duplicated():
    df = pd.read_csv(ROOT / "sample_data" / "customers.csv")
    rules = json.loads((ROOT / "rules.json").read_text())
    results = run_quality_rules(df, rules)
    unique_checks = [r for r in results if r.name == "Unique Check: customer_id"]
    required_checks = [r for r in results if r.name == "Required Check: customer_id"]
    assert len(unique_checks) == 1
    assert len(required_checks) == 1


def test_etl_report_mismatch_details_are_structured(tmp_path):
    source = pd.read_csv(ROOT / "sample_data" / "source_customers.csv")
    target = pd.read_csv(ROOT / "sample_data" / "target_customers.csv")
    results = run_comparison(source, target, "customer_id")
    by_name = {r.name: r for r in results}

    presence = by_name["Record Presence Comparison"]
    assert any(item["key"] == 1010 for item in presence.mismatches)

    hashes = by_name["Hash Comparison"]
    mismatch = next(item for item in hashes.mismatches if item["key"] == 1005)
    assert mismatch["source_hash"] != mismatch["target_hash"]
