"""Port configuration is loaded consistently in a fresh MCP process."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_ports(**overrides):
    env = {k: v for k, v in os.environ.items() if k not in {"DBV_LOGIN_PORT", "DBV_GAME_PORT"}}
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", "import constants; print(constants.LOGIN_PORT, constants.GAME_PORT)"],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )


def test_legacy_defaults_remain_available():
    result = load_ports()
    assert result.returncode == 0
    assert result.stdout.strip() == "7171 7172"


def test_local_client_game_port_override_preserves_login_default():
    result = load_ports(DBV_GAME_PORT="7475")
    assert result.returncode == 0
    assert result.stdout.strip() == "7171 7475"


def test_login_and_game_ports_can_be_configured_independently():
    result = load_ports(DBV_LOGIN_PORT="7474", DBV_GAME_PORT="7475")
    assert result.returncode == 0
    assert result.stdout.strip() == "7474 7475"


@pytest.mark.parametrize("name", ["DBV_LOGIN_PORT", "DBV_GAME_PORT"])
@pytest.mark.parametrize("value", ["0", "-1", "65536", "invalid"])
def test_invalid_ports_fail_before_network_start(name, value):
    result = load_ports(**{name: value})
    assert result.returncode != 0
    assert "ValueError" in result.stderr
