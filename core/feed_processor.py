"""Feed processing: column selection, filters, Age Gender Segment derivation."""
import re
import pandas as pd
from .utils import norm

RECOMMENDED_RAW = [
    "title", "availability", "price", "brand", "gtin", "mpn",
    "condition", "language", "age group", "product type", "gender", "color",
    "image link", "additional image link",
    "feed label", "item group id", "quantity", "qauntity",
    "google product category", "Age Gender Segment",
    "all clicks", "material",
]


def derive_age_gender_segment(df: pd.DataFrame) -> pd.DataFrame:
    """Add Age Gender Segment column derived from gender + age_group fields."""
    _cols = {c.lower(): c for c in df.columns}

    def _pick(*names):
        for n in names:
            c = _cols.get(n.lower())
            if c:
                return c
        return None

    g_col = _pick("gender", "sex", "target gender", "product gender")
    ag_col = _pick("age group", "age_group", "age", "target age", "age range")

    def safe_series(col):
        if col and col in df.columns:
            return df[col].astype(str).str.strip().str.lower()
        return pd.Series("", index=df.index, dtype="string")

    g = safe_series(g_col)
    ag = safe_series(ag_col)
    kids = ag == "kids"

    age_gender_norm = pd.Series("", index=df.index, dtype="string")
    age_gender_norm = age_gender_norm.mask(kids & (g == "male"), "boys")
    age_gender_norm = age_gender_norm.mask(kids & (g == "female"), "girls")
    age_gender_norm = age_gender_norm.mask(kids & (g == "unisex"), "childrens")
    age_gender_norm = age_gender_norm.mask(~kids & (g == "male"), "mens")
    age_gender_norm = age_gender_norm.mask(~kids & (g == "female"), "womens")

    df = df.copy()
    df["Age Gender Segment"] = age_gender_norm.fillna("")
    return df


def default_keep_map(df: pd.DataFrame) -> dict:
    """Build the default column keep_map: recommended cols on, rest off."""
    norm_to_orig = {norm(c): c for c in df.columns}
    recommended_norm = {norm(x) for x in RECOMMENDED_RAW}
    initial = {c: False for c in df.columns}
    for rn in recommended_norm:
        if rn in norm_to_orig:
            initial[norm_to_orig[rn]] = True
    if not any(initial.values()):
        initial = {c: True for c in df.columns}
    return initial


def apply_column_selection(df: pd.DataFrame, keep_map: dict) -> pd.DataFrame:
    """Return DataFrame with only kept columns."""
    keep_cols = [c for c, k in keep_map.items() if k and c in df.columns]
    return df[keep_cols].copy() if keep_cols else df.copy()


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Apply multiselect filters. filters = {col: [val, ...]}"""
    out = df.copy()
    for col, vals in filters.items():
        if col in out.columns and vals:
            out = out[out[col].isin(vals)]
    return out


def count_products(df: pd.DataFrame) -> int:
    """Return distinct product count (by id column if found, else row count)."""
    id_candidates = {"id", "item id", "item_id", "offer id", "offer_id", "product_id"}
    id_col = next((c for c in df.columns if c.lower() in id_candidates), None)
    if id_col:
        return int(df[id_col].astype(str).str.strip().replace({"": pd.NA}).nunique(dropna=True))
    return len(df)


def filter_options(df: pd.DataFrame, max_unique: int = 50) -> dict:
    """Return {col: [options]} for columns with 2..max_unique distinct values."""
    result = {}
    for col in df.columns:
        series = df[col].astype(str).str.strip()
        uniques = series[series.ne("")].unique()
        if 2 <= len(uniques) <= max_unique:
            result[col] = sorted(uniques.tolist())
    return result
