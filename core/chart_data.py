"""Vega-Lite chart spec builders (replaces Altair)."""
import pandas as pd

PALETTE = ["#4C6A92", "#6F8F72", "#B97A57", "#8C6C99", "#C4A46B", "#5B7C99", "#9E6E6E"]
MONTH_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

AXIS_CONFIG = {
    "axis": {"grid": False, "labelColor": "#2b0573", "titleColor": "#2b0573"},
    "legend": {"labelColor": "#2b0573", "titleColor": "#2b0573"},
    "view": {"strokeWidth": 0, "fill": "transparent"},
}


def build_chart_data(combined_df: pd.DataFrame, gads_df: pd.DataFrame) -> tuple:
    """
    Build the chart DataFrame from combined_df + gads_df.
    Returns (fv, totals_df) where fv has date column.
    """
    if gads_df is None or gads_df.empty or combined_df is None or combined_df.empty:
        return None, None

    left = combined_df.assign(keyword_norm=combined_df["keyword"].str.lower().str.strip())
    fv = left.merge(gads_df, on="keyword_norm", how="left").dropna(
        subset=["year", "month", "monthly_searches"]
    )
    fv["date"] = pd.to_datetime(
        dict(year=fv["year"].astype(int), month=fv["month"].astype(int), day=1)
    )
    totals = (
        fv.groupby("list_name", as_index=False)["monthly_searches"]
        .sum()
        .sort_values("monthly_searches", ascending=False)
    )
    return fv, totals


def chronological_spec(fv: pd.DataFrame, chosen_lists: list, legend_sort: list) -> dict:
    """Return Vega-Lite spec for chronological search volume chart."""
    subset = fv[fv["list_name"].isin(chosen_lists)] if chosen_lists else fv
    chron = (
        subset.groupby(["list_name", "date"], as_index=False)["monthly_searches"]
        .sum()
        .sort_values("date")
    )
    # Convert datetime to ISO string for JSON serialisation
    chron["date"] = chron["date"].dt.strftime("%Y-%m-%d")
    records = chron.to_dict(orient="records")

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": records},
        "mark": {"type": "line", "point": True, "strokeWidth": 2, "interpolate": "monotone"},
        "encoding": {
            "x": {
                "field": "date", "type": "temporal", "title": "Month",
                "axis": {"format": "%b '%y"},
            },
            "y": {
                "field": "monthly_searches", "type": "quantitative",
                "title": "Total searches",
                "axis": {"format": "~s"},
            },
            "color": {
                "field": "list_name", "type": "nominal", "title": "List",
                "scale": {"range": PALETTE},
                "sort": legend_sort,
            },
            "tooltip": [
                {"field": "list_name", "title": "List"},
                {"field": "date", "type": "temporal", "title": "Month", "format": "%b %Y"},
                {"field": "monthly_searches", "type": "quantitative", "title": "Searches", "format": "~s"},
            ],
        },
        "title": "Chronological search volume",
        "width": "container",
        "height": 320,
        "config": AXIS_CONFIG,
    }


def seasonality_spec(fv: pd.DataFrame, chosen_lists: list, legend_sort: list) -> dict:
    """Return Vega-Lite spec for seasonality index chart."""
    subset = fv[fv["list_name"].isin(chosen_lists)] if chosen_lists else fv
    subset = subset.copy()
    subset["month_name"] = pd.Categorical(
        subset["date"].dt.month_name(), categories=MONTH_ORDER, ordered=True
    )
    season = (
        subset.groupby(["list_name", "month_name"], as_index=False, observed=True)["monthly_searches"]
        .sum()
        .dropna(subset=["month_name"])
        .sort_values(["list_name", "month_name"])
    )
    denom = season.groupby("list_name")["monthly_searches"].transform("max").clip(lower=1)
    season["frac_of_peak"] = season["monthly_searches"] / denom
    season["month_name"] = season["month_name"].astype(str)
    records = season[["list_name", "month_name", "frac_of_peak"]].to_dict(orient="records")

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "data": {"values": records},
        "mark": {"type": "line", "point": True, "strokeWidth": 2, "interpolate": "monotone"},
        "encoding": {
            "x": {
                "field": "month_name", "type": "ordinal",
                "sort": MONTH_ORDER, "title": "Month (Jan\u2192Dec)",
            },
            "y": {
                "field": "frac_of_peak", "type": "quantitative",
                "title": "Seasonality",
                "scale": {"domain": [0, 1]},
                "axis": {"format": ".0%", "values": [0, 0.2, 0.4, 0.6, 0.8, 1]},
            },
            "color": {
                "field": "list_name", "type": "nominal", "title": "List",
                "scale": {"range": PALETTE},
                "sort": legend_sort,
            },
            "tooltip": [
                {"field": "list_name", "title": "List"},
                {"field": "month_name", "title": "Month"},
                {"field": "frac_of_peak", "type": "quantitative", "title": "% of peak", "format": ".0%"},
            ],
        },
        "title": "Seasonality by list (normalised)",
        "width": "container",
        "height": 320,
        "config": AXIS_CONFIG,
    }
