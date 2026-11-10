import os
import runpy
import subprocess
from pathlib import Path
from unittest.mock import Mock

REPO = Path(__file__).resolve().parents[1]


def test_packager_uses_current_home_for_conda(tmp_path, monkeypatch):
    module = runpy.run_path(str(REPO / "packaging/windows/build.py"))
    builder_class = module["WindowsPackageBuilder"]
    builder = builder_class.__new__(builder_class)
    builder.log = Mock()
    candidate = str(tmp_path / "miniforge3/bin/python3")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("os.path.exists", lambda path: path == candidate)
    run = Mock(return_value=subprocess.CompletedProcess([], 0, stdout="3.13"))
    monkeypatch.setattr(subprocess, "run", run)

    assert builder._find_suitable_python() == candidate
    assert run.call_count == 2
    assert all(call.args[0][0] == candidate for call in run.call_args_list)


def test_start_script_resolves_project_from_its_own_directory(tmp_path):
    # Stub every command that starts/stops processes or opens a browser.
    project = tmp_path / "project with spaces"
    (project / "dashboard").mkdir(parents=True)
    (project / ".venv/bin").mkdir(parents=True)
    (project / ".venv/bin/activate").write_text("")
    script = project / "start.sh"
    script.write_bytes((REPO / "start.sh").read_bytes())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("pkill", "sleep", "nohup", "open"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "CDPATH": str(tmp_path)}

    result = subprocess.run(["bash", str(script)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert str(project / ".logs") in result.stdout
    assert (project / ".logs/api.log").exists()
    assert (project / ".logs/worker.log").exists()
    assert (project / ".logs/dashboard.log").exists()
