"""Launch the dashboard with Streamlit state stored inside this project."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_STREAMLIT_CONFIG = ROOT / ".streamlit"
LOCAL_STREAMLIT_CONFIG.mkdir(exist_ok=True)
os.environ["STREAMLIT_CONFIG_DIR"] = str(LOCAL_STREAMLIT_CONFIG)
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

from streamlit.web.cli import main  # noqa: E402

if __name__ == "__main__":
    extra_args = sys.argv[1:]
    sys.argv = ["streamlit", "run", str(ROOT / "app.py"), *extra_args]
    main()
