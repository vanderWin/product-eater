# app.py
import io
import re
import hashlib
import math
import time
import random
import itertools
import pandas as pd
import streamlit as st
import datetime
from pathlib import Path
import yaml
from typing import Tuple, Optional

st.markdown("""
<style>
[data-testid="stAppViewContainer"] {
    background: radial-gradient(135deg, #fdfdfd 0%, #f3f5f8 40%, #e8ecf2 100%);
}

div[data-testid="stDownloadButton"] > button {
  background:#2B0573; color:#fff; border:1px solid #2B0573;
}
div[data-testid="stDownloadButton"] > button:hover {
  background:#730572; color:#fff; border-color:#730572;
}

.danger-scope { --primary-color:#d9534f; }
.danger-scope [data-testid="baseButton-primary"] {
  color:#fff !important;
  border-color:#d9534f !important;
}
.danger-scope [data-testid="baseButton-primary"]:hover {
  filter:brightness(0.92);
}

#preview-card-anchor + div[data-testid="stVerticalBlock"] {
  background: #ffffff;
  border: 1px solid #e6e9f0;
  border-radius: 14px;
  box-shadow: 0 12px 32px rgba(17, 24, 39, 0.08);
  padding: 20px 20px 16px;
  margin: 16px 0 28px;
}
#preview-card-anchor + div[data-testid="stVerticalBlock"] > div {
  padding: 0;
}

#instructions-anchor + div[data-testid="stExpander"] {
  background: #ffffff;
  border: 1px solid #e6e9f0;
  border-radius: 14px;
  box-shadow: 0 10px 28px rgba(17, 24, 39, 0.08);
  margin: 16px 0 20px;
}
#instructions-anchor + div[data-testid="stExpander"] > details > summary {
  font-weight: 600;
  color: #2b0573;
}

#gads-card-anchor + div[data-testid="stVerticalBlock"] {
  border-color: #d9534f;
  box-shadow: 0 8px 20px rgba(217, 83, 79, 0.25);
}

h1, h2, h3, h4, h5, h6,
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6 {
  color: #2b0573 !important;
}
</style>
""", unsafe_allow_html=True)


st.set_page_config(page_title="Product Feed Eater 📎", page_icon="favicon.png", layout="wide")

st.title("Product Feed Eater 📎")
st.markdown("""
<p>This tool cleans and standardises product data from exported feeds.<br>
Upload a TSV or CSV, preview your columns, normalise key attributes, and download a refined dataset.</p>
""", unsafe_allow_html=True)


# --- Cached IO helpers ---
@st.cache_data(show_spinner=False)
def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@st.cache_data(show_spinner=False)
def _excel_sheet_names(data: bytes) -> Tuple[str, ...]:
    xls = pd.ExcelFile(io.BytesIO(data), engine="openpyxl")
    return tuple(xls.sheet_names)


@st.cache_data(show_spinner=True)
def load_excel_bytes(file_bytes: bytes, sheet_name: str) -> pd.DataFrame:
    xls = pd.ExcelFile(io.BytesIO(file_bytes), engine="openpyxl")
    return pd.read_excel(
        xls,
        sheet_name=sheet_name,
        dtype=str,
        na_filter=False,
        engine="openpyxl",
    )


@st.cache_data(show_spinner=True)
def load_csv_like(file_bytes: bytes, sep: str) -> pd.DataFrame:
    return pd.read_csv(
        io.BytesIO(file_bytes),
        sep=sep,
        dtype=str,
        na_filter=False,
        low_memory=False,
        on_bad_lines="skip",
    )


@st.cache_data(show_spinner=False)
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")

# Non-cached Excel export helper
def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> bytes:
    # Write using openpyxl to ensure strings remain plain text (no auto URL encoding)
    # This avoids Excel/XlsxWriter hyperlink limits and related warnings.
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name=sheet_name)
    except Exception:
        # Fallback to xlsxwriter with URL detection disabled if openpyxl is unavailable
        try:
            with pd.ExcelWriter(
                buf,
                engine="xlsxwriter",
                engine_kwargs={"options": {"strings_to_urls": False, "strings_to_formulas": False}},
            ) as w:
                df.to_excel(w, index=False, sheet_name=sheet_name)
        except Exception:
            # Last-resort fallback: try xlsxwriter default
            with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
                df.to_excel(w, index=False, sheet_name=sheet_name)
    buf.seek(0)
    return buf.getvalue()


# === Feed ingest (TSV / CSV / Excel) ===
st.markdown('<div id="instructions-anchor"></div>', unsafe_allow_html=True)
with st.expander("Instructions", expanded=False):
    st.markdown(
        """
**Getting started**
- Log into Merchant Center, navigate to the right client, open the Products tab, and export all products.
- Unpack the archive Google gives you so you can access the TSV. If you are on Windows and there is a warning about errors, skip it.
- Browse for your TSV or drop it into the upload area below, then move through the sections to refine the feed into a more practical list of keywords.

**Section 1: Preview and select columns**
- The app tells you how many products it found, previews the top 200 rows, and lets you select which columns to work with.
- The default selected columns are usually what you want when facet hunting. Select any fields you want to filter on or create keyword lists from.

**Section 2: Filters**
- Screen out anything irrelevant to your analysis. For example, filter to English (language column), or use a feed label like "GB" to separate markets.
- You might also filter on availability and condition to focus on in-stock items that are not second-hand.

**Section 3: Product grouping**
- We need a way to estimate how many products a keyword combination represents. Feeds list every SKU/MPN, but most people think in product groups.
- We prefer `item group id` for grouping when available. If that is missing, we fall back to image URL grouping.
- Image grouping uses the first image URL and can reduce 50K SKUs to 5K product groups.
- Sense check the grouping column before continuing, then review the feed label sales/value table.

**Section 4: Colour summary**
- Sense check which column is used for colour counts, plus the product count and percentage for each colour.
- For example, Black or Navy might represent 7-10% of the feed.

**Section 5/6: Colour mapping**
- Group detailed colours into a more generic set to improve keyword research.
- By default, common colours are mapped automatically, but you may want to map high-volume leftovers manually.
- This is optional, but worthwhile if a colour has a significant product count. Some feeds misuse the colour field with values like "trench" or "classic."
- Use `generic_colour` going forward to avoid researching colours like "midnight leopard."

**Section 7: Category extraction**
- Infer product types from text strings, usually from the `product type` field.
- Choose which segment to extract: first, penultimate, or last segment.
- Example: "Rugby Shirts > Mens > Blue > XL" often works best as "Rugby Shirts" so size/colour/gender come from other fields.

**Section 8: Normalised product feed**
- If you are done, download the cleaned feed as CSV or Excel.
- If you want keyword combinations, continue to the next section.

**Section 9: Keyword combinations**
- Combine fields to create keyword lists, then validate them with product counts, product groups, quantities, and sales value.
- You can create multiple lists and name each one. Example: `main_category` gives top-level terms like "polo shirts."
- You can add another list with `age_gender_segment` + `main_category` to get phrases like "mens polo shirts."
- For deeper granularity, use `age_gender_segment` + `main_category` + `generic_colour` for phrases like "mens blue polo shirts."
- Order matters. "Blue mens polo shirts" implies something else than "mens blue polo shirts."
- Optional toggle: split `&` conjunctions (for example `shirts & tops`) into separate keyword rows.

**Section 10: Combined keyword list**
- Sense check the phrases, product counts, product groups, quantities, and sales value.
- Export the list if needed.

**Section 11: Google Ads Search Volumes**
- The app submits combined keywords to Google Ads and fetches search volumes for your selected country and language.
- Results run in batches. If you have more than ~1K phrases, be patient while it completes.

**Section 12: Keywords with Google Ads metrics**
- Results are returned as one row per keyword per month for seasonality analysis.
- An opportunity model is also included, using your historic clicks and Google Ads search volume to estimate upside.
- Google Ads only returns the last 12 months via API.
- Two charts are shown: total search volume by list over the last 12 months, and normalised seasonality by month.
- You can download chart data or pivot the Keywords and Metrics export to the same effect.

**Note on `age_gender_segment`**
- This is derived from the `gender` and `age_group` fields (for example, "male" + "adult" becomes "mens").
- We avoid possessive apostrophes because the Google Ads API filters them out.
- Everything is forced to lowercase for the same reason.
        """
    )

st.header("Feed Preview & Column Picker")

uploaded_file = st.file_uploader(
    "Upload a feed file (TSV, CSV, or Excel)",
    type=["tsv", "txt", "csv", "xlsx", "xls"]
)

preview_rows = st.number_input("Preview rows", 1, 2000, 200, step=50)

if not uploaded_file:
    st.info("Upload a TSV, CSV, or Excel file to begin.")
    st.stop()

ext = Path(uploaded_file.name).suffix.lower()
file_bytes = uploaded_file.getvalue()
file_hash = _hash_bytes(file_bytes)
file_sig = (uploaded_file.name, file_hash)

try:
    if ext in {".xlsx", ".xls"}:
        try:
            import openpyxl  # noqa
        except ImportError:
            st.error("Excel support requires `openpyxl`. "
                     "Install it with:\n\npip install openpyxl")
            st.stop()

        sheet_options = _excel_sheet_names(file_bytes)
        sheet_name = st.selectbox("Excel sheet", list(sheet_options), index=0)
        df = load_excel_bytes(file_bytes, sheet_name).copy()

    elif ext in {".tsv", ".txt"}:
        df = load_csv_like(file_bytes, "\t").copy()

    elif ext == ".csv":
        df = load_csv_like(file_bytes, ",").copy()

    else:
        st.error(f"Unsupported file type: {ext}")
        st.stop()

