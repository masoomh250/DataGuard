from typing import Any
import pandas as pd


def profile_dataset(df: pd.DataFrame) -> dict[str, Any]:
    columns = []
    for column in df.columns:
        series = df[column]
        columns.append(
            {
                "name": str(column),
                "dtype": str(series.dtype),
                "rows": int(len(series)),
                "null_count": int(series.isna().sum()),
                "null_pct": round(float(series.isna().mean() * 100), 2) if len(series) else 0.0,
                "unique_count": int(series.nunique(dropna=True)),
                "sample_values": [str(v) for v in series.dropna().head(5).tolist()],
            }
        )

    duplicate_rows = int(df.duplicated().sum())
    return {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "duplicate_rows": duplicate_rows,
        "columns": columns,
    }
