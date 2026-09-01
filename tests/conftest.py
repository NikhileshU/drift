import json
import subprocess
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture()
def git_repo(tmp_path, monkeypatch):
    """An empty git repo with one commit, cwd'd into."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture()
def example_results():
    return json.loads((EXAMPLES / "results.json").read_text())


@pytest.fixture()
def example_manifest():
    return json.loads((EXAMPLES / "manifest.json").read_text())


@pytest.fixture()
def invalid_results():
    return json.loads((EXAMPLES / "results.invalid.json").read_text())