except Exception as e:
    st.error(f"Failed to read file: {e}")
    st.stop()

# --- Normalise headers to safe strings ---
df.columns = (pd.Index(df.columns)
              .map(lambda x: str(x).strip())
              .str.replace(r"\s+", " ", regex=True))

# --- Precompute: age_gender_segment (robust) ---
_cols = {c.lower(): c for c in df.columns}
def _pick(*names):
    for n in names:
        c = _cols.get(n.lower())
        if c:
            return c
    return None

g_col  = _pick("gender", "sex", "target gender", "product gender")
ag_col = _pick("age group", "age_group", "age", "target age", "age range")

def safe_series(col):
    if col and col in df.columns:
        return df[col].astype(str).str.strip().str.lower()
    return pd.Series("", index=df.index, dtype="string")

g  = safe_series(g_col)
ag = safe_series(ag_col)

kids = (ag == "kids")
age_gender_norm = pd.Series("", index=df.index, dtype="string")
# kids → boys / girls / childrens
age_gender_norm = age_gender_norm.mask(kids & (g == "male"),   "boys")
age_gender_norm = age_gender_norm.mask(kids & (g == "female"), "girls")
age_gender_norm = age_gender_norm.mask(kids & (g == "unisex"), "childrens")
# adults → mens / womens ; unisex adults blank
age_gender_norm = age_gender_norm.mask(~kids & (g == "male"),   "mens")
age_gender_norm = age_gender_norm.mask(~kids & (g == "female"), "womens")

df["age_gender_segment"] = age_gender_norm.fillna("")

# Google-recommended fields for initial column selection
recommended_raw = [
    "title", "availability", "price", "brand", "gtin", "mpn",
    "condition", "language", "age group", "product type", "gender", "color",
    "image link", "additional image link",
    "feed label", "item group id", "quantity", "qauntity",
    "google product category", "age_gender_segment",
]

# --- Reconcile keep_map with current file/schema ---
schema_sig = (file_sig, tuple(df.columns))
if st.session_state.get("keep_map_sig") != schema_sig:
    # build fresh defaults for this file
    initial = {c: False for c in df.columns}
    # turn on recommended that actually exist
    # norm_to_orig is defined below; build a temporary one here
    _norm = lambda s: re.sub(r"[^a-z0-9]", "", str(s).lower())
    _n2o = {_norm(c): c for c in df.columns}
    for rn in {_norm(x) for x in recommended_raw}:
        if rn in _n2o:
            initial[_n2o[rn]] = True
    if not any(initial.values()):  # fallback
        initial = {c: True for c in df.columns}

    st.session_state.keep_map = initial
    st.session_state.keep_map_sig = schema_sig
else:
    # drop stale keys not in current df, add any new columns default False
    current = {c: bool(st.session_state.keep_map.get(c, False)) for c in df.columns}
    st.session_state.keep_map = current



# === Product count notice ===
id_candidates = {"id", "item id", "item_id", "offer id", "offer_id", "product_id"}
id_col = next((c for c in df.columns if c.lower() in id_candidates), None)

if id_col:
    count_products = (
        df[id_col].astype(str).str.strip().replace({"": pd.NA}).nunique(dropna=True)
    )
else:
    count_products = len(df)

msg = f"This file has {count_products:,} products in it"
st.info(msg)
if st.session_state.get("product_count_toast_sig") != file_sig:
    st.toast(msg)
    st.session_state.product_count_toast_sig = file_sig

# === Quick CSV/Excel export (post-upload, pre-preview) ===
if df is not None and not df.empty:
    try:
        base_name = uploaded_file.name
        file_name = re.sub(r"\.(tsv|txt|csv|xlsx|xls)$", ".csv", base_name, flags=re.I)
        xlsx_name = re.sub(r"\.(tsv|txt|csv|xlsx|xls)$", ".xlsx", base_name, flags=re.I)
    except Exception:
        file_name = "data.csv"
        xlsx_name = "data.xlsx"

    # Prepare-on-click to avoid building Excel in background
    if st.session_state.get("_source_xlsx_sig") != file_sig:
        st.session_state.pop("_source_xlsx_bytes", None)
        st.session_state["_source_xlsx_sig"] = file_sig
    if st.button("Generate Excel Version", key="prep_source_xlsx", help="Generate Excel for download"):
        st.session_state["_source_xlsx_bytes"] = to_xlsx_bytes(df, sheet_name="Data")
    if st.session_state.get("_source_xlsx_bytes"):
        st.download_button(
            "Download Excel Version",
            data=st.session_state["_source_xlsx_bytes"],
            file_name=xlsx_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            help="Export the uploaded data as Excel.",
            key="dl_source_xlsx",
        )
    st.download_button(
        "Download CSV version",
        data=to_csv_bytes(df),
        file_name=file_name,
        mime="text/csv",
        help="Export the uploaded data as CSV.",
        key="dl_source_csv",
    )


# --- Helpers ---
def norm(s: str) -> str:
    """lowercase and remove all non-alphanumerics"""
    return re.sub(r"[^a-z0-9]", "", s.lower()) if isinstance(s, str) else ""

# Build lookup: normalized_name -> original column name
norm_to_orig = {norm(c): c for c in df.columns}


recommended_norm = {norm(x) for x in recommended_raw}
# NEW BLOCK FOR COLUMN PICKER STARTS HERE
# === FIX 0: readable table text ===
st.markdown("""
<style>
/* Make st.dataframe and st.data_editor text readable on dark backgrounds */
div[data-testid="stDataFrame"] div[role="gridcell"],
div[data-testid="stDataFrame"] thead div[role="columnheader"],
div[data-testid="stDataEditor"] div[role="gridcell"],
div[data-testid="stDataEditor"] thead div[role="columnheader"] {
  color: #111 !important;
}
</style>
""", unsafe_allow_html=True)

# === Column selector + preview (robust) ===
# Anchor + container so we can style the container via CSS (next sibling)
st.markdown('<div id="preview-card-anchor"></div>', unsafe_allow_html=True)
with st.container():
    st.subheader("1. Preview and Select Dimensions to Analyse")
    st.caption("Sense check the default columns ticked are enough for your analysis. An example values are shown below.")

    # initialise keep_map once
    if "keep_map" not in st.session_state:
        # start all as False
        initial = {c: False for c in df.columns}
        # turn on recommended ones if provided
        for rn in (recommended_norm or []):
            if rn in norm_to_orig:
                initial[norm_to_orig[rn]] = True
        # if no recommended list, select all
        if not any(initial.values()):
            initial = {c: True for c in df.columns}
        st.session_state.keep_map = initial


    # quick actions
    qa1, qa2, qa3, qa4 = st.columns(4)
    if qa1.button("Select recommended"):
        sel = {c: False for c in df.columns}
        for rn in (recommended_norm or []):
            if rn in norm_to_orig:
                sel[norm_to_orig[rn]] = True
        st.session_state.keep_map = sel
    if qa2.button("Select all"):
        st.session_state.keep_map = {c: True for c in df.columns}
    if qa3.button("Select none"):
        st.session_state.keep_map = {c: False for c in df.columns}
    if qa4.button("Invert selection"):
        st.session_state.keep_map = {c: (not v) for c, v in st.session_state.keep_map.items()}

    # build selector table
    sample_vals = df.head(1).astype(str).T.reset_index()
    sample_vals.columns = ["Column", "Example"]

    # compute non-empty unique counts per column
    unique_counts = [
        int(pd.Series(df[c]).astype(str).str.strip().replace("", pd.NA).dropna().nunique())
        for c in df.columns
    ]

    selector_df = pd.DataFrame({
        "Keep": [bool(st.session_state.keep_map.get(c, False)) for c in df.columns],
        "Column": df.columns,
        "Unique Instances": unique_counts,
    }).merge(sample_vals, on="Column", how="left")
    # Reorder so Unique Instances is last
    selector_df = selector_df[["Keep", "Column", "Example", "Unique Instances"]]

    edited = st.data_editor(
        selector_df,
        hide_index=True,
        height=320,
        column_config={
            "Keep": st.column_config.CheckboxColumn("Keep", help="Tick to include this column", width="small"),
            "Column": st.column_config.TextColumn("Column", disabled=True, width="medium"),
            "Example": st.column_config.TextColumn("Example", disabled=True, width="large"),
            "Unique Instances": st.column_config.NumberColumn("Unique Instances", disabled=True, help="Distinct non-empty values in this field", width="small"),
        },
        disabled=["Column","Unique Instances","Example"],
        column_order=["Keep","Column","Example","Unique Instances"],
        key="col_selector_editor",
    )

    # persist selection
    for _, r in edited.iterrows():
        st.session_state.keep_map[r["Column"]] = bool(r["Keep"])

    keep_cols = [c for c, k in st.session_state.keep_map.items() if k and c in df.columns]
    st.caption(f"{len(keep_cols)} column(s) selected.")

    # preview with horizontal scroll and URL clamping
    URL_KEYS = {"link", "image link", "additional image link", "mobile link"}
    col_cfg = {}
    for c in keep_cols:
        if any(k in c.lower() for k in URL_KEYS):
            col_cfg[c] = st.column_config.TextColumn(c, width="medium")

    kept = df[keep_cols].copy()
    st.dataframe(
        kept.head(preview_rows),
        width="stretch",
        height=520,
        column_config=col_cfg,  # long URLs truncated visually
    )


# NEW BLOCK FOR COLUMN PICKER ENDS HERE


