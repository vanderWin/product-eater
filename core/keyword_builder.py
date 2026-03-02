"""Keyword combination builder."""
import re
import math
import itertools
import pandas as pd
from .utils import norm, to_number, to_ccy

INVALIDS = {"", "nan", "none", "null", "<na>"}

OUT_COLS = [
    "keyword", "Product Count", "Unique Product Groups",
    "Clicks (28d)", "Clicks Monthly Est",
    "Total Quantity", "Sales Currency", "Total Sales Value",
]

# Category columns produced by core.category_extraction — carried through if present.
CATEGORY_COLS = ["Main Category", "Penultimate Category", "Final Category"]


def _category_mode(s: pd.Series) -> str:
    """Return the most common non-empty, non-invalid category value in a series."""
    vals = s.astype(str).str.strip()
    vals = vals[~vals.str.lower().isin(INVALIDS)]
    if vals.empty:
        return ""
    m = vals.mode()
    return m.iloc[0] if not m.empty else ""


def make_keywords(df: pd.DataFrame, cols: list, explode_ampersand: bool = False) -> pd.DataFrame:
    """Build keyword DataFrame from column combinations."""
    if not cols:
        return pd.DataFrame(columns=OUT_COLS)

    sub = df[cols].copy()
    for c in cols:
        sub[c] = sub[c].where(sub[c].notna(), "")
        sub[c] = sub[c].astype(str).str.strip().str.lower()
    sub = sub.replace({"<na>": ""})

    valid_mask = (~sub.isin(INVALIDS)).all(axis=1)
    if not valid_mask.any():
        return pd.DataFrame(columns=OUT_COLS)

    valid_sub = sub.loc[valid_mask]

    if explode_ampersand:
        def _parts(v: str) -> list:
            txt = str(v).strip()
            if "&" not in txt:
                return [txt]
            parts = [p.strip() for p in re.split(r"\s*&\s*", txt) if p.strip()]
            return parts if parts else [txt]

        expanded_keywords, expanded_src_idx = [], []
        for src_idx, row in valid_sub.iterrows():
            token_lists = [_parts(v) for v in row.values]
            for combo_vals in itertools.product(*token_lists):
                kw = re.sub(r"\s+", " ", " ".join(combo_vals)).strip()
                if kw and kw not in INVALIDS:
                    expanded_keywords.append(kw)
                    expanded_src_idx.append(src_idx)

        if not expanded_keywords:
            return pd.DataFrame(columns=OUT_COLS)

        tmp = pd.DataFrame({"keyword": expanded_keywords})
        row_ix = pd.Index(expanded_src_idx)
    else:
        s = valid_sub.apply(lambda row: " ".join(row.values), axis=1)
        s = s.str.replace(r"\s+", " ", regex=True).str.strip()
        tmp = pd.DataFrame({"keyword": s})
        row_ix = tmp.index

    # Value fields
    n2o = {norm(c): c for c in df.columns}
    price_col = n2o.get(norm("price"))
    quantity_col = n2o.get(norm("quantity")) or n2o.get(norm("qauntity"))
    clicks_col = (
        n2o.get(norm("all clicks"))
        or n2o.get(norm("all_clicks"))
        or n2o.get(norm("28 day clicks"))
        or n2o.get(norm("clicks"))
    )

    tmp["_quantity"] = df.loc[row_ix, quantity_col].map(to_number).fillna(0.0).to_numpy() if quantity_col else 0.0
    tmp["_clicks_28d"] = df.loc[row_ix, clicks_col].map(to_number).fillna(0.0).to_numpy() if clicks_col else 0.0

    if price_col:
        tmp["_price_amount"] = df.loc[row_ix, price_col].map(to_number).to_numpy()
        tmp["_currency"] = df.loc[row_ix, price_col].map(to_ccy).to_numpy()
        tmp["_sales_value"] = tmp["_quantity"] * tmp["_price_amount"].fillna(0.0)
    else:
        tmp["_currency"] = ""
        tmp["_sales_value"] = math.nan

    if "image_group_id" in df.columns:
        tmp["image_group_id"] = df.loc[row_ix, "image_group_id"].to_numpy()
        out = tmp.groupby("keyword", as_index=False).agg(**{
            "Product Count": ("keyword", "size"),
            "Unique Product Groups": ("image_group_id", lambda x: x.dropna().nunique()),
            "Clicks (28d)": ("_clicks_28d", "sum"),
            "Total Quantity": ("_quantity", "sum"),
            "_sales_value": ("_sales_value", "sum"),
        })
    else:
        out = tmp.groupby("keyword", as_index=False).agg(**{
            "Product Count": ("keyword", "size"),
            "Clicks (28d)": ("_clicks_28d", "sum"),
            "Total Quantity": ("_quantity", "sum"),
            "_sales_value": ("_sales_value", "sum"),
        })
        out["Unique Product Groups"] = out["Product Count"]

    if price_col:
        ccy_sets = (
            tmp.assign(_currency=tmp["_currency"].replace("", pd.NA))
            .groupby("keyword")["_currency"]
            .agg(lambda x: sorted(set(x.dropna().tolist())))
        )
        out = out.merge(ccy_sets.rename("_currency_set"), left_on="keyword", right_index=True, how="left")
        out["Sales Currency"] = out["_currency_set"].apply(
            lambda xs: xs[0] if isinstance(xs, list) and len(xs) == 1
            else ("MIXED" if isinstance(xs, list) and len(xs) > 1 else "(unknown)")
        )
        out["Total Sales Value"] = out["_sales_value"].where(out["Sales Currency"].ne("MIXED"), math.nan)
        out["Total Sales Value"] = pd.to_numeric(out["Total Sales Value"], errors="coerce").round().astype("Int64")
        out = out.drop(columns=["_currency_set"])
    else:
        out["Sales Currency"] = "(n/a)"
        out["Total Sales Value"] = pd.Series(pd.NA, index=out.index, dtype="Int64")

    out["Clicks Monthly Est"] = (
        pd.to_numeric(out["Clicks (28d)"], errors="coerce").fillna(0.0) * (30.0 / 28.0)
    ).round(2)
    out["Total Quantity"] = pd.to_numeric(out["Total Quantity"], errors="coerce").fillna(0.0)

    out = out.sort_values(
        ["Product Count", "Unique Product Groups", "Total Quantity", "keyword"],
        ascending=[False, False, False, True],
    ).reset_index(drop=True)

    # Category columns: if any were produced by extract_categories, aggregate by
    # mode (most common value across all products contributing to each keyword).
    # Blanks are left blank — no invented values for keywords that genuinely span
    # many unrelated categories or where extraction was not run.
    cat_cols_present = [c for c in CATEGORY_COLS if c in df.columns]
    if cat_cols_present:
        cat_tmp = pd.DataFrame({"keyword": tmp["keyword"].values})
        for cat_col in cat_cols_present:
            cat_tmp[cat_col] = df.loc[row_ix, cat_col].fillna("").astype(str).str.strip().values
        cat_agg = cat_tmp.groupby("keyword")[cat_cols_present].agg(_category_mode).reset_index()
        out = out.merge(cat_agg, on="keyword", how="left")
        for cat_col in cat_cols_present:
            out[cat_col] = out[cat_col].fillna("")

    extra_cat = [c for c in CATEGORY_COLS if c in out.columns]
    return out[[c for c in OUT_COLS if c in out.columns] + extra_cat]
