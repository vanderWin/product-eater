"""Category extraction from product_type / google_product_category strings."""
import re
import pandas as pd


def extract_categories(
    df: pd.DataFrame,
    src_col: str,
    want_main: bool = False,
    want_penultimate: bool = False,
    want_final: bool = False,
) -> pd.DataFrame:
    """Add Main/Penultimate/Final Category columns as requested."""
    df = df.copy()
    parts = (
        df[src_col].astype(str)
        .apply(lambda v: [p.strip() for p in re.split(r"\s*>\s*", v) if p.strip()])
    )

    if want_main:
        df["Main Category"] = parts.apply(lambda xs: xs[0] if xs else "").str.lower()
    else:
        df.drop(columns=["Main Category", "main_category"], inplace=True, errors="ignore")

    if want_penultimate:
        df["Penultimate Category"] = parts.apply(
            lambda xs: xs[-2] if len(xs) >= 2 else (xs[0] if xs else "")
        ).str.lower()
    else:
        df.drop(columns=["Penultimate Category", "penultimate_category"], inplace=True, errors="ignore")

    if want_final:
        df["Final Category"] = parts.apply(lambda xs: xs[-1] if xs else "").str.lower()
    else:
        df.drop(columns=["Final Category", "final_category"], inplace=True, errors="ignore")

    return df


def preferred_category_column(df: pd.DataFrame) -> str | None:
    """Return the preferred source column for category extraction."""
    for name in ["google product category", "product type"]:
        if name in df.columns:
            return name
    # Fall back to any column whose name contains "category"
    for col in df.columns:
        if "category" in col.lower():
            return col
    return list(df.columns)[0] if df.columns.any() else None
