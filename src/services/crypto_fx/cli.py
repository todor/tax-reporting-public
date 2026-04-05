from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .client import get_crypto_eur_rate

logger = logging.getLogger(__name__)


def _parse_timestamps(value: str) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise argparse.ArgumentTypeError("expected at least one timestamp")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="crypto-fx")
    parser.add_argument("--log-level", default="INFO")

    subparsers = parser.add_subparsers(dest="command", required=True)

    rate_parser = subparsers.add_parser("get-rate", help="Get EUR conversion rate for crypto symbol/pair")
    rate_parser.add_argument("--symbol-or-pair", required=True)
    rate_parser.add_argument("--exchange", required=True, choices=["binance", "kraken"])
    rate_parser.add_argument("--is-future", action="store_true", help="Use futures resolution/pricing mode")
    ts_group = rate_parser.add_mutually_exclusive_group(required=True)
    ts_group.add_argument("--timestamp")
    ts_group.add_argument("--timestamps", type=_parse_timestamps)
    rate_parser.add_argument("--cache-dir", type=Path)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command != "get-rate":
        parser.error("unsupported command")

    items = [args.timestamp] if args.timestamp else args.timestamps
    print(
        "requested_input,exchange,is_future,resolved_symbol,is_pair,"
        "timestamp_requested,timestamp_effective,price_usd,price_eur,pricing_source,"
        "used_futures_fallback,conversion_path"
    )

    had_errors = False
    for item in items:
        try:
            result = get_crypto_eur_rate(
                symbol_or_pair=args.symbol_or_pair,
                timestamp=item,
                exchange=args.exchange,
                is_future=args.is_future,
                cache_dir=args.cache_dir,
            )
            usd = "" if result.price_usd is None else str(result.price_usd)
            print(
                f"{result.requested_input},{result.exchange},{result.is_future},"
                f"{result.resolved_symbol},{result.is_pair},"
                f"{result.timestamp_requested.isoformat()},{result.timestamp_effective.isoformat()},"
                f"{usd},{result.price_eur},{result.pricing_source},"
                f"{result.used_futures_fallback},{result.conversion_path}"
            )
        except Exception as exc:  # noqa: BLE001
            had_errors = True
            logger.error("Failed for timestamp %s: %s", item, exc)

    return 1 if had_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
