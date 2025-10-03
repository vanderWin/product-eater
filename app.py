# app.py
import re
import pandas as pd
import streamlit as st

st.set_page_config(page_title="TSV Preview & Column Picker", layout="wide")
st.title("TSV Preview & Column Picker")

# --- Inputs ---
tsv_file = st.file_uploader("Upload a Merchant Center TSV", type=["tsv", "txt"])
preview_rows = st.number_input("Preview rows", 1, 2000, 200, step=50)
if not tsv_file:
    st.info("Upload a TSV to begin.")
    st.stop()

# --- Load TSV ---
try:
    df = pd.read_csv(
        tsv_file, sep="\t", dtype=str, na_filter=False,
        low_memory=False, on_bad_lines="skip"
    )
except Exception as e:
    st.error(f"Failed to read file: {e}")
    st.stop()

# --- Helpers ---
def norm(s: str) -> str:
    """lowercase and remove all non-alphanumerics"""
    return re.sub(r"[^a-z0-9]", "", s.lower()) if isinstance(s, str) else ""

# Build lookup: normalized_name -> original column name
norm_to_orig = {norm(c): c for c in df.columns}

# Google-recommended fields (normalized)
recommended_raw = [
    "title","availability","price","brand","gtin","mpn",
    "condition","language","age group","product type","gender","color", "google product category"
]
recommended_norm = {norm(x) for x in recommended_raw}

# --- Preview ---
st.subheader("Preview")
st.dataframe(df.head(preview_rows), use_container_width=True, height=400)

# --- Schema ---
schema = pd.DataFrame({
    "column": df.columns,
    "non_empty": [df[c].ne("").sum() for c in df.columns],
    "unique": [df[c].nunique(dropna=False) for c in df.columns],
})

# with st.expander("Schema"):
#     st.dataframe(schema, use_container_width=True)

# --- Selection state (persist across edits) ---
if "keep_map" not in st.session_state:
    # default: select recommended present, else select "title" if present
    initial = set()
    for rn in recommended_norm:
        if rn in norm_to_orig:
            initial.add(norm_to_orig[rn])
    if not initial and "title" in df.columns:
        initial.add("title")
    st.session_state.keep_map = {c: (c in initial) for c in df.columns}

# --- Quick-select controls ---
st.subheader("Select columns to keep")

def set_select_recommended():
    sel = {c: False for c in df.columns}
    for rn in recommended_norm:
        if rn in norm_to_orig:
            sel[norm_to_orig[rn]] = True
    st.session_state.keep_map = sel

def set_select_all():
    st.session_state.keep_map = {c: True for c in df.columns}

def set_select_none():
    st.session_state.keep_map = {c: False for c in df.columns}

def set_invert():
    st.session_state.keep_map = {c: not v for c, v in st.session_state.keep_map.items()}

c1, c2, c3, c4 = st.columns(4)
with c1:
    if st.button("Select recommended"):
        set_select_recommended()
with c2:
    if st.button("Select all"):
        set_select_all()
with c3:
    if st.button("Select none"):
        set_select_none()
with c4:
    if st.button("Invert selection"):
        set_invert()

# Show which recommended fields are present/missing
present = [norm_to_orig[rn] for rn in recommended_norm if rn in norm_to_orig]
missing = [r for r in recommended_raw if norm(r) not in norm_to_orig]
st.caption(f"Recommended present: {', '.join(present) if present else 'none'}")
if missing:
    st.caption(f"Recommended missing: {', '.join(missing)}")

# --- Picker table with metrics ---
picker_df = schema.assign(keep=schema["column"].map(st.session_state.keep_map))[
    ["keep", "column", "non_empty", "unique"]
]

edited = st.data_editor(
    picker_df,
    hide_index=True,
    use_container_width=True,
    column_config={
        "keep": st.column_config.CheckboxColumn("keep"),
        "column": st.column_config.TextColumn("column", width="large", disabled=True),
        "non_empty": st.column_config.NumberColumn("non_empty", help="Non-empty rows", disabled=True),
        "unique": st.column_config.NumberColumn("unique", help="Distinct values (incl. empty)", disabled=True),
    },
    num_rows="fixed",
)

# sync state from editor
st.session_state.keep_map = {row["column"]: bool(row["keep"]) for _, row in edited.iterrows()}

keep_cols = [c for c, k in st.session_state.keep_map.items() if k]
if not keep_cols:
    st.warning("No columns selected.")
    st.stop()

# define kept here
kept = df[keep_cols].copy()

# ---- Optional filters ----
st.subheader("Apply filters (optional)")

filters = {}
for col in keep_cols:
    # Only show filter UI if column has few unique values
    nunique = df[col].nunique(dropna=False)
    if nunique <= 50:  # adjust threshold if needed
        options = sorted(df[col].dropna().unique().tolist())
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
st.dataframe(filtered.head(preview_rows), use_container_width=True, height=400)

# ---- Colour summary ----
st.subheader("Colour summary")

# auto-detect a colour column
colour_candidates = [c for c in ["generic_colour", "product_colour", "color", "colour"] if c in filtered.columns]
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
    vc["% of Products"] = (vc["Product Count"] / non_empty * 100).round(2)

    st.dataframe(vc, use_container_width=True)
    st.caption(f"Non-empty colours: {non_empty:,} rows.")

    st.download_button(
        "Download colour summary CSV",
        vc.to_csv(index=False).encode("utf-8"),
        "colour_summary.csv",
        "text/csv",
    )

# ---- Colour mapping ----
st.subheader("Colour mapping")

# path to your mapping file (adjust if needed)
mapping_path = "colour_mapping.csv"

try:
    colour_map = pd.read_csv(mapping_path, dtype=str).fillna("")
except Exception as e:
    st.error(f"Could not load colour mapping file: {e}")
    st.stop()

# ensure expected columns exist
if not {"product_colour", "generic_colour"}.issubset(colour_map.columns.str.lower()):
    st.error("Mapping file must contain columns: product_colour, generic_colour")
else:
    # standardise column names
    colour_map = colour_map.rename(columns={c.lower(): c for c in colour_map.columns})
    colour_col = st.selectbox(
        "Source colour column for mapping",
        options=[c for c in ["generic_colour", "product_colour", "color", "colour"] if c in filtered.columns],
        index=0,
    )

    merged = filtered.merge(
        colour_map,
        how="left",
        left_on=colour_col,
        right_on="product_colour"
    )

    mapped_rows = merged["generic_colour"].notna().sum()
    total_rows = len(merged)
    pct_mapped = round(mapped_rows / total_rows * 100, 2)

    st.metric("Products mapped to generic colour", f"{mapped_rows:,} / {total_rows:,}", f"{pct_mapped}%")

    # list unmapped colours with counts
    unmapped = (
        merged.loc[merged["generic_colour"].isna(), colour_col]
        .value_counts()
        .reset_index()
        .rename(columns={"index": "Unmapped Colour", colour_col: "Product Count"})
    )

    if not unmapped.empty:
        st.subheader("Unmapped colours")
        st.dataframe(unmapped, use_container_width=True)
        st.download_button(
            "Download unmapped colours CSV",
            unmapped.to_csv(index=False).encode("utf-8"),
            "unmapped_colours.csv",
            "text/csv",
        )
    else:
        st.success("All colours are mapped.")


st.download_button(
    "Download trimmed & filtered CSV",
    filtered.to_csv(index=False).encode("utf-8"),
    "filtered_feed.csv",
    "text/csv",
)
