# app.py
import re
import pandas as pd
import streamlit as st


st.markdown("""
<style>
[data-testid="stAppViewContainer"] {
    background: linear-gradient(45deg, #2b0573, #6325F4);
}
</style>
""", unsafe_allow_html=True)


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

# --- Precompute: normalised_gender (case-insensitive detection) ---
cols = {c.lower(): c for c in df.columns}
g_col  = cols.get("gender")
ag_col = cols.get("age group") or cols.get("age_group")

if g_col:
    g  = df[g_col].astype(str).str.strip().str.lower()
    ag = df[ag_col].astype(str).str.strip().str.lower() if ag_col else ""

    kids = (ag == "kids")
    norm = pd.Series("", index=df.index, dtype="string")

    # kids → boys / girls / childrens
    norm = norm.mask(kids & (g == "male"),   "boys")
    norm = norm.mask(kids & (g == "female"), "girls")
    norm = norm.mask(kids & (g == "unisex"), "childrens")

    # adults → mens / womens ; unisex adults blank
    norm = norm.mask(~kids & (g == "male"),   "mens")
    norm = norm.mask(~kids & (g == "female"), "womens")

    df["normalised_gender"] = norm.fillna("")


# --- Helpers ---
def norm(s: str) -> str:
    """lowercase and remove all non-alphanumerics"""
    return re.sub(r"[^a-z0-9]", "", s.lower()) if isinstance(s, str) else ""

# Build lookup: normalized_name -> original column name
norm_to_orig = {norm(c): c for c in df.columns}

# Google-recommended fields (normalized)
recommended_raw = [
    "title","availability","price","brand","gtin","mpn",
    "condition","language","age group","product type","gender","color",
    "google product category","normalised_gender"
]

recommended_norm = {norm(x) for x in recommended_raw}

# --- Preview ---
total_rows = len(df) - 1  # subtract header row
st.subheader(f"Preview (of {total_rows:,} products)")
st.dataframe(df.head(preview_rows), width="stretch", height=400)

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
    # count unique non-empty values
    series = df[col].astype(str).str.strip()
    uniques = series[series.ne("")].unique()
    nunique = len(uniques)

    # skip if only one unique value
    if nunique <= 1:
        continue

    # only show filter UI if the column has few unique values
    if nunique <= 50:  # adjust threshold if needed
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

# ---- Colour mapping (case-normalised) ----
st.subheader("Colour mapping")

# 1) choose source colour column
colour_candidates = [c for c in ["generic_colour", "product_colour", "color", "colour"] if c in filtered.columns]
if not colour_candidates:
    st.info("No colour column found in selected columns. Add one of: generic_colour, product_colour, color, colour.")
    st.stop()
colour_col = st.selectbox("Source colour column for mapping", options=colour_candidates, index=0)

# 2) load mapping and normalise
mapping_path = "colour_mapping.csv"
try:
    colour_map = pd.read_csv(mapping_path, dtype=str)
except Exception as e:
    st.error(f"Could not load colour mapping file: {e}")
    st.stop()

need_cols = {"product_colour", "generic_colour"}
if not need_cols.issubset({c.lower() for c in colour_map.columns}):
    st.error("Mapping file must contain columns: product_colour, generic_colour")
    st.stop()

colour_map = (colour_map.rename(columns={c: c.lower() for c in colour_map.columns})[["product_colour","generic_colour"]]
             .assign(product_colour=lambda d: d["product_colour"].astype(str).str.strip().str.lower(),
                     generic_colour=lambda d: d["generic_colour"].astype(str).str.strip().str.lower())
             .drop_duplicates(subset=["product_colour"]))

# 3) normalise source data
data = filtered.copy()
data["_src_colour_norm"] = data[colour_col].astype(str).str.strip().str.lower()

# 4) current state from existing map
merged = data.merge(colour_map, how="left", left_on="_src_colour_norm", right_on="product_colour")
eligible_now = merged["_src_colour_norm"].astype(str).str.strip().ne("")
unmapped_now = (merged.loc[merged["generic_colour"].isna() & eligible_now, "_src_colour_norm"]
                .value_counts().rename_axis("Unmapped Colour").reset_index(name="Product Count"))

# header + metric placeholders (will render above the editor)
st.subheader("Map unmapped colours")
metric_slot = st.empty()

