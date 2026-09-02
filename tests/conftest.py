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


@pytest.fixture()
def forced_color(monkeypatch):
    """Make colour available regardless of the terminal the suite is running in.

    Styling assertions are about what Drift *emits*, not about what the ambient
    terminal supports. Left to inherit the environment they pass on a developer's
    laptop and fail in CI, where there is no TTY and `NO_COLOR` is often set — which
    is rich behaving correctly, not a bug.

    Note that `Console(force_terminal=True, color_system="standard")` is NOT enough on
    its own: `NO_COLOR` overrides it, stripping colour while leaving bold intact. And
    `CliRunner(color=True)` only colours the runner's own capture, not the Console a
    command constructs internally. Normalising the environment is the one mechanism
    that covers both.
    """
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("TERM", "xterm-256color")
