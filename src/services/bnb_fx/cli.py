from __future__ import annotations

import argparse
import logging
from pathlib import Path

from services.bnb_fx.client import build_cache, build_cache_for_symbols_and_years, get_exchange_rate


logger = logging.getLogger(__name__)


def _parse_symbol_list(value: str) -> list[str]:
    symbols = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("expected at least one symbol")
    return symbols


def _parse_years(value: str) -> list[int]:
    years: list[int] = []
    for part in value.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            years.append(int(text))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid year: {text!r}") from exc
    if not years:
        raise argparse.ArgumentTypeError("expected at least one year")
    return years


def _parse_date_list(value: str) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one date")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bnb-fx")

    subparsers = parser.add_subparsers(dest="command", required=True)

    period_parser = subparsers.add_parser("period", help="Build cache for date period")
    period_parser.add_argument("--log-level", default="INFO")
    period_parser.add_argument("--symbols", required=True, type=_parse_symbol_list)
    period_parser.add_argument("--start-date", required=True)
    period_parser.add_argument("--end-date", required=True)
    period_parser.add_argument("--cache-dir", type=Path)

    years_parser = subparsers.add_parser("years", help="Build cache for full years")
    years_parser.add_argument("--log-level", default="INFO")
    years_parser.add_argument("--symbols", required=True, type=_parse_symbol_list)
    years_parser.add_argument("--years", required=True, type=_parse_years)
    years_parser.add_argument("--cache-dir", type=Path)

    rate_parser = subparsers.add_parser("get-rate", help="Get rate(s) for one symbol and one/more dates")
    rate_parser.add_argument("--log-level", default="INFO")
    rate_parser.add_argument("--symbol", required=True)
    date_group = rate_parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--date")
    date_group.add_argument("--dates", type=_parse_date_list)
    rate_parser.add_argument("--cache-dir", type=Path)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "period":
        result = build_cache(
            symbols=args.symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            cache_dir=args.cache_dir,
        )
    elif args.command == "years":
        result = build_cache_for_symbols_and_years(
            symbols=args.symbols,
            years=args.years,
            cache_dir=args.cache_dir,
        )
    else:
        requested_dates = [args.date] if args.date else args.dates
        print("requested_date,effective_date,symbol,eur_for_1_symbol")
        had_errors = False
        for requested in requested_dates:
            try:
                fx_rate = get_exchange_rate(args.symbol, requested, cache_dir=args.cache_dir)
                print(f"{requested},{fx_rate.date.isoformat()},{fx_rate.symbol},{fx_rate.rate}")
            except Exception as exc:  # noqa: BLE001
                had_errors = True
                logger.error("Failed for date %s: %s", requested, exc)
        return 1 if had_errors else 0

    logger.info(
        "Done. fetched=%s skipped=%s failed=%s rows=%s",
        result.fetched_count,
        result.skipped_count,
        result.failed_count,
        result.rows_written,
    )

    if result.failed_count > 0:
        for quarter, error in result.failed_quarters.items():
            logger.error("%s: %s", quarter, error)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
