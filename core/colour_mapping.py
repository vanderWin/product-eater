"""Colour summary and mapping logic."""
from pathlib import Path
import re
import pandas as pd

MAPPING_PATH = Path(__file__).parent.parent / "colour_mapping.csv"
WANTED_COLOUR_COLS = ["generic_colour", "product_colour", "color", "colour"]


def detect_colour_columns(df: pd.DataFrame) -> list:
    """Return list of colour column names present in df."""
    from .utils import norm
    n2o = {norm(c): c for c in df.columns}
    return [n2o[norm(w)] for w in WANTED_COLOUR_COLS if norm(w) in n2o]


def colour_summary(df: pd.DataFrame, colour_col: str) -> pd.DataFrame:
    """Return value_counts DataFrame with % of Products column."""
    s = df[colour_col].astype(str).str.strip()
    non_empty = s.ne("").sum()
    vc = (
        s[s.ne("")]
        .value_counts(dropna=False)
        .rename_axis("Colour")
        .reset_index(name="Product Count")
    )
    perc = vc["Product Count"] / max(non_empty, 1) * 100
    vc["% of Products"] = perc.map(lambda x: f"{x:.2f}%")
    return vc, int(non_empty)


def load_colour_mapping() -> pd.DataFrame | None:
    """Load colour_mapping.csv. Returns None if missing or invalid."""
    try:
        cm = pd.read_csv(MAPPING_PATH, dtype=str)
    except Exception:
        return None
    need = {"product_colour", "generic_colour"}
    if not need.issubset({c.lower() for c in cm.columns}):
        return None
    cm = (
        cm.rename(columns={c: c.lower() for c in cm.columns})[["product_colour", "generic_colour"]]
        .assign(
            product_colour=lambda d: d["product_colour"].astype(str).str.strip().str.lower(),
            generic_colour=lambda d: d["generic_colour"].astype(str).str.strip().str.lower(),
        )
        .drop_duplicates(subset=["product_colour"])
    )
    return cm


def unmapped_colours(df: pd.DataFrame, colour_col: str, colour_map: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame of unmapped colours with product counts."""
    data = df.copy()
    data["_src_norm"] = data[colour_col].astype(str).str.strip().str.lower()
    lkp = colour_map.set_index("product_colour")["generic_colour"]
    merged = data.assign(generic_colour=data["_src_norm"].map(lkp))
    eligible = merged["_src_norm"].ne("")
    unmapped = (
        merged.loc[merged["generic_colour"].isna() & eligible, "_src_norm"]
        .value_counts()
        .rename_axis("Unmapped Colour")
        .reset_index(name="Product Count")
    )
    return unmapped


def apply_colour_mapping(df: pd.DataFrame, colour_col: str, colour_map: pd.DataFrame) -> pd.DataFrame:
    """Add generic_colour column to df using colour_map lookup."""
    data = df.copy()
    data["_src_norm"] = data[colour_col].astype(str).str.strip().str.lower()
    lkp = colour_map.set_index("product_colour")["generic_colour"]
    data["generic_colour"] = data["_src_norm"].map(lkp)
    data = data.drop(columns=["_src_norm"])
    return data


def merge_new_mappings(base_map: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    """Prepend new_rows to base_map, deduplicate on product_colour."""
    combined = pd.concat([new_rows, base_map], ignore_index=True)
    return combined.drop_duplicates(subset=["product_colour"], keep="first")


def mapping_coverage(df: pd.DataFrame, colour_col: str, colour_map: pd.DataFrame) -> tuple:
    """Return (mapped_count, eligible_count, pct_mapped)."""
    data = df.copy()
    data["_src_norm"] = data[colour_col].astype(str).str.strip().str.lower()
    lkp = colour_map.set_index("product_colour")["generic_colour"]
    applied = data.assign(generic_colour=data["_src_norm"].map(lkp))
    eligible = applied["_src_norm"].ne("")
    eligible_count = int(eligible.sum())
    mapped_count = int((applied["generic_colour"].notna() & eligible).sum())
    pct = round(mapped_count / eligible_count * 100, 2) if eligible_count else 0.0
    return mapped_count, eligible_count, pct


def mapping_breakdown(df: pd.DataFrame, colour_col: str, base_map: pd.DataFrame, updated_map: pd.DataFrame) -> tuple:
    """
    Return mapping breakdown counts and percentages as:
    (
        currently_mapped_count,
        newly_mapped_count,
        unmapped_count,
        eligible_count,
        pct_mapped,
        pct_currently,
        pct_newly,
        pct_unmapped,
    )
    """
    data = df.copy()
    src = data[colour_col].astype(str).str.strip().str.lower()
    eligible = src.ne("")

    base_lookup = base_map.set_index("product_colour")["generic_colour"]
    updated_lookup = updated_map.set_index("product_colour")["generic_colour"]

    base_mapped = src.map(base_lookup).notna() & eligible
    updated_mapped = src.map(updated_lookup).notna() & eligible

    currently_mapped_count = int(base_mapped.sum())
    newly_mapped_count = int((updated_mapped & ~base_mapped).sum())
    eligible_count = int(eligible.sum())
    unmapped_count = max(eligible_count - currently_mapped_count - newly_mapped_count, 0)

    if eligible_count:
        pct_currently = round(currently_mapped_count / eligible_count * 100, 2)
        pct_newly = round(newly_mapped_count / eligible_count * 100, 2)
        pct_unmapped = round(unmapped_count / eligible_count * 100, 2)
    else:
        pct_currently = 0.0
        pct_newly = 0.0
        pct_unmapped = 0.0
    pct_mapped = round(pct_currently + pct_newly, 2)

    return (
        currently_mapped_count,
        newly_mapped_count,
        unmapped_count,
        eligible_count,
        pct_mapped,
        pct_currently,
        pct_newly,
        pct_unmapped,
    )


def add_suggestions(unmapped_df: pd.DataFrame, generic_colours: list[str]) -> pd.DataFrame:
    """
    Add a Suggestion column to unmapped_df by scanning words left-to-right and
    picking the first token that matches a generic colour.
    """
    if unmapped_df is None or unmapped_df.empty:
        out = unmapped_df.copy() if isinstance(unmapped_df, pd.DataFrame) else pd.DataFrame()
        if isinstance(out, pd.DataFrame) and "Suggestion" not in out.columns:
            out["Suggestion"] = ""
        return out

    out = unmapped_df.copy()
    generic_set = {str(g).strip().lower() for g in generic_colours if str(g).strip()}

    def _suggest(value: str) -> str:
        tokens = re.findall(r"[a-z0-9]+", str(value).lower())
        for tok in tokens:
            if tok in generic_set:
                return tok
        return ""

    out["Suggestion"] = out["Unmapped Colour"].map(_suggest)
    return out
