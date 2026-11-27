"""Where the installation keeps its files.

The install root is the folder that holds run_mcp.py, launcher_files.py and the
player's own data. Code finds data through these names, never relative to its
own file, so modules can move inside dbv/ without moving bot_settings.json,
local_data/, recordings/ or .dbvictory/ on an existing installation.
"""
import os
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parents[1]

# The program's own state: locks, logs, staged updates, runtime files. Earlier
# releases kept it in .codex/ (a name left from the Codex-driven updater); the launcher
# moves it at its next start, and until then every module keeps using the old folder.
STATE_DIR_NAME = ".dbvictory"
LEGACY_STATE_DIR_NAME = ".codex"


def state_dir(root=INSTALL_ROOT):
    """The installation's state folder: .dbvictory, or a not yet moved .codex."""
    current, legacy = Path(root) / STATE_DIR_NAME, Path(root) / LEGACY_STATE_DIR_NAME
    if not current.exists() and legacy.is_dir():
        return legacy
    return current


def migrate_state_dir(root=INSTALL_ROOT):
    """Rename .codex to .dbvictory; returns the folder in use afterwards.

    Called by the launcher at start, before it starts anything that writes there.
    Windows refuses the rename while a process (a bot started on its own) keeps a
    file open inside: the old folder then stays in use until a later start.
    """
    current, legacy = Path(root) / STATE_DIR_NAME, Path(root) / LEGACY_STATE_DIR_NAME
    if legacy.is_dir() and not current.exists():
        try:
            os.replace(legacy, current)
        except OSError:
            pass
    return state_dir(root)

# The player's data: settings, learned maps, routes, journals.
SETTINGS_FILE = INSTALL_ROOT / "bot_settings.json"
LOCAL_DATA_DIR = INSTALL_ROOT / "local_data"
RECORDINGS_DIR = INSTALL_ROOT / "recordings"
STATE_DIR = state_dir()
RUNTIME_DIR = STATE_DIR / "runtime"
JOURNAL_DIR = STATE_DIR / "journal"
# A catalogue exported from the player's own client wins over the one shipped with the bot.
LOCAL_SPELL_CATALOG = INSTALL_ROOT / "dashboard" / "public" / "local-spells" / "catalog.json"
SHIPPED_SPELL_CATALOG = INSTALL_ROOT / "dashboard" / "spells" / "catalog.json"
SPELL_CATALOG = LOCAL_SPELL_CATALOG if LOCAL_SPELL_CATALOG.is_file() else SHIPPED_SPELL_CATALOG

# Program files that stay at the root.
ACTIONS_DIR = INSTALL_ROOT / "actions"
DASHBOARD_DIR = INSTALL_ROOT / "dashboard"
DLL_DIR = INSTALL_ROOT / "dll"
SCRIPTS_DIR = INSTALL_ROOT / "scripts"
OFFSETS_FILE = INSTALL_ROOT / "offsets.json"
STARTER_MAP = INSTALL_ROOT / "config" / "starter-map.json.gz"
