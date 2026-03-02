"""Feed file loading utilities (TSV, CSV, Excel)."""
import hashlib
import io
from pathlib import Path
from typing import Tuple

import pandas as pd


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def excel_sheet_names(data: bytes) -> Tuple[str, ...]:
    xls = pd.ExcelFile(io.BytesIO(data), engine="openpyxl")
    return tuple(xls.sheet_names)


def load_excel_bytes(file_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    return pd.read_excel(
        xls,
        sheet_name=sheet_name,
        dtype=str,
        na_filter=False,
        engine="openpyxl",
    )


def load_csv_like(file_bytes: bytes, sep: str) -> pd.DataFrame:
    return pd.read_csv(
        io.BytesIO(file_bytes),
        sep=sep,
        dtype=str,
        na_filter=False,
        low_memory=False,
        on_bad_lines="skip",
    )


def load_feed(file_bytes: bytes, filename: str, sheet_name: str = None) -> pd.DataFrame:
    """Load a feed file from bytes. Returns a DataFrame with normalised column headers."""
    ext = Path(filename).suffix.lower()
    if ext in {".xlsx", ".xls"}:
        sheet = sheet_name or excel_sheet_names(file_bytes)[0]
        df = load_excel_bytes(file_bytes, sheet)
    elif ext in {".tsv", ".txt"}:
        df = load_csv_like(file_bytes, "\t")
    elif ext == ".csv":
        df = load_csv_like(file_bytes, ",")
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    # Normalise headers
    df.columns = (
        pd.Index(df.columns)
        .map(lambda x: str(x).strip())
        .str.replace(r"\s+", " ", regex=True)
    )
    return df
