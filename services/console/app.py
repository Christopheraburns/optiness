"""investigation-console. Placeholder until burn-down H1. Launches Streamlit
on the application port so the stack and a Workbench application come up."""
from __future__ import annotations

# --- interpreter shim -------------------------------------------------------
# Workbench launches application scripts with the runtime's default python,
# and the local stack mirrors that. The packages we need live in the
# pre-warmed kernel venv, so re-exec under it once if we are not already there.
import os as _os, sys as _sys  # noqa: E402
_KP = _os.environ.get("PRIME_AGENT_KERNEL_PYTHON")
# The venv's python is a symlink to the base interpreter, so compare the
# environment prefix (venv dir), not the executable path.
if _KP and _os.path.exists(_KP):
    _venv = _os.path.realpath(_os.path.dirname(_os.path.dirname(_KP)))
    if _os.path.realpath(_sys.prefix) != _venv:
        _os.execv(_KP, [_KP, *_sys.argv])
# ---------------------------------------------------------------------------

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from streamlit.web import cli as stcli

    from services.common.env import settings

    s = settings()
    sys.argv = [
        "streamlit", "run", str(ROOT / "services" / "console" / "page.py"),
        "--server.port", str(s.app_port),
        "--server.address", s.bind_host,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.baseUrlPath", os.environ.get("STREAMLIT_BASE_URL_PATH", ""),
    ]
    raise SystemExit(stcli.main())
