import pandas as pd


def profile_dataframe(df: pd.DataFrame) -> dict:
    """Generate basic profiling information for a DataFrame."""

    profile = {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": [],
    }

    for column in df.columns:
        series = df[column]

        column_profile = {
            "name": column,
            "dtype": str(series.dtype),
            "null_count": int(series.isna().sum()),
            "null_percentage": round(
                float(series.isna().mean() * 100), 2
            ),
            "unique_count": int(series.nunique(dropna=True)),
            "duplicate_count": int(
                series.duplicated(keep=False).sum()
            ),
        }

        profile["columns"].append(column_profile)

    return profile