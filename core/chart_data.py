"""Chart.js config builders for search volume charts."""
import pandas as pd

PALETTE = ["#4C6A92", "#6F8F72", "#B97A57", "#8C6C99", "#C4A46B", "#5B7C99", "#9E6E6E"]
MONTH_ORDER = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]
BRAND = "#2b0573"


def build_chart_data(combined_df: pd.DataFrame, gads_df: pd.DataFrame) -> tuple:
    """
    Build the chart DataFrame from combined_df + gads_df.
    Returns (fv, totals_df) where fv has a date column.
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


def _base_options(title):
    return {
        "responsive": True,
        "maintainAspectRatio": False,
        "plugins": {
            "legend": {
                "position": "bottom",
                "labels": {"color": BRAND, "boxWidth": 12},
            },
            "title": {"display": True, "text": title, "color": BRAND, "font": {"size": 14}},
        },
    }


def chronological_spec(fv: pd.DataFrame, chosen_lists: list, legend_sort: list) -> dict:
    """Return Chart.js config for chronological search volume line chart."""
    subset = fv[fv["list_name"].isin(chosen_lists)] if chosen_lists else fv
    chron = (
        subset.groupby(["list_name", "date"], as_index=False)["monthly_searches"]
        .sum()
        .sort_values("date")
    )
    chron["date_label"] = chron["date"].dt.strftime("%b %Y")

    # Unique ordered labels
    labels = (
        chron[["date", "date_label"]].drop_duplicates()
        .sort_values("date")["date_label"]
        .tolist()
    )

    datasets = []
    for i, lst in enumerate(legend_sort):
        if lst not in chosen_lists:
            continue
        row = chron[chron["list_name"] == lst]
        data_map = dict(zip(row["date_label"], row["monthly_searches"].round(0)))
        colour = PALETTE[i % len(PALETTE)]
        datasets.append({
            "label": lst,
            "data": [data_map.get(lbl) for lbl in labels],
            "borderColor": colour,
            "pointBackgroundColor": colour,
            "backgroundColor": colour,
            "fill": False,
            "tension": 0.3,
            "pointRadius": 3,
            "borderWidth": 2,
        })

    options = _base_options("Total search volume by list")
    options["scales"] = {
        "x": {
            "title": {"display": True, "text": "Month", "color": BRAND},
            "ticks": {"color": BRAND, "maxTicksLimit": 12},
            "grid": {"display": False},
        },
        "y": {
            "title": {"display": True, "text": "Total searches", "color": BRAND},
            "ticks": {"color": BRAND, "callback": "__SHORTNUM__"},
            "grid": {"display": False},
            "beginAtZero": True,
        },
    }

    return {"type": "line", "data": {"labels": labels, "datasets": datasets}, "options": options}


def seasonality_spec(fv: pd.DataFrame, chosen_lists: list, legend_sort: list) -> dict:
    """Return Chart.js config for seasonality index (normalised Jan-Dec) line chart."""
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
    # Multiply by 100 so values are 0-100 (avoids needing a JS callback for % format)
    season["pct_of_peak"] = ((season["monthly_searches"] / denom) * 100).round(1)

    short_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    datasets = []
    for i, lst in enumerate(legend_sort):
        if lst not in chosen_lists:
            continue
        row = season[season["list_name"] == lst].sort_values("month_name")
        data_map = dict(zip(row["month_name"].astype(str), row["pct_of_peak"]))
        colour = PALETTE[i % len(PALETTE)]
        datasets.append({
            "label": lst,
            "data": [data_map.get(m) for m in MONTH_ORDER],
            "borderColor": colour,
            "pointBackgroundColor": colour,
            "backgroundColor": colour,
            "fill": False,
            "tension": 0.3,
            "pointRadius": 3,
            "borderWidth": 2,
        })

    options = _base_options("Seasonality by list (% of peak month)")
    options["scales"] = {
        "x": {
            "title": {"display": True, "text": "Month", "color": BRAND},
            "ticks": {"color": BRAND},
            "grid": {"display": False},
        },
        "y": {
            "title": {"display": True, "text": "% of peak", "color": BRAND},
            "ticks": {
                "color": BRAND,
                "callback": "__PERCENT__",  # replaced in JS with actual formatter
            },
            "min": 0,
            "max": 100,
            "grid": {"display": False},
        },
    }

    return {"type": "line", "data": {"labels": short_labels, "datasets": datasets}, "options": options}
