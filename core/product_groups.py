"""Product grouping logic and feed label rollup."""
import re
import math
import pandas as pd
from .utils import norm, to_number, to_ccy, fmt_int, fmt_qty, fmt_sales

_url_re = re.compile(r"https?://[^\s,]+", flags=re.I)
INVALIDS = {"", "nan", "none", "null", "<na>"}


def _first_url(v: str) -> str:
    if not isinstance(v, str):
        v = str(v) if v is not None else ""
    m = _url_re.findall(v)
    return m[0].strip() if m else ""


def _distinct_non_empty(df: pd.DataFrame, col: str) -> int:
    """Count distinct non-empty values in a column."""
    s = df[col].astype(str).str.strip()
    return int(s[~s.str.lower().isin(INVALIDS)].nunique())


def group_candidates(df: pd.DataFrame) -> list:
    """Return ordered list of candidate grouping columns.

    Up to 3 entries:
      1. item_group_id (if present) — literal group string
      2. image_link (if present)
      3. additional_image_link (if present)
    """
    n2o = {norm(c): c for c in df.columns}
    group_id_col = n2o.get(norm("item group id"))
    cands = [group_id_col] if group_id_col else []

    img_col = n2o.get(norm("image link")) or n2o.get(norm("image_link"))
    add_col = n2o.get(norm("additional image link")) or n2o.get(norm("additional_image_link"))

    if img_col and img_col not in cands:
        cands.append(img_col)
    if add_col and add_col not in cands:
        cands.append(add_col)
    return cands


def compute_group_stats(df: pd.DataFrame, candidates: list) -> dict:
    """Return stats dict {col: {keys, has_key, group_count, sku_count}}."""
    n2o = {norm(c): c for c in df.columns}
    group_id_col = n2o.get(norm("item group id"))
    stats = {}
    for col in candidates:
        raw = df[col].astype(str).str.strip()
        if group_id_col and col == group_id_col:
            keys = raw
            has_key = ~raw.str.lower().isin(INVALIDS)
        else:
            keys = raw.map(_first_url).str.strip()
            has_key = keys.ne("")
        stats[col] = {
            "keys": keys,
            "has_key": has_key,
            "group_count": int(keys[has_key].nunique()),
            "sku_count": int(has_key.sum()),
        }
    return stats


def apply_grouping(df: pd.DataFrame, group_col: str, stats: dict) -> pd.DataFrame:
    """Assign stable 1-based image_group_id to df rows."""
    df = df.copy()
    keys = stats[group_col]["keys"]
    has_key = stats[group_col]["has_key"]
    codes = pd.Series(pd.NA, index=df.index, dtype="Int64")
    if has_key.any():
        fact = pd.factorize(keys[has_key])[0] + 1
        codes.loc[has_key] = pd.array(fact, dtype="Int64")
    df["image_group_id"] = codes
    return df


def feed_label_rollup(df: pd.DataFrame) -> pd.DataFrame:
    """Compute feed-label-level rollup (SKUs, product groups, quantity, sales)."""
    n2o = {norm(c): c for c in df.columns}
    feed_label_col = n2o.get(norm("feed label"))
    price_col = n2o.get(norm("price"))
    quantity_col = n2o.get(norm("quantity")) or n2o.get(norm("qauntity"))

    roll = df.copy()
    roll["_feed_label"] = (
        roll[feed_label_col].astype(str).str.strip().replace("", "(blank)")
        if feed_label_col else "(all)"
    )

    roll["_quantity"] = roll[quantity_col].map(to_number).fillna(0.0) if quantity_col else 0.0

    if price_col:
        roll["_price_amount"] = roll[price_col].map(to_number)
        roll["_currency"] = roll[price_col].map(to_ccy).replace("", "(unknown)")
        roll["_sales_value"] = roll["_price_amount"].fillna(0.0) * roll["_quantity"]

        result = (
            roll.groupby(["_feed_label", "_currency"], as_index=False)
            .agg(**{
                "SKU Rows": ("_feed_label", "size"),
                "Product Groups": ("image_group_id", lambda x: x.dropna().nunique()),
                "Total Quantity": ("_quantity", "sum"),
                "Total Sales Value": ("_sales_value", "sum"),
            })
            .sort_values(["_feed_label", "_currency"])
            .rename(columns={"_feed_label": "Feed Label", "_currency": "Currency"})
            .reset_index(drop=True)
        )
    else:
        result = (
            roll.groupby("_feed_label", as_index=False)
            .agg(**{
                "SKU Rows": ("_feed_label", "size"),
                "Product Groups": ("image_group_id", lambda x: x.dropna().nunique()),
                "Total Quantity": ("_quantity", "sum"),
            })
            .sort_values("_feed_label")
            .rename(columns={"_feed_label": "Feed Label"})
            .reset_index(drop=True)
        )

    return result


def format_rollup(rollup: pd.DataFrame) -> pd.DataFrame:
    """Return a display-formatted copy of the rollup DataFrame."""
    out = rollup.copy()
    if "SKU Rows" in out.columns:
        out["SKU Rows"] = out["SKU Rows"].map(fmt_int)
    if "Product Groups" in out.columns:
        out["Product Groups"] = out["Product Groups"].map(fmt_int)
    if "Total Quantity" in out.columns:
        out["Total Quantity"] = out["Total Quantity"].map(fmt_qty)
    if "Total Sales Value" in out.columns:
        ccy_col = out.get("Currency", pd.Series([""] * len(out), index=out.index))
        out["Total Sales Value"] = [
            fmt_sales(v, c) for v, c in zip(out["Total Sales Value"], ccy_col)
        ]
    return out
