"""Shared utilities used across core modules."""
import re
import math


def norm(s: str) -> str:
    """Lowercase and remove all non-alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", s.lower()) if isinstance(s, str) else ""


INVALIDS = {"", "nan", "none", "null", "<na>"}

_num_re = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_ccy_re = re.compile(r"\b([A-Z]{3})\b")


def to_number(v: str) -> float:
    s = str(v).strip()
    if not s or s.lower() in INVALIDS:
        return math.nan
    m = _num_re.search(s)
    if not m:
        return math.nan
    try:
        return float(m.group(0).replace(",", ""))
    except Exception:
        return math.nan


def to_ccy(v: str) -> str:
    s = str(v).strip().upper()
    if not s or s.lower() in INVALIDS:
        return ""
    m = _ccy_re.search(s)
    return m.group(1) if m else ""


def currency_symbol(ccy: str) -> str:
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


def fmt_int(v: float) -> str:
    if math.isnan(v) if isinstance(v, float) else False:
        return "0"
    try:
        return f"{int(round(float(v))):,}"
    except Exception:
        return "0"


def fmt_qty(v: float) -> str:
    try:
        n = float(v)
    except Exception:
        return "0"
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}"


def fmt_sales(v: float, ccy: str) -> str:
    try:
        n = 0.0 if (isinstance(v, float) and math.isnan(v)) else float(v)
    except Exception:
        n = 0.0
    whole = int(round(n))
    sym = currency_symbol(ccy)
    if sym:
        return f"{sym}{whole:,}"
    if ccy and ccy != "(unknown)":
        return f"{ccy} {whole:,}"
    return f"{whole:,}"
