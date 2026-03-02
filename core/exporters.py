"""CSV and Excel export helpers."""
import io
import pandas as pd


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def to_xlsx_bytes(df: pd.DataFrame, sheet_name: str = "Data") -> bytes:
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name=sheet_name)
    except Exception:
        try:
            with pd.ExcelWriter(
                buf,
                engine="xlsxwriter",
                engine_kwargs={"options": {"strings_to_urls": False, "strings_to_formulas": False}},
            ) as w:
                df.to_excel(w, index=False, sheet_name=sheet_name)
        except Exception:
            with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
                df.to_excel(w, index=False, sheet_name=sheet_name)
    buf.seek(0)
    return buf.getvalue()
