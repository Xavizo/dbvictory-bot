"""The dbv/ layout: program code uses dbv.* names, and layers only import downwards."""
import ast
from pathlib import Path
import sys

from dbv import _legacy

ROOT = Path(__file__).resolve().parents[1]
# Permanent root files: entry points the launcher, updater and MCP configs call by path.
ROOT_MODULES = {"run_bot.py", "run_mcp.py", "launcher_files.py", "dbv_release.py", "dbv_revision.py",
                "launcher_lock.py", "launcher_panel_build.py", "github_release.py", "mcp_clients.py"}
# Imports from client_bridge up into brain that existed before the move. Cutting them is
# the next step; this set may only shrink.
KNOWN_UPWARD = {
    ("dbv/client_bridge/client_equipment.py", "dbv.brain.navigation.passive_map"),
    ("dbv/client_bridge/client_player_state.py", "dbv.brain.navigation.passive_map"),
    ("dbv/client_bridge/client_player_state.py", "dbv.brain.cavebot.adaptive_cavebot"),
    ("dbv/client_bridge/client_protocol.py", "dbv.brain.navigation.passive_map"),
    ("dbv/client_bridge/packet_observer.py", "dbv.brain.state.game_state"),
    ("dbv/client_bridge/packet_observer.py", "dbv.brain.combat.hunter_knife"),
    ("dbv/client_bridge/passive_map_win32.py", "dbv.brain.navigation.passive_map"),
}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
            for alias in node.names:
                yield node.lineno, f"{node.module}.{alias.name}"


def _python_files():
    for pattern in ("*.py", "dbv/**/*.py", "actions/*.py", "scripts/*.py", "tools/*.py",
                    "tests/**/*.py", "launcher/*.py"):
        yield from ROOT.glob(pattern)


def test_only_entry_points_stay_at_the_root():
    assert {p.name for p in ROOT.glob("*.py")} == ROOT_MODULES


def test_code_uses_the_new_module_names():
    old = [f"{p.relative_to(ROOT).as_posix()}:{line}: {name}"
           for p in _python_files() for line, name in _imports(p) if name.split(".")[0] in _legacy.ALIASES]
    assert not old, "Import these through dbv.*:\n" + "\n".join(old)


def test_every_alias_points_at_an_existing_module():
    for name, target in _legacy.ALIASES.items():
        assert (ROOT / Path(*target.split("."))).with_suffix(".py").is_file(), name
        assert not (ROOT / f"{name}.py").exists(), name


def test_old_names_load_the_same_module_object():
    import importlib
    potions = importlib.import_module("dbv.brain.combat.potions")  # an old name, as a player's action may use it
    from dbv.brain.combat import potions as moved
    assert potions is moved and sys.modules["dbv.brain.combat.potions"] is moved


def test_layers_only_import_downwards():
    crossings = set()
    for layer, above in (("client_bridge", ("dbv.brain", "dbv.mcp_server")), ("brain", ("dbv.mcp_server",))):
        for path in (ROOT / "dbv" / layer).rglob("*.py"):
            for _, name in _imports(path):
                if name.startswith(above):
                    module = name if (ROOT / Path(*name.split("."))).with_suffix(".py").is_file() else name.rsplit(".", 1)[0]
                    crossings.add((path.relative_to(ROOT).as_posix(), module))
    assert crossings <= KNOWN_UPWARD, f"New upward imports: {sorted(crossings - KNOWN_UPWARD)}"
