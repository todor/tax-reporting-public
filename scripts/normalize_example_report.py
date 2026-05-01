from __future__ import annotations

import re
import sys
from pathlib import Path


def normalize_report(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(
        r"file://\S*?/examples/inputs/",
        "file://<REPO>/examples/inputs/",
        normalized,
    )
    normalized = re.sub(
        r"file://\S*?/output/examples/",
        "file://<OUTPUT>/",
        normalized,
    )
    return normalized


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: normalize_example_report.py <input-report> <output-report>", file=sys.stderr)
        return 2

    input_path = Path(args[0])
    output_path = Path(args[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        normalize_report(input_path.read_text(encoding="utf-8")),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
