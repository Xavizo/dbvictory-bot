"""User data lives at the install root, whatever folder the code that reads it sits in."""
from pathlib import Path
import re

from dbv import paths

ROOT = Path(__file__).resolve().parents[1]
# Files allowed to locate something from their own file. The permanent root entry points
# (run_mcp, run_bot's pre-update stub, launcher_files) run before the updater has installed
# dbv/, so they find the root themselves. recovery_preview re-runs itself; map_navigation
# reads a program catalogue that moves together with it. mcp_server's sys.path line is skipped.
SELF_LOCATING = {"run_mcp.py", "run_bot.py", "launcher_files.py", "dbv/brain/state/recovery_preview.py",
                 "dbv/brain/navigation/map_navigation.py", "dbv/paths.py"}
FILE_LOOKUP = re.compile(r"__file__")


def _program_files():
    yield from ROOT.glob("*.py")
    yield from (ROOT / "dbv").rglob("*.py")


def test_install_root_is_the_folder_with_the_entry_points():
    assert paths.INSTALL_ROOT == ROOT
    for name in ("run_mcp.py", "run_bot.py", "launcher_files.py"):
        assert (paths.INSTALL_ROOT / name).is_file()


def test_data_folders_hang_off_the_install_root():
    assert paths.SETTINGS_FILE == ROOT / "bot_settings.json"
    assert paths.LOCAL_DATA_DIR == ROOT / "local_data"
    assert paths.RECORDINGS_DIR == ROOT / "recordings"
    assert paths.JOURNAL_DIR == ROOT / ".codex" / "journal"
    assert paths.ACTIONS_DIR.is_dir() and paths.DLL_DIR.is_dir()


def test_modules_read_data_from_the_install_root():
    from dbv.brain import profiles as bot_profiles; from dbv.brain.cavebot import cavebot; from dbv.brain.cavebot import movement_history; from dbv.brain.state import session_journal

    assert bot_profiles.ROOT == ROOT / "local_data" / "profiles.json"
    assert cavebot.RECORDINGS_DIR == ROOT / "recordings"
    assert movement_history.REPORT_DIRECTORY == ROOT / "local_data" / "movement-reports"
    assert session_journal.JOURNAL_DIR == ROOT / ".codex" / "journal"


def test_no_program_module_finds_data_next_to_its_own_file():
    offenders = []
    for path in _program_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in SELF_LOCATING:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if FILE_LOOKUP.search(line) and "sys.path.insert" not in line and "__main__" not in line:
                offenders.append(f"{rel}:{number}: {line.strip()}")
    assert not offenders, "Use dbv.paths instead of __file__:\n" + "\n".join(offenders)
