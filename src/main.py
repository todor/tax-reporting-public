import argparse
import logging
from pathlib import Path

from config import OUTPUT_DIR
from integrations import AVAILABLE_INTEGRATIONS
from logging_config import configure_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tax-reporting")
    parser.add_argument("--log-level", default="INFO")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-integrations", help="List available integrations")

    run_parser = subparsers.add_parser("run", help="Run a specific integration")
    run_parser.add_argument("--integration", required=True)
    run_parser.add_argument("--input", type=Path)
    run_parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    run_parser.add_argument("--year", type=int)

    return parser


def list_integrations() -> int:
    for name in AVAILABLE_INTEGRATIONS:
        logger.info(name)
    return 0


def run_integration(
    integration: str,
    input_path: Path | None,
    output_path: Path,
    year: int | None,
) -> int:
    if integration == "binance":
        logger.info(
            "Binance integration is not implemented yet (input=%s, output=%s, year=%s)",
            input_path,
            output_path,
            year,
        )
        return 0

    logger.error("Unknown integration: %s", integration)
    return 2


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)

    if args.command == "list-integrations":
        return list_integrations()

    return run_integration(
        integration=args.integration,
        input_path=args.input,
        output_path=args.output,
        year=args.year,
    )


if __name__ == "__main__":
    raise SystemExit(main())
