"""P0 smoke tests: version, init, config validation, error exit codes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amber.cli.app import app, main
from amber.config import ConfigError, load_config, parse_config
from amber.version import __version__

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"amber {__version__}"


def test_init_creates_layout_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0, first.output
    assert (tmp_path / "amber.toml").is_file()
    assert (tmp_path / ".amber").is_dir()
    for sub in ("traces", "build", "proxy", "logs", "blobs"):
        assert (tmp_path / ".amber" / sub).is_dir()
    assert (tmp_path / ".amber" / ".gitignore").read_text() == "*\n"
    gitignore = (tmp_path / ".gitignore").read_text()
    assert ".amber/" in gitignore and "*.env" in gitignore

    cfg = load_config(tmp_path)
    assert cfg.models.backend == "auto"
    assert cfg.models.synthesis.startswith("claude-")

    second = runner.invoke(app, ["init", "--json"])
    assert second.exit_code == 0, second.output
    data = json.loads(second.stdout)
    assert data["created"] == []
    assert "amber.toml" in data["kept"]
    assert data["models_source"] == "defaults"


def test_init_json_via_root_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--json", "init"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["created"] and data["root"] == str(tmp_path.resolve())


def test_init_appends_to_existing_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.pyc\n")
    from amber.project import init_project

    result = init_project(tmp_path)
    assert ".gitignore (entries)" in result.created
    text = (tmp_path / ".gitignore").read_text()
    assert text.startswith("*.pyc\n") and ".amber/" in text and "*.env" in text
    again = init_project(tmp_path)
    assert ".gitignore" in again.kept


def test_config_rejects_secret_looking_values() -> None:
    bad = '[project]\nname = "x"\n\n[models]\nsynthesis = "sk-ant-api03-abcdefghijklmnop"\n'
    with pytest.raises(ConfigError) as info:
        parse_config(bad)
    assert "looks like" in info.value.message
    assert info.value.hint


def test_config_rejects_unknown_keys() -> None:
    with pytest.raises(ConfigError) as info:
        parse_config('[models]\nbackend = "auto"\nsynthesys = "typo"\n')
    assert "models.synthesys" in info.value.message


def test_main_maps_amber_error_to_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "amber.toml").write_text('[models]\njudgment = "xoxb-1234567890-secretsecret"\n')
    monkeypatch.setattr(sys, "argv", ["amber", "init"])
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code == 1
    err = capsys.readouterr().err
    assert "error:" in err and "hint:" in err
