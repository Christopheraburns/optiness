"""Workbench Application launchers (I3): checkout discovery, environment and child process."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.common import workbench  # noqa: E402


def checkout(tmp_path: Path, console_body: str = "print('ok')") -> Path:
    root = tmp_path / "project" / "optiness"
    (root / "services/bridge").mkdir(parents=True)
    (root / "services/console").mkdir(parents=True)
    (root / "skills").mkdir()
    (root / "services/bridge/app.py").write_text("")
    (root / "services/console/app.py").write_text(textwrap.dedent(console_body))
    return root


def test_find_root_from_parent_or_inside(tmp_path, monkeypatch):
    root = checkout(tmp_path)
    monkeypatch.delenv("OPTINESS_ROOT", raising=False)
    assert workbench.find_root(tmp_path / "project") == root  # Workbench: cwd /home/cdsw, checkout below
    assert workbench.find_root(root / "services") == root
    monkeypatch.setenv("OPTINESS_ROOT", str(tmp_path / "missing"))
    with pytest.raises(SystemExit, match="OPTINESS_ROOT"):
        workbench.find_root(tmp_path / "project")


def test_environment_points_paths_at_a_subdirectory_checkout(tmp_path):
    root = checkout(tmp_path)
    env = workbench.environment(root, {"HOME": str(tmp_path / "project"), "SKILLS_DIR": "/custom/skills"})
    assert env["SKILLS_DIR"] == "/custom/skills"  # the Application's own value wins
    assert env["BRIDGE_DATA_DIR"] == str(root / "data/bridge") and env["AGENT_WORKDIR"] == str(root / "data/agent")
    assert env["ATLAS_STUB_PATH"] == str(root / "config/atlas-stub.json") and env["PYTHONUNBUFFERED"] == "1"
    assert env["PYTHONPATH"].split(":") == [str(root / "skills/lakehouse/src"), str(root / "skills/atlas/src")]
    at_home = workbench.environment(root, {"HOME": str(root)})
    assert "BRIDGE_DATA_DIR" not in at_home  # project created from git: env.py's $HOME defaults already hold
    # ...but env.py has no PYTHONPATH default, so the skills path is set either way (found on the 50M run).
    assert at_home["PYTHONPATH"].split(":") == [str(root / "skills/lakehouse/src"), str(root / "skills/atlas/src")]
    assert workbench.environment(root, {"HOME": str(root), "PYTHONPATH": "/mine"})["PYTHONPATH"] == "/mine"


def test_launch_streams_output_and_returns_the_service_status(tmp_path, monkeypatch, capsys):
    root = checkout(tmp_path, "import os, sys\nprint('console on', os.environ['CDSW_APP_PORT'])\nsys.exit(3)\n")
    monkeypatch.setenv("HOME", str(tmp_path / "project"))
    monkeypatch.setenv("CDSW_APP_PORT", "8100")
    monkeypatch.delenv("PRIME_AGENT_KERNEL_PYTHON", raising=False)
    assert workbench.launch("console", root) == 3
    out = capsys.readouterr().out
    assert "console on 8100" in out and "[launch] console exited with status 3" in out
    with pytest.raises(SystemExit, match="unknown service"):
        workbench.launch("nope", root)


@pytest.mark.parametrize("script", ["bridge.py", "console.py"])
def test_application_scripts_run_as_workbench_cells(script, tmp_path, monkeypatch):
    """No __file__, kernel argv, cwd above the checkout: the script must still reach launch()."""
    calls = []
    monkeypatch.setattr(workbench, "launch", lambda service, root: calls.append((service, root)) or 0)
    (tmp_path / "optiness").symlink_to(ROOT, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPTINESS_ROOT", raising=False)
    monkeypatch.setattr(sys, "argv", ["/x/ipykernel_launcher.py", "-f", "/tmp/kernel.json"])
    monkeypatch.setattr(sys, "path", list(sys.path))
    exec(compile((ROOT / "deploy/cml" / script).read_text(), "<workbench cell>", "exec"), {"__name__": "__main__"})
    assert calls == [(script.removesuffix(".py"), ROOT)]
