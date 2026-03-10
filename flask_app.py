"""
Product Feed Eater — Flask application.
This file replaces the Streamlit app.py when deployment is ready.
Rename to app.py (or keep as flask_app.py during development alongside the Streamlit version).
"""
from dotenv import load_dotenv
load_dotenv()

import io
import json
import math
import os
import threading
import time
import uuid
from pathlib import Path

import pandas as pd
from flask import (
    Flask, flash, redirect, render_template, request,
    send_file, session, url_for, Response, stream_with_context,
)
from flask_session import Session

from config import Config
from session_helpers import (
    save_parquet, load_parquet, save_json, load_json,
    clear_session_files, cleanup_old_sessions,
)
from core.feed_loader import hash_bytes, excel_sheet_names, load_feed
from core.feed_processor import (
    derive_age_gender_segment, default_keep_map,
    apply_column_selection, apply_filters,
    count_products, filter_options,
)
from core.product_groups import (
    group_candidates, compute_group_stats, apply_grouping,
    feed_label_rollup, format_rollup,
)
from core.colour_mapping import (
    detect_colour_columns, colour_summary, load_colour_mapping,
    unmapped_colours, apply_colour_mapping, merge_new_mappings, mapping_breakdown, add_suggestions,
)
from core.category_extraction import extract_categories, preferred_category_column
from core.keyword_builder import make_keywords
from core.gads_client import get_gads_client_and_customer_id, load_gads_constants
from core.gads_opportunity import compute_opportunity, compute_sales_opportunity
from core.chart_data import build_chart_data, chronological_spec, seasonality_spec
from core.exporters import to_csv_bytes, to_xlsx_bytes

app = Flask(__name__)
app.config.from_object(Config)
Session(app)


@app.context_processor
def inject_globals():
    import datetime
    return {
        "now": datetime.datetime.now(),
        "session": session,
    }


# Jinja2 extension: enumerate filter for templates
app.jinja_env.globals["enumerate"] = enumerate


PREVIEW_ROWS = 100


@app.template_filter("fmt_whole")
def fmt_whole(value):
    """Render numeric values as whole numbers with comma separators."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "<na>", "nat"}:
        return ""
    try:
        n = float(str(value).replace(",", ""))
    except Exception:
        return s
    if math.isnan(n) or math.isinf(n):
        return ""
    return f"{int(round(n)):,}"


def _as_oob(html: str, element_id: str) -> str:
    """Mark a rendered section as HTMX out-of-band swap content."""
    return html.replace(
        f'id="{element_id}"',
        f'id="{element_id}" hx-swap-oob="true"',
        1,
    )


def _find_clicks_col(df: pd.DataFrame):
    """Return the 'all clicks' column name from df, or None."""
    from core.utils import norm
    n2o = {norm(c): c for c in df.columns}
    return (
        n2o.get(norm("all clicks"))
        or n2o.get(norm("all_clicks"))
        or n2o.get(norm("28 day clicks"))
        or n2o.get(norm("clicks"))
    )


def _clicks_top_idx(df: pd.DataFrame):
    """Return index label of the row with highest clicks, or the first row's index."""
    clicks_col = _find_clicks_col(df)
    if clicks_col:
        nums = pd.to_numeric(df[clicks_col].astype(str).str.extract(r"([\d.]+)")[0], errors="coerce")
        if nums.notna().any():
            return nums.idxmax()
    return df.index[0] if len(df) else None


def _clicks_sorted_preview(raw_df: pd.DataFrame, kept_df: pd.DataFrame) -> pd.DataFrame:
    """Return top PREVIEW_ROWS rows of kept_df ordered by clicks from raw_df."""
    clicks_col = _find_clicks_col(raw_df)
    if clicks_col:
        nums = pd.to_numeric(raw_df[clicks_col].astype(str).str.extract(r"([\d.]+)")[0], errors="coerce")
        sorted_idx = nums.sort_values(ascending=False, na_position="last").index
        kept_df = kept_df.reindex(sorted_idx)
    return kept_df.head(PREVIEW_ROWS)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def sid() -> str:
    """Return or create a stable session ID stored in the Flask session."""
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())
    return session["sid"]


