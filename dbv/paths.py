"""Where the installation keeps its files.

The install root is the folder that holds run_mcp.py, launcher_files.py and the
player's own data. Code finds data through these names, never relative to its
own file, so modules can move inside dbv/ without moving bot_settings.json,
local_data/, recordings/ or .codex/ on an existing installation.
"""
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parents[1]

# The player's data: settings, learned maps, routes, journals.
SETTINGS_FILE = INSTALL_ROOT / "bot_settings.json"
LOCAL_DATA_DIR = INSTALL_ROOT / "local_data"
RECORDINGS_DIR = INSTALL_ROOT / "recordings"
CODEX_DIR = INSTALL_ROOT / ".codex"
RUNTIME_DIR = CODEX_DIR / "runtime"
JOURNAL_DIR = CODEX_DIR / "journal"
SPELL_CATALOG = INSTALL_ROOT / "dashboard" / "public" / "local-spells" / "catalog.json"

# Program files that stay at the root.
ACTIONS_DIR = INSTALL_ROOT / "actions"
DASHBOARD_DIR = INSTALL_ROOT / "dashboard"
DLL_DIR = INSTALL_ROOT / "dll"
DOCS_DIR = INSTALL_ROOT / "docs"
SCRIPTS_DIR = INSTALL_ROOT / "scripts"
OFFSETS_FILE = INSTALL_ROOT / "offsets.json"
