from typer.testing import CliRunner

from getdrift.cli import app

runner = CliRunner()


def test_init_creates_layout(git_repo):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    drift = git_repo / ".drift"
    assert (drift / "config.yaml").is_file()
    assert (drift / "golden_set").is_dir()
    assert (drift / "snapshots").is_dir()
    assert (drift / "schema" / "results.schema.json").is_file()
    assert (drift / "schema" / "manifest.schema.json").is_file()


def test_config_stub_defines_the_three_keys(git_repo):
    runner.invoke(app, ["init"])
    text = (git_repo / ".drift" / "config.yaml").read_text()
    for key in ("golden_set_path", "scorer_config", "model_config"):
        assert f"{key}:" in text


def test_init_is_idempotent_and_preserves_config(git_repo):
    runner.invoke(app, ["init"])
    config = git_repo / ".drift" / "config.yaml"
    config.write_text("golden_set_path: custom\n")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert config.read_text() == "golden_set_path: custom\n"


def test_init_force_resets_config(git_repo):
    runner.invoke(app, ["init"])
    config = git_repo / ".drift" / "config.yaml"
    config.write_text("golden_set_path: custom\n")
    runner.invoke(app, ["init", "--force"])
    assert "scorer_config" in config.read_text()


def test_init_outside_a_git_repo_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "git repository" in result.output


def test_help_lists_all_three_commands():
    result = runner.invoke(app, ["--help"])
    for command in ("init", "snapshot", "diff"):
        assert command in result.output