def get_processed_df() -> pd.DataFrame | None:
    """Load raw_df from Parquet and apply all pipeline steps from session state."""
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return None
    keep_map = session.get("keep_map", {})
    filters = session.get("filters", {})
    group_col = session.get("group_col")
    colour_col = session.get("colour_col")
    colour_map_json = load_json(sid(), "colour_map")
    want_main = session.get("want_main", False)
    want_penultimate = session.get("want_penultimate", False)
    want_final = session.get("want_final", False)
    cat_src_col = session.get("cat_src_col")

    df = apply_column_selection(raw, keep_map) if keep_map else raw.copy()
    df = apply_filters(df, filters)

    # Keep generated field labels human-readable and consistent for legacy sessions.
    legacy_generated = {
        "age_gender_segment": "Age Gender Segment",
        "normalised_colour": "Normalised Colour",
        "main_category": "Main Category",
        "penultimate_category": "Penultimate Category",
        "final_category": "Final Category",
    }
    rename_map = {old: new for old, new in legacy_generated.items() if old in df.columns and new not in df.columns}
    if rename_map:
        df = df.rename(columns=rename_map)
    dup_old_cols = [old for old, new in legacy_generated.items() if old in df.columns and new in df.columns]
    if dup_old_cols:
        df = df.drop(columns=dup_old_cols, errors="ignore")

    # Group columns may not be in the user's kept columns — always search raw.
    # Auto-detect the best candidate (item_group_id preferred) when section 3
    # hasn't been configured yet, so Unique Product Groups is always meaningful.
    _raw_for_group = apply_filters(raw, filters)
    _grp_cands = group_candidates(_raw_for_group)
    _grp_col = group_col or (_grp_cands[0] if _grp_cands else None)
    if _grp_col and _grp_col in _raw_for_group.columns and _grp_col in _grp_cands:
        _grp_stats = compute_group_stats(_raw_for_group, _grp_cands)
        df["image_group_id"] = apply_grouping(_raw_for_group, _grp_col, _grp_stats)["image_group_id"]

    if colour_col:
        raw_filtered = apply_filters(raw, filters)
        if colour_col in raw_filtered.columns:
            colour_map = pd.DataFrame(colour_map_json) if colour_map_json else load_colour_mapping()
            if (
                colour_map is not None
                and not colour_map.empty
                and {"product_colour", "generic_colour"}.issubset(colour_map.columns)
            ):
                src_norm = raw_filtered[colour_col].astype(str).str.strip().str.lower()
                lkp = colour_map.set_index("product_colour")["generic_colour"]
                mapped = src_norm.map(lkp).reindex(df.index).fillna("")
                # Keep one canonical mapped colour field for downstream steps.
                generic_like_cols = [c for c in df.columns if c.strip().lower() in {"generic_colour", "generic colour"}]
                if generic_like_cols:
                    df = df.drop(columns=generic_like_cols, errors="ignore")
                df["Normalised Colour"] = mapped

    if cat_src_col and cat_src_col in df.columns:
        df = extract_categories(df, cat_src_col, want_main, want_penultimate, want_final)

    return df


# ─── Main routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cleanup_old_sessions()
    has_file = load_parquet(sid(), "raw_df") is not None
    return render_template("index.html", has_file=has_file)


@app.route("/session/reset")
def session_reset():
    clear_session_files(sid())
    session.clear()
    flash("Session cleared. Upload a new file to begin.", "info")
    return redirect(url_for("index"))