# 5) editor
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
        use_container_width=True,
        column_config={
            "Unmapped Colour": st.column_config.TextColumn(disabled=True, width="large"),
            "Product Count": st.column_config.NumberColumn(disabled=True),
            "Map to generic colour": st.column_config.SelectboxColumn(options=allowed_generic, required=False, width="medium"),
        },
        num_rows="fixed",
        key="map_editor",
    )

    # combine current selections with the existing map
    new_rows = (
        edited.loc[edited["Map to generic colour"].astype(str).str.strip() != "",
                   ["Unmapped Colour", "Map to generic colour"]]
        .rename(columns={"Unmapped Colour": "product_colour",
                         "Map to generic colour": "generic_colour"})
        .assign(product_colour=lambda d: d["product_colour"].astype(str).str.strip().str.lower(),
                generic_colour=lambda d: d["generic_colour"].astype(str).str.strip().str.lower())
        .drop_duplicates(subset=["product_colour"])
    )
    updated_map = pd.concat([new_rows, colour_map], ignore_index=True)\
                    .drop_duplicates(subset=["product_colour"], keep="first")

    # live metric computed from UPDATED map
    applied = data.merge(updated_map, how="left", left_on="_src_colour_norm", right_on="product_colour")
    eligible_after = applied["_src_colour_norm"].astype(str).str.strip().ne("")
    eligible_after_count = int(eligible_after.sum())
    mapped_after_count = int((applied["generic_colour"].notna() & eligible_after).sum())
    pct_mapped = round(mapped_after_count / eligible_after_count * 100, 2) if eligible_after_count else 0.0

    # no delta argument -> no arrow
    metric_slot.metric(
        "Products currently mapped",
        f"🌈 {pct_mapped}% ({mapped_after_count:,} / {eligible_after_count:,})"
    )


    st.download_button(
        "Download updated colour_mapping.csv",
        updated_map.to_csv(index=False).encode("utf-8"),
        "updated_colour_mapping.csv",
        "text/csv",
    )

# optional mapped output with updated map
applied = data.merge(updated_map, how="left", left_on="_src_colour_norm", right_on="product_colour")
mapped_output = applied.drop(columns=["_src_colour_norm", "product_colour"])
# sync any precomputed helpers into mapped_output
for aux_col in ["normalised_gender", "main_category", "final_category"]:
    if aux_col in filtered.columns:
        mapped_output[aux_col] = filtered[aux_col].values


# ==== Category extraction (auto via checkboxes, no button) ====
st.subheader("Category extraction")

# prefer taxonomy-like fields; fall back to any column
candidates = [c for c in ["google product category", "product type"] if c in filtered.columns]
default_col = candidates[0] if candidates else list(filtered.columns)[0]
cat_src_col = st.selectbox(
    "Column to split into categories",
    options=list(filtered.columns),
    index=list(filtered.columns).index(default_col),
    key="cat_src_col"
)

c1, c2 = st.columns(2)
want_main  = c1.checkbox("Create main_category (first segment)", value=True, key="want_main")
want_final = c2.checkbox("Create final_category (last segment)", value=False, key="want_final")

# compute parts once
parts = (
    filtered[cat_src_col].astype(str)
    .apply(lambda v: [p.strip() for p in re.split(r"\s*>\s*", v) if p.strip()])
)

# assign selected outputs
if want_main:
    filtered["main_category"] = parts.apply(lambda xs: (xs[0] if xs else "")).str.lower()
else:
    if "main_category" in filtered.columns:
        del filtered["main_category"]

if want_final:
    filtered["final_category"] = parts.apply(lambda xs: (xs[-1] if xs else "")).str.lower()
else:
    if "final_category" in filtered.columns:
        del filtered["final_category"]

# mirror into mapped_output if present
if 'mapped_output' in locals():
    mapped_output = mapped_output.reindex(filtered.index)
    if want_main:
        mapped_output["main_category"] = filtered.get("main_category", "")
    else:
        if "main_category" in mapped_output.columns:
            del mapped_output["main_category"]
    if want_final:
        mapped_output["final_category"] = filtered.get("final_category", "")
    else:
        if "final_category" in mapped_output.columns:
            del mapped_output["final_category"]

# preview only if any created
preview_cols = [cat_src_col] + [c for c in ["main_category","final_category"] if c in filtered.columns]
if len(preview_cols) > 1:
    st.dataframe(filtered[preview_cols].head(preview_rows), width="stretch", height=280)
else:
    st.caption("No category fields selected.")

