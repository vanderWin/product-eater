import argparse
import csv
import sys
from pathlib import Path

import yaml


def _norm_id(value: str) -> str:
    return str(value).replace("-", "").strip() if value else ""


def _load_customer_id(cfg_path: Path) -> str:
    if not cfg_path.exists():
        return ""
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return _norm_id(cfg.get("client_customer_id")) or _norm_id(cfg.get("login_customer_id"))


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fetch_rows(ga_service, customer_id: str, query: str) -> list:
    rows = []
    stream = ga_service.search_stream(customer_id=customer_id, query=query)
    for batch in stream:
        rows.extend(batch.results)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Google Ads geo targets (countries) and language constants to CSV."
    )
    parser.add_argument(
        "--config",
        default="google-ads.yaml",
        help="Path to google-ads.yaml",
    )
    parser.add_argument(
        "--customer-id",
        default="",
        help="Override customer ID (digits only). Defaults to client_customer_id/login_customer_id in config.",
    )
    parser.add_argument(
        "--out-dir",
        default="gads_exports",
        help="Directory to write CSV outputs into.",
    )
    args = parser.parse_args()

    cfg_path = Path(args.config)
    customer_id = _norm_id(args.customer_id) or _load_customer_id(cfg_path)
    if not customer_id:
        print("No customer ID found. Provide --customer-id or set client_customer_id/login_customer_id.", file=sys.stderr)
        return 2

    try:
        from google.ads.googleads.client import GoogleAdsClient
    except Exception as exc:
        print(f"Failed to import google-ads client: {exc}", file=sys.stderr)
        return 2

    try:
        client = GoogleAdsClient.load_from_storage(str(cfg_path), version="v20")
    except Exception as exc:
        print(f"Failed to load Google Ads config: {exc}", file=sys.stderr)
        return 2

    ga_service = client.get_service("GoogleAdsService")

    countries_query = """
        SELECT
          geo_target_constant.id,
          geo_target_constant.name,
          geo_target_constant.country_code,
          geo_target_constant.target_type,
          geo_target_constant.status
        FROM geo_target_constant
        WHERE geo_target_constant.target_type = 'Country'
          AND geo_target_constant.status = 'ENABLED'
    """

    languages_query = """
        SELECT
          language_constant.id,
          language_constant.name,
          language_constant.code
        FROM language_constant
    """

    country_rows = _fetch_rows(ga_service, customer_id, countries_query)
    country_out = []
    for row in country_rows:
        geo = row.geo_target_constant
        country_out.append(
            {
                "id": geo.id,
                "name": geo.name,
                "country_code": geo.country_code,
                "target_type": geo.target_type.name if hasattr(geo.target_type, "name") else str(geo.target_type),
                "status": geo.status.name if hasattr(geo.status, "name") else str(geo.status),
            }
        )

    language_rows = _fetch_rows(ga_service, customer_id, languages_query)
    language_out = []
    for row in language_rows:
        lang = row.language_constant
        language_out.append(
            {
                "id": lang.id,
                "name": lang.name,
                "code": lang.code,
            }
        )

    out_dir = Path(args.out_dir)
    _write_csv(
        out_dir / "geo_target_countries.csv",
        ["id", "name", "country_code", "target_type", "status"],
        country_out,
    )
    _write_csv(
        out_dir / "language_constants.csv",
        ["id", "name", "code"],
        language_out,
    )

    print(f"Wrote {len(country_out)} countries to {out_dir / 'geo_target_countries.csv'}")
    print(f"Wrote {len(language_out)} languages to {out_dir / 'language_constants.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
