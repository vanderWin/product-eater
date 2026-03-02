"""Google Ads opportunity scoring model."""
import math
import pandas as pd

# Stock multiplier — continuous log curve on avg units per product.
# Anchor = the avg qty/product at which a keyword is considered "fully stocked".
# Keywords below this get a partial weight; above it are capped at 1.0.
_STOCK_ANCHOR = 50.0

# Search volume curve divisor — lower = harsher penalty on low-volume terms.
# Floor prevents zero-volume keywords (no GBP data) from scoring 0 entirely.
_SV_DIVISOR = 2_000.0
_SV_FLOOR = 0.1

# Value multiplier — continuous log curve on sales per product group (SPG).
# Anchor = the SPG at which a keyword earns full value weight.
# Low-ticket keywords (e.g. gloves at £29/group) are soft-discounted, not excluded.
_VALUE_ANCHOR = 5_000.0


def compute_opportunity(combined_df: pd.DataFrame, gads_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge combined_df with gads_df.
    Returns final_view DataFrame with all columns in presentation order.
    """
    if gads_df is None or gads_df.empty or combined_df is None or combined_df.empty:
        return pd.DataFrame()

    left = combined_df.assign(keyword_norm=combined_df["keyword"].str.lower().str.strip())
    final_view = left.merge(gads_df, on="keyword_norm", how="left").drop(columns=["keyword_norm"])
    final_view["exact_match"] = (
        final_view["keyword"].str.lower().str.strip()
        .eq(final_view.get("canonical_keyword", ""))
    )
    final_view["matched_to"] = final_view.get("canonical_keyword", "")

    ordered = [
        "list_name", "keyword", "Product Count", "Unique Product Groups",
        "Clicks (28d)", "Clicks Monthly Est",
        "Total Quantity", "Sales Currency", "Total Sales Value",
        "avg_monthly_searches", "competition_level", "competition_index",
        "year", "month", "monthly_searches",
        "exact_match", "matched_to", "close_variants",
    ]
    final_view = final_view[
        [c for c in ordered if c in final_view.columns]
        + [c for c in final_view.columns if c not in ordered]
    ]

    return final_view


def compute_sales_opportunity(final_view: pd.DataFrame) -> pd.DataFrame:
    """
    Produce a keyword-level Sales Opportunity Sheet from a final_view DataFrame.

    Deduplicates monthly rows to one row per (list_name, keyword), drops the
    per-month columns, then applies the scoring model:

        Score = Clicks_Monthly × ln(Unique_Product_Groups) × Stock_Multiplier × Search_Volume_Multiplier × Value_Multiplier

    All component columns are included in the output so analysts can audit
    what's driving each result.
    """
    if final_view is None or final_view.empty:
        return pd.DataFrame()

    # Drop per-month columns and any legacy columns that may exist in old parquet files
    _drop = {
        "year", "month", "monthly_searches", "canonical_keyword",
        "Capture Rate", "Baseline Capture Rate", "Expected Clicks",
        "Potential Click Upside", "Value per Click", "Opportunity Value",
        "Opportunity Score",
    }
    keep_cols = [c for c in final_view.columns if c not in _drop]
    df = (
        final_view[keep_cols]
        .drop_duplicates(subset=["list_name", "keyword"])
        .copy()
        .reset_index(drop=True)
    )

    # Numeric coercions
    for col in ["Clicks Monthly Est", "Clicks (28d)", "Unique Product Groups",
                "Total Quantity", "avg_monthly_searches"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Base demand: prefer Clicks Monthly Est, fall back to Clicks (28d) × 30/28
    if "Clicks Monthly Est" in df.columns:
        clicks_monthly = df["Clicks Monthly Est"].copy()
    else:
        clicks_monthly = pd.Series(pd.NA, index=df.index, dtype=float)
    if "Clicks (28d)" in df.columns:
        fallback = df["Clicks (28d)"].fillna(0.0) * (30.0 / 28.0)
        clicks_monthly = clicks_monthly.where(clicks_monthly.notna(), fallback)
    df["Clicks Monthly"] = pd.to_numeric(clicks_monthly, errors="coerce").fillna(0.0).clip(lower=0.0)

    # Catalogue depth: ln(Unique Product Groups), floored at 1 to avoid ln(0)
    upg = (
        df["Unique Product Groups"].fillna(0.0).clip(lower=0.0)
        if "Unique Product Groups" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    df["Log Unique Product Groups"] = upg.map(lambda x: round(math.log(max(float(x), 1.0)), 4))

    # Stock multiplier — continuous log curve on avg units per product.
    # Using avg rather than raw total avoids penalising broad keywords simply
    # for having many products (Total Quantity scales with Product Count).
    qty = (
        df["Total Quantity"].fillna(0.0)
        if "Total Quantity" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    pc = (
        df["Product Count"].fillna(1.0).clip(lower=1.0)
        if "Product Count" in df.columns
        else pd.Series(1.0, index=df.index)
    )
    avg_qty = (qty / pc).clip(lower=0.0)
    log_anchor = math.log1p(_STOCK_ANCHOR)
    df["Stock Multiplier"] = avg_qty.map(
        lambda q: round(min(math.log1p(float(q)) / log_anchor, 1.0), 4)
    )

    # Search volume dampener — floored at _SV_FLOOR so keywords with no
    # reported search volume (common for precise long-tail phrases) are not
    # silently zeroed out. They receive reduced but non-zero weight.
    sv = (
        df["avg_monthly_searches"].fillna(0.0).clip(lower=0.0)
        if "avg_monthly_searches" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    df["Search Volume Multiplier"] = sv.map(
        lambda v: round(max(1.0 - math.exp(-float(v) / _SV_DIVISOR), _SV_FLOOR), 4)
    )

    # Value multiplier — continuous log curve on sales per product group.
    # Normalises Total Sales Value by catalogue breadth so broad terms don't
    # dominate purely through scale. Low-ticket keywords are soft-discounted;
    # nothing is hard-excluded.
    sales = (
        df["Total Sales Value"].fillna(0.0).clip(lower=0.0)
        if "Total Sales Value" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    spg = (sales / upg.clip(lower=1.0)).clip(lower=0.0)
    log_value_anchor = math.log1p(_VALUE_ANCHOR)
    df["Value Multiplier"] = spg.map(
        lambda v: round(min(math.log1p(float(v)) / log_value_anchor, 1.0), 4)
    )

    # Final raw score
    df["Opportunity Score"] = (
        df["Clicks Monthly"]
        * df["Log Unique Product Groups"]
        * df["Stock Multiplier"]
        * df["Search Volume Multiplier"]
        * df["Value Multiplier"]
    ).round(2)

    # Priority Score: log10 normalisation of raw score to a 0–10 scale,
    # capped at the 95th percentile so outlier broad terms don't compress
    # the rest of the distribution. Rounded to 1 decimal for readability.
    raw = df["Opportunity Score"].fillna(0.0).clip(lower=0.0)
    nonzero = raw[raw > 0]
    if not nonzero.empty:
        cap = max(float(nonzero.quantile(0.95)), 1.0)
        log_cap = math.log10(cap + 1)
        df["Priority Score"] = raw.map(
            lambda s: round(min(10.0 * math.log10(float(s) + 1) / log_cap, 10.0), 1)
        )
    else:
        df["Priority Score"] = 0.0

    # Drop intermediate/audit columns before presenting to client
    _output_drop = {
        "Clicks Monthly Est", "Clicks Monthly",
        "Log Unique Product Groups", "Stock Multiplier",
        "Search Volume Multiplier", "Value Multiplier",
        "competition_level", "competition_index",
        "low_top_of_page_bid_micros", "high_top_of_page_bid_micros",
        "exact_match", "matched_to", "close_variants",
    }
    df = df.drop(columns=[c for c in _output_drop if c in df.columns])

    if "avg_monthly_searches" in df.columns:
        df = df.rename(columns={"avg_monthly_searches": "Avg. Monthly Searches"})

    ordered = [
        "list_name", "keyword",
        "Main Category", "Penultimate Category", "Final Category",
        "Priority Score",
        "Product Count", "Unique Product Groups",
        "Clicks (28d)",
        "Total Quantity", "Sales Currency", "Total Sales Value",
        "Avg. Monthly Searches",
        "Opportunity Score",
    ]
    df = df[
        [c for c in ordered if c in df.columns]
        + [c for c in df.columns if c not in ordered]
    ]

    return df.sort_values("Priority Score", ascending=False, ignore_index=True)