# ---- Working DF for keyword combos (after possible extraction) ----
source_df = mapped_output if 'mapped_output' in locals() else filtered



# ==== Keyword combinations (named, 1–3 dims, many combos) ====
st.subheader("Keyword combinations")

source_df = mapped_output if 'mapped_output' in locals() else filtered
all_cols = list(source_df.columns)

# session state structure: [{ "fields": [...], "name": "..." }, ...]
if "combos" not in st.session_state or not isinstance(st.session_state.combos, list):
    st.session_state.combos = [{"fields": [], "name": ""}]

# controls
cols_bar = st.columns([1,1,1,1])
with cols_bar[0]:
    if st.button("Add another combination"):
        st.session_state.combos.append({"fields": [], "name": ""})
with cols_bar[1]:
    if st.button("Clear all combinations"):
        st.session_state.combos = [{"fields": [], "name": ""}]

to_remove = []
per_list_tables = []

def make_keywords(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if not cols:
        return pd.DataFrame(columns=["keyword", "Product Count"])
    sub = df[cols].copy()
    lower = sub.apply(lambda c: c.astype(str).str.strip().str.lower())
    INVALIDS = {"", "nan", "none", "null"}
    valid_mask = (~lower.isin(INVALIDS)).all(axis=1)
    s = lower[valid_mask].apply(lambda row: " ".join(row.values), axis=1)
    return s.value_counts().rename_axis("keyword").reset_index(name="Product Count")

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
    kc = make_keywords(source_df, chosen)
    if final_name:
        kc.insert(0, "list_name", final_name)
    else:
        kc.insert(0, "list_name", f"List {i+1}")
    c.dataframe(kc.head(preview_rows), width="stretch", height=220)
    c.download_button(
        f"Download list {i+1} CSV",
        kc.to_csv(index=False).encode("utf-8"),
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
    pd.DataFrame(columns=["list_name","keyword","Product Count"])
)

st.subheader("Combined keyword list")
st.dataframe(combined_df.head(preview_rows), width="stretch", height=300)
st.download_button(
    "Download combined keywords CSV",
    combined_df.to_csv(index=False).encode("utf-8"),
    "keywords_combined.csv",
    "text/csv",
)

# ==== Google Ads search volumes ====
st.subheader("Google Ads search volumes")

import yaml
from typing import Tuple

def _norm_id(x):
    return str(x).replace("-", "").strip() if x else ""

def get_gads_client_and_customer_id() -> Tuple[object, str]:
    """Prefer Streamlit Secrets in cloud. Fall back to local google-ads.yaml for dev."""
    from google.ads.googleads.client import GoogleAdsClient

    # --- Cloud: build from st.secrets["google_ads"] ---
    if "google_ads" in st.secrets:
        s = st.secrets["google_ads"]
        cfg = {
            "developer_token": s["developer_token"],
            "client_id": s["client_id"],
            "client_secret": s["client_secret"],
            "refresh_token": s["refresh_token"],
            # Optional fields supported by google-ads config:
            "login_customer_id": s.get("login_customer_id"),
            "client_customer_id": s.get("client_customer_id"),
            "use_proto_plus": True,
        }
        # Remove Nones so yaml is clean
        cfg = {k: v for k, v in cfg.items() if v is not None}
        yaml_text = yaml.dump(cfg)
        client = GoogleAdsClient.load_from_string(yaml_text, version="v20")
        effective_id = _norm_id(s.get("client_customer_id")) or _norm_id(s.get("login_customer_id"))
        return client, effective_id

    # --- Local dev: read google-ads.yaml next to app.py ---
    yaml_path = "google-ads.yaml"
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        st.error("No Streamlit secrets and could not read local google-ads.yaml.")
        return None, ""

    client = GoogleAdsClient.load_from_storage(yaml_path, version="v20")
    effective_id = _norm_id(cfg.get("client_customer_id")) or _norm_id(cfg.get("login_customer_id"))
    return client, effective_id

# User-configurable ONLY for geo and language
c2, c3 = st.columns([1,1])
with c2:
    geo_ids_str = st.text_input("Geo target IDs (comma)", value="2826", help="2826=UK, 2840=US")
with c3:
    language_id = st.text_input("Language ID", value="1000", help="1000=English")

run_fetch = st.button("Fetch Google Ads volumes")

def _parse_geo_ids(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]

def fetch_historical_metrics_gads(client, customer_id: str, keywords: list[str],
                                  geo_ids: list[str], language_id: str,
                                  batch_size: int = 700) -> tuple[pd.DataFrame, list]:
    try:
        from google.protobuf.json_format import MessageToDict
    except Exception:
        MessageToDict = None

    googleads_service = client.get_service("GoogleAdsService")
    idea_service = client.get_service("KeywordPlanIdeaService")

    out_rows, raw_results = [], []
    total = len(keywords)
    prog = st.progress(0.0, text="Requesting batches…")

    for i in range(0, total, batch_size):
        batch = keywords[i:i+batch_size]
        req = client.get_type("GenerateKeywordHistoricalMetricsRequest")
        req.customer_id = customer_id
        req.keywords.extend(batch)
        req.keyword_plan_network = client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH
        req.language = googleads_service.language_constant_path(language_id)
        for gid in geo_ids:
            req.geo_target_constants.append(googleads_service.geo_target_constant_path(gid))

        resp = idea_service.generate_keyword_historical_metrics(request=req)

        # raw JSON log
        if MessageToDict:
            raw_results.extend([MessageToDict(r._pb) for r in resp.results])
        else:
            for r in resp.results:
                raw_results.append({"text": getattr(r, "text", ""), "closeVariants": list(getattr(r, "close_variants", []))})

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
                    month_name = mv.month.name
                    month_num = ["JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE",
                                 "JULY","AUGUST","SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER"].index(month_name)+1
                    out_rows.append(base | {"year": int(mv.year), "month": month_num,
                                            "monthly_searches": int(mv.monthly_searches) if mv.monthly_searches is not None else None})
            else:
                out_rows.append(base | {"year": None, "month": None, "monthly_searches": None})

        prog.progress(min((i+batch_size)/max(total,1),1.0))

    df = pd.DataFrame(out_rows)
    if not df.empty and "aliases" in df.columns:
        df = df.explode("aliases", ignore_index=True).rename(columns={"aliases": "keyword_norm"})
    else:
        df["keyword_norm"] = ""
    return df, raw_results

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
            geo_ids = _parse_geo_ids(geo_ids_str)

            cache_key = ("gads_v20_synonyms", tuple(sorted(to_query)), tuple(sorted(geo_ids)), language_id)
            if st.session_state.get("gads_results_key") != cache_key:
                with st.spinner("Calling Google Ads API…"):
                    gads_df, raw_json = fetch_historical_metrics_gads(
                        client, effective_id, to_query, geo_ids, language_id
                    )
                st.session_state.gads_results_key = cache_key
                st.session_state.gads_results_df = gads_df
                st.session_state.gads_raw_json = raw_json
            else:
                gads_df = st.session_state.gads_results_df
                raw_json = st.session_state.get("gads_raw_json", [])

            if gads_df.empty:
                st.info("No metrics returned.")
            else:
                left = combined_df.assign(keyword_norm=combined_df["keyword"].str.lower().str.strip())
                final_view = left.merge(gads_df, on="keyword_norm", how="left").drop(columns=["keyword_norm"])
                final_view["exact_match"] = final_view["keyword"].str.lower().str.strip().eq(final_view.get("canonical_keyword",""))
                final_view["matched_to"] = final_view.get("canonical_keyword","")

                ordered = [
                    "list_name","keyword","Product Count",
                    "avg_monthly_searches","competition_level","competition_index",
                    "low_top_of_page_bid_micros","high_top_of_page_bid_micros",
                    "year","month","monthly_searches",
                    "exact_match","matched_to","close_variants"
                ]
                final_view = final_view[[c for c in ordered if c in final_view.columns] +
                                        [c for c in final_view.columns if c not in ordered]]

                st.subheader("Keywords with Google Ads metrics")
                st.dataframe(final_view.head(preview_rows), width="stretch", height=360)
                st.download_button(
                    "Download keywords + Google Ads metrics (CSV)",
                    final_view.to_csv(index=False).encode("utf-8"),
                    "keywords_with_gads_metrics.csv",
                    "text/csv",
                    key="dl_keywords_gads_unified"
                )

                if raw_json:
                    import json
                    st.download_button(
                        "Download raw API JSON",
                        json.dumps(raw_json, ensure_ascii=False, indent=2).encode("utf-8"),
                        "gads_historical_metrics_raw.json",
                        "application/json",
                        key="dl_gads_raw_json"
                    )
