from __future__ import annotations

import os
import sys
from pathlib import Path

from . import main


def _reexec_with_project_venv() -> None:
    root = Path(__file__).resolve().parent.parent
    venv_python = root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    if Path(sys.prefix).resolve() == (root / ".venv").resolve():
        return
    os.execv(str(venv_python), [str(venv_python), "-m", "report_analyzer", *sys.argv[1:]])


if __name__ == "__main__":
    _reexec_with_project_venv()
    raise SystemExit(main())