# ---- Optional filters ----
with st.container(border=True):
    st.subheader("2. Apply filters (optional)")
    st.caption("Use this section to apply filters and tidy your data. Should out of stock items be excluded? Perhaps second hand items aren't part of your analsysis.")

    filters = {}
    for col in keep_cols:
        # build choices from the same table we preview/export
        series = kept[col].astype(str).str.strip()
        uniques = series[series.ne("")].unique()
        nunique = len(uniques)

        if nunique <= 1:
            continue

        if nunique <= 50:
            options = sorted(uniques.tolist())
            chosen = st.multiselect(f"Filter {col}", options)
            if chosen:
                filters[col] = chosen

    # Apply filters if any
    if filters:
        filtered = kept.copy()
        for col, vals in filters.items():
            filtered = filtered[filtered[col].isin(vals)]
        st.success(f"Applied {len(filters)} filter(s)")
    else:
        filtered = kept

    # --- Output preview + download ---
    st.write(f"Keeping {len(filtered)} rows and {len(keep_cols)} column(s).")
    st.dataframe(filtered.head(preview_rows), width="stretch", height=400)

# ---- Product groups ----
with st.container(border=True):
    st.subheader("3. Product groups")

    # Choose grouping column (prefer item group id, then image fields)
    _f_norm_to_orig = {norm(c): c for c in filtered.columns}
    group_id_col = _f_norm_to_orig.get(norm("item group id"))

    img_wanted = [
        "image link", "additional image link",
        "image_link", "additional_image_link",
    ]
    img_wanted_norm = [norm(w) for w in img_wanted]

    # Deduplicate while preserving order (item group id first)
    _cands = [group_id_col] if group_id_col else []
    for w in img_wanted_norm:
        if w in _f_norm_to_orig:
            col = _f_norm_to_orig[w]
            if col not in _cands:
                _cands.append(col)
    group_candidates = _cands

    if not group_candidates:
        st.info("No grouping fields detected. Add `item group id` or an image field (`additional image link` / `image link`).")
    else:
        invalid_tokens = {"", "nan", "none", "null", "<na>"}

        # Extract first URL if multiple are present in image fields
        _url_re = re.compile(r"https?://[^\s,]+", flags=re.I)
        def _first_url(v: str) -> str:
            if not isinstance(v, str):
                v = str(v) if v is not None else ""
            m = _url_re.findall(v)
            return m[0].strip() if m else ""

        stats_by_col = {}
        for col in group_candidates:
            raw = filtered[col].astype(str).str.strip()
            if group_id_col and col == group_id_col:
                keys = raw
                has_key = ~raw.str.lower().isin(invalid_tokens)
            else:
                keys = raw.map(_first_url).str.strip()
                has_key = keys.ne("")
            stats_by_col[col] = {
                "keys": keys,
                "has_key": has_key,
                "group_count": int(keys[has_key].nunique()),
                "sku_count": int(has_key.sum()),
            }

        if group_id_col:
            default_col = group_id_col
        else:
            default_col = max(group_candidates, key=lambda c: stats_by_col[c]["group_count"])
        default_idx = group_candidates.index(default_col)
        group_col = st.selectbox(
            "Group by column",
            options=group_candidates,
            index=default_idx,
            help="Uses item group id when available; otherwise groups products by first image URL.",
        )

        keys = stats_by_col[group_col]["keys"]
        has_key = stats_by_col[group_col]["has_key"]
        # Assign stable 1-based group ids for rows with a grouping key
        codes = pd.Series(pd.NA, index=filtered.index, dtype="Int64")
        if has_key.any():
            fact = pd.factorize(keys[has_key])[0] + 1
            codes.loc[has_key] = pd.array(fact, dtype="Int64")
        filtered["image_group_id"] = codes

        # ---- Feed label sales potential summary ----
        feed_label_col = _f_norm_to_orig.get(norm("feed label"))
        price_col = _f_norm_to_orig.get(norm("price"))
        quantity_col = _f_norm_to_orig.get(norm("quantity")) or _f_norm_to_orig.get(norm("qauntity"))

        num_re = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
        ccy_re = re.compile(r"\b([A-Z]{3})\b")

        def _to_number(v: str) -> float:
            s = str(v).strip()
            if not s or s.lower() in invalid_tokens:
                return math.nan
            m = num_re.search(s)
            if not m:
                return math.nan
            try:
                return float(m.group(0).replace(",", ""))
            except Exception:
                return math.nan

        def _to_ccy(v: str) -> str:
            s = str(v).strip().upper()
            if not s or s.lower() in invalid_tokens:
                return ""
            m = ccy_re.search(s)
            return m.group(1) if m else ""

        def _currency_symbol(ccy: str) -> str:
            c = str(ccy).upper()
            return {
                "GBP": "£",
                "EUR": "€",
                "USD": "$",
                "CAD": "C$",
                "AUD": "A$",
                "NZD": "NZ$",
                "JPY": "¥",
                "CNY": "¥",
                "HKD": "HK$",
                "SGD": "S$",
                "CHF": "CHF ",
                "SEK": "kr ",
                "NOK": "kr ",
                "DKK": "kr ",
                "PLN": "zł ",
                "CZK": "Kč ",
                "HUF": "Ft ",
                "RON": "lei ",
                "TRY": "₺",
                "AED": "AED ",
                "SAR": "SAR ",
                "QAR": "QAR ",
                "INR": "₹",
                "ZAR": "R ",
                "BRL": "R$",
                "MXN": "MX$",
                "KRW": "₩",
            }.get(c, "")

        def _fmt_int(v: float) -> str:
            if pd.isna(v):
                return "0"
            return f"{int(round(float(v))):,}"

        def _fmt_qty(v: float) -> str:
            if pd.isna(v):
                return "0"
            n = float(v)
            if abs(n - round(n)) < 1e-9:
                return f"{int(round(n)):,}"
            return f"{n:,.2f}"

        def _fmt_sales(v: float, ccy: str) -> str:
            n = 0.0 if pd.isna(v) else float(v)
            whole = int(round(n))
            sym = _currency_symbol(ccy)
            if sym:
                return f"{sym}{whole:,}"
            if ccy and ccy != "(unknown)":
                return f"{ccy} {whole:,}"
            return f"{whole:,}"

        roll = filtered.copy()
        if feed_label_col:
            roll["_feed_label"] = roll[feed_label_col].astype(str).str.strip().replace("", "(blank)")
        else:
            roll["_feed_label"] = "(all)"

        if quantity_col:
            roll["_quantity"] = roll[quantity_col].map(_to_number).fillna(0.0)
        else:
            roll["_quantity"] = 0.0

        if price_col:
            roll["_price_amount"] = roll[price_col].map(_to_number)
            roll["_currency"] = roll[price_col].map(_to_ccy).replace("", "(unknown)")
            roll["_sales_value"] = (roll["_price_amount"].fillna(0.0) * roll["_quantity"])

            feed_value = (
                roll.groupby(["_feed_label", "_currency"], as_index=False)
                    .agg(**{
                        "SKU Rows": ("_feed_label", "size"),
                        "Product Groups": ("image_group_id", lambda x: x.dropna().nunique()),
                        "Total Quantity": ("_quantity", "sum"),
                        "Total Sales Value": ("_sales_value", "sum"),
                    })
                    .sort_values(["_feed_label", "_currency"], ascending=[True, True])
                    .rename(columns={"_feed_label": "Feed Label", "_currency": "Currency"})
                    .reset_index(drop=True)
            )
        else:
            feed_value = (
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

        feed_value_display = feed_value.copy()
        if "SKU Rows" in feed_value_display.columns:
            feed_value_display["SKU Rows"] = feed_value_display["SKU Rows"].map(_fmt_int)
        if "Product Groups" in feed_value_display.columns:
            feed_value_display["Product Groups"] = feed_value_display["Product Groups"].map(_fmt_int)
        if "Total Quantity" in feed_value_display.columns:
            feed_value_display["Total Quantity"] = feed_value_display["Total Quantity"].map(_fmt_qty)
        if "Total Sales Value" in feed_value_display.columns:
            ccy_vals = feed_value_display.get("Currency", pd.Series([""] * len(feed_value_display), index=feed_value_display.index))
            feed_value_display["Total Sales Value"] = [
                _fmt_sales(v, c) for v, c in zip(feed_value_display["Total Sales Value"], ccy_vals)
            ]

        st.caption("Feed label rollup uses parsed numeric `price` and `quantity` to estimate total sales value.")
        st.dataframe(feed_value_display, width="stretch")

# ---- Colour summary ----
with st.container(border=True):
    st.subheader("4. Colour summary")
    st.caption("This is the percentage of products grouped by colour. You may want to map any strange sounding colours in step 6 below.")

    # Robustly detect colour column(s) ignoring case/spaces
    _f_norm_to_orig = {norm(c): c for c in filtered.columns}
    _wanted = ["generic_colour", "product_colour", "color", "colour"]
    colour_candidates = [_f_norm_to_orig[w] for w in _wanted if w in _f_norm_to_orig]

    if not colour_candidates:
        st.info("No colour column found in selected columns. Add one of: generic_colour, product_colour, color, colour.")
    else:
        colour_col = st.selectbox("Colour column", options=colour_candidates, index=0)

        s = filtered[colour_col].astype(str).str.strip()
        non_empty = s.ne("").sum()

        vc = (
            s[s.ne("")].value_counts(dropna=False)
            .rename_axis("Colour")
            .reset_index(name="Product Count")
        )
        # Show percentage with % symbol (0.00%)
        perc = (vc["Product Count"] / max(non_empty, 1) * 100)
        vc["% of Products"] = perc.map(lambda x: f"{x:.2f}%")

        st.dataframe(vc, width="stretch")
        st.caption(f"Non-empty colours: {non_empty:,} rows.")

        # Excel prepared on click
        if st.button("Prepare colour summary (Excel)", key="prep_colour_summary_xlsx"):
            st.session_state["_colour_summary_xlsx_bytes"] = to_xlsx_bytes(vc, sheet_name="Colour Summary")
        if st.session_state.get("_colour_summary_xlsx_bytes"):
            st.download_button(
                "Download colour summary (Excel)",
                data=st.session_state["_colour_summary_xlsx_bytes"],
                file_name="colour_summary.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_colour_summary_xlsx",
            )
        st.download_button(
            "Download colour summary CSV",
            to_csv_bytes(vc),
            "colour_summary.csv",
            "text/csv",
        )

# ---- Colour mapping (index-safe: use Series.map, not merge) ----
with st.container(border=True):
    st.subheader("5. Colour mapping")
    st.caption("This section is for grouping the odd colour names used by brands to more generic colours. E.g. 'midnight panther' could just be 'black'.")

    # Robustly detect possible colour source columns (ignore case/spaces)
    _f_norm_to_orig = {norm(c): c for c in filtered.columns}
    _wanted = ["generic_colour", "product_colour", "color", "colour"]
    colour_candidates = [_f_norm_to_orig[w] for w in _wanted if w in _f_norm_to_orig]
    has_colour = bool(colour_candidates)

    if not has_colour:
        st.info("No colour column found. Skipping mapping. You can still extract categories and build keyword combinations.")
        mapped_output = filtered.copy()
    else:
        colour_col = st.selectbox("Source colour column for mapping", options=colour_candidates, index=0)

        mapping_path = "colour_mapping.csv"
        try:
            colour_map = pd.read_csv(mapping_path, dtype=str)
        except Exception as e:
            st.error(f"Could not load colour mapping file: {e}")
            mapped_output = filtered.copy()
        else:
            need_cols = {"product_colour", "generic_colour"}
            if not need_cols.issubset({c.lower() for c in colour_map.columns}):
                st.error("Mapping file must contain columns: product_colour, generic_colour")
                mapped_output = filtered.copy()
            else:
                colour_map = (
                    colour_map.rename(columns={c: c.lower() for c in colour_map.columns})[["product_colour","generic_colour"]]
                              .assign(product_colour=lambda d: d["product_colour"].astype(str).str.strip().str.lower(),
                                      generic_colour=lambda d: d["generic_colour"].astype(str).str.strip().str.lower())
                              .drop_duplicates(subset=["product_colour"])
                )

                # Prepare current data and a lookup that preserves order and index
                data = filtered.copy()
                data["_src_colour_norm"] = data[colour_col].astype(str).str.strip().str.lower()

                # Current mapping coverage
                lkp_now = colour_map.set_index("product_colour")["generic_colour"]
                merged_now = data.assign(generic_colour=data["_src_colour_norm"].map(lkp_now))

                eligible_now = merged_now["_src_colour_norm"].ne("")
                unmapped_now = (
                    merged_now.loc[merged_now["generic_colour"].isna() & eligible_now, "_src_colour_norm"]
                    .value_counts().rename_axis("Unmapped Colour").reset_index(name="Product Count")
                )

                st.subheader("6. Map unmapped colours")
                st.caption("These are the oddly named colours that won't be used to create keyword research. If the product count is high enough, map to a generic colour using the drop-downs provided.")
                metric_slot = st.empty()

                if unmapped_now.empty:
                    updated_map = colour_map.copy()
                    metric_slot.metric("Products currently unmapped", "0 / 0", "0%")
                    st.success("All colours are mapped.")
                else:
                    allowed_generic = sorted(colour_map["generic_colour"].dropna().unique().tolist())
                    unmapped_now["Map to generic colour"] = ""

                    edited = st.data_editor(
                        unmapped_now,
                        hide_index=True,
                        width="stretch",
                        column_config={
                            "Unmapped Colour": st.column_config.TextColumn(disabled=True, width="large"),
                            "Product Count": st.column_config.NumberColumn(disabled=True),
                            "Map to generic colour": st.column_config.SelectboxColumn(options=allowed_generic, required=False, width="medium"),
                        },
                        num_rows="fixed",
                        key="map_editor",
                    )

                    new_rows = (
                        edited.loc[edited["Map to generic colour"].astype(str).str.strip() != "",
                                   ["Unmapped Colour", "Map to generic colour"]]
                        .rename(columns={"Unmapped Colour": "product_colour",
                                         "Map to generic colour": "generic_colour"})
                        .assign(product_colour=lambda d: d["product_colour"].astype(str).str.strip().str.lower(),
                                generic_colour=lambda d: d["generic_colour"].astype(str).str.strip().str.lower())
                        .drop_duplicates(subset=["product_colour"])
                    )

                    updated_map = (
                        pd.concat([new_rows, colour_map], ignore_index=True)
                          .drop_duplicates(subset=["product_colour"], keep="first")
                    )

                    # Recompute mapped percentage using map, not merge
                    lkp_after = updated_map.set_index("product_colour")["generic_colour"]
                    applied = data.assign(generic_colour=data["_src_colour_norm"].map(lkp_after))

                    eligible_after = applied["_src_colour_norm"].ne("")
                    eligible_after_count = int(eligible_after.sum())
                    mapped_after_count = int((applied["generic_colour"].notna() & eligible_after).sum())
                    pct_mapped = round(mapped_after_count / eligible_after_count * 100, 2) if eligible_after_count else 0.0

                    metric_slot.metric("Products currently mapped", f"🌈 {pct_mapped}% ({mapped_after_count:,} / {eligible_after_count:,})")

                    # Excel first, then CSV
                    if st.button("Prepare updated colour_mapping (Excel)", key="prep_updated_colour_map_xlsx"):
                        st.session_state["_updated_colour_map_xlsx_bytes"] = to_xlsx_bytes(updated_map, sheet_name="colour_mapping")
                    if st.session_state.get("_updated_colour_map_xlsx_bytes"):
                        st.download_button(
                            "Download updated colour_mapping.xlsx",
                            data=st.session_state["_updated_colour_map_xlsx_bytes"],
                            file_name="updated_colour_mapping.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_updated_colour_map_xlsx",
                        )
                    st.download_button(
                        "Download updated colour_mapping.csv",
                        to_csv_bytes(updated_map),
                        "updated_colour_mapping.csv",
                        "text/csv",
                    )

                # Final mapped output. No positional overwrite of other fields.
                lkp_final = updated_map.set_index("product_colour")["generic_colour"]
                mapped_output = data.assign(generic_colour=data["_src_colour_norm"].map(lkp_final)) \
                                    .drop(columns=["_src_colour_norm"])

# ==== Category extraction (auto via checkboxes, no button) ====
with st.container(border=True):
    st.subheader("7. Category extraction")
    st.caption("If available, select a column to use to create product categories with. This could be the 'google product category' column, or sometimes a 'product type' column. Creates additional columns to group by.")

    candidates = [c for c in ["google product category", "product type"] if c in filtered.columns]
    default_col = candidates[0] if candidates else list(filtered.columns)[0]
    cat_src_col = st.selectbox(
        "Column to split into categories",
        options=list(filtered.columns),
        index=list(filtered.columns).index(default_col),
        key="cat_src_col"
    )

    c1, c2, c3 = st.columns(3)
    want_main  = c1.checkbox("Create main_category (first segment)", value=False, key="want_main")
    want_penultimate = c2.checkbox("Create penultimate_category (second-to-last segment)", value=False, key="want_penultimate")
    want_final = c3.checkbox("Create final_category (last segment)", value=True, key="want_final")

    # compute parts once
    parts = (
        filtered[cat_src_col].astype(str)
        .apply(lambda v: [p.strip() for p in re.split(r"\s*>\s*", v) if p.strip()])
    )

    # assign selected outputs on the filtered frame
    if want_main:
        filtered["main_category"] = parts.apply(lambda xs: (xs[0] if xs else "")).str.lower()
    else:
        if "main_category" in filtered.columns:
            del filtered["main_category"]

    if want_penultimate:
        filtered["penultimate_category"] = parts.apply(
            lambda xs: (xs[-2] if len(xs) >= 2 else (xs[0] if xs else ""))
        ).str.lower()
    else:
        if "penultimate_category" in filtered.columns:
            del filtered["penultimate_category"]

    if want_final:
        filtered["final_category"] = parts.apply(lambda xs: (xs[-1] if xs else "")).str.lower()
    else:
        if "final_category" in filtered.columns:
            del filtered["final_category"]

    # mirror into mapped_output without reindexing or positional writes
    if 'mapped_output' in locals():
        # main_category
        if want_main and "main_category" in filtered.columns:
            mapped_output["main_category"] = filtered["main_category"]
        else:
            mapped_output.drop(columns=["main_category"], inplace=True, errors="ignore")

        # penultimate_category
        if want_penultimate and "penultimate_category" in filtered.columns:
            mapped_output["penultimate_category"] = filtered["penultimate_category"]
        else:
            mapped_output.drop(columns=["penultimate_category"], inplace=True, errors="ignore")

        # final_category
        if want_final and "final_category" in filtered.columns:
            mapped_output["final_category"] = filtered["final_category"]
        else:
            mapped_output.drop(columns=["final_category"], inplace=True, errors="ignore")


    # preview only if any created
    preview_cols = [cat_src_col] + [
        c for c in ["main_category", "penultimate_category", "final_category"]
        if c in filtered.columns
    ]
    if len(preview_cols) > 1:
        st.dataframe(filtered[preview_cols].head(preview_rows), width="stretch", height=280)
    else:
        st.caption("No category fields selected.")

# ---- Normalised feed download (post-category extraction) ----
tidy_df = mapped_output if 'mapped_output' in locals() else filtered

with st.container(border=True):
    st.subheader("8. Download normalised product feed")
    st.caption("All the columns you've created so far in one big list. Useful for seeing product info at a SKU level.")
    st.dataframe(tidy_df.head(preview_rows), width="stretch", height=320)
    if st.button("Prepare normalised feed (Excel)", key="prep_normalised_feed_xlsx"):
        st.session_state["_normalised_feed_xlsx_bytes"] = to_xlsx_bytes(tidy_df, sheet_name="Normalised Feed")
    if st.session_state.get("_normalised_feed_xlsx_bytes"):
        st.download_button(
            "Download normalised feed (Excel)",
            data=st.session_state["_normalised_feed_xlsx_bytes"],
            file_name="normalised_feed.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_normalised_feed_xlsx",
        )
    st.download_button(
        "Download normalised feed (CSV)",
        to_csv_bytes(tidy_df),
        "normalised_feed.csv",
        "text/csv",
        key="dl_normalised_feed",
    )

###################################
# visual separation before keyword tools
st.divider()

# ==== Keyword combinations (named, 1-3 dims, many combos) ====
with st.container(border=True):
    st.subheader("9. Keyword combinations")
    st.caption("Combine different columns together to make keyword lists. You can give your lists custom names if required. Combine upto three different fields, create as many lists as needed using the button below.")

    source_df = mapped_output if 'mapped_output' in locals() else filtered
    all_cols = list(source_df.columns)

    # session state structure: [{ "fields": [...], "name": "..." }, ...]
    if "combos" not in st.session_state or not isinstance(st.session_state.combos, list):
        st.session_state.combos = [{"fields": [], "name": ""}]

    # controls
    cols_bar = st.columns([1,1,1,1])
    with cols_bar[0]:
        if st.button("Add another list to the stack"):
            st.session_state.combos.append({"fields": [], "name": ""})
    with cols_bar[1]:
        if st.button("Clear all combinations"):
            st.session_state.combos = [{"fields": [], "name": ""}]
    with cols_bar[2]:
        explode_ampersand = st.checkbox(
            "Split '&' values",
            value=False,
            help="Explodes values like 'shirts & tops' into separate keyword rows.",
            key="kw_split_ampersand",
        )

    to_remove = []
    per_list_tables = []

    def make_keywords(df: pd.DataFrame, cols: list[str], explode_ampersand: bool = False) -> pd.DataFrame:
        out_cols = [
            "keyword", "Product Count", "Unique Product Groups",
            "Clicks (28d)", "Clicks Monthly Est",
            "Total Quantity", "Sales Currency", "Total Sales Value",
        ]
        if not cols:
            return pd.DataFrame(columns=out_cols)

        sub = df[cols].copy()

        # clean nulls BEFORE string cast; then normalise
        for c in cols:
            sub[c] = sub[c].where(sub[c].notna(), "")  # pd.NA/NaN/None -> ""
            sub[c] = sub[c].astype(str).str.strip().str.lower()

        # drop literal "<na>" that comes from pandas StringDtype
        sub = sub.replace({"<na>": ""})

        INVALIDS = {"", "nan", "none", "null", "<na>"}
        valid_mask = (~sub.isin(INVALIDS)).all(axis=1)

        if not valid_mask.any():
            return pd.DataFrame(columns=out_cols)

        valid_sub = sub.loc[valid_mask]
        if explode_ampersand:
            def _parts_for_value(v: str) -> list[str]:
                txt = str(v).strip()
                if "&" not in txt:
                    return [txt]
                parts = [p.strip() for p in re.split(r"\s*&\s*", txt) if p.strip()]
                return parts if parts else [txt]

            expanded_keywords: list[str] = []
            expanded_src_idx: list[int] = []
            for src_idx, row in valid_sub.iterrows():
                token_lists = [_parts_for_value(v) for v in row.values]
                for combo_vals in itertools.product(*token_lists):
                    kw = re.sub(r"\s+", " ", " ".join(combo_vals)).strip()
                    if kw and kw not in INVALIDS:
                        expanded_keywords.append(kw)
                        expanded_src_idx.append(src_idx)

            if not expanded_keywords:
                return pd.DataFrame(columns=out_cols)

            tmp = pd.DataFrame({"keyword": expanded_keywords})
            row_ix = pd.Index(expanded_src_idx)
        else:
            s = valid_sub.apply(lambda row: " ".join(row.values), axis=1)
            s = s.str.replace(r"\s+", " ", regex=True).str.strip()  # tidy spacing
            tmp = pd.DataFrame({"keyword": s})
            row_ix = tmp.index

        # Optional value fields from the feed for sales potential rollups
        _df_norm_to_orig = {norm(c): c for c in df.columns}
        price_col = _df_norm_to_orig.get(norm("price"))
        quantity_col = _df_norm_to_orig.get(norm("quantity")) or _df_norm_to_orig.get(norm("qauntity"))
        clicks_col = (
            _df_norm_to_orig.get(norm("all clicks"))
            or _df_norm_to_orig.get(norm("all_clicks"))
            or _df_norm_to_orig.get(norm("28 day clicks"))
            or _df_norm_to_orig.get(norm("clicks"))
        )

        num_re = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
        ccy_re = re.compile(r"\b([A-Z]{3})\b")

        def _to_number(v: str) -> float:
            x = str(v).strip()
            if not x or x.lower() in INVALIDS:
                return math.nan
            m = num_re.search(x)
            if not m:
                return math.nan
            try:
                return float(m.group(0).replace(",", ""))
            except Exception:
                return math.nan

        def _to_ccy(v: str) -> str:
            x = str(v).strip().upper()
            if not x or x.lower() in INVALIDS:
                return ""
            m = ccy_re.search(x)
            return m.group(1) if m else ""

        if quantity_col:
            tmp["_quantity"] = df.loc[row_ix, quantity_col].map(_to_number).fillna(0.0).to_numpy()
        else:
            tmp["_quantity"] = 0.0

        if clicks_col:
            tmp["_clicks_28d"] = df.loc[row_ix, clicks_col].map(_to_number).fillna(0.0).to_numpy()
        else:
            tmp["_clicks_28d"] = 0.0

        if price_col:
            tmp["_price_amount"] = df.loc[row_ix, price_col].map(_to_number).to_numpy()
            tmp["_currency"] = df.loc[row_ix, price_col].map(_to_ccy).to_numpy()
            tmp["_sales_value"] = tmp["_quantity"] * tmp["_price_amount"].fillna(0.0)
        else:
            tmp["_currency"] = ""
            tmp["_sales_value"] = math.nan

        if "image_group_id" in df.columns:
            tmp["image_group_id"] = df.loc[row_ix, "image_group_id"].to_numpy()
            out = (tmp.groupby("keyword", as_index=False)
                       .agg(**{
                            "Product Count": ("keyword", "size"),
                            "Unique Product Groups": ("image_group_id", lambda x: x.dropna().nunique()),
                            "Clicks (28d)": ("_clicks_28d", "sum"),
                            "Total Quantity": ("_quantity", "sum"),
                            "_sales_value": ("_sales_value", "sum"),
                       }))
        else:
            out = (tmp.groupby("keyword", as_index=False)
                       .agg(**{
                            "Product Count": ("keyword", "size"),
                            "Clicks (28d)": ("_clicks_28d", "sum"),
                            "Total Quantity": ("_quantity", "sum"),
                            "_sales_value": ("_sales_value", "sum"),
                       }))
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
            # Avoid inaccurate totals when mixed currencies exist inside the same keyword.
            out["Total Sales Value"] = out["_sales_value"].where(out["Sales Currency"].ne("MIXED"), math.nan)
            out["Total Sales Value"] = pd.to_numeric(out["Total Sales Value"], errors="coerce").round().astype("Int64")
            out = out.drop(columns=["_currency_set"])
        else:
            out["Sales Currency"] = "(n/a)"
            out["Total Sales Value"] = pd.Series(pd.NA, index=out.index, dtype="Int64")

        out["Clicks Monthly Est"] = (pd.to_numeric(out["Clicks (28d)"], errors="coerce").fillna(0.0) * (30.0 / 28.0)).round(2)
        out["Total Quantity"] = pd.to_numeric(out["Total Quantity"], errors="coerce").fillna(0.0)

        out = out.sort_values(
            ["Product Count", "Unique Product Groups", "Total Quantity", "keyword"],
            ascending=[False, False, False, True],
        ).reset_index(drop=True)
        return out[[c for c in out_cols if c in out.columns]]


    for i, combo in enumerate(st.session_state.combos):
        c = st.container(border=True)
        c.markdown(f"**Combination {i+1}**")

        # UI: name + up to 3 fields
        name_col, f1, f2, f3, rm = c.columns([1.2, 1, 1, 1, 0.3])

        # current fields
        fields = combo.get("fields", [])
        # inputs
        name_val = name_col.text_input("List name", value=combo.get("name",""), key=f"combo_name_{i}", placeholder="Auto from fields")

        a = f1.selectbox("Field 1", [""] + all_cols,
                         index=(all_cols.index(fields[0])+1) if len(fields)>0 and fields[0] in all_cols else 0,
                         key=f"cmb_{i}_a")
        b = f2.selectbox("Field 2", [""] + all_cols,
                         index=(all_cols.index(fields[1])+1) if len(fields)>1 and fields[1] in all_cols else 0,
                         key=f"cmb_{i}_b")
        d = f3.selectbox("Field 3", [""] + all_cols,
                         index=(all_cols.index(fields[2])+1) if len(fields)>2 and fields[2] in all_cols else 0,
                         key=f"cmb_{i}_c")

        chosen = [x for x in (a, b, d) if x]
        auto_name = " + ".join(chosen) if chosen else ""
        final_name = name_val.strip() or auto_name

        # persist
        st.session_state.combos[i] = {"fields": chosen, "name": final_name}

        with rm:
            if st.button("Remove", key=f"rm_{i}"):
                to_remove.append(i)

        # per-combo table
        kc = make_keywords(source_df, chosen, explode_ampersand=explode_ampersand)
        if final_name:
            kc.insert(0, "list_name", final_name)
        else:
            kc.insert(0, "list_name", f"List {i+1}")
        c.dataframe(kc.head(preview_rows), width="stretch", height=220)
        if "Sales Currency" in kc.columns:
            mixed_n = int(kc["Sales Currency"].eq("MIXED").sum())
            if mixed_n:
                c.caption(f"{mixed_n:,} keyword(s) span multiple currencies; `Total Sales Value` is left blank for those rows.")
        # Excel prepared on click
        if c.button(f"Prepare list {i+1} (Excel)", key=f"prep_list_{i+1}_xlsx"):
            st.session_state[f"_list_{i+1}_xlsx_bytes"] = to_xlsx_bytes(kc, sheet_name=f"List {i+1}")
        if st.session_state.get(f"_list_{i+1}_xlsx_bytes"):
            c.download_button(
                f"Download list {i+1} (Excel)",
                data=st.session_state[f"_list_{i+1}_xlsx_bytes"],
                file_name=f"keywords_list_{i+1}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_list_{i+1}_xlsx"
            )
        c.download_button(
            f"Download list {i+1} CSV",
            to_csv_bytes(kc),
            f"keywords_list_{i+1}.csv",
            "text/csv",
            key=f"dl_list_{i+1}"
        )
        per_list_tables.append(kc)

    # remove requested
    for idx in reversed(to_remove):
        st.session_state.combos.pop(idx)

    # Combined table: keep list_name so analysts can filter
    combined_df = (
        pd.concat(per_list_tables, ignore_index=True) if per_list_tables else
        pd.DataFrame(columns=[
            "list_name", "keyword", "Product Count", "Unique Product Groups",
            "Clicks (28d)", "Clicks Monthly Est",
            "Total Quantity", "Sales Currency", "Total Sales Value",
        ])
    )



# ---- Working DF for keyword combos (after possible extraction) ----
source_df = tidy_df

# Final section, keyword combos
with st.container(border=True):
    st.subheader("10. Combined keyword list")
    st.caption("All of your keyword lists stacked together - this is what will be passed to Google Ads for search volumes")
    st.dataframe(combined_df.head(preview_rows), width="stretch", height=300)
    if st.button("Prepare combined keywords (Excel)", key="prep_combined_keywords_xlsx"):
        st.session_state["_combined_keywords_xlsx_bytes"] = to_xlsx_bytes(combined_df, sheet_name="Combined Keywords")
    if st.session_state.get("_combined_keywords_xlsx_bytes"):
        st.download_button(
            "Download combined keywords (Excel)",
            data=st.session_state["_combined_keywords_xlsx_bytes"],
            file_name="keywords_combined.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_combined_keywords_xlsx",
        )
    st.download_button(
        "Download combined keywords CSV",
        to_csv_bytes(combined_df),
        "keywords_combined.csv",
        "text/csv",
    )

# ==== Google Ads search volumes ====
st.markdown('<div id="gads-card-anchor"></div>', unsafe_allow_html=True)
with st.container(border=True):
    st.subheader("11. Google Ads search volumes")
    st.caption("This section is for displaying seasonal trends and recent search volume history. By default we target United Kingdom and English.")

    # ---- Keyword count indicator ----
    if "combined_df" in locals() and not combined_df.empty:
        st.metric("Keywords to send to Google Ads API", f"{len(combined_df):,}")
    else:
        st.info("No keyword combinations generated yet.")

    # ---- Auth helper (secrets OR local yaml) ----
    def _norm_id(x):
        return str(x).replace("-", "").strip() if x else ""

    def get_gads_client_and_customer_id() -> Tuple[object, str]:
        from google.ads.googleads.client import GoogleAdsClient
        try:
            has_secrets = "google_ads" in st.secrets
        except Exception:
            has_secrets = False

        if has_secrets:
            s = st.secrets["google_ads"]
            cfg = {
                "developer_token": s["developer_token"],
                "client_id": s["client_id"],
                "client_secret": s["client_secret"],
                "refresh_token": s["refresh_token"],
                "login_customer_id": s.get("login_customer_id"),
                "client_customer_id": s.get("client_customer_id"),
                "use_proto_plus": True,
            }
            yaml_text = yaml.dump({k: v for k, v in cfg.items() if v is not None})
            client = GoogleAdsClient.load_from_string(yaml_text, version="v20")
            effective_id = _norm_id(s.get("client_customer_id")) or _norm_id(s.get("login_customer_id"))
            return client, effective_id

        yaml_path = Path(__file__).parent / "google-ads.yaml"
        if not yaml_path.exists():
            st.error("No Streamlit secrets and no local google-ads.yaml found.")
            return None, ""

        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            client = GoogleAdsClient.load_from_storage(str(yaml_path), version="v20")
            effective_id = _norm_id(cfg.get("client_customer_id")) or _norm_id(cfg.get("login_customer_id"))
            return client, effective_id
        except Exception as e:
            st.error(f"Failed to load Google Ads client from {yaml_path.name}: {e}")
            return None, ""

    @st.cache_data(show_spinner=False)
    def load_gads_constants():
        base = Path(__file__).parent / "gads_exports"
        countries_path = base / "geo_target_countries.csv"
        languages_path = base / "language_constants.csv"
        if not countries_path.exists() or not languages_path.exists():
            return None, None
        try:
            countries = pd.read_csv(countries_path, dtype=str).fillna("")
            languages = pd.read_csv(languages_path, dtype=str).fillna("")
        except Exception:
            return None, None
        if "status" in countries.columns:
            countries = countries[countries["status"].str.upper() == "ENABLED"]
        return countries, languages

    # ---- User inputs ----
    countries_df, languages_df = load_gads_constants()
    use_dropdowns = countries_df is not None and languages_df is not None
    if not use_dropdowns:
        st.info("Google Ads country/language lists not found. Run the export script to enable dropdowns.")

    geo_ids = []
    language_id = ""
    c2, c3, c4 = st.columns([1,1,0.6])
    with c2:
        if use_dropdowns:
            countries_df = countries_df.copy()
            countries_df["id"] = countries_df["id"].astype(str)
            countries_df["name"] = countries_df["name"].fillna("")
            if "country_code" in countries_df.columns:
                countries_df["country_code"] = countries_df["country_code"].fillna("")
            countries_df = countries_df.sort_values("name")
            country_ids = countries_df["id"].tolist()
            country_name_map = dict(zip(countries_df["id"], countries_df["name"]))
            default_country_id = ""
            if "country_code" in countries_df.columns:
                match = countries_df[countries_df["country_code"].str.upper() == "GB"]
                if not match.empty:
                    default_country_id = str(match.iloc[0]["id"])
            if not default_country_id:
                match = countries_df[countries_df["name"].str.lower() == "united kingdom"]
                if not match.empty:
                    default_country_id = str(match.iloc[0]["id"])
            default_idx = country_ids.index(default_country_id) if default_country_id in country_ids else 0
            selected_country_id = st.selectbox(
                "Country",
                options=country_ids,
                index=default_idx,
                format_func=lambda x: country_name_map.get(x, x),
            )
            if selected_country_id:
                geo_ids = [selected_country_id]
        else:
            geo_ids_str = st.text_input("Geo target IDs (comma)", value="2826", help="2826=UK, 2840=US")
            geo_ids = [x.strip() for x in geo_ids_str.split(",") if x.strip()]
    with c3:
        if use_dropdowns:
            languages_df = languages_df.copy()
            languages_df["id"] = languages_df["id"].astype(str)
            languages_df["name"] = languages_df["name"].fillna("")
            if "code" in languages_df.columns:
                languages_df["code"] = languages_df["code"].fillna("")
            language_ids = languages_df["id"].tolist()
            language_name_map = dict(zip(languages_df["id"], languages_df["name"]))
            default_language_id = ""
            match = languages_df[languages_df["name"].str.lower() == "english"]
            if not match.empty:
                default_language_id = str(match.iloc[0]["id"])
            if not default_language_id and "code" in languages_df.columns:
                match = languages_df[languages_df["code"].str.lower() == "en"]
                if not match.empty:
                    default_language_id = str(match.iloc[0]["id"])
            default_idx = language_ids.index(default_language_id) if default_language_id in language_ids else 0
            language_id = st.selectbox(
                "Language",
                options=language_ids,
                index=default_idx,
                format_func=lambda x: language_name_map.get(x, x),
            )
        else:
            language_id = st.text_input("Language ID", value="1000", help="1000=English")
    with c4:
        if st.button("Clear results"):
            st.session_state.pop("gads_results_df", None)
            st.session_state.pop("gads_raw_json", None)
            st.session_state.pop("gads_results_key", None)
            st.success("Cleared cached Google Ads results.")

    # ---- Danger-styled fetch button ----
    st.markdown('<div style="--primary-color:#d9534f;">', unsafe_allow_html=True)
    run_fetch = st.button("Fetch Google Ads volumes", type="primary")
    st.markdown("</div>", unsafe_allow_html=True)

    QPS_SLEEP = 1.2  # seconds between requests to respect Google Ads 1 QPS limit

    def _sleep_with_retry_delay(retry_seconds: Optional[float], attempt: int) -> None:
        if retry_seconds is not None:
            time.sleep(retry_seconds + 1.0)
        else:
            base = min(60.0, 4.0 * (2 ** max(attempt, 0)))
            time.sleep(base + random.random())

    def batched(iterable, n: int):
        it = iter(iterable)
        size = max(1, n)
        while True:
            chunk = list(itertools.islice(it, size))
            if not chunk:
                break
            yield chunk

    def fetch_historical_metrics_gads(client, customer_id: str, keywords: list[str],
                                      geo_ids: list[str], language_id: str,
                                      batch_size: int = 700) -> tuple[pd.DataFrame, list]:
        if not keywords:
            return pd.DataFrame(), []

        try:
            from google.protobuf.json_format import MessageToDict
        except Exception:
            MessageToDict = None

        from google.api_core.retry import Retry, if_exception_type
        from google.api_core import exceptions as gcore_exc
        import grpc

        googleads_service = client.get_service("GoogleAdsService")
        idea_service = client.get_service("KeywordPlanIdeaService")
        network_enum = client.enums.KeywordPlanNetworkEnum

        grpc_retry = Retry(
            predicate=if_exception_type(gcore_exc.ResourceExhausted, gcore_exc.ServiceUnavailable),
            initial=4.0, maximum=60.0, multiplier=1.6,
        )

        out_rows, raw_results = [], []
        total = len(keywords)
        batches = math.ceil(total / max(1, batch_size))
        prog = st.progress(0.0, text="Requesting batches…") if total else None

        for idx, chunk in enumerate(batched(keywords, batch_size)):
            req = client.get_type("GenerateKeywordHistoricalMetricsRequest")  # fresh instance
            req.customer_id = customer_id
            req.keywords.extend(chunk)
            req.keyword_plan_network = network_enum.GOOGLE_SEARCH
            req.language = googleads_service.language_constant_path(language_id)
            try:

                req.historical_metrics_options.year_month_range.include_zero_monthly_searches = True
            except AttributeError:
                pass
            for gid in geo_ids:
                req.geo_target_constants.append(googleads_service.geo_target_constant_path(gid))

            attempts = 0
            while True:
                try:
                    time.sleep(QPS_SLEEP)
                    resp = grpc_retry(idea_service.generate_keyword_historical_metrics)(request=req)
                    break
                except gcore_exc.ResourceExhausted as e:
                    attempts += 1
                    if attempts > 8:
                        raise
                    retry_seconds: Optional[float] = None
                    m = re.search(r"Retry in (\d+)\s*second", str(e))
                    if m:
                        retry_seconds = float(m.group(1))
                    _sleep_with_retry_delay(retry_seconds, attempts - 1)
                except (gcore_exc.ServiceUnavailable, gcore_exc.DeadlineExceeded, grpc.RpcError):
                    attempts += 1
                    if attempts > 8:
                        raise
                    _sleep_with_retry_delay(None, attempts - 1)

            if MessageToDict and hasattr(resp, "results"):
                raw_results.extend([MessageToDict(r._pb) for r in resp.results])
            else:
                for r in resp.results:
                    raw_results.append({
                        "text": getattr(r, "text", ""),
                        "closeVariants": list(getattr(r, "close_variants", [])),
                    })

            for r in resp.results:
                m = r.keyword_metrics
                canonical = r.text.lower().strip()
                variants = [v.lower().strip() for v in (list(r.close_variants) if r.close_variants else [])]
                aliases = [canonical] + [v for v in variants if v]
                base = {
                    "canonical_keyword": canonical,
                    "aliases": aliases,
                    "close_variants": ", ".join(variants) if variants else "",
                    "avg_monthly_searches": int(m.avg_monthly_searches) if m.avg_monthly_searches is not None else None,
                    "competition_index": int(m.competition_index) if m.competition_index is not None else None,
                    "competition_level": m.competition.name if hasattr(m.competition, "name") else str(m.competition),
                    "low_top_of_page_bid_micros": int(m.low_top_of_page_bid_micros) if m.low_top_of_page_bid_micros is not None else None,
                    "high_top_of_page_bid_micros": int(m.high_top_of_page_bid_micros) if m.high_top_of_page_bid_micros is not None else None,
                }
                if getattr(m, "monthly_search_volumes", None):
                    for mv in m.monthly_search_volumes:
                        month_num = ["JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE",
                                     "JULY","AUGUST","SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER"].index(mv.month.name)+1
                        out_rows.append(base | {
                            "year": int(mv.year),
                            "month": month_num,
                            "monthly_searches": int(mv.monthly_searches) if mv.monthly_searches is not None else None,
                        })
                else:
                    out_rows.append(base | {"year": None, "month": None, "monthly_searches": None})

            if prog:
                prog.progress(min((idx + 1) / max(batches, 1), 1.0))

        if prog:
            prog.progress(1.0, text="Google Ads batches complete.")

        df = pd.DataFrame(out_rows)
        if not df.empty and "aliases" in df.columns:
            df = df.explode("aliases", ignore_index=True).rename(columns={"aliases": "keyword_norm"})
        else:
            df["keyword_norm"] = ""
        return df, raw_results

    # ---- Persist on fetch ----
    if run_fetch:
        if "combined_df" not in locals() or combined_df.empty:
            st.warning("Build a combined keyword list first.")
        else:
            client, effective_id = get_gads_client_and_customer_id()
            if not client or not effective_id:
                st.error("Google Ads credentials not available. Add them to Streamlit Secrets or a local google-ads.yaml.")
            else:
                to_query = (combined_df["keyword"].astype(str).str.strip().str.lower()
                            .replace("", pd.NA).dropna().unique().tolist())
                if not geo_ids or not language_id:
                    st.warning("Select a country and language before fetching Google Ads volumes.")
                else:
                    cache_key = ("gads_v20_synonyms", tuple(sorted(to_query)), tuple(sorted(geo_ids)), language_id)
                    if st.session_state.get("gads_results_key") != cache_key:
                        with st.spinner("Calling Google Ads API…"):
                            gads_df, raw_json = fetch_historical_metrics_gads(client, effective_id, to_query, geo_ids, language_id)
                        st.session_state.gads_results_key = cache_key
                        st.session_state.gads_results_df = gads_df
                        st.session_state.gads_raw_json = raw_json

    # ---- Always render results if cached ----
    def render_gads_results(combined_df: pd.DataFrame):
        gdf = st.session_state.get("gads_results_df")
        if gdf is None or gdf.empty:
            return
        if combined_df is None or combined_df.empty:
            return

        left = combined_df.assign(keyword_norm=combined_df["keyword"].str.lower().str.strip())
        final_view = left.merge(gdf, on="keyword_norm", how="left").drop(columns=["keyword_norm"])
        final_view["exact_match"] = final_view["keyword"].str.lower().str.strip().eq(final_view.get("canonical_keyword",""))
        final_view["matched_to"] = final_view.get("canonical_keyword","")

        baseline_capture_rate = 0.05
        baseline_source = "fallback"
        baseline_n = 0
        if {"avg_monthly_searches", "Clicks Monthly Est"}.issubset(final_view.columns):
            k = final_view[[
                c for c in [
                    "list_name", "keyword", "Product Count", "Sales Currency",
                    "Total Sales Value", "avg_monthly_searches", "Clicks Monthly Est"
                ] if c in final_view.columns
            ]].drop_duplicates(subset=["list_name", "keyword"]).copy()

            k["avg_monthly_searches"] = pd.to_numeric(k.get("avg_monthly_searches"), errors="coerce")
            k["Clicks Monthly Est"] = pd.to_numeric(k.get("Clicks Monthly Est"), errors="coerce").fillna(0.0)
            k["Product Count"] = pd.to_numeric(k.get("Product Count"), errors="coerce").fillna(0.0)
            k["Total Sales Value"] = pd.to_numeric(k.get("Total Sales Value"), errors="coerce")

            k["Capture Rate"] = k["Clicks Monthly Est"] / k["avg_monthly_searches"].replace(0, pd.NA)
            k["Capture Rate"] = pd.to_numeric(k["Capture Rate"], errors="coerce").replace([math.inf, -math.inf], math.nan)

            stable_mask = (
                k["avg_monthly_searches"].ge(100)
                & k["Clicks Monthly Est"].gt(0)
                & k["Capture Rate"].notna()
            )
            stable_rates = k.loc[stable_mask, "Capture Rate"]
            positive_rates = k.loc[k["Capture Rate"].gt(0) & k["Capture Rate"].notna(), "Capture Rate"]

            if not stable_rates.empty:
                baseline_capture_rate = float(stable_rates.median())
                baseline_source = "stable keywords"
                baseline_n = int(stable_rates.shape[0])
            elif not positive_rates.empty:
                baseline_capture_rate = float(positive_rates.median())
                baseline_source = "all positive keywords"
                baseline_n = int(positive_rates.shape[0])

            if not math.isfinite(baseline_capture_rate):
                baseline_capture_rate = 0.05
                baseline_source = "fallback"
                baseline_n = 0
            baseline_capture_rate = min(max(baseline_capture_rate, 0.0), 1.0)

            k["Baseline Capture Rate"] = baseline_capture_rate
            k["Expected Clicks"] = k["avg_monthly_searches"].fillna(0.0) * baseline_capture_rate
            k["Potential Click Upside"] = (k["Expected Clicks"] - k["Clicks Monthly Est"]).clip(lower=0.0)
            k["Value per Click"] = k["Total Sales Value"] / k["Clicks Monthly Est"].replace(0, pd.NA)

            if "Sales Currency" in k.columns:
                invalid_currency = {"MIXED", "(n/a)", "(unknown)", ""}
                bad_ccy = k["Sales Currency"].astype(str).str.strip().isin(invalid_currency)
                k.loc[bad_ccy, "Value per Click"] = math.nan

            k["Opportunity Value"] = k["Potential Click Upside"] * k["Value per Click"]

            raw_rank = k["Opportunity Value"].where(k["Opportunity Value"].gt(0)).rank(method="average", pct=True)
            search_conf = (
                k["avg_monthly_searches"].fillna(0.0).clip(lower=0.0)
                .map(lambda x: math.log1p(float(x)) / math.log1p(1000.0))
                .clip(lower=0.0, upper=1.0)
            )
            prod_conf = (
                k["Product Count"].fillna(0.0).clip(lower=0.0)
                .map(lambda x: math.log1p(float(x)) / math.log1p(50.0))
                .clip(lower=0.0, upper=1.0)
            )
            conf = (0.7 * search_conf + 0.3 * prod_conf).clip(lower=0.0, upper=1.0)
            k["Opportunity Score"] = ((raw_rank.fillna(0.0) * conf) * 100.0).round().astype("Int64")

            opp_cols = [
                "Capture Rate", "Baseline Capture Rate", "Expected Clicks",
                "Potential Click Upside", "Value per Click", "Opportunity Value", "Opportunity Score",
            ]
            final_view = final_view.merge(
                k[["list_name", "keyword"] + opp_cols],
                on=["list_name", "keyword"],
                how="left",
            )

        ordered = [
            "list_name","keyword","Product Count","Unique Product Groups",
            "Clicks (28d)","Clicks Monthly Est",
            "Total Quantity","Sales Currency","Total Sales Value",
            "Capture Rate","Baseline Capture Rate","Expected Clicks","Potential Click Upside",
            "Value per Click","Opportunity Value","Opportunity Score",
            "avg_monthly_searches","competition_level","competition_index",
            "low_top_of_page_bid_micros","high_top_of_page_bid_micros",
            "year","month","monthly_searches",
            "exact_match","matched_to","close_variants"
        ]
        final_view = final_view[[c for c in ordered if c in final_view.columns] +
                                [c for c in final_view.columns if c not in ordered]]

        st.subheader("12. Keywords with Google Ads metrics")
        st.caption(
            f"Opportunity score uses a baseline capture rate of {baseline_capture_rate:.2%} "
            f"({baseline_source}; n={baseline_n:,}) to estimate click and value upside."
        )
        st.dataframe(final_view.head(preview_rows), width="stretch", height=360)
        # Excel prepared on click
        if st.button("Prepare keywords + metrics (Excel)", key="prep_keywords_gads_unified_xlsx"):
            st.session_state["_keywords_gads_unified_xlsx_bytes"] = to_xlsx_bytes(final_view, sheet_name="Keywords + Metrics")
        if st.session_state.get("_keywords_gads_unified_xlsx_bytes"):
            st.download_button(
                "Download keywords + Google Ads metrics (Excel)",
                data=st.session_state["_keywords_gads_unified_xlsx_bytes"],
                file_name="keywords_with_gads_metrics.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_keywords_gads_unified_xlsx"
            )
        st.download_button(
            "Download keywords + Google Ads metrics (CSV)",
            to_csv_bytes(final_view),
            "keywords_with_gads_metrics.csv",
            "text/csv",
            key="dl_keywords_gads_unified"
        )

        raw_json = st.session_state.get("gads_raw_json", [])
        if raw_json:
            import json
            st.download_button(
                "Download raw API JSON",
                json.dumps(raw_json, ensure_ascii=False, indent=2).encode("utf-8"),
                "gads_historical_metrics_raw.json",
                "application/json",
                key="dl_gads_raw_json"
            )

    render_gads_results(combined_df)

# ==== Search volume charts (after Google Ads results) ====
with st.container(border=True):
    st.subheader("Search volume charts")
    gdf = st.session_state.get("gads_results_df")
    if gdf is None or gdf.empty or combined_df.empty:
        st.info("Fetch Google Ads volumes first to plot charts.")
    else:
        left = combined_df.assign(keyword_norm=combined_df["keyword"].str.lower().str.strip())
        fv = left.merge(gdf, on="keyword_norm", how="left").dropna(subset=["year","month","monthly_searches"])
        fv["date"] = pd.to_datetime(dict(year=fv["year"].astype(int), month=fv["month"].astype(int), day=1))

        totals = (fv.groupby("list_name", as_index=False)["monthly_searches"].sum()
                    .sort_values("monthly_searches", ascending=False))
        default_lists = totals["list_name"].head(6).tolist()
        chosen_lists = st.multiselect("Lists to plot", totals["list_name"].tolist(), default_lists)
        fv = fv[fv["list_name"].isin(chosen_lists)] if chosen_lists else fv
        legend_sort = chosen_lists if chosen_lists else totals["list_name"].tolist()

        import altair as alt
        PALETTE = ["#4C6A92", "#6F8F72", "#B97A57", "#8C6C99", "#C4A46B", "#5B7C99", "#9E6E6E"]

        chron = (fv.groupby(["list_name","date"], as_index=False)["monthly_searches"].sum()
                   .sort_values("date"))
        c1 = (alt.Chart(chron).mark_line(point=True, strokeWidth=2, interpolate="monotone")
              .encode(
                  x=alt.X("yearmonth(date):T", title="Month", axis=alt.Axis(format="%b '%y")),
                  y=alt.Y("monthly_searches:Q", title="Total searches", axis=alt.Axis(format="~s")),
                  color=alt.Color("list_name:N", title="List",
                                  scale=alt.Scale(range=PALETTE), sort=legend_sort),
                  tooltip=["list_name",
                           alt.Tooltip("yearmonth(date):T", title="Month"),
                           alt.Tooltip("monthly_searches:Q", title="Searches", format="~s")],
              )
              .properties(title="Chronological search volume", width="container", height=320)
              .configure_axis(grid=False, labelColor="#2b0573", titleColor="#2b0573")
              .configure_legend(labelColor="#2b0573", titleColor="#2b0573")
              .configure_view(strokeWidth=0, fill="transparent"))
        st.altair_chart(c1, theme=None, use_container_width=True)

        month_order = ["January","February","March","April","May","June","July","August","September","October","November","December"]
        fv["month_name"] = pd.Categorical(fv["date"].dt.month_name(), categories=month_order, ordered=True)
        season = (fv.groupby(["list_name","month_name"], as_index=False)["monthly_searches"].sum()
                    .dropna(subset=["month_name"]).sort_values(["list_name","month_name"]))
        denom = season.groupby("list_name")["monthly_searches"].transform("max").clip(lower=1)
        season["frac_of_peak"] = season["monthly_searches"] / denom

        c2 = (alt.Chart(season).mark_line(point=True, strokeWidth=2, interpolate="monotone")
              .encode(
                  x=alt.X("month_name:O", sort=month_order, title="Month (Jan→Dec)"),
                  y=alt.Y("frac_of_peak:Q", title="Seasonality", scale=alt.Scale(domain=[0,1]),
                          axis=alt.Axis(format=".0%", values=[0,0.2,0.4,0.6,0.8,1])),
                  color=alt.Color("list_name:N", title="List",
                                  scale=alt.Scale(range=PALETTE), sort=legend_sort),
                  tooltip=["list_name","month_name",
                           alt.Tooltip("frac_of_peak:Q", title="% of peak", format=".0%")],
              )
              .properties(title="Seasonality by list (normalised)", width="container", height=320)
              .configure_axis(grid=False, labelColor="#2b0573", titleColor="#2b0573")
              .configure_legend(labelColor="#2b0573", titleColor="#2b0573")
              .configure_view(strokeWidth=0, fill="transparent"))
        st.altair_chart(c2, theme=None, use_container_width=True)



# ===== Centered footer (replaces sidebar logo & copyright) =====
import base64, mimetypes, datetime, os
from pathlib import Path

def _data_uri_for_logo():
    # try common locations / names
    candidates = [
        Path("logo.svg"), Path("logo.png"),
        Path("static/logo.svg"), Path("static/logo.png")
    ]
    for p in candidates:
        if p.exists():
            mime = mimetypes.guess_type(p.name)[0] or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
            return f"data:{mime};base64,{b64}"
    return None  # fallback: no logo found

def render_footer(bg="#2b0573", max_h=70):
    current_year = datetime.datetime.now().year
    logo_uri = _data_uri_for_logo()

    img_html = (
        f'<img src="{logo_uri}" style="max-height:{max_h}px; width:auto; margin-bottom:0px;" />'
        if logo_uri else ""
    )

    st.markdown(
        f"""
        <div style="background:{bg}; padding:5px; text-align:center; margin-top:40px; border-radius:10px; ">
            {img_html}
            <div style="color:#fff; font-size:0.9em;">
                &copy; {current_year}
                <a href="https://www.journeyfurther.com/?utm_source=product-feed-eater&utm_medium=footer&utm_campaign=product-feed-eater"
                   target="_blank" style="color:#fff; text-decoration:none;">Journey Further</a>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

render_footer()  # call at the very end of the script
