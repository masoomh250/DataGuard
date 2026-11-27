from pathlib import Path
import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".parquet"}


def load_dataset(path: str | Path) -> pd.DataFrame:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Dataset not found: {file_path}")
    if not file_path.is_file():
        raise ValueError(f"Dataset path is not a file: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{suffix}'. Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if suffix == ".csv":
        return pd.read_csv(file_path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(file_path)
    if suffix == ".parquet":
        try:
            return pd.read_parquet(file_path)
        except ImportError as exc:
            raise ImportError("Parquet support requires pyarrow. Install it with: pip install pyarrow") from exc

    raise ValueError(f"Unsupported file type: {suffix}")
