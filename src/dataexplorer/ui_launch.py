"""Launch the Streamlit apps through the Streamlit runtime."""

import subprocess
import sys
from pathlib import Path


def _run(script: str, port: int) -> None:
    path = Path(__file__).with_name(script)
    raise SystemExit(subprocess.call([
        sys.executable, "-m", "streamlit", "run", str(path),
        "--server.port", str(port),
    ]))


def workspace() -> None:
    _run("ui.py", 8501)


def admin() -> None:
    _run("ui_admin.py", 8502)