# ─── Upload ───────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("feed_file")
    if not f or not f.filename:
        flash("No file selected.", "warning")
        return redirect(url_for("index"))

    file_bytes = f.read()
    file_hash = hash_bytes(file_bytes)
    filename = f.filename
    ext = Path(filename).suffix.lower()

    # Detect Excel sheet selection
    sheet_name = request.form.get("sheet_name")
    sheets = None
    if ext in {".xlsx", ".xls"}:
        try:
            sheets = list(excel_sheet_names(file_bytes))
        except Exception as e:
            flash(f"Could not read Excel sheets: {e}", "error")
            return redirect(url_for("index"))
        if not sheet_name:
            sheet_name = sheets[0]

    try:
        df = load_feed(file_bytes, filename, sheet_name)
    except Exception as e:
        flash(f"Failed to read file: {e}", "error")
        return redirect(url_for("index"))

    df = derive_age_gender_segment(df)

    # Reset session if new file
    if session.get("file_hash") != file_hash:
        clear_session_files(sid())
        session.clear()
        _ = sid()  # re-create sid
        session["file_hash"] = file_hash
        session["file_name"] = filename
        session["sheet_name"] = sheet_name
        keep_map = default_keep_map(df)
        session["keep_map"] = keep_map
        session["filters"] = {}
        session["combos"] = [{"fields": [], "name": ""}]
        session["want_main"] = False
        session["want_penultimate"] = False
        session["want_final"] = False
        session["cat_src_col"] = preferred_category_column(df)
    save_parquet(sid(), "raw_df", df)

    # Build preview data
    keep_map = session.get("keep_map", {})
    kept = apply_column_selection(df, keep_map)
    product_count = count_products(df)

    top_idx = _clicks_top_idx(df)
    sample = (df.loc[[top_idx]] if top_idx is not None else df.head(1)).astype(str).T.reset_index()
    sample.columns = ["Column", "Example"]
    unique_counts = [
        int(pd.Series(df[c]).astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        for c in df.columns
    ]
    selector_rows = [
        {
            "column": c,
            "keep": bool(keep_map.get(c, False)),
            "example": sample.loc[sample["Column"] == c, "Example"].values[0] if c in sample["Column"].values else "",
            "unique": unique_counts[i],
        }
        for i, c in enumerate(df.columns)
    ]

    return render_template(
        "index.html",
        has_file=True,
        file_name=filename,
        sheet_name=sheet_name,
        sheets=sheets,
        product_count=product_count,
        selector_rows=selector_rows,
        keep_cols=[c for c, k in keep_map.items() if k],
        preview_df=_clicks_sorted_preview(df, kept),
        row_count=len(kept),
        col_count=len(kept.columns),
        show_column_picker=True,
    )


# ─── Step: Column selection ───────────────────────────────────────────────────

@app.route("/step/columns", methods=["POST"])
def step_columns():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return redirect(url_for("index"))

    action = request.form.get("action", "update")
    keep_map = {c: False for c in raw.columns}
    from core.feed_processor import RECOMMENDED_RAW
    from core.utils import norm

    if action == "recommended":
        n2o = {norm(c): c for c in raw.columns}
        for rn in {norm(x) for x in RECOMMENDED_RAW}:
            if rn in n2o:
                keep_map[n2o[rn]] = True
    elif action == "all":
        keep_map = {c: True for c in raw.columns}
    elif action == "none":
        keep_map = {c: False for c in raw.columns}
    elif action == "invert":
        old = session.get("keep_map", {c: False for c in raw.columns})
        keep_map = {c: not old.get(c, False) for c in raw.columns}
    else:
        # Update from form checkboxes
        checked = set(request.form.getlist("keep"))
        keep_map = {c: (c in checked) for c in raw.columns}

    session["keep_map"] = keep_map
    session.modified = True

    kept = apply_column_selection(raw, keep_map)

    top_idx = _clicks_top_idx(raw)
    sample = (raw.loc[[top_idx]] if top_idx is not None else raw.head(1)).astype(str).T.reset_index()
    sample.columns = ["Column", "Example"]
    unique_counts = [
        int(pd.Series(raw[c]).astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        for c in raw.columns
    ]
    selector_rows = [
        {
            "column": c,
            "keep": bool(keep_map.get(c, False)),
            "example": sample.loc[sample["Column"] == c, "Example"].values[0] if c in sample["Column"].values else "",
            "unique": unique_counts[i],
        }
        for i, c in enumerate(raw.columns)
    ]

    col_html = render_template(
        "partials/column_picker.html",
        selector_rows=selector_rows,
        keep_cols=[c for c, k in keep_map.items() if k],
        preview_df=_clicks_sorted_preview(raw, kept),
    )

    # OOB: refresh filter options and category extraction to match new column selection
    options = filter_options(kept)
    filters = session.get("filters", {})
    filtered = apply_filters(kept, filters)
    filter_html = render_template(
        "partials/filters.html",
        options=options,
        filters=filters,
        filtered_count=len(filtered),
        total_count=len(kept),
        keep_col_count=len(kept.columns),
        preview_df=_clicks_sorted_preview(raw, filtered),
        _oob=True,
    )
    categories_html = _render_categories_html(filtered)
    return col_html + filter_html + _as_oob(categories_html, "section-categories")


# ─── Step: Filters ────────────────────────────────────────────────────────────

@app.route("/step/filters", methods=["GET", "POST"])
def step_filters():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return redirect(url_for("index"))

    keep_map = session.get("keep_map", {})
    kept = apply_column_selection(raw, keep_map)
    options = filter_options(kept)
    preview_rows = PREVIEW_ROWS

    if request.method == "POST":
        filters = {}
        for col in options:
            chosen = request.form.getlist(f"filter_{col}")
            if chosen:
                filters[col] = chosen
        session["filters"] = filters
        session.modified = True
    else:
        filters = session.get("filters", {})

    filtered = apply_filters(kept, filters)

    filters_html = render_template(
        "partials/filters.html",
        options=options,
        filters=filters,
        filtered_count=len(filtered),
        total_count=len(kept),
        keep_col_count=len(kept.columns),
        preview_df=filtered.head(preview_rows),
    )

    if request.method == "POST":
        return filters_html + _render_groups_html(oob=True)
    return filters_html


# ─── Step: Product Groups ─────────────────────────────────────────────────────

def _render_groups_html(group_col_override=None, oob=False):
    """Shared helper — renders the product_groups partial from current session state."""
    from core.utils import norm
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return ""
    filters = session.get("filters", {})
    raw_filtered = apply_filters(raw, filters)
    candidates = group_candidates(raw_filtered)
    if not candidates:
        return render_template("partials/product_groups.html", candidates=[], _oob=oob)
    stats = compute_group_stats(raw_filtered, candidates)
    n2o = {norm(c): c for c in raw_filtered.columns}
    group_id_col = n2o.get(norm("item group id"))
    default_col = group_id_col or max(candidates, key=lambda c: stats[c]["group_count"])
    group_col = group_col_override or session.get("group_col", default_col)
    if group_col not in candidates:
        group_col = default_col
    session["group_col"] = group_col
    session.modified = True
    df_grouped = apply_grouping(raw_filtered, group_col, stats)
    rollup = feed_label_rollup(df_grouped)
    rollup_display = format_rollup(rollup)
    return render_template(
        "partials/product_groups.html",
        candidates=candidates,
        stats=stats,
        group_col=group_col,
        rollup=rollup_display,
        _oob=oob,
    )


@app.route("/step/groups", methods=["GET", "POST"])
def step_groups():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return redirect(url_for("index"))
    group_col_override = request.form.get("group_col") if request.method == "POST" else None
    return _render_groups_html(group_col_override=group_col_override)


# ─── Step: Colour Summary ─────────────────────────────────────────────────────

@app.route("/step/colour-summary", methods=["GET", "POST"])
def step_colour_summary():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return redirect(url_for("index"))

    keep_map = session.get("keep_map", {})
    filters = session.get("filters", {})
    kept = apply_column_selection(raw, keep_map)
    filtered = apply_filters(kept, filters)

    colour_candidates = detect_colour_columns(filtered)
    if not colour_candidates:
        return render_template("partials/colour_summary.html", colour_candidates=[])

    if request.method == "POST":
        colour_col = request.form.get("colour_col", colour_candidates[0])
    else:
        colour_col = session.get("colour_col", colour_candidates[0])

    if colour_col not in colour_candidates:
        colour_col = colour_candidates[0]

    session["colour_col"] = colour_col
    session.modified = True

    vc, non_empty = colour_summary(filtered, colour_col)

    return render_template(
        "partials/colour_summary.html",
        colour_candidates=colour_candidates,
        colour_col=colour_col,
        vc=vc,
        non_empty=non_empty,
    )


# ─── Step: Colour Mapping ─────────────────────────────────────────────────────

@app.route("/step/colour-mapping", methods=["GET", "POST"])
def step_colour_mapping():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return render_template("partials/colour_mapping.html", has_colour=False)

    keep_map = session.get("keep_map", {})
    filters = session.get("filters", {})
    colour_col = session.get("colour_col")

    kept = apply_column_selection(raw, keep_map)
    filtered = apply_filters(kept, filters)

    colour_candidates = detect_colour_columns(filtered)
    if not colour_candidates or not colour_col or colour_col not in filtered.columns:
        colour_col = colour_candidates[0] if colour_candidates else None

    if not colour_col:
        return render_template("partials/colour_mapping.html", has_colour=False)

    # Persist chosen colour source column for downstream processing even before save.
    session["colour_col"] = colour_col
    session.modified = True

    base_map = load_colour_mapping()
    if base_map is None:
        return render_template("partials/colour_mapping.html", has_colour=True, map_error=True)

    if request.method == "POST":
        unmapped_colours_list = request.form.getlist("unmapped_colour")
        generic_choices = request.form.getlist("generic_colour_choice")
        new_rows_data = [
            {"product_colour": u.strip().lower(), "generic_colour": g.strip().lower()}
            for u, g in zip(unmapped_colours_list, generic_choices)
            if g.strip()
        ]
        if new_rows_data:
            new_rows = pd.DataFrame(new_rows_data).drop_duplicates(subset=["product_colour"])
            updated_map = merge_new_mappings(base_map, new_rows)
        else:
            updated_map = base_map.copy()

        save_json(sid(), "colour_map", updated_map.to_dict(orient="records"))
        session["colour_col"] = colour_col
        session.modified = True
    else:
        colour_map_json = load_json(sid(), "colour_map")
        updated_map = pd.DataFrame(colour_map_json) if colour_map_json else base_map.copy()

    unmapped = unmapped_colours(filtered, colour_col, updated_map)
    (
        currently_mapped_count,
        newly_mapped_count,
        unmapped_count,
        eligible_count,
        pct_mapped,
        pct_currently,
        pct_newly,
        pct_unmapped,
    ) = mapping_breakdown(filtered, colour_col, base_map, updated_map)
    allowed_generic = sorted(updated_map["generic_colour"].dropna().unique().tolist())
    unmapped = add_suggestions(unmapped, allowed_generic)

    main_html = render_template(
        "partials/colour_mapping.html",
        has_colour=True,
        map_error=False,
        colour_col=colour_col,
        unmapped=unmapped,
        allowed_generic=allowed_generic,
        currently_mapped_count=currently_mapped_count,
        newly_mapped_count=newly_mapped_count,
        unmapped_count=unmapped_count,
        eligible_count=eligible_count,
        pct_mapped=pct_mapped,
        pct_currently=pct_currently,
        pct_newly=pct_newly,
        pct_unmapped=pct_unmapped,
    )

    if request.method == "POST" and request.headers.get("HX-Request") == "true":
        normalised_html = step_normalised_feed()
        return main_html + _as_oob(normalised_html, "section-normalised-feed")

    return main_html


# ─── Step: Category Extraction ────────────────────────────────────────────────

def _render_categories_html(filtered: "pd.DataFrame") -> str:
    """Render category_extraction.html using current session state."""
    default_src = preferred_category_column(filtered)
    cat_src_col = session.get("cat_src_col", default_src)
    if cat_src_col not in filtered.columns:
        cat_src_col = default_src
    want_main = session.get("want_main", False)
    want_penultimate = session.get("want_penultimate", False)
    want_final = session.get("want_final", False)

    preview_df = extract_categories(filtered, cat_src_col, want_main, want_penultimate, want_final)
    preview_cols = [cat_src_col] + [
        c for c in ["Main Category", "Penultimate Category", "Final Category"]
        if c in preview_df.columns
    ]
    return render_template(
        "partials/category_extraction.html",
        all_cols=list(filtered.columns),
        cat_src_col=cat_src_col,
        want_main=want_main,
        want_penultimate=want_penultimate,
        want_final=want_final,
        preview_df=preview_df[preview_cols].head(PREVIEW_ROWS),
    )


@app.route("/step/categories", methods=["GET", "POST"])
def step_categories():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        return redirect(url_for("index"))

    keep_map = session.get("keep_map", {})
    filters = session.get("filters", {})
    kept = apply_column_selection(raw, keep_map)
    filtered = apply_filters(kept, filters)

    if request.method == "POST":
        default_src = preferred_category_column(filtered)
        cat_src_col = request.form.get("cat_src_col", default_src)
        if cat_src_col not in filtered.columns:
            cat_src_col = default_src
        session["cat_src_col"] = cat_src_col
        session["want_main"] = bool(request.form.get("want_main"))
        session["want_penultimate"] = bool(request.form.get("want_penultimate"))
        session["want_final"] = bool(request.form.get("want_final"))
        session.modified = True

    main_html = _render_categories_html(filtered)

    if request.method == "POST" and request.headers.get("HX-Request") == "true":
        normalised_html = step_normalised_feed()
        processed_df = get_processed_df()
        keywords_html = _render_keywords_html(processed_df) if processed_df is not None else ""
        return (
            main_html
            + _as_oob(normalised_html, "section-normalised-feed")
            + (_as_oob(keywords_html, "section-keywords") if keywords_html else "")
        )

    return main_html


# ─── Step: Normalised Feed Preview ────────────────────────────────────────────

@app.route("/step/normalised-feed")
def step_normalised_feed():
    df = get_processed_df()
    if df is None:
        return redirect(url_for("index"))
    preview_rows = PREVIEW_ROWS
    return render_template(
        "partials/normalised_feed.html",
        preview_df=df.head(preview_rows),
        row_count=len(df),
        col_count=len(df.columns),
    )


# ─── Step: Keyword Builder ────────────────────────────────────────────────────

_KEYWORD_PRIORITY_COLS = [
    "Age Gender Segment",
    "Normalised Colour",
    "Main Category",
    "Penultimate Category",
    "Final Category",
]

def _split_keyword_cols(cols: list) -> tuple[list, list]:
    """Return (priority_cols, other_cols) with priority fields first."""
    col_set = set(cols)
    priority = [c for c in _KEYWORD_PRIORITY_COLS if c in col_set]
    # Also surface any column named exactly "material" (case-insensitive)
    for c in cols:
        if c.lower() == "material" and c not in priority:
            priority.append(c)
    priority_set = set(priority)
    other = [c for c in cols if c not in priority_set]
    return priority, other


def _render_keywords_html(df: "pd.DataFrame") -> str:
    """Render keyword_builder.html using current session state and processed df."""
    all_cols = list(df.columns)
    priority_cols, other_cols = _split_keyword_cols(all_cols)
    combos = session.get("combos", [{"fields": [], "name": ""}])
    legacy_field_alias = {
        "age_gender_segment": "Age Gender Segment",
        "normalised_colour": "Normalised Colour",
        "main_category": "Main Category",
        "penultimate_category": "Penultimate Category",
        "final_category": "Final Category",
    }
    combos = [
        {
            "fields": [legacy_field_alias.get(f, f) for f in c.get("fields", [])],
            "name": c.get("name", ""),
            "split_ampersand": bool(c.get("split_ampersand", False)),
        }
        for c in combos
    ]

    raw = load_parquet(sid(), "raw_df")
    top_idx = _clicks_top_idx(raw) if raw is not None else None
    if top_idx is not None and top_idx in df.index:
        ex = df.loc[top_idx]
    elif len(df):
        ex = df.iloc[0]
    else:
        ex = None
    _skip = {"nan", "none", "nat", "<na>", ""}
    example_vals = {}
    if ex is not None:
        for col in all_cols:
            val = str(ex[col]).strip() if col in ex.index else ""
            example_vals[col] = "" if val.lower() in _skip else val[:50]

    per_list_tables = []
    per_list_display_cols = []
    combo_previews = []
    for i, combo in enumerate(combos):
        fields = combo.get("fields", [])
        split_ampersand = bool(combo.get("split_ampersand", False))
        valid_fields = [f for f in fields if f in all_cols]
        kc = make_keywords(df, valid_fields, split_ampersand)
        list_name = combo.get("name") or (f"List {i+1}")
        kc.insert(0, "list_name", list_name)
        per_list_tables.append(kc)
        if kc is not None and not kc.empty and "keyword" in kc.columns:
            combo_previews.append(str(kc.iloc[0]["keyword"]))
        else:
            combo_previews.append("")
        skip = {"Clicks Monthly Est"}
        if (
            "Unique Product Groups" in kc.columns
            and "Product Count" in kc.columns
            and not kc.empty
            and (kc["Unique Product Groups"] == kc["Product Count"]).all()
        ):
            skip.add("Unique Product Groups")
        per_list_display_cols.append([c for c in kc.columns if c not in skip])

    return render_template(
        "partials/keyword_builder.html",
        all_cols=all_cols,
        priority_cols=priority_cols,
        other_cols=other_cols,
        combos=combos,
        per_list_tables=per_list_tables,
        per_list_display_cols=per_list_display_cols,
        combo_previews=combo_previews,
        preview_rows=PREVIEW_ROWS,
        example_vals=example_vals,
    )


@app.route("/step/keywords", methods=["GET", "POST"])
def step_keywords():
    if request.headers.get("HX-Request") != "true":
        return redirect(url_for("index"))

    df = get_processed_df()
    if df is None:
        return redirect(url_for("index"))

    return _render_keywords_html(df)


_KEYWORD_PRESETS = [
    {
        "name": "Gender + Category",
        "fields": ["Age Gender Segment", "Final Category"],
        "split_ampersand": True,
    },
    {
        "name": "Gender + Colour + Category",
        "fields": ["Age Gender Segment", "Normalised Colour", "Final Category"],
        "split_ampersand": True,
    },
    {
        "name": "Material + Category",
        "fields": ["_material_", "Final Category"],
        "split_ampersand": True,
    },
    {
        "name": "Colour + Material + Category",
        "fields": ["Normalised Colour", "_material_", "Final Category"],
        "split_ampersand": True,
    },
]


_KW_INVALIDS = {"", "nan", "none", "null", "<na>"}


def _build_preset_combos(available_cols: list, df: pd.DataFrame = None) -> list:
    """Return preset combos whose fields exist in available_cols AND have real data.

    The placeholder '_material_' is resolved case-insensitively so feeds that
    name the column 'material', 'Material', 'MATERIAL' etc. are all matched.
    When df is provided, each preset is also checked to ensure at least one row
    has non-empty values in all required fields — preventing ghost presets that
    look populated but produce zero keywords.
    """
    col_set = set(available_cols)
    col_lower = {c.lower(): c for c in available_cols}
    material_col = col_lower.get("material")

    result = []
    for preset in _KEYWORD_PRESETS:
        fields = [
            material_col if f == "_material_" else f
            for f in preset["fields"]
        ]
        if not all(f and f in col_set for f in fields):
            continue
        if df is not None:
            sub = df[fields].astype(str).apply(lambda s: s.str.strip().str.lower())
            if not (~sub.isin(_KW_INVALIDS)).all(axis=1).any():
                continue
        result.append({**preset, "fields": fields})
    return result


def _parse_combos_from_form() -> list:
    """Parse all combo data from the current request form."""
    n = int(request.form.get("combo_count", 0))
    combos = []
    for i in range(n):
        name = request.form.get(f"combo_name_{i}", "").strip()
        split_ampersand = bool(request.form.get(f"combo_split_{i}"))
        fields = [
            x for x in [
                request.form.get(f"cmb_{i}_a", ""),
                request.form.get(f"cmb_{i}_b", ""),
                request.form.get(f"cmb_{i}_c", ""),
            ] if x
        ]
        combos.append({"fields": fields, "name": name, "split_ampersand": split_ampersand})
    return combos


@app.route("/step/keywords/build-presets", methods=["POST"])
def step_keywords_build_presets():
    """Replace current combos with auto-generated presets based on available columns."""
    if request.headers.get("HX-Request") != "true":
        return redirect(url_for("index"))
    df = get_processed_df()
    if df is None:
        return redirect(url_for("index"))
    preset_combos = _build_preset_combos(list(df.columns), df)
    session["combos"] = preset_combos or [{"fields": [], "name": "", "split_ampersand": False}]
    session.modified = True
    return step_keywords()


@app.route("/step/keywords/add", methods=["POST"])
def step_keywords_add():
    combos = _parse_combos_from_form()
    combos.append({"fields": [], "name": "", "split_ampersand": False})
    session["combos"] = combos
    session.modified = True
    if request.headers.get("HX-Request") == "true":
        return step_keywords()
    return redirect(url_for("index"))


@app.route("/step/keywords/remove/<int:idx>", methods=["POST"])
def step_keywords_remove(idx: int):
    combos = _parse_combos_from_form()
    if 0 <= idx < len(combos):
        combos.pop(idx)
    session["combos"] = combos
    session.modified = True
    if request.headers.get("HX-Request") == "true":
        return step_keywords()
    return redirect(url_for("index"))


@app.route("/step/keywords/<int:idx>/preview", methods=["POST"])
def step_keywords_combo_preview(idx: int):
    """Return the preview fragment for a single combo card — no session write."""
    if request.headers.get("HX-Request") != "true":
        return redirect(url_for("index"))
    df = get_processed_df()
    if df is None:
        return f'<div id="combo-preview-{idx}"></div>'

    all_cols = list(df.columns)
    fields = [
        x for x in [
            request.form.get(f"cmb_{idx}_a", ""),
            request.form.get(f"cmb_{idx}_b", ""),
            request.form.get(f"cmb_{idx}_c", ""),
        ] if x
    ]
    split_ampersand = bool(request.form.get(f"combo_split_{idx}"))
    valid_fields = [f for f in fields if f in all_cols]
    kc = make_keywords(df, valid_fields, split_ampersand)

    preview = str(kc.iloc[0]["keyword"]) if not kc.empty and "keyword" in kc.columns else ""
    skip = {"Clicks Monthly Est"}
    if (
        "Unique Product Groups" in kc.columns
        and "Product Count" in kc.columns
        and not kc.empty
        and (kc["Unique Product Groups"] == kc["Product Count"]).all()
    ):
        skip.add("Unique Product Groups")
    display_cols = [c for c in kc.columns if c not in skip]

    return render_template(
        "partials/keyword_combo_preview.html",
        idx=idx,
        kc=kc,
        preview=preview,
        display_cols=display_cols,
        preview_rows=PREVIEW_ROWS,
    )


def _build_combined_html() -> tuple[str, int]:
    """Build combined keyword table, save parquet. Returns (html, keyword_count).
    Used by the route and by other routes that need to cascade the update."""
    df = get_processed_df()
    if df is None:
        return render_template(
            "partials/keyword_combined.html", combined_df=None, total_keywords=0
        ), 0

    combos = session.get("combos", [])
    legacy_field_alias = {
        "age_gender_segment": "Age Gender Segment",
        "normalised_colour": "Normalised Colour",
        "main_category": "Main Category",
        "penultimate_category": "Penultimate Category",
        "final_category": "Final Category",
    }
    combos = [
        {
            "fields": [legacy_field_alias.get(f, f) for f in c.get("fields", [])],
            "name": c.get("name", ""),
            "split_ampersand": bool(c.get("split_ampersand", False)),
        }
        for c in combos
    ]
    all_cols = list(df.columns)

    per_list_tables = []
    for i, combo in enumerate(combos):
        split_ampersand = bool(combo.get("split_ampersand", False))
        fields = [f for f in combo.get("fields", []) if f in all_cols]
        kc = make_keywords(df, fields, split_ampersand)
        list_name = combo.get("name") or f"List {i+1}"
        kc.insert(0, "list_name", list_name)
        per_list_tables.append(kc)

    combined_df = pd.concat(per_list_tables, ignore_index=True) if per_list_tables else pd.DataFrame()
    save_parquet(sid(), "combined_df", combined_df)

    html = render_template(
        "partials/keyword_combined.html",
        combined_df=combined_df.head(PREVIEW_ROWS),
        total_keywords=len(combined_df),
    )
    return html, len(combined_df)


def _render_gads_config_html(keyword_count: int) -> str:
    """Render the Google Ads config section with the given keyword count."""
    countries_df, languages_df = load_gads_constants()
    geo_ids = session.get("geo_ids", ["2826"])
    language_id = session.get("language_id", "1000")
    gads_df = load_parquet(sid(), "gads_df")
    return render_template(
        "partials/gads_config.html",
        countries_df=countries_df,
        languages_df=languages_df,
        geo_ids=geo_ids,
        language_id=language_id,
        keyword_count=keyword_count,
        has_results=gads_df is not None and not gads_df.empty,
    )


@app.route("/step/keywords/combined")
def step_keywords_combined():
    html, kw_count = _build_combined_html()
    if request.headers.get("HX-Request") == "true":
        html += _as_oob(_render_gads_config_html(kw_count), "section-gads-config")
    return html


@app.route("/step/keywords/finalise", methods=["POST"])
def step_keywords_finalise():
    """Save combo config, build combined table, return atomic OOB update for
    sections 8 (keyword builder), 9 (combined table) and 10 (gads config)."""
    if request.headers.get("HX-Request") != "true":
        return redirect(url_for("index"))

    df = get_processed_df()
    if df is None:
        return redirect(url_for("index"))

    # Parse and persist combos from form
    new_combos = _parse_combos_from_form()
    session["combos"] = new_combos
    session.modified = True

    # Section 8: re-render keyword builder with saved combos
    keywords_html = step_keywords()

    # Section 9 + save combined_df.parquet
    combined_html, kw_count = _build_combined_html()

    # Section 10: gads config with up-to-date keyword count
    gads_html = _render_gads_config_html(kw_count)

    return (
        keywords_html
        + _as_oob(combined_html, "section-keywords-combined")
        + _as_oob(gads_html, "section-gads-config")
    )


# ─── Google Ads ───────────────────────────────────────────────────────────────

@app.route("/gads/fetch/start", methods=["POST"])
def gads_fetch_start():
    combined_df = load_parquet(sid(), "combined_df")
    if combined_df is None or combined_df.empty:
        flash("Build a combined keyword list first.", "warning")
        return redirect(url_for("step_keywords_combined"))

    geo_ids = request.form.getlist("geo_ids") or session.get("geo_ids", ["2826"])
    language_id = request.form.get("language_id", session.get("language_id", "1000"))
    session["geo_ids"] = geo_ids
    session["language_id"] = language_id
    session.modified = True

    to_query = (
        combined_df["keyword"].astype(str).str.strip().str.lower()
        .replace("", pd.NA).dropna().unique().tolist()
    )

    if not to_query:
        flash("No keywords to query.", "warning")
        return redirect(url_for("step_keywords_combined"))

    # Write initial progress
    save_json(sid(), "gads_progress", {"status": "running", "batch": 0, "total_batches": 1, "pct": 0.0})

    session_id = sid()
    gads_config = app.config["GOOGLE_ADS"]

    def run_fetch():
        try:
            client, effective_id = get_gads_client_and_customer_id(gads_config)

            def on_progress(pct, batch, total_batches):
                save_json(session_id, "gads_progress", {
                    "status": "running",
                    "batch": batch,
                    "total_batches": total_batches,
                    "pct": pct,
                })

            from core.gads_client import fetch_historical_metrics_gads
            gads_df, raw_json = fetch_historical_metrics_gads(
                client, effective_id, to_query, geo_ids, language_id,
                progress_callback=on_progress,
            )
            save_parquet(session_id, "gads_df", gads_df)
            save_json(session_id, "gads_raw_json", raw_json)
            save_json(session_id, "gads_progress", {"status": "done", "pct": 1.0, "batch": 1, "total_batches": 1})
        except Exception as e:
            save_json(session_id, "gads_progress", {"status": "error", "message": str(e), "pct": 0.0})

    t = threading.Thread(target=run_fetch, daemon=True)
    t.start()

    countries_df, languages_df = load_gads_constants()
    return render_template(
        "partials/gads_fetch.html",
        keyword_count=len(to_query),
        geo_ids=geo_ids,
        language_id=language_id,
        countries_df=countries_df,
        languages_df=languages_df,
    )


@app.route("/gads/fetch/progress")
def gads_fetch_progress():
    session_id = sid()

    def generate():
        while True:
            data = load_json(session_id, "gads_progress") or {"status": "running", "pct": 0.0}
            yield f"data: {json.dumps(data)}\n\n"
            if data.get("status") in ("done", "error"):
                break
            time.sleep(0.8)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/gads/results")
def gads_results():
    combined_df = load_parquet(sid(), "combined_df")
    gads_df = load_parquet(sid(), "gads_df")
    if combined_df is None or gads_df is None:
        return render_template("partials/gads_results.html", has_results=False)

    final_view = compute_opportunity(combined_df, gads_df)
    if final_view is None or final_view.empty:
        return render_template("partials/gads_results.html", has_results=False)

    preview_rows = PREVIEW_ROWS
    keywords_with_volume = int(
        final_view["monthly_searches"].notna().sum()
    ) if "monthly_searches" in final_view.columns else 0
    return render_template(
        "partials/gads_results.html",
        has_results=True,
        final_view=final_view.head(preview_rows),
        total_rows=len(final_view),
        keywords_with_volume=keywords_with_volume,
    )


@app.route("/gads/charts")
def gads_charts():
    combined_df = load_parquet(sid(), "combined_df")
    gads_df = load_parquet(sid(), "gads_df")
    if combined_df is None or gads_df is None:
        return render_template("partials/charts.html", has_data=False)

    fv, totals = build_chart_data(combined_df, gads_df)
    if fv is None:
        return render_template("partials/charts.html", has_data=False)

    all_lists = totals["list_name"].tolist()
    chron_spec = chronological_spec(fv, all_lists, all_lists)
    season_spec = seasonality_spec(fv, all_lists, all_lists)

    return render_template(
        "partials/charts.html",
        has_data=True,
        chronological_spec=chron_spec,
        seasonality_spec=season_spec,
    )


@app.route("/gads/config", methods=["GET", "POST"])
def gads_config():
    """Render the Google Ads config section (country/language selectors)."""
    if request.method == "POST":
        geo_ids = request.form.getlist("geo_ids") or ["2826"]
        language_id = request.form.get("language_id", "1000")
        session["geo_ids"] = geo_ids
        session["language_id"] = language_id
        session.modified = True

    combined_df = load_parquet(sid(), "combined_df")
    keyword_count = len(combined_df) if combined_df is not None else 0
    return _render_gads_config_html(keyword_count)


# ─── Download routes ─────────────────────────────────────────────────────────

@app.route("/download/source-csv")
def download_source_csv():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        flash("No file uploaded.", "warning")
        return redirect(url_for("index"))
    name = session.get("file_name", "feed")
    stem = Path(name).stem
    return send_file(
        io.BytesIO(to_csv_bytes(raw)),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{stem}.csv",
    )


@app.route("/download/source-xlsx")
def download_source_xlsx():
    raw = load_parquet(sid(), "raw_df")
    if raw is None:
        flash("No file uploaded.", "warning")
        return redirect(url_for("index"))
    name = session.get("file_name", "feed")
    stem = Path(name).stem
    return send_file(
        io.BytesIO(to_xlsx_bytes(raw, sheet_name="Data")),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"{stem}.xlsx",
    )


@app.route("/download/normalised-csv")
def download_normalised_csv():
    df = get_processed_df()
    if df is None:
        return redirect(url_for("index"))
    return send_file(io.BytesIO(to_csv_bytes(df)), mimetype="text/csv",
                     as_attachment=True, download_name="normalised_feed.csv")


@app.route("/download/normalised-xlsx")
def download_normalised_xlsx():
    df = get_processed_df()
    if df is None:
        return redirect(url_for("index"))
    return send_file(
        io.BytesIO(to_xlsx_bytes(df, sheet_name="Normalised Feed")),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="normalised_feed.xlsx",
    )


@app.route("/download/keywords-combined-csv")
def download_keywords_combined_csv():
    df = load_parquet(sid(), "combined_df")
    if df is None:
        return redirect(url_for("index"))
    return send_file(io.BytesIO(to_csv_bytes(df)), mimetype="text/csv",
                     as_attachment=True, download_name="keywords_combined.csv")


@app.route("/download/keywords-combined-xlsx")
def download_keywords_combined_xlsx():
    df = load_parquet(sid(), "combined_df")
    if df is None:
        return redirect(url_for("index"))
    return send_file(
        io.BytesIO(to_xlsx_bytes(df, sheet_name="Combined Keywords")),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="keywords_combined.xlsx",
    )


@app.route("/download/gads-metrics-csv")
def download_gads_metrics_csv():
    combined_df = load_parquet(sid(), "combined_df")
    gads_df = load_parquet(sid(), "gads_df")
    if combined_df is None or gads_df is None:
        return redirect(url_for("index"))
    final_view = compute_opportunity(combined_df, gads_df)
    if final_view is None or final_view.empty:
        return redirect(url_for("index"))
    return send_file(io.BytesIO(to_csv_bytes(final_view)), mimetype="text/csv",
                     as_attachment=True, download_name="keywords_with_gads_metrics.csv")


@app.route("/download/gads-metrics-xlsx")
def download_gads_metrics_xlsx():
    combined_df = load_parquet(sid(), "combined_df")
    gads_df = load_parquet(sid(), "gads_df")
    if combined_df is None or gads_df is None:
        return redirect(url_for("index"))
    final_view = compute_opportunity(combined_df, gads_df)
    if final_view is None or final_view.empty:
        return redirect(url_for("index"))
    return send_file(
        io.BytesIO(to_xlsx_bytes(final_view, sheet_name="Keywords + Metrics")),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="keywords_with_gads_metrics.xlsx",
    )


@app.route("/download/sales-opportunity-csv")
def download_sales_opportunity_csv():
    combined_df = load_parquet(sid(), "combined_df")
    gads_df = load_parquet(sid(), "gads_df")
    if combined_df is None or gads_df is None:
        return redirect(url_for("index"))
    final_view = compute_opportunity(combined_df, gads_df)
    if final_view is None or final_view.empty:
        return redirect(url_for("index"))
    sheet = compute_sales_opportunity(final_view)
    return send_file(
        io.BytesIO(to_csv_bytes(sheet)),
        mimetype="text/csv",
        as_attachment=True,
        download_name="sales_opportunity_sheet.csv",
    )


@app.route("/download/gads-raw-json")
def download_gads_raw_json():
    raw_json = load_json(sid(), "gads_raw_json")
    if not raw_json:
        return redirect(url_for("index"))
    data = json.dumps(raw_json, ensure_ascii=False, indent=2).encode("utf-8")
    return send_file(io.BytesIO(data), mimetype="application/json",
                     as_attachment=True, download_name="gads_historical_metrics_raw.json")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
