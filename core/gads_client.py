"""Google Ads API client and keyword historical metrics fetcher."""
import re
import math
import time
import random
import itertools
from pathlib import Path
from typing import Optional, Callable

import pandas as pd
import yaml


QPS_SLEEP = 1.2  # seconds between requests


def _norm_id(x) -> str:
    return str(x).replace("-", "").strip() if x else ""


def get_gads_client_and_customer_id(config: dict):
    """
    Load Google Ads client from config dict (env vars / Replit Secrets).
    Returns (client, customer_id) or raises RuntimeError.
    """
    from google.ads.googleads.client import GoogleAdsClient

    cfg = {
        "developer_token": config.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "client_id": config.get("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": config.get("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": config.get("GOOGLE_ADS_REFRESH_TOKEN"),
        "login_customer_id": config.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
        "client_customer_id": config.get("GOOGLE_ADS_CLIENT_CUSTOMER_ID"),
        "use_proto_plus": True,
    }

    # Fall back to local yaml if env vars not set
    if not cfg["developer_token"]:
        yaml_path = Path(__file__).parent.parent / "google-ads.yaml"
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                file_cfg = yaml.safe_load(f) or {}
            cfg.update({
                "developer_token": cfg["developer_token"] or file_cfg.get("developer_token"),
                "client_id": cfg["client_id"] or file_cfg.get("client_id"),
                "client_secret": cfg["client_secret"] or file_cfg.get("client_secret"),
                "refresh_token": cfg["refresh_token"] or file_cfg.get("refresh_token"),
                "login_customer_id": cfg["login_customer_id"] or file_cfg.get("login_customer_id"),
                "client_customer_id": cfg["client_customer_id"] or file_cfg.get("client_customer_id"),
            })
        else:
            raise RuntimeError("No Google Ads credentials found (env vars or google-ads.yaml).")

    yaml_text = yaml.dump({k: v for k, v in cfg.items() if v is not None})
    client = GoogleAdsClient.load_from_string(yaml_text, version="v20")
    effective_id = _norm_id(cfg.get("client_customer_id")) or _norm_id(cfg.get("login_customer_id"))
    return client, effective_id


def load_gads_constants():
    """Load country/language dropdown data from gads_exports CSVs."""
    base = Path(__file__).parent.parent / "gads_exports"
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


def _batched(iterable, n: int):
    it = iter(iterable)
    size = max(1, n)
    while True:
        chunk = list(itertools.islice(it, size))
        if not chunk:
            break
        yield chunk


def _sleep_with_retry_delay(retry_seconds: Optional[float], attempt: int) -> None:
    if retry_seconds is not None:
        time.sleep(retry_seconds + 1.0)
    else:
        base = min(60.0, 4.0 * (2 ** max(attempt, 0)))
        time.sleep(base + random.random())


def fetch_historical_metrics_gads(
    client,
    customer_id: str,
    keywords: list,
    geo_ids: list,
    language_id: str,
    batch_size: int = 700,
    progress_callback: Optional[Callable] = None,
) -> tuple:
    """
    Fetch keyword historical metrics from Google Ads API.

    progress_callback(pct: float, batch: int, total_batches: int) is called
    after each batch completes (pct in [0,1]).

    Returns (df, raw_results).
    """
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

    for idx, chunk in enumerate(_batched(keywords, batch_size)):
        req = client.get_type("GenerateKeywordHistoricalMetricsRequest")
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
                retry_seconds = None
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

        MONTHS = ["JANUARY","FEBRUARY","MARCH","APRIL","MAY","JUNE",
                  "JULY","AUGUST","SEPTEMBER","OCTOBER","NOVEMBER","DECEMBER"]

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
            }
            if getattr(m, "monthly_search_volumes", None):
                for mv in m.monthly_search_volumes:
                    month_num = MONTHS.index(mv.month.name) + 1
                    out_rows.append(base | {
                        "year": int(mv.year),
                        "month": month_num,
                        "monthly_searches": int(mv.monthly_searches) if mv.monthly_searches is not None else None,
                    })
            else:
                out_rows.append(base | {"year": None, "month": None, "monthly_searches": None})

        pct = min((idx + 1) / max(batches, 1), 1.0)
        if progress_callback:
            progress_callback(pct, idx + 1, batches)

    df = pd.DataFrame(out_rows)
    if not df.empty and "aliases" in df.columns:
        df = df.explode("aliases", ignore_index=True).rename(columns={"aliases": "keyword_norm"})
    else:
        df["keyword_norm"] = ""
    return df, raw_results
