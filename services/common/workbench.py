"""Launch a service as a Cloudera AI Workbench Application (BURNDOWN I3).

Workbench runs an Application's script inside an IPython kernel: no ``__file__``, a
``sys.argv`` full of kernel flags, an event loop already running, and none of the image
entrypoint's preparation. So the scripts in ``deploy/cml/`` only locate the checkout
and call ``launch()``, which does what ``image/entrypoint.sh`` does locally and then
runs the real service as an ordinary child process under the Prime Agent kernel venv,
streaming its output into the Application log.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

SERVICES = {"bridge": "services/bridge/app.py", "console": "services/console/app.py"}
STOP_GRACE_S = 45  # the bridge needs up to ~30 s to interrupt a run and stop its sessions


def find_root(start: Path | None = None) -> Path:
    """The checkout: OPTINESS_ROOT, else the working directory, its parents or its subdirectories."""
    configured = os.environ.get("OPTINESS_ROOT")
    cwd = (start or Path.cwd()).resolve()
    candidates = [Path(configured).expanduser()] if configured else [
        cwd, *cwd.parents, *sorted(p for p in cwd.iterdir() if p.is_dir())]
    for candidate in candidates:
        if (candidate / SERVICES["bridge"]).is_file() and (candidate / "skills").is_dir():
            return candidate.resolve()
    raise SystemExit("Cannot locate the Optiness checkout; set OPTINESS_ROOT to the directory containing services/ and skills/")


def environment(root: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """The service environment: the Application's own values win; path defaults follow the checkout.

    services/common/env.py defaults paths to $HOME/..., which holds when the project is the
    checkout (Docker Compose, a Workbench project created from git). A checkout in a
    subdirectory needs them pointed at it."""
    env = dict(os.environ if base is None else base)
    env.setdefault("HOME", "/home/cdsw")
    env["PYTHONUNBUFFERED"] = "1"
    if root != Path(env["HOME"]).resolve():
        for key, relative in (("SKILLS_DIR", "skills"), ("ATLAS_STUB_PATH", "config/atlas-stub.json"),
                              ("AGENT_WORKDIR", "data/agent"), ("BRIDGE_DATA_DIR", "data/bridge"),
                              ("DATA_STAGING_DIR", "data/staging")):
            env.setdefault(key, str(root / relative))
        # The agent kernel imports its skills from PYTHONPATH; the daemon inherits this environment.
        env.setdefault("PYTHONPATH", os.pathsep.join(str(root / "skills" / name / "src") for name in ("lakehouse", "atlas")))
    return env


def interpreter(env: dict[str, str]) -> str:
    kernel = env.get("PRIME_AGENT_KERNEL_PYTHON")
    return kernel if kernel and os.path.exists(kernel) else sys.executable


def launch(service: str, root: Path) -> int:
    if service not in SERVICES:
        raise SystemExit(f"unknown service {service!r}; expected one of {sorted(SERVICES)}")
    env = environment(root)
    python = interpreter(env)
    print(f"[launch] {service} from {root} with {python} on port {env.get('CDSW_APP_PORT')}", flush=True)
    if service == "bridge":
        # image/entrypoint.sh does this locally; Workbench never runs the entrypoint.
        (Path(env["HOME"]) / ".prime" / "agent").mkdir(parents=True, exist_ok=True)
        subprocess.run([python, str(root / "scripts/render_config.py")], cwd=root, env=env, check=True)
    child = subprocess.Popen([python, str(root / SERVICES[service])], cwd=root, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    # Pass a stop on to the service and keep streaming its shutdown, rather than dying
    # and orphaning it (the bridge interrupts its run and stops its sessions on SIGTERM).
    stopping = []
    def stop(signum, frame):
        stopping.append(signum)
        child.send_signal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, stop)
    try:
        for line in child.stdout:
            print(line, end="", flush=True)
        code = child.wait()
    finally:
        if child.poll() is None:
            # Interrupted (kernel stop, KeyboardInterrupt): let the service shut down cleanly.
            child.send_signal(signal.SIGTERM)
            try:
                child.wait(STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    if stopping and code in (0, -signal.SIGTERM):
        code = 0  # a requested stop that the service honoured is a clean exit
    print(f"[launch] {service} exited with status {code}", flush=True)
    return code
