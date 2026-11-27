import json

import pytest

import mcp_clients


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(mcp_clients, "_claude_cli", lambda: None)
    return tmp_path


def test_nothing_is_connected_by_default(home):
    status = mcp_clients.status()
    assert not any(target["connected"] for target in status.values())
    assert status["claude-code"]["available"] is False


def test_codex_entry_is_added_and_removed_without_touching_other_settings(home):
    config = home / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "o"\n')
    mcp_clients.codex_set(True, home / "Bot Folder")
    text = config.read_text()
    assert mcp_clients.codex_connected()
    assert "[mcp_servers.dbvictory-bot]" in text and "[mcp_servers.dbvictory-bot.env]" in text
    assert json.dumps(str(home / "Bot Folder" / "run_mcp.py")) in text
    mcp_clients.codex_set(True, home / "Bot Folder")
    assert config.read_text().count("[mcp_servers.dbvictory-bot]") == 1
    mcp_clients.codex_set(False)
    assert config.read_text() == 'model = "x"\n\n[mcp_servers.other]\ncommand = "o"\n'
    assert (config.parent / "config.toml.dbv-backup").is_file()


def test_codex_toml_parses(home):
    tomllib = pytest.importorskip("tomllib")
    mcp_clients.codex_set(True, home / "Bot's \"folder\"")
    data = tomllib.loads(mcp_clients.codex_config().read_text())
    server = data["mcp_servers"]["dbvictory-bot"]
    assert server["args"] == [str(home / "Bot's \"folder\"" / "run_mcp.py")]
    assert server["env"]["PYTHONUTF8"] == "1"


def test_claude_desktop_keeps_other_servers(home):
    path = home / "Roaming" / "Claude" / "claude_desktop_config.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"mcpServers": {"x": {"command": "x"}}, "theme": "dark"}))
    mcp_clients.claude_desktop_set(True, home)
    data = json.loads(path.read_text())
    assert set(data["mcpServers"]) == {"x", "dbvictory-bot"} and data["theme"] == "dark"
    assert mcp_clients.claude_desktop_connected()
    mcp_clients.claude_desktop_set(False, home)
    assert set(json.loads(path.read_text())["mcpServers"]) == {"x"}


def test_claude_code_needs_its_cli(home):
    with pytest.raises(RuntimeError, match="Claude Code"):
        mcp_clients.claude_code_set(True, home)
