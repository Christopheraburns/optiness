"""Workbench Application script for the bridge. Set this file as the Application's script.

Workbench runs it in an IPython kernel without __file__, so it finds the checkout
itself and hands over to services.common.workbench (see that module).
"""
import os
import sys
from pathlib import Path


def _root():
    configured = os.environ.get("OPTINESS_ROOT")
    cwd = Path.cwd().resolve()
    candidates = [Path(configured).expanduser()] if configured else [cwd, *cwd.parents, *sorted(p for p in cwd.iterdir() if p.is_dir())]
    for candidate in candidates:
        if (candidate / "services/common/workbench.py").is_file():
            return candidate.resolve()
    raise SystemExit("Cannot locate the Optiness checkout; set OPTINESS_ROOT")


ROOT = _root()
sys.path.insert(0, str(ROOT))
from services.common.workbench import launch  # noqa: E402

status = launch("bridge", ROOT)
if status:
    raise SystemExit(status)
