# DataGuard v1.0

DataGuard is a Python-based data quality and source-to-target ETL validation engine.

## Features

1. CSV, Excel and Parquet input
2. Dataset profiling
3. Configurable JSON quality rules
4. Required, unique, type, range and allowed-value checks
5. Duplicate-row detection
6. Source vs target row-count validation
7. Column structure and data-type validation
8. Null-percentage comparison with tolerance
9. Primary-key comparison
10. Missing and extra record detection
11. Record-level field comparison
12. SHA256 record hash comparison
13. Severity-weighted quality scoring
14. Detailed HTML reports with mismatch tables and score breakdown
15. Pytest test suite

## Commands

```powershell
python -m app.cli profile sample_data\customers.csv
python -m app.cli validate sample_data\customers.csv --rules rules.json --report reports\quality.html
python -m app.cli compare sample_data\source_customers.csv sample_data\target_customers.csv --primary-key customer_id
```

## Tests

```powershell
python -m pytest -q
```

The sample project should report 6 passing tests.

## Exit codes

`0` means all applicable checks passed.

`2` means validation completed successfully but one or more checks failed.

`1` means an application error occurred.
